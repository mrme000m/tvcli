# grid-autonomy — operating manual

`agents/grid-autonomy/` is a **fully autonomous grid-trading portfolio
manager**. One daemon runs the whole loop — screen → deliberate → guard →
deploy → watch → rotate → reflect — and executes on **WunderTrading paper
profiles only** unless an operator explicitly lifts the live gate.

Accuracy note: every fact below is verified against the code at
`agents/grid-autonomy/` (daemon.py, config.yaml, the `execution/`, `agents/`,
`llm/`, `policy/`, `screen/`, `watch/` modules, and `scripts/`).

> **Default safety posture.** `daemon.py` defaults to `--dry-run` (plans and
> journals everything, creates nothing). `--live-paper` creates real paper
> bots on the allowlisted paper profiles. The daemon never selects a
> real-money profile: `autonomy.live_profiles` is empty and the one real
> Hyperliquid profile is hard-denylisted. Paper-only until you change that on
> purpose (see “Paper → live escalation”).

## Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │                 daemon.py                    │
                    │        (schedule + orchestrate + journal)     │
                    └───┬───────────────────────────────────┬──────┘
        every 60m       │ rescreen                           │ every 60s health poll
                        ▼                                    ▼
┌──────────────────────────────────┐        ┌──────────────────────────────────┐
│  SCREEN      screen/merge.py     │        │  WATCH       execution/observe.py │
│  Hyperliquid perps + Binance     │        │  real bot status/positions/       │
│  spot; regime, preset score,     │        │  history → stagnation policy      │
│  tvcli /hunt confluence, 4h      │        │  (policy/stagnation.py) →         │
│  confirm, Binance spreads        │        │  in-place re-centre or re-analyse │
└───────────────┬──────────────────┘        └───────────────▲──────────────────┘
                │ candidates                                │
                ▼                                            │
┌──────────────────────────────────┐        ┌───────────────┴──────────────────┐
│  DELIBERATE   agents/swarm.py    │        │  ROTATE       daemon.execute_     │
│  bull/bear debate → facilitator  │        │               rotation (stop→      │
│  → 3-stance risk team; llm/      │        │               verify→delete→       │
│  provider.py CF→nvidia→open-     │        │               cooldown→deploy)    │
│  router; rule fallback           │        │                                   │
└───────────────┬──────────────────┘        └───────────────▲──────────────────┘
                │ ticket                                    │
                ▼                                            │
┌──────────────────────────────────┐        ┌───────────────┴──────────────────┐
│  GUARD      execution/guardrails │        │  DEPLOY      execution/           │
│  .py — 8 fail-closed gates       │        │  grid_adapter.py + resolve.py;   │
│  (KILL, pairCode, profile,       │        │  reliability_grid.py escalation  │
│  sizing, spread, venue/side,     │───────▶│  ladder; ATR channel, geometric  │
│  reliability, rotation)          │        │  lines, USD per-trade sizing      │
└──────────────────────────────────┘        └───────────────────────────────────┘
        │                                   ▲
        └──── agents/reflect.py ────────────┘  decisions.jsonl + run cards
              (decision journal, k=3 memory recall, state/reports/)
