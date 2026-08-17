# Layer 3: Execution Model — Historical vs Realtime, var/varip, Performance (Extended)

> **Prerequisite:** Layer 2 (Type System). Understanding how Pine Script actually runs on TradingView servers.

---

## Two Execution Phases (Expanded)

| Phase | Bars | Data | Purpose | Agent Pattern |
|-------|------|------|---------|---------------|
| **Historical** | All past bars (up to max_bars_back) | Known OHLCV | Build indicator state, warm up, backtest | `tvcli run --bars 500` — load warm-up data |
| **Realtime** | Current forming bar + future | Live ticks | Update with live data, emit signals | `tvcli run` with WebSocket — live bar updates |
| **Strategy Backtest** | Full history with simulated trades | OHLCV + order fill simulation | Equity curve, trade analysis | `calc_on_every_tick` strategies |

**Same script runs both phases.** No separate "backtest" mode for indicators. The `barstate.ishistory` flag distinguishes.

---

## `var` vs `varip` — Persistence Control (Expanded)

```pine
// var: initialized ONCE on first historical bar, persists across all bars
var int historicalCounter = 0
historicalCounter := historicalCounter + 1  // Increments every bar (historical + realtime)

// varip: like var, but ALSO updates on every realtime TICK (intrabar)
varip float intrabarHigh = high
varip float intrabarLow = low
varip float intrabarVolume = volume
intrabarHigh := math.max(nz(intrabarHigh, high), high)
intrabarLow  := math.min(nz(intrabarLow, low), low)
intrabarVolume := nz(intrabarVolume, volume) + volume
```

| Keyword | Historical Init | Historical Update | Realtime Bar Update | Realtime Tick Update |
|---------|-----------------|-------------------|---------------------|----------------------|
| `var` | ✓ (first bar only) | ✓ every bar | ✓ | ✗ |
| `varip` | ✓ (first bar only) | ✓ every bar | ✓ | ✓ |

**Use `varip` for:**
- Intrabar high/low tracking
- Tick-level volume accumulation
- Real-time order flow / liquidity detection
- Session POC tracking across ticks

**Use `var` for:**
- State machines (signal flags, trend changes)
- Order block detection
- Fair value gap (FVG) tracking
- Most indicator logic

**Critical pattern:** Initialize `varip` on the first realtime bar only:

```pine
varip float myTickVar = na
if barstate.isrealtime and bar_index == 0
    myTickVar := 0
// Now safely update on every tick
myTickVar := myTickVar + 1
```

---

## `max_bars_back` — History Depth & Agent Workflow

```pine
//@version=6
indicator("Deep Lookback", max_bars_back=3000)
```

### Agent Control via `tvcli`

| Scenario | Command | Explanation |
|----------|---------|-------------|
| **Default auto-sizing** | `tvcli run PID` | Pine auto-sizes from referenced series (e.g., `close[200]` → 200) |
| **Explicit override** | `tvcli run PID --bars 3000` | Force 3000 bars regardless of script params |
| **Conservative tier-aware** | `TV_TIER=free tvcli run PID --bars 180` | Free tier: 180-day history cap |
| **Full history (Ultimate)** | `TV_TIER=ultimate tvcli run PID` | Unlimited bars, but `max_bars_back` hard cap at 5000 |

### Auto-Detection Limitations

Pine auto-sizes `max_bars_back` from **static** history references only:
- `ta.sma(close, 200)` → infers 200
- `close[100]` → infers 100

**Dynamic lookbacks may be missed:**
- `array` loops that grow per bar
- `request.security` with variable timeframes
- `bar_index` dependent array sizes

**Always explicit for agent scripts:**
```pine
// ✅ Explicit — works across all tiers
indicator("Safe", max_bars_back=5000)

// ⚠️ Risky — may underflow on complex scripts
indicator("Auto")  // Relies on Pine's auto-detection
```

---

## `force_overlay` — Scale Control

```pine
//@version=6
indicator("Volume Profile", overlay=false, force_overlay=true)  // Force main pane
```

### When `force_overlay=true` is Useful

1. **Multi-panel scripts** — Ensure price scale always visible
2. **Strategy backtest alignment** — Strategy overlay must match main chart scale
3. **tvcli signal extraction** — Consistent Y-axis across runs
4. **Agent comparison workflows** — Same scale for all script outputs

### Interactions with `overlay=false`

- `overlay=false` creates a separate pane
- `force_overlay=true` **overrides** the user's pane choice and forces main pane
- Use one or the other, not both for the same intent

---

## `barstate.*` — Execution Context (Expanded Reference)

### Full `barstate` Flag Table

| Flag | Value | When True | Typical Use |
|------|-------|-----------|-------------|
| `barstate.ishistory` | `true`/`false` | Any historical bar (including last warm-up bar) | Warm-up calculations, init `var` |
| `barstate.isrealtime` | `true`/`false` | Current forming bar (open) | `varip` tracking, intrabar state |
| `barstate.islast` | `true`/`false` | Last bar of the chart | Final signal emission, cleanup |
| `barstate.isconfirmed` | `true`/`false` | Realtime bar has closed | Trigger alerts, trade entries, save state |
| `barstate.isreal` | `true`/`false` | `ishistory or isrealtime` | General branch routing |

