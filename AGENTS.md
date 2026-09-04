# AGENTS.md — tvcli workspace orientation

This repository is a **TradingView Pine Script market-analysis toolkit** built
around the `tvcli` Go CLI (`module github.com/mrme000m/tvcli`). It compiles,
runs, and extracts structured signals from any Pine Script indicator/strategy
on live market data via TradingView's WebSocket + HTTP (Pine Facade) APIs, and
exposes everything (OHLCV, studies, graphics, strategy reports) as
agent-friendly JSON. It is packaged for agent consumption two ways:

1. **Agent Skills** (agentskills.io standard) in `.agents/skills/` — loaded
   on demand by any compatible harness (Pi, Claude Code, Codex, DSH, etc.):

   | Skill | Use it for |
   |-------|-----------|
   | `tvcli` | Running any of the 20 registered indicator skills (incl. `vp-pro`, `xau-scalp`, `mtf-confluence`), `eval`/`run`/`analyze` of arbitrary Pine source, the async HTTP server (`serve --daemon`, `:8765`, 11 endpoints), free-tier limits, and progressive Pine Script v5 reference docs under `references/pinescript/`. |
   | `pine2tool` | Turning any `PUB;…`/`USER;…` ID or local `.pine` file into a reusable structured analysis tool + registrable skill stub. |
   | `tv-usecases` | Discover, develop, and orchestrate all major TradingView strategy/indicator use cases for agentic usage. Bridges bdg (live-chart investigation) with tvcli (headless execution). |
   | `tv-scout` | Drive the live TradingView web chart (headful CDP) to visually represent and confirm market understanding with screenshots. |
   | `tv-watch` | Watchtower agent protocol — act on market-watch trigger events (invocation packages from `agents/watchtower/`). |
   | `openknowledge` | Reading/writing the `Wiki/` knowledge base via the `openknowledge` CLI. |
   | `codespace-prime-stack` | Operate/verify/extend the bootstrapped DSH prime-orchestrator stack in the GitHub Codespace/devcontainer (dsh + dsh-prime-orchestrator + prime-agent + CF Workers AI; the `bootstrapping/` playbook, dsh Web GUI on :3081, secrets contract, verified gotchas) — including the specialist fleet (`fleet` tag: tv-scout/tv-investigator/wt-investigator presets + grid-trading wiring; blueprint `bootstrapping/docs/grid-fleet.md`). |
   | `web-discovery` | Generalized bdg+cloak reverse-engineering loop: investigate any web platform's UX + network API, codify it, and forge reusable CLI tools, skills, dsh plugin rows, and fleet presets. |
   | `wundertrading` | WunderTrading execution layer — classify a token's market regime from public OHLCV, map it to a classic/DCA strategy config (TP ladders, stops, trailing, DCA sizing math), deploy/manage via wun MCP tools or HMAC-signed REST `/open_api` (both now also via `wt_httpx.py` httpx without browser, `browser` parity via `wt_browser.py` CDP fetch-in-page), and prove reliability from exported strategy history. MCP server `wundertrading` is configured in zcode + PrimeAgent. |
   | `grid-bot` | WunderTrading Grid-bot configurator — screen the universe, derive ATR-band channel + ATR-derived Profit-per-GRID + grid count + sizing + Stop Trigger/Pump Protection via `grid_config.py`, then create/manage via web-UI session API (`wt-grid.mjs` or `wt_browser.py` httpx+CDP `fetch()` in-page, `wt_httpx.py session` best-effort) |
   | `stealth-browser` | Stealth browser automation (vibheksoni/stealth-browser-mcp, 97 tools via `stealth-browser` stage + `dsh-mcp-client`, headful via `DISPLAY=:99` → `x11vnc:5900`/`websockify:6080` + `dsh-cloak-panel` zoom/scale) |

2. **Pi package** — `package.json` at the root carries the `pi-package`
   keyword and a `pi.skills` manifest, so `pi install <this repo>` (local path
   or `git:` URL) installs the skills in one step.

## Ground rules for agents

