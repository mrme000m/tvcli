# Universal Pine Script Analyzer

The `tvcli analyze` command (alias: `universal`, `ua`) automatically analyzes **ANY** Pine Script indicator or strategy without requiring a custom parser. It extracts signals, levels, and graphics semantically — identifying order blocks, fair value gaps (FVG), volume profiles, liquidity zones, support/resistance, and more.

## Quick Start

```bash
# Analyze any public script by Pine ID
tvcli analyze "PUB;aea729456b7a44e09661b70ce9e4e987" --symbol OANDA:XAUUSD --tf 1h

# With marketing report (Twitter/X thread format)
tvcli analyze "PUB;ff639e15f24646fbaf19ae22ac663140" --report --format marketing

# Full JSON for programmatic use
tvcli analyze "PUB;fVSb3j0I87LvTzPKrQTY5hDUEdsGdnm6" --json --out analysis.json

# Positional argument also works
tvcli analyze PUB;09ebff5ba23c452b89ea82522f2aab35 --tf 15m --bars 200
```

## Command Reference

### Required
| Argument | Description |
|----------|-------------|
| `<pineId>` or `--pine <pineId>` | Pine script ID (e.g., `PUB;abc123`) |

### Market Options
| Flag | Default | Description |
|------|---------|-------------|
| `--symbol` | `OANDA:XAUUSD` | Market symbol |
| `--tf`, `--timeframe` | `5m` | Timeframe |
| `--bars` | `500` | Number of bars |

### Input Overrides
| Flag | Description |
|------|-------------|
| `--input.key=VALUE` | Override script inputs (e.g., `--input.lookback=50`) |
| `--settle` | `1500` | Settle time in ms |
| `--timeout` | `120` | Timeout in seconds |
| `--force-schema` | false | Re-fetch schema from TradingView |

### Output Options
| Flag | Description |
|------|-------------|
| `--json` | Output full JSON |
| `--report` | Generate analysis report |
| `--format` | `markdown`, `html`, `marketing`, `text` (default: markdown) |
| `--title` | Report title |
| `--out FILE` | Save output to file |
| `--verbose` | Verbose output |

## Supported Graphic Types (Auto-Detected)

The analyzer examines TradingView's graphic draw types and classifies them:

| Graphic Type | Detected As | Confidence |
|--------------|-------------|------------|
| `dwgboxes` with FVG text | **Fair Value Gap (FVG)** | 90% |
| `dwgboxes` narrow (1-6 bars), gap-like | **FVG (heuristic)** | 70% |
| `dwgboxes` with OB/Order Block text | **Order Block** | 90% |
| `dwgboxes` with Liquidity/Buyside/Sellside text | **Liquidity** | 90% |
| `dwgboxes` small, stacked, no text | **Volume Profile** | 80% |
| `dwgboxes` wide, tall | **Session** | 70% |
| `dwglines` horizontal | **Support/Resistance/Band** | 70-90% |
| `dwglines` sloped | **Trendline** | 70% |
| `dwglabels` with BUY/LONG/BULL | **Buy Signal** | 90% |
| `dwglabels` with SELL/SHORT/BEAR | **Sell Signal** | 90% |
| `dwglabels` with BOS/CHOCH | **Structure Break** | 90% |
| `dwglabels` with POC/VAH/VAL | **Volume Profile Levels** | 90% |
| `dwgtables` with Timeframe/Trend | **Dashboard** | - |
| `hhists` | **Volume Profile Histogram** | - |

## Output Formats

### Text (Default)
Human-readable terminal output with sections for:
- Script info (name, version, type, plots, inputs, graphic types)
- Market data (symbol, timeframe, last price, bars)
- Signal summary (bias, confidence, events, levels, warnings)
- Graphic analysis (counts by type, inferred types)
- Key levels (price, kind, strength, source, active status)
- Recent signals
- Patterns detected
- Risk metrics (ATR, distances, R:R)
- Recommendations
- Agent-ready envelope summary

### Markdown Report (`--report --format markdown`)
Professional analysis report with:
- Executive summary
- Detected graphic types table
- Key levels table with active status
- Recent signals table
- Detected patterns
- Risk metrics
- Recommendations
- Top opportunities (agent-ready)
- Warnings

### HTML Report (`--report --format html`)
Web-ready report with embedded CSS styling.

### Marketing Report (`--report --format marketing`)
Twitter/X thread format with emojis:
```
🧵 UNIVERSAL ANALYSIS THREAD

📊 OANDA:XAUUSD | 1h via PUB;ff639e15f24646fbaf19ae22ac663140
🎯 Bias: Long | Confidence: 100%

🎨 Graphic Elements:
  • fvg: 62
  • volume_profile: 89

🎯 Active Levels:
🟢 fvg_high: 4043.49
🟢 fvg_low: 4037.80
🟢 fvg_high: 4050.32

---
Generated: 2026-08-16 10:01 | tvcli universal
#Trading #TechnicalAnalysis #OANDAXAUUSD
```

