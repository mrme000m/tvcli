# Layer 0: Essentials — Pine Script in 5 Minutes

> **Read this first.** Covers the absolute minimum to write, compile, and run a Pine Script.

---

## What Is Pine Script?

A domain-specific language for TradingView charts. Runs on TradingView's servers — **not** in your browser. Two execution modes:

| Mode | Use Case | Persistence |
|------|----------|-------------|
| **Indicator** | Visual analysis, signals, levels | Plots on chart, no trades |
| **Strategy** | Backtesting, forward-testing, paper trading | Simulated trades, performance report |

---

## Minimal Valid Script

```pine
//@version=6
indicator("My First Script", overlay=true)
plot(close, "Close Price")
```

Save as `first.pine` → `tvcli compile first.pine` → `tvcli create first.pine`

---

## Script Structure (Required Order)

```pine
//@version=6                    // 1. Version (MUST be first line)
indicator("Name", overlay=true) // 2. Declaration (indicator OR strategy)
// --- optional: inputs, variables, functions ---
plot(close)                     // 3. At least one plot/call
```

**Version 6** is the current Pine Script® release; the examples in this skill use `//@version=6`. Version 5 scripts remain fully supported and compile unchanged. Version 4 still works but lacks modern features (namespaces, types, matrices, etc.).

---

## Two Ways to Run

| Method | Command | When |
|--------|---------|------|
| **HTTP Compile** | `tvcli compile file.pine` | Syntax check only, no chart data |
| **WebSocket Run** | `tvcli run PINE_ID --symbol X --tf 5m` | Live data, real indicator values |

The HTTP compile (`/translate_light`) validates syntax. The WebSocket run executes on real bars — catches runtime errors (division by zero, array out of bounds, etc.) that compile misses.

---

## Agent Quick-Start

If you're an agent (Pi, OpenCode, Claude, etc.) writing Pine Script via `tvcli`, follow this checklist:

### Before Writing
1. **Set auth**: `tvcli check-auth --json` must return `"valid": true` with fresh SESSION/SIGNATURE cookies
2. **Use v6**: Start every script with `//@version=6` — v5 scripts compile but lack modern features
3. **Know the tier**: Free tier limits: 2 studies, 180d bars, 20s calc timeout. Upgrade for more.

### Template
```pine
//@version=6
indicator("Agent Script", overlay=true)

// Inputs with titles (required)
fastLen = input.int(9, "Fast EMA Length", minval=1, title="Fast Length")
slowLen = input.int(21, "Slow EMA Length", minval=1, title="Slow Length")
src = input.source(close, "Source")

// Calculations
emaFast = ta.ema(src, fastLen)
emaSlow = ta.ema(src, slowLen)

// Plots with titles
plot(emaFast, "Fast EMA", color=color.green)
plot(emaSlow, "Slow EMA", color=color.red)

// Strategy (optional — for backtesting)
//@version=6
// strategy("Agent Strategy", overlay=true, initial_capital=10000, commission_type=strategy.commission.percent, commission_value=0.00)
```

### Common Agent Pitfalls
- **Missing `//@version=6`** → compile error or v5-only features
- **`map.from()`** → doesn't exist; use `map.new()` + `map.put()`
- **`array.push` in loop** → timeout from O(n²) growth; use circular buffer or `ta.sma()`
- **`request.security` without `lookahead_off`** → lookahead bias in backtest results
- **`close[300]` without `max_bars_back`** → runtime error; set `max_bars_back=5000` or restructure
- **`varip` on historical-only bars** → value stays `na`; use `var` for historical, `varip` for realtime
- **Drawing > limits** → 500 labels/boxes, 100 polylines auto-deleted; track counts

### tvcli Commands Agent Agents Use
```bash
# 1. Validate syntax
tvcli compile script.pine

# 2. Check auth
tvcli check-auth --json

# 3. Run with signals
tvcli run PID --symbol OANDA:XAUUSD --tf 15m --signals --json

# 4. If auth fails, re-extract cookies from browser DevTools → Application → Cookies → tradingview.com
#    Update .env: SESSION=..., SIGNATURE=...

# 5. Clean stale sessions
tvcli clean --iterations 10 --delay 1000
```

### Quick Reference
- **Pine ID format**: `USER;abc123` (from `tvcli create`)
- **Symbol format**: `EXCHANGE:SYMBOL` (e.g., `OANDA:XAUUSD`, `BINANCE:BTCUSDT`)
- **Timeframe**: `5`, `60`, `D`, `W`, `M`
- **Calc timeout**: Tier-dependent (Free: 20s, Ultimate: 100s)

---

## Pine ID Format

Every saved script gets a **Pine ID**:
```
USER;abc123def456    // Your private scripts
PUB;xyz789           // Public scripts
STD;MA               // Built-in (e.g., STD;MA = Moving Average)
INDIC;name           // Community indicators
```

- `tvcli create` returns the Pine ID
- `tvcli list` shows your tracked scripts with local numeric IDs (#1, #2...)
- Use either `#N` or `USER;...` with `tvcli push/run/pull`

---

## Inputs = Settings Panel

```pine
//@version=6
indicator("EMA Crossover", overlay=true)
lenFast = input.int(9, "Fast EMA Length", minval=1)
lenSlow = input.int(21, "Slow EMA Length", minval=1)
src = input.source(close, "Source")
emaFast = ta.ema(src, lenFast)
emaSlow = ta.ema(src, lenSlow)
plot(emaFast, "Fast", color=color.green)
plot(emaSlow, "Slow", color=color.red)
```

These appear in the **Settings → Inputs** tab on TradingView. `tvcli run --signals` extracts values programmatically.

---

## Overlay vs Pane

| Declaration | Where It Appears |
|-------------|------------------|
| `indicator("Name", overlay=true)` | Main chart pane (price scale) |
| `indicator("Name", overlay=false)` | Separate pane below (own scale) |
| `strategy("Name", overlay=true)` | Main pane + trades |

---

## Essential Functions Cheatsheet

| Category | Functions |
|----------|-----------|
| **Moving Averages** | `ta.sma`, `ta.ema`, `ta.rma`, `ta.wma`, `ta.vwma` |
| **Oscillators** | `ta.rsi`, `ta.stoch`, `ta.macd`, `ta.cci` |
| **Volatility** | `ta.atr`, `ta.bb` (Bollinger Bands) |
| **Volume** | `ta.volume`, `ta.obv`, `ta.mfi` |
| **Math** | `math.min`, `math.max`, `math.abs`, `math.round`, `math.sqrt` |
| **Logic** | `ta.cross`, `ta.crossover`, `ta.crossunder`, `ta.rising`, `ta.falling` |
| **Time** | `timeframe.period`, `timeframe.isintraday`, `session.ismarket` |
| **Plotting** | `plot`, `plotshape`, `plotchar`, `plotarrow`, `hline`, `fill` |

---

## Next Layer

→ **Layer 1: Language Core** → `core/language.md`

Covers: variables, types, control flow, functions, namespaces, and the execution model (bar-by-bar).