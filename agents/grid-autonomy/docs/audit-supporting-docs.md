# Audit — grid-autonomy supporting docs vs code

Date: 2026-09-05. Method: every factual claim in the docs below was checked
against the current source (`agents/grid-autonomy/**`, `.agents/skills/grid-autonomy/SKILL.md`,
`bootstrapping/docs/grid-fleet.md`, root `AGENTS.md`) by reading/searching
with python3. No files were edited. The full offline test suite was executed to
verify test-count claims (`python3 -m unittest discover -s tests -t .` → **188
tests, OK**).

Scope (everything grid-autonomy-related **except** the main
`agents/grid-autonomy/README.md`):

1. `agents/grid-autonomy/console/README.md`
2. `agents/grid-autonomy/docs/binance-paper-profile.md`
3. `.agents/skills/grid-autonomy/SKILL.md`
4. `bootstrapping/docs/grid-fleet.md` (grid-autonomy claims only)
5. root `AGENTS.md` grid-autonomy paragraph
6. `agents/grid-autonomy/.gitignore`, `scripts/*.sh`, `launchd/*.plist`

## Verdict

| Doc | Status |
|-----|--------|
| `console/README.md` | ✅ confirmed, no discrepancies found |
| `docs/binance-paper-profile.md` | ✅ confirmed vs code (2 live-session facts not code-verifiable) |
| `.agents/skills/grid-autonomy/SKILL.md` | ⚠️ 1 stale fact (test count) + 2 minor imprecisions |
| `bootstrapping/docs/grid-fleet.md` | ⚠️ 1 minor omission (ctl endpoint list) |
| `AGENTS.md` grid-autonomy paragraph | ✅ confirmed |
| `.gitignore` + `scripts/*` + `launchd/*` | ✅ confirmed |

---

## 1. console/README.md — CONFIRMED

Verified against `console/server.py`, `console/yaml_edit.py`,
`console/static/*`:

- Ports: default `8798`, `CONSOLE_PORT` override, binds `127.0.0.1` only
  (`server.py:68`, `server.py:668` area: `ThreadingHTTPServer(("127.0.0.1", CONSOLE_PORT), ...)`).
- Endpoints table: all 14 GET + 9 POST routes match `do_GET`/`do_POST`
  (`server.py:677-757`, `server.py:738-801` row numbers of the handler) —
  `/api/overview`, `/api/daemon`, `/api/state`, `/api/journal?limit=`,
  `/api/decisions?limit=`, `/api/reliability`, `/api/screen`, `/api/reports`,
  `/api/reports/<stem>`, `/api/logs?lines=&grep=`, `/api/config`,
  `/api/observe` (proxy), `/api/meta` (ports/paths, `server.py:725-731` area),
  `POST /api/ctl/{rescreen,reliability,rotate,kill,unkill}`, `POST /api/config`,
  `POST /api/daemon/{stop,start,restart}`. No missing or extra endpoints.
- State files read: `state/{state.json, decisions.jsonl, reliability.json,
  reports/, daemon.log}` — exactly what the readers use (`_load_state`,
  `decisions_payload`, `reliability_payload`, `reports_index`, `logs_payload`).
- Ladder thresholds "base <10 samples → probe ≥10 → full ≥30 & PF ≥1.3;
  recent PF <1.0 kills" match `LADDER = {"probe_samples": 10,
  "full_samples": 30, "pf_pass": 1.3, "pf_kill": 1.0}` (`server.py:80`) and
  `_tier()` — same semantics as `daemon.size_multiplier` (`daemon.py:704-721`).
- Config whitelist: groups portfolio/cadence/policy/sizing ladder/memory match
  the `EDITABLE` dict (`server.py:89-125`); `autonomy.live_profiles` /
  `paper_profiles` deliberately absent; writes are comment-preserving
  (`yaml_edit.py`), round-trip verified, leave `config.yaml.bak`
  (`apply_config_edits`); restart required note included.
- Safety model: same-origin POST refusal (`_same_origin`), `{"confirm": true}`
  required for kill/stop/restart, KILL file never cleared implicitly —
  `daemon_start`/`daemon_restart` return `409 kill_present` unless
  `clear_kill` (`server.py:540-560` area).
- Lifecycle ops: `daemon_stop` = ctl `/kill` + direct KILL write fallback +
  SIGTERM (+SIGKILL with `force`); `daemon_start` = `scripts/start.sh
  [--live-paper]`; `daemon_restart` = `launchctl kickstart -k` when
  launchd-managed, else stop+start. All match the doc table.
- UI claims: channel ladder with crimson out-of-channel cursor
  (`styles.css:426-439` `.ladder--outside`), 5s poll skipped while hidden
  (`app.js` `setInterval(..., 5000)` with `document.hidden` guard), decision
  filter (`#dec-filter`), "rotate queued" badge (`force_rotate` in `app.js`),
  Logs grep/follow — all present.
