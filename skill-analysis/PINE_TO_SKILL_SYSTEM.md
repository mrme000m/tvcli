# Universal Pine Script to Skill Command System

## Executive Summary

The system you want **already exists** in the `tvcli` tool via the `--signals --agent` flag. This document explains the architecture and how to use it for any Pine Script.

---

## The Problem with Broken Skills

The broken skills (dvi, quantum, swingarm) have **custom parsers** that were written based on **incorrect assumptions** about field names:

### 1. delta-volume-intensity (dvi)

**Custom parser expects:**
```go
Trend, trend, TrendLine
Volatility, volatility, ATR
Momentum, momentum, ROC
```

**Actual TradingView fields:**
```json
{
  "Background_Color": 0,      // metric (0, 1, 2)
  "Downtrend_Alert": 0,      // metric (0, 1)
  "Momentum_ROC": 11.97,     // metric (continuous)
  "Resistance": 65600,        // style (price level)
  "Sideways_Alert": 0,       // metric (0, 1)
  "Support": 61306.84,        // price (price level)
  "Uptrend_Alert": 1,        // metric (0, 1)
  "Volatility_ATR": 1773.90  // metric (continuous)
}
```

**Root cause:** The custom parser uses `getField(last, []string{"Trend", "trend", "TrendLine"})` but the actual field is `Uptrend_Alert`/`Downtrend_Alert`.

### 2. quantum-ribbon

**Custom parser expects:**
```go
RibbonState, ribbonState, State
```

**Actual TradingView fields:**
```json
{
  "Plot": 64193.68,           // price (main EMA)
  "plot_10": 2365576192,      // metric (layer 1 color code)
  "plot_11": 2162604032,      // metric (layer 2 color code)
  "plot_12": 1942788352,      // metric (layer 3 color code)
  "plot_13": 1723039488,      // metric (layer 4 color code)
  "plot_14": 1503225344       // metric (layer 5 color code)
}
```

**Root cause:** The custom parser looks for a single `RibbonState` field, but the ribbon is represented as 5 separate plot lines with color codes.

### 3. swingarm-atr-trend-indicator

**Custom parser expects:**
```go
Trailingstop, plot_0
Extremum, plot_2
Fib_1, plot_4
Fib_2, plot_5
Fib_3, plot_6
plot_8 (signal)
plot_9 (bgColor)
```

**Actual TradingView fields:**
```json
{
  "Extremum": 2,                    // metric
  "Fib_1": 64231.08,               // price
  "Fib_2": 64030.96,               // price
  "Fib_3": 63911.84,               // price
  "Trailingstop": 0,               // metric
  "plot_8": 0,                     // metric (signal)
  "plot_9": 4,                     // metric (background color)
  "plot_10": 6                     // metric (another value)
}
```

**Root cause:** The custom parser uses generic `plot_N` names, but the Pine Script has named plots with titles.

---

## The Solution: `--signals --agent`

The `tvcli` tool already has a **universal schema-guided extractor** that works with ANY Pine Script:

### Basic Usage

```bash
# For any Pine Script by ID
./tvcli run <pine-id> --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json

# For registered skill commands
./tvcli <skill> --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json
```

### Examples

```bash
# Delta Volume Intensity
./tvcli dvi --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json

# Quantum Ribbon
./tvcli quantum --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json

# SwingArm ATR Trend
./tvcli swingarm --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json

# Any arbitrary Pine Script
./tvcli run PUB;bdd3bc54cf9f4dc6b42e6b2879b4eed2 --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json
```

### Output Structure

The `--signals --agent` flag produces an **agent-ready-v2** envelope:

```json
{
  "status": "ok",
  "exitCode": 0,
  "timestamp": "...",
  "execution": { "durationMs": ..., "attempts": 1 },
  "agentContext": {
    "workflow": "<pine-script-name>",
    "modelVersion": "agent-ready-v2",
    "symbol": "BINANCE:BTCUSDT",
    "timeframe": "1h"
  },
  "market": {
    "lastPrice": 61306.84,
    "bias": "neutral" | "bullish" | "bearish"
  },
  "structure": {
    "classifications": {
      "<field>": "price" | "metric" | "signal" | "style" | "noise"
    },
    "last": { "<field>": <value> },
    "series": [ /* last 50 bars */ ],
    "levels": [ { "field": "...", "kind": "support"|"resistance"|"band", "value": ... } ],
    "events": [ { "field": "...", "kind": "buy"|"sell"|"bos"|"choch", ... } ],
    "graphicCounts": { "dwgboxes": 12, "dwglabels": 4 },
    "meta": { "pineId": "...", "symbol": "...", "timeframe": "...", "periodCount": 150 }
  },
  "opportunities": [],
  "narrative": {
    "marketStructure": "...",
    "primaryOpportunity": "",
    "warnings": []
  },
  "conformance": {
    "hasValidData": true,
    "agenticScore": 0.5
  },
  "schemaVersion": "agent-ready-v2.0.0"
}
```

