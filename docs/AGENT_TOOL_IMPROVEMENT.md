# Analysis: tvcli as a Tool for Arbitrary Pine Script Execution by Agents

## Current Architecture Summary

The codebase is a Go CLI (~16,500 lines) that acts as an unofficial TradingView API client:

1. **pinefacade** (`pkg/pinefacade/`) — HTTP client for Pine Facade (fetch script source/metaInfo, compile, save, search)
2. **tradingview** (`pkg/tradingview/`) — WebSocket client implementing TradingView's Socket.IO protocol (chart sessions, studies)
3. **schema** (`pkg/schema/`) — Parses metaInfo into a structured PineSchema (plots, inputs, styles, palettes)
4. **pipeline** (`pkg/pipeline/`) — Dynamic parser + signal extractor that converts raw study output → typed bars → signals/events/levels
5. **runner** (`pkg/runner/`) — Orchestrates runs (one-shot, persistent, loop, multi-run/sweep)
6. **service** (`internal/service/`) — Use-case layer wiring pinefacade + tradingview + runner
7. **skill** (`internal/skill/`) — Framework wrapping specific Pine indicators as typed CLI commands with custom parsers
8. **cmd** (`internal/cmd/`) — CLI commands (run, create, push, pull, compile, fetch, sync, search, skills, etc.)

### Current Execution Flow

```
Agent/CLI → `tv run "PUB;<id>" --symbol X --tf 5m --bars 500 --signals --agent --json`
  → pinefacade.Get(pineID) → fetch script source + metaInfo
  → tradingview.WSClient.Connect() → WebSocket to data.tradingview.com
  → ChartSession.SetMarket(symbol, tf, bars) → resolve_symbol + create_series
  → ChartSession.Study(indicator) → create_study with script source + inputs
  → Study.OnUpdate/OnReady/OnError → collect periods + graphic + strategyReport
  → pipeline.Parse(periods, schema) → TypedBars with semantic names
  → pipeline.ExtractWithSchema() → Signals {bias, events, levels, report}
  → JSON output for agent consumption
```

## What Works Well

1. **Generic `run` command already supports arbitrary Pine IDs** — Any `PUB;xxx` or `USER;xxx` script can be run.
2. **`--signals` flag enables schema-guided extraction** — Works on any script where metaInfo has plots.
3. **`--agent --json` produces structured agent-ready output** — With bias, confidence, events, levels, strategy metrics.
4. **`--schema` flag introspects any script** — Shows plots, inputs, styles without running.
5. **`--raw` / `--raw-out` dumps raw data** — Useful for debugging and custom analysis.
6. **Tier-aware limits** — Bars, indicators, timeouts are capped per TradingView subscription.
7. **Study cleanup** — Aggressive retry + cleanup prevents study-limit leaks.
8. **PersistentRunner** — Long-lived WS connection for repeated runs (used in `--persistent`/`--loop`).

## Key Gaps for Arbitrary Agent-Driven Pine Execution

### 1. No Ad-Hoc Script Execution (Must Have a Pine ID)

**Problem:** The `run` command requires a pre-published Pine ID. An agent cannot submit arbitrary
Pine Script source code for execution. It must first `create` (publish) the script to TradingView,
get a Pine ID, then `run` it. This is slow, pollutes the user's script library, and requires
write auth.

**Solution:** Add an `eval` command that compiles + runs ad-hoc Pine source:

```bash
tv eval --script-file analysis.pine --symbol BINANCE:BTCUSDT --tf 1h --bars 500 --json
# or
tv eval --script '//@version=5 indicator("Agent RSI", overlay=false) plot(ta.rsi(close, 14))' --symbol BTCUSDT --json
```

Implementation: Use `pinefacade.Compile()` to get the IL/metaInfo, then if no Pine ID exists,
use `pinefacade.SaveNew()` to create a temporary script, run it, then `pinefacade.Delete()`
to clean up. Or investigate if TradingView supports running unpublished scripts directly
via the WS `create_study` with raw source (the current code already sends `script` as the
`text` field in study inputs — this might work without a Pine ID).

