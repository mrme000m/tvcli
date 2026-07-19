# tvcli — TradingView Pine Script CLI (Go)

A Go CLI tool that manages, compiles, and runs TradingView Pine Scripts via TradingView's HTTP and WebSocket APIs. Includes a skill framework for wrapping indicators as typed, agent-ready commands.

## Architecture

```
go/
├── cmd/tvcli/
│   └── main.go                  CLI entry point
├── internal/
│   ├── cli/                     Generic CLI framework (root, routing)
│   ├── cmd/                     Command implementations (run, list, pull, skills, ...)
│   ├── config/                  Env/`.env` config, cookie auth
│   ├── metadb/                  Local metadata database
│   ├── service/                 Script execution service (run, fetch)
│   └── skill/
│       ├── skill.go             Skill, InputDef, SkillResult, AgentResult types
│       ├── registry.go          Global Register/Get/All skill registry
│       └── parsers/             One file per skill (ParseOutput + FormatText)
├── pkg/
│   ├── pinefacade/              HTTP client for Pine Facade (script CRUD, search, compile)
│   ├── pipeline/                Generic schema-guided signal extractor (--signals)
│   ├── runner/                  Persistent/multi-run WS orchestration
│   ├── schema/                  PineScript metaInfo schema parsing
│   └── tradingview/             WebSocket client, protocol framing, chart/study lifecycle
├── .env                         Session cookies (not committed)
├── go.mod
└── go.sum
```

## Key Packages

| Package | Import Path | Purpose |
|---------|-------------|---------|
| `tradingview` | `github.com/ch99q/tvcli/pkg/tradingview` | WS client, protocol framing (`~m~<len>~m~<json>`), chart/study lifecycle |
| `pinefacade` | `github.com/ch99q/tvcli/pkg/pinefacade` | HTTP client for script CRUD, search, compile |
| `runner` | `github.com/ch99q/tvcli/pkg/runner` | Persistent WS runner, input sweep / multi-run analysis |
| `pipeline` | `github.com/ch99q/tvcli/pkg/pipeline` | Generic schema-guided signal extractor (`--signals`) |
| `schema` | `github.com/ch99q/tvcli/pkg/schema` | PineScript `metaInfo` schema parsing (plots, inputs, styles) |
| `skill` | `github.com/ch99q/tvcli/internal/skill` | Skill framework: `Skill`, `InputDef`, `SkillResult`, registry |
| `parsers` | `github.com/ch99q/tvcli/internal/skill/parsers` | Per-skill output parsers (one `.go` file each) |
| `cmd` | `github.com/ch99q/tvcli/internal/cmd` | CLI command implementations |
| `service` | `github.com/ch99q/tvcli/internal/service` | Script execution service |

## Quick Start

```bash
# Build
go build -o tvcli ./cmd/tvcli

# Run any public Pine script
./tvcli run "PUB;ff1a0136336340f38e908eeb12ea33aa" --symbol BTCUSDT --tf 1h --bars 50 --json

# Run with generic signal extraction
./tvcli run "PUB;ff1a0136336340f38e908eeb12ea33aa" --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json

# Run a registered skill command
./tvcli vgaps --symbol BTCUSDT --tf 1h --bars 50 --json
```

## Skill Commands

Each registered skill wraps a Pine Script indicator with typed inputs, presets, and structured output parsing.

```bash
# List all available skills
./tvcli skills --json

# Run a skill with agent-ready output
./tvcli smc --symbol BTCUSDT --tf 1h --bars 500 --agent --json

# Use a preset
./tvcli sniper --symbol BTCUSDT --tf 5m --preset scalping --agent --json

# Override individual inputs
./tvcli dvi --symbol BTCUSDT --tf 1h --input length_volatility=20 --agent --json

# Bypass custom parser, use generic extractor
./tvcli dvi --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json
```

### Available Skills

