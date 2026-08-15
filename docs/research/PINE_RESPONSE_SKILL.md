---
name: pine-response-anatomy
description: |
  Reference for the raw TradingView Pine Script run response. Covers periods,
  graphics, strategy reports, metaInfo/schema, which inputs matter, and the
  exact CLI commands that parse the response into structured quantitative data.
version: 1.0.0
---

# TradingView Pine Script Run Response Anatomy

Use this when building, debugging, or extending `tv <skill>` commands. It covers the raw response shape, how to interpret each section, which inputs matter, and the exact CLI commands that parse/describe the response.

> **Core idea:** TradingView returns the same three sections for every run — `periods`, `graphics`, and (for strategies) `strategyReport`. The `tvcli` tooling uses the script's `metaInfo`/schema to map opaque `plot_N` keys to human-readable names and classify values as price, signal, metric, band, or style.

---

## 1. What kind of script is it?

| Type | `schema.isStrategy` | Typical raw output | What to extract |
|------|---------------------|--------------------|-----------------|
| **Indicator** | `false` | `periods[]` + `graphic{}` | Plots, levels, signals, recent bar series |
| **Strategy** | `true` | `periods[]` + `graphic{}` + `strategyReport{}` | Same as indicator **plus** performance metrics and trade list |

Most scripts in the skill registry are indicators. Strategies additionally emit a `strategyReport` with backtest results and executed trades.

```bash
# Check the type and high-level shape before running data extraction.
./tvcli run <pine-id> --symbol BTCUSDT --tf 1h --bars 50 --schema
```

---

## 2. Anatomy of a raw TradingView response

After `service.RunScript` finishes, the raw response (`tv run ... --raw`) contains:

```go
RunResult {
    Indicator      *PineIndicator      // source, metaInfo, schema, inputs
    Periods        []map[string]any    // bar-by-bar plot/candle values
    Graphic        map[string]map[string]any  // drawing primitives
    StrategyReport map[string]any      // only for strategies
}
```

### 2.1 `periods[]` — bar-by-bar values

List of bars, **newest first** (`periods[0]` is the in-progress bar, `periods[1]` is the last closed bar).

Common keys in a single period:

```json
{
  "$time": 1784430000,
  "plot_0": 0,
  "plot_1": 61306.84,
  "Momentum_ROC": 14.12,
  "Support": 61306.84,
  " Resistance": 64700
}
```

| Key pattern | Meaning |
|-------------|---------|
| `$time` | Unix timestamp of the bar (seconds) |
| `plot_0` … `plot_N` | Raw plot slots. When `metaInfo.styles` has titles, the schema renames these to semantic names like `Support`, `Volatility_ATR`, `Uptrend_Alert`. |
| `plotcandle_0_ohlc_*` | OHLC replay fields when the script replays candle data |
| Named boolean flags | `Uptrend_Alert`, `Downtrend_Alert`, `Bearish_BOS`, `Bullish_FVG`, etc. Usually `0/1` signal plots. |

> **Important:** `periods[0]` is often incomplete. For analysis, prefer `periods[1]` (last closed bar) or let `--signals`/`--series` handle bar selection.

### 2.2 `graphic{}` — drawing primitives

Map of drawing layers emitted by the script. Each key holds a map of objects keyed by an ID.

```json
{
  "graphic": {
    "dwgboxes":    { "1": { "x1": 1355, "x2": 1357, "y1": 64006, "y2": 63887, ... }, ... },
    "dwglabels":   { "1001": { "t": "EQH", "x": 55, "y": 108638, ... }, ... },
    "dwglines":    { "1000": { "x1": 53, "x2": 56, "y1": 108678, "y2": 108638, ... }, ... }
  }
}
```

| Layer | What it carries | Useful keys |
|-------|-------------------|-------------|
| `dwgboxes` | Rectangles/zones (order blocks, FVGs, volume clusters) | `x1/x2` (bar indexes), `y1/y2` (price levels), `t` (text), `bc`/`tc` (color codes) |
| `dwglabels` | Text labels at price/time points | `t` (label text), `x` (bar index), `y` (price), `yl` = `"pr"` when y is price |
| `dwglines` | Horizontal/trend lines, ray lines | `x1/x2`, `y1/y2`, `st` (style) |
| `dwgpolylines` | Multi-segment lines/paths | `points[]` |
| `dwgtable` | Dashboard-style tables | `Rows`, `cols`, `cell` values |
| `dwgpolycircles` / `dwgellipses` | Circles, ellipses | `x`, `y`, `r` |