**Key insight:** The `PineIndicator.GetInputs()` method already puts `ind.Script` into the
`text` field of the study creation payload. The `create_study` WS message sends this to
TradingView's server. The server may accept raw script text without a Pine ID — the Pine ID
field (`pineId`) may be optional or accept a synthetic value. This needs testing.

### 2. No HTTP API / Server Mode for Agent Integration

**Problem:** The tool is CLI-only. Agents must shell out to `tvcli` and parse stdout. This is
fragile, slow (each run starts a new WS connection), and doesn't support concurrent analysis.

**Solution:** Add an HTTP server mode:

```bash
tv server --port 8080
```

Endpoints:
- `POST /run` — Run a Pine ID or ad-hoc source on a symbol/tf/bars
- `POST /eval` — Compile + run ad-hoc Pine source
- `GET /schema/:pineId` — Get script schema
- `GET /fetch/:symbol` — Get OHLCV bars
- `POST /search` — Search public scripts
- `WS /stream` — Real-time signal streaming

This lets agents make HTTP calls instead of shelling out, and the server maintains a
persistent WS connection to TradingView (using the existing `PersistentRunner`).

**Implementation path:** Go's `net/http` stdlib is sufficient. The server wraps the existing
`service.RunScript()` and `service.LoadIndicator()` functions. The `PersistentRunner` from
`pkg/runner/persistent.go` is reused for connection management.

### 3. No Symbol Discovery / Market Catalog

**Problem:** Agents must know the exact `EXCHANGE:SYMBOL` format. There's no way to search for
symbols or discover available markets.

**Solution:** Add a `symbols` command or API endpoint:
```bash
tv symbols --query bitcoin --json
tv symbols --exchange BINANCE --json
```

TradingView has a symbol search API at `https://symbol-search.tradingview.com/symbol-search`
that can be queried with a text query. Add an HTTP client for this in `pkg/pinefacade/` or
a new `pkg/marketdata/` package.

### 4. No Multi-Market / Multi-Timeframe Analysis

**Problem:** Each run targets one symbol + one timeframe. Agents that want to analyze multiple
markets or timeframes must make multiple CLI calls.

**Solution:** Support batch runs:
```bash
tv run "PUB;xxx" --symbols BTCUSDT,ETHUSDT,SOLUSDT --tfs 5m,1h,4h --json
```

Or via API:
```json
POST /batch
{
  "pineId": "PUB;xxx",
  "targets": [
    {"symbol": "BINANCE:BTCUSDT", "tf": "1h", "bars": 500},
    {"symbol": "BINANCE:ETHUSDT", "tf": "4h", "bars": 500}
  ]
}
```

The server runs each target sequentially (TradingView study limits prevent true parallelism
on a single connection) but returns a combined result.

### 5. Signal Extraction Is Heuristic, Not Semantic

**Problem:** The `pipeline.ExtractWithSchema()` uses statistical heuristics to classify plots
(bool-like → signal, large values → price, etc.). This misclassifies many indicators.
The `classifyFromMetadata` in `schema/semantic.go` uses keyword matching on plot names, but plot names
from metaInfo are often generic ("plot_0", "Line 1", etc.).

**Solution:** 
- Let agents provide a **signal mapping** alongside the script: "plot_0 = rsi, plot_1 = signal"
- Support Pine Script `//@agent` annotations in comments that declare plot semantics:
  ```pine
  //@agent plot RSI = oscillator
  //@agent plot BuySignal = signal:buy
  //@agent plot SellSignal = signal:sell
  //@agent plot UpperBand = band:resistance
  ```
- Parse these annotations from the script source and pass them to the extractor.
- The parser would be a simple regex scan of the script source before compilation.
- The annotations override the heuristic classification in `classifyFromSchema()`.

### 6. No Output Schema Declaration for Custom Scripts

**Problem:** When an agent writes a custom Pine Script, it has no way to declare what the
output means. The generic extractor guesses, often wrong.

