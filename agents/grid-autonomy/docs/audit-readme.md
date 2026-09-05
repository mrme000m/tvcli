# README audit — `agents/grid-autonomy/README.md` vs code

Audit date: 2026-09-05. Every factual claim in the README was checked against
the current tree (`daemon.py`, `config.yaml`, `config_lite.py`, `ctl_http.py`,
`pbclient.py`, `execution/`, `agents/`, `llm/`, `policy/`, `screen/`, `watch/`,
`scripts/`, `launchd/`, `tests/`, `console/`). Line numbers refer to the
audited files as of this date.

**Verdict:** the README is substantially accurate — architecture, loop
cadences, guardrails, sizing ladder, profile rules, capacities, ports,
launchd/start/stop behavior, PocketBase write-through, and the LLM provider
chain all match the code. **One clear stale fact** (test count: 129 vs 188)
plus a handful of minor imprecisions (details below).

---

## CONFIRMED accurate (evidence per section)

### Architecture / loop
- Loop order screen → deliberate → guard → deploy → watch → rotate → reflect,
  journal + run cards: `daemon.py` (rescreen_cycle :1132, health_cycle :1344,
  reliability_cycle :1713, run loop :1753).
- **Cadences:** health poll 60s (`daemon.py:1783`, `watch.interval_s`),
  rescreen 60m (`daemon.py:1784`, `screen.rescreen_minutes`),
  reliability cron 24h (`daemon.py:1785`), grid-edit rate limit 6h
  (`daemon.py:1439` "rate limit (1 edit/6h)").
- Screen stack: HL perps + Binance spot, presets `grid-neutral` +
  `grid-directional`, tvcli `/hunt` confluence with skills
  `squeeze,choppiness,mtf-confluence` (`screen/merge.py:41-57, 270`),
  4h directional confirm reading `screen.confirm_interval`
  (`screen/merge.py:209-228`), Binance book-ticker spreads
  (`execution/spreads.py`, merged via `merge.py:48`).
- Deliberation swarm: bull open → bear open → 1 rebuttal round each →
  facilitator → 3-stance risk team (seeking/neutral/conservative), max 8 LLM
  calls, rule fallback + `llm_degraded: true`
  (`agents/swarm.py:4-10, 174-210`).
- `agents/reflect.py` injects up to `k=3` memories
  (`daemon.py:849-850` reads `memory.k`; `reflect.py:305 memories_for(brief, k=3)`).
- Deploy: capacity pre-check `venue_capacity_block()` + `capacity-veto`
  journal (`daemon.py:1088-1129, 1211-1217`), ATR channel / geometric lines /
  USD per-trade sizing (`execution/grid_adapter.py:54-124, 167-230`),
  `pairCode` from live market map (`execution/resolve.py`).
- Watch: out-of-channel/stopped → `needs_reanalysis` (`daemon.py:1382-1393`),
  in-place re-centre only when regime intact (`daemon.py:1395-1412`).
- Rotate: stop → verify → delete → per-token cooldown → replace
  (`daemon.py:1560-1619`); `POST /rotate {"slot": n}` sets `force_rotate` +
  queues rescreen (`ctl_http.py`).
- Adopted bots get a `kind: "adopted"` decision record with archetype
  classified (`daemon.py:1688-1694`).

### Guardrails (all 8)
`execution/guardrails.py:82-84` — `CHECKS = (check_kill, check_paircode,
check_profile, check_sizing, check_spread, check_venue_side,
check_reliability, check_rotation)`. Matches the README's 8-gate list
(KILL, pairCode, profile, sizing/slot+portfolio ceiling, step ≥ 2× spread,
venue/side, reliability, rotation cooldown+hysteresis). Reliability gate
numbers: <30 samples / PF <1.3 / recent PF <1.0 refuse non-paper
(`guardrails.py:99-110`).

### Sizing ladder
Base 25% (<10 samples) → probe 40% (≥10) → full 50% (≥30 and PF ≥1.3);
`recent_pf < 1.0` kills the archetype (`daemon.py:704-727`), recent window =
last 20 round-trips (`execution/reliability_grid.py:29 RECENT_WINDOW = 20`).
Kill-flag also flags existing bots (`daemon.py:1363-1369`).

### Profiles / paper-only
- `PROFILE_DENYLIST = {"c629f5ba3a643a82137e7864"}` (`daemon.py:199`).
- Venue-strict `VENUE_EXCHANGES`: hyperliquid→`HYPERLIQUID_SWAP`,
  binance→`{BINANCE, BINANCE_FUTURES}`, no cross-venue fallback
  (`daemon.py:591-593, 609-638`).