```

## The loop

1. **Screen (60m).** `screen/merge.py` screens Hyperliquid perps and Binance
   spot (venue fetches are serial, not threaded): public OHLCV → regime classification (`market_regime`),
   preset scoring (`universe_screen`, presets `grid-neutral` +
   `grid-directional`), real Binance book-ticker spreads
   (`execution/spreads.py`), optional tvcli `/hunt` confluence (squeeze +
   choppiness + mtf-confluence), and 4h directional re-confirmation for
   trend candidates. Output: ranked candidates with `score_final`.
2. **Deliberate.** `agents/swarm.py` runs a TradingAgents-style pipeline per
   candidate — bull open → bear open → bull/bear rebuttals → facilitator →
   seeking/neutral/conservative risk team — via `llm/provider.py`
   (Cloudflare Workers AI primary, Nvidia/OpenRouter fallback). Strict-JSON
   parsing retries and falls back to the rule map on total LLM outage
   (`llm_degraded: true`). `agents/reflect.py` injects up to `k=3` past
   outcome memories into the briefs.
3. **Guard (fail-closed).** `execution/guardrails.py` runs 8 gates. Any
   violation vetoes the deployment. No WunderTrading mutation happens before
   all gates pass.
4. **Deploy.** A plan-capacity pre-check (`observe.grid_capacity()` →
    `Daemon.venue_capacity_block()`) skips venues whose exchange tier is at
    its active-grid-bot cap (free plan: HYPERLIQUID_SWAP is premium/200 via
    WT's 0.035% builder-fee arrangement, everything else allows one active
    bot) with a `capacity-veto` journal entry. `execution/grid_adapter.py`
    then turns the ticket into the verified
   `grid_bots/upsert` payload: ATR-band channel, ATR-derived
   profit-per-grid, geometric grid lines, USD-denominated per-trade
   sizing (WT `amountPerTrade` is in the market's USD-stable base
   currency), and a per-pair minimum-viable floor from `:2087` market
   metadata — the effective floor is `max(limits.cost.min, $10)`, i.e. the
   5–50 USDT per-pair minimums on Binance never drop below the hardcoded
   `MIN_USD_PER_GRID = $10` fallback (`daemon.py`).
   `execution/resolve.py` resolves `pairCode` from the live market map.
5. **Watch (60s).** `daemon.health_cycle` polls real bot status/positions/
   history (`execution/observe.py`), evaluates the per-token stagnation
   policy (`policy/stagnation.py`), re-centres the channel in place (grid
   edit, 6h rate limit) when the regime is intact, or flags
   out-of-channel/stopped bots (`needs_reanalysis`) for rotation on the
   next rescreen.
6. **Rotate.** A stagnant incumbent — or one flagged
   out-of-channel/stopped — with an eligible challenger (stagnant
   incumbents additionally need Δscore ≥ 5 hysteresis; the min-hold floor
   and per-token cooldowns always apply) is stopped
   (`stop_and_close_all`), verified (`stopped`,
   `stopped_and_close_all`, `stopped_with_unrealized`, `closed` all count —
   the transient post-stop state while WT closes the legs), deleted,
   cooldown-set per token, and replaced. Before the delete, the incumbent's
   closed round-trips are exported and archived
   (`state/reliability_archive.json`) so they keep feeding the reliability
   ledger, and the true realized PnL sum is recorded on the decision
   outcome. `POST /rotate {"slot": n}` forces a manual rotation (bypasses
   hysteresis and the min-hold floor).
7. **Reflect.** Every decision lands in `state/decisions.jsonl`; every cycle
   writes a run card `state/reports/<UTC-ts>-<kind>.{json,md}` with
   Route/Ground/Deliberate/Guard/Deploy/Observe/Reflect/Caveats sections.
   Adopted bots get a decision record at adoption (`kind: adopted`, regime
   archetype classified) so a later rotation attaches an outcome — the
   memory/reflection loop learns from adopted bots too.
8. **Reliability (24h).** `daemon.reliability_cycle` exports each active
   bot's closed round-trips (`execution/reliability_grid.py`), aggregates
   them per archetype into `state/reliability.json` (zero-sample
   archetypes never erase existing entries), and the reloaded ledger gates
   the sizing ladder and archetype kill-flags.

## Safety rails (what protects you)

- **KILL file.** Presence of `agents/grid-autonomy/KILL` halts the daemon at
  the next loop tick and refuses startup. `stop.sh` and `POST /kill` write it.
- **Paper-only by default.** `autonomy.live_profiles: []`;
  `daemon.select_profile` is called only with `paper=True`. The real
  Hyperliquid profile `c629f5ba3a643a82137e7864` is in
  `daemon.PROFILE_DENYLIST`.
- **Venue-strict profiles.** A `binance` slot must resolve to a profile on
  `BINANCE` or `BINANCE_FUTURES`; a `hyperliquid` slot to `HYPERLIQUID_SWAP`.
  No cross-venue fallback — a mismatch is a veto, never a silent misroute.
- **8 fail-closed gates** (KILL, pairCode resolved, profile active +
  allowlisted + venue-strict, worst-case commitment ≤ slot cap and portfolio
  ≤ 85% ceiling, step ≥ 2× spread, venue/side rules, reliability gates,
  rotation cooldown + hysteresis).
- **Sizing ladder.** Base 25% (<10 closed samples) → probe 40% (≥10) →
  full 50% (≥30 and PF ≥ 1.3); `recent_pf < 1.0` kills the archetype. Minimum
  viable minimum notional per grid (per-pair `limits.cost.min`).
- **Reliability gate for live.** `guardrails.check_reliability` refuses
  non-paper deployments below 30 samples / PF 1.3 / recent PF 1.0.

## File map

| Path | Role |
|------|------|
| `daemon.py` | Scheduler + orchestrator + state/journal (the Daemon class). |
| `config_lite.py` | Stdlib-only YAML-subset parser (`load_yaml`, `deep_merge`). |
| `ctl_http.py` | HTTP control plane on :8799 (endpoints below). |
| `console/` | **Mission console** — web UI + additive API on :8798 for observing the fleet (channel-ladder slot cards, decision ledger, run cards, reliability, logs), editing config.yaml (whitelisted, comment-preserving), and dev control (rescreen/rotate/KILL/start/stop/restart). See `console/README.md`. |
| `config.yaml` | Contract: portfolio, venues, providers, policy defaults. |
| `screen/merge.py` | Parallel HL+Binance screen, confluence, 4h confirm. |
| `agents/swarm.py` | TradingAgents deliberation swarm (rule fallback). |
| `agents/reflect.py` | `decisions.jsonl`, memory recall, run cards. |
| `llm/provider.py` | CF → Nvidia → OpenRouter chain, strict-JSON retries. |
| `execution/guardrails.py` | 8 pure fail-closed gates (unit-testable). |
| `execution/grid_adapter.py` | Ticket → `grid_bots/upsert` payloads + grid create/stop/delete/edit. |
| `execution/resolve.py` | venue+symbol → `pairCode` via cached all-markets map. |
| `execution/observe.py` | Read-only bot status/positions/history observation + plan capacity (`grid_capacity()`, `account_limits()`). |
| `execution/reliability_grid.py` | Per-archetype PF/samples ledger. |
| `execution/profiles.py` | WunderTrading paper-profile creation helper. |
| `execution/spreads.py` | Public Binance spot spread fetch (no auth). |
| `policy/stagnation.py` | Per-token stagnation policy + slot allocator. |
| `watch/spec.py` | Per-bot tvcli watch spec generator. |
| `scripts/start.sh` / `stop.sh` | Start (CF env from `dsh web`) / stop (POST /kill). |
| `scripts/run_launchd.py` | launchd entrypoint (foreground, CF env import, PID file). |
| `scripts/install_launchd.sh` + `launchd/*.plist` | Install the supervision agents (grid-autonomy + tvcli serve). |
| `scripts/smoke.sh` | One-shot dry-run E2E smoke (see Operations runbook). |
| `tests/` | Offline unit tests — 197 as of 2026-09-05 (`python3 -m unittest` / `pytest`). The count changes; trust the runner output over this table. |
| `state/` | Runtime state, journal, reports, market-map caches. **Not source.** |
| `docs/binance-paper-profile.md` | Binance paper-stand-in investigation + runbook. |

## Configuration reference

`config.yaml` is merged over the daemon's built-in defaults. Every key, and
whether the daemon actually reads it:

| Key | Value / effect | Wired? |
|-----|----------------|--------|
| `portfolio.total_usd` | Fund size; feeds the slot plan. | yes |
| `portfolio.venues.hyperliquid.balance_usd` | HL sleeve (`300.0`). | yes |
| `portfolio.venues.binance.balance_usd` | Binance sleeve (`200.0`). | yes |
| `portfolio.venues.*.market` | `perps` / `spot` label. | doc only |
| `portfolio.venues.*.grids` | Allowed grid types per venue. | doc only |
| `portfolio.slots_min` / `slots_max` | 3 / 5. | doc only (bounds) |
| `portfolio.slots_default` | Number of slots (`4`). | yes |
| `portfolio.max_alloc_per_slot` | Per-slot worst-case commitment cap (`0.5`). | yes |
| `portfolio.cash_buffer_pct` | Deployable ceiling = total × (1 − buffer) = 85%. | yes |
| `screen.presets` | `grid-neutral,grid-directional`. | doc only (merge.py default) |
| `screen.interval` | Screen candle interval `1h`. | doc only (merge.py default) |
| `screen.confirm_interval` | 4h directional confirmation. | yes (merge.py reads it) |
| `screen.limit` | Candle limit `300`. | doc only (merge.py default) |
| `screen.min_volume_usd` | 24h quote-volume floor `5000000`. | doc only (merge.py default) |
| `screen.top_per_preset_venue` | Shortlist size per preset/venue. | doc only |
| `screen.confluence_top` | Candidates sent to tvcli `/hunt` (`10`). | doc only (daemon default) |
| `screen.confluence_skills` | `squeeze,choppiness,mtf-confluence`. | doc only (merge.py constant) |
| `screen.rescreen_minutes` | Rescreen cadence (`60`). | yes |
| `grid_defaults.band_atr` | ATR-band width `3.0`. | doc only (`grid_args` default) |
| `grid_defaults.step_factor` | ATR→step multiplier `0.5`. | doc only (`grid_args` default) |
| `grid_defaults.step_min` / `step_max` | Profit-per-grid clamp `0.1`–`2.0`. | doc only (`grid_args` default) |
| `grid_defaults.min_grids` / `max_grids` | Grid-count bounds `5`–`30`, threaded into `grid_adapter.grid_args` by the daemon's density sizing. | yes (`min_grids`) / doc only (`max_grids`) |
| `grid_defaults.dca_factor` | DCA order factor `1.5`. | doc only (`grid_args` default) |
| `grid_defaults.stop_trigger` | `"stop_and_close_all"`. | doc only (hardcoded in `compute_upsert`) |
| `grid_defaults.pump_protection` | `true`. | doc only (hardcoded `pumpProtection: true`) |
| `llm.chain` | `[cf, nvidia, openrouter]`. | env only (`GRID_LLM_CHAIN`) |
| `llm.cf_model` | `@cf/zai-org/glm-5.3`. | env only (`CF_MODEL`) |
| `llm.nvidia_model` | `meta/llama-3.3-70b-instruct`. | env only (`NVIDIA_MODEL`) |
| `llm.openrouter_model` | `arcee-ai/trinity-large-preview:free`. | env only (`OPENROUTER_MODEL`) |
| `llm.max_calls_per_decision` | 8-call budget. | doc only (swarm hardcodes) |
| `policy.cooldown_min_h` / `cooldown_max_h` | 12–72h clamp. | doc only (stagnation.py constants) |
| `policy.fill_ratio_stagnant` | `0.3` fill threshold. | doc only (stagnation.py constant) |
| `policy.realized_ratio_stagnant` | `0.4` realized threshold. | doc only (stagnation.py constant) |
| `policy.score_drop_rotate` | `12.0` score-drop trigger. | doc only (stagnation.py constant) |
| `policy.hysteresis_score` | Challenger must beat incumbent by `5.0`. | yes |
| `policy.min_hold_h` | `24`. | yes (rotation churn guard) |
| `reliability.paper_profile` | `demo-hype`. | doc only |
| `reliability.min_samples` / `profit_factor_pass` / `profit_factor_kill` | 30 / 1.3 / 1.0. | doc only (hardcoded) |
| `watch.interval_s` | Health-poll cadence (`60`). | yes |
| `watch.adjust_steps_threshold` | In-place re-centre when price drifts > `2.0` grid steps from mid. | yes |
| `watch.browser_cdp` | CloakBrowser CDP probe URL (`http://127.0.0.1:9222`) for the browser watchdog. | yes |
| `watch.browser_restart_cooldown_s` | Watchdog relaunch cooldown (`600`). | yes |
| `watch.browser_launch_cmd` | Headful browser relaunch command (launch from the minimal-mjs profile). | yes |
| `watch.wt_restore_cmd` | WT session/cookie re-assert command after relaunch. | yes |
| `autonomy.mode` | `auto`. | doc only (daemon always loops) |
| `autonomy.base_pct` | Base allocation `0.25`. | yes |
| `autonomy.probe_pct` | Probe allocation `0.40`. | yes |
| `autonomy.full_pct` | Full allocation `0.50`. | yes |
| `autonomy.live_profiles` | Real-money profile names; **empty = real money refused**. | yes |
| `autonomy.paper_profiles.hyperliquid` | `[demo-hype]`. | yes |
| `autonomy.paper_profiles.binance` | `[demo-bn]` (futures paper stand-in). | yes |
| `memory.k` | Top-k memories injected per candidate (`3`). | yes |
| `adopt_existing` | Adopt matching active paper bots on first run. | yes |
| `guardrails.checks` | The 7 config-visible gate names (the KILL gate is code-only). | doc only (guardrails.py tuple) |
| `guardrails.kill_file` | `agents/grid-autonomy/KILL`. | doc only (daemon hardcodes) |
| `server.tvcli` | `http://127.0.0.1:8765`. | doc only (env `TVCLI_SERVER`) |
| `server.daemon_port` | Control-plane port `8799`. | yes |

## Operations runbook

All commands run from `agents/grid-autonomy/` (the scripts `cd` there
themselves).

**Single writer.** The daemon refuses to start while another live process
holds `state/daemon.pid` (launchd/start.sh write that file with the daemon's
own pid, so the normal paths always pass). A second instance — e.g.
`daemon.py --once` for smoke while the supervised daemon runs — would
clobber `state.json` (observed live 2026-09-05). Set `GRID_NO_PIDGUARD=1`
to override deliberately. A ctl-port bind failure is journaled as
`ctl-error` instead of killing the control plane silently. Note the launchd
daemon's stdout/stderr goes to `~/Library/Logs/grid-autonomy-launchd.log`,
not `state/daemon.log` (that one only collects start.sh launches).

**Start (dry-run planning):**

```sh
scripts/start.sh
# starts `nohup python3 daemon.py` with dry-run ON
```

`start.sh` exports `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_KEY` from the
running `dsh web` process environment (`/proc/<pid>/environ` on Linux,
`ps -Eww` on macOS) and refuses to
start if it cannot read them. It writes `state/daemon.pid` and appends to
`state/daemon.log`.

**Start (paper deploys — actually create paper bots):**

```sh
scripts/start.sh --live-paper
```

**Smoke / one-shot:**

```sh
scripts/smoke.sh                                          # dry-run E2E (wraps the line below)
python3 daemon.py --once --no-confluence --top 5          # dry-run
python3 daemon.py --once --live-paper --top 5             # paper
```

`smoke.sh` is the repeatable dry-run E2E check: one full cycle (screen →
deliberate → guard → deploy-plan) against live public market data with zero
WunderTrading mutations. Use it after any config or code change before
starting the daemon.

CLI flags: `--once`, `--dry-run` (default on), `--live-paper`,
`--no-confluence`, `--top N`, `--port N`.

**Stop:**

```sh
scripts/stop.sh
```

`stop.sh` POSTs `/kill` (writes the KILL file), SIGTERMs the PID, waits up to
15s, then SIGKILLs, and removes the PID file. Port override:
`GRID_DAEMON_PORT`.

**Supervision (survive crashes + reboots):**

```sh
scripts/install_launchd.sh   # installs + loads two per-user LaunchAgents
```

- `com.tvcli.grid-autonomy` — runs `scripts/run_launchd.py` **in the
  foreground** with `--live-paper` (imports the CF keys from the `dsh web`
  process env, writes the PID file, then runs `daemon.py` in-process).
- `com.tvcli.serve` — runs `tvcli serve` in the foreground (`:8765`,
  the confluence backend).
- Restart policy: `KeepAlive={SuccessfulExit: false}` — a crash (non-zero
  exit) restarts after `ThrottleInterval` (30s); `stop.sh`/SIGTERM exits 0
  and stays stopped until the next boot/load; a leftover KILL file blocks
  startup on purpose. The daemon handles SIGTERM gracefully (state save,
  exit 0). Verified live: `kill -9 <pid>` → launchd restarts within ~30s.
- macOS TCC note: the repo lives on a removable volume, which launchd-spawned
  bash cannot read — the agent must run under Homebrew python3
  (`/opt/homebrew/bin/python3`, holds the Removable Volumes grant, same as
  `com.tvcli.watchtower`); that interpreter has `httpx`+`websockets` for the
  `wt_browser.py` subprocesses.
- Manual control:
  `launchctl kickstart -k gui/$(id -u)/com.tvcli.grid-autonomy` (restart),
  `launchctl bootout gui/$(id -u)/com.tvcli.grid-autonomy` (unload).

**Status and control plane (port 8799):**

| Method | Path | Effect |
|--------|------|--------|
| GET | `/health` | `{status, at, kill}` — liveness + KILL presence. |
| GET | `/status` | slots, active bots, committed, `live_allow`, profiles, plan `capacity` + `account_limits`, `last_cycle`, last 10 journal entries. |
| GET | `/reliability` | Current reliability ledger. |
| GET | `/observe` | Latest `observe_all()` snapshot. |
| POST | `/rescreen` | Queue an immediate rescreen cycle. |
| POST | `/reliability` | Queue an immediate reliability-ledger refresh (else the 24h cron). |
| POST | `/rotate` | Force-rotate a slot: body `{"slot": n}`. |
| POST | `/kill` | Write the KILL file (daemon halts on next tick). |

**KILL file (hard stop):**

```sh
touch agents/grid-autonomy/KILL   # daemon halts within one loop tick
rm  -f agents/grid-autonomy/KILL  # clear before restarting
```

The daemon checks the KILL file every loop tick, on startup, and refuses to
start while it is present.

**Mission console (web UI, port 8798):**

```sh
python3 console/server.py         # open http://127.0.0.1:8798
```

A full observation / configuration / dev-control dashboard over the same
state the ctl plane serves: channel-ladder slot cards with live price
cursors, the decision ledger with outcomes, run cards, the reliability
ledger with sizing tiers, a whitelisted config.yaml editor
(comment-preserving, backup kept), log tail, and the same ctl actions
(rescreen / rotate / KILL / start / stop / restart, all confirm-gated).
Purely additive — the daemon is untouched. Full reference:
`console/README.md`.

## Paper → live escalation

The daemon is **paper-only in code today** — `select_profile(..., paper=True)`
is the only call site. Escalation is deliberate and gated:

1. **Paper forever until proven.** Paper deployments bypass the reliability
   gate by design (they exist to gather samples). Within paper, allocation
   still escalates: **base 25%** (<10 closed samples) → **probe 40%** (≥10) →
   **full 50%** (≥30 and PF ≥ 1.3).
2. **Reliability kill.** `recent_pf < 1.0` (last 20 closed round-trips)
   refuses new deployments of that archetype and flags existing bots.
3. **To go real-money** you must, explicitly and together:
   - add the profile name to `autonomy.live_profiles`;
   - ensure the profile is **not** in `daemon.PROFILE_DENYLIST` (enforced in
     code) and is not a `paperTrading` profile (operator-verified: the
     paper=False selection path does not itself reject paper profiles);
   - pass `guardrails.check_reliability` (≥30 samples, PF ≥ 1.3, recent PF
     ≥ 1.0);
   - set `live_allow: true` in `state/state.json` (reported by `/status`);
   - invoke the `paper=False` profile-selection path (the daemon currently
     never does this on its own).
4. **Hard denylist.** The real Hyperliquid profile
   `c629f5ba3a643a82137e7864` is refused even if accidentally allowlisted.

There is no automatic paper→live transition. If you are not trying to go
live, leave `live_profiles: []` and `live_allow: false` alone.

## Binance futures-paper stand-in

WunderTrading has **no Binance spot paper mode**. Binance paper is
futures-only (`BINANCE_FUTURES`, USDT-M). The Binance sleeve therefore runs
on profile **`demo-bn`** (`BINANCE_FUTURES` paper) as a stand-in while the
code keeps the sleeve's **spot-like no-Short rule**:

- `execution/guardrails.check_venue_side` vetoes `binance + short`.
- `daemon.select_profile` accepts `BINANCE` (spot) or `BINANCE_FUTURES`
  (stand-in) for the `binance` venue, and prefers native spot when both exist.
- `daemon.market_for_profile` maps `BINANCE_FUTURES` → `derivative`, so
  `resolve_pair` resolves `pairCode` from the **derivative** map
  (`state/market_map-derivative.json`), matching the profile's exchange.
- The grid payload's `exchangeCode` is the profile's actual exchange
  (`BINANCE_FUTURES`), not the static `BINANCE` default.

Going spot-live requires connecting a real Binance spot account and listing
its profile name in `autonomy.live_profiles`. Full evidence and the manual
UI/API flow: `docs/binance-paper-profile.md`.

## LLM provider setup

Providers are configured by **environment variables** (the `llm:` block in
`config.yaml` is documentation, not read by the code).

| Provider | Env vars | Default model |
|----------|----------|---------------|
| Cloudflare Workers AI (primary) | `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_KEY` (or `CLOUDFLARE_AI_TOKEN`); `CF_MODEL` | `@cf/zai-org/glm-5.3` |
| Nvidia NIM (fallback) | `NVIDIA_API_KEY`; `NVIDIA_MODEL` | `meta/llama-3.3-70b-instruct` |
| OpenRouter (fallback) | `OPENROUTER_API_KEY`; `OPENROUTER_MODEL` | `arcee-ai/trinity-large-preview:free` |

`GRID_LLM_CHAIN` overrides the fallback order (comma-separated, e.g.
`cf,nvidia,openrouter`). `start.sh` imports the CF keys from the `dsh web`
process env; Nvidia/OpenRouter keys must be present in the daemon's own
environment to be used.

Ping the chain:

```sh
python3 llm/provider.py --ping --json
```

## Runtime dependencies & self-healing

The daemon has three **hard runtime dependencies**. Each has a self-healing
path so a transient failure does not silently degrade autonomy:

| Dependency | Used by | Self-heal | Failure symptom |
|------------|---------|-----------|-----------------|
| **CloakBrowser on CDP `:9222`** (headful, profile at `minimal-mjs/profile`) + a logged-in **wundertrading.com page** | every WT session-API call: observe, deploy, stop/delete, reliability, capacity, profiles | `watch.browser_*` watchdog in `health_cycle` probes CDP every 60 s and relaunches `browser_launch_cmd` + `wt_restore_cmd` (≤1 try / 10 min, journaled `browser-restart`) | `observe error: grid status list unavailable (browser/session down)`, `deploy-failed`, guard-profile vetoes |
| **CF Workers AI keys** in env (`CLOUDFLARE_ACCOUNT_ID` + token) | `llm/provider.py` chain → swarm deliberation | `run_launchd.py` imports them from the `dsh web` process env at boot; the daemon re-runs `self_heal_env()` every rescreen (journaled `env-heal`) — so a `dsh web` started *after* the daemon still gets picked up | `llm_degraded: true` decisions (rule fallback, not fatal) |
| **PocketBase env** (`.pocketbase/pb.env`) | write-through side channel (`pbclient.py`) | sourced by `start.sh` and by `run_launchd.py` at boot; re-healed by `self_heal_env()` at runtime | PB collections go stale (file layer keeps working — it is the system of record) |

Prolonged total blindness now escalates: when **every** bot errors on every
observe sweep for ~30 min, the journal gets one loud `observe-outage` entry
per 30-min window (in addition to the per-bot `health-warn` lines).

**Known operator task — WT session expiry:** the WunderTrading `PHPSESSID`
cookie (in the browser profile) expires roughly weekly (current one:
2026-09-06 ~19:34 UTC). When it lapses, `wt_restore_cmd` re-asserts cookies
only if `browser-debug/secrets/runtime/wt-session.env` exists (vault item
`wundertrading-session`, materialized by `bw-provision.sh`); otherwise a
**manual re-login in the CloakBrowser window** is required. Watch for
`browser-restart … relaunch ok` followed by persisting
`grid status list unavailable` errors — that means login, not browser, is
the problem.

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `start.sh` exits “could not read CLOUDFLARE_…” | The `dsh web` process is not running or holds no CF keys. Start `dsh web` first, then re-run `start.sh`. |
| “daemon already running (PID …)” | A PID file exists and is alive. `scripts/stop.sh` first. |
| “KILL file present …” | `agents/grid-autonomy/KILL` exists. `rm -f agents/grid-autonomy/KILL`, then start. |
| All LLM calls fail | The daemon **does not block**: swarm falls back to the rule map and tags decisions `llm_degraded: true`. Check keys with `python3 llm/provider.py --ping`. |
| `observe error: grid status list unavailable (browser/session down)` on every bot | CloakBrowser/CDP is down or the WT login lapsed. The watchdog relaunches the browser every 10 min (journal `browser-restart`); if relaunch is `ok` but errors persist, the session cookie expired — re-login in the browser window (see “Runtime dependencies & self-healing”). |
| Decisions tagged `llm_degraded: true` under launchd | Env import failed at boot (`dsh web` was down/restarting). The daemon self-heals each rescreen (`env-heal` journal) — or restart the agent: `launchctl kickstart -k gui/$(id -u)/com.tvcli.grid-autonomy`. |
| Browser down / Cloudflare 403 | `resolve.py` and `observe.py` go through `wt_browser.py`; on failure `resolve` falls back to the cached market map and `observe` returns error/empty fields. Restart the browser session and re-run. |
| `pairCode` unresolved | Market map missing or stale. The daemon fetches `https://wundertrading.com:2087/all-markets?market=…&marketExpiryGroup=infinite` via the browser and caches `state/market_map-{spot,derivative}.json` for 24h (stale cache is still used when the browser is down). |
| `Maximum number of Grid Bots reached` on create | Plan capacity, **not** the account-limits dashboard number. The enforced caps live in the `grid_bots/upsert` init data: `maxActiveGridBots = {other: 1, premium: 200}` — HYPERLIQUID_SWAP is a *premium* exchange (200 active bots); every other exchange (incl. `BINANCE_FUTURES`) is the *other* tier with **one active grid bot** on the free plan. The daemon observes both views every rescreen cycle (journal kind `subscription` on any change) and pre-checks this (`capacity-veto`, `GET /status → capacity` / `account_limits`), skipping the create instead of retrying into the 400. Hyperliquid's Premium caps come from WunderTrading's 0.035% builder-fee arrangement. Rotations are unaffected (stop→delete frees the slot before the challenger create). `GET /en/trader/dashboard/account-limits` reports `gridBots: n/200` but is not what `upsert` enforces. |
| Slot 4 (binance) never deploys | Known limitation, not a bug: the default slot plan assigns **two** binance slots but the free plan's `other` tier allows only **one** active grid bot — slot 4 is permanently capacity-vetoed while slot 3 runs (one `capacity-veto` journal line per rescreen). The plan is config-driven, not derived from observed capacity; to use that capital, raise the plan tier, or re-balance venues in `portfolio.venues`/`slots_default`. |
| Bot stillborn / tiny grid lines | The per-pair min-notional floor (`limits.cost.min` from `:2087`) bumps the allocation within caps, then widens the grid step to fit fewer lines; if neither fits it vetoes. Check `state/state.json → journal` for `size-floor` / `size-fit` / `guard-veto` entries. |
| Rotation won’t happen | Requires a stagnant incumbent **and** Δscore ≥ 5 **and** expired per-token cooldown. Check `/status` journal for `rotation-veto` / `rotation-skip`. |
| Empty `.md` run card | Older daemon shape bug (fixed); the companion `.json` was always correct. If you see it, upgrade daemon.py. |

## Tests

```sh
cd agents/grid-autonomy
python3 -m unittest discover -s tests -t .
```

197 tests as of 2026-09-05, all offline (network/browser calls are mocked
or stubbed; state is isolated in temp dirs, and a `GRID_STATE_DIR` override
also disables the PocketBase write-through so tests can never pollute the
live side channel). Expected output:

```
Ran 197 tests in …
OK
```

(`python3 -m pytest tests/ -q` works too.) The count changes with the code —
trust the runner output over any number printed in docs.

A cosmetic `ResourceWarning` about unclosed fixture file handles may appear;
it is not a failure.

## State artifacts (runtime, not source)

| Path | Meaning |
|------|---------|
| `state/state.json` | Live daemon state: slots, `active_bots`, cooldowns, reliability, `live_allow`, committed, journal (last 200). |
| `state/daemon.pid` / `state/daemon.log` | Process id + stdout/stderr log. |
| `state/decisions.jsonl` | One JSON line per decision; outcomes attached on close. |
| `state/reports/<UTC-ts>-<kind>.{json,md}` | Per-cycle run cards. |
| `state/reliability.json` | Per-archetype reliability ledger — auto-refreshed from closed round-trips by the 24h reliability cron (see `GET /reliability`). |
| `state/reliability_archive.json` | Closed round-trips of already-deleted (rotated-out) bots, per archetype — merged into every reliability recompute so history is never lost. |
| `state/market_map-{spot,derivative}.json` | 24h all-markets caches. |
| `watch/specs/<symbol>-s<slot>.json` | Per-bot tvcli watch specs. |

Do not treat any `state/` file as source or commit it.
