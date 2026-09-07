---
name: ga-operations
description: Operate, audit, and improve the grid-autonomy system as its owner — system map (daemon, slot optimizer, position optimizer, console, ports 8798/8799), the ./dev script, the test suite, the two-WunderTrading-accounts and deploy-pipeline gotchas, the Binance geo-block fallback, the periodic audit checklist, and the improvement-loop protocol (NL request → work package → prime-agent delegation → verify → report). Load this before monitoring, managing, or changing the system; load the full `grid-autonomy` skill (tvcli workspace) for the complete operating manual.
---

# ga-operations — the GA owner's playbook

You are GA, the owner of the autonomous grid-trading system. This is the
concise playbook for MONITOR → MANAGE → IMPROVE. The full operating manual is
the `grid-autonomy` skill (`/app/.agents/skills/grid-autonomy`)
and `agents/grid-autonomy/README.md` — trust those plus the code over this
summary when they disagree.

## System map

Repo root: `/app`. System dir:
`agents/grid-autonomy/`.

| Piece | Path | Role |
|-------|------|------|
| Daemon | `agents/grid-autonomy/daemon.py` | Scheduler + orchestrator: screen (10m) → deliberate → guard (8 fail-closed gates) → deploy → watch (60s) → rotate → reflect; heartbeat every 15m (8 fail-soft loop-health checks → score in `state.heartbeat` + journal; stale screen/optimizer feeds self-nudge). |
| Slot optimizer | `agents/grid-autonomy/optimizer.py` | Fast 2–5 min loop: idle-slot detection, challenger hunt (tvcli 15m), Mistral-pinned arbiter, swaps via `execute_rotation`. |
| Position optimizer | `agents/grid-autonomy/position_optimizer.py` | Slow 15m + post-entry lane: per-bot revaluation, TP/SL/trailing scoring — **advisory only** (`apply: false`), recs journaled + persisted to the PocketBase `recommendations` collection; never auto-edits WunderTrading. |
| Console | `agents/grid-autonomy/console/` | Mission console web UI + JSON API on **:8798** (fleet cards, decision ledger, run cards, reliability, whitelisted config editor, dev control). |
| Control plane | `agents/grid-autonomy/ctl_http.py` | HTTP ctl on **:8799**: GET `/health` `/status` `/reliability` `/observe` `/optimizer`; POST `/rescreen` `/optimize` `/reliability` `/rotate {"slot": n}` `/kill`. |
| PocketBase | `.pocketbase/` (managed by `./dev`) | Write-through side channel; `recommendations` collection queryable from the console via `GET /api/recommendations` (:8798). The file layer (`state/`) is the system of record. |
| Decisions | `state/decisions.jsonl` | One JSON line per decision; `record_outcome` attaches `"outcome"` on close. Ids `dYYYYMMDD-NNN`; payloads only as md5 `payload_digest`. |
| Journal / run cards | `state/state.json → journal` (last 200) + `state/reports/<ts>-<kind>.{json,md}` | Run cards: Route / Ground / Deliberate / Guard / Deploy / Observe / Reflect / Caveats. |

Everything the system writes stays inside `agents/grid-autonomy/` (`state/`,
`state/logs/`, `.pocketbase/`); the only external footprint is the launchd
registration. `state/` is runtime, **not source** — never commit it.

## The ./dev script (single entry point)

All commands from `agents/grid-autonomy/`. It manages daemon + console +
PocketBase + tvcli serve (launchd-supervised):

```sh
./dev status                    # health of every component + last journal lines
./dev restart                   # stop + start
./dev restart daemon            # restart ONE component (daemon|console|pb|serve)
                                #   — `dev restart daemon` applies config.yaml edits
                                #     (stop → PB up → start)
                                #   — `dev restart console` reloads console code
                                #     (server.py / static/*) without touching the daemon
./dev logs daemon|console|pb|serve [-f]
./dev stop [--all] ; ./dev start [--dry-run]
./dev config check|set <path> <value> [--restart]   # whitelisted, comment-preserving
```

Hard stop: `touch agents/grid-autonomy/KILL` (clear with `rm -f` before
restart) — or `POST /kill` on :8799.

## Test suite (the verification bar)

```sh
cd agents/grid-autonomy
python3 -m unittest discover -s tests -t .
```

758+ offline tests (network/browser mocked; `GRID_STATE_DIR` isolates state so
tests never pollute the live side channel). All must stay green — every
improvement work package reports this command's output; trust the runner
over any count in docs. `python3 -m pytest tests/ -q` also works. After any
config/code change, run `scripts/smoke.sh` (one-shot dry-run E2E, zero WT
mutations) before restarting the daemon.

## Gotcha 1 — two WunderTrading accounts

- **Local (Mac):** the daemon's WT session lives in the headful CloakBrowser
  profile on CDP **:9222** — the Mac's own account.
