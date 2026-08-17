# Layer 8: WebSocket Runtime — Real-Time Execution

> **Prerequisite:** Layer 7 (Pine Facade). The WebSocket API for live indicator/strategy execution on real data.

---

## Connection

```
Endpoint: wss://data.tradingview.com/socket.io/websocket (free)
          wss://prodata.tradingview.com/socket.io/websocket (premium)

Protocol: ~m~<length>~m~<json> framing
Auth: Session cookie + auth_token from chart page
```

**tvcli connection** (`pkg/tradingview/client.go:Connect`):
1. Dial WebSocket with Origin/UA headers + cookies
2. Fetch `auth_token` from `https://www.tradingview.com/chart/` (requires cookies)
3. Send `set_auth_token` message
4. Start read loop

---

## Protocol Framing (`pkg/tradingview/protocol.go`)

**Wire format:** `~m~<len>~m~<json>`

```
~m~45~m~{"m":"chart_create_session","p":["cs_abc123"]}
~m~12~m~{"m":"ping","p":[12345]}
```

**Parser** (`Protocol.ParseWSPacket`): Splits by `~m~`, reads length, extracts JSON.
**Formatter** (`Protocol.FormatWSPacket`): JSON marshals, wraps in `~m~<len>~m~`.

---

## Session Classes (Client → Session → Chart → Study)

```
WSClient (connection)
  └── ChartSession (chart_create_session)
        ├── resolve_symbol (symbol + timeframe)
        ├── create_series / modify_series (price data)
        └── ChartStudy (create_study)
              ├── PineIndicator (custom script) or BuiltinIndicator
              └── onData → periods, graphics, strategyReport
```

---

## Message Flow (Run Command)

```go
// tvcli run flow (internal/cmd/run.go)
1. Connect WS
2. chart_create_session → cs_xxx
3. resolve_symbol(cs_xxx, ser_1, {symbol: "OANDA:XAUUSD", adjustment: "splits"})
4. create_series(cs_xxx, "$prices", "s1", ser_1, "15", 500)  // timeframe, bars
5. Wait for symbol_resolved
6. create_study(cs_xxx, st_xxx, "st1", "$prices", "Script@tv-scripting-101!", inputs)
7. Wait for study_completed
8. Collect periods + graphics + strategyReport
9. Clean up: remove_study → chart_delete_session → close WS
```

---

## Key WebSocket Messages

### Client → Server
| Message | Params | Purpose |
|---------|--------|---------|
| `chart_create_session` | [sessionId] | Create chart |
| `resolve_symbol` | [chartId, seriesId, "=JSON"] | Request symbol data |
| `create_series` | [chartId, "$prices", "s1", seriesId, tf, range] | Create price series |
| `modify_series` | [chartId, "$prices", "s1", seriesId, tf, ""] | Change timeframe |
| `create_study` | [chartId, studyId, "st1", "$prices", type, inputs] | Add indicator |
| `remove_study` | [chartId, studyId] | Remove indicator |
| `chart_delete_session` | [chartId] | Delete chart |
| `set_auth_token` | [token] | Authenticate |

### Server → Client
| Message | Data | Purpose |
|---------|------|---------|
| `symbol_resolved` | [chartId, seriesId, symbolInfo] | Symbol metadata |
| `timescale_update` / `du` | [chartId, {s1: priceData, st_xxx: studyData}] | Bar updates |
| `study_completed` | [chartId, studyId] | Indicator ready |
| `study_error` | [chartId, studyId, error, details] | Indicator failed |
| `protocol_error` | [errorDetails] | Protocol violation |

---

## Study Data Structure (`study.go:processStudyData`)

```json
{
  "st": [                          // Period data (time-series)
    {"v": [timestamp, plot0, plot1, plot2...]},
    {"v": [timestamp, plot0, plot1, plot2...]}
  ],
  "ns": {                          // Namespace: graphics + strategy report
    "d": "{\"graphicsCmds\":{...},\"report\":{...}}",  // Inline JSON string
    "dCompressed": "base64...",     // Compressed (zlib/zip)
    "dataCompressed": "base64..."   // Alternative compressed field
  }
}
```

**tvcli parses:**
- `st` → `Periods()` with named plots (from metaInfo)
- `ns.d` / `dCompressed` → `Graphic()` (drawings) + `StrategyReport()` (trades, equity)

