---
name: grid-autonomy
description: Operate the agents/grid-autonomy daemon — the autonomous grid-trading portfolio manager that screens Hyperliquid perps + Binance spot, deliberates through a TradingAgents-style LLM swarm (CF → Nvidia → OpenRouter with rule fallback), fails closed through 8 guardrails, deploys paper grid bots on WunderTrading, watches them, rotates stagnant incumbents, and writes decisions.jsonl + run cards. Use when asked to start/stop/status/rotate/kill the daemon, read its run cards or decision journal, troubleshoot the loop, or explain its safety rails and paper→live escalation.
---

# grid-autonomy — operate the autonomous grid-trading daemon

One daemon (`agents/grid-autonomy/daemon.py`) runs the whole loop:
**screen → deliberate → guard → deploy → watch → rotate → reflect**. Full
operating manual: `agents/grid-autonomy/README.md`. It executes on
WunderTrading **paper profiles only** unless an operator lifts the live gate.

## What it does

- **Screen (60m):** `screen/merge.py` screens Hyperliquid perps + Binance
  spot — broad universe (≥ `screen.min_volume_usd`, top
  `screen.universe_max_symbols` by 24h volume; universe fetches retry with
  backoff and fail soft per venue), regime + preset score, real spreads,
  numeric tvcli `/hunt` fitness (squeeze/choppiness/mtf-confluence/dvi —
  "moves large & fast" bonuses, cap +6, fail-soft), 4h trend confirmation,
  dead-tape floor + expected-value grid-fill pass. When a venue's slots are
  full but a token scores ≥ `screen.open_slot_min_score` and deployable
  capital is spare, the daemon opens another slot (up to
  `portfolio.slots_max`). On every startup, `reconcile_slots` re-normalizes
  the persisted slot budgets to the current `portfolio.venues`/`total_usd`
  (journal `slots-reconciled`) so config edits actually reach the fleet on
  restart.
- **Deliberate:** `agents/swarm.py` runs bull/bear debate → facilitator →
  3-stance risk team via `llm/provider.py`; rule-based fallback on LLM
  outage (`llm_degraded: true`). `agents/reflect.py` injects k=3 memories.
- **Guard:** `execution/guardrails.py` — 8 fail-closed gates (KILL, pairCode,
  profile, sizing, spread, venue/side, reliability, rotation). Any veto
  blocks deployment.
- **Deploy:** plan-capacity pre-check (`observe.grid_capacity()` →
  `capacity-veto` — free plan: 1 active grid bot on non-Hyperliquid
  exchanges; HYPERLIQUID_SWAP is premium/200 via WT's 0.035% builder-fee
  arrangement; rotation frees capacity before the challenger create), then
  `execution/grid_adapter.py` + `resolve.py` — ATR-band channel,
  geometric grid lines, USD-denominated sizing, per-pair min-notional
  floor from `:2087` market metadata.
- **Watch (60s):** `execution/observe.py` reads real status/positions/
  history → per-token stagnation policy (`policy/stagnation.py`) →
  in-place re-centre (6h rate limit) or re-analysis.
- **Rotate:** stagnant incumbent + challenger Δscore ≥ 5 + cooldown expired
  → stop → verify → delete → cooldown → deploy.
- **Reflect:** `state/decisions.jsonl` + run cards
  `state/reports/<ts>-<kind>.{json,md}`. Subscription state (account-limits
  dashboard + enforced tier caps) is re-observed every rescreen cycle and
  journaled as `subscription` on any change (`/status` → `capacity` +
  `account_limits`).

## Operate

All commands from `agents/grid-autonomy/`.

**The single dev script** (preferred entry point — manages daemon +
console + PocketBase + tvcli serve, all launchd-supervised after
`scripts/install_launchd.sh`):

