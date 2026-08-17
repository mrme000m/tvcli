# Layer 10: Common Patterns — Reusable Idioms

> **Prerequisite:** Layer 9 (Integration). Battle-tested Pine Script patterns from the workspace skill parsers.

---

## Trend Detection

### Multi-Timeframe Trend
```pine
//@version=6
indicator("MTF Trend", overlay=true)
htf = input.timeframe("240", "HTF")
htfTrend = request.security(syminfo.tickerid, htf, close > ta.sma(close, 200), lookahead=barmerge.lookahead_off)
plot(htfTrend ? close : na, "HTF Bull", color=color.green, style=plot.style_circles)
```

### ADX + DI Trend
```pine
[len, thresh] = [input.int(14), input.int(20)]
[diplus, diminus, adx] = ta.dmi(len, len)
trendUp = adx > thresh and diplus > diminus
trendDown = adx > thresh and diminus > diplus
```

---

## Support / Resistance

### Pivot Points (Standard)
```pine
left = input.int(10), right = input.int(10)
ph = ta.pivothigh(high, left, right)
pl = ta.pivotlow(low, left, right)
var float res = na
var float sup = na
if not na(ph)
    res := ph[1]
if not na(pl)
    sup := pl[1]
plot(res, "Resistance", color.red)
plot(sup, "Support", color.green)
```

### Dynamic S/R from Volume Profile
```pine
// Uses VbPFixed built-in or custom volume profile
// See skill parsers: vp.go, anchored_vp.go
```

---

## Smart Money Concepts (SMC)

### Order Blocks (Bullish)
```pine
// Bearish candle before bullish move
obBull = close[1] < open[1] and close > open and close > high[1]
// Bullish candle before bearish move
obBear = close[1] > open[1] and close < open and close < low[1]

var box[] bullOBs = array.new_box(0)
var box[] bearOBs = array.new_box(0)

if obBull
    b = box.new(bar_index-1, high[1], bar_index, low[1], border_color=color.green, bgcolor=color.new(color.green, 90))
    array.push(bullOBs, b)
    // Clean old
    if array.size(bullOBs) > 10
        box.delete(array.shift(bullOBs))
```

### Fair Value Gaps (FVG)
```pine
// Bullish FVG: low[0] > high[2]
bullFVG = low > high[2]
// Bearish FVG: high[0] < low[2]
bearFVG = high < low[2]

if bullFVG
    box.new(bar_index-2, high[2], bar_index, low, border_color=color.blue, bgcolor=color.new(color.blue, 80))
```

### Break of Structure (BOS) / Change of Character (CHoCH)
```pine
// Swing highs/lows
swingHigh = ta.pivothigh(high, 5, 5)
swingLow = ta.pivotlow(low, 5, 5)

var float lastHigh = na
var float lastLow = na
var string structure = "neutral"

if not na(swingHigh)
    lastHigh := swingHigh[1]
if not na(swingLow)
    lastLow := swingLow[1]

bosUp = close > lastHigh and structure != "bull"
bosDown = close < lastLow and structure != "bear"

if bosUp
    structure := "bull"
    label.new(bar_index, high, "BOS ↑", color=color.green)
if bosDown
    structure := "bear"
    label.new(bar_index, low, "BOS ↓", color=color.red)
```

---

## ICT Patterns

### Kill Zones
```pine
// NY Kill Zone: 09:00-11:00 ET
// London Kill Zone: 03:00-05:00 ET
// Asian Kill Zone: 20:00-22:00 ET (prev day)

inNYKZ = session.ismarket and hour(time) >= 9 and hour(time) < 11
inLKZ = session.ismarket and hour(time) >= 3 and hour(time) < 5

bgcolor(inNYKZ ? color.new(color.blue, 95) : na)
bgcolor(inLKZ ? color.new(color.purple, 95) : na)
```