- `select_profile` called only with `paper=True` (both call sites:
  `daemon.py:871-872`, `daemon.py:1104-1105`) — the daemon never selects
  real-money profiles on its own.
- `paper_profiles` map (venue-keyed or legacy flat list) is read
  (`daemon.py:598-606`); `demo-hype` (HL) and `demo-bn` (Binance futures
  stand-in) come from `config.yaml` `autonomy.paper_profiles` — both wired.
- `BINANCE_FUTURES` → derivative map for `pairCode`; `exchangeCode` in the
  payload is the profile's actual exchange (`daemon.py:641-649, 877-878, 917`).

### Capacities / min-notionals
- `maxActiveGridBots = {other: 1, premium: 200}` from `grid_bots/upsert` init
  data; dashboard `account-limits` is a different view
  (`execution/observe.py:146-187`, incl. the 0.035% builder-fee / Hyperliquid
  premium note).
- Per-pair floor from `:2087` `limits.cost.min`; `$10` fallback
  (`daemon.py:263-266 MIN_USD_PER_GRID = 10.0`); market map / metadata
  fetched via browser, cached 24h (map) / 7d (meta), stale cache reused
  (`execution/resolve.py:8-14, 29, 128-144, 253`).

### Control plane / console
- `ctl_http.py`: exactly the 8 documented endpoints; `/status` returns slots,
  active bots, committed, `live_allow`, profiles, `capacity`,
  `account_limits`, `last_cycle`, last-10 journal tail (`ctl_http.py`,
  `daemon.py:338`).
- Ports: daemon ctl **8799** (`daemon.py:735`), console **8798**
  (`console/server.py:68 CONSOLE_PORT`), tvcli serve **8765**
  (`screen/merge.py:56 TVCLI_SERVER` default).
- Console endpoints/actions incl. whitelisted comment-preserving config edit,
  run cards, reliability with sizing tiers, confirm-gated ctl actions
  (`console/server.py:19-42, 677-822`; `console/yaml_edit.py`;
  `console/README.md`).

### Scripts / launchd
- `start.sh`: exports CF keys from `dsh web` process env, refuses without
  them, sources PocketBase env, `nohup python3 daemon.py` dry-run default —
  all as documented (`scripts/start.sh`).
- `stop.sh`: POST `/kill` → SIGTERM → wait up to 15s (30×0.5s) → SIGKILL →
  remove PID file; `GRID_DAEMON_PORT` override (`scripts/stop.sh`).
- `run_launchd.py`: foreground entrypoint, CF env import, PB env, PID file,
  runs `daemon.py --live-paper` in-process; KILL-file refusal
  (`scripts/run_launchd.py`).
- Plists: `KeepAlive={SuccessfulExit: false}`, `ThrottleInterval` 30,
  `/opt/homebrew/bin/python3` ProgramArguments, `com.tvcli.serve` runs
  `tvcli serve` (`launchd/com.tvcli.grid-autonomy.plist`,
  `launchd/com.tvcli.serve.plist`).
- `smoke.sh` wraps `python3 daemon.py --once --no-confluence --top 5`
  (`scripts/smoke.sh`). CLI flags `--once --dry-run --live-paper
  --no-confluence --top --port` all exist (`daemon.py:1853-1860`).
- SIGTERM → exit 0 with state save (`daemon.py:1839-1870`).

### Configuration reference table
Every "Wired?" verdict re-checked against reads in `daemon.py` /
`screen/merge.py` / `agents/swarm.py` / `policy/stagnation.py` /
`execution/*`:
- Wired (yes): `portfolio.total_usd`, `venues.*.balance_usd`,
  `slots_default`, `max_alloc_per_slot`, `cash_buffer_pct` (all via
  `plan_slots` → `slot_plan`, `daemon.py:832-836`); `screen.confirm_interval`
  (`merge.py:209`); `screen.rescreen_minutes` (`daemon.py:1784`);
  `grid_defaults.min_grids` (`daemon.py:941-951`); `policy.hysteresis_score`
  (`daemon.py:392, 1008`); `policy.min_hold_h` (`daemon.py:1271-1273`);
  `watch.interval_s` (`daemon.py:1783`); `watch.adjust_steps_threshold`
  (`daemon.py:1399`); `autonomy.base_pct/probe_pct/full_pct`
  (`daemon.py:711-713`); `autonomy.live_profiles` (`daemon.py:618`);
  `autonomy.paper_profiles.*` (`daemon.py:600-604`); `memory.k`
  (`daemon.py:850`); `adopt_existing` (`daemon.py:1623`);
  `server.daemon_port` (`daemon.py:735`).
