# Watchdog change log

Every code change by the watchdog gets an entry here.
Attribution rules (see README §6): a profit delta may be attributed to a
change only after ≥ 6 h AND ≥ 3 post-change LEDGER snapshots; report as an
observation with market context, never proven causation. A change that
looks harmful over a full observation window gets reverted (from
`watchdog/snapshots/`) and the revert recorded here.

## Coordination record

- 2026-09-06T01:50Z–05:20Z: orchestrator hold — watchdog beats 22–33 were strictly observe-only (no code edits, no component restarts by the watchdog) while a parallel 3-worker audit-fix batch landed (daemon.py, config.yaml, ctl_http.py, pbclient.py, execution/observe.py, execution/reliability_grid.py, scripts/repair_ledger.py, console/**). Normal protocol resumed 05:20Z (orchestrator decision; issuing session had left the roster). Standing foreign-edit check rule active — see STATE.md.

## Active changes

- 2026-09-06T13:50Z — wd-beat-89 — tests/test_optimizer.py hermetic test fix (orchestrator-authorized)
  - what: test_optimizer_cycle_through_daemon now injects a hermetic hunter (identity refresh_one, no apply_structure) via run_cycle's documented hunter= seam.
  - why (classification: STALE/FLAKY TEST, not a 12:16 regression): the daemon-wired test ran the default FastHunter against LIVE market data; with fresh-vs-fresh swap margins (the incumbent's score is re-hunted every cycle), a strong PUMP tape made the veto fire (live repro 13:40Z: fresh PUMP ~98.6 vs challenger SOL 90 → Δ−5.6 < band 5). Test runtime dropped 11.6s → 0.66s after the fix — the network dependency is gone.
  - fix rationale: FastHunter's contract says "injected callables so unit tests run without network"; the test simply never used it. Assertions unchanged — still verifies idle → eligible challenger → band → rule arbiter → execute_rotation (stop/verify/delete/create) end-to-end.
  - deploy: with the suite green (520/520), daemon restarted 13:49:42Z (pid 36330) deploying BOTH the beat-88 exchangeCode fix (code) and this (test-only). No in-flight rotation at restart.
  - outcome (exchangeCode fix): VERIFIED LIVE 14:06:15Z — binance:GIGGLE watch-lane recenter succeeded ("adjust ... re-centered at 39.75"), the exact edit class that 400'd at 12:50:57Z pre-fix. Closed.
  - snapshots: 20260906T134830Z-test_optimizer.py (pre-edit).

- 2026-09-06T13:31Z — wd-beat-88

- 2026-09-06T13:31Z — wd-beat-88 — daemon.py `adjust_bot` (spot/futures grid-market mismatch fix) + tests/test_daemon_manage.py
  - what: adjust_bot now passes the bot's stored deploy `upsert.exchangeCode` into compute_upsert (was: static venue default — binance→"BINANCE"→gridMarket=spot).
  - why: binance-venue bots on the BINANCE_FUTURES paper profile edit with gridMarket=spot and WT 400s on missing investmentRef/investmentBase (live 12:50:57Z, slot 3 GIGGLE — bot's real gridMarket is derivative, verified via live grid_status).
  - status: DEPLOYED 13:49:42Z (pid 36330) after the suite went green — see the wd-beat-89 entry below for the test-flake root cause and fix.
  - snapshots: 20260906T132002Z-daemon.py + 20260906T132002Z-test_daemon_manage.py (pre-edit).

## Active changes

- 2026-09-06T13:50Z — wd-beat-89 — tests/test_optimizer.py hermetic test fix (orchestrator-authorized)
  - what: test_optimizer_cycle_through_daemon now injects a hermetic hunter (identity refresh_one, no apply_structure) via run_cycle's documented hunter= seam.
  - why (classification: STALE/FLAKY TEST, not a 12:16 regression): the daemon-wired test ran the default FastHunter against LIVE market data; with fresh-vs-fresh swap margins (the incumbent's score is re-hunted every cycle), a strong PUMP tape made the veto fire (live repro 13:40Z: fresh PUMP ~98.6 vs challenger SOL 90 → Δ−5.6 < band 5). Test runtime dropped 11.6s → 0.66s after the fix — the network dependency is gone.
  - fix rationale: FastHunter's contract says "injected callables so unit tests run without network"; the test simply never used it. Assertions unchanged — still verifies idle → eligible challenger → band → rule arbiter → execute_rotation (stop/verify/delete/create) end-to-end.
  - deploy: with the suite green (520/520), daemon restarted 13:49:42Z (pid 36330) deploying BOTH the beat-88 exchangeCode fix (code) and this (test-only). No in-flight rotation at restart.
  - outcome (exchangeCode fix): VERIFIED LIVE 14:06:15Z — binance:GIGGLE watch-lane recenter succeeded ("adjust ... re-centered at 39.75"), the exact edit class that 400'd at 12:50:57Z pre-fix. Closed.
  - snapshots: 20260906T134830Z-test_optimizer.py (pre-edit).

- 2026-09-06T13:31Z — wd-beat-88

- 2026-09-06T01:06Z — wd-beat-17 — execution/observe.py + execution/reliability_grid.py + tests (orchestrator hand-off, HIGH)
  - what: closed round-trips now include WT's "panic_exited" status (stop_and_close_all leftovers) in BOTH realized-PnL accounting (`observe._closed_round_trips` → realized_pnl/fills) and the reliability ledger (`reliability_grid.parse_trades`). CLOSED_STATUSES = ("completed", "panic_exited") defined in each module, documented to stay in sync.
  - why: the `!= "completed"` filter dropped real PnL from panic closes — fleet realized was overstated ~+$0.72 (orchestrator audit: FARTCOIN panic close −$1.02 contributed 0). Verified live 2026-09-06 over all 5 active + 8 deleted/reachable bots: vocabulary is exactly {completed, panic_exited}; panic_exited sum −7229 raw = −$0.7229.
  - expected_effect: honest realized numbers; profit-exit and reliability tiers evaluate on true PnL. Rules unchanged (accounting honesty only — loss-veto/profit-exit semantics untouched).
  - MEASUREMENT DISCONTINUITY (LEDGER discipline): realized_usd may JUMP DOWN at any beat where a bot's panic closes exist (deleted-bot history is not in /observe, so active-bot realized stays $0.9609 until a rotation/stop actually produces a panic close on a live bot). Future attribution must not blame trading code for that jump — it is this accounting change.
  - measure_by: after any stop_and_close_all rotation, the stopped bot's realized in LEDGER reflects its panic-close PnL (vs the old 0); reliability PF/tier recomputes include panic trips.
  - outcome: DEPLOYED 01:05:20Z (restart pid 61537, 444 tests green pre-restart, no in-flight rotation). Post-restart: daemon healthy, realized unchanged ($0.9609 — expected, active bots have only completed trips). Note: reliability.json was built with the old filter; it self-corrects on future archive/recompute cycles — watch tier shifts.
  - snapshots: 20260906T010339Z-{observe.py, reliability_grid.py, test_reliability_grid.py}; tests/test_observe_closed_trips.py is a new file.

- 2026-09-05T23:31Z — wd-beat-7 — daemon.py `reconcile_slots` fill-tracker heal + tests/test_daemon_manage.py
  - what: at daemon start, clear any slot fill-tracker whose last_increase_at predates the occupying bot's deploy time (pure inherited staleness; genuine newer trackers stay).
  - why: the beat-5 deploy-time reset only protects NEW deploys — the live XVG (and its successor GIGGLE, plus DOGE on slot 1) carried pre-deploy counters that kept flagging false idles; XVG was churned out after 56 min because of it (23:30:22Z swap).
  - measure_by: journal shows the heal line at restart; no optimizer-idle flag on a bot younger than its idle threshold afterwards.
  - outcome: DEPLOYED 23:30:53Z — "fill-tracker heal (stale inheritance): slots 1, 3" journaled; GIGGLE + DOGE idle clocks restarted at deploy time.
  - snapshots: 20260905T232810Z-daemon.py + 20260905T232810Z-test_daemon_manage.py (pre-edit).

- 2026-09-05T23:21Z — wd-beat-6 — daemon.py `adopt_existing` + `reconcile_slots` + tests in tests/test_daemon_manage.py
  - what: (a) forward fix — adopt_existing now writes committed[slot] = slot max_commitment and clears the slot fill tracker at adoption; (b) heal — reconcile_slots (runs at daemon start) backfills committed for every ACTIVE slot missing an entry, using the slot's max_commitment (conservative, fail-closed).
  - why: adopted slot 2 (CHIP) never entered state.committed → open_slot spare = ceiling − committed − reserved overstated deployable capital by ~$50 (adopted bots are in neither committed nor reserved). Also folds backlog "adopted bots inherit stale trackers" into the adoption path.
  - expected_effect: spare-capital math accounts for all 5 active bots; the heal will journal "committed-capital heal (adopted bots): slot 2 $50" at first restart.
  - measure_by: after restart, state.committed contains all active slots; journal shows the heal line once; no new over-deployment.
  - outcome: DEPLOYED 23:30:53Z — heal fired live: "committed-capital heal (adopted bots): slot 2 $50.0" at 23:30:53Z; state.committed now covers all 5 active slots {7,1,5,3,2}.
  - snapshots: 20260905T231848Z-daemon.py (pre-edit) + 20260905T231848Z-test_daemon_manage.py (pre-edit).

- 2026-09-05T23:05Z — wd-beat-5 — daemon.py `commit_deploy` + new tests/test_slot_tracker_reset.py
  - what: on a successful deploy, clear the slot's optimizer fill tracker (`state["optimizer"]["trackers"].pop(slot)`), so the new bot's idle clock restarts at deploy instead of inheriting the previous occupant's `last_increase_at`.
  - why: stale tracker flagged XVG idle at age 21m ("no fills for 272m" — 22:55:52Z journal, counter predates even the ROBO deploy that preceded it); the same staleness fed the premature ROBO→XVG swap decision. This is a churn driver, not just noise.
  - expected_effect: no premature idle flags on freshly deployed bots; min_hold 20m + idle threshold now measured from deploy/first-fill, as designed.
  - measure_by: after the restart, new deploys should NOT journal optimizer-idle within their first threshold window; swap count for age<threshold bots = 0.
  - outcome: DEPLOYED 23:30:53Z. Live: the 23:30:22Z XVG→GIGGLE swap was the last premature-churn instance (pre-restart code); fill-tracker heal cleared GIGGLE's inherited counter at startup, so its idle clock now runs from deploy. No optimizer-idle flag on slot 3 since the heal (watching).
  - snapshot: PROTOCOL SLIP — beat 5 edited daemon.py without a fresh pre-edit cp (only beat-4's 20260905T224929Z-daemon.py existed). Mitigated: reconstructed the exact pre-beat-5 state (beat-4 snapshot + beat-4 documented diff) and verified byte-identical → watchdog/snapshots/20260905T230900Z-daemon.py.pre-beat5.reconstructed. tests/test_slot_tracker_reset.py is a new file (no pre-edit state). Future daemon.py edits: cp FIRST, always.
  - note: adopt_existing installs bots without commit_deploy — adopted bots can still inherit a stale tracker; rare (first-run only), left for a later beat if it ever bites.

- 2026-09-05T22:50Z — wd-beat-4 — daemon.py `adjust_bot` + tests/test_daemon_manage.py
  - what: throttle the "adjust-skip rate limit (1 edit/6h)" journal entry to at most once per hour per slot (in-memory `_adjust_skip_logged`; behavior unchanged otherwise).
  - why: the fast optimizer re-proposes the rate-limited LTC recenter every ~72s; at that rate the spam (~275 lines over the 6h window) evicts the entire 200-entry rolling journal, destroying signal. Evidence: journal 22:35:29Z→22:46:31Z, 9 identical lines.
  - expected_effect: journal stays readable during the 6h rate-limit window; LTC skip still visible 1×/h.
  - measure_by: count of adjust-skip entries per hour in the journal after the daemon restart (pre-fix: ~50/h).
  - outcome: VERIFIED LIVE 23:42-23:47Z — GRAM's rate-limited recenter journaled exactly ONE adjust-skip line (23:42:20Z) with no repeat over the next 5+ minutes of fast-lane cycles (pre-fix rate was ~1 line/72s). Throttle confirmed working in production.
  - snapshot: 20260905T224929Z-daemon.py

- 2026-09-05T22:31Z — wd-beat-2 — position_optimizer.py `_default_fetch` + new tests/test_position_fetch_retry.py
  - what: single retry (1.5 s backoff) on transient transport errors (SSL handshake timeout / connection reset / refusal) in the live candle fetch; persistent or non-transient errors (e.g. HTTP 400) still raise immediately.
  - why: 22:24:36Z position-optimizer analysis (slot 3 ROBO) failed on a one-off SSL handshake timeout; one network flake was skipping the whole 15-min analysis.
  - expected_effect: fewer skipped analyses; next-cycle retry becomes same-cycle recovery.
  - measure_by: count of "analysis failed" journal entries with transient-error text over ≥6 h / ≥3 snapshots (beat-1 pre-change comparison: 2 entries — 1×400, 1×timeout).
  - outcome: verified-enough, closed 22:49Z — retry code live since 22:30:26Z restart, exercised successfully on the 22:34:18Z post-deploy XVG analysis, 434 tests green, zero new "analysis failed" journal entries (only the 2 pre-fix ones from 22:03/22:24Z remain). Periodic ticks at 22:45Z had nothing eligible (60-min per-bot cooldown); real periodic re-analysis resumes ~23:03Z+ and is covered by routine beat watching.
  - note: the 22:30:26Z restart cut a slot-3 ROBO→XVG rotation between stop and challenger deploy (stop landed 22:30:23Z). Slot 3 left stopped; idle detection already flagged it and the fast optimizer should refill. Lesson: check journal for in-flight rotations before restarting the daemon.

## Goal archive

| goal | objective | opened | closed | outcome |
|------|-----------|--------|--------|---------|
| WD-005 | Fix grid-edit success-blind journaling (hand-off #2) | 2026-09-06T11:42Z | 2026-09-06T11:49Z | complete — FIXED BY PARALLEL BATCH, verified read-only by watchdog: both paths (adjust_bot + PO apply) gate all post-call mutations on res.ok, journal error kinds, burn no rate window, mark no rec applied. No watchdog edit made. |
| WD-004 | Fix realized-PnL accounting dropping WT panic closes (orchestrator hand-off) | 2026-09-06T01:03Z | 2026-09-06T01:06Z | complete — vocabulary verified live ({completed, panic_exited}; panic sum −$0.7229), both filters fixed, 3 new tests, 444 green, deployed 01:05:20Z; LEDGER discontinuity note recorded |
| WD-003 | Deploy adjust-skip throttle + tracker reset + committed heal (+beat-7 tracker heal) | 2026-09-05T22:50Z | 2026-09-05T23:31Z | complete — one restart (23:30:53Z, 441 tests green) carried all four disjoint fixes; committed heal + tracker heal verified live in the startup journal; throttle + no-premature-idle verified by routine watching |
| WD-002 | Verify beat-2 fetch-retry + slot-3 refill | 2026-09-05T22:32Z | 2026-09-05T22:49Z | complete — slot 3 refilled ROBO→XVG 22:34:18Z with clean post-deploy analysis; retry fix verified (see Active changes outcome) |
| WD-001 | Verify bootstrap + fix position-optimizer HTTP 400 | 2026-09-05T22:09Z | 2026-09-05T22:24Z | complete — root cause: daemon pid 56960 (started 21:44:49Z, pre-fix code) fetched bare binance symbol ROBO; Binance klines returns HTTP 400 without USDT suffix (reproduced: bare ROBO → 400 on spot+fapi, ROBOUSDT → OK). The `_fetch_symbol` USDT-mangling fix was already added to position_optimizer.py at 21:49:33Z by the orchestrator's uncommitted work; daemon restart 22:05:28Z loaded it. No watchdog code change needed (no snapshot/edit of daemon code). Verified: offline `cycle()` on slot 3 → rec "keep", no error; 431 tests OK; no "analysis failed" journal entries since restart (successful cycles journal nothing by design). Beat 2 re-checks for recurrence. |

## Pre-watchdog changes (context only — attribution starts at LEDGER baseline 2026-09-05T22:08:56Z)

- Uncommitted in tree: position-optimizer integration (position_optimizer.py,
  daemon hooks, TP/SL/trailing kwargs in execution/grid_adapter.py, tests).
- Committed earlier: profit exit + held-bot re-centering + per-line loss veto
  (d944d1c), never-close-at-a-loss rotation veto (6f8ae2f).
