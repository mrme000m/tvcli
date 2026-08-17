# Layer 6: Plots & Visuals — Chart Output

> **Prerequisite:** Layer 5 (Inputs). Every visual output method and programmatic extraction.

---

## Plot Functions

| Function | Output | Use For |
|----------|--------|---------|
| `plot(series, title, ...)` | Line/histogram/circles | Main indicator lines |
| `plotshape(series, title, ...)` | Shapes (triangles, circles, labels) | Signals, crossovers |
| `plotchar(series, title, char, ...)` | Single character | Compact signals |
| `plotarrow(series, title, ...)` | Up/down arrows | Directional signals |
| `plotcandle(open, high, low, close, ...)` | Candlesticks | Custom candles (Heikin Ashi) |
| `plotbar(open, high, low, close, ...)` | OHLC bars | Custom bars |

---

## Plot Styling

```pine
plot(emaFast, "Fast EMA", 
    color=color.green,
    linewidth=2,
    style=plot.style_line,        // line, stepline, histogram, circles, area, columns
    trackprice=true,              // Show price on Y-axis
    show_last=100,                // Only show last N bars
    offset=0,                     // Horizontal shift (bars)
    join=true,                    // Connect across na gaps
    editable=true                 // Allow user to override in Settings
)
```

**Styles:** `plot.style_line`, `plot.style_stepline`, `plot.style_histogram`, `plot.style_circles`, `plot.style_area`, `plot.style_columns`

---

## Dynamic Colors

```pine
// Color per bar based on condition
plot(close, "Close", 
    color=close > open ? color.green : color.red)

// Gradient (requires array of colors)
// Not directly supported — use plot with color=series color
bull = close > ta.sma(close, 200)
plot(ta.sma(close, 200), "Trend MA", color=bull ? color.green : color.red)
```

---

## Plotshape / Plotchar — Signal Markers

```pine
// Crossover signals
longCond = ta.crossover(ta.sma(close, 9), ta.sma(close, 21))
shortCond = ta.crossunder(ta.sma(close, 9), ta.sma(close, 21))

plotshape(longCond, "Long", shape.triangleup, location.belowbar, color.green, size=size.small)
plotshape(shortCond, "Short", shape.triangledown, location.abovebar, color.red, size=size.small)

// Plotchar (text character)
plotchar(longCond, "Long", "▲", location.belowbar, color.green, size=size.tiny)
plotchar(shortCond, "Short", "▼", location.abovebar, color.red, size=size.tiny)
```

**Shapes:** `shape.triangleup`, `shape.triangledown`, `shape.circle`, `shape.square`, `shape.diamond`, `shape.labelup`, `shape.labeldown`, `shape.flag`, `shape.xcross`

**Locations:** `location.belowbar`, `location.abovebar`, `location.top`, `location.bottom`, `location.absolute` (uses price value)

---

## Drawings (Labels, Lines, Boxes)

```pine
// Label
label.new(bar_index, high, "Text\nLine 2",
    color=color.blue,
    textcolor=color.white,
    style=label.style_label_down,
    size=size.normal,
    xloc=xloc.bar_index)

// Line
line.new(bar_index - 10, low, bar_index, high,
    color=color.yellow,
    width=2,
    style=line.style_solid,
    extend=extend.none)

// Box (rectangle)
box.new(bar_index - 20, high, bar_index, low,
    border_color=color.purple,
    bgcolor=color.new(color.purple, 90))

// Polyline (multi-point line)
var polyline pl = na
if barstate.islast
    pl := polyline.new([bar_index-5, bar_index], [high, low], closed=false)
```

**Manage drawings:** Store IDs in `var` arrays, delete old ones to stay under limits.

---

## Horizontal Lines & Fills

```pine
// Horizontal lines
hline(70, "Overbought", color=color.red, linestyle=hline.style_dashed)
hline(30, "Oversold", color=color.green, linestyle=hline.style_dashed)

// Fill between two hlines or plots
p1 = plot(ta.sma(close, 20), "MA20", color=color.blue)
p2 = plot(ta.sma(close, 50), "MA50", color=color.orange)
fill(p1, p2, color=color.new(color.blue, 90))
```

---

## Tables (Dashboard Display)

```pine
var table dashboard = table.new(position.top_right, 2, 3,
    bgcolor=color.new(color.black, 80),
    border_width=1)

if barstate.islast
    table.cell(dashboard, 0, 0, "Metric", bgcolor=color.gray)
    table.cell(dashboard, 1, 0, "Value", bgcolor=color.gray)
    table.cell(dashboard, 0, 1, "RSI", textcolor=color.white)
    table.cell(dashboard, 1, 1, str.tostring(ta.rsi(close, 14), "#.##"))
    table.cell(dashboard, 0, 2, "Trend", textcolor=color.white)
    table.cell(dashboard, 1, 2, close > ta.sma(close, 200) ? "Bull" : "Bear")
```

**Positions:** `top_left`, `top_center`, `top_right`, `middle_left`, `middle_center`, `middle_right`, `bottom_left`, `bottom_center`, `bottom_right`

---

## Alert Conditions

```pine
// Trigger TradingView alerts
alertcondition(longCond, "Long Signal", "Bullish crossover detected")
alertcondition(shortCond, "Short Signal", "Bearish crossover detected")

// Alert with dynamic message
alertcondition(ta.cross(close, ta.sma(close, 200)), "MA Cross", 
    "Price crossed 200 SMA at {{close}}")
// {{close}}, {{high}}, {{low}}, {{volume}}, {{ticker}}, {{interval}} — placeholders
```

---

## Extracting Plots Programmatically (tvcli --signals)

The Go `tvcli` parses WebSocket study data and maps plot indices to names via metaInfo:

```bash
tvcli run USER;abc123 --symbol OANDA:XAUUSD --tf 15m --bars 500 --signals --json
```

**Output:**
```json
{
  "periods": [
    {
      "$time": 1704067200000,
      "EMA Fast": 2034.5,
      "EMA Slow": 2031.2,
      "plot_0": 2034.5,
      "plot_1": 2031.2
    }
  ],
  "signals": [
    {"type": "crossover", "plot": "EMA Fast", "crossed": "EMA Slow", "direction": "up", "time": 1704067200000}
  ]
}
```

**MetaInfo mapping (from `indicator.go:buildPlotsMap`):**
- Styles → sanitized titles (e.g., "EMA Fast" from "EMA Fast Length")
- Plots → `styleTitle_plotType` (e.g., "EMA Fast_line")
- Fallback: `plot_0`, `plot_1`...

---

## Plot Limits

| Limit | Default | Max (via declaration) |
|-------|---------|----------------------|
| Plots (plot count) | 64 | 64 |
| Labels / Lines / Boxes | 50 | 500 |
| Polylines | 50 | 100 |

`max_labels_count`, `max_lines_count`, `max_boxes_count` accept 1–500; `max_polylines_count` accepts 1–100. The runtime deletes the oldest drawings of a type once the limit is exceeded.

**Declaration:**
```pine
indicator("Name", 
    max_labels_count=500,
    max_lines_count=500,
    max_boxes_count=500,
    max_polylines_count=100)
```

---

## Next Layer

→ **Layer 7: Backend API (Pine Facade)** → `api/pine-facade.md`

Covers: HTTP endpoints for script CRUD, compilation, search, and the Go `pinefacade` client implementation.