- Doc-only rows confirmed doc-only (defaults live in the named module and
  the daemon never reads the key): `venues.*.market`, `venues.*.grids`,
  `slots_min/slots_max` (only enforced as the 3–5 clamp inside
  `stagnation.slot_plan`, `policy/stagnation.py:147`), `screen.presets /
  interval / limit / min_volume_usd / top_per_preset_venue / confluence_top /
  confluence_skills`, `grid_defaults.band_atr / step_factor / step_min /
  step_max / max_grids / dca_factor / stop_trigger / pump_protection`
  (hardcoded in `execution/grid_adapter.py:54-67, 117-120`),
  `llm.max_calls_per_decision` (8 hardcoded, `agents/swarm.py:175`),
  `policy.cooldown_min_h / cooldown_max_h / fill_ratio_stagnant /
  realized_ratio_stagnant / score_drop_rotate` (constants in
  `policy/stagnation.py:38-43`), `reliability.paper_profile`,
  `reliability.min_samples / profit_factor_pass / profit_factor_kill`
  (hardcoded in `execution/guardrails.py:99-110`),
  `autonomy.mode` (never read), `guardrails.checks`,
  `guardrails.kill_file` (daemon hardcodes `HERE/KILL`, `daemon.py:994`),
  `server.tvcli` (env `TVCLI_SERVER`, `merge.py:56`).
- Env-only rows confirmed: `llm.chain / cf_model / nvidia_model /
  openrouter_model` read exclusively from `GRID_LLM_CHAIN`, `CF_MODEL`,
  `NVIDIA_MODEL`, `OPENROUTER_MODEL` (`llm/provider.py:24-27, 78-98`); the
  `llm:` block in config.yaml is not read by code.

### PocketBase write-through
Best-effort side channel, file layer stays system of record: journal
(`daemon.py:166-172, 338-339`), decisions + outcomes
(`agents/reflect.py:213-216, 260-263`), reliability ledger
(`execution/reliability_grid.py:225-228`), bots + slots mirror
(`daemon.py:175-196`). Env from `.pocketbase/pb.env` sourced by `start.sh`
and `run_launchd.py`, re-healed by `self_heal_env()` (`daemon.py:217-239`).

### Self-healing / troubleshooting
- Browser watchdog in `health_cycle` (`daemon.py:1345, 752-790`): CDP probe
  each pass, relaunch `browser_launch_cmd` + `wt_restore_cmd` at most every
  `browser_restart_cooldown_s` (600s), journaled `browser-restart`.
- `observe-outage` escalation every ~30 blind minutes (`daemon.py:1413-1430`).
- `self_heal_env()` every rescreen, journaled `env-heal`
  (`daemon.py:1138, 217-239`).
- KILL file checked at startup + every tick (`daemon.py:1758-1760, 1790-1793`).
- `observe error: grid status list unavailable (browser/session down)`
  string exists (`execution/observe.py:385`).
- WT session-expiry section is operational guidance (cookie date cannot be
  verified from code) — consistent with the `wt_restore_cmd` mechanics.

---

## DISCREPANCIES

### 1. Test count is stale: README says 129, actual is 188  *(main finding)*

README (File map): "`tests/` | 129 offline unit tests (`python3 -m unittest`)."
README (Tests section): "129 tests, all offline … `Ran 129 tests in …`".

Evidence: run from `agents/grid-autonomy/`:

```
$ python3 -m unittest discover -s tests -t .
...............................................................
Ran 188 tests in 48.857s
OK
```

Fix: replace 129 → 188 in both the File map row and the Tests section
(expected output block included).

### 2. `guardrails.checks` is "The 8 gate names" — config.yaml lists only 7

README config row: "`guardrails.checks` | The 8 gate names. | doc only
(guardrails.py tuple)".

Evidence: `config.yaml` `guardrails.checks` lists 7 names
(`pairCode_from_get_exchange_markets`, `profilesCodes_active_with_balance`,
`worst_case_commitment_within_max_alloc`, `profit_per_grid_ge_2x_spread`,
`venue_side_allowed`, `reliability_gate`, `cooldown_and_hysteresis`) — the
KILL-file gate has no config name. The 8 functions live in
`execution/guardrails.py:82-84` (`CHECKS` tuple incl. `check_kill`).

