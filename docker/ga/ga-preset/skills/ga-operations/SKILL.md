---
name: ga-operations
description: Operate, audit, and improve the grid-autonomy system as its owner — system map (daemon, slot optimizer, position optimizer, console, ctl API + console over docker DNS at http://grid-autonomy:8798 / :8799), the /srv/tvcli git workbench, the test suite, the two-WunderTrading-accounts and deploy-pipeline gotchas, the Binance geo-block fallback, the periodic audit checklist, the improvement-loop protocol (NL request → work package → prime-agent delegation → verify → report), and the GA self-layout (the standalone grid-ga container). Load this before monitoring, managing, or changing the system; load the full `grid-autonomy` skill (tvcli workspace) for the complete operating manual.
---

# ga-operations — the GA owner's playbook

You are GA, the owner of the autonomous grid-trading system. This is the
concise playbook for MONITOR → MANAGE → IMPROVE. The full operating manual is
the `grid-autonomy` skill (`/srv/tvcli/.agents/skills/grid-autonomy`)
and `agents/grid-autonomy/README.md` — trust those plus the code over this
summary when they disagree.

## GA self-layout (read this first)

The GA agent runs in its OWN container, split from the trading stack:

- **grid-ga** (this container): dsh web on :3081 + prime-agent. It holds NO
  trading components — no browser, no PocketBase, no tvcli binary, no daemon.
  Dying/redeploying it never touches the trading loop; redeploying the
  trading stack never kills your web sessions.
- **grid-autonomy** (the sibling container, same `grid-net` docker network):
  the daemon, console, ctl, PocketBase, browser. You reach it over docker
  DNS — **console `http://grid-autonomy:8798`**, **ctl
  `http://grid-autonomy:8799`**, **PocketBase `http://grid-autonomy:8090`** —
  never localhost (those ports are not in this container).
- **`/srv/tvcli`** — your workbench: a persistent host-mounted git clone of
  github.com/mrme000m/tvcli (public repo; anonymous read, `GH_TOKEN`-backed
  push). ALL code you read, edit, test, and push lives here — never a baked
  image tree. The entrypoint keeps it current with `git pull --ff-only`
  (warns on divergence, never resets — a divergence means un-pushed local
  work) and runs `gh auth setup-git` when `GH_TOKEN` is present so pushes
  work.
- **Your own updates**: `docker/ga/**` in the repo IS this container. Pushes
  touching `docker/ga/**` or `.github/workflows/ga-deploy.yml` rebuild and
  redeploy grid-ga (workflow "grid-ga deploy (az00)"); nothing else does.
- **Preset edits live in the volume**: your dsh preset files are
  `/data/dsh/.agent-presets/ga/` (the grid-dsh volume, shared mount
  inheritance from the original in-grid deployment). Image redeploys refresh
  them per-file, preserving operator-edited files.
- **Push guardrail**: a `git push` to main is a production auto-deploy of the
  grid-autonomy container. NEVER push to main without explicit human
  confirmation in the web UI.

## System map

Repo root: `/srv/tvcli` (the GA workbench). System dir:
`agents/grid-autonomy/`.

| Piece | Path | Role |
|-------|------|------|
| Daemon | `agents/grid-autonomy/daemon.py` | Scheduler + orchestrator: screen (10m) → deliberate → guard (8 fail-closed gates) → deploy → watch (60s) → rotate → reflect; heartbeat every 15m (8 fail-soft loop-health checks → score in `state.heartbeat` + journal; stale screen/optimizer feeds self-nudge). |
| Slot optimizer | `agents/grid-autonomy/optimizer.py` | Fast 2–5 min loop: idle-slot detection, challenger hunt (tvcli 15m), Mistral-pinned arbiter, swaps via `execute_rotation`. |
| Position optimizer | `agents/grid-autonomy/position_optimizer.py` | Slow 15m + post-entry lane: per-bot revaluation, TP/SL/trailing scoring — **advisory only** (`apply: false`), recs journaled + persisted to the PocketBase `recommendations` collection; never auto-edits WunderTrading. |
| Console | `agents/grid-autonomy/console/` | Mission console web UI + JSON API at **http://grid-autonomy:8798** (fleet cards, decision ledger, run cards, reliability, whitelisted config editor, dev control). |
| Control plane | `agents/grid-autonomy/ctl_http.py` | HTTP ctl at **http://grid-autonomy:8799**: GET `/health` `/status` `/reliability` `/observe` `/optimizer`; POST `/rescreen` `/optimize` `/reliability` `/rotate {"slot": n}` `/kill`. |
| PocketBase | grid-autonomy container `:8090` | Write-through side channel; `recommendations` collection queryable from the console via `GET /api/recommendations` (:8798). The file layer (`state/`) is the system of record. |
| Decisions | `state/decisions.jsonl` | One JSON line per decision; `record_outcome` attaches `"outcome"` on close. Ids `dYYYYMMDD-NNN`; payloads only as md5 `payload_digest`. |
| Journal / run cards | `state/state.json → journal` (last 200) + `state/reports/<ts>-<kind>.{json,md}` | Run cards: Route / Ground / Deliberate / Guard / Deploy / Observe / Reflect / Caveats. |

Everything the system writes stays inside `agents/grid-autonomy/` (`state/`,
`state/logs/`, `.pocketbase/`) in the grid-autonomy container's volumes;
from here it is visible only through the console/ctl APIs. `state/` is
runtime, **not source** — never commit it.

## Operating the running system (host-level equivalents)

The `./dev` script and daemon restarts are HOST-level operations — you
CANNOT restart the grid daemon from inside this container. Operate the system
instead via:

- **ctl API** `http://grid-autonomy:8799` — `/health` `/status` `/optimizer`
  `/observe` `/reliability` (GET), `/rescreen` `/optimize` `/rotate` `/kill`
  (POST); the console's confirm-gated lifecycle ops wrap the same surface.
- **console** `http://grid-autonomy:8798` — the human-facing mission UI;
  point the operator there for anything visual.
- **code updates** — edit + test in `/srv/tvcli`, then (with explicit human
  confirmation) `git push`: pushes to main auto-deploy the grid-autonomy
  container, which restarts the daemon with the new code (a graceful SIGTERM
  stop; volumes keep state). Track the deploy with
  `gh run watch` (repo mrme000m/tvcli, workflow grid-autonomy-deploy.yml).

Hard stop: `touch agents/grid-autonomy/KILL` inside the workbench has NO
effect on the running container — use `POST /kill` on the ctl API instead.

## Test suite (the verification bar)

```sh
cd /srv/tvcli/agents/grid-autonomy
python3 -m unittest discover -s tests -t .
```

758+ offline tests (network/browser mocked; `GRID_STATE_DIR` isolates state so
tests never pollute the live side channel). All must stay green — every
improvement work package reports this command's output; trust the runner
over any count in docs. `python3 -m pytest tests/ -q` also works.

## Gotcha 1 — two WunderTrading accounts

- **Local (Mac):** the daemon's WT session lives in the headful CloakBrowser
  profile on CDP **:9222** — the Mac's own account.
- **Deployment (az00 VPS):** the grid-autonomy container logs into the
  **vault account** (vault item `wundertrading`, folder `grid-autonomy`, via
  `wt-login.mjs`).
- Dev-side access to the vault account: `browser-debug/wt-exchanges-live.py`
  on CDP **:9223** (profile `profile-vault`) — **never point it at the
  Mac's :9222 browser.**
- Fleets, paper profiles, and bots are fully independent per account;
  `dev reset-wt` is account-scoped. Both fleets may run live-paper at once.

## Gotcha 2 — deploy pipeline

- Pushes to `main` that touch the grid build context (`docker/`,
  `agents/grid-autonomy/`, `.agents/skills/`, `browser-debug/wt-login.mjs` +
  deps, Go sources) auto-deploy the **grid-autonomy container** to az00,
  **preserving the running container's GRID_MODE** (a first deploy with no
  previous container falls back to live-paper). If the container was left in
  dry-run, re-promote deliberately — from the host, or ask the human:
  `gh workflow run grid-autonomy-deploy.yml --ref main -f mode=live-paper -f transport=ghcr`
  then `gh run watch` (build ~4 min + pull + boot ≈ 10–12 min).
