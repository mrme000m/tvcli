# The tvcli Devcontainer — Complete TradingView Analysis & Debugging System

One container, every tool, every secret: headless **and** headful TradingView
market analysis, multi-account parallel symbol sweeps, custom Pine Script
running and backtesting, backend/frontend API debugging with `bdg`, and the
self-improvement loops (skills + scout KB) that extend the system itself.
Secrets come from the **Bitwarden vault via the `bw` CLI** — nothing secret is
ever committed or inlined into shell profiles.

## Component map

| Layer | Tool | What it does |
|---|---|---|
| Headless engine | `tvcli` (Go, built by post-create) | compile/run/extract signals from any Pine script; 20-skill registry; `eval`/`run`/`analyze`/`backtest`/`scan`; async HTTP server `:8765` |
| Multi-account pool | `accounts.json` (provisioned from vault) | 40+ TradingView accounts, per-account tier limits (free = 2 studies), round-robin failover; `POST /hunt` fans one skill across N symbols in parallel |
| Headful browser | CloakBrowser (stealth Chromium) + `launch.mjs`/`tv.mjs` | authenticated headful chart on a virtual display; cookie injection from `browser-debug/.env` |
| CDP debugger | `bdg` (browser-debugger-cli, `bdg/dist/index.js`) | attach to the live browser: raw CDP, DOM, network capture, `tv` command group (WS protocol capture, studies add/remove, drawings, chart model) |
| Display stack | Xvfb `:99` + x11vnc `:5900` + noVNC `:6080` | virtual screen for headful Chromium on headless hosts; watch the chart over VNC/noVNC |
| Frontend tooling | `chart-control.mjs`, `max-chart.mjs`, `toggle-widgets.mjs` | right-rail UI automation with real CDP input events, before/after screenshots |
| Vision | `vision.mjs` (Mistral) | describe/diff chart screenshots — visual confirmation of UI + chart state |
| Secrets | `bw` CLI + `secrets/bw-provision.sh` | Bitwarden-vault → gitignored runtime files (`.env`, `accounts.json`, `browser-debug/.env`, `secrets/runtime/*.env`) |
| Deps management | `browser-debug/ansible/deps.yml` | idempotent check/install of every layer above (`--tags node,browser,cdp,bdg,proxy,display,bw,…`) |
| Agent surface | `.agents/skills/` (tvcli, tv-scout, tv-usecases, pine2tool, tv-network) + `Wiki/` (openknowledge) | agent-facing manuals, recipes, progressive Pine v5 references |
| LLM assistant | OpenCode (Cloudflare Workers AI creds from vault) | in-container coding agent for the self-improvement loop |

## Ports & services

| Port | Service |
|---|---|
| 8765 | tvcli HTTP server — `GET /health /skills /accounts /queue-stats /check-auth`, `POST /fetch /run /run-skill /hunt /compile /clean` |
| 9222–9321 | CloakBrowser CDP (`/json`, page WebSocket, bdg attach) |
| 5900 | VNC onto the Xvfb display (what the browser "sees") |
| 6080 | noVNC web viewer (`/vnc.html`) |
| 22 | sshd feature (devcontainer feature) |

`post-start.sh` starts the display stack on every boot and — if the marker
file `.tvcli-autoserve` exists — also starts `tvcli serve --daemon`.

## Secrets — Bitwarden vault

Schema, one-time codespace setup (`BW_CLIENTID` / `BW_CLIENTSECRET` /
`BW_PASSWORD`), provisioning and rotation: **[browser-debug/secrets/README.md](../browser-debug/secrets/README.md)**.
Targets are always gitignored runtime files; the old `.env-secrets` →
`~/.profile` value-pasting is gone (rc files only get guarded `source` lines).

## Core workflows

### 1. Parallel multi-symbol analysis (multi-account WS pool)
```bash
tvcli serve --daemon                      # or: touch .tvcli-autoserve + restart
curl -s localhost:8765/queue-stats        # per-account slot usage
curl -s -X POST localhost:8765/hunt -d '{
  "skill": "squeeze", "timeframe": "4H", "bars": 180,
  "symbols": ["OANDA:XAUUSD","BINANCE:BTCUSDT","BINANCE:ETHUSDT"]}' | jq
```
N accounts → N symbols concurrently; per-symbol failover on auth/limit errors.

### 2. Custom Pine Script — analyze, backtest, productize
```bash
tvcli eval my-idea.pine --signals --agent --symbol OANDA:XAUUSD --tf 15m
tvcli backtest "PUB;<strategy-id>" --symbol OANDA:XAUUSD --tf 1H --agent
tvcli scan "gold scalp" --strategy        # search + classify public scripts
.agents/skills/pine2tool/bin/pine2tool.sh "PUB;abc123"   # → reusable skill stub
```

### 3. Backend API analysis
Surfaces documented in **[docs/TRADINGVIEW_BACKEND_API.md](TRADINGVIEW_BACKEND_API.md)**:
Pine Facade HTTPS, the `data` WebSocket protocol, cookie→auth_token scraping.
```bash
bdg tv ws -s 8                 # capture the live WS protocol frames
tvcli fetch --symbol OANDA:XAUUSD --tf 5m --to 1788222600   # point-in-time anchor
```

### 4. Frontend / chart analysis
```bash
node tv.mjs                                   # authenticated headful chart
node bdg/dist/index.js tv studies             # what's on the chart
node bdg/dist/index.js tv study add "RSI" --inputs '{"length":21}'
node bdg/dist/index.js network list            # captured API calls
```

### 5. Visual confluence (represent + confirm)
From the `visual/` sibling workspace (tvvisual): `confluence xau-scalp`
runs tvcli compute → draws SL/TP/bias on the live chart → screenshot →
exact-value readback + drift report. Inside this container the chart runs on
the Xvfb display (watch it via VNC/noVNC).

### 6. Self-improvement loop
- **Extend the skill set**: `pine2tool` turns any Pine ID into a parser-less
  structured tool; new skills register in `pkg/skill/parsers/`.
- **Repair the visual layer**: tv-scout probe → recipe → verify → codegen
  (KB in `~/.tvvisual/scout/`).
- **Verify the whole stack**: `ansible-playbook … --tags <layer>` re-checks
  any dependency; `secrets/bw-provision.sh --dry-run` validates the manifest.
- **Understand UI changes**: `vision.mjs` diffs screenshots (Mistral).
- Agents document findings into the `Wiki/` openknowledge base and the
  `.agents/skills/` manuals, which future agents (and OpenCode) consume.

## Verification checklist (smoke tests)

```bash
go build -o tvcli ./cmd/tvcli && ./tvcli skills | head -3
./tvcli check-auth --json | jq .username, .canRunStudies
curl -s localhost:8765/health | jq .
node bdg/dist/index.js tv chart
bash browser-debug/secrets/bw-provision.sh --dry-run
ansible-playbook browser-debug/ansible/deps.yml -i localhost, \
  -e ansible_connection=local -e tv_workspace="$PWD/browser-debug" \
  --tags display,bdg,bw --skip-tags guacamole,kasm
```
