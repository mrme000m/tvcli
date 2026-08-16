# tvcli Agent System

A multi-skill market analysis agent that orchestrates multiple TradingView indicators to produce comprehensive, structured market analysis reports.

## Overview

The `tvcli agent` command runs multiple technical analysis skills (indicators) in parallel or sequence, aggregates their results, and produces unified analysis in multiple formats:

- **JSON** — Full structured data for programmatic consumption
- **Markdown** — Human-readable analysis reports
- **HTML** — Web-ready reports with styling
- **Marketing** — Social-media ready threads (Twitter/X, LinkedIn)

## Quick Start

```bash
# Run analysis with default skills (all public)
tvcli agent --symbol OANDA:XAUUSD --tf 15m --report

# Run specific skills with marketing output
tvcli agent --skills bsv,dvi,ema-atr --symbol BINANCE:BTCUSDT --tf 1h --report --format marketing

# Save JSON for programmatic use
tvcli agent --skills bsv,smc,order-flow --json --out analysis.json

# Run sequentially (useful for rate limiting)
tvcli agent --skills bsv,dvi,smc,trend --sequential --timeout 180
```

## Command Reference

### Basic Options

| Flag | Description | Default |
|------|-------------|---------|
| `--symbol` | Market symbol (e.g., `OANDA:XAUUSD`, `BINANCE:BTCUSDT`) | `OANDA:XAUUSD` |
| `--tf`, `--timeframe` | Timeframe (e.g., `5m`, `15m`, `1h`, `4h`, `1D`) | `5m` |
| `--bars` | Number of bars to analyze | `500` |
| `--skills` | Comma-separated skill names (default: all) | all |
| `--sequential` | Run skills sequentially instead of parallel | false |
| `--timeout` | Per-skill timeout in seconds | `120` |
| `--verbose` | Verbose output | false |

### Skill Configuration

| Flag | Description |
|------|-------------|
| `--preset NAME` | Global preset for all skills that have it (e.g., `scalping`, `swing`) |
| `--preset.skill=NAME` | Skill-specific preset (e.g., `--preset.cust=scalping`) |
| `--input.key=VALUE` | Global input override (e.g., `--input.atrLen=14`) |
| `--validate-inputs` | Validate inputs against skill schemas before running |
| `--list-inputs` | List available inputs for all skills and exit |

### Output Options

| Flag | Description |
|------|-------------|
| `--json` | Output full JSON |
| `--report` | Generate analysis report |
| `--format` | Report format: `markdown`, `html`, `text`, `marketing` |
| `--title` | Report title |
| `--out FILE` | Save output to file |

## Available Skills

Run `tvcli skills --json` to see all available skills with their categories and presets.

### Categories

- **scalp** — Scalping-focused indicators (e.g., `cust`)
- **smc** — Smart Money Concepts (e.g., `smc`, `ict`, `liq-sweep`, `order-flow`, `swingarm`)
- **trend** — Trend following (e.g., `trend`, `mtf`, `xau-trend`)
- **volume** — Volume-based (e.g., `bsv`, `dvi`, `vp`, `vgaps`, `anchored-vp`)
- **levels** — Support/Resistance (e.g., `sr-breaks`)
- **other** — Miscellaneous (e.g., `ema-atr`, `golden`, `quantum`, `shemar`, `sniper`, `ust`, `gold-divergence`)

### Presets

Many skills have presets for different trading styles:
- `default` — Standard settings
- `scalping` — Fast, aggressive settings
- `swing` — Longer-term settings
- `aggressive` — High sensitivity

## Output Formats

### JSON Output

Complete structured data including all skill results, summary, and metadata.

```bash
tvcli agent --skills bsv,dvi --json --out analysis.json
```

### Markdown Report

Comprehensive analysis report with:
- Executive summary
- Per-indicator analysis
- Consensus view (agreements/divergences)
- Risk assessment
- Top opportunities with entry/SL/TP

```bash
tvcli agent --skills bsv,dvi,ema-atr --report --format markdown --out report.md
```

### HTML Report

Web-ready report with embedded CSS styling.

```bash
tvcli agent --skills bsv,dvi --report --format html --out report.html
```

### Marketing Report (Social Media)

Twitter/X thread format with emojis, concise formatting, and hashtags.

```bash
tvcli agent --skills bsv,dvi,trend --report --format marketing --out thread.txt
```

**Example output:**
```
🧵 MARKET ANALYSIS THREAD

📊 OANDA:XAUUSD | 15m
🎯 Bias: Bearish | Confidence: 67%

📈 Indicators Analyzed:
🔴 bsv: Bearish
🟢 dvi: Bullish
⚪ ema-atr: Neutral

🎯 Key Opportunities:
🟢 long sr_break | Conf: 75% | R:R: 2.3
🔴 short fvg_fill | Conf: 62% | R:R: 1.8

⚠️ Watchouts:
• Price 2.1% from EMA200 — mean-reversion risk
• Low trend quality (TQI < 0.3): choppy regime

---
Generated: 2026-01-15 14:30 | tvcli agent
#Trading #TechnicalAnalysis #XAUUSD
```

## Programmatic Usage

The agent can be used as a library in your own Go code:

```go
import "github.com/ch99q/tvcli/internal/agent"

cfg := agent.AgentConfig{
    Symbol:    "BINANCE:BTCUSDT",
    Timeframe: "1h",
    Bars:      500,
    Skills:    []string{"bsv", "dvi", "trend"},
    Parallel:  true,
    Timeout:   120 * time.Second,
}

agt := agent.NewAgent(tvcliConfig, cfg)
result, err := agt.Run(context.Background())

// Access structured results
fmt.Printf("Bias Consensus: %s\n", result.Summary.BiasConsensus)
for _, opp := range result.Summary.TopOpportunities {
    fmt.Printf("%s %s @ %.2f\n", opp.Direction, opp.Setup, opp.Entry)
}

// Generate reports
markdown := agent.GenerateReport(result, agent.ReportConfig{
    Format: "markdown",
})
```

## Integration with tvcli Server

The agent system works with the `tvcli serve` HTTP server for AI agent integration:

```bash
# Start server
tvcli serve --addr :8765

# In another terminal, run agent via HTTP
curl -X POST http://localhost:8765/run \
  -H "Content-Type: application/json" \
  -d '{"source": "...", "symbol": "BINANCE:BTCUSDT", "timeframe": "1h"}'
```

## Tips

1. **Use public skills for reliability** — Private skills (USER;) require specific access
2. **Parallel mode is faster** — But may hit rate limits; use `--sequential` if needed
3. **Adjust bars for timeframe** — 5m: 500 bars (~42h), 1h: 500 bars (~21 days), 1D: 365 bars (~1 year)
4. **Presets matter** — `scalping` preset uses tighter stops, `swing` uses wider
5. **Marketing format is great for newsletters** — Pipe to social media schedulers

## Examples

### Daily Gold Analysis (Marketing)
```bash
tvcli agent \
  --skills bsv,dvi,ema-atr,sr-breaks,trend \
  --symbol OANDA:XAUUSD \
  --tf 1h \
  --bars 500 \
  --report \
  --format marketing \
  --title "Daily Gold Analysis" \
  --out gold_daily_$(date +%Y%m%d).txt
```

### Crypto Scalping Setup (JSON)
```bash
tvcli agent \
  --skills cust,liq-sweep,order-flow \
  --symbol BINANCE:BTCUSDT \
  --tf 5m \
  --bars 300 \
  --preset scalping \
  --json \
  --out btc_scalp_$(date +%H%M).json
```

### Multi-Timeframe Swing Analysis (Markdown)
```bash
tvcli agent \
  --skills trend,mtf,xau-trend,golden \
  --symbol OANDA:XAUUSD \
  --tf 4h \
  --bars 300 \
  --report \
  --format markdown \
  --out xau_swing_analysis.md
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "study limit hit" | Use `--sequential` or increase `--timeout` |
| "skill not found" | Run `tvcli skills` to see available names |
| "private script" | Use `--allow-private` or stick to PUB; skills |
| "no data" | Increase `--bars` or check symbol/timeframe validity |
| Input warnings | Use TV input IDs (in_0, in_1) via `--input.in_0=value` |

## Architecture

```
tvcli agent
    ├── AgentConfig (symbol, timeframe, skills, presets, inputs)
    ├── Agent (orchestrates parallel/sequential execution)
    │   ├── skill registry (20+ built-in skills)
    │   ├── service.RunScript (WebSocket + Pine Facade)
    │   └── result aggregation
    ├── SkillResult (per-skill output)
    ├── AgentResult (aggregated + summary)
    └── Report Generator (markdown/html/marketing/text)

tvcli analyze / tvcli eval --agent  (any unknown Pine script)
    ├── UniversalAnalyzer
    │   ├── Layer 1: pipeline.Extract (flat signal extraction from all draw types)
    │   ├── Layer 2: graphics_generic.go (topology-based structural analysis)
    │   └── Agent-ready v2 envelope
    └── No per-script matchers needed — topology rules are geometric universals
```

### Two-Layer Generic Graphics Design

The universal analyzer (`tv analyze`, `tv eval --agent`) uses a **two-layer
generic design** that handles any Pine script's graphics without per-script
matchers:

| Layer | Location | Role |
|-------|----------|------|
| **Layer 1: Flat signal extraction** | `pkg/pipeline/extract.go` | Universal handlers for every TV draw type (boxes→S/R, lines→levels, labels→events, tables→grids, hhists→volume bins). Zero script-specific code. |
| **Layer 2: Structural topology analysis** | `internal/agent/graphics_generic.go` | Groups elements by geometric topology (shared edges, width, extension, style) and infers semantics from group properties. Detects POC/VAH/VAL, order blocks, FVGs, breaker blocks, liquidity levels, session markers. |

**Per-script parsers** in `internal/skill/parsers/` remain only for **registered
skills** where exact Pine field names and plot semantics are known (e.g., SMC's
`Bullish_BOS` field, VP's `POC`/`VAH`/`VAL` plot values). For unknown/arbitrary
scripts, the generic topology approach handles any arrangement without
per-script code.

The agent leverages the existing skill system, so every skill's parser,
presets, and agent-ready output format are automatically available.