**Solution:** Support a sidecar YAML/JSON schema file:
```yaml
# analysis.schema.yaml
plots:
  - index: 0
    name: rsi
    semantic: oscillator
  - index: 1  
    name: buy_signal
    semantic: signal
    direction: long
  - index: 2
    name: sell_signal
    semantic: signal
    direction: short
levels:
  - plot: 0
    value: 70
    kind: overbought
  - plot: 0
    value: 30
    kind: oversold
```

```bash
tv eval --script-file analysis.pine --schema-file analysis.schema.yaml --json
```

The sidecar schema is merged with the metaInfo-derived `PineSchema` to override plot
classifications. This is a new input to `pipeline.ExtractWithSchema()`.

### 7. No Caching / State Persistence Between Runs

**Problem:** Each run reconnects, creates a chart, loads the symbol, runs the study, tears down.
This takes 3-5 seconds. Agents making many calls waste time.

**Solution:** The `PersistentRunner` exists but is only used in `--persistent`/`--loop` mode.
The HTTP server mode should use it by default. Also cache:
- Script source/metaInfo (already cached in metadb, but only for tracked scripts)
- Symbol info (provisional symbols, tick size, etc.)
- OHLCV data (already done in `fetch`/`sync`, but not shared with `run`)

**Implementation:** Add a TTL cache layer in the `service` package:
```go
type Cache struct {
    scripts   map[string]*ScriptCache  // pineID → {source, metaInfo, expiry}
    symbols   map[string]*SymbolCache  // symbol → {info, bars, expiry}
    mu        sync.RWMutex
}
```

### 8. No Concurrency Control for Multiple Agents

**Problem:** TradingView enforces tier limits (max charts, indicators, connections). Multiple
agents running simultaneously will hit these limits.

**Solution:** 
- The HTTP server should have a request queue with configurable concurrency.
- Track active chart sessions and reject/queue requests when at the limit.
- Use a connection pool that reuses WS connections.

**Implementation:** A semaphore-based limiter in the server:
```go
type Limiter struct {
    sem chan struct{}
}

func (l *Limiter) Acquire() { l.sem <- struct{}{} }
func (l *Limiter) Release() { <-l.sem }
```
Configured based on `config.GetTierLimits().MaxCharts`.

### 9. No Timeframe Aggregation / Multi-Timeframe Confluence

**Problem:** Agents must manually run multiple timeframes and correlate results.

**Solution:** Built-in MTF confluence:
```bash
tv run "PUB;xxx" --symbol BTCUSDT --mtf 5m,1h,4h --confluence --json
```
Output includes confluence score: "3/4 timeframes bullish → high confluence"

**Implementation:** Run the script on each timeframe sequentially, collect `Signals` for each,
then aggregate:
```go
type MTFResult struct {
    Timeframes map[string]*pipeline.Signals
    Confluence ConfluenceScore
}
type ConfluenceScore struct {
    BullCount int
    BearCount int
    Score     float64
    Direction  string
}
```

### 10. No Pine Script Templating for Agents

**Problem:** Agents that want custom analysis must write full Pine Scripts from scratch.

**Solution:** Provide a template library:
```bash
tv eval --template rsi-divergence --symbol BTCUSDT --tf 1h --params length=14
tv eval --template ema-cross --symbol BTCUSDT --tf 4h --params fast=9,slow=21
```

Templates are parameterized Pine Scripts with pre-declared agent schemas stored in
a `templates/` directory. Each template is a `.pine` file with `{{.Param}}` placeholders
and a `.schema.yaml` sidecar.

### 11. Missing Error Recovery / Retry Intelligence

**Problem:** Study errors, timeouts, and rate limits are handled with simple retry loops.
Agents get opaque errors.

**Solution:** Structured error responses with actionable codes:
```json
{
  "error": "study_limit_exceeded",
  "message": "Maximum 2 indicators reached on free tier",
  "retryAfter": 5,
  "suggestion": "Close indicators in TradingView web UI or upgrade tier"
}
```