```sh
./dev status              # health of every component + last journal lines
./dev start [--dry-run]   # start the whole stack (live-paper default)
./dev stop [--all]        # stop console + daemon + PB (--all + tvcli serve)
./dev restart             # stop + start
./dev restart console     # restart ONE component (daemon|console|pb|serve)
                          # — `dev restart console` reloads console code
                          # changes (server.py/static/*) without touching
                          # the daemon; `dev restart daemon` applies
                          # config.yaml edits (stop → PB up → start)
./dev logs daemon|console|pb|serve [-f]   # tail any component log
./dev reset [--wt|--no-wt] [--keep-decisions] [--start]  # wipe runtime state
                          # (interactive runs ask about the WT reset too)
./dev reset-wt            # delete ALL WT paper bots + clear daemon bot state
./dev clean [--all]       # clear logs, run cards, market caches, specs
./dev config check        # validate config.yaml + daemon-vs-file drift
./dev config set <path> <value> [--restart]  # whitelisted edit, like the console
```

Everything the system writes stays inside `agents/grid-autonomy/`
(`state/`, `state/logs/`, `.pocketbase/`); the only external footprint is
the launchd registration. The console's Dev-maintenance panel runs the same
`dev` commands via `POST /api/dev/{reset,reset-wt,clean}` (detached,
confirm-gated, output in `state/logs/dev.log`).

Lower-level equivalents:

```sh
scripts/start.sh                # dry-run planning (default; creates nothing)
scripts/start.sh --live-paper   # actually create paper bots
scripts/stop.sh                 # POST /kill + SIGTERM (escalates to SIGKILL)
scripts/smoke.sh                # one-shot dry-run E2E smoke (live public data)
python3 daemon.py --once --no-confluence --top 5   # one-shot smoke (same, explicit)
python3 console/server.py       # mission console UI on :8798 (observe/configure/control)
```

Control plane on `:8799`:

| Method | Path | Effect |
|--------|------|--------|
| GET | `/health` | Liveness + KILL presence. |
| GET | `/status` | Slots, active bots, `live_allow`, profiles, journal tail. |
| GET | `/reliability` | Reliability ledger. |
| GET | `/observe` | Latest observation snapshot. |
| POST | `/rescreen` | Queue a rescreen cycle. |
| POST | `/reliability` | Queue an immediate reliability-ledger refresh. |
| POST | `/rotate` | Force-rotate: body `{"slot": n}`. |
| POST | `/kill` | Write KILL file (daemon halts next tick). |

Hard stop: `touch agents/grid-autonomy/KILL` (clear with `rm -f` before
restart). `start.sh` imports `CLOUDFLARE_ACCOUNT_ID` plus
`CLOUDFLARE_API_KEY` (or `CLOUDFLARE_AI_TOKEN`) from the `dsh web` process
env and refuses to start without them. `stop.sh` also stops the PocketBase
side channel. The daemon refuses to start while another live process holds
`state/daemon.pid` (override: `GRID_NO_PIDGUARD=1`).

**Supervision (crash + reboot survival):** `scripts/install_launchd.sh`
installs two per-user LaunchAgents — `com.tvcli.grid-autonomy`
(`scripts/run_launchd.py`, foreground `--live-paper`) and `com.tvcli.serve`
(`tvcli serve`, the `:8765` confluence backend). `KeepAlive
{SuccessfulExit: false}` restarts only on crashes (verified: `kill -9`
→ restart in ~30s); `stop.sh`/SIGTERM exits 0 and stays stopped; a leftover
KILL file blocks startup on purpose. macOS TCC: the repo is on a removable
volume, so the agent must run under Homebrew python3 (`/opt/homebrew/bin/
python3` holds the Removable Volumes grant; launchd bash cannot read the
volume). `run_launchd.py` also imports the CF Workers-AI keys from the
`dsh web` process env and sources `.pocketbase/pb.env` at boot.

**Dependency self-healing (in-daemon, verified 2026-09-05):** the daemon
depends on the headful **CloakBrowser on CDP :9222** (WT cookie session in
the `minimal-mjs/profile` profile) for ALL WunderTrading session-API calls.
`health_cycle` probes CDP every 60 s and relaunches the browser + re-asserts
the WT page (≤1 try/10 min; journal `browser-restart`; config
`watch.browser_*`). `self_heal_env()` re-imports CF/PB env each rescreen
when missing (journal `env-heal`). ~30 min of total observation blindness
journals a loud `observe-outage` entry. Observe errors distinguish
`grid status list unavailable (browser/session down)` (transport/login
down) from `grid resource not found in status list` (bot actually gone).
The WT `PHPSESSID` cookie expires ~weekly — after expiry a manual
re-login in the browser window (or vault `wundertrading-session` →
`browser-debug/secrets/runtime/wt-session.env`) is required.