| Command | Skill | Pine ID |
|---------|-------|---------|
| `bsv` | Buy/Sell Volume | `PUB;28a4da159ce246dab2cb6524c25f950f` |
| `dvi` | Delta Volume Intensity | `PUB;bdd3bc54cf9f4dc6b42e6b2879b4eed2` |
| `ust` | Ultra Sensitive SuperTrend | `PUB;fc33f2d98699414a8585923116dbd959` |
| `swingarm` | SwingArm ATR Trend | `PUB;GdkmXaTINI8knwuCrctQD1pB5dFaRnyr` |
| `ema-atr` | EMA + ATR Pro Engine | `PUB;7d5f8755ab67400899ef73a9898471e4` |
| `sr-breaks` | Support/Resistance Breaks | `PUB;NXS6SoOdr880Hrvh9vA36UcAjC14bOkc` |
| `shemar` | SHEMAR HMA ST + SMC Confidence | `PUB;70f6e4e05f9c439c9d1f8fe26019357e` |
| `quantum` | EMA Ribbon [Krypt] | `PUB;GOYNhZP4X9VEbYA54MRIYU5FPvyr5IJB` |
| `vgaps` | Volume Gaps & Imbalances | `PUB;ff1a0136336340f38e908eeb12ea33aa` | heavy; needs paid tier |
| `anchored-vp` | Anchored Volume Profile (graphics-only) | `PUB;92974e0a3cfb481eaf058cdab9f925a3` | no period data; errors on BTCUSD |
| `mtf` | XAUUSD MTF Trend Dashboard | `PUB;d1ad30c0261f49f297357f8aa2a7854a` | XAUUSD-specific |
| `sniper` | BS Buy & Sell Signals with EMA | `PUB;0287a71c10904118b75d4360a32c0579` |
| `smc` | Smart Money Concepts | `PUB;6daafb2cabe6419d98ae25229d2327f8` |
| `golden` | Golden Rule Strategy | `PUB;6daafb2cabe6419d98ae25229d2327f8` | wrong PineID; reports no_data |
| `trend` | Self-Aware Trend System | `PUB;0f80bcf05d544d4c98fde06faab1c976` | heavy; needs paid tier |
| `ict` | ICT Auto-Validated SMC | `PUB;789a5c79bfe9443585da09e85ece73de` | heavy; needs paid tier |
| `liq-sweep` | Institutional Liquidity Sweep | `PUB;b9372355c2e6483f952ca49a21d2ebbb` |

### Common CLI Flags

| Flag | Meaning |
|------|---------|
| `--symbol <SYM>` | Trading symbol (default: `OANDA:XAUUSD`) |
| `--tf <timeframe>` | `1m`, `5m`, `15m`, `1h`, `4h`, `1D` |
| `--bars <n>` | Number of historical bars |
| `--input key=value` | Override a Pine input (repeatable) |
| `--preset <name>` | Use a bundled preset (`scalping`, `default`, `swing`, etc.) |
| `--json` | JSON output |
| `--agent` | Agent-ready JSON envelope |
| `--signals` | Use generic schema-guided extractor (bypasses custom parser) |
| `--raw` / `--raw-out <file>` | Dump raw periods + graphic data |
| `--schema` | Show Pine metaInfo schema without running (works for `run` and any skill command) |

`tv skills --json` now reports each skill's `category`, `tier` (minimum
TradingView plan), and `knownBroken` (e.g. wrong PineID, paid-tier only, or
graphics-only) so agents can avoid or special-case fragile skills.

## Authentication

Pine Script operations require browser session cookies from TradingView.

1. Log in to [tradingview.com](https://www.tradingview.com)
2. Open DevTools → Application → Cookies
3. Copy `sessionid` and `sessionid_sign` cookie values
4. Create/update `.env` in this directory:

```
SESSION=<sessionid cookie value>
SIGNATURE=<sessionid_sign cookie value>
TV_USER=<your TradingView username>
```

| Command | Auth Required |
|---------|--------------|
| `run` | SESSION + SIGNATURE (anonymous fallback) |
| `list` (local) | None |
| `list --remote`, `pull`, `search` | SESSION + SIGNATURE |
| `create`, `push`, `delete` | SESSION + SIGNATURE + TV_USER |

## Subscription Tiers

| Tier | Charts | Indicators | Connections | Bars | Calc Timeout |
|------|--------|------------|-------------|------|--------------|
| `free` | 1 | 2 | 2 | 180d | 20s |
| `essential` | 2 | 5 | 10 | 365d | 40s |
| `plus` | 4 | 10 | 20 | unlimited | 40s |
| `premium` | 8 | 25 | 50 | unlimited | 40s |
| `ultimate` | 16 | 50 | 200 | unlimited | 100s |

Set `TV_TIER=free` (default) in `.env` to match your plan.

## Cross-Compile

```bash
GOOS=linux GOARCH=amd64 go build -o tvcli-linux ./cmd/tvcli
GOOS=linux GOARCH=arm64 go build -o tvcli-arm64 ./cmd/tvcli
GOOS=windows GOARCH=amd64 go build -o tvcli.exe ./cmd/tvcli
```