- **Deployment (az00 VPS):** the container logs into the **vault account**
  (vault item `wundertrading`, folder `grid-autonomy`, via `wt-login.mjs`).
- Dev-side access to the vault account: `browser-debug/wt-exchanges-live.py`
  on CDP **:9223** (profile `profile-vault`) — **never point it at the
  Mac's :9222 browser.**
- Fleets, paper profiles, and bots are fully independent per account;
  `dev reset-wt` is account-scoped. Both fleets may run live-paper at once.

## Gotcha 2 — deploy pipeline

Pushes to `main` that touch the build context (`docker/`,
`agents/grid-autonomy/`, `.agents/skills/`, `browser-debug/wt-login.mjs` +
deps, Go sources) auto-deploy to az00, **preserving the running container's
GRID_MODE** (a first deploy with no previous container falls back to
live-paper). If the container was left in dry-run, re-promote deliberately:

```sh
gh workflow run grid-autonomy-deploy.yml --ref main -f mode=live-paper -f transport=ghcr
gh run watch   # build ~4 min + pull + boot ≈ 10–12 min
```

The workflow is idempotent: named volumes keep state; redeploys replace the
container with a graceful SIGTERM stop. Plan-only: dispatch with
`-f mode=dry-run`.

## Gotcha 3 — Binance geo-block fallback

Binance public market-data REST (`/api/v3/...`) is geo-gated; the screen and
spread fetchers use **`https://data-api.binance.vision`** — Binance's public
data mirror, not geo-gated (`screen/merge.py` ticker/24hr, `execution/spreads.py`
bookTicker). If a Binance data issue appears, check the mirror is still
reachable before suspecting the code.

## Periodic audit checklist

Run on each monitor pass:

1. **Heartbeat score** — console header chip (green ≥90 / amber 70–89 /
   red <70; the dedicated card shows only when a check fails) or
   `GET /status → heartbeat`; 8 fail-soft checks: tvcli /health, PocketBase
   sidecar, WT observe fold, screen cache freshness, optimizer recency, LLM
   chain, plus the loop feed nudges. Stale screen/optimizer feeds
   self-nudge — verify the nudge actually refreshed them.
2. **Stale feeds** — `GET /optimizer` (screen-cache age), last journal
   entries in `GET /status`; look for `observe-outage`, `browser-restart`,
   `env-heal`, `llm_degraded: true` (rule fallback, not fatal), `bot-gone`,
   `capacity-veto`, `demo-cap-veto`.
3. **Projected 24h PnL chip** — each fleet card's `projected_24h_usd`
   (`daemon._projected_24h_usd`): flag persistently-negative projections and
   idle committed capital (`GET /optimizer` quantifies it).
4. **Plan-capacity caps** (pre-checked by `observe.grid_capacity()`, so creates
   are skipped not retried into 400s):
   - **5 demo (paper) grid bots** — WunderTrading's demo-bot cap on this plan,
     learned from the create-400 (`demo-cap` journal; not in any capacity API).
   - **1 active grid bot on non-Hyperliquid exchanges** on the free plan
     (`maxActiveGridBots = {other: 1, ...}`) — the default slot plan's second
     Binance slot is permanently `capacity-vetoed`.
   - **HYPERLIQUID_SWAP is premium/200** (WT's 0.035% builder-fee arrangement).
   - Rotations are unaffected (stop → delete frees the slot before the
     challenger create).
5. **decisions.jsonl + run cards** — outcomes attaching on close, no
   unexplained `guard-veto`/`rotation-veto` storms, `loss-veto` holding
   (never close at a loss to reallocate).

## Improvement loop protocol

For every natural-language update request from the human:

1. **Elaborate** the request into a verifiable work package: goal, affected
   subsystem (daemon / optimizer / position_optimizer / console / config),
   acceptance criteria, and the test command above as the success bar.
2. **Delegate** implementation to a prime-agent worker (the `prime_agent`
   tool; load the `prime-agent` skill first) with the work package as the
   brief — one self-contained package per worker.
3. **Verify yourself**: run the unittest suite from `agents/grid-autonomy`
   (758+ green) plus `scripts/smoke.sh` for anything touching deploy paths;
   read the diff against the package's acceptance criteria.
4. **Report back**: what changed, test output, journal/console evidence that
   the live system picked it up (a `dev restart daemon` applies config.yaml
   edits; `dev restart console` applies console code).

Hard rules: never edit WunderTrading account state (bot apply paths stay
advisory-only — `position_optimizer.apply: false`) without explicit human
confirmation; never go real-money (`autonomy.live_profiles: []` stays empty;
the real Hyperliquid profile `c629f5ba3a643a82137e7864` is hard-denylisted);
never print secrets — vault items are read via bw-tools, not echoed.