The generic extractor scans `dwglabels` for text tokens such as `BUY`, `SELL`, `SUPPORT`, `RESISTANCE`, `POC` and turns them into `events` and `levels`.

### 2.3 `strategyReport{}` — strategy-only

Only present for strategies (`isStrategy: true`). Typical structure:

```json
{
  "performance": {
    "all": {
      "netProfit": 1234.5,
      "totalTrades": 42,
      "percentProfitable": 52.4,
      "profitFactor": 1.3,
      "maxDrawdown": -987.6
    }
  },
  "trades": [ ... ],
  "equity": [ ... ],
  "buyHold": { ... }
}
```

The generic extractor surfaces the `performance.all` block as `strategy` in the signals output.

---

## 3. Schema / `metaInfo` — the Rosetta Stone

Every public Pine script loaded through the Pine Facade returns `metaInfo`. `tvcli` turns this into a `PineSchema` with:

- `isStrategy`, `isOverlay`
- `plots[]` — index, title, plot type (`line`, `histogram`, `bgcolor`, `alertcondition`, `fill`, etc.), semantic class
- `inputs[]` — `id` (`in_0`, `in_1`, …), name, type, default, hidden/fake flags
- `styles` — title per `plot_N` slot
- `graphics` — flags for lines/labels/boxes/tables/fills

Use the schema commands first:

```bash
# Human-readable summary
./tvcli run <pine-id> --symbol BTCUSDT --tf 1h --schema

# Full machine-readable schema
./tvcli run <pine-id> --symbol BTCUSDT --tf 1h --schema --json
```

The schema is what rename `plot_3` to `Volatility_ATR` and classifies it as `metric` instead of raw noise.

---

## 4. Which inputs actually matter?

Not every input in `metaInfo.inputs` changes the quantitative response. Use these rules:

### 4.1 Ignore unless they carry a number

| Input type | Usually safe to ignore? | Exception |
|------------|------------------------|-----------|
| `color` | **Yes** | A `color.new(..., transparency)` input can still affect whether a layer is visible, but it does not change numeric values. |
| `bool` that only toggles visibility | **Yes** e.g. `Show Lines`, `Show Background` | A bool that switches a calculation mode or enables a moving average *does* matter. Inspect the tooltip/name. |
| Inputs with empty `name` | **Yes** | Cosmetic/transparency inputs often ship with no name. |
| `isHidden: true` / `isFake: true` | **Yes** | Internal Pine/TradingView runtime inputs (fast-calc, transp, width, etc.). |
| `Border`, `Text Color`, `Line Style`, `Transparency` | **Yes** | Pure styling. |

### 4.2 Inputs that usually matter

- **Length/period/lookback** inputs (`atrLen`, `lengthMA`, `lookback_sr`, `swingsLength`)
- **Source/offset** inputs (`src`, `offset`, `startTime`, `endTime`)
- **Method/select** inputs (`maType`, `modeInput`, `trailType`) — change the formula
- **Threshold/multiplier** inputs (`atrMult`, `ATRFactor`)
- **Booleans that enable/disable calculations** (`useEMA2`, `enableReentry`, `showStructureInput` only if it gates a plot that affects signals)

### 4.3 Quick command to audit inputs

```bash
./tvcli run <pine-id> --symbol BTCUSDT --tf 1h --schema --json | jq '.inputs[] | {id, name, type, default, isHidden, isFake}'
```

Filter out obvious cosmetic rows (`type == "color"`, empty `name`) to get the list of inputs worth exposing as CLI flags.

---

## 5. CLI commands that parse/decipher the response

### 5.1 Dump the raw response

```bash
./tvcli run <pine-id> --symbol BTCUSDT --tf 1h --bars 50 --raw --json
# or write to file
./tvcli run <pine-id> --symbol BTCUSDT --tf 1h --bars 50 --raw-out dump.raw.json
```

Use this when you need to see the exact field names, graphics primitives, or strategy report shape.

### 5.2 Inspect the schema

```bash
./tvcli run <pine-id> --symbol BTCUSDT --schema
./tvcli run <pine-id> --symbol BTCUSDT --schema --json
```

### 5.3 Generic structured extraction (any Pine script)

```bash
# Compact summary
./tvcli run <pine-id> --symbol BTCUSDT --tf 1h --bars 50 --signals

# Full structured JSON: classifications, last, series, levels, events, bias
./tvcli run <pine-id> --symbol BTCUSDT --tf 1h --bars 50 --signals --json

# Wrapped in the agent-ready-v2 envelope used by skill commands
./tvcli run <pine-id> --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json
```