---

## Authentication & Study Limits

**Free tier anonymous:** 0 studies allowed → every `create_study` fails with "study limit"

**Authenticated (SESSION+SIGNATURE):**
| Tier | Charts | Indicators/Chart | Connections |
|------|--------|------------------|-------------|
| Free | 1 | 2 | 2 |
| Essential | 2 | 5 | 10 |
| Plus | 4 | 10 | 20 |
| Premium | 8 | 25 | 50 |
| Ultimate | 16 | 50 | 200 |

**tvcli cleanup** (`chart.go:RemoveAllStudies`, `Delete`):
- Spaces `remove_study` 100ms apart
- Waits 300ms after all removals
- Sends `chart_delete_session`
- Waits 500ms before closing WS

**Without cleanup:** Stale sessions consume study slots → next run fails.

---

## PineIndicator Inputs (`indicator.go:PineIndicator.GetInputs`)

```go
// Builds input map for create_study
inputs := map[string]any{
    "in_0": map[string]any{"v": 14, "f": false, "t": "int"},
    "in_1": map[string]any{"v": "close", "f": false, "t": "source"},
    "pineId": "USER;abc123",
    "pineVersion": "1.0",
    "text": "<compiled IL blob from Pine Facade>"
}

// ⚠ The "text" field is the COMPILED IL blob fetched by LoadIndicator
// from Pine Facade's Get/translate endpoint — NOT the raw Pine source.
// Passing raw Pine source as "text" causes server-side compilation error:
// "line 1:12 no viable alternative at character '\n'"
// Pine Facade compiles the source (via /save/new or /save/next) and stores
// the IL blob; Get/translate returns it. See Layer 7 (Pine Facade) for details.
```

**Color inputs:** Value = input index (0, 1, 2...) not color string.

**Source inputs:** Value = "close", "open", "hl2", etc.

---

## tvcli Run Options

```bash
tvcli run USER;abc123 \
    --symbol OANDA:XAUUSD \
    --tf 15m \
    --bars 500 \
    --json \
    --signals \
    --raw \
    --raw-out dump.json \
    --settle 1500 \
    --force-cleanup \
    --persistent \
    --loop 30s
```

| Flag | Purpose |
|------|---------|
| `--symbol` | Exchange:Symbol (auto-mapped: XAUUSD → OANDA:XAUUSD) |
| `--tf` | Timeframe (normalized: 15m → 15) |
| `--bars` | Historical bars (capped by tier) |
| `--json` | JSON output |
| `--signals` | Extract crossover/level signals |
| `--raw` | Full periods + graphics + strategyReport |
| `--settle` | Wait ms after first data for graphics/backfill |
| `--force-cleanup` | Aggressive study slot cleanup on limit error |
| `--persistent` | Keep WS open across runs |
| `--loop` | Re-run periodically (implies --persistent) |

---

## Signal Extraction (`pkg/schema/semantic.go`)

tvcli analyzes periods for:
- **Crossovers:** Plot A crosses Plot B
- **Level crosses:** Plot crosses fixed level (0, 50, 70...)
- **Slope changes:** Rising/falling transitions
- **Pattern matches:** From skill parsers (SMC, ICT, etc.)

**Output:**
```json
{
  "signals": [
    {"type": "crossover", "plot": "EMA Fast", "crossed": "EMA Slow", "direction": "up", "time": 1704067200000, "price": 2034.5},
    {"type": "level_cross", "plot": "RSI", "level": 70, "direction": "down", "time": 1704067200000}
  ]
}
```

---

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| "study limit" | Too many indicators for tier | `tvcli clean`, wait, or upgrade tier |
| "symbol not found" | Wrong symbol format | Use EXCHANGE:SYMBOL (OANDA:XAUUSD) |
| "auth failed" | Expired cookies | Re-extract SESSION/SIGNATURE |
| "timeout waiting for symbol" | Symbol resolves slowly | Increase timeout, check symbol |
| "no data" | Market closed / wrong timeframe | Check session hours, use valid TF |

---

## Next Layer

→ **Layer 9: Go tvcli Integration** → `integration/go-tvcli.md`

Covers: Using the compiled binary, command reference, automation patterns, and HTTP server mode.