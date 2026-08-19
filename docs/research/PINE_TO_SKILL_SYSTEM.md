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

### 4. Graphics Extraction (Two-Layer Generic Design)

Graphics extraction uses a **two-layer design** that works across any script
without per-script matchers:

**Layer 1** (`pkg/pipeline/extract.go`): flat signal extraction from every draw type
(labels→events, lines→levels, boxes→S/R, tables→grids, hhists→volume bins).
Zero script-specific code.

**Layer 2** (`internal/agent/graphics_generic.go`): topology-based structural analysis
that groups elements by geometric topology (shared edges, width, extension, style)
and infers semantics from group properties. Detects volume-profile POC/VAH/VAL,
order-block zones, FVG/gap boxes, breaker blocks, liquidity levels, and session
markers purely from the drawing layer — no per-script knowledge needed.

```json
{
  "graphicCounts": { "dwgboxes": 12, "dwglabels": 4 },
  "events": [
    { "field": "dwglabels", "kind": "buy", "value": 65000, "time": 1784430000 }
  ],
  "levels": [
    { "field": "dwglabels", "kind": "support", "value": 61306.84 }
  ],
  "volumePeaks": [{"poc": 64000, "vah": 64500, "val": 63500, "stackCount": 10}],
  "zones": [{"top": 64500, "bottom": 63500, "mid": 64000}]
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
// pkg/skill/registry.go
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

## When to write a custom parser (and how)

The generic `--signals` extractor is the right default, but some scripts need a custom parser:

- The script only emits **graphic primitives** (boxes, labels, polylines) and returns **zero periods**.
- The signal you care about is embedded in **graphics labels** rather than numeric plots.
- You want **typed presets** (`scalping` / `swing`) and CLI-friendly inputs.
- You need a **stable `workflow` name** and a custom `Structure` shape for downstream agents.

### Step-by-step custom parser workflow

1. **Find the public Pine ID**
   ```bash
   ./tvcli search "institutional liquidity sweep XAUUSD" --limit 5 --json
   ```

2. **Inspect the schema**
   ```bash
   ./tvcli run PUB;b9372355c2e6483f952ca49a21d2ebbb --schema --json
   ```
   Check whether plots are numeric, shape-only, or missing entirely.

3. **Capture a raw response fixture**
   ```bash
   ./tvcli run PUB;b9372355c2e6483f952ca49a21d2ebbb \
     --symbol BINANCE:BTCUSDT --tf 1h --bars 50 \
     --raw-out pkg/skill/parsers/testdata/my_skill_fixture.json
   ```
   Use BTC/USDT (or any 24/7 market) on weekends when XAUUSD is thin/closed.

4. **Check for a `Close` / price field**
   - If `periods[0]` has `Close`/`close`, read it directly.
   - If the script is overlay and lacks a price plot, derive the price from the most recent `dwglabels` entry (`y` field is price when `yl == "pr"`).

5. **Create the skill file**
   - Path: `pkg/skill/parsers/<name>.go`
   - Declare `var XxxSkill = &skill.Skill{...}`
   - Provide `Inputs`, `Presets`, `ParseOutput`, `FormatText`
   - Call `skill.Register` in `init()`
   - `internal/cmd/shared.go` already blank-imports `pkg/skill/parsers`, so no extra wiring is needed.

6. **Register skill metadata in `internal/cmd/help.go`**
   Add the skill name under **Indicator Skills** in the static help text.

7. **Add tests**
   - Path: `pkg/skill/parsers/<name>_test.go`
   - Load the captured fixture and assert on `Status`, `Market.Bias`, `Structure` keys, and `Conformance.AgenticScore`.

8. **Build and verify**
   ```bash
   go build -o tvcli ./cmd/tvcli
   go test ./pkg/skill/parsers -v
   ./tvcli <skill> --symbol BINANCE:BTCUSDT --tf 5m --preset scalping --agent --json
   ```

### Common pitfalls discovered in practice

| Pitfall | Symptom | Fix |
|---|---|---|
| Graphics-only script | `periodCount: 0`, all data in `dwgboxes`/`dwglabels` | Either use the generic `--signals` path (events from labels) or pick an alternative script with numeric plots. |
| Missing `Close` field | `market.lastPrice: 0` | Read the latest label price from `graphic["dwglabels"]` as a fallback. |
| Heavy dashboard script | Timeout even with `TV_TIER=ultimate` | Drop the script; use a simpler alternative already in the registry (`smc`, `ict`, `vp`, `vgaps`). |
| Preset keys must match JS variable names | Preset values are silently ignored | Use the `Name` field of `InputDef`, not the `TVInputID`. |
| Unnamed `in_N` inputs | `--help` shows blank or cryptic flags | Only expose the few tunable inputs that matter; rely on defaults for the rest. |

### Minimal custom parser template

```go
package parsers

import (
    "fmt"
    "math"
    "strings"

    "github.com/mrme000m/tvcli/pkg/skill"
)

var MySkill = &skill.Skill{
    Name:     "my-skill",
    Synopsis: "Describe what it does",
    PineID:   "PUB;...",
    Inputs: []skill.InputDef{
        {Name: "length", TVInputID: "in_0", Type: "int", Default: 20},
    },
    Presets: map[string]map[string]any{
        "default":  {"length": 20},
        "scalping": {"length": 10},
        "swing":    {"length": 50},
    },
    ParseOutput: parseMySkill,
    FormatText:  formatMySkill,
}

func parseMySkill(periods []map[string]any, graphic map[string]map[string]any, tf, symbol string, args map[string]string) skill.SkillResult {
    if len(periods) == 0 {
        return skill.SkillResult{Status: "no_data", Workflow: "my-skill",
            Narrative: skill.Narrative{MarketStructure: "No data"}}
    }
    last := latestClosed(periods)
    price := toFloat(getField(last, []string{"Close", "close"}))
    if price == 0 {
        price = latestGraphicPrice(graphic)
    }
    // ... your event/metric parsing ...
    return skill.SkillResult{
        Status: "ok", Workflow: "my-skill",
        Market: skill.MarketData{LastPrice: price, Bias: bias},
        Structure: map[string]any{...},
        Opportunities: opps,
        Narrative: skill.Narrative{...},
        Validation: skill.Validation{Passed: true},
        Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(score)},
    }
}

func init() { skill.Register(MySkill) }
```

### Example: `liq-sweep` findings

Implemented in `pkg/skill/parsers/liq_sweep.go` for:

```text
PUB;b9372355c2e6483f952ca49a21d2ebbb
Institutional Liquidity Sweep & Volume Breakout [SMC]
```

- Schema exposes only `Bullish_Sweep_Shape`/`Bearish_Sweep_Shape` event flags.
- The script has no price plot, so price is read from the latest `dwglabels` price.
- Counting recent sweep events gives a directional bias and a high-confidence opportunity when the latest closed bar fires a sweep.
- Presets tune swing lookback and volume multiplier for scalping vs swing styles.

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