- **Build before use:** `go build -o tvcli ./cmd/tvcli` from this root. The
  binary is a local (gitignored) build — rebuild it after pulling.
- Auth lives in `.env` (`SESSION`, `SIGNATURE`, `TV_USER`, `DEVICE_T`) and the
  optional multi-account sidecar `accounts.json` — never commit them, never
  print cookie values. `SESSION`/`SIGNATURE` = `sessionid`/`sessionid_sign`,
  `DEVICE_T` = `device_t`, all scraped from the `/chart/` page.
- **The skill registry is the source of truth.** `tvcli skills` (and the
  server `GET /skills`) enumerate the live registry — currently **20
  indicators**. The hardcoded list inside `internal/cmd/help.go` and the table
  in `README.md` have historically drifted from the registry; trust
  `tvcli skills --json`, not the prose. ~10 parser files in
  `pkg/skill/parsers/` (e.g. `bsv`, `ict`, `ema-atr`, `anchored-vp`, `cust`,
  `mtf`, `order-flow`, `shemar`, `trend`, `vgaps`) have their `func init()`
  **commented out** — they are retired skills kept only for reference and do
  NOT appear in the registry.
- **Prefer consolidated skills over running indicators one by one.** Free
  tier allows only 2 studies per chart; `xau-scalp` (all-in-one EMA+ST+RSI+
  Squeeze+Volume+BB) and `mtf-confluence` (chart TF + 2 HTF composites in one
  run) return a full composite in a single study slot.
- Use `--json --agent` for machine-readable envelopes; `--raw` only for
  debugging. Pass Pine inputs flexibly (`length=20`, `--input in_1=4`, or
  `--input.lookback=50`) — all are merged by `internal/cmd/inputs_util.go`.
- Reference docs for Pine v5 live in
  `.agents/skills/tvcli/references/pinescript/` — consult them before writing
  or debugging Pine instead of relying on memory.
- The TradingView backend API (Pine Facade HTTP, the `data` WebSocket, and
  cookie→auth_token scraping) is documented in
  **[docs/TRADINGVIEW_BACKEND_API.md](docs/TRADINGVIEW_BACKEND_API.md)**.
- Data dumps (`*.csv`, `BINANCE_*`, `OANDA_*`, `rawdumps/`, `skill-runs/`,
  `skill_work/`, `shots/`) are local working artifacts, not source.

## Architecture (actual)

```
tvcli/
├── cmd/tvcli/          CLI entry point (main.go)
├── internal/           application glue (NOT importable)
│   ├── cli/            tiny command framework (FlagSet, Root dispatcher)
│   ├── cmd/            ~30 command implementations + skill subcommand wiring
│   ├── config/         .env load, cookie auth, tier limits, account switch
│   ├── metadb/         local script metadata db (.tv-meta.json)
│   ├── server/         HTTP server for AI agents (per-account concurrency pool)
│   ├── service/        study/​fetch orchestration (RunScript, FetchOHLCV…)
│   └── agent/          generic topology-based graphics analysis + agent report
├── pkg/                ← importable library surface
│   ├── account/        multi-account registry + per-account tier limits
│   ├── pinefacade/     HTTP client: Pine Facade (compile, save/get/delete, search, IL)
│   ├── pipeline/       script-agnostic signal extraction (order blocks, FVGs, levels…)
│   ├── runner/         high-level study orchestration + ParseOutput
│   ├── schema/         Pine metaInfo → typed inputs/plots
│   ├── skill/          20-skill registry + per-script parsers (pkg/skill/parsers/)
│   └── tradingview/    WebSocket client: protocol, chart/study lifecycle, auth
├── docs/               CLI_REFERENCE, MULTI_ACCOUNT, TIER_LIMITS, skills/, research/
├── bootstrapping/      repeatable devcontainer ansible playbooks (ansible/prime-stack.yml:
│                       dsh + dsh-prime-orchestrator + prime-agent + CF Workers AI — see
│                       the codespace-prime-stack skill in .agents/skills/)
├── go.mod              module github.com/mrme000m/tvcli (go 1.22)
└── Makefile
```