### JSON Output (`--json`)
Complete structured data for programmatic consumption:
```json
{
  "script": { "pineId": "...", "name": "...", "version": "5.0", ... },
  "market": { "symbol": "...", "lastPrice": 4380.55, "priceSource": "graphic", ... },
  "signals": { "bias": "long", "confidence": 1.0, "events": 30, "levels": 6, ... },
  "graphic": { "boxes": 151, "lines": 0, "inferredTypes": { "fvg": 62, "volume_profile": 89 }, ... },
  "summary": { "bias": "long", "keyLevels": [...], "signals": [...], "patterns": [...], ... },
  "agent": { "status": "ok", "opportunities": [...], "conformance": { "agenticScore": 1.0 }, ... }
}
```

## Programmatic Usage

```go
import "github.com/ch99q/tvcli/internal/agent"

cfg := agent.UniversalAnalyzerConfig{
    Symbol:    "BINANCE:BTCUSDT",
    Timeframe: "1h",
    Bars:      500,
    Inputs:    map[string]string{"lookback": "50"},
    Timeout:   120 * time.Second,
}

analyzer := agent.NewUniversalAnalyzer(tvcliConfig, cfg)
result, err := analyzer.Analyze(context.Background(), "PUB;abc123")

// Access structured results
fmt.Printf("Bias: %s (%.0f%%)\n", result.Signals.Bias, result.Signals.Confidence*100)
for _, lvl := range result.Summary.KeyLevels {
    if lvl.IsActive {
        fmt.Printf("Active %s: %.2f\n", lvl.Kind, lvl.Price)
    }
}

// Generate reports
markdown := agent.GenerateUniversalReport(result, agent.ReportConfig{Format: "marketing"})
```

## How It Works

1. **Fetch Schema** — Gets script metadata (plots, inputs, version) from Pine Facade
2. **Run Script** — Executes via TradingView WebSocket, capturing periods + graphics
3. **Extract Signals** — Uses pipeline to classify plots (price, signal, metric, noise)
4. **Analyze Graphics** — Deep semantic analysis of `dwgboxes`, `dwglines`, `dwglabels`, `dwgtables`, `hhists`
5. **Infer Types** — Heuristics classify graphics as FVG, Order Block, Liquidity, Volume Profile, etc.
6. **Build Summary** — Aggregates levels, signals, patterns, risk metrics
7. **Generate Output** — Text, Markdown, HTML, Marketing, or JSON

## Examples

### Order Block Analysis
```bash
tvcli analyze "PUB;fVSb3j0I87LvTzPKrQTY5hDUEdsGdnm6" \
  --symbol BINANCE:BTCUSDT --tf 1h --bars 200 \
  --report --format markdown --out ob_analysis.md
```

### FVG Scalping
```bash
tvcli analyze "PUB;ff639e15f24646fbaf19ae22ac663140" \
  --symbol BINANCE:BTCUSDT --tf 5m --bars 200 \
  --report --format marketing --out fvg_thread.txt
```

### Volume Profile Levels
```bash
tvcli analyze "PUB;aea729456b7a44e09661b70ce9e4e987" \
  --symbol OANDA:XAUUSD --tf 4h --bars 500 \
  --json --out vp_levels.json
```

### Liquidity Sweeps
```bash
tvcli analyze "PUB;09ebff5ba23c452b89ea82522f2aab35" \
  --symbol OANDA:XAUUSD --tf 15m --bars 300 \
  --report --format html --out liquidity_report.html
```

### Custom Inputs
```bash
tvcli analyze "PUB;abc123" \
  --input.length=20 --input.mult=2.5 --input.src=close \
  --symbol BINANCE:ETHUSDT --tf 1h
```

## Tips

1. **Quote Pine IDs** — Use quotes: `"PUB;abc123"` to avoid shell issues with `;`
2. **Public scripts work best** — `PUB;` scripts run on any tier; `USER;` scripts need access
3. **Price from graphics** — When periods are empty (0 bars), price is extracted from graphic price range
4. **Filter noise** — Colorer/style values (1, 2, 0, -1) are automatically filtered from signals
5. **Active levels** — Levels within 2% of current price are marked 🟢 ACTIVE

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "study limit hit" | Use `--sequential` (not available yet) or wait |
| "private script" | Use public `PUB;` scripts or ensure access |
| "no periods received" | Script only outputs graphics; price comes from graphic data |
| Wrong FVG detection | Script may not use standard FVG text; relies on heuristics |
| Price is 0 | No periods and no graphic price data; check symbol/timeframe |