### Critical Patterns

```pine
// ❌ Wrong: Using islast for per-bar signals
// islast is true on every realtime bar execution, not just the final bar

// ✅ Correct: Use isconfirmed for per-bar signals
if barstate.isconfirmed
    // Runs once per closed bar (historical + realtime confirmed)
    plotshape(crossover, "Signal", shape.triangleup, location.belowbar)

// ✅ Correct: islast + isconfirmed for end-of-bar actions
if barstate.islast and barstate.isconfirmed
    // Runs ONLY on the last closed bar of the chart
    // Ideal: save state, emit final alert, cleanup drawings

// ✅ Correct: varip tracking always runs (no islast/isconfirmed guard needed)
varip float sessionHigh = na
if session.ismarket
    sessionHigh := math.max(nz(sessionHigh, high), high)
```

---

## Intrabar Updates (`varip` + `barstate`) — Deep Dive

### Pattern: Tick-Accumulated Volume POC

```pine
// Track POC (Point of Control) across realtime ticks
varip float priceAccumulation[100] = array.new_float(0)
varip int tickCount = 0

if session.ismarket
    // Accumulate price levels
    priceAccumulation[tickCount % 100] := high + low / 2  // Typical price
    tickCount := tickCount + 1

// Compute POC on every realtime bar (islast + isconfirmed context)
if barstate.islast and barstate.isconfirmed
    // Find price level with max accumulation
    pocPrice = 0.0
    maxAcc = 0
    for i = 0 to tickCount - 1
        level = priceAccumulation[i]
        // Count bars at this level (simplified)
        acc = 0
        for j = 0 to tickCount - 1
            if priceAccumulation[j] == level
                acc := acc + 1
        if acc > maxAcc
            maxAcc := acc
            pocPrice := level
    plot(pocPrice, "POC", color=color.purple, style=plot.style_circle)
```

### Pattern: Resetting at Session Start

```pine
// Reset session trackers when market session starts
varip float sessionOpenHigh = na
varip float sessionOpenLow = na

// Detect session start (prior bar was out of market)
sessionStart = session.ismarket and not session.ismarket[1]

if sessionStart
    sessionOpenHigh := high
    sessionOpenLow := low

if session.ismarket and not sessionStart
    sessionOpenHigh := math.max(nz(sessionOpenHigh, high), high)
    sessionOpenLow  := math.min(nz(sessionOpenLow, low), low)
```

---

## Performance Limits — Hard Constraints (Expanded)

| Limit | Value | Enforced By | Consequence |
|-------|-------|-------------|-------------|
| **Loop execution time** | 500ms per loop per bar | TradingView WS server | `study_error: "Loop execution time exceeded"` |
| **Total script execution budget** | ~20s (free) / ~40s (Essential+) / ~100s (Ultimate) | TradingView WS server | Entire script times out |
| **Max drawings** | 500 IDs (lines/labels/boxes) | TradingView WS server | Oldest auto-deleted when exceeded |
| **Max polylines** | 100 IDs | TradingView WS server | Same auto-delete behavior |
| **Max alerts** | Tier-dependent | TradingView server | Alert creation fails silently |
| **Max studies/chart** | 2 (free) / 5 (Essential) / 10+ (Premium+) | TradingView server | "study limit" error |

### The 500ms Loop Budget — Time-Based, Not Iteration-Based

**Important:** The limit is **execution time per loop per bar**, not a fixed iteration count. A loop with 10,000 iterations of simple math may pass, while 100 iterations of expensive operations may timeout.

```pine
// ❌ Risky: May timeout on slow series ops
for i = 0 to array.size(myArray) - 1
    val := math.sqrt(myArray[i])  // sqrt is expensive

// ✅ Safe: Use built-ins when possible
myResult = ta.sma(myArray, 20)  // C implementation, fast

// ✅ Safe: Small, simple loops
sum = 0.0
for i = 0 to 99
    sum := sum + close[i]
```

### Total Execution-Time Budget

| Tier | Budget per Script Run |
|------|----------------------|
| Free | ~20 seconds |
| Essential | ~40 seconds |
| Plus | ~40 seconds |
| Premium | ~40 seconds |
| Ultimate | ~100 seconds |

**Optimization patterns:**
```pine
// ✅ Incremental update (avoid O(n) per bar)
var float[] window = array.new_float(20)
var int idx = 0
array.set(window, idx % 20, close)  // O(1) per bar
idx := idx + 1
mySma = array.sum(window) / 20.0  // Or use ta.sma

// ✅ Pre-compute, don't recalculate
// ❌ Bad: recalculate full sum every bar
// sum := 0.0
// for i = 0 to array.size(recent) - 1
//     sum := sum + recent[i]
// ✅ Good: maintain running total
var float runningSum = 0.0
// On each bar: runningSum := runningSum + close - oldValue
```

