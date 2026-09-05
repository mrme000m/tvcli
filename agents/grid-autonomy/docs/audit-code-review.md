# Code review — grid-autonomy daemon (correctness & concurrency audit)

Date: 2026-09-05 · Scope: `agents/grid-autonomy/` (daemon.py, ctl_http.py, pbclient.py,
config_lite.py, llm/provider.py, agents/swarm.py, agents/reflect.py, execution/*,
policy/stagnation.py, screen/merge.py, watch/spec.py, console/server.py,
console/yaml_edit.py, scripts/*). No source files were modified.
Evidence includes live `state/` artifacts (state.json, daemon.log, decisions.jsonl,
reports/) and the captured WT fixtures in `tests/fixtures/`.

Severity ranking: **CRITICAL** (money-path correctness / state corruption),
**HIGH** (wrong decisions or lost data under realistic conditions),
**MEDIUM** (degraded behavior, noise, edge-case failures), **LOW** (cosmetic or
hardening).

---

## CRITICAL

### C1. No single-writer enforcement — `daemon.py --once` ignores `state/daemon.pid` and clobbers the live daemon's state

- `daemon.py:339-363` — `load_state()`/`save_state()` have no lock and no
  ownership check. `main()` (`daemon.py:1839-1870`) and `Daemon.__init__`
  (`daemon.py:733-751`) never read or write `state/daemon.pid`.
- The pidfile guard exists only in the *launchers*: `scripts/run_launchd.py:100-108`
  refuses to double-start, `scripts/start.sh:57-60` refuses when the pid is alive —
  but `daemon.py --once` (e.g. `scripts/smoke.sh`, manual runs, cron) bypasses both.
- **Observed:** `state/reports/20260905T095122Z-rescreen.json` (`dry_run: true`)
  was written at 09:51:22Z by a second process while the live-paper daemon
  (pid 89966, `state/daemon.pid`) was mid-loop (`20260905T092405Z-rescreen.json`
  is `dry_run: false`, journal entries continue across 09:51 without a restart).
- Consequences (last-writer-wins on every `save_state`):
  - journal entries, `cooldowns_until`, `last_observe`, `needs_reanalysis`,
    `force_rotate` written by the live daemon are silently reverted by the
    `--once` process's stale snapshot (and vice versa);
  - `adopt_existing()` in the second process can double-adopt a bot adopted by
    the live daemon after the snapshot was taken (`daemon.py:1622-1710` — only
    `tracked_codes` from its own stale view are excluded);
  - if run with `--once --live-paper`, two autonomous traders act on the same
    account.
- The daemon never re-reads `state.json` after startup, so the `--once` process's
  writes (e.g. rotation cooldowns) are *always* lost on the next live save.
- **Fix:** (a) in `Daemon.__init__`, refuse to start (any mode) when
  `state/daemon.pid` holds a live pid — print the pid and exit non-zero, with an
  explicit `--force` override; (b) write the pidfile from `daemon.py` itself, not
  just from the launchers; (c) additionally take an `fcntl.flock` on
  `state/state.lock` around load→modify→save so even a bypassed guard cannot
  interleave writes; (d) make `--once` a read-only reporter (journal + run card
  only, never `save_state`) or route it through the live daemon's ctl plane
  (`POST /rescreen` already exists).

### C2. Unrealized PnL is mis-scaled ~10× — open-position `totalProfitLoss/10000` is not mark-to-market PnL

- `execution/observe.py:339-346`:
  ```python
  if open_positions:
      pnl_sum = sum(_number(r.get("totalProfitLoss")) for r in open_positions)
      unrealized = round(pnl_sum / 10000.0, 4)
  if unrealized is None:
      up = res.get("unrealizedPnl") or {}
      ...pnlFiat...
  ```
  The authoritative value (`unrealizedPnl.pnlFiat` on the grid resource) is used
  only when there are **no** open positions — exactly the case where it is
  unavailable. Priority is inverted.
- **Verification against the fixtures** (`tests/fixtures/grid_resource.json` +
  `positions_live.json`, same two open positions `6a9a87c…`/`6a9a3bba…`):
  - resource says `pnlFiat = -6.12` total (`-1.83` + `-4.29`); both per-position
    values are consistent with a single mark of ≈86.486 (4.8×(86.486−86.867)=−1.83,
    4.77×(86.486−87.385)=−4.29) → the resource field is genuine mark PnL.
  - the code computes (−2918.748 + −2917.8018)/10⁴ = **−0.5837** — ~10.5× off,
    and no constant scale factor exists (per-position ratios are 6.3× and 14.7×).
  - for open rows `totalProfitLoss` equals **−(entryCommissionVolume × 10⁴)**
    exactly on both rows — it tracks entry commission, not mark PnL, until the
    position closes.
  - the `/10000` scale *is* correct for the **history** rows' `profitLoss`
    (reliability_grid.py:30 `PNL_SCALE`): the three completed trips
    ($1.8389 + $1.7321 + $1.9461 = $5.517) match `stats.profitLoss = 5.52`.
  - `tests/test_observe.py` (`test_observe_all_offline`) asserts
    `unrealized_pnl == -0.5837`, i.e. the unit test bakes in the wrong number.
- Downstream damage:
  - `execute_rotation` → `record_outcome(..., realized_pnl=obs.get("realized_pnl_24h",
    obs.get("unrealized_pnl")))` (`daemon.py:1591-1601`) — see also H3;
  - `memories_for` → `outcome_pnl` fed to the LLM swarm
    (`agents/reflect.py:289-302`, `agents/swarm.py:61-99`);
  - the console UI renders `observed.unrealized_pnl`.
- **Fix:** in `_observe_one`, prefer `res["unrealizedPnl"]["pnlFiat"]` (and its
  per-position `summary`) whenever present; keep the scaled open-position sum
  only as a fallback when the resource lacks the field; fix the fixture-derived
  test expectation (−6.12 for the captured snapshot) or capture a coherent pair
  of snapshots.

### C3. Reliability ledger permanently drops rotated-out bots' closed round-trips

- `daemon.py:1713-1741` (`reliability_cycle`) aggregates `bot_trades()` **only for
  `state["active_bots"]`**; `execution/reliability_grid.py:127-146` fetches
  `/grid_bots/{code}/positions-history` — once `execute_rotation` stops and
  **deletes** the incumbent (`daemon.py:1574`), that history is unreachable.
- The 24 h cadence means every trade closed since the previous reliability run is
  lost at rotation time. The docstring's "history from already-deleted bots
  survives" (`daemon.py:1718-1721`) is only true for stats already computed while
  the bot was active.
- **Observed:** the rotated HYPE bot (`c629f5ba3a643a82fc53dd4e`, fixture
  `stats: {profitLoss: 5.52, completedTrades: 3}`, later 8 winning round-trips)
  has no entry in `state/reliability.json` — the ledger holds only
  `Neutral Grid (mean-reversion): 3 samples` and `unknown: 3 samples`. Its wins
  never count toward the ≥30-sample escalation gate or the archetype kill-flag.
- **Where the fix belongs:** `execute_rotation` — capture the incumbent's trades
  *before* `grid_delete` and merge them into the ledger:
  1. `trades = bot_trades(bot_code)` immediately before deletion;
  2. persist them under the incumbent's archetype in `state/reliability.json`
     (e.g. extend `archetype_stats` input with per-bot archived trades, or keep a
     `state/trades_archive.json` keyed by bot_code that `reliability_cycle`
     always folds in);
  3. optionally also merge at stop time, since the bot is already frozen then.
  A cheaper partial fix: run `reliability_cycle` (or just the affected
  archetype) inline during every rotation.

---

## HIGH

### H1. `serve_ctl` has no bind-failure handling — the daemon can run silently without its ctl plane

- `ctl_http.py:99-101`: `HTTPServer(("127.0.0.1", port), Ctl).serve_forever()` —
  an `OSError: [Errno 48] Address already in use` raises inside the daemon
  thread (`daemon.py:1755-1757`), the thread dies after printing a traceback,
  and the daemon keeps running with **no ctl plane**. Symptoms:
  `GET /health|/status|/observe` and `POST /rotate|/rescreen|/kill` all fail,
  `scripts/stop.sh`'s `/kill` falls back to SIGTERM (works, but the
  "graceful KILL-file" path is skipped), and the console's ctl proxying breaks.
- Concretely reachable: a stray `daemon.py --once` still holding the port when
  the supervised daemon (re)starts, or two start paths racing.
- **Fix:** wrap the constructor in try/except in `serve_ctl`, log/journal the
  bind failure, retry with backoff (and `allow_reuse_address`); for `--once`,
  bind port 0 or skip the ctl thread entirely.

### H2. Observe-error dicts are stored as `bot["observed"]` — a WT outage can trigger blind rotations

- `daemon.py:1352` stores the observation **before** the error check at
  `daemon.py:1354-1358`; an errored sweep leaves `bot["observed"] = {"error": …}`
  with no `fills_24h`/`realized_ratio` keys.
- The rescreen rotation pass reads `obs = bot.get("observed") or {}`
  (`daemon.py:1250`) and calls `is_stagnant(obs, …)` (`daemon.py:1285`);
  `policy/stagnation.py:110-118` treats missing values as `0` → `0 < min_fills`
  and `0 < 0.4` → **every incumbent is "stagnant"** while the daemon is blind.
- The stop/delete/create calls go through the same wt_browser transport, so a
  *total* outage self-vetoes at `rotation-stop` — but in *partial* degradation
  (positions/status endpoints failing while grid ops succeed, or a transient
  observe failure with recovery by rotation time) this churns live paper bots
  and burns cooldowns. The "observe-outage" escalation
  (`daemon.py:1413-1429`) detects the condition but does not gate rotation.
- **Fix:** in the rotation pass, skip (or refuse to rotate) any bot whose
  `observed` carries `error` or whose `last_observed` is stale — mirror the
  guard `health_cycle` itself already applies per-bot.

### H3. `record_outcome` never gets realized PnL — the field is dead and the fallback is the mis-scaled unrealized value

- `daemon.py:1594-1595`: `obs.get("realized_pnl_24h", obs.get("unrealized_pnl"))`.
  `_observe_one` never emits `realized_pnl_24h` (`observe.py:375-383`), so
  outcomes always record (wrongly-scaled, see C2) *unrealized* PnL as
  `realized_pnl`; the reflection memories then learn from fabricated numbers
  (`reflect.py:289-302`).
- **Fix:** at rotation time, compute realized PnL from the incumbent's closed
  round-trips (`bot_trades(bot_code)` — the same call C3 needs before deletion —
  summed over the holding window) and pass that; drop the dead key.

### H4. Slot plan ignores observed plan capacity — binance slot 4 can never deploy

- `daemon.py:832-838` `plan_slots()` is pure config math
  (`policy/stagnation.py:129-192`): 500 USD → HL 2 × $150 + **BN 2 × $100**
  (slots 3 and 4 both binance).
- The free plan's enforced tier cap is `max_active.other = 1`
  (`state.json` → `capacity.max_active`), verified live in
  `execution/observe.py:146-171`. `venue_capacity_block`
  (`daemon.py:1088-1131`) vetoes the second binance deploy at `act >= cap`
  — so **slot 4 is permanently undeployable** and every rescreen emits a
  `capacity-veto` journal line for binance (`daemon.py:1211-1216`).
- The capacity observation is already in state
  (`daemon.py:1196-1213` writes `state["capacity"]` and journals signature
  changes) — the plan just never uses it.
- **Fix:** derive per-venue slot counts from the observed capacity when
  available: `binance_slots = clamp(max_active.other - active_elsewhere, 0, n)`
  and redistribute the remainder to funded venues (or park the sleeve in
  reserve); recompute the plan when `limits_signature` changes; fall back to the
  config-derived plan only while capacity is unobservable.

---

## MEDIUM

### M1. `save_state` is atomic vs readers, not vs writers; a failed load silently resets state

- `daemon.py:356-363`: fixed `STATE_PATH + ".tmp"` + `os.replace` — atomic for
  readers, but two concurrent writers (see C1) share the same tmp inode:
  interleaved/truncated JSON can be `os.replace`d into place. No `fsync`, so a
  crash can also leave a stale-but-valid old file (benign) or an empty tmp
  renamed on top (not benign without fsync semantics on some filesystems).
- `load_state()` (`daemon.py:343-355`) on any parse error prints "starting
  fresh" and the next `save_state` **overwrites the previous good state**.
- **Fix:** unique tmp name (`f".{pid}.tmp"`), `f.flush()+os.fsync()` before
  replace, an flock (shared with C1), and on load failure back up the corrupt
  file and exit rather than running with a blank slate.

### M2. Journal churn destroys the 200-entry window (and spams PocketBase)

- `log()` caps `state["journal"]` at 200 (`daemon.py:337-339`). `health_cycle`
  logs `stagnant` per bot **every ~60 s pass** (`daemon.py:1372-1374`) —
  `daemon.log` shows ~3 identical stagnant lines per minute for hours.
  The window therefore covers well under an hour; deploy/veto/subscription
  events rotate out quickly, and each line is also written through to
  PocketBase (`_pb_journal`, `daemon.py:166-172`) → ~4 records/min of noise.
- **Fix:** log stagnation on state-change only (per-slot "was stagnant" flag)
  or rate-limit per (slot, kind) to e.g. 1/h; keep the 200 cap for real events.

### M3. Rotation proceeds (state pop + cooldown) even when `grid_delete` fails

- `daemon.py:1574-1580`: `del_res` is journaled but never checked; the incumbent
  is popped from `active_bots` and the cooldown set regardless of delete
  outcome. A stopped-but-undeleted bot keeps the pair in
  `exchangesUsedPairs` (`capacity.used_pairs`), so any future deploy of that
  pair on the same profile hits the "pair already has a bot" veto
  (`daemon.py:1122-1130`), and the zombie accumulates in the WT UI.
- **Fix:** on `del_res.ok == false`, journal a `rotation-error`, keep a
  `pending_delete` marker on the slot (or a separate list) and re-attempt
  deletion on the next health pass before the slot may be reused.

### M4. Decision-id allocation races under two writers

- `agents/reflect.py:109-129`: `_next_decision_id` scans the file *without* a
  lock, then `_append_json_line` (`reflect.py:83-106`) flocks only the append.
  Two concurrent writers (C1) can allocate the same `dYYYYMMDD-NNN` id;
  `record_outcome` (`reflect.py:222-265`) then patches only one of them, and
  PB mirroring splits. `record_outcome` itself is an unlocked
  read-modify-write of the whole file.
- **Fix:** hold the flock across the id scan + append; or embed pid/uuid
  entropy in the id.

### M5. Production `rescreen-error` is untraceable from the log

- `daemon.log` 2026-09-04T19:15:09: `rescreen-error: unsupported operand type(s)
  for -: 'float' and 'NoneType'` — the traceback is stored in the journal's `tb`
  field but `log()` prints only kind+msg (`daemon.py:334-342`), and the journal
  entry has since rotated out (M2). The same applies to every `*-error` event.
- **Fix:** print the tail of `tb` in the log output for error kinds (and fix
  the underlying `float - None`, most plausibly a `score_final`/metrics `None`
  leaking into a subtraction in the rescreen path).

### M6. Legacy adopted bots keep `archetype: null` / `decision_id: null` forever

- `state.json` slots 2 (ETH) and 3 (SOL) were adopted by an older `adopt_existing`
  and permanently sit with `archetype: null`; their trades aggregate under the
  `"unknown"` bucket in the ledger. Current `adopt_existing`
  (`daemon.py:1681-1695`) sets both, but nothing backfills existing entries.
- **Fix:** one-time migration in `Daemon.__init__` (or first
  `reliability_cycle`): reclassify regime for adopted bots missing
  `archetype` and record a retroactive decision for missing `decision_id`.

---

## LOW

1. **`ctl /rotate` mutates state without lock or save** — `ctl_http.py:83-91`
   sets `bot["force_rotate"] = True` from the HTTP thread; it is lost if the
   daemon dies before the next rescreen saves. Also `json.loads(self.rfile…)`
   (`ctl_http.py:78`) raises on malformed bodies → connection reset instead of
   a 400. Fix: save_state after flagging (or persist the flag in its own file);
   wrap the body parse.
2. **`grid_edit` dry-run cmd embeds a dict repr** — `execution/grid_adapter.py:313-315`
   builds `--data @{body}` where `body` is a dict; the echoed cmd is
   unrunnable. Cosmetic in dry-run, but misleading for replay. Fix: format the
   value like the non-dry path (or serialize to a temp file).
3. **`pbclient._import_dir` assumes slots is a dict** — `pbclient.py:386-387`
   `(state.get("slots") or {}).items()` raises `AttributeError` for the actual
   list shape (`daemon._pb_mirror_state` at `daemon.py:187-194` handles both).
   The one-shot importer dies midway. Fix: mirror the daemon's list/dict
   handling.
4. **HL candidates get a BINANCE tv_symbol** — `screen/merge.py:129` stamps
   `BINANCE:{sym}USDT` for hyperliquid perps; tvcli confluence is measured on a
   different venue's market. If intentional (no HL charts in tvcli), document
   it; otherwise map to the correct feed.
5. **stop.sh hardcodes ctl port 8799** (`GRID_DAEMON_PORT` default) while the
   daemon reads `config.yaml server.daemon_port` — a config change silently
   misses `/kill` (SIGTERM fallback still works). Read the same config.
6. **Console nits** — `console/server.py:566` leaks the `daemon.log` fd in
   `daemon_start`; `:402` compiles a raw user regex (`ValueError` → unhandled
   500 on bad `grep`); `_pid()` (`console/server.py:201-208`) trusts the pidfile
   without a process-name check (pid reuse can report a false "running").
7. **`grid_adapter.build_ticket_payloads` size-floor log prints a stale
   `usd_per_grid`** — after rebuilding `payloads` (`daemon.py:974-986`, the
   `elif worst > 1e-9` branch), `sizing` still points at the pre-rebuild dict from `daemon.py:932`;
   the log line can misreport. Re-derive `sizing` after the rebuild like the
   size-fit branch does (`daemon.py:946-952`).
8. **`load_config` failure is silent** — `daemon.py:316-325` prints to stdout
   and runs with defaults; under launchd stdout goes to a log nobody greps.
   Consider refusing to run live-paper on config parse failure.

---

## What is solid (verified, no action)

- Rotation ordering stop → verify-stopped → delete → deploy with
  never-delete-a-live-bot guards (`daemon.py:1485-1608`), incl. the
  already-stopped fast path, is correctly sequenced and unit-tested
  (`tests/test_daemon_manage.py::test_rotation_order_*`).
- Min-hold floor (`daemon.py:1261-1274`), per-symbol cooldown writes
  (`daemon.py:1578`), manual force-rotate bypassing only score hysteresis
  (`daemon.py:1005-1007` + `guardrails.check_rotation`) behave as designed.
- `reliability_grid.PNL_SCALE = 10000` for **history** `profitLoss` is
  correct (fixture cross-check above).
- Guardrail gates (`execution/guardrails.py`) are pure, fail-closed, and
  venue-strict profile selection with denylist (`daemon.py:259-308`) is sound.
- `retry_grid_call` retries only live calls (`daemon.py:549-566`); observe /
  resolve / reliability wrappers never raise.
- Reflect's flock'd append, PocketBase's non-fatal write-through, and
  `provider.chat_json`'s balanced-JSON extraction are reasonable for their
  scope.

## Priority fix order

1. C1 + M1 + H1 (single-writer enforcement, locking, bind handling) — one
   afternoon, removes the whole class of concurrency damage.
2. C2 + H3 (PnL source fix + realized-PnL computation) — small diff, fixes the
   reflection/ledger numbers the LLM learns from.
3. C3 (trade capture at rotation) — required for the escalation ladder to ever
   reach ≥30 samples on this account.
4. H4 (capacity-aware slot plan) — stops the dead slot-4 noise and aligns the
   portfolio with the actual plan.
5. H2 (blindness gate on rotation) then the MEDIUM items.