- Tests: `python3 -m unittest tests.test_console` and full discover both run
  (suite ran 188 tests, OK).

**Discrepancies: none.**

## 2. docs/binance-paper-profile.md — CONFIRMED vs code

- `POST /en/trader/my-exchanges/master-api-profile/upsert` codified in
  `execution/profiles.py:37` (`PROFILE_UPSERT`); request body in the doc is
  byte-identical to `paper_profile_body()` (`profiles.py:45-61`), including
  `paperTrading: true`, `exchangeFamily: "BINANCE"`, `marginMode: "cross"`,
  `tradeMode: "hedge_mode"`.
- 32-hex placeholder api/secret: `_dummy_secret()` = `secrets.token_hex(16)`
  (`profiles.py:40-42`) ✓.
- Name uniqueness / HTTP 400 "You have account with that name": detected by
  `create_paper_profile` (`profiles.py:119-124`, `already_exists`) ✓.
- "Use the browser transport (`wt_browser.py api POST ...`)": the module shells
  out to `.agents/skills/wundertrading/scripts/wt_browser.py` (`profiles.py:109-110`)
  ✓ (file exists).
- `profiles.create_paper_profile("demo-bn", dry_run=False)` — signature and
  default `dry_run=True` ✓ (`profiles.py:95-96`).
- "Binance paper is futures-only; resolves to BINANCE_FUTURES": corroborated by
  `daemon.py:589-595` (`VENUE_EXCHANGES["binance"] = {"BINANCE", "BINANCE_FUTURES"}`
  with the paper-stand-in comment), `market_for_profile` (`daemon.py:641-649`,
  `*_FUTURES → "derivative"`), `resolve._exchange_for_market`
  (`execution/resolve.py:237-248`, binance+derivative → `BINANCE_FUTURES`),
  `execution/guardrails.py` `check_venue_side` (no-Short rule kept), and
  `config.yaml` `paper_profiles.binance: [demo-bn]` with the same caveat.

**Discrepancies: none.** Two facts are live-session claims that cannot be
re-verified from code (they match code comments, so they are consistent):
the $10,000 demo balance of `demo-bn`, and the exact UI drawer flow.

## 3. .agents/skills/grid-autonomy/SKILL.md

### CONFIRMED (summary)

- Loop, cadences, and config numbers: screen 60m / watch 60s (`config.yaml`
  `rescreen_minutes: 60`, `watch.interval_s: 60`); ladder base 25% / probe
  40% (≥10) / full 50% (≥30 & PF ≥1.3); `recent_pf < 1.0` kills
  (`daemon.py:704-727`, `guardrails.py check_reliability`); hysteresis Δscore
  ≥ 5 (`config.yaml policy.hysteresis_score: 5.0`, `guardrails.py
  HYSTERESIS_DEFAULT = 5.0`); re-centre 6h rate limit (`daemon.py:1439`);
  portfolio ≤ 85% ceiling (`config.yaml cash_buffer_pct: 0.15`).
- 8 fail-closed guardrails = `CHECKS` tuple in `execution/guardrails.py`
  (kill, paircode, profile, sizing, spread, venue_side, reliability, rotation) ✓.
- LLM chain CF → Nvidia → OpenRouter with rule fallback
  (`llm/provider.py:77`, `agents/swarm.py` `llm_degraded`) ✓; swarm =
  bull/bear debate → facilitator → 3-stance risk team (`agents/swarm.py:6`,
  `102-222`) ✓; `reflect.py` k=3 memories (`config.yaml memory.k: 3`) ✓.
- ctl plane `:8799`: all 8 endpoints in the table match `ctl_http.py`
  (GET health/status/reliability/observe, POST rescreen/reliability/rotate/kill);
  `/status` carries `capacity` + `account_limits` + `journal_tail` ✓.
- Capacity veto: `daemon.venue_capacity_block` (`daemon.py:1088-1129`),
  `observe.grid_capacity` (`execution/observe.py:146-157`, `max_active
  {"other": 1, "premium": 200}`) — free tier = 1 active grid bot on
  non-Hyperliquid, HYPERLIQUID_SWAP premium/200 via 0.035% builder fee ✓;
  subscription re-observed per rescreen cycle, `subscription` journal kind on
  change (`daemon.py:1154-1177`) ✓.
- Journal: last 200 events (`daemon.py:338`); all listed kinds exist in code
  (incl. `deploy-failed` via `action["kind"]` at `daemon.py:1046`,
  `rotation-veto`/`rotation-skip`, `browser-restart`, `env-heal`,
  `observe-outage`) ✓. Decision ids `dYYYYMMDD-NNN` and md5
  `payload_digest` (`agents/reflect.py:109-143`) ✓.