### 5.4 Use it on registered skill commands

Every skill command (`tv dvi`, `tv quantum`, `tv sniper`, …) now accepts `--signals` and `--signals --agent`. This bypasses the hand-coded parser and uses the schema-guided extractor.

```bash
./tvcli dvi --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json
./tvcli quantum --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json
./tvcli swingarm --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json
```

This is the fastest way to fix a broken field-name parser: run the same command through `--signals --agent` and compare the output.

---

## 6. Understanding the generic `Signals` output

`--signals` returns a `pipeline.Signals` object:

```json
{
  "meta": { "pineId": "...", "symbol": "...", "timeframe": "...", "periodCount": 150 },
  "classifications": {
    "Support": "price",
    "Momentum_ROC": "metric",
    "Uptrend_Alert": "signal"
  },
  "last": { "Support": 61306.84, "Momentum_ROC": 43.47, ... },
  "series": [ /* last 50 chronological bars, same fields */ ],
  "levels": [ { "field": "Support", "kind": "support", "value": 61306.84 } ],
  "events": [ { "field": "dwglabels", "kind": "buy", "value": 65000, "time": 1784430000 } ],
  "graphicCounts": { "dwgboxes": 12, "dwglabels": 4 },
  "bias": "neutral",
  "confidence": 0.5
}
```

### Plot classes

| Class | Meaning | Examples |
|-------|---------|----------|
| `price` | Price-level lines/bands | `Support`, `Resistance`, EMA/MA lines |
| `metric` | Continuous calculated values | `Volatility_ATR`, `Momentum_ROC`, dominance ratios |
| `signal` | Sparse 0/1 or -1/0/1 event flags | `Uptrend_Alert`, `Buy_Signal`, `Bullish_BOS` |
| `histogram` | Momentum/volume histograms | buy/sell volume delta |
| `band` | Area/band plots | Bollinger bands, VWAP bands |
| `style` | Color/line-style selectors | `Background_Color`, `plotcandle` colorers |
| `noise` | All-NaN, all-zero, or raw color values | ignored by default |

The `classifications` map tells you which fields are worth keeping in a downstream model and which are safe to drop.

---

## 7. Practical workflow for a new Pine script

1. **Identify the type**
   ```bash
   ./tvcli run <pine-id> --schema
   ```

2. **Dump raw response once**
   ```bash
   ./tvcli run <pine-id> --symbol BTCUSDT --tf 1h --bars 50 --raw-out raw.json
   ```

3. **Check generic extraction**
   ```bash
   ./tvcli run <pine-id> --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json
   ```

4. **Audit inputs**
   ```bash
   ./tvcli run <pine-id> --schema --json | jq '.inputs[] | select(.type != "color" and .name != "" and .isHidden != true)'
   ```

5. **Decide whether a custom parser is needed**
   - If `--signals --agent` produces enough structure/series/events, no custom parser is needed.
   - If the script relies heavily on graphics tables, clusters, or exotic shapes, extend `pkg/pipeline` extractors instead of writing a one-off parser.

---

## 8. Common gotchas

- **XAUUSD on weekends:** there are no bars. Use `BTCUSDT` for testing.
- **`periods[0]` is the forming bar:** it can have zero/missing values. The generic extractor uses `periods[1..N]` for closed-bar statistics.
- **Duplicate plot titles:** some scripts name every EMA line "Plot". The generic extractor deduplicates, but a custom parser may need to fall back to `plot_N` keys or inspect `metaInfo.styles` directly.
- **Graphics vs plots:** many scripts output *only* graphics (no numeric plots). If `--signals` returns empty `last`, inspect `graphicCounts` and add a graphics extractor in `pkg/pipeline`.
- **Color inputs:** ignore unless a numeric transparency/limit value is being used as a threshold.

---

## 9. Files to read when extending extraction

- `pkg/schema/schema.go` — `PineSchema`, `PlotDef`, `InputDef`
- `pkg/pipeline/dynparse.go` — maps `plot_N` → semantic names
- `pkg/pipeline/extract_schema.go` — schema-guided classification and series builder
- `pkg/pipeline/extract.go` — fallback statistical extractor and graphics decoder
- `internal/cmd/run.go` — `tv run` orchestration
- `internal/cmd/skillcmd.go` — skill command wrapper
- `internal/cmd/shared.go` — `signalsToAgent()` bridge
