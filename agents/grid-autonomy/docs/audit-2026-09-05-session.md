# grid-autonomy full review + autonomous E2E verification — 2026-09-05

This session: (1) audited all grid-autonomy docs against the code
(docs/audit-readme.md, docs/audit-supporting-docs.md), (2) ran a full
code review (docs/audit-code-review.md), (3) ran the system autonomously
live and verified every loop stage end-to-end, and (4) fixed the bugs the
live run surfaced. Companion audit reports hold the file:line evidence.

## Autonomous end-to-end verification (live, paper)

Stack state at verification: launchd-supervised daemon (`--live-paper`,
`com.tvcli.grid-autonomy`), tvcli serve on :8765, ctl plane :8799, mission
console :8798, PocketBase side channel :8090, CloakBrowser+WT session on
CDP :9222.

| Stage | Evidence |
|-------|----------|
| Screen | 53 candidates, top hyperliquid:CHIP 119.1 (journal `screen`) |
| Deliberate | LLM chain (CF primary) healthy — `llm_degraded: false` on d20260905-001/002; rule fallback also exercised earlier (d20260904-004) |
| Guard | 8 gates verified live: `capacity-veto` (free-plan `other`=1), `deploy-failed` (WT 400 max-bots), size-floor ($10/line funding) |
| Deploy | PUMP long grid created on demo-hype (d20260904-009, 13 grids, $10/line); CHIP neutral grid after rotation (12 grids, $60 committed) |
| Watch | 60s polls: stagnation flags, regime-change PUMP chop→squeeze, out-of-grid detection on HYPE |
| Rotate | PUMP→CHIP forced rotation executed live: stop → verify → **delete** → cooldown → deploy, outcome attached to d20260904-009 (holding_h 15.0, fills 3) |
| Reflect | decisions.jsonl 12 records, memory recall wired, run cards hourly |
| Reliability | on-demand refresh verified; ledger `Neutral Grid (mean-reversion)` 3 samples PF 99 |
| Recovery | `kill -9`/kickstart restart under launchd re-verified; smoke E2E (25s) after stop; stop.sh/start path exercised |

**Profitability (paper, WunderTrading-exported round-trips):**

| Bot | Closed round-trips | Realized PnL |
|-----|--------------------|--------------|
| HYPE (rotated out, out_of_grid) | 8 | +14.68 USD |
| PUMP (slot 1, rotated to CHIP) | 3 | +0.33 USD |
| ETH (slot 2) | 1 | +0.04 USD |
| SOL (slot 3) | 2 | +0.09 USD |
| **Total** | **14/14 winners** | **+15.15 USD** |

Caveat: this is a 36h paper sample with a benign regime; the daemon's own
reliability gates (≥30 samples, PF ≥ 1.3) correctly keep every archetype at
the 25% base tier. No live-money path exists (live_profiles empty, denylist
enforced).

## Bugs found live and fixed this session

1. **Rotation vetoed after a successful stop** (`stopped_with_unrealized`):
   WT's transient terminal state right after `stop_and_close_all` was not in
   `STOPPED_STATES`, so a manual rotation stopped the bot, then refused to
   delete/deploy — stranding the slot. Reproduced live 10:18Z; fixed, now
   covered by a regression test. (daemon.py)
2. **Unrealized PnL mis-scaled ~10x**: open-position `totalProfitLoss` is
   entry commission, not mark PnL; the /10000 scaling is only valid for
   history rows. observe now prefers the grid resource's authoritative
   `unrealizedPnl.pnlFiat`. (execution/observe.py, test updated)
3. **Reliability ledger dropped rotated bots' trades**: the 24h recompute
   only reaches ACTIVE bots, so HYPE's 8 winners never entered the ledger.
   Rotations now export + archive the incumbent's round-trips
   (`state/reliability_archive.json`, merged into every recompute), and the
   decision outcome records the true realized sum instead of the mis-scaled
   unrealized fallback. (daemon.py, execution/reliability_grid.py)
4. **No single-writer guard**: a `daemon.py --once` run while the launchd
   daemon was live clobbered state.json (observed 09:51Z). The daemon now
   refuses to start when a live foreign pid holds `state/daemon.pid`
   (`GRID_NO_PIDGUARD=1` overrides). (daemon.py)
5. **Blind rotation churn**: observe-error dicts flowed into the rotation
   pass where missing fills default to 0 → fake stagnation during outages.
   Bots whose observation carries an error are now skipped (fail-closed).
   (daemon.py)
6. **ctl plane died silently on bind failure** (EADDRINUSE): now journaled
   as `ctl-error` + stdout instead of a silent thread death. (ctl_http.py)