### Silver Bullet (10-11 AM reversal)
```pine
// High/low of 09:00-10:00, look for reversal 10:00-11:00
var float kzHigh = na
var float kzLow = na

if hour(time) == 9 and minute(time) == 0
    kzHigh := high
    kzLow := low
else if hour(time) == 9
    kzHigh := math.max(kzHigh, high)
    kzLow := math.min(kzLow, low)

silverBulletLong = inNYKZ and close > kzHigh and close[1] <= kzHigh
silverBulletShort = inNYKZ and close < kzLow and close[1] >= kzLow
```

---

## Session & Time Logic

### Session High/Low
```pine
var float sessionHigh = na
var float sessionLow = na

newSession = session.ismarket and not session.ismarket[1]
if newSession
    sessionHigh := high
    sessionLow := low
else if session.ismarket
    sessionHigh := math.max(sessionHigh, high)
    sessionLow := math.min(sessionLow, low)

plot(sessionHigh, "Session High", color=color.teal)
plot(sessionLow, "Session Low", color=color.teal)
```

### Time-Filtered Signals
```pine
// Only trade 09:30-15:30 ET
tradeTime = session.ismarket and hour(time) >= 9 and hour(time) <= 15
// Avoid first/last 30 min
avoidOpen = not (hour(time) == 9 and minute(time) < 30)
avoidClose = not (hour(time) == 15 and minute(time) > 30)
canTrade = tradeTime and avoidOpen and avoidClose
```

---

## Volume Analysis

### Buy/Sell Volume (BSV)
```pine
// Approximate: up-close volume vs down-close volume
buyVol = close > open ? volume : 0
sellVol = close < open ? volume : 0
// Or use intrabar (varip) for tick-level
```

### CVD (Cumulative Volume Delta)
```pine
var float cvd = 0
delta = close > open ? volume : (close < open ? -volume : 0)
cvd := cvd + delta
plot(cvd, "CVD")
```

### Volume Profile (Anchored)
```pine
// Anchor at swing low/high, session open, or fixed date
anchorTime = input.time(timestamp("2024-01-01 00:00"), "Anchor")
var float[] priceLevels = array.new_float(0)
var float[] volumes = array.new_float(0)

if time >= anchorTime
    // Bin volume by price level
    // See anchored_vp.go for full implementation
```

---

## Multi-Timeframe (MTF) Pattern

```pine
//@version=6
indicator("MTF Template")
tf = input.timeframe("60", "HTF")
[htfOpen, htfHigh, htfLow, htfClose] = request.security(syminfo.tickerid, tf, [open, high, low, close], lookahead=barmerge.lookahead_off)

htfTrend = htfClose > ta.sma(htfClose, 200)
htfRSI = ta.rsi(htfClose, 14)

// Use HTF bias to filter LTF signals
ltfLong = ta.crossover(ta.sma(close, 9), ta.sma(close, 21))
longSignal = ltfLong and htfTrend and htfRSI < 70
```

---

## Risk Management Helpers

### ATR-Based Stops
```pine
atrLen = input.int(14), atrMult = input.float(2.0)
atr = ta.atr(atrLen)
longSL = low - atr * atrMult
shortSL = high + atr * atrMult
```

### Position Size from Risk %
```pine
riskPct = input.float(1.0, "Risk %") / 100
accountSize = 10000  // Or strategy.equity
riskAmount = accountSize * riskPct
positionSize = riskAmount / (close - longSL)  // For long
```

---

## Visual Polish

### Gradient Plot
```pine
// Color intensity by value
rsiVal = ta.rsi(close, 14)
plot(rsiVal, "RSI", color=color.from_gradient(rsiVal, 30, 70, color.red, color.green))
```

### Clean Labels (Auto-cleanup)
```pine
var label[] labels = array.new_label(0)
if signal
    l = label.new(bar_index, high, "Long", color=color.green)
    array.push(labels, l)
    if array.size(labels) > 20
        label.delete(array.shift(labels))
```

---

## Performance: Circular Buffer for Rolling Calcs

```pine
// Rolling correlation without arrays (series math)
len = 20
sumXY = math.sum(close * volume, len)
sumX = math.sum(close, len)
sumY = math.sum(volume, len)
sumX2 = math.sum(close * close, len)
sumY2 = math.sum(volume * volume, len)
corr = (len * sumXY - sumX * sumY) / math.sqrt((len * sumX2 - sumX * sumX) * (len * sumY2 - sumY * sumY))
```