- Pushes touching ONLY `docker/ga/**` / `.github/workflows/ga-deploy.yml`
  rebuild the **grid-ga container** (you) — never the trading stack.
- Both workflows are idempotent: named volumes keep state; redeploys replace
  the container with a graceful SIGTERM stop.
- Plan-only grid deploy: dispatch with `-f mode=dry-run`.

## Gotcha 3 — Binance geo-block fallback

Binance public market-data REST (`/api/v3/...`) is geo-gated; the screen and
spread fetchers use **`https://data-api.binance.vision`** — Binance's public
data mirror, not geo-gated (`screen/merge.py` ticker/24hr, `execution/spreads.py`
bookTicker). If a Binance data issue appears, check the mirror is still
reachable before suspecting the code.

## Periodic audit checklist

Run on each monitor pass (all via `http://grid-autonomy:8799` / `:8798`):

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
3. **Verify yourself**: run the unittest suite from
   `/srv/tvcli/agents/grid-autonomy` (758+ green); read the diff against
   the package's acceptance criteria.
4. **Report back**: what changed, test output, and — after the human
   confirms the push — `gh run watch` evidence that the grid-autonomy
   deploy picked it up (a push to main auto-deploys; the new container
   restarts the daemon with the new code and preserves all state volumes).

## Self-improvement loop