---

## How the Schema-Guided Extractor Works

### 1. Schema Analysis

When a Pine Script is loaded, the system extracts `metaInfo` which contains:

- **`plots[]`** — index, title, plot type (`line`, `histogram`, `bgcolor`, `alertcondition`, `fill`, etc.)
- **`styles`** — title per `plot_N` slot (renames generic `plot_0` to semantic names)
- **`inputs[]`** — `id`, `name`, `type`, `default`, `hidden`/`fake` flags

```bash
# View the schema for any script
./tvcli run <pine-id> --schema --json
```

### 2. Field Classification

The extractor classifies each field into categories:

| Class | Meaning | Examples |
|-------|---------|----------|
| `price` | Price-level lines/bands | `Support`, `Resistance`, EMA/MA lines |
| `metric` | Continuous calculated values | `Volatility_ATR`, `Momentum_ROC` |
| `signal` | Sparse 0/1 or -1/0/1 event flags | `Uptrend_Alert`, `Buy_Signal` |
| `histogram` | Momentum/volume histograms | buy/sell volume delta |
| `style` | Color/line-style selectors | `Background_Color` |
| `noise` | All-NaN, all-zero, or raw color values | ignored by default |

### 3. Series Extraction

The extractor builds a **chronological series** of the last 50 bars with all classified fields:

```json
{
  "series": [
    {
      "time": 1783893600,
      "Support": 57800.19,
      "Resistance": 63999,
      "Volatility_ATR": 1997.82,
      "Momentum_ROC": 0,
      "Background_Color": 2,
      "Uptrend_Alert": 0,
      "Downtrend_Alert": 0,
      "Sideways_Alert": 1
    },
    // ... more bars
  ]
}
```

### 4. Graphics Extraction

The extractor also processes **graphics primitives** (labels, boxes, lines):

```json
{
  "graphicCounts": { "dwgboxes": 12, "dwglabels": 4 },
  "events": [
    { "field": "dwglabels", "kind": "buy", "value": 65000, "time": 1784430000 }
  ],
  "levels": [
    { "field": "dwglabels", "kind": "support", "value": 61306.84 }
  ]
}
```

### 5. Bias Detection

The extractor computes a **bias** (neutral/bullish/bearish) based on:
- Price position relative to levels
- Signal distribution (buy vs sell events)
- Metric trends (momentum, volatility)

---

## Creating a New Skill Command

To create a skill command for ANY Pine Script:

### Step 1: Get the Pine Script ID

```bash
# Search for public scripts
./tvcli publist --limit 20 --json

# Or use a known Pine ID
PINE_ID="PUB;bdd3bc54cf9f4dc6b42e6b2879b4eed2"
```

### Step 2: Analyze the Schema

```bash
./tvcli run $PINE_ID --schema --json
```

This shows:
- What plots exist (with titles)
- What inputs are available
- What graphics are produced

### Step 3: Test the Generic Extractor

```bash
./tvcli run $PINE_ID --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json
```

If this produces useful output, **no custom parser is needed**.

### Step 4: Register as a Skill (Optional)

If you want a named command like `tv my-skill`, add it to the skill registry:

```go
// internal/skill/registry.go
var MySkill = &skill.Skill{
    Name:     "my-skill",
    Synopsis: "My custom indicator",
    PineID:   "PUB;...",
    Inputs: []skill.InputDef{
        // Define inputs here
    },
    ParseOutput: parseMySkill, // Optional: custom parser
    FormatText:  formatMySkill,
}
```

But **you don't need a custom parser** — the generic extractor works for most scripts.

---

## Comparison: Custom Parser vs Generic Extractor