---

## Agent Anti-Patterns & Best Practices

| Anti-Pattern | Why It Fails | Fix |
|-------------|-------------|-----|
| `array.push` inside `for` loop on every bar | O(n²) total growth → OOM / timeout | Use `varip` + circular buffer, or `ta.*` functions |
| `ta.sma(close, 200)` with `max_bars_back` omitted but `close[500]` referenced | Auto-detect misses dynamic lookbacks | Set `max_bars_back=5000` explicitly, or restructure |
| `plot` inside user-defined function | Functions are pure — no side effects | Move `plot` to calling scope; use `var` to persist |
| `var` + `plot` inside `if` block | `var` initializes on first bar regardless of `if` | Use `varip` for tick-level, or guard with `barstate.isconfirmed` |
| Loop with `close[i]` where `i > bar_index` | Runtime error on early bars | Guard: `bar_index >= i ? close[i] : na` |
| `request.security` without `lookahead_off` | Lookahead bias in backtest | Always pass `lookahead=barmerge.lookahead_off` |
| `max_bars_back` omitted, dynamic `[close[300]]` references | Pine auto-sizes from static refs only | Explicit `max_bars_back=5000` or restructure |
| `map.from` usage | Does not exist in Pine | Use `map.new` + `map.put` |
| `array<T>` / `na(T)` / generics | Pine has no generics | Use UDTs + tuples for reusable logic |
| Drawing > 500 labels/boxes (100 polylines) | Oldest auto-deleted | Track counts, use `label.delete()` / `line.delete()` |
| `color.new(r,g,b,>` 100 | Transparency 0-100 range only | Keep transparency in 0-100 range |
| `strategy()` without `initial_capital` / `commission_type` | Strategy test fails to run | Always declare strategy params explicitly |
| `varip` on non-realtime bar | `varip` only updates on realtime ticks | Use `var` for historical bars only |
| `barstate.islast` used for "last bar only" logic | `islast` is true on every realtime bar tick | Use `barstate.isconfirmed` for last closed bar |
| `array.fill(close, 1, 3)` — 3rd param is exclusive | `index_to` must be one more than last index to fill | Remember: `fill(start, end_exclusive)` |
| `ta.dmi(len)` returning 3 values unpacked wrong | Returns `[plusDI, minusDI, adx]` | Use `[diplus, diminus, adx] = ta.dmi(len, len)` |
| `input.time` defval not using `timestamp()` | Must be `const int` | Use `input.time(timestamp("2024-01-01"), "Title")` |
| `matrix.new<float>(r, c, close)` — series as initializer | Initializer must be constant | Use `matrix.new<float>(r, c, 0.0)` |

### Golden Rules for Agent-Generated Pine Script

1. **Version first**: `//@version=6` must be the absolute first line
2. **Indicator vs Strategy**: Use `indicator()` for analysis, `strategy()` only when you need backtesting
3. **All inputs must have titles**: `input.int(..., title="...")` — unnamed inputs cause TV errors
4. **Guard series accesses**: Always check `bar_index >= N` before `close[N]`
5. **Prefer `ta.*` over hand-rolled**: Built-ins are optimized; custom loops risk 500ms timeout
6. **Use `varip` for intrabar state**, `var` for historical persistence
7. **Unpack `ta.dmi` correctly**: 3-value return `[plusDI, minusDI, adx]`
8. **No `map.from()`**: Use `map.new()` + `map.put()` — the function doesn't exist
9. **Transparency 0-100**: `color.new(r,g,b,a)` where a ∈ [0,100], 0=opaque, 100=invisible
10. **Strategy params**: Always specify `initial_capital` and `commission_type`/`commission_value`

---

## Next Layer

→ **Layer 11: Debugging & Errors** → `runtime/debugging.md`

Covers: Compile vs runtime errors, common error messages, WebSocket diagnostics, and the `check-auth` command.