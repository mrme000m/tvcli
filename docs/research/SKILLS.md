# Skill Framework Reference

Technical reference for the TradingView skill command system in `tvcli`. Covers the architecture, how to add new skills, and how the generic `--signals` extractor works.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  tvcli <skill> --symbol BTCUSDT --tf 1h --agent --json      │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  1. Resolve skill from registry (pkg/skill/registry.go)│
│     • Match command name → Skill struct                      │
│     • Apply preset or --input overrides                      │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Run Pine Script via TradingView WebSocket               │
│     • service.RunScript → periods[], graphic{}, strategy    │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  3. Parse output                                            │
│     A) Custom parser: skill.ParseOutput(periods, graphic)   │
│     B) Generic extractor: --signals (schema-guided)         │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  4. Emit result                                             │
│     • --json: full SkillResult JSON                         │
│     • --agent: agent-ready-v2 envelope (AgentResult)        │
│     • default: human-readable text                          │
└─────────────────────────────────────────────────────────────┘
```

## Two Ways to Run a Skill

### 1. Hand-coded parser (default)

```bash
./tvcli <skill> <SYMBOL> --tf <timeframe> --json --agent
```

Uses the `ParseOutput` function in `pkg/skill/parsers/<skill>.go`. Best when the script needs custom graphic/table extraction.

### 2. Generic schema-guided extractor

```bash
# Registered skill
./tvcli <skill> <SYMBOL> --tf <timeframe> --signals --agent --json

# Any public Pine script
./tvcli run <pine-id> <SYMBOL> --tf <timeframe> --signals --agent --json
```

Uses Pine `metaInfo` to rename `plot_N` keys, classify values (price / signal / metric / band / style / noise), and emit a standard `Signals` object. Use this first when onboarding a new script or when a custom parser is broken.

---

## Key Types

### `skill.Skill`

```go
type Skill struct {
    Name        string                                    // CLI command name
    Synopsis    string                                    // Short description
    PineID      string                                    // TradingView Pine Script ID
    Inputs      []InputDef                                // Configurable inputs
    Presets     map[string]map[string]any                 // Named input bundles
    ParseOutput func(periods, graphic, tf, symbol, args) SkillResult
    FormatText  func(result SkillResult) string
}
```

### `skill.SkillResult`

```go
type SkillResult struct {
    Status        string         // "ok", "no_data", "error"
    Workflow      string         // Skill name identifier
    Market        MarketData     // { LastPrice, Bias }
    Structure     map[string]any // Skill-specific numeric features
    Opportunities []Opportunity  // Ranked trade setups
    Narrative     Narrative      // { MarketStructure, PrimaryOpp, Warnings }
    Validation    Validation     // { Passed, Warnings }
    Conformance   Conformance    // { HasValidData, AgenticScore }
}
```

### `skill.AgentResult` (agent-ready-v2 envelope)

```json
{
  "status": "ok",
  "exitCode": 0,
  "timestamp": "2026-07-19T12:00:00Z",
  "execution": { "durationMs": 3200, "attempts": 1 },
  "agentContext": {
    "workflow": "smc",
    "modelVersion": "agent-ready-v2",
    "symbol": "BINANCE:BTCUSDT",
    "timeframe": "1h"
  },
  "market": { "lastPrice": 61306.84, "bias": "bullish" },
  "structure": { ... },
  "opportunities": [ ... ],
  "narrative": { ... },
  "conformance": { "hasValidData": true, "agenticScore": 0.75 },
  "schemaVersion": "agent-ready-v2.0.0"
}
```

---

## Adding a New Skill

### 1. Create the parser file

Path: `pkg/skill/parsers/<name>.go`

```go
package parsers

import (
    "github.com/mrme000m/tvcli/pkg/skill"
)

