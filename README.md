# tvcli — TradingView Pine Script CLI (Go)

A Go implementation of the TradingView Pine Script CLI. It manages, compiles, and runs Pine Scripts via TradingView's HTTP and WebSocket APIs.

This Go codebase is the authoritative implementation. The JavaScript files in `/Volumes/ExMac/code/tradingview/` are historical reference material only; they are no longer the source of truth for behavior or output.

## Source Files

The implementation is organized into a few focused Go packages:

| Go Package | Responsibility |
|-----------|----------------|
| `cmd/tvcli/` + `pkg/pinefacade/` | CLI entry point; Pine Facade HTTP client for script CRUD, search, and compile. |
| `pkg/tradingview/` | WebSocket client, protocol framing (`~m~<len>~m~<json>`), chart/study lifecycle, indicator types. |
| `pkg/runner/` | Generic indicator runner and persistent/multi-run orchestration. |
| `internal/skill/` + `internal/skill/parsers/` | Declarative skill registry and per-Pine-Script output parsers. |

Historical JS reference files (no longer canonical):

- `/Volumes/ExMac/code/tradingview/tv-cli.js`
- `/Volumes/ExMac/code/tradingview/tv.js`
- `/Volumes/ExMac/code/tradingview/js-experiment06/generic-indicator.cjs`

## Project Index

For historical context on earlier JS-based TVCLI projects in `/Volumes/ExMac/code/tradingview/`, see `/Volumes/ExMac/code/tradingview/Index.md`.

## Structure

```
go/
├── cmd/tvcli/main.go             — CLI entry point, all commands
├── internal/config/config.go     — Env/`.env` config, cookie auth
├── pkg/
│   ├── pinefacade/
│   │   ├── client.go             — HTTP client (CRUD, search, compile)
│   │   ├── parser.go             — PineScript input extraction
│   │   └── types.go              — Response types
│   ├── tradingview/
│   │   ├── client.go             — WebSocket client, auth token fetch, IsConnected()
│   │   ├── chart.go              — Chart session, symbol loading
│   │   ├── study.go              — Study/indicator execution
│   │   ├── indicator.go          — PineIndicator + BuiltinIndicator
│   │   └── protocol.go           — ~m~ framing protocol
│   └── runner/
│       ├── runner.go             — Generic indicator output parser
│       ├── persistent.go         — Persistent WS connection runner
│       └── multirun.go           — Input sweep / multi-run analysis
├── .env                          — Session cookies (not committed)
├── go.mod
└── go.sum
```

## Use as a Library

Import the packages directly in your Go project:

```go
import (
    "github.com/ch99q/tvcli/pkg/tradingview"
    "github.com/ch99q/tvcli/pkg/runner"
    "github.com/ch99q/tvcli/pkg/pinefacade"
)
```

### Quick Example — Run an Indicator

```go
package main

import (
    "fmt"
    "log"
    "time"

    "github.com/ch99q/tvcli/pkg/tradingview"
    "github.com/ch99q/tvcli/pkg/runner"
)

func main() {
    // 1. Create WS client
    client := tradingview.NewClient(
        tradingview.WithToken("your-session-cookie"),
        tradingview.WithSignature("your-signature-cookie"),
    )
    if err := client.Connect(); err != nil {
        log.Fatal(err)
    }
    defer client.Close()

    // 2. Load indicator metadata (via Pine Facade HTTP API)
    // indResult, _ := pineClient.Get(pineID, "last", cookieHeader)

    // 3. Create chart + study
    ch := tradingview.NewChartSession(client)
    ch.SetMarket("OANDA:XAUUSD", map[string]any{"timeframe": "5m", "range": 500})
    ch.WaitForSymbol(15 * time.Second)

    indicator := tradingview.NewPineIndicator(map[string]any{
        "pineId":  pineID,
        "script":  sourceCode,
        "metaInfo": metaInfo,
    })
    study := ch.Study(indicator)

    // 4. Wait for data
    // ... (use study.OnUpdate / study.OnError callbacks)

    // 5. Parse results
    result := runner.ParseOutput(study.Periods(), study.Graphic(), study.StrategyReport(), "5m", pineID, indicator.Schema)
    fmt.Println(runner.FormatResults(result, false))
}
```

### Quick Example — Persistent Connection (Loop)

```go
pr := runner.NewPersistentRunner(
    []tradingview.ClientOption{
        tradingview.WithToken(session),
        tradingview.WithSignature(sig),
    },
    false, // debug
)
defer pr.Close()

// Run repeatedly — WS stays open, only chart sessions cycle
for i := 0; i < 10; i++ {
    result, err := pr.Run(runner.RunOnceOptions{
        PineID:     pineID,
        Symbol:     "OANDA:XAUUSD",
        Timeframe:  "5m",
        Bars:       500,
        Indicator:  indicator,
        CalcTimeout: 60 * time.Second,
    })
    if err != nil {
        log.Printf("run %d error: %v", i, err)
        continue
    }
    fmt.Printf("Run %d: %d periods\n", i, len(result.NumericalData.Fields))
    time.Sleep(30 * time.Second)
}
```

### Available Packages

| Package | Import Path | Key Types |
|---------|-------------|-----------|
| `tradingview` | `github.com/ch99q/tvcli/pkg/tradingview` | `Client`, `ChartSession`, `ChartStudy`, `PineIndicator` |
| `runner` | `github.com/ch99q/tvcli/pkg/runner` | `PersistentRunner`, `RunResult`, `ParseOutput()` |
| `pinefacade` | `github.com/ch99q/tvcli/pkg/pinefacade` | `Client`, `Compile()`, `Get()`, `SearchPublicScripts()` |
| `extract` | `github.com/ch99q/tvcli/pkg/extract` | `Extract()`, `Signals` |
| `schema` | `github.com/ch99q/tvcli/pkg/schema` | `PineSchema`, `ScriptSchema` |

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