### Anti-Patterns & Fixes (Expanded Table)

| Anti-Pattern | Why It Fails | Fix |
|-------------|-------------|-----|
| `array.push` inside `for` loop on every bar | O(n²) total growth → OOM / timeout | Use `varip` + circular buffer, or `ta.*` functions |
| `ta.sma(close, 200)` with `max_bars_back` omitted | Auto-detect misses dynamic lookbacks | Set `max_bars_back=5000` explicitly |
| `plot` inside user-defined function | Functions are pure — no side effects | Move `plot` to calling scope; use `var` to persist |
| `var` + `plot` inside `if` block | `var` initializes on first bar regardless of `if` | Use `varip` for tick-level, or guard with `barstate.isconfirmed` |
| Loop with `close[i]` where `i > bar_index` | Runtime error on early bars | Guard: `bar_index >= i ? close[i] : na` |
| `request.security` without `lookahead_off` | Lookahead bias in backtest | Always pass `lookahead=barmerge.lookahead_off` |
| `max_bars_back` omitted, dynamic `[close[300]]` references | Pine auto-sizes from static refs only | Explicit `max_bars_back=5000` or restructure |
| Recursive function calls | Pine doesn't support recursion | Convert to iterative loop |
| Unbounded `array.push` without limit | Memory growth → timeout | Cap size: `if array.size(arr) < 200; array.push(arr, val)` |

---

## `calc_on_every_tick` — Strategy Only (Expanded)

```pine
//@version=6
strategy("High-Freq Strategy", calc_on_every_tick=true, initial_capital=10000)
```

### Behaviors

| Aspect | `calc_on_every_tick=false` (default) | `calc_on_every_tick=true` |
|--------|--------------------------------------|---------------------------|
| **Calculation frequency** | Bar close only | Every realtime tick |
| **`varip` updates** | Still update on every tick (independent) | Same — `varip` always updates on ticks |
| **`strategy.entry` execution** | On bar open | On every tick (if condition met) |
| **Backtest speed** | Faster | Slower (more calculations) |
| **Signal granularity** | Per-bar | Per-tick |
| **Use case** | Swing/trend following | Scalping, day trading, order flow |

### When to Use `calc_on_every_tick=true`

1. **Scalping strategies** — Capture small price moves on each tick
2. **Order flow detection** — Track volume/price per tick
3. **High-frequency signal generators** — Need per-tick accuracy
4. **Strategy with `varip`** — Combine tick-level state with order execution

**When NOT to use:**
- Most swing-trend strategies (default `false` is sufficient)
- Strategies where bar-close accuracy is sufficient
- When backtest speed is a priority

---

## Timeframe & Session Context (Expanded)

### Timeframe Functions — Full Reference

```pine
// Current chart timeframe
tf = timeframe.period           // "5", "60", "D", "W", "M"
isIntraday = timeframe.isintraday
isDailyPlus = timeframe.isdaily or timeframe.isweekly or timeframe.ismonthly

// Time of day
t = time                        // Current bar open time (Unix ms epoch)
tClose = time_close             // Current bar close time (Unix ms)
hourOfDay = hour(t)             // 0-23 (exchange timezone)
minuteOfDay = minute(t)         // 0-1439
dayOfWeek = dayofweek(t)        // 1=Sunday ... 7=Saturday

// Session-aware time
sessionName = syminfo.session   // e.g., "0930-1600"
inMarket = session.ismarket     // True during market hours

// Timeframe comparison
timeframe.isintraday           // true for intraday (not daily/weekly/monthly)
timeframe.isdaily              // true if "D" or larger
timeframe.isweekly             // true if "W"
timeframe.ismonthly            // true if "M"

// Convert between timeframes
curTf = timeframe.period
// To check if current is intraday vs daily
isIntraday = timeframe.isintraday
```

### Session Trading Patterns

```pine
// Trade only during specific sessions
mySession = input.session("0930-1600", "Trading Session")
inSession = session.ismarket and time(timeframe.period, mySession)

// Session-based signal filtering
if inSession and ta.crossover(fast, slow)
    strategy.entry("Session Long", strategy.long)

// Session close — exit before market close
if time >= 1590 and strategy.position_size > 0
    strategy.close("Session Long", comment="Close before close of session")
```

### Multi-Timeframe (MTF) Context

```pine
// Higher timeframe signal on lower timeframe chart
htf = "240"  // 4-hour
htfTrend = request.security(syminfo.tickerid, htf, close > ta.sma(close, 200), lookahead=barmerge.lookahead_off)

// Use on lower timeframe
if ta.crossover(close, htfTrend * close)
    // Enter long when price crosses above 4H trend on 5m chart
```

---

## Next Layer

→ **Layer 7: Backend API (Pine Facade)** → `api/pine-facade.md`

Covers: HTTP endpoints for script CRUD, compilation, search, and the Go `pinefacade` client implementation.