| Aspect | Custom Parser | Generic Extractor |
|--------|---------------|-------------------|
| **Setup** | Write Go code per skill | Use `--signals --agent` |
| **Field names** | Must match exactly | Auto-mapped via schema |
| **Classification** | Manual | Schema-guided + statistical |
| **Series** | Manual extraction | Automatic (last 50 bars) |
| **Graphics** | Manual parsing | Automatic (labels, boxes, lines) |
| **Maintenance** | High (breaks on Pine changes) | Low (schema auto-adapts) |

**Recommendation:** Use the generic extractor unless you need highly specialized output formatting.

---

## Practical Examples

### Example 1: Any Pine Script

```bash
# Run any public Pine Script
./tvcli run PUB;bdd3bc54cf9f4dc6b42e6b2879b4eed2 \
  --symbol BTCUSDT \
  --tf 1h \
  --bars 50 \
  --signals \
  --agent \
  --json
```

### Example 2: With Custom Inputs

```bash
# Override Pine Script inputs
./tvcli run PUB;bdd3bc54cf9f4dc6b42e6b2879b4eed2 \
  --symbol BTCUSDT \
  --tf 1h \
  --bars 50 \
  --input length_volatility=20 \
  --input length_momentum=10 \
  --signals \
  --agent \
  --json
```

### Example 3: Strategy Script

```bash
# For strategies (includes strategyReport)
./tvcli run PUB;6daafb2cabe6419d98ae25229d2327f8 \
  --symbol BTCUSDT \
  --tf 1h \
  --bars 200 \
  --signals \
  --agent \
  --json
```

### Example 4: Save Output

```bash
# Save to file
./tvcli run PUB;bdd3bc54cf9f4dc6b42e6b2879b4eed2 \
  --symbol BTCUSDT \
  --tf 1h \
  --bars 50 \
  --signals \
  --agent \
  --json \
  --out analysis.json
```

---

## Troubleshooting

### "No data returned"

```bash
# Check if the script loads
./tvcli run <pine-id> --schema

# Try a different symbol/timeframe
./tvcli run <pine-id> --symbol BTCUSDT --tf 1h --bars 50 --signals
```

### "Field names don't match"

The generic extractor handles this automatically via schema mapping. If you're writing a custom parser, use the schema to get correct field names:

```bash
./tvcli run <pine-id> --schema --json | jq '.plots[] | {index, title}'
```

### "Graphics not extracted"

Check if the script produces graphics:

```bash
./tvcli run <pine-id> --raw --json | jq '.graphic | keys'
```

### "Strategy report missing"

Only strategy scripts (`isStrategy: true`) produce strategy reports. Check:

```bash
./tvcli run <pine-id> --schema --json | jq '.isStrategy'
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    tvcli run <pine-id>                       │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  1. Load Pine Script (metaInfo, schema, inputs)             │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Execute on TradingView (periods[], graphic{}, strategy) │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  3. Extract Signals (--signals flag)                        │
│     • Schema-guided classification                          │
│     • Series extraction (last 50 bars)                      │
│     • Graphics decoding (labels, boxes, lines)              │
│     • Bias computation                                      │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  4. Agent Envelope (--agent flag)                           │
│     • Standardized market/structure/opportunities           │
│     • Agent-ready-v2 schema                                 │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Files

- **Schema analysis**: `pkg/schema/schema.go`
- **Signal extraction**: `pkg/pipeline/extract_schema.go`
- **Generic extractor**: `pkg/pipeline/extract.go`
- **Dynamic parser**: `pkg/pipeline/dynparse.go`
- **Run command**: `internal/cmd/run.go`
- **Skill command**: `internal/cmd/skillcmd.go`
- **Agent bridge**: `internal/cmd/shared.go`

---

## Conclusion

The system you want **already exists**. Use:

```bash
./tvcli run <pine-id> --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json
```

This works for **any Pine Script** without custom parsers. The schema-guided extractor automatically:
1. Maps plot names via `metaInfo.styles`
2. Classifies fields (price, metric, signal, etc.)
3. Extracts series data for recent bars
4. Decodes graphics primitives
5. Computes bias and opportunities
6. Wraps everything in an agent-ready envelope

The broken skills (dvi, quantum, swingarm) can be fixed by either:
1. Using `--signals --agent` instead of custom parsers
2. Updating the custom parsers to use correct field names from the schema
