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
  spot in parallel (regime, preset score, real spreads, optional tvcli
  `/hunt` confluence, 4h trend confirmation).
- **Deliberate:** `agents/swarm.py` runs bull/bear debate → facilitator →
  3-stance risk team via `llm/provider.py`; rule-based fallback on LLM
  outage (`llm_degraded: true`). `agents/reflect.py` injects k=3 memories.
- **Guard:** `execution/guardrails.py` — 8 fail-closed gates (KILL, pairCode,
  profile, sizing, spread, venue/side, reliability, rotation). Any veto
  blocks deployment.
- **Deploy:** `execution/grid_adapter.py` + `resolve.py` — ATR-band channel,
  geometric grid lines, USD-denominated sizing, per-pair min-notional
  floor from `:2087` market metadata.
- **Watch (60s):** `execution/observe.py` reads real status/positions/
  history → per-token stagnation policy (`policy/stagnation.py`) →
  in-place re-centre (6h rate limit) or re-analysis.
- **Rotate:** stagnant incumbent + challenger Δscore ≥ 5 + cooldown expired
  → stop → verify → delete → cooldown → deploy.
- **Reflect:** `state/decisions.jsonl` + run cards
  `state/reports/<ts>-<kind>.{json,md}`.

## Operate

All commands from `agents/grid-autonomy/`.

```sh
scripts/start.sh                # dry-run planning (default; creates nothing)
scripts/start.sh --live-paper   # actually create paper bots
scripts/stop.sh                 # POST /kill + SIGTERM (escalates to SIGKILL)
python3 daemon.py --once --no-confluence --top 5   # one-shot smoke (dry-run)
```

Control plane on `:8799`:

| Method | Path | Effect |
|--------|------|--------|
| GET | `/health` | Liveness + KILL presence. |
| GET | `/status` | Slots, active bots, `live_allow`, profiles, journal tail. |
| GET | `/reliability` | Reliability ledger. |
| GET | `/observe` | Latest observation snapshot. |
| POST | `/rescreen` | Queue a rescreen cycle. |
| POST | `/rotate` | Force-rotate: body `{"slot": n}`. |
| POST | `/kill` | Write KILL file (daemon halts next tick). |

Hard stop: `touch agents/grid-autonomy/KILL` (clear with `rm -f` before
restart). `start.sh` imports `CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_API_KEY`
from the `dsh web` process env and refuses to start without them.

## Read run cards + journal

- **Run card** `.md`: title, TL;DR, then Route / Ground / Deliberate /
  Guard / Deploy / Observe / Reflect / Caveats sections (tables). The
  sibling `.json` is the exact cycle report.
- **Journal:** `state/state.json → journal` (last 200 events) and
  `GET /status → journal_tail`. Kinds: `screen`, `veto`, `guard-veto`,
  `reliability-veto`, `deploy-paper`, `adopted`, `stagnant`, `adjust`,
  `rotation-stop/delete/rotate`, `kill`, and more.
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
| `daemon.py` | Scheduler + orchestrator + HTTP ctl. |
| `config.yaml` | Portfolio, venues, policy defaults, port 8799. |
| `screen/merge.py` | Parallel screen + confluence + 4h confirm. |
| `agents/swarm.py`, `agents/reflect.py` | Deliberation + memory/run cards. |
| `llm/provider.py` | CF → Nvidia → OpenRouter chain. |
| `execution/guardrails.py` | 8 fail-closed gates. |
| `execution/grid_adapter.py`, `resolve.py` | Deploy payloads + pairCode. |
| `execution/observe.py`, `reliability_grid.py` | Watch + reliability ledger. |
| `policy/stagnation.py` | Stagnation policy + slot allocator. |
| `scripts/start.sh`, `stop.sh` | Start/stop. |
| `state/` | Runtime state, journal, reports, caches (not source). |

## Safety rails

- Paper-only by default: `autonomy.live_profiles: []`; daemon calls
  `select_profile(..., paper=True)` only; real HL profile
  `c629f5ba3a643a82137e7864` hard-denylisted.
- 8 fail-closed gates; no WunderTrading mutation before they all pass.
- Allocation ladder: base 25% (<10 samples) → probe 40% (≥10) → full 50%
  (≥30, PF ≥ 1.3); `recent_pf < 1.0` kills the archetype.
- Worst-case commitment ≤ slot cap and portfolio ≤ 85% ceiling; step ≥ 2×
  spread; per-pair `limits.cost.min` as the floor.
- Binance sleeve runs on `demo-bn` (`BINANCE_FUTURES` paper) because
  WunderTrading has no Binance spot paper mode; the spot-like no-Short rule
  is still enforced.
- Tests: `python3 -m unittest discover -s tests -t .` → 125 offline tests.