var MySkill = &skill.Skill{
    Name:     "my-skill",
    Synopsis: "What it does",
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

func parseMySkill(periods []map[string]any, graphic map[string]map[string]any,
    tf string, symbol string, args map[string]string) skill.SkillResult {
    // Parse periods/graphic into SkillResult
    ...
}

func formatMySkill(result skill.SkillResult) string { ... }

func init() { skill.Register(MySkill) }
```

### 2. Registration is automatic

`internal/cmd/shared.go` blank-imports `pkg/skill/parsers`, so `init()` runs automatically. No wiring needed.

### 3. Add help text

Edit `internal/cmd/help.go` to list the new skill under **Indicator Skills**.

### 4. Add tests

- Path: `pkg/skill/parsers/<name>_test.go`
- Capture fixture: `./tvcli run <pine-id> --raw-out pkg/skill/parsers/testdata/<name>_fixture.json`
- Assert on `Status`, `Market.Bias`, `Structure` keys, `Conformance.AgenticScore`

### 5. Build and verify

```bash
go build -o tvcli ./cmd/tvcli
go test ./pkg/skill/parsers -v
./tvcli my-skill --symbol BINANCE:BTCUSDT --tf 5m --agent --json
```

---

## Raw TradingView Response

Every Pine Script run returns three sections:

```json
{
  "periods": [ /* bar-by-bar plot/candle values, newest first */ ],
  "graphic": { /* drawing primitives: dwgboxes, dwglabels, dwglines, ... */ },
  "strategyReport": { /* strategies only: performance, trades, equity */ }
}
```

### periods[]

Each bar is a map with keys like `$time`, `plot_0`..`plot_N`, named fields from `metaInfo.styles`, and optional `plotcandle_0_ohlc_*` fields.

**Important:** `periods[0]` is the forming (incomplete) bar. Use `periods[1]` for the last closed bar.

### graphic{}

Drawing layers: `dwgboxes` (rectangles/zones), `dwglabels` (text labels), `dwglines` (lines), `dwgtable` (dashboards), `dwgpolylines`, `dwgpolycircles`.

### strategyReport{}

Only for strategies (`isStrategy: true`). Contains `performance.all` (netProfit, totalTrades, etc.), `trades[]`, `equity[]`.

---

## Schema / metaInfo

The script's `metaInfo` maps opaque `plot_N` keys to human-readable names:

```bash
# View schema
./tvcli run <pine-id> --symbol BTCUSDT --schema
./tvcli run <pine-id> --symbol BTCUSDT --schema --json
```

Schema contains: `isStrategy`, `isOverlay`, `plots[]` (index, title, type), `inputs[]` (id, name, type, default), `styles` (title per plot slot), `graphics` (flags for drawing layers).

### Which Inputs Matter

| Input type | Usually ignore? | Exception |
|------------|----------------|-----------|
| `color` | Yes | Transparency toggles may hide layers |
| `bool` for visibility | Yes | Bools that switch calculation mode matter |
| `isHidden`/`isFake` | Yes | Internal runtime inputs |
| Length/period/lookback | **No** | These change the formula |
| Source/offset | **No** | These change what data is processed |
| Method/select | **No** | These change the calculation |

---

## Generic `--signals` Output

The `--signals` flag produces a `pipeline.Signals` object:

```json
{
  "meta": { "pineId": "...", "symbol": "...", "timeframe": "...", "periodCount": 150 },
  "classifications": {
    "Support": "price",
    "Momentum_ROC": "metric",
    "Uptrend_Alert": "signal"
  },
  "last": { "Support": 61306.84, "Momentum_ROC": 43.47 },
  "series": [ /* last 50 chronological bars */ ],
  "levels": [ { "field": "Support", "kind": "support", "value": 61306.84 } ],
  "events": [ { "field": "dwglabels", "kind": "buy", "value": 65000 } ],
  "graphicCounts": { "dwgboxes": 12, "dwglabels": 4 },
  "bias": "neutral",
  "confidence": 0.5
}
```

### Plot Classes

| Class | Meaning | Examples |
|-------|---------|----------|
| `price` | Price-level lines/bands | Support, Resistance, EMA lines |
| `metric` | Continuous calculated values | Volatility_ATR, Momentum_ROC |
| `signal` | Sparse 0/1 or -1/0/1 flags | Uptrend_Alert, Buy_Signal |
| `histogram` | Momentum/volume histograms | buy/sell volume delta |
| `band` | Area/band plots | Bollinger bands, VWAP bands |
| `style` | Color/line-style selectors | Background_Color |
| `noise` | All-NaN, all-zero, raw colors | Ignored by default |

---

## Error Codes

| Exit | Meaning |
|------|---------|
| 0 | Success (check payload for `status`/`error`) |
| 1 | Critical error (auth, connection) |
| 2 | No data returned |
| 3 | Timeout / cancelled / validation |
| 4 | Validation error (invalid pine id, bad symbol) |

---

## Source of Truth

| Layer | Location |
|-------|----------|
| Skill framework | `pkg/skill/skill.go`, `pkg/skill/registry.go` |
| Per-skill parsers | `pkg/skill/parsers/*.go` |
| Generic extractor | `pkg/pipeline/extract.go`, `pkg/pipeline/extract_schema.go`, `pkg/pipeline/dynparse.go` |
| Schema parsing | `pkg/schema/schema.go` |
| Run orchestration | `internal/cmd/run.go`, `internal/cmd/skillcmd.go`, `internal/cmd/shared.go` |
| Script execution | `internal/service/run.go` |

---

## Gotchas

- **XAUUSD on weekends:** no bars available. Use `BTCUSDT` for testing.
- **`periods[0]` is incomplete.** Use `periods[1]` for closed-bar analysis.
- **Duplicate plot titles:** some scripts name every line "Plot". The generic extractor deduplicates; custom parsers may need `plot_N` fallback.
- **Graphics-only scripts:** if `--signals` returns empty `last`, check `graphicCounts` and add graphics extraction in `pkg/pipeline`.