GA distills each session's findings into the repo so the same problem is
never rediscovered twice. The loop contract, the entry format, and the
write rules live in `docker/ga/learnings/README.md`; the ledger itself is
`docker/ga/learnings/ledger.md` (reverse-chronological, newest first).

Journal a learning with the repo's tool (stdlib-only, no network, no
secrets, no git operations):

```sh
python3 /srv/tvcli/docker/ga/ga_learn.py add \
  --title "Short imperative title" \
  --body "The verified knowledge: cause, effect, consequence." \
  [--changes "docker/ga/foo.py,docker/ga/bar.sh"]
python3 /srv/tvcli/docker/ga/ga_learn.py tail [--count 5]
```

- **When to write**: end of every substantive session (the (4) SELF-IMPROVE
  persona duty), after any incident or fix once the root cause is
  understood, and the second time a gotcha bites — bump the existing entry
  instead of writing a near-duplicate.
- **Format**: one `## YYYY-MM-DD — <title>` entry: a short body of verified
  knowledge, plus an optional `Changes:` list of the files the learning
  caused to change.
- **KNOWLEDGE, not logs**: never secrets, raw dumps, or transient progress;
  verified true, concise, written for a reader who has not seen the session.
- **Update the code path**: when a finding warrants it, update the GA
  code/preset/skill under `docker/ga/**` accordingly — then commit and
  (only with explicit human confirmation) push; pushes touching
  `docker/ga/**` ride the ga-deploy workflow and rebuild THIS grid-ga
  container, which is the delivery path for GA-stack changes.
- **Confirmation guardrail**: `ga_learn.py` only WRITES the ledger — no
  commit, no push. Pushing to main always requires explicit human
  confirmation in the web UI (push = production auto-deploy).

Hard rules: never push to main without explicit human confirmation in the
web UI (push = production auto-deploy); never edit WunderTrading account
state (bot apply paths stay advisory-only — `position_optimizer.apply:
false`) without explicit human confirmation; never go real-money
(`autonomy.live_profiles: []` stays empty; the real Hyperliquid profile
`c629f5ba3a643a82137e7864` is hard-denylisted); never print secrets — vault
items are read via bw-tools, not echoed.
