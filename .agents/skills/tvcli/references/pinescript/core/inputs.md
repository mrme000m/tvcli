# Layer 5: Inputs & Settings — Configuration UX

> **Prerequisite:** Layer 4 (Indicators vs Strategies). Master the Settings panel and automation inputs.

---

## Input Functions (v5/v6)

| Function | Type | Returns | Use For |
|----------|------|---------|---------|
| `input.int(defval, title, ...)` | Integer | `int` | Lengths, periods, counts |
| `input.float(defval, title, ...)` | Float | `float` | Prices, multipliers, percentages |
| `input.bool(defval, title, ...)` | Boolean | `bool` | Toggles, enable/disable |
| `input.string(defval, title, ...)` | String | `string` | Symbols, text, enum selection |
| `input.source(defval, title, ...)` | Source | `series float` | Price data (close, hl2, etc.) |
| `input.color(defval, title, ...)` | Color | `color` | Plot colors |
| `input.time(defval, title, ...)` | Time | `int` (timestamp) | Session times, anchor points |
| `input.timeframe(defval, title, ...)` | Timeframe | `string` | MTF selection ("15", "60", "D") |
| `input.session(defval, title, ...)` | Session | `string` | Trading session ("0930-1600") |

---

## Common Options (All Inputs)

```pine
input.int(14, "Length",
    minval=1,           // Minimum value
    maxval=500,         // Maximum value
    step=1,             // Step increment
    tooltip="Lookback period",  // Hover text
    group="Main Settings",      // Group in Settings panel
    inline="ema_group",         // Inline group (side-by-side)
    confirm=false       // Require confirmation dialog
)
```

---

## Input Types Deep Dive

### Integer & Float
```pine
len = input.int(20, "Length", minval=1, maxval=200)
mult = input.float(2.0, "Multiplier", step=0.1, minval=0.1)

// Step creates slider granularity in UI
// minval/maxval enforce bounds (clamped at runtime)
```

### Boolean
```pine
showLabels = input.bool(true, "Show Labels")
useHeikin = input.bool(false, "Use Heikin Ashi")
```

### String (with Dropdown Options)
```pine
// Free text
symbol = input.string("BTCUSDT", "Symbol")

// Dropdown enum (options = array of strings)
maType = input.string("EMA", "MA Type", options=["SMA", "EMA", "RMA", "WMA", "VWMA"])
```
**Options appear as dropdown in Settings panel.**

### Source (Price Data)
```pine
src = input.source(close, "Source")
// Returns series float: close, open, high, low, hl2, hlc3, ohlc4, etc.
// User selects from: Close, Open, High, Low, HL2, HLC3, OHLC4, Volume...
```

### Color
```pine
bullColor = input.color(color.green, "Bull Color")
bearColor = input.color(color.red, "Bear Color")
// Transparency handled separately or via color.new()
```

### Time & Timeframe
```pine
// Time input (session open/close, anchor time)
anchorTime = input.time(timestamp("2024-01-01 00:00"), "Anchor Time")

// Timeframe input (MTF)
mtf = input.timeframe("60", "HTF Timeframe")
// Returns string like "60", "240", "D" — use with request.security()
```

### Session
```pine
sess = input.session("0930-1600", "Trading Session")
// Returns string — use with session.ismarket or time(timeframe.period, sess)
```

---

## Inline Groups (Side-by-Side Inputs)

```pine
// Group related inputs horizontally
fastLen = input.int(9, "Fast", inline="ema", group="EMA Settings")
slowLen = input.int(21, "Slow", inline="ema", group="EMA Settings")
src     = input.source(close, "Source", inline="ema", group="EMA Settings")

// Another group
tp = input.float(2.0, "Take Profit %", inline="risk", group="Risk Management")
sl = input.float(1.0, "Stop Loss %", inline="risk", group="Risk Management")
```

**Result:** Settings panel shows collapsible groups with inline inputs on same row.

---

## Dynamic Inputs (Conditional Visibility)

```pine
useAdvanced = input.bool(false, "Advanced Settings")

// Only show if useAdvanced = true
advLen = input.int(50, "Advanced Length", 
    tooltip="Only used when Advanced enabled")
    // Note: Pine doesn't support true conditional visibility
    // Workaround: use tooltip + validation in code
```

**True conditional visibility not supported.** All inputs always visible. Use tooltips and code-level guards.

---

## Generating Input YAML for Automation (tvcli)

The Go `tvcli` extracts inputs from compiled script metaInfo and generates YAML:

```bash
tvcli inputs USER;abc123 --json
# Output:
{
  "inputs": [
    {"id": "in_0", "name": "Length", "type": "int", "defval": 14, "minval": 1, "maxval": 200},
    {"id": "in_1", "name": "Source", "type": "source", "defval": "close"},
    {"id": "in_2", "name": "MA Type", "type": "string", "defval": "EMA", "options": ["SMA","EMA","RMA"]}
  ]
}

tvcli inputs USER;abc123 > my_indicator_inputs.yaml
# Edit YAML, then:
tvcli run USER;abc123 --inputs my_indicator_inputs.yaml
```

**YAML Format:**
```yaml
inputs:
  in_0: 20          # By internal ID (in_0, in_1...)
  in_1: "hl2"       # Source type
  in_2: "SMA"       # String enum
```

**tvcli also matches by name:** `Length: 20` works too (case-insensitive, fuzzy).

---

## Input ID Mapping (Internal vs Display)

| In Pine Script | In TV API (metaInfo) | In tvcli YAML |
|----------------|----------------------|---------------|
| `len = input.int(14, "Length")` | `id: "in_0"`, `name: "Length"` | `in_0: 20` or `Length: 20` |
| `src = input.source(close, "Source")` | `id: "in_1"`, `name: "Source"` | `in_1: "hl2"` |
| `maType = input.string("EMA", "MA Type", options=["SMA","EMA"])` | `id: "in_2"`, `name: "MA Type"`, `options: [...]` | `in_2: "SMA"` |

**Always use `inline` groups** — they create stable internal IDs (`in_0`, `in_1`...) that don't shift when you add inputs.

---

## Best Practices

1. **Always set `minval`/`maxval`** — prevents invalid states
2. **Use `group` + `inline`** — organized Settings panel, stable IDs
3. **Add `tooltip`** — documents intent for you and others
4. **Prefer `input.source` over `input.string` for prices** — proper type, UI picker
5. **Use `input.timeframe` for MTF** — validates timeframe format
6. **Keep defaults sensible** — script should work out of the box

---

## Next Layer

→ **Layer 6: Plots & Visuals** → `core/plots.md`

Covers: all plot types, styling, drawings, tables, and extracting plot data programmatically.