**Implementation:** A typed error hierarchy in the `service` package:
```go
type AgentError struct {
    Code       string `json:"error"`
    Message    string `json:"message"`
    RetryAfter int    `json:"retryAfter,omitempty"`
    Suggestion string `json:"suggestion,omitempty"`
}
```

### 12. No Real-Time Streaming Output

**Problem:** The `--loop` mode re-runs the full pipeline each cycle. There's no way to stream
incremental updates (new bar, signal change) to an agent.

**Solution:** WebSocket streaming in server mode:
```json
WS /stream
→ {"subscribe": "BTCUSDT", "tf": "5m", "pineId": "PUB;xxx"}
← {"type": "bar", "data": {"time": 1234567890, "close": 95000}}
← {"type": "signal", "data": {"kind": "buy", "field": "rsi_cross", "time": 1234567890}}
```

**Implementation:** The `ChartSession.OnUpdate()` callback already fires on each `timescale_update`.
Wire it to a WebSocket push channel instead of the current settle-and-snapshot approach.

## Recommended Improvement Priority

### Phase 1: Core Agent Tool (Immediate)
1. **`eval` command** — Run ad-hoc Pine source without pre-publishing
2. **Agent schema annotations** — `//@agent` comments for plot semantics  
3. **HTTP server mode** — REST API for agent integration
4. **Structured error codes** — Actionable errors for agents

### Phase 2: Market Intelligence (Short-term)
5. **Symbol discovery** — Search/browse available markets
6. **Batch/multi-market runs** — Analyze many symbols at once
7. **MTF confluence** — Multi-timeframe analysis with confluence scoring
8. **OHLCV caching** — Share price data between runs

### Phase 3: Advanced Agent Features (Medium-term)
9. **Template library** — Pre-built parameterized analysis scripts
10. **Real-time streaming** — WebSocket push of signals/updates
11. **Connection pool** — Concurrent agent requests with queue management
12. **Sidecar schema files** — Explicit output declaration for custom scripts

### Phase 4: Polish (Long-term)
13. **Pine v5/v6 feature support** — Request.security, arrays, matrices
14. **Agent skill generation** — Auto-generate skill definitions from any script
15. **Backtesting API** — Run strategies with historical data + report
16. **Alert integration** — Convert signals to TradingView alerts

## Key Files to Modify

| Improvement | Files | New Files |
|------------|-------|-----------|
| `eval` command | `internal/cmd/eval.go` (new), `internal/cmd/shared.go`, `cmd/tvcli/main.go` | `internal/cmd/eval.go` |
| Agent annotations | `pkg/pipeline/annotations.go` (new), `pkg/pipeline/extract_schema.go` | `pkg/pipeline/annotations.go` |
| HTTP server | `internal/server/server.go` (new), `cmd/tvcli/main.go` | `internal/server/`, `cmd/tvcli-server/main.go` |
| Structured errors | `internal/service/errors.go` (new), `internal/service/run.go` | `internal/service/errors.go` |
| Symbol discovery | `pkg/marketdata/search.go` (new), `internal/cmd/symbols.go` (new) | `pkg/marketdata/` |
| Batch runs | `internal/cmd/run.go`, `internal/service/batch.go` (new) | `internal/service/batch.go` |
| MTF confluence | `internal/service/mtf.go` (new), `pkg/runner/mtf.go` (new) | `internal/service/mtf.go` |
| Caching | `internal/service/cache.go` (new) | `internal/service/cache.go` |
| Concurrency | `internal/server/limiter.go` (new) | `internal/server/limiter.go` |
| Templates | `templates/` (new dir), `internal/cmd/eval.go` | `templates/*.pine` |
| Streaming | `internal/server/stream.go` (new), `pkg/tradingview/chart.go` | `internal/server/stream.go` |
| Sidecar schema | `pkg/schema/sidecar.go` (new), `pkg/pipeline/extract_schema.go` | `pkg/schema/sidecar.go` |
