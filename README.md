# tvcli — TradingView Pine Script CLI (Go)

A Go CLI that compiles, runs, and extracts trading signals from any TradingView Pine Script via the TradingView WebSocket and HTTP APIs. Runs arbitrary Pine source code without a pre-published script ID, extracts graphics (boxes, lines, labels, tables) and full strategy reports (per-trade data, Sharpe, Sortino, max drawdown, etc.) into agent-friendly JSON.

## Quick Start

```bash
# 1. Build
go build -o tvcli ./cmd/tvcli

# 2. Configure auth (.env in project root)
cat > .env <<EOF
SESSION=your_sessionid_cookie
SIGNATURE=your_sessionid_sign_cookie
TV_USER=your_tradingview_username
DEVICE_T=your_device_t_cookie
EOF

# 3. Fetch live OHLCV (no auth needed for public data)
./tvcli fetch --symbol BINANCE:BTCUSDT --tf 1H --bars 50 --json-out bars.json

# 4. Run any Pine Script from source
./tvcli eval script.pine --signals --json --symbol BINANCE:BTCUSDT --tf 1H

# 5. Run a pre-published script by Pine ID
./tvcli run "PUB;6daafb2cabe6419d98ae25229d2327f8" --signals --agent --json --symbol BTCUSDT --tf 1H

# 6. Run a built-in skill (22 indicators pre-configured)
./tvcli smc --symbol BTCUSDT --tf 1H --agent --json

# 7. Search TradingView's public script library
./tvcli search "volume profile" --limit 10 --json

# 8. Start the HTTP server for AI agent integration
./tvcli serve --addr :8765
```

## Architecture

```
tvcli/
├── cmd/tvcli/          CLI entry point
├── internal/
│   ├── cli/            CLI framework (routing, flags)
│   ├── cmd/            Command implementations (23 commands)
│   ├── config/         Env/.env config, cookie auth, tier limits
│   ├── metadb/         Local script metadata database
│   ├── server/         HTTP server for AI agent integration
│   ├── service/        Script execution orchestration
│   └── skill/
│       ├── skill.go    Skill, InputDef, AgentResult types
│       ├── registry.go Global skill registry
│       └── parsers/    Per-skill output parsers (22 indicators)
├── pkg/
│   ├── pinefacade/     HTTP client: Pine Facade (CRUD, search, compile)
│   ├── pipeline/       Signal extractor (periods → events/levels/report)
│   ├── runner/         WS orchestration (one-shot, persistent, loop, sweep)
│   ├── schema/         PineScript metaInfo schema parsing
│   └── tradingview/    WebSocket client: protocol, chart/study lifecycle
├── docs/
│   ├── skills/         Per-skill documentation (21 files)
│   ├── research/       Research notes from reverse engineering
│   └── CLI_REFERENCE.md
├── go.mod
├── Makefile
└── .env (not committed)
```

## Commands

| Command | Purpose | Auth |
|---------|---------|------|
| `fetch` | Fetch raw OHLCV data (CSV + JSON) | None |
| `sync` | Fetch + compress OHLCV to .json.gz | None |
| `eval` | Run arbitrary Pine Script source (compile → save → run → delete) | SESSION+SIGNATURE+DEVICE_T |
| `run` | Run a pre-published script by Pine ID | SESSION+SIGNATURE |
| `compile` | Compile Pine Script (syntax check only) | SESSION+SIGNATURE |
| `search` | Search TradingView's public script library | SESSION+SIGNATURE |
| `publist` | List public scripts (paginated) | SESSION+SIGNATURE |
| `top` | Fetch top public scripts to JSON | SESSION+SIGNATURE |
| `create` | Create a new remote script | SESSION+SIGNATURE+TV_USER |
| `push` | Push local changes to remote script | SESSION+SIGNATURE+TV_USER |
| `pull` | Pull remote script source to local | SESSION+SIGNATURE |
| `delete` | Delete a remote script | SESSION+SIGNATURE+TV_USER |
| `list` | List tracked scripts (local or remote) | Optional |
| `inputs` | Inspect Pine inputs vs Go-declared inputs | SESSION+SIGNATURE |
| `skills` | List registered indicator skills | None |
| `clean` | Clean chart sessions to free indicator slots | SESSION+SIGNATURE |
| `serve` | Start HTTP server for AI agent integration | SESSION+SIGNATURE |

## Built-in Skills (22 Indicators)

| Command | Skill | Category |
|---------|-------|----------|
| `cust` | ScalpQuant v2 (private) | Scalping |
| `bsv` | Buy/Sell Volume | Volume |
| `dvi` | Delta Volume Intensity | Volume |
| `vgaps` | Volume Gaps & Imbalances | Volume |
| `anchored-vp` | Anchored Volume Profile | Volume |
| `vp` | Volume Profile Fixed Range | Volume |
| `order-flow` | Volume Spike Order Flow | Volume |
| `smc` | Smart Money Concepts | Market Structure |
| `ict` | ICT Auto-Validated SMC | Market Structure |
| `liq-sweep` | Institutional Liquidity Sweep | Market Structure |
| `sr-breaks` | Support/Resistance Breaks | Market Structure |
| `sniper` | Precision Sniper | Price Action |
| `swingarm` | SwingArm ATR Trend | Trend |
| `ema-atr` | EMA + ATR Pro Engine | Trend |
| `quantum` | EMA Ribbon | Trend |
| `ust` | Ultra Sensitive SuperTrend | Trend |
| `trend` | Self-Aware Trend System | Trend |
| `golden` | Golden Rule Strategy | Strategy |
| `shemar` | SHEMAR HMA + SMC Confidence | Confluence |
| `mtf` | XAUUSD MTF Trend Dashboard | Multi-Timeframe |
| `gold-divergence` | Gold RSI Divergence | Divergence |
| `xau-trend` | XAUUSD EMA + Bollinger Trend | Trend |