## Current working-tree state (verified)

- **Skills:** 20 registered (0 registration errors), enumerated by
  `tvcli skills`. Categories: volume (`vp`, `vp-pro`), smc (`smc`, `liq-sweep`,
  `swingarm`), trend (`ust`, `xau-trend`, `mtf-confluence`), other/price-action
  (`camarilla`, `choppiness`, `cvd`, `dvi`, `golden`, `ichimoku`, `quantum`,
  `sniper`, `squeeze`, `gold-divergence`, `xau-scalp`, `sr-breaks`).
- **HTTP server:** 11 endpoints — `/health` `/compile` `/fetch` `/clean`
  `/run` `/run-skill` `/hunt` `/skills` `/check-auth` `/accounts`
  `/queue-stats` (the `serve --help` line and old README list only 6 — stale).
  `/hunt` fans one skill across many symbols over the account pool; per-account
  concurrency is capped at each account's tier `MaxIndicators` (free = 2).
- **Build:** `go build -o tvcli ./cmd/tvcli` passes cleanly (verified); the
  built binary enumerates the 20 skills and the help text lists all 11
  endpoints. `runSkillCompute`/`runSkillWithAccount` are `*Server` methods.
  (`pkg/skill/parsers/mtf_confluence.{go,pine}` and
  `pkg/tradingview/inputparse.go` are now committed at HEAD.)
- **Codespace prime stack (verified 2026-09-04):** the devcontainer is
  bootstrapped into a DSH prime-orchestrator host by
  `bootstrapping/ansible/prime-stack.yml` (run by `post-create.sh`,
  idempotent, never build-breaking): dsh `0.1.1-rc.2` (exact — plugin
  compatibility), `dsh-prime-orchestrator` in profile `web`,
  `prime-orchestrator` as the default agent preset, `prime-agent` CLI, CF
  Workers AI models from the `CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_API_KEY` repo
  codespace secrets, bw CLI secrets via `browser-debug/secrets/bw-provision.sh`.
  The playbook is a slim orchestrator (apt + tags); every install/config step
  runs in the unit-tested Python engine `bootstrapping/python/prime_stack/`
  (CLI: `bootstrapping/python/bin/prime-stack [--dry-run] <stage|group>`;
  JSON envelopes on stdout, tests: `python3 -m unittest discover -s
  bootstrapping/python/tests -t bootstrapping/python`).
  dsh Web GUI (Prime fleet column) on forwarded port 3081, opt-in autostart
  via `touch .dsh-autoweb`. E2E-verified in the codespace: post-create exit 0,
  LLM answers, GUI serving. See the `codespace-prime-stack` skill.

## Key commands (subset)

| Command | Purpose | Auth |
|---------|---------|------|
| `fetch` | Raw OHLCV (CSV+JSON); `--to` anchors a past-time window; `--deep`/`--all` backfill | none |
| `eval` | Run arbitrary Pine source (compile→save→run→delete) | SESSION+SIGNATURE+DEVICE_T |
| `run` | Run a pre-published script by Pine ID | SESSION+SIGNATURE |
| `analyze` | Universal script analyzer (any Pine ID, schema-driven, no per-script parser) | SESSION+SIGNATURE |
| `backtest` | Run a STRATEGY, extract full trade list + metrics | SESSION+SIGNATURE |
| `scan` | Search public scripts, classify strategy vs indicator | SESSION+SIGNATURE |
| `serve` | Start/stop/status HTTP server (`--daemon`, `--stop`, `--status`) | SESSION+SIGNATURE |
| `skills` | List the 20 registered indicator skills (`--json`) | none |
| `account` | Manage `accounts.json` multi-account pool | — |
| `<skill>` | Run a registered skill, e.g. `tv smc --symbol OANDA:XAUUSD --tf 15m --agent --json` | SESSION+SIGNATURE |

See [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md) and `tvcli --help` for the
full list, and the tvcli skill for free-tier limits and Pine v5 references.