Fix: "7 of the 8 gate names (the KILL-file gate is unnamed in config;
`guardrails.py CHECKS` has 8)".

### 3. Min-notional floor: "5–50 USDT on Binance" is misleading — the effective floor never drops below $10

README (loop step 4): "…a per-pair minimum-viable floor from `:2087` market
metadata (`limits.cost.min`: 10 USDC on Hyperliquid, 5–50 USDT on Binance
markets; falls back to $10 when metadata is unavailable)."

Evidence: `daemon.py:897-901` — `min_cost = max(meta.get("min_cost") or 0,
MIN_USD_PER_GRID)` with `MIN_USD_PER_GRID = 10.0` (`daemon.py:266`), and the
inline comment states the exchange value "must only raise it, never lower it
below the grid floor". So a Binance market with `limits.cost.min = 5` still
deploys at $10/line.

Fix: "effective floor = max(limits.cost.min, $10)" and drop or requalify the
"5–50 USDT on Binance" range (it describes the metadata, not the applied
floor). Same nuance applies to the Safety-rails bullet "Minimum viable
minimum notional per grid (per-pair `limits.cost.min`)."

### 4. "screens Hyperliquid perps and Binance spot in parallel" — it is serial

README (loop step 1): "`screen/merge.py` screens Hyperliquid perps and
Binance spot in parallel".

Evidence: `screen/merge.py:294-300` runs `screen_hyperliquid` and
`screen_binance` in a plain `for` loop; no threading/concurrency anywhere in
the module.

Fix: "in one pass" / "across both venues" (or make it actually parallel).

### 5. Paper→live checklist: "not `paperTrading`" is not enforced by code

README (Paper → live escalation, step 3): "ensure the profile is **not** in
`daemon.PROFILE_DENYLIST` and is not `paperTrading`".

Evidence: `daemon.py:635` — `if paper and not p.get("paperTrading"): continue`
filters non-paper profiles only in **paper** mode. On the `paper=False` path
nothing rejects a `paperTrading` profile; the denylist check (`daemon.py:633`)
is enforced, the paperTrading one is not.

Fix: mark it as an operator-only requirement ("code enforces the denylist and
venue-strict rules; the non-paperTrading check is on you") — or add the check
to `select_profile`.

### 6. `start.sh` env import described with the Linux path only

README (Operations runbook): "`start.sh` exports `CLOUDFLARE_ACCOUNT_ID` /
`CLOUDFLARE_API_KEY` from the running `dsh web` process environment
(`/proc/<pid>/environ`) …".

Evidence: `scripts/start.sh` reads `/proc/<pid>/environ` on Linux and
`ps -Eww` on macOS (this machine is macOS); it also accepts keys already
exported in the shell.

Fix: mention the macOS `ps -Eww` path (as `run_launchd.py`'s docstring does).

### 7. Configuration reference table omits wired `watch.*` keys (incompleteness)

`watch.browser_cdp`, `watch.browser_restart_cooldown_s`,
`watch.browser_launch_cmd`, `watch.wt_restore_cmd` exist in `config.yaml`,
are genuinely read (`daemon.py:763, 770, 774-775`), and are described in the
"Runtime dependencies & self-healing" section — but they are missing from the
"Configuration reference" table, which claims "Every key, and whether the
daemon actually reads it".

Fix: add the four rows (all "yes").

---

## Adjacent findings (not README claims, but nearby)

- `pbclient.py:30` documents `run_cards <- reflect.write_run_card`, but no
  code writes a `run_cards` collection (`reflect.write_run_card` is
  file-only); the `market_cache` collection is only populated by the CLI
  `_import_dir` helper (`pbclient.py:410`), not by live write-through. The
  README itself makes no collection-level claim, so this is a docstring drift
  inside `pbclient.py`.
- `console/server.py` exposes an extra `POST /api/ctl/unkill` action the
  README's console summary doesn't mention (purely additive omission).
- README launchd bullet "Verified live: `kill -9 <pid>` → launchd restarts
  within ~30s" is a historical runtime observation — not verifiable from
  code, but consistent with `KeepAlive` + `ThrottleInterval 30`.

## Test-run record

```
$ cd agents/grid-autonomy && python3 -m unittest discover -s tests -t .
Ran 188 tests in 48.857s
OK
```