## Output Formats

## Turn Any Script Into an Analysis Tool

The workspace ships an agent skill — `.agents/skills/pine2tool` — that turns any
Pine script (public ID, private ID, or local `.pine`) into a reusable, structured
analysis tool without a hand-written parser:

```bash
# analyze a public script + generate a reusable skill stub
./.agents/skills/pine2tool/bin/pine2tool.sh \
    "PUB;3b3ebba156574e058cc8ae73dc5c7fa2" \
    --symbol BINANCE:BTCUSDT --tf 1H --input "in_1=4,in_0=3" --out skill_work/vp_ob

# same for a local .pine file
./.agents/skills/pine2tool/bin/pine2tool.sh ./my_script.pine --tf 5m --out skill_work/tool
```

It downloads the source, introspects inputs, runs with custom inputs, runs the
universal analyzer (`tv analyze`), and emits agent-ready JSON plus a registrable
`skill.yaml` / `SKILL.md` stub. The universal analyzer uses a **two-layer design**
that works across any script without per-script matchers:

- **Layer 1** (`pkg/pipeline/extract.go`): flat signal extraction from every TV
  draw type (boxes, lines, labels, tables, histograms) — zero script-specific code.
- **Layer 2** (`internal/agent/graphics_generic.go`): **generic topology-based**
  graphics analysis that groups elements by geometric topology (shared edges,
  width, extension) and infers semantics from group properties — not from
  script-specific layouts. Auto-extracts volume-profile **POC/VAH/VAL**,
  **order-block zones**, **FVG/gap boxes**, **breaker blocks**, **liquidity
  levels** and **session markers** purely from the drawing layer.

Per-script parsers in `internal/skill/parsers/` remain only for **registered
skills** where exact Pine field names and plot semantics are known.

### Passing Pine inputs (all spellings work)
```
tvcli eval s.pine length=20 src=close        # positional after the file
tvcli run  "PUB;x" --input in_1=4,in_0=3      # --input key=value / comma list
tvcli analyze "PUB;x" --input.lookback=50     # dotted form
tvcli eval  s.pine --in_1=4                   # raw TV input ID
```
All are merged by `internal/cmd/inputs_util.go` and resolved to canonical TV
input IDs by ID, index, or human-readable name (`PineIndicator.SetOption`).


### `--json` (signals format)
Raw extracted signals: classifications, last values, series, levels, events, graphic counts, strategy report.

### `--agent` (agent-ready envelope)
Agent-ready v2 envelope: market data, structure, opportunities, narrative, conformance score. Strategy report included in `Structure.strategy`.

### `--raw`
Unprocessed capture: raw periods array, graphic map, strategy report. For debugging and development.

## Authentication

1. Log in to [tradingview.com](https://www.tradingview.com/chart/)
2. Open DevTools → Application → Cookies → `https://www.tradingview.com`
3. Copy these cookie values into `.env`:

```
SESSION=<sessionid cookie value>
SIGNATURE=<sessionid_sign cookie value>
TV_USER=<your TradingView username>
DEVICE_T=<device_t cookie value>
```

> **Important:** Cookies must be fetched from the `/chart/` page, not the main page. The `device_t` cookie is required for study creation.

## Subscription Tiers

| Tier | Charts | Indicators | Connections | Bars | Calc Timeout |
|------|--------|------------|-------------|------|--------------|
| `free` | 1 | 2 | 2 | 180 | 20s |
| `essential` | 2 | 5 | 10 | 365d | 40s |
| `plus` | 4 | 10 | 20 | unlimited | 40s |
| `premium` | 8 | 25 | 50 | unlimited | 40s |
| `ultimate` | 16 | 50 | 200 | unlimited | 100s |

Set `TV_TIER=free` (default) in `.env` to match your plan. The CLI auto-caps bars
and cleans up study slots for free accounts. See **[docs/TIER_LIMITS.md](docs/TIER_LIMITS.md)**
for the full, current subscription feature/price matrix (scraped from
`tradingview.com/pricing`) and how each plan gates Pine-script capabilities.

## HTTP Server

```bash
./tvcli serve --addr :8765
```

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Status check (tier, user, endpoint) |
| `/compile` | POST | Compile Pine Script source |
| `/fetch` | POST | Fetch OHLCV data |
| `/clean` | POST | Clean chart sessions |
| `/run` | POST | Compile + run Pine Script |

## Development

```bash
make build     # go build -o tvcli ./cmd/tvcli
make test      # go test ./...
make vet       # go vet ./...
make lint      # go vet + staticcheck (if installed)
make skills    # tvcli skills
```

## Cross-Compile

```bash
GOOS=linux GOARCH=amd64 go build -o tvcli-linux ./cmd/tvcli
GOOS=linux GOARCH=arm64 go build -o tvcli-arm64 ./cmd/tvcli
GOOS=windows GOARCH=amd64 go build -o tvcli.exe ./cmd/tvcli
```

## License

Unofficial TradingView API client. Not affiliated with TradingView Inc.