- Self-healing claims: `browser_watchdog` probes CDP each health pass (60s),
  restart cooldown 600s (`daemon.py:753-792`, `config.yaml
  watch.browser_restart_cooldown_s: 600`) ✓; `self_heal_env()` re-imports
  CF/PB env (`daemon.py:217-239`, journal `env-heal`) ✓; observe-outage after
  ~30 blind minutes (`daemon.py:1413-1428`) ✓; the two distinct observe-error
  strings match `execution/observe.py:385-387` ✓.
- Scripts/supervision: `start.sh` CF-key import + hard refusal,
  `--live-paper` default-off; `stop.sh` = POST `/kill` + SIGTERM → SIGKILL
  escalation (and stops PocketBase); `smoke.sh` = `daemon.py --once
  --no-confluence --top 5` (flags exist, `daemon.py:1853-1859`);
  `install_launchd.sh` installs both LaunchAgents to `~/Library/LaunchAgents`;
  plists use `/opt/homebrew/bin/python3` (TCC) + `KeepAlive
  {SuccessfulExit: false}` + `ThrottleInterval 30`; `run_launchd.py` runs
  foreground `--live-paper` and sources `.pocketbase/pb.env` ✓.
- Console `:8798`, PB `:8090` (`server.py:74` PB_URL default), KILL file at
  `agents/grid-autonomy/KILL` ✓.

### DISCREPANCY 1 (stale) — test count

- **Doc:** "Tests: `python3 -m unittest discover -s tests -t .` → **129
  offline tests**." (Safety rails, last bullet)
- **Code evidence:** running the suite today: `Ran 188 tests in 46.792s — OK`.
  Per-file method counts sum to 188 (`tests/test_console.py` 24,
  `test_daemon_manage.py` 28, `test_integration.py` 21, `test_selfheal.py` 23,
  `test_guardrails.py` 15, `test_stagnation.py` 15, `test_resolve.py` 13, …).
- **Suggested fix:** update to "188 offline tests" — or better, drop the exact
  number ("the full offline suite") so it stops drifting.

### DISCREPANCY 2 (minor, incomplete) — CF key import description

- **Doc:** "`start.sh` imports `CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_API_KEY`
  from the `dsh web` process env and refuses to start without them."
- **Code evidence:** `scripts/start.sh` also accepts `CLOUDFLARE_AI_TOKEN` as
  an alternative to `CLOUDFLARE_API_KEY`, and accepts keys already exported
  in the invoking shell (`grep -E '^CLOUDFLARE_(ACCOUNT_ID|API_KEY|AI_TOKEN)='`
  plus the "already exported in this shell" branch). Same in
  `scripts/run_launchd.py` (`CF_RE` includes `AI_TOKEN`).
- **Suggested fix:** "(or `CLOUDFLARE_AI_TOKEN`)" after `API_KEY`.

### DISCREPANCY 3 (minor, omission) — stop.sh side effect

- **Doc:** "scripts/stop.sh # POST /kill + SIGTERM (escalates to SIGKILL)".
- **Code evidence:** `stop.sh` step 3 also stops the PocketBase side channel
  (`.pocketbase/pb.pid`) and removes the daemon PID file. True as written,
  just incomplete.
- **Suggested fix:** optional; add "(also stops the PocketBase side channel)".

## 4. bootstrapping/docs/grid-fleet.md (grid-autonomy claims)

### CONFIRMED

- Daemon loop description (screen 60m / deliberate / 8 gates / deploy with
  `$10` min-notional fallback — `daemon.py:266` `MIN_USD_PER_GRID = 10.0`,
  `daemon.py:901` / watch 60s with 6h re-centre / rotate Δscore ≥ 5 /
  reflect paths `state/decisions.jsonl` + `state/reports/<ts>-<kind>.{json,md}`)
  all match the code as itemized in §3.
- Operate commands (`scripts/start.sh` dry-run default, `scripts/stop.sh`)
  and ctl port `:8799` ✓; linked paths (`agents/grid-autonomy/daemon.py`,
  `agents/grid-autonomy/README.md`) exist ✓.
- "PF < 1.0 over the last 20 samples kills the archetype" —
  `execution/reliability_grid.py:29` `RECENT_WINDOW = 20`; `recent_pf`
  computed over the last 20 pnls; kill at < 1.0 (`daemon.py:724-727`,
  `guardrails.py check_reliability`) ✓.
- Referenced tooling exists: `scripts/token_screen.py`, `market_regime.py`,
  `grid-bot.md` reference, `wt.mjs`/`launch.mjs` ✓.

### DISCREPANCY 4 (minor, omission) — ctl endpoint list