## Read run cards + journal

- **Run card** `.md`: title, TL;DR, then Route / Ground / Deliberate /
  Guard / Deploy / Observe / Reflect / Caveats sections (tables). The
  sibling `.json` is the exact cycle report.
- **Journal:** `state/state.json → journal` (last 200 events) and
  `GET /status → journal_tail`. Kinds: `screen`, `veto`, `guard-veto`,
  `reliability-veto`, `deploy-paper`, `deploy-failed`, `adopted`, `stagnant`,
  `adjust`, `rotation-stop/delete/rotate`, `capacity-veto` (plan bot cap),
  `subscription` (plan limits observed/changed), `tier-cap` (tier grid
  density cap applied), `reliability-migrate` (ledger key normalization),
  `slot-open`/`slot-open-veto` (dynamic venue slot opening),
  `slots-reconciled` (config edits re-normalized slot budgets at startup),
  `browser-restart`,
  `env-heal`, `observe-outage`, `kill`, and more. `stagnant` and
  `re-analysis` log once per state transition, not every 60 s sweep.
- **Decision journal:** `state/decisions.jsonl` — one line per decision;
  `record_outcome` attaches `"outcome"` on close. Ids are
  `dYYYYMMDD-NNN`. `payload_digest` is an md5 — full payloads are never
  stored verbatim.
- **How to spot trouble:** `llm_degraded: true` = rule fallback (not fatal);
  `veto`/`guard-veto` = blocked by guardrail; `stagnant` = rotation candidate;
  `rotation-veto`/`rotation-skip` = rotation blocked.

## Key file map

| Path | Role |
|------|------|
| `daemon.py` | Scheduler + orchestrator (Daemon class, the manage loop). |
| `config_lite.py` | Stdlib-only YAML-subset parser. |
| `ctl_http.py` | HTTP ctl plane on :8799 (health/status/rotate/kill). |
| `console/` | Mission console on :8798 — web UI (fleet/ladder cards, decision ledger, run cards, reliability, config editor, logs) + JSON API over the same state; whitelisted config.yaml edits and confirm-gated daemon lifecycle ops (`console/README.md`). |
| `config.yaml` | Portfolio, venues, policy defaults, port 8799. |
| `screen/merge.py` | Parallel screen + confluence + 4h confirm. |
| `agents/swarm.py`, `agents/reflect.py` | Deliberation + memory/run cards. |
| `llm/provider.py` | CF → Nvidia → OpenRouter chain. |
| `execution/guardrails.py` | 8 fail-closed gates. |
| `execution/grid_adapter.py`, `resolve.py` | Deploy payloads + pairCode. |
| `execution/observe.py`, `reliability_grid.py` | Watch + reliability ledger. |
| `policy/stagnation.py` | Stagnation policy + slot allocator. |
| `scripts/start.sh`, `stop.sh`, `smoke.sh` | Start / stop / one-shot dry-run smoke. |
| `state/` | Runtime state, journal, reports, caches (not source). |

## Safety rails

- Paper-only by default: `autonomy.live_profiles: []`; daemon calls
  `select_profile(..., paper=True)` only; real HL profile
  `c629f5ba3a643a82137e7864` hard-denylisted.
- 8 fail-closed gates; no WunderTrading mutation before they all pass.
- Allocation ladder: base 25% (<10 samples) → probe 40% (≥10) → full 50%
  (≥30, PF ≥ 1.3); tiers also cap grid density
  (`autonomy.tier_max_grids` 12/20/30 lines). `recent_pf < 1.0` kills the
  archetype, but only with ≥ `reliability.kill_min_samples` (default 10)
  closed trips — one losing round-trip must not ban a regime forever.
- Worst-case commitment ≤ slot cap and portfolio ≤ 85% ceiling; step ≥ 2×
  spread; per-pair `limits.cost.min` as the floor.
- Binance sleeve runs on `demo-bn` (`BINANCE_FUTURES` paper) because
  WunderTrading has no Binance spot paper mode; the spot-like no-Short rule
  is still enforced.
- Tests: `python3 -m unittest discover -s tests -t .` (or `pytest tests/`) — 271 offline tests as of 2026-09-05; trust the runner output over any count in docs.