7. **Tests could pollute the live PocketBase** when ambient PB env was
   sourced (also made the suite 5x slower via live network calls):
   `GRID_STATE_DIR` (test isolation) now disables every PB mirror.
   (daemon.py, agents/reflect.py, execution/reliability_grid.py, tests)

## Docs corrected

- README: test count 129→197 (two places + expected output), 7-vs-8
  guardrails.checks wording, min-notional floor is `max(cost.min, $10)`,
  venue screens are serial not parallel, paper→live "not paperTrading" is
  operator-verified not code-enforced, start.sh also uses macOS `ps -Eww`,
  four missing wired `watch.browser_*` rows added, single-writer runbook
  note, launchd log location, slot-4/binance capacity limitation row,
  `stopped_with_unrealized` + rotation archive + `reliability_archive.json`
  documented.
- SKILL.md: test count, CLOUDFLARE_AI_TOKEN alternative, stop.sh stops PB,
  pidguard note.
- bootstrapping/docs/grid-fleet.md: `POST /reliability` added to ctl list.
- pbclient.py docstring: run_cards/market_cache are CLI-import only, not
  live mirrors.

## Known limitations (documented, deliberately not changed)

- Slot plan is config-driven, not capacity-derived: with the free plan the
  second binance slot can never deploy (capacity-veto each rescreen).
- The 200-entry journal is churned by per-minute stagnation lines (<1h
  window); PocketBase is the durable journal.
- `grid_delete` failures still pop the bot from state (the journal records
  the failure); a stopped residue can remain on WT (e.g. HYPE).
- Adopted bots carry `archetype: null` → "unknown" ledger bucket.
- PB `decisions` records are created (not upserted) — re-running the same
  decision id would duplicate (no current call path does).

## Console readiness panel

The console gained a **readiness strip** (Fleet view): a row of
instrument cells for the daemon's dependency diagnostics — LLM provider
env per chain link, browser CDP, PocketBase, enforced venue capacity
(non-premium 1/1, premium 2/200), connected profiles (the one real-money
profile is flagged red), and worker capabilities. Purely additive
(`_readiness()` derives from the existing ctl `/status` payload);
`console/README.md` updated to match. Verified live on :8798.


---

# Session 2 — console integration, containment, and the `dev` script (2026-09-05, later)

## Console → backend integration (now complete)

- The console is **launchd-supervised** as `com.tvcli.grid-autonomy-console`
  via `scripts/run_console.py` (foreground, in-process stdio redirect to
  `state/logs/console.log`, SIGTERM → exit 0 so stops stay stopped).
  `scripts/install_launchd.sh` installs all three agents (daemon, console,
  tvcli serve) and now waits out the bootout race that previously caused
  EIO-5 bootstrap failures.
- Fixed a latent bug: the console's Logs view always read
  `state/daemon.log` even when the daemon was launchd-supervised — it now
  uses the `_log_source()` chooser and reports which file/source it read.
- The console's **Dev maintenance** panel drives the single `dev` script
  (`POST /api/dev/{reset,reset-wt,clean}`, confirm-gated, detached;
  output in `state/logs/dev.log`).

## Local containment

launchd itself cannot open files on this removable volume (TCC — a plist
`StandardOutPath` here makes the job die with EX_CONFIG 78, verified live).
Both launchd entrypoints (`run_launchd.py`, `run_console.py`) therefore
redirect stdio **in-process** (Homebrew python3 holds the grant) into
`state/logs/`. The plists send early stdio to `/dev/null`. Result: **every
runtime artifact — state, logs, PB data, caches — lives inside
`agents/grid-autonomy/`**; the only external footprint is the launchd
registration in `~/Library/LaunchAgents` and tvcli serve's own repo-level
log.

## The single dev script (`agents/grid-autonomy/dev`)

`status | start [--dry-run] | stop [--all] | restart | logs | reset |
reset-wt | clean` — verified live, command by command:

- `start` brought up PocketBase (fresh token), daemon (launchd
  `--live-paper`), console (launchd), and verified tvcli serve.
- `stop` cleanly stopped console + daemon + PB (tvcli serve kept by
  default).
- `clean` cleared 76 run cards + market caches + logs, keeping
  state.json/decisions.
- `reset-wt` deleted all 5 WunderTrading paper bots (stop → verify →
  delete; two deletes hit a transient WT 403 while positions were still
  closing — now retried with backoff; both deleted on retry) and cleared
  the daemon's phantom bot state. Real-money profiles/bots never touched.
- `reset --keep-decisions --start` wiped runtime state (backup under
  `state/backups/`), preserved the 14-decision learning journal, and
  restarted the full stack — the fresh daemon then re-deployed bots
  autonomously within minutes (CHIP, XMR observed).

Tests: 201 offline, all passing (new: dev-action console endpoints, archive
merge, observe-outage rotation skip, pidguard, stopped_with_unrealized).