- **Doc:** "the HTTP ctl plane on `:8799` (`/health`, `/status`,
  `/reliability`, `/observe`, `POST /rescreen`, `POST /rotate`, `POST /kill`)."
- **Code evidence:** `ctl_http.py do_POST` also implements
  `POST /reliability` (queue an immediate reliability-ledger refresh) — listed
  in SKILL.md and console/README.md but missing here.
- **Suggested fix:** add `POST /reliability` to the list.

## 5. AGENTS.md grid-autonomy paragraph — CONFIRMED

Every claim verified:

- Loop, paper-profiles-only execution (`select_profile(..., paper=True)` is
  the only call form, `daemon.py:871,1104`; `autonomy.live_profiles: []`) ✓.
- 8 fail-closed guardrails (`guardrails.py CHECKS`) ✓.
- Plan-capacity-aware deploys: auto-observed subscription limits,
  HYPERLIQUID_SWAP premium/200 via 0.035% builder fee, 1 active grid bot on
  other exchanges' free tier (`observe.py:146-186`, `daemon.py:1088-1129`) ✓.
- HTTP ctl `:8799`, `decisions.jsonl` + run cards, console UI + JSON API
  `:8798` with channel-ladder fleet cards / decision ledger / reliability
  tiers / whitelisted `config.yaml` editor / confirm-gated lifecycle ops
  (all `console/server.py`) ✓.

**Discrepancies: none.**

## 6. .gitignore + scripts/*.sh + launchd/*.plist — CONFIRMED

- `.gitignore`: "see README 'State artifacts'" — `README.md` has a
  "## State artifacts (runtime, not source)" section ✓; "root .gitignore has
  a global *.json rule" — root `.gitignore` indeed contains a bare `*.json`
  with re-includes ✓; `!tests/fixtures/*.json` matches the committed
  fixtures ✓. `state/`, `watch/specs/`, `KILL`, `*.log`, `*.pid`,
  `config.yaml.bak`, `.pocketbase/` all match the runtime artifacts present
  on disk.
- `start.sh`: CF env import from `dsh web` (Linux `/proc/<pid>/environ`,
  macOS `ps -Eww`) with hard failure when unavailable; refuses when PID alive
  or KILL file present; co-starts PocketBase non-fatally (idempotent
  `setup_pocketbase.sh`, re-sources `pb.env`); `nohup python3 daemon.py "$@"`
  with PID file + `state/daemon.log` — matches its own header comments ✓.
- `stop.sh`: POST `/kill` to `GRID_DAEMON_PORT` (default 8799), SIGTERM with
  15s wait, SIGKILL escalation, removes PID file, stops PocketBase — header
  ("POST /kill to the ctl plane, then SIGTERM") accurate ✓.
- `install_launchd.sh`: copies `launchd/*.plist` to `~/Library/LaunchAgents`,
  bootout+bootstrap per user ✓; comments describe exactly what the plists do.
- `launchd/com.tvcli.grid-autonomy.plist`: `/opt/homebrew/bin/python3` +
  `scripts/run_launchd.py` foreground, `RunAtLoad`, `KeepAlive
  {SuccessfulExit: false}`, `ThrottleInterval 30`, logs to
  `~/Library/Logs/grid-autonomy-launchd.log` — matches the doc'd semantics
  (crash-only restart; ~30s restart after `kill -9`) ✓.
  `com.tvcli.serve.plist`: `tvcli serve` foreground, same KeepAlive ✓.
- `run_launchd.py`: exits cleanly when a daemon is already running, refuses
  on KILL file, imports CF keys (incl. `AI_TOKEN`) from `dsh web`, sources
  `pb.env`, execs `daemon.py --live-paper` in the foreground via `runpy` —
  its docstring matches behavior ✓.
- `setup_pocketbase.sh`: defaults `127.0.0.1:8090`, `PB_VERSION 0.40.2`,
  idempotent download/configure/start, writes `pb.env` — header comments
  match ✓.

**Discrepancies: none.**

---

## Consolidated fix list

| # | File | Severity | Fix |
|---|------|----------|-----|
| 1 | `.agents/skills/grid-autonomy/SKILL.md` | **stale fact** | "129 offline tests" → 188 (or drop the number) |
| 2 | `.agents/skills/grid-autonomy/SKILL.md` | minor | mention `CLOUDFLARE_AI_TOKEN` as accepted alternative |
| 3 | `.agents/skills/grid-autonomy/SKILL.md` | minor (optional) | note stop.sh also stops the PocketBase side channel |
| 4 | `bootstrapping/docs/grid-fleet.md` | minor | add `POST /reliability` to the ctl-plane endpoint list |

Everything else in the six audited targets is accurate against the code.
