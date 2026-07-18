# tvcli — TradingView Pine Script CLI (Go)

A Go port of the JavaScript TradingView CLI tools for managing Pine Scripts via HTTP and WebSocket APIs.

## Source Files

This project is a clean port of three JavaScript files:

| Go Package | JS Source | Lines (JS → Go) |
|-----------|-----------|-----------------|
| `cmd/tvcli/` + `pkg/pinefacade/` | `/Volumes/ExMac/code/tradingview/tv-cli.js` | 1,721 → 1,514 |
| `pkg/tradingview/` | `/Volumes/ExMac/code/tradingview/tv.js` | 2,275 → 1,015 |
| `pkg/runner/` | `/Volumes/ExMac/code/tradingview/js-experiment06/generic-indicator.cjs` | 1,090 → 357 |

### JS File Reference

- **`/Volumes/ExMac/code/tradingview/tv-cli.js`** — Unified Pine Script Manager CLI. Provides CRUD operations (`create`, `pull`, `push`, `delete`), compilation, search, and YAML input management via the Pine Facade HTTP API. Uses `SESSION`/`SIGNATURE` cookies for auth.

- **`/Volumes/ExMac/code/tradingview/tv.js`** — TradingView WebSocket API client. Implements the `~m~<len>~m~<json>` framing protocol, `Client`/`ChartSession`/`ChartStudy`/`QuoteSession` classes, `PineIndicator`/`BuiltInIndicator` types, and the `PineFacadeClient` HTTP wrapper. Supports SOCKS proxy, gzip, and custom headers.

- **`/Volumes/ExMac/code/tradingview/js-experiment06/generic-indicator.cjs`** — Universal indicator runner. Connects via WebSocket, applies any public Pine Script to a chart, extracts numerical data, graphics, strategy reports, and generates agent-ready JSON output with signals, trends, and confluence scoring.

## Project Index

For a full inventory of all JS-based TVCLI projects in `/Volumes/ExMac/code/tradingview/`, see:

- **`/Volumes/ExMac/code/tradingview/Index.md`** — Complete project inventory, ranking, API endpoints, and architecture overview.

## Structure

```
go/
├── cmd/tvcli/main.go           (867 lines)  — CLI entry point, all commands
├── internal/config/config.go   (127 lines)  — Env/`.env` config, cookie auth
├── pkg/
│   ├── pinefacade/
│   │   ├── client.go           (414 lines)  — HTTP client (CRUD, search, compile)
│   │   ├── parser.go           (233 lines)  — PineScript input extraction
│   │   └── types.go            (63 lines)   — Response types
│   ├── tradingview/
│   │   ├── client.go           (304 lines)  — WebSocket client, auth token fetch
│   │   ├── chart.go            (238 lines)  — Chart session, symbol loading
│   │   ├── study.go            (223 lines)  — Study/indicator execution
│   │   ├── indicator.go        (169 lines)  — PineIndicator + BuiltinIndicator
│   │   └── protocol.go         (81 lines)   — ~m~ framing protocol
│   └── runner/
│       └── runner.go           (357 lines)  — Generic indicator output parser
├── .env                        — Session cookies (not committed)
├── go.mod
└── go.sum
```

## Authentication

Pine Script CRUD operations require browser session cookies from TradingView.

### Setup

1. Log in to [tradingview.com](https://www.tradingview.com)
2. Open DevTools → Application → Cookies
3. Copy the `sessionid` and `sessionid_sign` cookie values
4. Create/update `.env` in this directory:

```
SESSION=<sessionid cookie value>
SIGNATURE=<sessionid_sign cookie value>
TV_USER=<your TradingView username>
```

### Auth requirements by command

| Command | Auth Required |
|---------|--------------|
| `list` (local) | None |
| `list --remote` | SESSION + SIGNATURE |
| `pull` | SESSION + SIGNATURE |
| `search` | SESSION + SIGNATURE |
| `compile` | SESSION + SIGNATURE + TV_USER |
| `create` | SESSION + SIGNATURE + TV_USER |
| `push` | SESSION + SIGNATURE + TV_USER |
| `delete` | SESSION + SIGNATURE + TV_USER |
| `run` | SESSION + SIGNATURE (anonymous fallback) |

## Build & Run

```bash
cd /Volumes/ExMac/code/tradingview/go

# Build
go build -o tvcli ./cmd/tvcli

# Run
./tvcli list --remote
./tvcli pull PUB;ff1a0136336340f38e908eeb12ea33aa
./tvcli run PUB;ff1a0136336340f38e908eeb12ea33aa --symbol BTCUSDT --tf 15m --json
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `github.com/gorilla/websocket` | WebSocket client for TradingView protocol |
| `github.com/joho/godotenv` | Auto-load `.env` files |
| `gopkg.in/yaml.v3` | YAML input file generation |

## Cross-Compile

```bash
# Linux amd64 (VPS)
GOOS=linux GOARCH=amd64 go build -o tvcli-linux ./cmd/tvcli

# Linux arm64 (Raspberry Pi)
GOOS=linux GOARCH=arm64 go build -o tvcli-arm64 ./cmd/tvcli

# Windows
GOOS=windows GOARCH=amd64 go build -o tvcli.exe ./cmd/tvcli
```
