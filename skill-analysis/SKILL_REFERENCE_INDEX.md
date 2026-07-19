# TradingView Skill Reference Index — Go Skill Commands

**Generated:** 2026-07-19  
**Go parsers:** `/Volumes/ExMac/code/tradingview/go/internal/skill/parsers`  
**Dumps:** `skill-analysis/dumps/<skill>/` (`payload.json` plus historical JS runner `stdout.txt`, `stderr.txt`, `help.txt`)  
**Meta:** `skill-analysis/meta/<skill>.json`

Use this index to fix and verify Go skill commands. For each Go skill command (`tv <name>`),
this doc shows the Pine Script ID, the Go parser file, the captured reference payload,
and concrete discrepancies between the captured reference and the current Go parser output.

**New:** before editing a custom parser, try the generic schema-guided extractor:

```bash
./tvcli <skill> --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json
```

If this produces a usable agent-ready payload, the hand-coded parser may not need fixing.

See:

- [`PINE_RESPONSE_SKILL.md`](PINE_RESPONSE_SKILL.md) for raw response anatomy, schema usage, and input filtering.
- [`PARSING_PROTOCOL_FOR_GO.md`](PARSING_PROTOCOL_FOR_GO.md) for skill command invocation and the `--signals` path.
- [`README.md`](README.md) for an overview of this workspace.

The historical JS runner files (`/Volumes/ExMac/code/tradingview/js-experiment06/*.cjs`)
were used only as loose reference material during the initial port; they are not the source of truth.

---

## 1. Quick Reference Table

| Go command | Pine Script name | Pine ID | Go parser file | Dump directory | Reference payload |
|------------|------------------|---------|----------------|----------------|-------------------|
| `tv anchored-vp` | `anchored-clusters-vp` | `PUB;92974e0a3cfb481eaf058cdab9f925a3` | `internal/skill/parsers/anchored_vp.go` | `skill-analysis/dumps/anchored-clusters-vp/` | 19 keys |
| `tv bsv` | `buying-selling-volume` | `PUB;28a4da159ce246dab2cb6524c25f950f` | `internal/skill/parsers/bsv.go` | `skill-analysis/dumps/buying-selling-volume/` | 16 keys |
| `tv dvi` | `delta-volume-intensity` | `PUB;bdd3bc54cf9f4dc6b42e6b2879b4eed2` | `internal/skill/parsers/dvi.go` | `skill-analysis/dumps/delta-volume-intensity/` | 15 keys |
| `tv ema-atr` | `ema-atr-pro-engine` | `PUB;7d5f8755ab67400899ef73a9898471e4` | `internal/skill/parsers/ema_atr.go` | `skill-analysis/dumps/ema-atr-pro-engine/` | 13 keys |
| `tv golden` | `golden-rule-strategy` | `PUB;6daafb2cabe6419d98ae25229d2327f8` | `internal/skill/parsers/golden.go` | `skill-analysis/dumps/golden-rule-strategy/` | 10 keys |
| `tv ict` | `ict-auto-validated-smc` | `PUB;789a5c79bfe9443585da09e85ece73de` | `internal/skill/parsers/ict.go` | `skill-analysis/dumps/ict-auto-validated-smc/` | 14 keys |
| `tv mtf` | `xauusd-mtf-trend` | `PUB;d1ad30c0261f49f297357f8aa2a7854a` | `internal/skill/parsers/mtf.go` | `skill-analysis/dumps/xauusd-mtf-trend/` | 11 keys |
| `tv quantum` | `quantum-ribbon` | `PUB;91e003af510345f299e5846773538206` | `internal/skill/parsers/quantum.go` | `skill-analysis/dumps/quantum-ribbon/` | 13 keys |
| `tv shemar` | `shemar-smc-confidence` | `PUB;70f6e4e05f9c439c9d1f8fe26019357e` | `internal/skill/parsers/shemar.go` | `skill-analysis/dumps/shemar-smc-confidence/` | 14 keys |
| `tv smc` | `smart-money-concepts` | `PUB;6daafb2cabe6419d98ae25229d2327f8` | `internal/skill/parsers/smc.go` | `skill-analysis/dumps/smart-money-concepts/` | 14 keys |
| `tv sniper` | `precision-sniper` | `PUB;1fc29950178c42a1a88f52a18161dd53` | `internal/skill/parsers/sniper.go` | `skill-analysis/dumps/precision-sniper/` | 15 keys |
| `tv sr-breaks` | `support-resistance-breaks` | `PUB;NXS6SoOdr880Hrvh9vA36UcAjC14bOkc` | `internal/skill/parsers/sr_breaks.go` | `skill-analysis/dumps/support-resistance-breaks/` | 13 keys |
| `tv swingarm` | `swingarm-atr-trend-indicator` | `PUB;GdkmXaTINI8knwuCrctQD1pB5dFaRnyr` | `internal/skill/parsers/swingarm.go` | `skill-analysis/dumps/swingarm-atr-trend-indicator/` | 9 keys |
| `tv trend` | `self-aware-trend-system` | `PUB;0f80bcf05d544d4c98fde06faab1c976` | `internal/skill/parsers/trend.go` | `skill-analysis/dumps/self-aware-trend-system/` | 15 keys |
| `tv ust` | `ultra-sensitive-supertrend` | `PUB;fc33f2d98699414a8585923116dbd959` | `internal/skill/parsers/ust.go` | `skill-analysis/dumps/ultra-sensitive-supertrend/` | 13 keys |
| `tv vgaps` | `volume-gaps-imbalances-zeiierman` | `PUB;ff1a0136336340f38e908eeb12ea33aa` | `internal/skill/parsers/vgaps.go` | `skill-analysis/dumps/volume-gaps-imbalances-zeiierman/` | 17 keys |

---

## 2. Per-Skill Detail

### 2.1 `tv anchored-vp` — `anchored-clusters-vp`

- **Synopsis:** Anchored Volume Profile — k-means clusters and POC levels
- **Pine ID:** `PUB;92974e0a3cfb481eaf058cdab9f925a3`  (reference Pine ID: `PUB;92974e0a3cfb481eaf058cdab9f925a3`)
- **Workflow ID:** `anchored-clusters-vp`  (captured reference: `anchored-clusters-vp`)
- **Go parser:** `internal/skill/parsers/anchored_vp.go`  → func `parseAnchoredVP` / format `formatAnchoredVP`
- **Historical JS runner:** `/Volumes/ExMac/code/tradingview/js-experiment06/anchored-clusters-vp.cjs`
- **Historical sample command:** `node anchored-clusters-vp.cjs BTCUSDT --tf 1h --bars 300 --agent --json`
- **Captured reference run:** rc=0 parsed_via=bracket hint=anchored-clusters-vp
- **Reference payload top-level keys (19):** `agentContext, clusters, conformance, currentBar, dotLabels, execution, exitCode, latest, narrative, opportunities, pocLabels, pocLevels, profile, recentBars, schemaVersion, signals, status, summary, timestamp`

#### Inputs (Go)

| name | tv_input_id | type | default |
|------|-------------|------|---------|
| `kInput` | `in_3` | int | `5` |
| `iters` | `in_4` | int | `50` |
| `rowsInput` | `in_5` | int | `20` |
| `vpWidth` | `in_6` | int | `40` |
| `showDots` | `in_8` | bool | `true` |

#### Inputs (historical JS reference — INPUT_MAP)

| variable | tvInputId | type | default |
|----------|-----------|------|---------|
| `startTime` | `in_0` | `time` | `1704067200000` |
| `endTime` | `in_1` | `time` | `1735689600000` |
| `rangeColor` | `in_2` | `color` | `color.new(#607d8b, 90)` |
| `kInput` | `in_3` | `int` | `5` |
| `iters` | `in_4` | `int` | `50` |
| `rowsInput` | `in_5` | `int` | `20` |
| `vpWidth` | `in_6` | `int` | `40` |
| `vpOffset` | `in_7` | `int` | `10` |
| `showDots` | `in_8` | `bool` | `true` |
| `dotSizeInput` | `in_9` | `string` | `size.small` |

#### Input Comparison

| Go name | Go TV id | Reference variable | Reference tvInputId | Name match | TV id match |
|---------|----------|-------------|--------------|------------|-------------|
| `kInput` | `in_3` | `kInput` | `in_3` | OK | OK |
| `iters` | `in_4` | `iters` | `in_4` | OK | OK |
| `rowsInput` | `in_5` | `rowsInput` | `in_5` | OK | OK |
| `vpWidth` | `in_6` | `vpWidth` | `in_6` | OK | OK |
| `showDots` | `in_8` | `showDots` | `in_8` | OK | OK |
| — | — | `startTime` | `in_0` | **MISSING in Go** | — |
| — | — | `endTime` | `in_1` | **MISSING in Go** | — |
| — | — | `rangeColor` | `in_2` | color (cosmetic, safe to omit) | — |
| — | — | `vpOffset` | `in_7` | **MISSING in Go** | — |
| — | — | `dotSizeInput` | `in_9` | **MISSING in Go** | — |

#### Go parser reads from periods[] (getField aliases)

`Close`, `POC`, `PointOfControl`, `close`, `poc`

#### Go Structure keys produced

`bias`, `poc`, `price`

#### Reference payload schema (from captured dump)

Dump: `skill-analysis/dumps/anchored-clusters-vp/payload.json`

| key | type/shape |
|-----|-----------|
| `status` | `str` |
| `exitCode` | `int` |
| `timestamp` | `str` |
| `execution` | `object{durationMs,attempts}` |
| `agentContext` | `object{workflow,modelVersion,symbol,timeframe,htfTimeframe}` |
| `profile` | `object{totalClusters,priceRange,clusterDensity,avgClusterHeight,volumeWeightedPOC}` |
| `latest` | `object{cluster,poc,totalVolumeLabel}` |
| `clusters` | `list[object{top,bottom,left,right,borderColor}]` |
| `pocLabels` | `list[object{text,price,x,volume,isTotal}]` |
| `pocLevels` | `list[object{price,color,style,width}]` |
| `dotLabels` | `list[object{price,x,color}]` |
| `opportunities` | `list[object{rank,setup,direction,confidence,confluenceScore}]` |
| `narrative` | `object{marketStructure,primaryOpportunity,warnings,watchlist}` |
| `conformance` | `object{hasValidData,agenticScore}` |
| `schemaVersion` | `str` |
| `summary` | `object{totalClusters,totalLabels,totalLines,totalTables,avgClusterHeight,priceRange...}` |
| `currentBar` | `object{top,bottom,left,right,borderColor,width}` |
| `recentBars` | `list[object{top,bottom,left,right,borderColor}]` |
| `signals` | `list[object{rank,setupType,direction,confluenceScore,confidence}]` |

#### Go `SkillResult` schema (target output)

| field | type |
|-------|------|
| `status` | string |
| `workflow` | string |
| `market.lastPrice` | any |
| `market.bias` | string |
| `structure` | map[string]any (per-skill keys above) |
| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |
| `narrative.marketStructure` | string |
| `narrative.primaryOpportunity` | string |
| `narrative.warnings[]` | string |
| `narrative.watchlist[]` | string |
| `validation.passed` | bool |
| `validation.warnings[]` | string |
| `conformance.hasValidData` | bool |
| `conformance.agenticScore` | float64 |
| `raw` | map (omitted in agent output) |

#### Discrepancies & fix recommendations

- **Historical JS input `startTime` (`in_0`) missing in Go.** Consider adding `InputDef{Name: "startTime", TVInputID: "in_0", Type: "time", Default: 1704067200000}`.
- **Historical JS input `endTime` (`in_1`) missing in Go.** Consider adding `InputDef{Name: "endTime", TVInputID: "in_1", Type: "time", Default: 1735689600000}`.
- **Historical JS input `rangeColor` (`in_2`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `vpOffset` (`in_7`) missing in Go.** Consider adding `InputDef{Name: "vpOffset", TVInputID: "in_7", Type: "int", Default: 10}`.
- **Historical JS input `dotSizeInput` (`in_9`) missing in Go.** Consider adding `InputDef{Name: "dotSizeInput", TVInputID: "in_9", Type: "string", Default: size.small}`.
- **Reference payload has rich keys not in Go SkillResult:** `clusters, currentBar, dotLabels, latest, pocLabels, pocLevels, profile, recentBars, signals, summary`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.

#### Captured `--help` output

```
◇ injected env (13) from .env // tip: ⌘ enable debugging { debug: true }

Anchored Clusters Volume Profile — Standalone Runner
Usage: node anchored-clusters-vp.cjs <SYMBOL> [options]
Options: --tf, --bars, --input key=value, --json, --agent, --out, --verbose, --dry-run, --help
Inputs: startTime, endTime, rangeColor, kInput, iters, rowsInput, vpWidth, vpOffset, showDots, dotSizeInput
```

#### Original SKILL.md excerpt (first 1500 chars)

```markdown
---
name: anchored-clusters-vp
description: |
  Use the Anchored Clusters Volume Profile TradingView indicator to analyze volume distribution, identify Point of Control (POC) levels, and detect cluster extremes for structural trade setups.
version: 1.0.0
license: MIT
author: TradingView Pine Skills
compatibility: Node.js 18+ with tv-optimized.cjs, tv.cjs, agent-output.cjs and .env (SESSION, SIGNATURE) at project root
metadata:
  hermes:
    tags: [trading, tradingview, pine-script, volume-profile, poc]
    category: trading
required_environment_variables:
  - name: SESSION
    prompt: TradingView session cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid
    required_for: full functionality
  - name: SIGNATURE
    prompt: TradingView signature cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid_sign
    required_for: full functionality
---

# Anchored Clusters Volume Profile — Trading Opportunity Finder

## When to Use

Helps the user run the standalone `anchored-clusters-vp.cjs` script against any TradingView symbol and timeframe, then interprets the structured output to surface high-probability trading setups based on volume cluster analysis. The output includes:

- **Cluster Distribution** — price-by-price volume blocks showing where liquidity concentrated
- **POC Levels** — Point of Control prices where the most volume traded
- **Cluster Extremes** — range highs and lows tha
```

---

### 2.2 `tv bsv` — `buying-selling-volume`

- **Synopsis:** Buy/Sell Volume analysis with MA crossovers
- **Pine ID:** `PUB;28a4da159ce246dab2cb6524c25f950f`  (reference Pine ID: `PUB;28a4da159ce246dab2cb6524c25f950f`)
- **Workflow ID:** `buying-selling-volume`  (captured reference: `buying-selling-volume`)
- **Go parser:** `internal/skill/parsers/bsv.go`  → func `parseBSV` / format `formatBSV`
- **Historical JS runner:** `/Volumes/ExMac/code/tradingview/js-experiment06/buying-selling-volume.cjs`
- **Historical sample command:** `node buying-selling-volume.cjs BTCUSDT --preset scalping --tf 5m --bars 300 --agent --json`
- **Captured reference run:** rc=0 parsed_via=bracket hint=buying-selling-volume
- **Reference payload top-level keys (16):** `agentContext, conformance, currentBar, execution, exitCode, latestBars, narrative, opportunities, recentBars, recentCrosses, schemaVersion, signals, status, summary, timestamp, volume`

#### Inputs (Go)

| name | tv_input_id | type | default |
|------|-------------|------|---------|
| `lengthMA1` | `in_0` | int | `10` |
| `lengthMA2` | `in_1` | int | `10` |
| `maType` | `in_2` | string | `"SMA"` |

#### Inputs (historical JS reference — INPUT_MAP)

| variable | tvInputId | type | default |
|----------|-----------|------|---------|
| `lengthMA1` | `in_0` | `int` | `10` |
| `lengthMA2` | `in_1` | `int` | `10` |
| `maType` | `in_2` | `string` | `SMA` |

#### Input Comparison

| Go name | Go TV id | Reference variable | Reference tvInputId | Name match | TV id match |
|---------|----------|-------------|--------------|------------|-------------|
| `lengthMA1` | `in_0` | `lengthMA1` | `in_0` | OK | OK |
| `lengthMA2` | `in_1` | `lengthMA2` | `in_1` | OK | OK |
| `maType` | `in_2` | `maType` | `in_2` | OK | OK |

#### Presets

| preset | go | js | match |
|--------|----|----|-------|
| `default` | `{'lengthMA1': '10', 'lengthMA2': '10'}` | `{'lengthMA1': 10, 'lengthMA2': 10, 'maType': 'SMA'}` | OK js-only:{'maType'} |
| `scalping` | `{'lengthMA1': '9', 'lengthMA2': '21'}` | `{'lengthMA1': 9, 'lengthMA2': 21, 'maType': 'EMA'}` | OK js-only:{'maType'} |
| `swing` | `{'lengthMA1': '50', 'lengthMA2': '200'}` | `{'lengthMA1': 50, 'lengthMA2': 200, 'maType': 'SMA'}` | OK js-only:{'maType'} |

#### Go parser reads from periods[] (getField aliases)

`Background Color`, `BackgroundColor`, `BuyVolume`, `Close`, `SellVolume`, `backgroundColor`, `buyVolume`, `close`, `sellVolume`

#### Go Structure keys produced

`bgConsensus`, `buyDominant`, `dominanceRatio`, `neutral`, `recentCrosses`, `sellDominant`, `totalBars`

#### Reference payload schema (from captured dump)

Dump: `skill-analysis/dumps/buying-selling-volume/payload.json`

| key | type/shape |
|-----|-----------|
| `status` | `str` |
| `exitCode` | `int` |
| `timestamp` | `str` |
| `execution` | `object{durationMs,attempts}` |
| `agentContext` | `object{workflow,modelVersion,symbol,timeframe,htfTimeframe}` |
| `volume` | `object{buyDominant,sellDominant,neutral,dominanceRatio,bgConsensus,recentCrosses}` |
| `latestBars` | `list[object{time,close,buyVolume,sellVolume,barState}]` |
| `recentCrosses` | `list[object{time,type,price}]` |
| `opportunities` | `list[any]` |
| `summary` | `object{totalBars,buyDominant,sellDominant,neutral,dominanceRatio,bgConsensus...}` |
| `currentBar` | `object{time,barIndex,buyVolume,sellVolume,buyVolumeRaw,sellVolumeRaw...}` |
| `recentBars` | `list[object{time,barIndex,buyVolume,sellVolume,buyVolumeRaw}]` |
| `signals` | `list[any]` |
| `narrative` | `object{marketStructure,primaryOpportunity,warnings,watchlist}` |
| `conformance` | `object{hasValidData,agenticScore}` |
| `schemaVersion` | `str` |

#### Go `SkillResult` schema (target output)

| field | type |
|-------|------|
| `status` | string |
| `workflow` | string |
| `market.lastPrice` | any |
| `market.bias` | string |
| `structure` | map[string]any (per-skill keys above) |
| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |
| `narrative.marketStructure` | string |
| `narrative.primaryOpportunity` | string |
| `narrative.warnings[]` | string |
| `narrative.watchlist[]` | string |
| `validation.passed` | bool |
| `validation.warnings[]` | string |
| `conformance.hasValidData` | bool |
| `conformance.agenticScore` | float64 |
| `raw` | map (omitted in agent output) |

#### Discrepancies & fix recommendations

- **Reference payload has rich keys not in Go SkillResult:** `currentBar, latestBars, recentBars, recentCrosses, signals, summary, volume`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.

#### Captured `--help` output

```
Buying Selling Volume — Standalone Runner
Usage: node buying-selling-volume.cjs <SYMBOL> [options]
Options: --tf, --bars, --input key=value, --preset (scalping|default|swing), --json, --agent, --out, --verbose, --dry-run, --help
Inputs: lengthMA1, lengthMA2, maType
```

#### Original SKILL.md excerpt (first 1500 chars)

```markdown
---
name: buying-selling-volume
description: |
  Use the Buying Selling Volume TradingView indicator to analyze volume pressure, detect buying vs selling dominance, and identify MA cross signals for directional trade setups.
version: 1.0.0
license: MIT
author: TradingView Pine Skills
compatibility: Node.js 18+ with tv-optimized.cjs, tv.cjs, agent-output.cjs and .env (SESSION, SIGNATURE) at project root
metadata:
  hermes:
    tags: [trading, tradingview, pine-script, volume-pressure, ma-cross]
    category: trading
required_environment_variables:
  - name: SESSION
    prompt: TradingView session cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid
    required_for: full functionality
  - name: SIGNATURE
    prompt: TradingView signature cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid_sign
    required_for: full functionality
---

# Buying Selling Volume — Trading Opportunity Finder

## When to Use

Helps the user run the standalone `buying-selling-volume.cjs` script against any TradingView symbol and timeframe, then interprets the structured output to surface high-probability trading setups based on volume pressure analysis. The output includes:

- **Buy/Sell Volume** — per-bar volume decomposition into buying and selling pressure
- **MA Cross Detection** — background color transitions signaling trend changes
- **Volume Bias** — dominant pressure over recent bars (bullish/bear
```

---

### 2.3 `tv dvi` — `delta-volume-intensity`

- **Synopsis:** Delta Volume Intensity — trend, S/R, momentum
- **Pine ID:** `PUB;bdd3bc54cf9f4dc6b42e6b2879b4eed2`  (reference Pine ID: `PUB;bdd3bc54cf9f4dc6b42e6b2879b4eed2`)
- **Workflow ID:** `trend-following-sr-break`  (captured reference: `trend-following-sr-break`)
- **Go parser:** `internal/skill/parsers/dvi.go`  → func `parseDVI` / format `formatDVI`
- **Historical JS runner:** `/Volumes/ExMac/code/tradingview/js-experiment06/delta-volume-intensity.cjs`
- **Historical sample command:** `node delta-volume-intensity.cjs BTCUSDT --tf 1h --bars 300 --agent --json`
- **Captured reference run:** rc=0 parsed_via=bracket hint=trend-following-sr-break
- **Reference payload top-level keys (15):** `agentContext, conformance, currentBar, execution, exitCode, market, narrative, recentBars, schemaVersion, signals, status, structure, summary, timestamp, validation`

#### Inputs (Go)

| name | tv_input_id | type | default |
|------|-------------|------|---------|
| `length_volatility` | `in_0` | int | `14` |
| `length_momentum` | `in_1` | int | `14` |
| `lookback_sr` | `in_2` | int | `7` |

#### Inputs (historical JS reference — INPUT_MAP)

| variable | tvInputId | type | default |
|----------|-----------|------|---------|
| `length_volatility` | `in_0` | `int` | `14` |
| `length_momentum` | `in_1` | `int` | `14` |
| `lookback_sr` | `in_2` | `int` | `7` |

#### Input Comparison

| Go name | Go TV id | Reference variable | Reference tvInputId | Name match | TV id match |
|---------|----------|-------------|--------------|------------|-------------|
| `length_volatility` | `in_0` | `length_volatility` | `in_0` | OK | OK |
| `length_momentum` | `in_1` | `length_momentum` | `in_1` | OK | OK |
| `lookback_sr` | `in_2` | `lookback_sr` | `in_2` | OK | OK |

#### Go parser reads from periods[] (getField aliases)

`ATR`, `Close`, `Momentum`, `ROC`, `Trend`, `TrendLine`, `Volatility`, `close`, `momentum`, `trend`, `volatility`

#### Go Structure keys produced

`momentum`, `trend`, `trendDown`, `trendUp`, `volatility`

#### Reference payload schema (from captured dump)

Dump: `skill-analysis/dumps/delta-volume-intensity/payload.json`

| key | type/shape |
|-----|-----------|
| `status` | `str` |
| `exitCode` | `int` |
| `timestamp` | `str` |
| `execution` | `object{durationMs,attempts}` |
| `agentContext` | `object{workflow,modelVersion,symbol,timeframe}` |
| `market` | `object{lastPrice,bias,dominantFlow,regime}` |
| `structure` | `object{trend,volatility,momentum,srBreaks}` |
| `signals` | `list[any]` |
| `narrative` | `object{marketStructure,primaryOpportunity,warnings,watchlist}` |
| `validation` | `object{checks}` |
| `conformance` | `object{hasValidStructure,hasDirectionalImpulse,agenticScore}` |
| `schemaVersion` | `str` |
| `summary` | `object{totalBars,uptrendBars,downtrendBars,sidewaysBars,trendConsensus,bgConsensus...}` |
| `currentBar` | `object{support,resistance,atr,roc,trend,backgroundTrend...}` |
| `recentBars` | `list[object{support,resistance,atr,roc,trend}]` |

#### Go `SkillResult` schema (target output)

| field | type |
|-------|------|
| `status` | string |
| `workflow` | string |
| `market.lastPrice` | any |
| `market.bias` | string |
| `structure` | map[string]any (per-skill keys above) |
| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |
| `narrative.marketStructure` | string |
| `narrative.primaryOpportunity` | string |
| `narrative.warnings[]` | string |
| `narrative.watchlist[]` | string |
| `validation.passed` | bool |
| `validation.warnings[]` | string |
| `conformance.hasValidData` | bool |
| `conformance.agenticScore` | float64 |
| `raw` | map (omitted in agent output) |

#### Discrepancies & fix recommendations

- **Reference payload has rich keys not in Go SkillResult:** `currentBar, recentBars, signals, summary`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.

#### Captured `--help` output

```
Delta Volume Intensity — Standalone Runner
Usage: node delta-volume-intensity.cjs <SYMBOL> [options]
Options: --tf, --bars, --input key=value, --json, --agent, --out, --verbose, --dry-run, --help
Inputs: length_volatility, length_momentum, lookback_sr
```

#### Original SKILL.md excerpt (first 1500 chars)

```markdown
---
name: delta-volume-intensity
description: |
  Use the Delta Volume Intensity TradingView indicator to analyze trend direction, support/resistance levels, and rate-of-change momentum for structural trade setups.
version: 1.0.0
license: MIT
author: TradingView Pine Skills
compatibility: Node.js 18+ with tv-optimized.cjs, tv.cjs, agent-output.cjs and .env (SESSION, SIGNATURE) at project root
metadata:
  hermes:
    tags: [trading, tradingview, pine-script, volume-momentum, trend-alerts]
    category: trading
required_environment_variables:
  - name: SESSION
    prompt: TradingView session cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid
    required_for: full functionality
  - name: SIGNATURE
    prompt: TradingView signature cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid_sign
    required_for: full functionality
---

# Delta Volume Intensity — Trading Opportunity Finder

## When to Use

Helps the user run the standalone `delta-volume-intensity.cjs` script against any TradingView symbol and timeframe, then interprets the structured output to surface high-probability trading setups based on trend + momentum analysis. The output includes:

- **Trend State** — UPTREND / DOWNTREND / SIDEWAYS classification
- **Support/Resistance Levels** — calculated S/R from volume structure
- **ROC Momentum** — rate-of-change for momentum confirmation
- **ATR** — volatility context for pos
```

---

### 2.4 `tv ema-atr` — `ema-atr-pro-engine`

- **Synopsis:** EMA + ATR Pro Engine — trailing stop with re-entry
- **Pine ID:** `PUB;7d5f8755ab67400899ef73a9898471e4`  (reference Pine ID: `PUB;7d5f8755ab67400899ef73a9898471e4`)
- **Workflow ID:** `ema-atr-structure`  (captured reference: `ema-atr-structure`)
- **Go parser:** `internal/skill/parsers/ema_atr.go`  → func `parseEMAATR` / format `formatEMAATR`
- **Historical JS runner:** `/Volumes/ExMac/code/tradingview/js-experiment06/ema-atr-pro-engine.cjs`
- **Historical sample command:** `node ema-atr-pro-engine.cjs BTCUSDT --tf 1h --bars 300 --agent --json --silent`
- **Captured reference run:** rc=0 parsed_via=delimiter hint=None
- **Reference payload top-level keys (13):** `_parserMeta, agentContext, conformance, execution, exitCode, labels, market, narrative, opportunities, schemaVersion, signals, status, timestamp`

#### Inputs (Go)

| name | tv_input_id | type | default |
|------|-------------|------|---------|
| `ema2Len` | `in_0` | int | `20` |
| `ema3Len` | `in_1` | int | `50` |
| `useEMA2` | `in_2` | bool | `true` |
| `useEMA3` | `in_3` | bool | `false` |
| `pivotLen` | `in_4` | int | `1` |
| `atrLen` | `in_5` | int | `7` |
| `atrMult` | `in_6` | float | `1.4` |
| `confirmClose` | `in_7` | bool | `true` |
| `fastMode` | `in_8` | bool | `false` |
| `enableReentry` | `in_9` | bool | `false` |

#### Inputs (historical JS reference — INPUT_MAP)

| variable | tvInputId | type | default |
|----------|-----------|------|---------|
| `ema2Len` | `in_0` | `int` | `20` |
| `ema3Len` | `in_1` | `int` | `50` |
| `useEMA2` | `in_2` | `bool` | `true` |
| `useEMA3` | `in_3` | `bool` | `false` |
| `pivotLen` | `in_4` | `int` | `1` |
| `atrLen` | `in_5` | `int` | `7` |
| `atrMult` | `in_6` | `float` | `1.4` |
| `confirmClose` | `in_7` | `bool` | `true` |
| `fastMode` | `in_8` | `bool` | `false` |
| `enableReentry` | `in_9` | `bool` | `false` |
| `buyColor` | `in_10` | `color` | `color.rgb(5, 7, 12)` |
| `sellColor` | `in_11` | `color` | `color.gray` |
| `textColor` | `in_12` | `color` | `color.white` |
| `bullTrailColor` | `in_13` | `color` | `color.rgb(94, 255, 0)` |
| `bearTrailColor` | `in_14` | `color` | `color.red` |

#### Input Comparison

| Go name | Go TV id | Reference variable | Reference tvInputId | Name match | TV id match |
|---------|----------|-------------|--------------|------------|-------------|
| `ema2Len` | `in_0` | `ema2Len` | `in_0` | OK | OK |
| `ema3Len` | `in_1` | `ema3Len` | `in_1` | OK | OK |
| `useEMA2` | `in_2` | `useEMA2` | `in_2` | OK | OK |
| `useEMA3` | `in_3` | `useEMA3` | `in_3` | OK | OK |
| `pivotLen` | `in_4` | `pivotLen` | `in_4` | OK | OK |
| `atrLen` | `in_5` | `atrLen` | `in_5` | OK | OK |
| `atrMult` | `in_6` | `atrMult` | `in_6` | OK | OK |
| `confirmClose` | `in_7` | `confirmClose` | `in_7` | OK | OK |
| `fastMode` | `in_8` | `fastMode` | `in_8` | OK | OK |
| `enableReentry` | `in_9` | `enableReentry` | `in_9` | OK | OK |
| — | — | `buyColor` | `in_10` | color (cosmetic, safe to omit) | — |
| — | — | `sellColor` | `in_11` | color (cosmetic, safe to omit) | — |
| — | — | `textColor` | `in_12` | color (cosmetic, safe to omit) | — |
| — | — | `bullTrailColor` | `in_13` | color (cosmetic, safe to omit) | — |
| — | — | `bearTrailColor` | `in_14` | color (cosmetic, safe to omit) | — |

#### Go parser reads from periods[] (getField aliases)

`BUY_Re_entry`, `BUY_Signal`, `Plot`, `Plot_2`, `Plot_2_colorer`, `SELL_Re_entry`, `SELL_Signal`, `plot_0`, `plot_2`

#### Go Structure keys produced

`buyReentry`, `buySignal`, `plot0`, `plot2`, `sellReentry`, `sellSignal`, `trailTrend`

#### Reference payload schema (from captured dump)

Dump: `skill-analysis/dumps/ema-atr-pro-engine/payload.json`

| key | type/shape |
|-----|-----------|
| `status` | `str` |
| `exitCode` | `int` |
| `timestamp` | `str` |
| `execution` | `object{durationMs,attempts}` |
| `agentContext` | `object{workflow,modelVersion,symbol,timeframe,htfTimeframe}` |
| `market` | `object{trailTrend,combinedTrend,currentTrail,currentEMA2,currentEMA3,lastPrice}` |
| `signals` | `object{buy,sell,buyReentry,sellReentry,currentBuy,currentSell}` |
| `labels` | `object{buyLabels,sellLabels}` |
| `opportunities` | `list[any]` |
| `narrative` | `object{marketStructure,primaryOpportunity,warnings,watchlist}` |
| `conformance` | `object{hasValidData,agenticScore}` |
| `schemaVersion` | `str` |
| `_parserMeta` | `object{schemaVersion,emittedAt,deterministic}` |

#### Go `SkillResult` schema (target output)

| field | type |
|-------|------|
| `status` | string |
| `workflow` | string |
| `market.lastPrice` | any |
| `market.bias` | string |
| `structure` | map[string]any (per-skill keys above) |
| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |
| `narrative.marketStructure` | string |
| `narrative.primaryOpportunity` | string |
| `narrative.warnings[]` | string |
| `narrative.watchlist[]` | string |
| `validation.passed` | bool |
| `validation.warnings[]` | string |
| `conformance.hasValidData` | bool |
| `conformance.agenticScore` | float64 |
| `raw` | map (omitted in agent output) |

#### Discrepancies & fix recommendations

- **Historical JS input `buyColor` (`in_10`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `sellColor` (`in_11`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `textColor` (`in_12`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `bullTrailColor` (`in_13`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `bearTrailColor` (`in_14`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Reference payload has rich keys not in Go SkillResult:** `_parserMeta, labels, signals`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.

#### Captured `--help` output

```
◇ injected env (13) from .env // tip: ⌘ suppress logs { quiet: true }

EMA + ATR PRO Ultimate Engine — Standalone Runner
Usage: node ema-atr-pro-engine.cjs <SYMBOL> [options]
Options: --tf, --bars, --input key=value, --json, --agent, --out, --verbose, --dry-run, --help
Inputs: ema2Len, ema3Len, useEMA2, useEMA3, pivotLen, atrLen, atrMult, confirmClose, fastMode, enableReentry, buyColor, sellColor, textColor, bullTrailColor, bearTrailColor
```

#### Original SKILL.md excerpt (first 1500 chars)

```markdown
---
name: ema-atr-pro-engine
description: |
  Use the EMA + ATR PRO Ultimate Engine TradingView indicator to track signal activation history, analyze EMA trail trends, and identify high-probability entry/exit setups based on ATR-based trailing stops and EMA crossovers.
version: 1.0.0
license: MIT
author: TradingView Pine Skills
compatibility: Node.js 18+ with tv-optimized.cjs, tv.cjs, agent-output.cjs and .env (SESSION, SIGNATURE) at project root
metadata:
  hermes:
    tags: [trading, tradingview, pine-script, ema, atr-trail]
    category: trading
required_environment_variables:
  - name: SESSION
    prompt: TradingView session cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid
    required_for: full functionality
  - name: SIGNATURE
    prompt: TradingView signature cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid_sign
    required_for: full functionality
---

# EMA + ATR PRO Ultimate Engine — Trading Opportunity Finder

## When to Use

Helps the user run the standalone `ema-atr-pro-engine.cjs` script against any TradingView symbol and timeframe, then interprets the structured output to surface high-probability trading setups based on EMA trail and ATR signal analysis. The output includes:

- **Trail Trend** — ATR-based trail direction (BULLISH/BEARISH)
- **Signal Counts** — total buy/sell signals and reentries in lookback
- **EMA Levels** — EMA2 and EMA3 values for trend c
```

---

### 2.5 `tv golden` — `golden-rule-strategy`

- **Synopsis:** Golden Rule Strategy — multi-TF weekly/daily/4H alignment
- **Pine ID:** `PUB;6daafb2cabe6419d98ae25229d2327f8`  (reference Pine ID: `PUB;6daafb2cabe6419d98ae25229d2327f8`)
- **Workflow ID:** `golden-rule-strategy`  (captured reference: `golden-rule-strategy`)
- **Go parser:** `internal/skill/parsers/golden.go`  → func `parseGolden` / format `formatGolden`
- **Historical JS runner:** `/Volumes/ExMac/code/tradingview/js-experiment06/golden-rule-strategy.cjs`
- **Historical sample command:** `node golden-rule-strategy.cjs BTCUSDT --tf 1h --bars 500 --agent --json --silent`
- **Captured reference run:** rc=0 parsed_via=delimiter hint=None
- **Reference payload top-level keys (10):** `_parserMeta, agentContext, execution, exitCode, goldenRule, indicators, schemaVersion, status, timeframes, timestamp`

#### Inputs (Go)

| name | tv_input_id | type | default |
|------|-------------|------|---------|
| `showStructureInput` | `in_10` | bool | `true` |
| `showFairValueGapsInput` | `in_33` | bool | `true` |
| `showInternalOrderBlocksInput` | `in_19` | bool | `true` |
| `swingsLengthInput` | `in_17` | int | `50` |

#### Inputs (historical JS reference — INPUT_MAP)

| variable | tvInputId | type | default |
|----------|-----------|------|---------|
| `modeInput` | `in_0` | `string` | `HISTORICAL` |
| `styleInput` | `in_1` | `string` | `COLORED` |
| `showTrendInput` | `in_2` | `bool` | `false` |
| `showInternalsInput` | `in_3` | `bool` | `true` |
| `showInternalBullInput` | `in_4` | `string` | `ALL` |
| `internalBullColorInput` | `in_5` | `string` | `GREEN` |
| `showInternalBearInput` | `in_6` | `string` | `ALL` |
| `internalBearColorInput` | `in_7` | `string` | `RED` |
| `internalFilterConfluenceInput` | `in_8` | `bool` | `false` |
| `internalStructureSize` | `in_9` | `string` | `TINY` |
| `showStructureInput` | `in_10` | `bool` | `true` |
| `showSwingBullInput` | `in_11` | `string` | `ALL` |
| `swingBullColorInput` | `in_12` | `string` | `GREEN` |
| `showSwingBearInput` | `in_13` | `string` | `ALL` |
| `swingBearColorInput` | `in_14` | `string` | `RED` |
| `swingStructureSize` | `in_15` | `string` | `SMALL` |
| `showSwingsInput` | `in_16` | `bool` | `false` |
| `swingsLengthInput` | `in_17` | `int` | `50` |
| `showHighLowSwingsInput` | `in_18` | `bool` | `true` |
| `showInternalOrderBlocksInput` | `in_19` | `bool` | `true` |
| `internalOrderBlocksSizeInput` | `in_20` | `int` | `5` |
| `showSwingOrderBlocksInput` | `in_21` | `bool` | `false` |
| `swingOrderBlocksSizeInput` | `in_22` | `int` | `5` |
| `orderBlockFilterInput` | `in_23` | `string` | `Atr` |
| `orderBlockMitigationInput` | `in_24` | `string` | `HIGHLOW` |
| `internalBullishOrderBlockColor` | `in_25` | `color` | `color.new(#3179f5, 80)` |
| `internalBearishOrderBlockColor` | `in_26` | `color` | `color.new(#f77c80, 80)` |
| `swingBullishOrderBlockColor` | `in_27` | `color` | `color.new(#1848cc, 80)` |
| `swingBearishOrderBlockColor` | `in_28` | `color` | `color.new(#b22833, 80)` |
| `showEqualHighsLowsInput` | `in_29` | `bool` | `true` |
| `equalHighsLowsLengthInput` | `in_30` | `int` | `3` |
| `equalHighsLowsThresholdInput` | `in_31` | `float` | `0.1` |
| `equalHighsLowsSizeInput` | `in_32` | `string` | `TINY` |
| `showFairValueGapsInput` | `in_33` | `bool` | `true` |
| `fairValueGapsThresholdInput` | `in_34` | `bool` | `true` |
| `fairValueGapsTimeframeInput` | `in_35` | `timeframe` | `` |
| `fairValueGapsBullColorInput` | `in_36` | `color` | `color.new(#00ff68, 70)` |
| `fairValueGapsBearColorInput` | `in_37` | `color` | `color.new(#ff0008, 70)` |
| `fairValueGapsExtendInput` | `in_38` | `int` | `1` |
| `showDailyLevelsInput` | `in_39` | `bool` | `false` |
| `dailyLevelsStyleInput` | `in_40` | `string` | `SOLID` |
| `dailyLevelsColorInput` | `in_41` | `string` | `BLUE` |
| `showWeeklyLevelsInput` | `in_42` | `bool` | `false` |
| `weeklyLevelsStyleInput` | `in_43` | `string` | `SOLID` |
| `weeklyLevelsColorInput` | `in_44` | `string` | `BLUE` |
| `showMonthlyLevelsInput` | `in_45` | `bool` | `false` |
| `monthlyLevelsStyleInput` | `in_46` | `string` | `SOLID` |
| `monthlyLevelsColorInput` | `in_47` | `string` | `BLUE` |
| `showPremiumDiscountZonesInput` | `in_48` | `bool` | `false` |
| `premiumZoneColorInput` | `in_49` | `color` | `RED` |
| `equilibriumZoneColorInput` | `in_50` | `color` | `GRAY` |
| `discountZoneColorInput` | `in_51` | `color` | `GREEN` |

#### Input Comparison

| Go name | Go TV id | Reference variable | Reference tvInputId | Name match | TV id match |
|---------|----------|-------------|--------------|------------|-------------|
| `showStructureInput` | `in_10` | `showStructureInput` | `in_10` | OK | OK |
| `showFairValueGapsInput` | `in_33` | `showFairValueGapsInput` | `in_33` | OK | OK |
| `showInternalOrderBlocksInput` | `in_19` | `showInternalOrderBlocksInput` | `in_19` | OK | OK |
| `swingsLengthInput` | `in_17` | `swingsLengthInput` | `in_17` | OK | OK |
| — | — | `modeInput` | `in_0` | **MISSING in Go** | — |
| — | — | `styleInput` | `in_1` | **MISSING in Go** | — |
| — | — | `showTrendInput` | `in_2` | **MISSING in Go** | — |
| — | — | `showInternalsInput` | `in_3` | **MISSING in Go** | — |
| — | — | `showInternalBullInput` | `in_4` | **MISSING in Go** | — |
| — | — | `internalBullColorInput` | `in_5` | **MISSING in Go** | — |
| — | — | `showInternalBearInput` | `in_6` | **MISSING in Go** | — |
| — | — | `internalBearColorInput` | `in_7` | **MISSING in Go** | — |
| — | — | `internalFilterConfluenceInput` | `in_8` | **MISSING in Go** | — |
| — | — | `internalStructureSize` | `in_9` | **MISSING in Go** | — |
| — | — | `showSwingBullInput` | `in_11` | **MISSING in Go** | — |
| — | — | `swingBullColorInput` | `in_12` | **MISSING in Go** | — |
| — | — | `showSwingBearInput` | `in_13` | **MISSING in Go** | — |
| — | — | `swingBearColorInput` | `in_14` | **MISSING in Go** | — |
| — | — | `swingStructureSize` | `in_15` | **MISSING in Go** | — |
| — | — | `showSwingsInput` | `in_16` | **MISSING in Go** | — |
| — | — | `showHighLowSwingsInput` | `in_18` | **MISSING in Go** | — |
| — | — | `internalOrderBlocksSizeInput` | `in_20` | **MISSING in Go** | — |
| — | — | `showSwingOrderBlocksInput` | `in_21` | **MISSING in Go** | — |
| — | — | `swingOrderBlocksSizeInput` | `in_22` | **MISSING in Go** | — |
| — | — | `orderBlockFilterInput` | `in_23` | **MISSING in Go** | — |
| — | — | `orderBlockMitigationInput` | `in_24` | **MISSING in Go** | — |
| — | — | `internalBullishOrderBlockColor` | `in_25` | color (cosmetic, safe to omit) | — |
| — | — | `internalBearishOrderBlockColor` | `in_26` | color (cosmetic, safe to omit) | — |
| — | — | `swingBullishOrderBlockColor` | `in_27` | color (cosmetic, safe to omit) | — |
| — | — | `swingBearishOrderBlockColor` | `in_28` | color (cosmetic, safe to omit) | — |
| — | — | `showEqualHighsLowsInput` | `in_29` | **MISSING in Go** | — |
| — | — | `equalHighsLowsLengthInput` | `in_30` | **MISSING in Go** | — |
| — | — | `equalHighsLowsThresholdInput` | `in_31` | **MISSING in Go** | — |
| — | — | `equalHighsLowsSizeInput` | `in_32` | **MISSING in Go** | — |
| — | — | `fairValueGapsThresholdInput` | `in_34` | **MISSING in Go** | — |
| — | — | `fairValueGapsTimeframeInput` | `in_35` | **MISSING in Go** | — |
| — | — | `fairValueGapsBullColorInput` | `in_36` | color (cosmetic, safe to omit) | — |
| — | — | `fairValueGapsBearColorInput` | `in_37` | color (cosmetic, safe to omit) | — |
| — | — | `fairValueGapsExtendInput` | `in_38` | **MISSING in Go** | — |
| — | — | `showDailyLevelsInput` | `in_39` | **MISSING in Go** | — |
| — | — | `dailyLevelsStyleInput` | `in_40` | **MISSING in Go** | — |
| — | — | `dailyLevelsColorInput` | `in_41` | **MISSING in Go** | — |
| — | — | `showWeeklyLevelsInput` | `in_42` | **MISSING in Go** | — |
| — | — | `weeklyLevelsStyleInput` | `in_43` | **MISSING in Go** | — |
| — | — | `weeklyLevelsColorInput` | `in_44` | **MISSING in Go** | — |
| — | — | `showMonthlyLevelsInput` | `in_45` | **MISSING in Go** | — |
| — | — | `monthlyLevelsStyleInput` | `in_46` | **MISSING in Go** | — |
| — | — | `monthlyLevelsColorInput` | `in_47` | **MISSING in Go** | — |
| — | — | `showPremiumDiscountZonesInput` | `in_48` | **MISSING in Go** | — |
| — | — | `premiumZoneColorInput` | `in_49` | color (cosmetic, safe to omit) | — |
| — | — | `equilibriumZoneColorInput` | `in_50` | color (cosmetic, safe to omit) | — |
| — | — | `discountZoneColorInput` | `in_51` | color (cosmetic, safe to omit) | — |

#### Go parser reads from periods[] (getField aliases)

`Close`, `Verdict`, `close`, `verdict`

#### Go Structure keys produced

`price`, `verdict`

#### Reference payload schema (from captured dump)

Dump: `skill-analysis/dumps/golden-rule-strategy/payload.json`

| key | type/shape |
|-----|-----------|
| `status` | `str` |
| `exitCode` | `int` |
| `timestamp` | `str` |
| `execution` | `object{durationMs,attempts}` |
| `agentContext` | `object{workflow,modelVersion,symbol}` |
| `goldenRule` | `object{verdict,direction,score,checklist,sltp,rationale...}` |
| `timeframes` | `object{weekly,daily,h4}` |
| `indicators` | `object{rsi,stochastic,macd,latestClose}` |
| `schemaVersion` | `str` |
| `_parserMeta` | `object{schemaVersion,emittedAt,deterministic}` |

#### Go `SkillResult` schema (target output)

| field | type |
|-------|------|
| `status` | string |
| `workflow` | string |
| `market.lastPrice` | any |
| `market.bias` | string |
| `structure` | map[string]any (per-skill keys above) |
| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |
| `narrative.marketStructure` | string |
| `narrative.primaryOpportunity` | string |
| `narrative.warnings[]` | string |
| `narrative.watchlist[]` | string |
| `validation.passed` | bool |
| `validation.warnings[]` | string |
| `conformance.hasValidData` | bool |
| `conformance.agenticScore` | float64 |
| `raw` | map (omitted in agent output) |

#### Discrepancies & fix recommendations

- **Historical JS input `modeInput` (`in_0`) missing in Go.** Consider adding `InputDef{Name: "modeInput", TVInputID: "in_0", Type: "string", Default: HISTORICAL}`.
- **Historical JS input `styleInput` (`in_1`) missing in Go.** Consider adding `InputDef{Name: "styleInput", TVInputID: "in_1", Type: "string", Default: COLORED}`.
- **Historical JS input `showTrendInput` (`in_2`) missing in Go.** Consider adding `InputDef{Name: "showTrendInput", TVInputID: "in_2", Type: "bool", Default: false}`.
- **Historical JS input `showInternalsInput` (`in_3`) missing in Go.** Consider adding `InputDef{Name: "showInternalsInput", TVInputID: "in_3", Type: "bool", Default: true}`.
- **Historical JS input `showInternalBullInput` (`in_4`) missing in Go.** Consider adding `InputDef{Name: "showInternalBullInput", TVInputID: "in_4", Type: "string", Default: ALL}`.
- **Historical JS input `internalBullColorInput` (`in_5`) missing in Go.** Consider adding `InputDef{Name: "internalBullColorInput", TVInputID: "in_5", Type: "string", Default: GREEN}`.
- **Historical JS input `showInternalBearInput` (`in_6`) missing in Go.** Consider adding `InputDef{Name: "showInternalBearInput", TVInputID: "in_6", Type: "string", Default: ALL}`.
- **Historical JS input `internalBearColorInput` (`in_7`) missing in Go.** Consider adding `InputDef{Name: "internalBearColorInput", TVInputID: "in_7", Type: "string", Default: RED}`.
- **Historical JS input `internalFilterConfluenceInput` (`in_8`) missing in Go.** Consider adding `InputDef{Name: "internalFilterConfluenceInput", TVInputID: "in_8", Type: "bool", Default: false}`.
- **Historical JS input `internalStructureSize` (`in_9`) missing in Go.** Consider adding `InputDef{Name: "internalStructureSize", TVInputID: "in_9", Type: "string", Default: TINY}`.
- **Historical JS input `showSwingBullInput` (`in_11`) missing in Go.** Consider adding `InputDef{Name: "showSwingBullInput", TVInputID: "in_11", Type: "string", Default: ALL}`.
- **Historical JS input `swingBullColorInput` (`in_12`) missing in Go.** Consider adding `InputDef{Name: "swingBullColorInput", TVInputID: "in_12", Type: "string", Default: GREEN}`.
- **Historical JS input `showSwingBearInput` (`in_13`) missing in Go.** Consider adding `InputDef{Name: "showSwingBearInput", TVInputID: "in_13", Type: "string", Default: ALL}`.
- **Historical JS input `swingBearColorInput` (`in_14`) missing in Go.** Consider adding `InputDef{Name: "swingBearColorInput", TVInputID: "in_14", Type: "string", Default: RED}`.
- **Historical JS input `swingStructureSize` (`in_15`) missing in Go.** Consider adding `InputDef{Name: "swingStructureSize", TVInputID: "in_15", Type: "string", Default: SMALL}`.
- **Historical JS input `showSwingsInput` (`in_16`) missing in Go.** Consider adding `InputDef{Name: "showSwingsInput", TVInputID: "in_16", Type: "bool", Default: false}`.
- **Historical JS input `showHighLowSwingsInput` (`in_18`) missing in Go.** Consider adding `InputDef{Name: "showHighLowSwingsInput", TVInputID: "in_18", Type: "bool", Default: true}`.
- **Historical JS input `internalOrderBlocksSizeInput` (`in_20`) missing in Go.** Consider adding `InputDef{Name: "internalOrderBlocksSizeInput", TVInputID: "in_20", Type: "int", Default: 5}`.
- **Historical JS input `showSwingOrderBlocksInput` (`in_21`) missing in Go.** Consider adding `InputDef{Name: "showSwingOrderBlocksInput", TVInputID: "in_21", Type: "bool", Default: false}`.
- **Historical JS input `swingOrderBlocksSizeInput` (`in_22`) missing in Go.** Consider adding `InputDef{Name: "swingOrderBlocksSizeInput", TVInputID: "in_22", Type: "int", Default: 5}`.
- **Historical JS input `orderBlockFilterInput` (`in_23`) missing in Go.** Consider adding `InputDef{Name: "orderBlockFilterInput", TVInputID: "in_23", Type: "string", Default: Atr}`.
- **Historical JS input `orderBlockMitigationInput` (`in_24`) missing in Go.** Consider adding `InputDef{Name: "orderBlockMitigationInput", TVInputID: "in_24", Type: "string", Default: HIGHLOW}`.
- **Historical JS input `internalBullishOrderBlockColor` (`in_25`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `internalBearishOrderBlockColor` (`in_26`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `swingBullishOrderBlockColor` (`in_27`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `swingBearishOrderBlockColor` (`in_28`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `showEqualHighsLowsInput` (`in_29`) missing in Go.** Consider adding `InputDef{Name: "showEqualHighsLowsInput", TVInputID: "in_29", Type: "bool", Default: true}`.
- **Historical JS input `equalHighsLowsLengthInput` (`in_30`) missing in Go.** Consider adding `InputDef{Name: "equalHighsLowsLengthInput", TVInputID: "in_30", Type: "int", Default: 3}`.
- **Historical JS input `equalHighsLowsThresholdInput` (`in_31`) missing in Go.** Consider adding `InputDef{Name: "equalHighsLowsThresholdInput", TVInputID: "in_31", Type: "float", Default: 0.1}`.
- **Historical JS input `equalHighsLowsSizeInput` (`in_32`) missing in Go.** Consider adding `InputDef{Name: "equalHighsLowsSizeInput", TVInputID: "in_32", Type: "string", Default: TINY}`.
- **Historical JS input `fairValueGapsThresholdInput` (`in_34`) missing in Go.** Consider adding `InputDef{Name: "fairValueGapsThresholdInput", TVInputID: "in_34", Type: "bool", Default: true}`.
- **Historical JS input `fairValueGapsTimeframeInput` (`in_35`) missing in Go.** Consider adding `InputDef{Name: "fairValueGapsTimeframeInput", TVInputID: "in_35", Type: "timeframe", Default: }`.
- **Historical JS input `fairValueGapsBullColorInput` (`in_36`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `fairValueGapsBearColorInput` (`in_37`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `fairValueGapsExtendInput` (`in_38`) missing in Go.** Consider adding `InputDef{Name: "fairValueGapsExtendInput", TVInputID: "in_38", Type: "int", Default: 1}`.
- **Historical JS input `showDailyLevelsInput` (`in_39`) missing in Go.** Consider adding `InputDef{Name: "showDailyLevelsInput", TVInputID: "in_39", Type: "bool", Default: false}`.
- **Historical JS input `dailyLevelsStyleInput` (`in_40`) missing in Go.** Consider adding `InputDef{Name: "dailyLevelsStyleInput", TVInputID: "in_40", Type: "string", Default: SOLID}`.
- **Historical JS input `dailyLevelsColorInput` (`in_41`) missing in Go.** Consider adding `InputDef{Name: "dailyLevelsColorInput", TVInputID: "in_41", Type: "string", Default: BLUE}`.
- **Historical JS input `showWeeklyLevelsInput` (`in_42`) missing in Go.** Consider adding `InputDef{Name: "showWeeklyLevelsInput", TVInputID: "in_42", Type: "bool", Default: false}`.
- **Historical JS input `weeklyLevelsStyleInput` (`in_43`) missing in Go.** Consider adding `InputDef{Name: "weeklyLevelsStyleInput", TVInputID: "in_43", Type: "string", Default: SOLID}`.
- **Historical JS input `weeklyLevelsColorInput` (`in_44`) missing in Go.** Consider adding `InputDef{Name: "weeklyLevelsColorInput", TVInputID: "in_44", Type: "string", Default: BLUE}`.
- **Historical JS input `showMonthlyLevelsInput` (`in_45`) missing in Go.** Consider adding `InputDef{Name: "showMonthlyLevelsInput", TVInputID: "in_45", Type: "bool", Default: false}`.
- **Historical JS input `monthlyLevelsStyleInput` (`in_46`) missing in Go.** Consider adding `InputDef{Name: "monthlyLevelsStyleInput", TVInputID: "in_46", Type: "string", Default: SOLID}`.
- **Historical JS input `monthlyLevelsColorInput` (`in_47`) missing in Go.** Consider adding `InputDef{Name: "monthlyLevelsColorInput", TVInputID: "in_47", Type: "string", Default: BLUE}`.
- **Historical JS input `showPremiumDiscountZonesInput` (`in_48`) missing in Go.** Consider adding `InputDef{Name: "showPremiumDiscountZonesInput", TVInputID: "in_48", Type: "bool", Default: false}`.
- **Historical JS input `premiumZoneColorInput` (`in_49`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `equilibriumZoneColorInput` (`in_50`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `discountZoneColorInput` (`in_51`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Reference payload has rich keys not in Go SkillResult:** `_parserMeta, goldenRule, indicators, timeframes`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.

#### Captured `--help` output

```
◇ injected env (13) from .env // tip: ⌘ override existing { override: true }

Golden Rule Strategy — Multi-Timeframe High-Probability Runner
Usage: node golden-rule-strategy.cjs <SYMBOL> [options]
Options: --bars, --input key=value, --json, --agent, --out, --verbose, --dry-run, --silent, --help

Description:
  Runs SMC (Smart Money Concepts) on Weekly → Daily → 4H timeframes.
  Computes RSI, Stochastic, MACD from 4H price data locally.
  Applies the Golden Rule 3-step filter + 4-signal checklist.
  Outputs PASS/FAIL verdict with SL/TP suggestions from nearest OBs/FVGs.
```

#### Original SKILL.md excerpt (first 1500 chars)

```markdown
---
name: golden-rule-strategy
description: |
  Execute the Golden Rule Strategy — a multi-timeframe high-probability trading system.
version: 1.0.0
license: MIT
author: TradingView Pine Skills
compatibility: Node.js 18+ with tv-optimized.cjs, tv.cjs, agent-output.cjs and .env (SESSION, SIGNATURE) at project root
metadata:
  hermes:
    tags: [trading, tradingview, pine-script, technical-analysis, multi-timeframe, golden-rule]
    category: trading
required_environment_variables:
  - name: SESSION
    prompt: TradingView session cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid
    required_for: full functionality
  - name: SIGNATURE
    prompt: TradingView signature cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid_sign
    required_for: full functionality
---

# Golden Rule Strategy — Multi-Timeframe High-Probability Trading

## When to Use

Automates the **Golden Rule Strategy**: a disciplined, probability-based trading framework that requires **three timeframes to align** and **four technical signals to confirm** before capital is put at risk. The strategy is designed for serious investors seeking consistent portfolio growth by trading *with* the primary market tide, not against it.

**The Golden Rule:** *Never trade against the weekly momentum.*

This skill:
1. Runs **Smart Money Concepts (LuxAlgo)** on Weekly, Daily, and 4-Hour timeframes
2. Computes **RSI(14)**, **Stoc
```

---

### 2.6 `tv ict` — `ict-auto-validated-smc`

- **Synopsis:** ICT Auto-Validated SMC — full ICT system with OTE zones
- **Pine ID:** `PUB;789a5c79bfe9443585da09e85ece73de`  (reference Pine ID: `PUB;789a5c79bfe9443585da09e85ece73de`)
- **Workflow ID:** `ict-smc-structure`  (captured reference: `ict-smc-structure`)
- **Go parser:** `internal/skill/parsers/ict.go`  → func `parseICT` / format `formatICT`
- **Historical JS runner:** `/Volumes/ExMac/code/tradingview/js-experiment06/ict-auto-validated-smc.cjs`
- **Historical sample command:** `node ict-auto-validated-smc.cjs BTCUSDT --tf 1h --bars 500 --agent --json`
- **Captured reference run:** rc=0 parsed_via=bracket hint=None
- **Reference payload top-level keys (14):** `agentContext, conformance, execution, exitCode, market, narrative, opportunities, schemaVersion, signals, status, structure, timestamp, validation, zones`

#### Inputs (Go)

| name | tv_input_id | type | default |
|------|-------------|------|---------|
| `swingLen` | `in_0` | int | `10` |
| `internalLen` | `in_1` | int | `5` |
| `showSwings` | `in_2` | bool | `true` |
| `showStructure` | `in_3` | bool | `true` |
| `useHTF` | `in_6` | bool | `true` |
| `htfTimeframe` | `in_7` | timeframe | `240` |
| `showOB` | `in_10` | bool | `true` |
| `showFVG` | `in_19` | bool | `true` |
| `showBreakers` | `in_15` | bool | `true` |
| `showOTE` | `in_49` | bool | `true` |
| `enableSignals` | `in_56` | bool | `true` |
| `minSigScore` | `in_57` | int | `4` |

#### Inputs (historical JS reference — INPUT_MAP)

| variable | tvInputId | type | default |
|----------|-----------|------|---------|
| `swingLen` | `in_0` | `int` | `10` |
| `internalLen` | `in_1` | `int` | `5` |
| `showSwings` | `in_2` | `bool` | `true` |
| `showStructure` | `in_3` | `bool` | `true` |
| `showInternalStructure` | `in_4` | `bool` | `false` |
| `requireBodyClose` | `in_5` | `bool` | `false` |
| `useHTF` | `in_6` | `bool` | `true` |
| `htfTimeframe` | `in_7` | `timeframe` | `240` |
| `htfSwingLen` | `in_8` | `int` | `10` |
| `showHTFStructure` | `in_9` | `bool` | `true` |
| `showOB` | `in_10` | `bool` | `true` |
| `obMaxCount` | `in_11` | `int` | `5` |
| `requireSweep` | `in_12` | `bool` | `true` |
| `requireDisplacement` | `in_13` | `bool` | `true` |
| `showMitigated` | `in_14` | `bool` | `false` |
| `showBreakers` | `in_15` | `bool` | `true` |
| `brkMaxCount` | `in_16` | `int` | `5` |
| `brkBullColor` | `in_17` | `color` | `color.new(#00E676, 75)` |
| `brkBearColor` | `in_18` | `color` | `color.new(#FF6D00, 75)` |
| `showFVG` | `in_19` | `bool` | `true` |
| `fvgMaxCount` | `in_20` | `int` | `5` |
| `fvgMinATRMult` | `in_21` | `float` | `1` |
| `showCE` | `in_22` | `bool` | `true` |
| `showMitigatedFVG` | `in_23` | `bool` | `false` |
| `showIFVG` | `in_24` | `bool` | `true` |
| `ifvgColor` | `in_25` | `color` | `color.new(#FFD600, 80)` |
| `showBPR` | `in_26` | `bool` | `true` |
| `bprColor` | `in_27` | `color` | `color.new(#E040FB, 75)` |
| `showLiquidity` | `in_28` | `bool` | `true` |
| `showEQHL` | `in_29` | `bool` | `true` |
| `eqTolerance` | `in_30` | `float` | `0.15` |
| `showSweeps` | `in_31` | `bool` | `true` |
| `sweepRequireWickReject` | `in_32` | `bool` | `false` |
| `showIDM` | `in_33` | `bool` | `true` |
| `idmColor` | `in_34` | `color` | `color.new(#FF6D00, 0)` |
| `idmMaxCount` | `in_35` | `int` | `5` |
| `showPD` | `in_36` | `bool` | `true` |
| `showEQ` | `in_37` | `bool` | `true` |
| `showSessionLevels` | `in_38` | `bool` | `true` |
| `showPDHL` | `in_39` | `bool` | `true` |
| `showPWHL` | `in_40` | `bool` | `true` |
| `slPDColor` | `in_41` | `color` | `color.new(#2196F3, 30)` |
| `slPWColor` | `in_42` | `color` | `color.new(#E040FB, 30)` |
| `showKZ` | `in_43` | `bool` | `true` |
| `kzAsian` | `in_44` | `bool` | `false` |
| `kzLondon` | `in_45` | `bool` | `true` |
| `kzNYAM` | `in_46` | `bool` | `true` |
| `kzNYPM` | `in_47` | `bool` | `false` |
| `kzTransparency` | `in_48` | `int` | `92` |
| `showOTE` | `in_49` | `bool` | `true` |
| `oteFibHigh` | `in_50` | `float` | `0.786` |
| `oteFibLow` | `in_51` | `float` | `0.618` |
| `showOTEFibs` | `in_52` | `bool` | `true` |
| `oteMaxCount` | `in_53` | `int` | `3` |
| `oteBullColor` | `in_54` | `color` | `color.new(#00BCD4, 80)` |
| `oteBearColor` | `in_55` | `color` | `color.new(#E040FB, 80)` |
| `enableSignals` | `in_56` | `bool` | `true` |
| `minSigScore` | `in_57` | `int` | `4` |
| `requireHTFAlign` | `in_58` | `bool` | `true` |
| `requireKZActive` | `in_59` | `bool` | `false` |
| `requireCISD` | `in_60` | `bool` | `false` |
| `showSigSL` | `in_61` | `bool` | `true` |
| `showSigTP` | `in_62` | `bool` | `true` |
| `sigLongColor` | `in_63` | `color` | `color.new(#00E676, 0)` |
| `sigShortColor` | `in_64` | `color` | `color.new(#FF1744, 0)` |
| `sigCooldown` | `in_65` | `int` | `10` |
| `showConfluence` | `in_66` | `bool` | `true` |
| `minScore` | `in_67` | `int` | `3` |
| `bullColor` | `in_68` | `color` | `color.new(#00C853, 0)` |
| `bearColor` | `in_69` | `color` | `color.new(#FF1744, 0)` |
| `fvgBullColor` | `in_70` | `color` | `color.new(#00C853, 85)` |
| `fvgBearColor` | `in_71` | `color` | `color.new(#FF1744, 85)` |
| `obBullColor` | `in_72` | `color` | `color.new(#2196F3, 80)` |
| `obBearColor` | `in_73` | `color` | `color.new(#FF9800, 80)` |
| `sweepColor` | `in_74` | `color` | `color.new(#FFD600, 0)` |
| `showInfoPanel` | `in_75` | `bool` | `true` |
| `chartLabelSize` | `in_76` | `string` | `small` |
| `panelTextSize` | `in_77` | `string` | `small` |

#### Input Comparison

| Go name | Go TV id | Reference variable | Reference tvInputId | Name match | TV id match |
|---------|----------|-------------|--------------|------------|-------------|
| `swingLen` | `in_0` | `swingLen` | `in_0` | OK | OK |
| `internalLen` | `in_1` | `internalLen` | `in_1` | OK | OK |
| `showSwings` | `in_2` | `showSwings` | `in_2` | OK | OK |
| `showStructure` | `in_3` | `showStructure` | `in_3` | OK | OK |
| `useHTF` | `in_6` | `useHTF` | `in_6` | OK | OK |
| `htfTimeframe` | `in_7` | `htfTimeframe` | `in_7` | OK | OK |
| `showOB` | `in_10` | `showOB` | `in_10` | OK | OK |
| `showFVG` | `in_19` | `showFVG` | `in_19` | OK | OK |
| `showBreakers` | `in_15` | `showBreakers` | `in_15` | OK | OK |
| `showOTE` | `in_49` | `showOTE` | `in_49` | OK | OK |
| `enableSignals` | `in_56` | `enableSignals` | `in_56` | OK | OK |
| `minSigScore` | `in_57` | `minSigScore` | `in_57` | OK | OK |
| — | — | `showInternalStructure` | `in_4` | **MISSING in Go** | — |
| — | — | `requireBodyClose` | `in_5` | **MISSING in Go** | — |
| — | — | `htfSwingLen` | `in_8` | **MISSING in Go** | — |
| — | — | `showHTFStructure` | `in_9` | **MISSING in Go** | — |
| — | — | `obMaxCount` | `in_11` | **MISSING in Go** | — |
| — | — | `requireSweep` | `in_12` | **MISSING in Go** | — |
| — | — | `requireDisplacement` | `in_13` | **MISSING in Go** | — |
| — | — | `showMitigated` | `in_14` | **MISSING in Go** | — |
| — | — | `brkMaxCount` | `in_16` | **MISSING in Go** | — |
| — | — | `brkBullColor` | `in_17` | color (cosmetic, safe to omit) | — |
| — | — | `brkBearColor` | `in_18` | color (cosmetic, safe to omit) | — |
| — | — | `fvgMaxCount` | `in_20` | **MISSING in Go** | — |
| — | — | `fvgMinATRMult` | `in_21` | **MISSING in Go** | — |
| — | — | `showCE` | `in_22` | **MISSING in Go** | — |
| — | — | `showMitigatedFVG` | `in_23` | **MISSING in Go** | — |
| — | — | `showIFVG` | `in_24` | **MISSING in Go** | — |
| — | — | `ifvgColor` | `in_25` | color (cosmetic, safe to omit) | — |
| — | — | `showBPR` | `in_26` | **MISSING in Go** | — |
| — | — | `bprColor` | `in_27` | color (cosmetic, safe to omit) | — |
| — | — | `showLiquidity` | `in_28` | **MISSING in Go** | — |
| — | — | `showEQHL` | `in_29` | **MISSING in Go** | — |
| — | — | `eqTolerance` | `in_30` | **MISSING in Go** | — |
| — | — | `showSweeps` | `in_31` | **MISSING in Go** | — |
| — | — | `sweepRequireWickReject` | `in_32` | **MISSING in Go** | — |
| — | — | `showIDM` | `in_33` | **MISSING in Go** | — |
| — | — | `idmColor` | `in_34` | color (cosmetic, safe to omit) | — |
| — | — | `idmMaxCount` | `in_35` | **MISSING in Go** | — |
| — | — | `showPD` | `in_36` | **MISSING in Go** | — |
| — | — | `showEQ` | `in_37` | **MISSING in Go** | — |
| — | — | `showSessionLevels` | `in_38` | **MISSING in Go** | — |
| — | — | `showPDHL` | `in_39` | **MISSING in Go** | — |
| — | — | `showPWHL` | `in_40` | **MISSING in Go** | — |
| — | — | `slPDColor` | `in_41` | color (cosmetic, safe to omit) | — |
| — | — | `slPWColor` | `in_42` | color (cosmetic, safe to omit) | — |
| — | — | `showKZ` | `in_43` | **MISSING in Go** | — |
| — | — | `kzAsian` | `in_44` | **MISSING in Go** | — |
| — | — | `kzLondon` | `in_45` | **MISSING in Go** | — |
| — | — | `kzNYAM` | `in_46` | **MISSING in Go** | — |
| — | — | `kzNYPM` | `in_47` | **MISSING in Go** | — |
| — | — | `kzTransparency` | `in_48` | **MISSING in Go** | — |
| — | — | `oteFibHigh` | `in_50` | **MISSING in Go** | — |
| — | — | `oteFibLow` | `in_51` | **MISSING in Go** | — |
| — | — | `showOTEFibs` | `in_52` | **MISSING in Go** | — |
| — | — | `oteMaxCount` | `in_53` | **MISSING in Go** | — |
| — | — | `oteBullColor` | `in_54` | color (cosmetic, safe to omit) | — |
| — | — | `oteBearColor` | `in_55` | color (cosmetic, safe to omit) | — |
| — | — | `requireHTFAlign` | `in_58` | **MISSING in Go** | — |
| — | — | `requireKZActive` | `in_59` | **MISSING in Go** | — |
| — | — | `requireCISD` | `in_60` | **MISSING in Go** | — |
| — | — | `showSigSL` | `in_61` | **MISSING in Go** | — |
| — | — | `showSigTP` | `in_62` | **MISSING in Go** | — |
| — | — | `sigLongColor` | `in_63` | color (cosmetic, safe to omit) | — |
| — | — | `sigShortColor` | `in_64` | color (cosmetic, safe to omit) | — |
| — | — | `sigCooldown` | `in_65` | **MISSING in Go** | — |
| — | — | `showConfluence` | `in_66` | **MISSING in Go** | — |
| — | — | `minScore` | `in_67` | **MISSING in Go** | — |
| — | — | `bullColor` | `in_68` | color (cosmetic, safe to omit) | — |
| — | — | `bearColor` | `in_69` | color (cosmetic, safe to omit) | — |
| — | — | `fvgBullColor` | `in_70` | color (cosmetic, safe to omit) | — |
| — | — | `fvgBearColor` | `in_71` | color (cosmetic, safe to omit) | — |
| — | — | `obBullColor` | `in_72` | color (cosmetic, safe to omit) | — |
| — | — | `obBearColor` | `in_73` | color (cosmetic, safe to omit) | — |
| — | — | `sweepColor` | `in_74` | color (cosmetic, safe to omit) | — |
| — | — | `showInfoPanel` | `in_75` | **MISSING in Go** | — |
| — | — | `chartLabelSize` | `in_76` | **MISSING in Go** | — |
| — | — | `panelTextSize` | `in_77` | **MISSING in Go** | — |

#### Go parser reads from periods[] (getField aliases)

`BOSCount`, `CHoCHCount`, `Close`, `bosCount`, `chochCount`, `close`

#### Go Structure keys produced

`bosCount`, `chochCount`

#### Reference payload schema (from captured dump)

Dump: `skill-analysis/dumps/ict-auto-validated-smc/payload.json`

| key | type/shape |
|-----|-----------|
| `status` | `str` |
| `exitCode` | `int` |
| `timestamp` | `str` |
| `execution` | `object{durationMs,attempts}` |
| `agentContext` | `object{workflow,htfTimeframe,modelVersion,symbol,timeframe}` |
| `market` | `object{lastPrice,bias,zone}` |
| `structure` | `object{direction,text,lastBreak,lastBreakText,htf,alignment...}` |
| `zones` | `object{orderBlocks,fvgs,breakers,oteZones}` |
| `signals` | `object{grades,markers}` |
| `opportunities` | `list[any]` |
| `narrative` | `object{marketStructure,primaryOpportunity,warnings,watchlist}` |
| `validation` | `object{passed,checks,warnings}` |
| `conformance` | `object{hasValidStructure,htfAligned,hasStructuralZones,agenticScore}` |
| `schemaVersion` | `str` |

#### Go `SkillResult` schema (target output)

| field | type |
|-------|------|
| `status` | string |
| `workflow` | string |
| `market.lastPrice` | any |
| `market.bias` | string |
| `structure` | map[string]any (per-skill keys above) |
| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |
| `narrative.marketStructure` | string |
| `narrative.primaryOpportunity` | string |
| `narrative.warnings[]` | string |
| `narrative.watchlist[]` | string |
| `validation.passed` | bool |
| `validation.warnings[]` | string |
| `conformance.hasValidData` | bool |
| `conformance.agenticScore` | float64 |
| `raw` | map (omitted in agent output) |

#### Discrepancies & fix recommendations

- **Historical JS input `showInternalStructure` (`in_4`) missing in Go.** Consider adding `InputDef{Name: "showInternalStructure", TVInputID: "in_4", Type: "bool", Default: false}`.
- **Historical JS input `requireBodyClose` (`in_5`) missing in Go.** Consider adding `InputDef{Name: "requireBodyClose", TVInputID: "in_5", Type: "bool", Default: false}`.
- **Historical JS input `htfSwingLen` (`in_8`) missing in Go.** Consider adding `InputDef{Name: "htfSwingLen", TVInputID: "in_8", Type: "int", Default: 10}`.
- **Historical JS input `showHTFStructure` (`in_9`) missing in Go.** Consider adding `InputDef{Name: "showHTFStructure", TVInputID: "in_9", Type: "bool", Default: true}`.
- **Historical JS input `obMaxCount` (`in_11`) missing in Go.** Consider adding `InputDef{Name: "obMaxCount", TVInputID: "in_11", Type: "int", Default: 5}`.
- **Historical JS input `requireSweep` (`in_12`) missing in Go.** Consider adding `InputDef{Name: "requireSweep", TVInputID: "in_12", Type: "bool", Default: true}`.
- **Historical JS input `requireDisplacement` (`in_13`) missing in Go.** Consider adding `InputDef{Name: "requireDisplacement", TVInputID: "in_13", Type: "bool", Default: true}`.
- **Historical JS input `showMitigated` (`in_14`) missing in Go.** Consider adding `InputDef{Name: "showMitigated", TVInputID: "in_14", Type: "bool", Default: false}`.
- **Historical JS input `brkMaxCount` (`in_16`) missing in Go.** Consider adding `InputDef{Name: "brkMaxCount", TVInputID: "in_16", Type: "int", Default: 5}`.
- **Historical JS input `brkBullColor` (`in_17`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `brkBearColor` (`in_18`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `fvgMaxCount` (`in_20`) missing in Go.** Consider adding `InputDef{Name: "fvgMaxCount", TVInputID: "in_20", Type: "int", Default: 5}`.
- **Historical JS input `fvgMinATRMult` (`in_21`) missing in Go.** Consider adding `InputDef{Name: "fvgMinATRMult", TVInputID: "in_21", Type: "float", Default: 1}`.
- **Historical JS input `showCE` (`in_22`) missing in Go.** Consider adding `InputDef{Name: "showCE", TVInputID: "in_22", Type: "bool", Default: true}`.
- **Historical JS input `showMitigatedFVG` (`in_23`) missing in Go.** Consider adding `InputDef{Name: "showMitigatedFVG", TVInputID: "in_23", Type: "bool", Default: false}`.
- **Historical JS input `showIFVG` (`in_24`) missing in Go.** Consider adding `InputDef{Name: "showIFVG", TVInputID: "in_24", Type: "bool", Default: true}`.
- **Historical JS input `ifvgColor` (`in_25`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `showBPR` (`in_26`) missing in Go.** Consider adding `InputDef{Name: "showBPR", TVInputID: "in_26", Type: "bool", Default: true}`.
- **Historical JS input `bprColor` (`in_27`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `showLiquidity` (`in_28`) missing in Go.** Consider adding `InputDef{Name: "showLiquidity", TVInputID: "in_28", Type: "bool", Default: true}`.
- **Historical JS input `showEQHL` (`in_29`) missing in Go.** Consider adding `InputDef{Name: "showEQHL", TVInputID: "in_29", Type: "bool", Default: true}`.
- **Historical JS input `eqTolerance` (`in_30`) missing in Go.** Consider adding `InputDef{Name: "eqTolerance", TVInputID: "in_30", Type: "float", Default: 0.15}`.
- **Historical JS input `showSweeps` (`in_31`) missing in Go.** Consider adding `InputDef{Name: "showSweeps", TVInputID: "in_31", Type: "bool", Default: true}`.
- **Historical JS input `sweepRequireWickReject` (`in_32`) missing in Go.** Consider adding `InputDef{Name: "sweepRequireWickReject", TVInputID: "in_32", Type: "bool", Default: false}`.
- **Historical JS input `showIDM` (`in_33`) missing in Go.** Consider adding `InputDef{Name: "showIDM", TVInputID: "in_33", Type: "bool", Default: true}`.
- **Historical JS input `idmColor` (`in_34`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `idmMaxCount` (`in_35`) missing in Go.** Consider adding `InputDef{Name: "idmMaxCount", TVInputID: "in_35", Type: "int", Default: 5}`.
- **Historical JS input `showPD` (`in_36`) missing in Go.** Consider adding `InputDef{Name: "showPD", TVInputID: "in_36", Type: "bool", Default: true}`.
- **Historical JS input `showEQ` (`in_37`) missing in Go.** Consider adding `InputDef{Name: "showEQ", TVInputID: "in_37", Type: "bool", Default: true}`.
- **Historical JS input `showSessionLevels` (`in_38`) missing in Go.** Consider adding `InputDef{Name: "showSessionLevels", TVInputID: "in_38", Type: "bool", Default: true}`.
- **Historical JS input `showPDHL` (`in_39`) missing in Go.** Consider adding `InputDef{Name: "showPDHL", TVInputID: "in_39", Type: "bool", Default: true}`.
- **Historical JS input `showPWHL` (`in_40`) missing in Go.** Consider adding `InputDef{Name: "showPWHL", TVInputID: "in_40", Type: "bool", Default: true}`.
- **Historical JS input `slPDColor` (`in_41`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `slPWColor` (`in_42`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `showKZ` (`in_43`) missing in Go.** Consider adding `InputDef{Name: "showKZ", TVInputID: "in_43", Type: "bool", Default: true}`.
- **Historical JS input `kzAsian` (`in_44`) missing in Go.** Consider adding `InputDef{Name: "kzAsian", TVInputID: "in_44", Type: "bool", Default: false}`.
- **Historical JS input `kzLondon` (`in_45`) missing in Go.** Consider adding `InputDef{Name: "kzLondon", TVInputID: "in_45", Type: "bool", Default: true}`.
- **Historical JS input `kzNYAM` (`in_46`) missing in Go.** Consider adding `InputDef{Name: "kzNYAM", TVInputID: "in_46", Type: "bool", Default: true}`.
- **Historical JS input `kzNYPM` (`in_47`) missing in Go.** Consider adding `InputDef{Name: "kzNYPM", TVInputID: "in_47", Type: "bool", Default: false}`.
- **Historical JS input `kzTransparency` (`in_48`) missing in Go.** Consider adding `InputDef{Name: "kzTransparency", TVInputID: "in_48", Type: "int", Default: 92}`.
- **Historical JS input `oteFibHigh` (`in_50`) missing in Go.** Consider adding `InputDef{Name: "oteFibHigh", TVInputID: "in_50", Type: "float", Default: 0.786}`.
- **Historical JS input `oteFibLow` (`in_51`) missing in Go.** Consider adding `InputDef{Name: "oteFibLow", TVInputID: "in_51", Type: "float", Default: 0.618}`.
- **Historical JS input `showOTEFibs` (`in_52`) missing in Go.** Consider adding `InputDef{Name: "showOTEFibs", TVInputID: "in_52", Type: "bool", Default: true}`.
- **Historical JS input `oteMaxCount` (`in_53`) missing in Go.** Consider adding `InputDef{Name: "oteMaxCount", TVInputID: "in_53", Type: "int", Default: 3}`.
- **Historical JS input `oteBullColor` (`in_54`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `oteBearColor` (`in_55`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `requireHTFAlign` (`in_58`) missing in Go.** Consider adding `InputDef{Name: "requireHTFAlign", TVInputID: "in_58", Type: "bool", Default: true}`.
- **Historical JS input `requireKZActive` (`in_59`) missing in Go.** Consider adding `InputDef{Name: "requireKZActive", TVInputID: "in_59", Type: "bool", Default: false}`.
- **Historical JS input `requireCISD` (`in_60`) missing in Go.** Consider adding `InputDef{Name: "requireCISD", TVInputID: "in_60", Type: "bool", Default: false}`.
- **Historical JS input `showSigSL` (`in_61`) missing in Go.** Consider adding `InputDef{Name: "showSigSL", TVInputID: "in_61", Type: "bool", Default: true}`.
- **Historical JS input `showSigTP` (`in_62`) missing in Go.** Consider adding `InputDef{Name: "showSigTP", TVInputID: "in_62", Type: "bool", Default: true}`.
- **Historical JS input `sigLongColor` (`in_63`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `sigShortColor` (`in_64`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `sigCooldown` (`in_65`) missing in Go.** Consider adding `InputDef{Name: "sigCooldown", TVInputID: "in_65", Type: "int", Default: 10}`.
- **Historical JS input `showConfluence` (`in_66`) missing in Go.** Consider adding `InputDef{Name: "showConfluence", TVInputID: "in_66", Type: "bool", Default: true}`.
- **Historical JS input `minScore` (`in_67`) missing in Go.** Consider adding `InputDef{Name: "minScore", TVInputID: "in_67", Type: "int", Default: 3}`.
- **Historical JS input `bullColor` (`in_68`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `bearColor` (`in_69`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `fvgBullColor` (`in_70`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `fvgBearColor` (`in_71`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `obBullColor` (`in_72`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `obBearColor` (`in_73`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `sweepColor` (`in_74`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `showInfoPanel` (`in_75`) missing in Go.** Consider adding `InputDef{Name: "showInfoPanel", TVInputID: "in_75", Type: "bool", Default: true}`.
- **Historical JS input `chartLabelSize` (`in_76`) missing in Go.** Consider adding `InputDef{Name: "chartLabelSize", TVInputID: "in_76", Type: "string", Default: small}`.
- **Historical JS input `panelTextSize` (`in_77`) missing in Go.** Consider adding `InputDef{Name: "panelTextSize", TVInputID: "in_77", Type: "string", Default: small}`.
- **Reference payload has rich keys not in Go SkillResult:** `signals, zones`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.

#### Captured `--help` output

```
◇ injected env (13) from .env // tip: ◈ secrets for agents [www.dotenvx.com]

ICT Auto-Validated SMC — Standalone Runner
===========================================

Usage:
  node ict-auto-validated-smc.cjs <SYMBOL> [options]

Arguments:
  SYMBOL                    Trading pair (default: BTCUSDT)

Options:
  --tf <timeframe>          Timeframe (default: 15m)
  --bars <n>                Number of chart bars (default: 500)
  --json                    Output JSON
  --agent                   Agent mode
  --out <file>              Write JSON to file
  --verbose, -v             Verbose output
  --dry-run                 Skip connection
  --help, -h                Show this help

Examples:
  node ict-auto-validated-smc.cjs BTCUSDT
  node ict-auto-validated-smc.cjs ETHUSDT --tf 1h --bars 1000 --json
  node ict-auto-validated-smc.cjs BTCUSDT --agent
```

#### Original SKILL.md excerpt (first 1500 chars)

```markdown
---
name: ict-auto-validated-smc
description: |
  Use the ICT Auto-Validated SMC TradingView indicator to analyze any symbol/timeframe and extract Smart Money Concepts structural trading signals.
version: 1.0.0
license: MIT
author: TradingView Pine Skills
compatibility: Node.js 18+ with tv-optimized.cjs, tv.cjs, agent-output.cjs and .env (SESSION, SIGNATURE) at project root
metadata:
  hermes:
    tags: [trading, tradingview, pine-script, ict, smc, bos-choch]
    category: trading
required_environment_variables:
  - name: SESSION
    prompt: TradingView session cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid
    required_for: full functionality
  - name: SIGNATURE
    prompt: TradingView signature cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid_sign
    required_for: full functionality
---

# ICT Auto-Validated SMC — Trading Opportunity Finder

## When to Use

Helps the user run the standalone `ict-auto-validated-smc.cjs` script against any TradingView symbol and timeframe, then interprets the structured output to surface SMC-based trading setups. The output includes:

- **Structure State** — current directional bias, last break type (BOS/CHoCH), HTF alignment
- **Structural Zones** — Order Blocks, FVGs, Breakers, BPRs, OTE zones with price levels
- **Validation Metrics** — sweep requirement, displacement requirement, HTF check, min score
- **Grade Signals** — Long/Short 
```

---

### 2.7 `tv mtf` — `xauusd-mtf-trend`

- **Synopsis:** XAUUSD Multi-Timeframe Trend Dashboard
- **Pine ID:** `PUB;d1ad30c0261f49f297357f8aa2a7854a`  (reference Pine ID: `PUB;d1ad30c0261f49f297357f8aa2a7854a`)
- **Workflow ID:** `xauusd-mtf-trend`  (captured reference: `xauusd-mtf-trend`)
- **Go parser:** `internal/skill/parsers/mtf.go`  → func `parseMTF` / format `formatMTF`
- **Historical JS runner:** `/Volumes/ExMac/code/tradingview/js-experiment06/xauusd-mtf-trend.cjs`
- **Historical sample command:** `node xauusd-mtf-trend.cjs XAUUSD --tf 1h --bars 500 --agent --json`
- **Captured reference run:** rc=0 parsed_via=bracket hint=xauusd-mtf-trend
- **Reference payload top-level keys (11):** `agentContext, conformance, counts, execution, exitCode, mtf, narrative, opportunities, schemaVersion, status, timestamp`

#### Inputs (Go)

| name | tv_input_id | type | default |
|------|-------------|------|---------|
| `show_M15` | `in_0` | bool | `true` |
| `show_M30` | `in_1` | bool | `true` |
| `show_H1` | `in_2` | bool | `true` |
| `show_H4` | `in_3` | bool | `true` |
| `show_D1` | `in_4` | bool | `true` |
| `fastLength` | `in_5` | int | `10` |
| `slowLength` | `in_6` | int | `20` |
| `rsiLength` | `in_7` | int | `14` |

#### Inputs (historical JS reference — INPUT_MAP)

| variable | tvInputId | type | default |
|----------|-----------|------|---------|
| `show_M15` | `in_0` | `bool` | `true` |
| `show_M30` | `in_1` | `bool` | `true` |
| `show_H1` | `in_2` | `bool` | `true` |
| `show_H4` | `in_3` | `bool` | `true` |
| `show_D1` | `in_4` | `bool` | `true` |
| `fastLength` | `in_5` | `int` | `10` |
| `slowLength` | `in_6` | `int` | `20` |
| `rsiLength` | `in_7` | `int` | `14` |
| `rsiOverbought` | `in_8` | `float` | `70` |
| `rsiOversold` | `in_9` | `float` | `30` |
| `macdFastLength` | `in_10` | `int` | `12` |
| `macdSlowLength` | `in_11` | `int` | `26` |
| `macdSignalLength` | `in_12` | `int` | `9` |
| `bbLength` | `in_13` | `int` | `20` |
| `bbMultiplier` | `in_14` | `float` | `2` |
| `dmiLength` | `in_15` | `int` | `14` |
| `dmiSmoothing` | `in_16` | `int` | `14` |
| `sarStartValue` | `in_17` | `float` | `0.02` |
| `sarIncrement` | `in_18` | `float` | `0.02` |
| `sarMaxValue` | `in_19` | `float` | `0.2` |

#### Input Comparison

| Go name | Go TV id | Reference variable | Reference tvInputId | Name match | TV id match |
|---------|----------|-------------|--------------|------------|-------------|
| `show_M15` | `in_0` | `show_M15` | `in_0` | OK | OK |
| `show_M30` | `in_1` | `show_M30` | `in_1` | OK | OK |
| `show_H1` | `in_2` | `show_H1` | `in_2` | OK | OK |
| `show_H4` | `in_3` | `show_H4` | `in_3` | OK | OK |
| `show_D1` | `in_4` | `show_D1` | `in_4` | OK | OK |
| `fastLength` | `in_5` | `fastLength` | `in_5` | OK | OK |
| `slowLength` | `in_6` | `slowLength` | `in_6` | OK | OK |
| `rsiLength` | `in_7` | `rsiLength` | `in_7` | OK | OK |
| — | — | `rsiOverbought` | `in_8` | **MISSING in Go** | — |
| — | — | `rsiOversold` | `in_9` | **MISSING in Go** | — |
| — | — | `macdFastLength` | `in_10` | **MISSING in Go** | — |
| — | — | `macdSlowLength` | `in_11` | **MISSING in Go** | — |
| — | — | `macdSignalLength` | `in_12` | **MISSING in Go** | — |
| — | — | `bbLength` | `in_13` | **MISSING in Go** | — |
| — | — | `bbMultiplier` | `in_14` | **MISSING in Go** | — |
| — | — | `dmiLength` | `in_15` | **MISSING in Go** | — |
| — | — | `dmiSmoothing` | `in_16` | **MISSING in Go** | — |
| — | — | `sarStartValue` | `in_17` | **MISSING in Go** | — |
| — | — | `sarIncrement` | `in_18` | **MISSING in Go** | — |
| — | — | `sarMaxValue` | `in_19` | **MISSING in Go** | — |

#### Go parser reads from periods[] (getField aliases)

`Bias`, `Close`, `OverallBias`, `close`, `overallBias`

#### Go Structure keys produced

`overallBias`, `price`

#### Reference payload schema (from captured dump)

Dump: `skill-analysis/dumps/xauusd-mtf-trend/payload.json`

| key | type/shape |
|-----|-----------|
| `status` | `str` |
| `exitCode` | `int` |
| `timestamp` | `str` |
| `execution` | `object{durationMs,attempts}` |
| `agentContext` | `object{workflow,modelVersion,symbol,timeframe,htfTimeframe}` |
| `mtf` | `object{overallBias,entries,trendLabels,levels,avgStrength,netStrength}` |
| `counts` | `object{bullish,bearish,neutral}` |
| `opportunities` | `list[any]` |
| `narrative` | `object{marketStructure,primaryOpportunity,warnings,watchlist}` |
| `conformance` | `object{hasValidData,agenticScore}` |
| `schemaVersion` | `str` |

#### Go `SkillResult` schema (target output)

| field | type |
|-------|------|
| `status` | string |
| `workflow` | string |
| `market.lastPrice` | any |
| `market.bias` | string |
| `structure` | map[string]any (per-skill keys above) |
| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |
| `narrative.marketStructure` | string |
| `narrative.primaryOpportunity` | string |
| `narrative.warnings[]` | string |
| `narrative.watchlist[]` | string |
| `validation.passed` | bool |
| `validation.warnings[]` | string |
| `conformance.hasValidData` | bool |
| `conformance.agenticScore` | float64 |
| `raw` | map (omitted in agent output) |

#### Discrepancies & fix recommendations

- **Historical JS input `rsiOverbought` (`in_8`) missing in Go.** Consider adding `InputDef{Name: "rsiOverbought", TVInputID: "in_8", Type: "float", Default: 70}`.
- **Historical JS input `rsiOversold` (`in_9`) missing in Go.** Consider adding `InputDef{Name: "rsiOversold", TVInputID: "in_9", Type: "float", Default: 30}`.
- **Historical JS input `macdFastLength` (`in_10`) missing in Go.** Consider adding `InputDef{Name: "macdFastLength", TVInputID: "in_10", Type: "int", Default: 12}`.
- **Historical JS input `macdSlowLength` (`in_11`) missing in Go.** Consider adding `InputDef{Name: "macdSlowLength", TVInputID: "in_11", Type: "int", Default: 26}`.
- **Historical JS input `macdSignalLength` (`in_12`) missing in Go.** Consider adding `InputDef{Name: "macdSignalLength", TVInputID: "in_12", Type: "int", Default: 9}`.
- **Historical JS input `bbLength` (`in_13`) missing in Go.** Consider adding `InputDef{Name: "bbLength", TVInputID: "in_13", Type: "int", Default: 20}`.
- **Historical JS input `bbMultiplier` (`in_14`) missing in Go.** Consider adding `InputDef{Name: "bbMultiplier", TVInputID: "in_14", Type: "float", Default: 2}`.
- **Historical JS input `dmiLength` (`in_15`) missing in Go.** Consider adding `InputDef{Name: "dmiLength", TVInputID: "in_15", Type: "int", Default: 14}`.
- **Historical JS input `dmiSmoothing` (`in_16`) missing in Go.** Consider adding `InputDef{Name: "dmiSmoothing", TVInputID: "in_16", Type: "int", Default: 14}`.
- **Historical JS input `sarStartValue` (`in_17`) missing in Go.** Consider adding `InputDef{Name: "sarStartValue", TVInputID: "in_17", Type: "float", Default: 0.02}`.
- **Historical JS input `sarIncrement` (`in_18`) missing in Go.** Consider adding `InputDef{Name: "sarIncrement", TVInputID: "in_18", Type: "float", Default: 0.02}`.
- **Historical JS input `sarMaxValue` (`in_19`) missing in Go.** Consider adding `InputDef{Name: "sarMaxValue", TVInputID: "in_19", Type: "float", Default: 0.2}`.
- **Reference payload has rich keys not in Go SkillResult:** `counts, mtf`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.

#### Captured `--help` output

```
◇ injected env (13) from .env // tip: ⌘ override existing { override: true }

XAUUSD MTF Trend Dashboard — Standalone Runner
Usage: node xauusd-mtf-trend.cjs <SYMBOL> [options]
Options: --tf, --bars, --input key=value, --json, --agent, --out, --verbose, --dry-run, --help
Inputs: show_M15, show_M30, show_H1, show_H4, show_D1, fastLength, slowLength, rsiLength, rsiOverbought, rsiOversold, macdFastLength, macdSlowLength, macdSignalLength, bbLength, bbMultiplier, dmiLength, dmiSmoothing, sarStartValue, sarIncrement, sarMaxValue
        macdFastLength, macdSlowLength, macdSignalLength, bbLength, bbMultiplier, dmiLength, dmiSmoothing,
        sarStartValue, sarIncrement, sarMaxValue
```

#### Original SKILL.md excerpt (first 1500 chars)

```markdown
---
name: xauusd-mtf-trend
description: |
  Use the XAUUSD MTF Trend Dashboard TradingView indicator to analyze multi-timeframe trend alignment across multiple timeframes and identify high-probability directional bias for XAUUSD and other symbols.
version: 1.0.0
license: MIT
author: TradingView Pine Skills
compatibility: Node.js 18+ with tv-optimized.cjs, tv.cjs, agent-output.cjs and .env (SESSION, SIGNATURE) at project root
metadata:
  hermes:
    tags: [trading, tradingview, pine-script, multi-timeframe, trend-dashboard]
    category: trading
required_environment_variables:
  - name: SESSION
    prompt: TradingView session cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid
    required_for: full functionality
  - name: SIGNATURE
    prompt: TradingView signature cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid_sign
    required_for: full functionality
---

# XAUUSD MTF Trend Dashboard — Trading Opportunity Finder

## When to Use

Helps the user run the standalone `xauusd-mtf-trend.cjs` script against any TradingView symbol and timeframe, then interprets the structured output to surface high-probability trading setups based on multi-timeframe trend alignment. The output includes:

- **MTF Entries** — trend readings across multiple timeframes (from graphic tables)
- **Overall Bias** — STRONGLY_BULLISH / STRONGLY_BEARISH / BULLISH / BEARISH / NEUTRAL
- **Trend Labels** — annot
```

---

### 2.8 `tv quantum` — `quantum-ribbon`

- **Synopsis:** Quantum Ribbon Lite — 5-layer EMA ribbon alignment
- **Pine ID:** `PUB;91e003af510345f299e5846773538206`  (reference Pine ID: `PUB;91e003af510345f299e5846773538206`)
- **Workflow ID:** `quantum-ribbon`  (captured reference: `quantum-ribbon`)
- **Go parser:** `internal/skill/parsers/quantum.go`  → func `parseQuantum` / format `formatQuantum`
- **Historical JS runner:** `/Volumes/ExMac/code/tradingview/js-experiment06/quantum-ribbon.cjs`
- **Historical sample command:** `node quantum-ribbon.cjs BTCUSDT --tf 1h --bars 300 --agent --json`
- **Captured reference run:** rc=0 parsed_via=bracket hint=quantum-ribbon
- **Reference payload top-level keys (13):** `agentContext, conformance, crossovers, execution, exitCode, market, narrative, opportunities, ribbon, schemaVersion, status, table, timestamp`

#### Inputs (Go)

| name | tv_input_id | type | default |
|------|-------------|------|---------|
| `i_sensitivity` | `in_0` | int | `5` |
| `i_stop_distance` | `in_1` | string | `"Normal"` |
| `i_target_rr` | `in_2` | string | `"2R"` |
| `i_show_table` | `in_3` | bool | `true` |

#### Inputs (historical JS reference — INPUT_MAP)

| variable | tvInputId | type | default |
|----------|-----------|------|---------|
| `i_sensitivity` | `in_0` | `int` | `5` |
| `i_stop_distance` | `in_1` | `string` | `Normal` |
| `i_target_rr` | `in_2` | `string` | `2R` |
| `i_show_table` | `in_3` | `bool` | `true` |
| `i_table_size` | `in_4` | `string` | `Small` |
| `i_show_ribbon_state` | `in_5` | `bool` | `true` |
| `i_show_lines` | `in_6` | `bool` | `true` |
| `i_entry_line_color` | `in_7` | `color` | `color.white` |
| `i_entry_line_opacity` | `in_8` | `int` | `100` |
| `i_entry_line_width` | `in_9` | `int` | `2` |
| `i_stop_line_color` | `in_10` | `color` | `color.red` |
| `i_stop_line_opacity` | `in_11` | `int` | `100` |
| `i_stop_line_width` | `in_12` | `int` | `2` |
| `i_tp_line_color` | `in_13` | `color` | `color.green` |
| `i_tp_line_opacity` | `in_14` | `int` | `100` |
| `i_tp_line_width` | `in_15` | `int` | `2` |
| `i_table_bg_color` | `in_16` | `color` | `color.white` |
| `i_table_bg_opacity` | `in_17` | `int` | `100` |
| `i_table_text_color` | `in_18` | `color` | `color.black` |
| `i_table_border_color` | `in_19` | `color` | `color.gray` |
| `i_table_border_width` | `in_20` | `int` | `1` |

#### Input Comparison

| Go name | Go TV id | Reference variable | Reference tvInputId | Name match | TV id match |
|---------|----------|-------------|--------------|------------|-------------|
| `i_sensitivity` | `in_0` | `i_sensitivity` | `in_0` | OK | OK |
| `i_stop_distance` | `in_1` | `i_stop_distance` | `in_1` | OK | OK |
| `i_target_rr` | `in_2` | `i_target_rr` | `in_2` | OK | OK |
| `i_show_table` | `in_3` | `i_show_table` | `in_3` | OK | OK |
| — | — | `i_table_size` | `in_4` | **MISSING in Go** | — |
| — | — | `i_show_ribbon_state` | `in_5` | **MISSING in Go** | — |
| — | — | `i_show_lines` | `in_6` | **MISSING in Go** | — |
| — | — | `i_entry_line_color` | `in_7` | color (cosmetic, safe to omit) | — |
| — | — | `i_entry_line_opacity` | `in_8` | **MISSING in Go** | — |
| — | — | `i_entry_line_width` | `in_9` | **MISSING in Go** | — |
| — | — | `i_stop_line_color` | `in_10` | color (cosmetic, safe to omit) | — |
| — | — | `i_stop_line_opacity` | `in_11` | **MISSING in Go** | — |
| — | — | `i_stop_line_width` | `in_12` | **MISSING in Go** | — |
| — | — | `i_tp_line_color` | `in_13` | color (cosmetic, safe to omit) | — |
| — | — | `i_tp_line_opacity` | `in_14` | **MISSING in Go** | — |
| — | — | `i_tp_line_width` | `in_15` | **MISSING in Go** | — |
| — | — | `i_table_bg_color` | `in_16` | color (cosmetic, safe to omit) | — |
| — | — | `i_table_bg_opacity` | `in_17` | **MISSING in Go** | — |
| — | — | `i_table_text_color` | `in_18` | color (cosmetic, safe to omit) | — |
| — | — | `i_table_border_color` | `in_19` | color (cosmetic, safe to omit) | — |
| — | — | `i_table_border_width` | `in_20` | **MISSING in Go** | — |

#### Go parser reads from periods[] (getField aliases)

`Close`, `RibbonState`, `State`, `close`, `ribbonState`

#### Go Structure keys produced

`bias`, `price`, `ribbonState`

#### Reference payload schema (from captured dump)

Dump: `skill-analysis/dumps/quantum-ribbon/payload.json`

| key | type/shape |
|-----|-----------|
| `status` | `str` |
| `exitCode` | `int` |
| `timestamp` | `str` |
| `execution` | `object{durationMs,attempts}` |
| `agentContext` | `object{workflow,modelVersion,symbol,timeframe,htfTimeframe}` |
| `ribbon` | `object{state,bullishLayers,bearishLayers,totalLayers,layers}` |
| `table` | `object{ribbonState,status,entry,stop,target,wins...}` |
| `crossovers` | `object{count,lastCross,recent}` |
| `market` | `object{recommendation,currentBuy,currentSell,spread}` |
| `opportunities` | `list[object{rank,setup,direction,confidence,confluenceScore}]` |
| `narrative` | `object{marketStructure,primaryOpportunity,warnings,watchlist}` |
| `conformance` | `object{hasValidData,agenticScore}` |
| `schemaVersion` | `str` |

#### Go `SkillResult` schema (target output)

| field | type |
|-------|------|
| `status` | string |
| `workflow` | string |
| `market.lastPrice` | any |
| `market.bias` | string |
| `structure` | map[string]any (per-skill keys above) |
| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |
| `narrative.marketStructure` | string |
| `narrative.primaryOpportunity` | string |
| `narrative.warnings[]` | string |
| `narrative.watchlist[]` | string |
| `validation.passed` | bool |
| `validation.warnings[]` | string |
| `conformance.hasValidData` | bool |
| `conformance.agenticScore` | float64 |
| `raw` | map (omitted in agent output) |

#### Discrepancies & fix recommendations

- **Historical JS input `i_table_size` (`in_4`) missing in Go.** Consider adding `InputDef{Name: "i_table_size", TVInputID: "in_4", Type: "string", Default: Small}`.
- **Historical JS input `i_show_ribbon_state` (`in_5`) missing in Go.** Consider adding `InputDef{Name: "i_show_ribbon_state", TVInputID: "in_5", Type: "bool", Default: true}`.
- **Historical JS input `i_show_lines` (`in_6`) missing in Go.** Consider adding `InputDef{Name: "i_show_lines", TVInputID: "in_6", Type: "bool", Default: true}`.
- **Historical JS input `i_entry_line_color` (`in_7`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `i_entry_line_opacity` (`in_8`) missing in Go.** Consider adding `InputDef{Name: "i_entry_line_opacity", TVInputID: "in_8", Type: "int", Default: 100}`.
- **Historical JS input `i_entry_line_width` (`in_9`) missing in Go.** Consider adding `InputDef{Name: "i_entry_line_width", TVInputID: "in_9", Type: "int", Default: 2}`.
- **Historical JS input `i_stop_line_color` (`in_10`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `i_stop_line_opacity` (`in_11`) missing in Go.** Consider adding `InputDef{Name: "i_stop_line_opacity", TVInputID: "in_11", Type: "int", Default: 100}`.
- **Historical JS input `i_stop_line_width` (`in_12`) missing in Go.** Consider adding `InputDef{Name: "i_stop_line_width", TVInputID: "in_12", Type: "int", Default: 2}`.
- **Historical JS input `i_tp_line_color` (`in_13`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `i_tp_line_opacity` (`in_14`) missing in Go.** Consider adding `InputDef{Name: "i_tp_line_opacity", TVInputID: "in_14", Type: "int", Default: 100}`.
- **Historical JS input `i_tp_line_width` (`in_15`) missing in Go.** Consider adding `InputDef{Name: "i_tp_line_width", TVInputID: "in_15", Type: "int", Default: 2}`.
- **Historical JS input `i_table_bg_color` (`in_16`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `i_table_bg_opacity` (`in_17`) missing in Go.** Consider adding `InputDef{Name: "i_table_bg_opacity", TVInputID: "in_17", Type: "int", Default: 100}`.
- **Historical JS input `i_table_text_color` (`in_18`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `i_table_border_color` (`in_19`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `i_table_border_width` (`in_20`) missing in Go.** Consider adding `InputDef{Name: "i_table_border_width", TVInputID: "in_20", Type: "int", Default: 1}`.
- **Reference payload has rich keys not in Go SkillResult:** `crossovers, ribbon, table`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.

#### Captured `--help` output

```
◇ injected env (13) from .env // tip: ⌘ suppress logs { quiet: true }

Quantum Ribbon Lite — Standalone Runner
Usage: node quantum-ribbon.cjs <SYMBOL> [options]
Options: --tf, --bars, --input key=value, --json, --agent, --out, --verbose, --dry-run, --help
Inputs: i_sensitivity, i_stop_distance, i_target_rr, i_show_table, i_table_size, i_show_ribbon_state, i_show_lines, i_entry_line_color, i_entry_line_opacity, i_entry_line_width, i_stop_line_color, i_stop_line_opacity, i_stop_line_width, i_tp_line_color, i_tp_line_opacity, i_tp_line_width, i_table_bg_color, i_table_bg_opacity, i_table_text_color, i_table_border_color, i_table_border_width
```

#### Original SKILL.md excerpt (first 1500 chars)

```markdown
---
name: quantum-ribbon
description: |
  Use the Quantum Ribbon Lite TradingView indicator to analyze multi-layer EMA alignment, detect ribbon crossovers, and identify trend strength through 5-layer ribbon momentum analysis.
version: 1.0.0
license: MIT
author: TradingView Pine Skills
compatibility: Node.js 18+ with tv-optimized.cjs, tv.cjs, agent-output.cjs and .env (SESSION, SIGNATURE) at project root
metadata:
  hermes:
    tags: [trading, tradingview, pine-script, ema-ribbon, trend-strength]
    category: trading
required_environment_variables:
  - name: SESSION
    prompt: TradingView session cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid
    required_for: full functionality
  - name: SIGNATURE
    prompt: TradingView signature cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid_sign
    required_for: full functionality
---

# Quantum Ribbon Lite — Trading Opportunity Finder

## When to Use

Helps the user run the standalone `quantum-ribbon.cjs` script against any TradingView symbol and timeframe, then interprets the structured output to surface high-probability trading setups based on multi-layer EMA ribbon analysis. The output includes:

- **Ribbon State** — strong_bull / strong_bear / bull / bear / neutral classification
- **Layer Analysis** — per-layer fast vs slow EMA comparison (5 layers)
- **Crossover Detection** — layer 5 (slowest) cross signals
- **Momentum Slo
```

---

### 2.9 `tv shemar` — `shemar-smc-confidence`

- **Synopsis:** SHEMAR HMA ST + SMC Confidence — HMA, Supertrend, Kernel convergence
- **Pine ID:** `PUB;70f6e4e05f9c439c9d1f8fe26019357e`  (reference Pine ID: `PUB;70f6e4e05f9c439c9d1f8fe26019357e`)
- **Workflow ID:** `shemar-smc-confidence`  (captured reference: `shemar-smc-confidence`)
- **Go parser:** `internal/skill/parsers/shemar.go`  → func `parseShemar` / format `formatShemar`
- **Historical JS runner:** `/Volumes/ExMac/code/tradingview/js-experiment06/shemar-smc-confidence.cjs`
- **Historical sample command:** `node shemar-smc-confidence.cjs BTCUSDT --tf 1h --bars 300 --agent --json`
- **Captured reference run:** rc=0 parsed_via=bracket hint=shemar-smc-confidence
- **Reference payload top-level keys (14):** `agentContext, conformance, execution, exitCode, latestBars, narrative, opportunities, regime, schemaVersion, signals, status, structure, timeline, timestamp`

#### Inputs (Go)

| name | tv_input_id | type | default |
|------|-------------|------|---------|
| `hmaLength` | `in_0` | int | `50` |
| `atrPeriod` | `in_1` | int | `10` |
| `factor` | `in_2` | int | `3` |
| `enableShorts` | `in_3` | bool | `true` |
| `useStopEntry` | `in_4` | bool | `true` |
| `htfPeriod` | `in_6` | int | `50` |
| `sqzLength` | `in_7` | int | `20` |
| `sqzMult` | `in_8` | int | `2` |
| `kernelPeriod` | `in_13` | int | `30` |
| `confidenceThresh` | `in_14` | int | `30` |

#### Inputs (historical JS reference — INPUT_MAP)

| variable | tvInputId | type | default |
|----------|-----------|------|---------|
| `hmaLength` | `in_0` | `int` | `50` |
| `atrPeriod` | `in_1` | `int` | `10` |
| `factor` | `in_2` | `float` | `3` |
| `enableShorts` | `in_3` | `bool` | `true` |
| `useStopEntry` | `in_4` | `bool` | `true` |
| `stopEntryOffset` | `in_5` | `float` | `1` |
| `htfPeriod` | `in_6` | `int` | `50` |
| `sqzLength` | `in_7` | `int` | `20` |
| `sqzMult` | `in_8` | `int` | `2` |
| `sqzKCLength` | `in_9` | `int` | `20` |
| `sqzKCMult` | `in_10` | `float` | `1.5` |
| `sqzThreshold` | `in_11` | `float` | `0.8` |
| `sqzTF` | `in_12` | `string` | `5` |
| `kernelPeriod` | `in_13` | `int` | `30` |
| `confidenceThresh` | `in_14` | `int` | `30` |
| `showScore` | `in_15` | `bool` | `true` |

#### Input Comparison

| Go name | Go TV id | Reference variable | Reference tvInputId | Name match | TV id match |
|---------|----------|-------------|--------------|------------|-------------|
| `hmaLength` | `in_0` | `hmaLength` | `in_0` | OK | OK |
| `atrPeriod` | `in_1` | `atrPeriod` | `in_1` | OK | OK |
| `factor` | `in_2` | `factor` | `in_2` | OK | OK |
| `enableShorts` | `in_3` | `enableShorts` | `in_3` | OK | OK |
| `useStopEntry` | `in_4` | `useStopEntry` | `in_4` | OK | OK |
| `htfPeriod` | `in_6` | `htfPeriod` | `in_6` | OK | OK |
| `sqzLength` | `in_7` | `sqzLength` | `in_7` | OK | OK |
| `sqzMult` | `in_8` | `sqzMult` | `in_8` | OK | OK |
| `kernelPeriod` | `in_13` | `kernelPeriod` | `in_13` | OK | OK |
| `confidenceThresh` | `in_14` | `confidenceThresh` | `in_14` | OK | OK |
| — | — | `stopEntryOffset` | `in_5` | **MISSING in Go** | — |
| — | — | `sqzKCLength` | `in_9` | **MISSING in Go** | — |
| — | — | `sqzKCMult` | `in_10` | **MISSING in Go** | — |
| — | — | `sqzThreshold` | `in_11` | **MISSING in Go** | — |
| — | — | `sqzTF` | `in_12` | **MISSING in Go** | — |
| — | — | `showScore` | `in_15` | **MISSING in Go** | — |

#### Go parser reads from periods[] (getField aliases)

`BUY`, `Buy`, `BuySignal`, `Close`, `SELL`, `Sell`, `SellSignal`, `close`

#### Go Structure keys produced

`buyCount`, `buySignal`, `sellCount`, `sellSignal`

#### Reference payload schema (from captured dump)

Dump: `skill-analysis/dumps/shemar-smc-confidence/payload.json`

| key | type/shape |
|-----|-----------|
| `status` | `str` |
| `exitCode` | `int` |
| `timestamp` | `str` |
| `execution` | `object{durationMs,attempts}` |
| `agentContext` | `object{workflow,modelVersion,symbol,timeframe,htfTimeframe}` |
| `signals` | `object{rawBuys,rawSells,filteredBuys,filteredSells,filterRatio}` |
| `structure` | `object{bosEvents,recentBOS,latestSqueeze}` |
| `regime` | `object{kernelTrend,htfAligned,confidenceScore}` |
| `timeline` | `list[object{rawSignal,filteredSignal,closeSignal}]` |
| `latestBars` | `list[object{hma,supertrend,kernel,filteredBuy,filteredSell}]` |
| `opportunities` | `list[object{rank,setup,direction,confidence,confluenceScore}]` |
| `narrative` | `object{marketStructure,primaryOpportunity,warnings,watchlist}` |
| `conformance` | `object{hasValidData,agenticScore}` |
| `schemaVersion` | `str` |

#### Go `SkillResult` schema (target output)

| field | type |
|-------|------|
| `status` | string |
| `workflow` | string |
| `market.lastPrice` | any |
| `market.bias` | string |
| `structure` | map[string]any (per-skill keys above) |
| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |
| `narrative.marketStructure` | string |
| `narrative.primaryOpportunity` | string |
| `narrative.warnings[]` | string |
| `narrative.watchlist[]` | string |
| `validation.passed` | bool |
| `validation.warnings[]` | string |
| `conformance.hasValidData` | bool |
| `conformance.agenticScore` | float64 |
| `raw` | map (omitted in agent output) |

#### Discrepancies & fix recommendations

- **Historical JS input `stopEntryOffset` (`in_5`) missing in Go.** Consider adding `InputDef{Name: "stopEntryOffset", TVInputID: "in_5", Type: "float", Default: 1}`.
- **Historical JS input `sqzKCLength` (`in_9`) missing in Go.** Consider adding `InputDef{Name: "sqzKCLength", TVInputID: "in_9", Type: "int", Default: 20}`.
- **Historical JS input `sqzKCMult` (`in_10`) missing in Go.** Consider adding `InputDef{Name: "sqzKCMult", TVInputID: "in_10", Type: "float", Default: 1.5}`.
- **Historical JS input `sqzThreshold` (`in_11`) missing in Go.** Consider adding `InputDef{Name: "sqzThreshold", TVInputID: "in_11", Type: "float", Default: 0.8}`.
- **Historical JS input `sqzTF` (`in_12`) missing in Go.** Consider adding `InputDef{Name: "sqzTF", TVInputID: "in_12", Type: "string", Default: 5}`.
- **Historical JS input `showScore` (`in_15`) missing in Go.** Consider adding `InputDef{Name: "showScore", TVInputID: "in_15", Type: "bool", Default: true}`.
- **Input `factor` type mismatch.** Go=`int` JS=`float`.
- **Reference payload has rich keys not in Go SkillResult:** `latestBars, regime, signals, timeline`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.

#### Captured `--help` output

```
◇ injected env (13) from .env // tip: ⌘ enable debugging { debug: true }

Shemar SMC Confidence — Standalone Runner
Usage: node shemar-smc-confidence.cjs <SYMBOL> [options]
Options: --tf, --bars, --input key=value, --json, --agent, --out, --verbose, --dry-run, --help
Inputs: hmaLength, atrPeriod, factor, enableShorts, useStopEntry, stopEntryOffset, htfPeriod, sqzLength, sqzMult, sqzKCLength, sqzKCMult, sqzThreshold, sqzTF, kernelPeriod, confidenceThresh, showScore
```

#### Original SKILL.md excerpt (first 1500 chars)

```markdown
---
name: shemar-smc-confidence
description: |
  Use the SHEMAR HMA ST + SMC Confidence Filter TradingView indicator to analyze HMA, Supertrend, and Kernel convergence for high-confidence filtered trading signals.
version: 1.0.0
license: MIT
author: TradingView Pine Skills
compatibility: Node.js 18+ with tv-optimized.cjs, tv.cjs, agent-output.cjs and .env (SESSION, SIGNATURE) at project root
metadata:
  hermes:
    tags: [trading, tradingview, pine-script, hma, supertrend, kernel]
    category: trading
required_environment_variables:
  - name: SESSION
    prompt: TradingView session cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid
    required_for: full functionality
  - name: SIGNATURE
    prompt: TradingView signature cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid_sign
    required_for: full functionality
---

# SHEMAR HMA ST + SMC Confidence Filter — Trading Opportunity Finder

## When to Use

Helps the user run the standalone `shemar-smc-confidence.cjs` script against any TradingView symbol and timeframe, then interprets the structured output to surface high-probability trading setups based on multi-indicator convergence. The output includes:

- **Alignment State** — FULLY_ALIGNED_BULLISH / FULLY_ALIGNED_BEARISH / MIXED
- **Indicator States** — HMA bullish/bearish, Supertrend bullish/bearish, Kernel position
- **Signal Counts** — raw buy/sell signals vs filtered (high
```

---

### 2.10 `tv smc` — `smart-money-concepts`

- **Synopsis:** Smart Money Concepts — BOS/CHoCH, FVG, Order Blocks
- **Pine ID:** `PUB;6daafb2cabe6419d98ae25229d2327f8`  (reference Pine ID: `PUB;6daafb2cabe6419d98ae25229d2327f8`)
- **Workflow ID:** `smart-money-concepts`  (captured reference: `smart-money-concepts`)
- **Go parser:** `internal/skill/parsers/smc.go`  → func `parseSMC` / format `formatSMC`
- **Historical JS runner:** `/Volumes/ExMac/code/tradingview/js-experiment06/smart-money-concepts.cjs`
- **Historical sample command:** `node smart-money-concepts.cjs BTCUSDT --tf 1h --bars 500 --agent --json --silent`
- **Captured reference run:** rc=0 parsed_via=delimiter hint=None
- **Reference payload top-level keys (14):** `_parserMeta, active, agentContext, conformance, execution, exitCode, levels, narrative, opportunities, recent, schemaVersion, status, structure, timestamp`

#### Inputs (Go)

| name | tv_input_id | type | default |
|------|-------------|------|---------|
| `showStructureInput` | `in_10` | bool | `true` |
| `showSwingBullInput` | `in_11` | string | `"ALL"` |
| `showSwingBearInput` | `in_13` | string | `"ALL"` |
| `showInternalOrderBlocksInput` | `in_19` | bool | `true` |
| `showSwingOrderBlocksInput` | `in_21` | bool | `false` |
| `showFairValueGapsInput` | `in_33` | bool | `true` |
| `fairValueGapsThresholdInput` | `in_34` | bool | `true` |
| `showEqualHighsLowsInput` | `in_29` | bool | `true` |
| `swingsLengthInput` | `in_17` | int | `50` |

#### Inputs (historical JS reference — INPUT_MAP)

| variable | tvInputId | type | default |
|----------|-----------|------|---------|
| `modeInput` | `in_0` | `string` | `HISTORICAL` |
| `styleInput` | `in_1` | `string` | `COLORED` |
| `showTrendInput` | `in_2` | `bool` | `false` |
| `showInternalsInput` | `in_3` | `bool` | `true` |
| `showInternalBullInput` | `in_4` | `string` | `ALL` |
| `internalBullColorInput` | `in_5` | `string` | `GREEN` |
| `showInternalBearInput` | `in_6` | `string` | `ALL` |
| `internalBearColorInput` | `in_7` | `string` | `RED` |
| `internalFilterConfluenceInput` | `in_8` | `bool` | `false` |
| `internalStructureSize` | `in_9` | `string` | `TINY` |
| `showStructureInput` | `in_10` | `bool` | `true` |
| `showSwingBullInput` | `in_11` | `string` | `ALL` |
| `swingBullColorInput` | `in_12` | `string` | `GREEN` |
| `showSwingBearInput` | `in_13` | `string` | `ALL` |
| `swingBearColorInput` | `in_14` | `string` | `RED` |
| `swingStructureSize` | `in_15` | `string` | `SMALL` |
| `showSwingsInput` | `in_16` | `bool` | `false` |
| `swingsLengthInput` | `in_17` | `int` | `50` |
| `showHighLowSwingsInput` | `in_18` | `bool` | `true` |
| `showInternalOrderBlocksInput` | `in_19` | `bool` | `true` |
| `internalOrderBlocksSizeInput` | `in_20` | `int` | `5` |
| `showSwingOrderBlocksInput` | `in_21` | `bool` | `false` |
| `swingOrderBlocksSizeInput` | `in_22` | `int` | `5` |
| `orderBlockFilterInput` | `in_23` | `string` | `Atr` |
| `orderBlockMitigationInput` | `in_24` | `string` | `HIGHLOW` |
| `internalBullishOrderBlockColor` | `in_25` | `color` | `color.new(#3179f5, 80)` |
| `internalBearishOrderBlockColor` | `in_26` | `color` | `color.new(#f77c80, 80)` |
| `swingBullishOrderBlockColor` | `in_27` | `color` | `color.new(#1848cc, 80)` |
| `swingBearishOrderBlockColor` | `in_28` | `color` | `color.new(#b22833, 80)` |
| `showEqualHighsLowsInput` | `in_29` | `bool` | `true` |
| `equalHighsLowsLengthInput` | `in_30` | `int` | `3` |
| `equalHighsLowsThresholdInput` | `in_31` | `float` | `0.1` |
| `equalHighsLowsSizeInput` | `in_32` | `string` | `TINY` |
| `showFairValueGapsInput` | `in_33` | `bool` | `true` |
| `fairValueGapsThresholdInput` | `in_34` | `bool` | `true` |
| `fairValueGapsTimeframeInput` | `in_35` | `timeframe` | `` |
| `fairValueGapsBullColorInput` | `in_36` | `color` | `color.new(#00ff68, 70)` |
| `fairValueGapsBearColorInput` | `in_37` | `color` | `color.new(#ff0008, 70)` |
| `fairValueGapsExtendInput` | `in_38` | `int` | `1` |
| `showDailyLevelsInput` | `in_39` | `bool` | `false` |
| `dailyLevelsStyleInput` | `in_40` | `string` | `SOLID` |
| `dailyLevelsColorInput` | `in_41` | `string` | `BLUE` |
| `showWeeklyLevelsInput` | `in_42` | `bool` | `false` |
| `weeklyLevelsStyleInput` | `in_43` | `string` | `SOLID` |
| `weeklyLevelsColorInput` | `in_44` | `string` | `BLUE` |
| `showMonthlyLevelsInput` | `in_45` | `bool` | `false` |
| `monthlyLevelsStyleInput` | `in_46` | `string` | `SOLID` |
| `monthlyLevelsColorInput` | `in_47` | `string` | `BLUE` |
| `showPremiumDiscountZonesInput` | `in_48` | `bool` | `false` |
| `premiumZoneColorInput` | `in_49` | `color` | `RED` |
| `equilibriumZoneColorInput` | `in_50` | `color` | `GRAY` |
| `discountZoneColorInput` | `in_51` | `color` | `GREEN` |

#### Input Comparison

| Go name | Go TV id | Reference variable | Reference tvInputId | Name match | TV id match |
|---------|----------|-------------|--------------|------------|-------------|
| `showStructureInput` | `in_10` | `showStructureInput` | `in_10` | OK | OK |
| `showSwingBullInput` | `in_11` | `showSwingBullInput` | `in_11` | OK | OK |
| `showSwingBearInput` | `in_13` | `showSwingBearInput` | `in_13` | OK | OK |
| `showInternalOrderBlocksInput` | `in_19` | `showInternalOrderBlocksInput` | `in_19` | OK | OK |
| `showSwingOrderBlocksInput` | `in_21` | `showSwingOrderBlocksInput` | `in_21` | OK | OK |
| `showFairValueGapsInput` | `in_33` | `showFairValueGapsInput` | `in_33` | OK | OK |
| `fairValueGapsThresholdInput` | `in_34` | `fairValueGapsThresholdInput` | `in_34` | OK | OK |
| `showEqualHighsLowsInput` | `in_29` | `showEqualHighsLowsInput` | `in_29` | OK | OK |
| `swingsLengthInput` | `in_17` | `swingsLengthInput` | `in_17` | OK | OK |
| — | — | `modeInput` | `in_0` | **MISSING in Go** | — |
| — | — | `styleInput` | `in_1` | **MISSING in Go** | — |
| — | — | `showTrendInput` | `in_2` | **MISSING in Go** | — |
| — | — | `showInternalsInput` | `in_3` | **MISSING in Go** | — |
| — | — | `showInternalBullInput` | `in_4` | **MISSING in Go** | — |
| — | — | `internalBullColorInput` | `in_5` | **MISSING in Go** | — |
| — | — | `showInternalBearInput` | `in_6` | **MISSING in Go** | — |
| — | — | `internalBearColorInput` | `in_7` | **MISSING in Go** | — |
| — | — | `internalFilterConfluenceInput` | `in_8` | **MISSING in Go** | — |
| — | — | `internalStructureSize` | `in_9` | **MISSING in Go** | — |
| — | — | `swingBullColorInput` | `in_12` | **MISSING in Go** | — |
| — | — | `swingBearColorInput` | `in_14` | **MISSING in Go** | — |
| — | — | `swingStructureSize` | `in_15` | **MISSING in Go** | — |
| — | — | `showSwingsInput` | `in_16` | **MISSING in Go** | — |
| — | — | `showHighLowSwingsInput` | `in_18` | **MISSING in Go** | — |
| — | — | `internalOrderBlocksSizeInput` | `in_20` | **MISSING in Go** | — |
| — | — | `swingOrderBlocksSizeInput` | `in_22` | **MISSING in Go** | — |
| — | — | `orderBlockFilterInput` | `in_23` | **MISSING in Go** | — |
| — | — | `orderBlockMitigationInput` | `in_24` | **MISSING in Go** | — |
| — | — | `internalBullishOrderBlockColor` | `in_25` | color (cosmetic, safe to omit) | — |
| — | — | `internalBearishOrderBlockColor` | `in_26` | color (cosmetic, safe to omit) | — |
| — | — | `swingBullishOrderBlockColor` | `in_27` | color (cosmetic, safe to omit) | — |
| — | — | `swingBearishOrderBlockColor` | `in_28` | color (cosmetic, safe to omit) | — |
| — | — | `equalHighsLowsLengthInput` | `in_30` | **MISSING in Go** | — |
| — | — | `equalHighsLowsThresholdInput` | `in_31` | **MISSING in Go** | — |
| — | — | `equalHighsLowsSizeInput` | `in_32` | **MISSING in Go** | — |
| — | — | `fairValueGapsTimeframeInput` | `in_35` | **MISSING in Go** | — |
| — | — | `fairValueGapsBullColorInput` | `in_36` | color (cosmetic, safe to omit) | — |
| — | — | `fairValueGapsBearColorInput` | `in_37` | color (cosmetic, safe to omit) | — |
| — | — | `fairValueGapsExtendInput` | `in_38` | **MISSING in Go** | — |
| — | — | `showDailyLevelsInput` | `in_39` | **MISSING in Go** | — |
| — | — | `dailyLevelsStyleInput` | `in_40` | **MISSING in Go** | — |
| — | — | `dailyLevelsColorInput` | `in_41` | **MISSING in Go** | — |
| — | — | `showWeeklyLevelsInput` | `in_42` | **MISSING in Go** | — |
| — | — | `weeklyLevelsStyleInput` | `in_43` | **MISSING in Go** | — |
| — | — | `weeklyLevelsColorInput` | `in_44` | **MISSING in Go** | — |
| — | — | `showMonthlyLevelsInput` | `in_45` | **MISSING in Go** | — |
| — | — | `monthlyLevelsStyleInput` | `in_46` | **MISSING in Go** | — |
| — | — | `monthlyLevelsColorInput` | `in_47` | **MISSING in Go** | — |
| — | — | `showPremiumDiscountZonesInput` | `in_48` | **MISSING in Go** | — |
| — | — | `premiumZoneColorInput` | `in_49` | color (cosmetic, safe to omit) | — |
| — | — | `equilibriumZoneColorInput` | `in_50` | color (cosmetic, safe to omit) | — |
| — | — | `discountZoneColorInput` | `in_51` | color (cosmetic, safe to omit) | — |

#### Go parser reads from periods[] (getField aliases)

`BOSCount`, `CHoCHCount`, `Close`, `FVGCount`, `OBCount`, `bosCount`, `chochCount`, `close`, `fvgCount`, `obCount`

#### Go Structure keys produced

`bosCount`, `chochCount`, `fvgCount`, `obCount`

#### Reference payload schema (from captured dump)

Dump: `skill-analysis/dumps/smart-money-concepts/payload.json`

| key | type/shape |
|-----|-----------|
| `status` | `str` |
| `exitCode` | `int` |
| `timestamp` | `str` |
| `execution` | `object{durationMs,attempts}` |
| `agentContext` | `object{workflow,modelVersion,symbol,timeframe,htfTimeframe}` |
| `structure` | `object{bias,bosCount,chochCount,fvgCount,obCount,eqhCount}` |
| `active` | `object{obCount,fvgCount}` |
| `recent` | `object{bos,choch,ob,fvg}` |
| `levels` | `object{eqh,trendLines}` |
| `opportunities` | `list[any]` |
| `narrative` | `object{marketStructure,primaryOpportunity,warnings,watchlist}` |
| `conformance` | `object{hasValidData,agenticScore}` |
| `schemaVersion` | `str` |
| `_parserMeta` | `object{schemaVersion,emittedAt,deterministic}` |

#### Go `SkillResult` schema (target output)

| field | type |
|-------|------|
| `status` | string |
| `workflow` | string |
| `market.lastPrice` | any |
| `market.bias` | string |
| `structure` | map[string]any (per-skill keys above) |
| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |
| `narrative.marketStructure` | string |
| `narrative.primaryOpportunity` | string |
| `narrative.warnings[]` | string |
| `narrative.watchlist[]` | string |
| `validation.passed` | bool |
| `validation.warnings[]` | string |
| `conformance.hasValidData` | bool |
| `conformance.agenticScore` | float64 |
| `raw` | map (omitted in agent output) |

#### Discrepancies & fix recommendations

- **Historical JS input `modeInput` (`in_0`) missing in Go.** Consider adding `InputDef{Name: "modeInput", TVInputID: "in_0", Type: "string", Default: HISTORICAL}`.
- **Historical JS input `styleInput` (`in_1`) missing in Go.** Consider adding `InputDef{Name: "styleInput", TVInputID: "in_1", Type: "string", Default: COLORED}`.
- **Historical JS input `showTrendInput` (`in_2`) missing in Go.** Consider adding `InputDef{Name: "showTrendInput", TVInputID: "in_2", Type: "bool", Default: false}`.
- **Historical JS input `showInternalsInput` (`in_3`) missing in Go.** Consider adding `InputDef{Name: "showInternalsInput", TVInputID: "in_3", Type: "bool", Default: true}`.
- **Historical JS input `showInternalBullInput` (`in_4`) missing in Go.** Consider adding `InputDef{Name: "showInternalBullInput", TVInputID: "in_4", Type: "string", Default: ALL}`.
- **Historical JS input `internalBullColorInput` (`in_5`) missing in Go.** Consider adding `InputDef{Name: "internalBullColorInput", TVInputID: "in_5", Type: "string", Default: GREEN}`.
- **Historical JS input `showInternalBearInput` (`in_6`) missing in Go.** Consider adding `InputDef{Name: "showInternalBearInput", TVInputID: "in_6", Type: "string", Default: ALL}`.
- **Historical JS input `internalBearColorInput` (`in_7`) missing in Go.** Consider adding `InputDef{Name: "internalBearColorInput", TVInputID: "in_7", Type: "string", Default: RED}`.
- **Historical JS input `internalFilterConfluenceInput` (`in_8`) missing in Go.** Consider adding `InputDef{Name: "internalFilterConfluenceInput", TVInputID: "in_8", Type: "bool", Default: false}`.
- **Historical JS input `internalStructureSize` (`in_9`) missing in Go.** Consider adding `InputDef{Name: "internalStructureSize", TVInputID: "in_9", Type: "string", Default: TINY}`.
- **Historical JS input `swingBullColorInput` (`in_12`) missing in Go.** Consider adding `InputDef{Name: "swingBullColorInput", TVInputID: "in_12", Type: "string", Default: GREEN}`.
- **Historical JS input `swingBearColorInput` (`in_14`) missing in Go.** Consider adding `InputDef{Name: "swingBearColorInput", TVInputID: "in_14", Type: "string", Default: RED}`.
- **Historical JS input `swingStructureSize` (`in_15`) missing in Go.** Consider adding `InputDef{Name: "swingStructureSize", TVInputID: "in_15", Type: "string", Default: SMALL}`.
- **Historical JS input `showSwingsInput` (`in_16`) missing in Go.** Consider adding `InputDef{Name: "showSwingsInput", TVInputID: "in_16", Type: "bool", Default: false}`.
- **Historical JS input `showHighLowSwingsInput` (`in_18`) missing in Go.** Consider adding `InputDef{Name: "showHighLowSwingsInput", TVInputID: "in_18", Type: "bool", Default: true}`.
- **Historical JS input `internalOrderBlocksSizeInput` (`in_20`) missing in Go.** Consider adding `InputDef{Name: "internalOrderBlocksSizeInput", TVInputID: "in_20", Type: "int", Default: 5}`.
- **Historical JS input `swingOrderBlocksSizeInput` (`in_22`) missing in Go.** Consider adding `InputDef{Name: "swingOrderBlocksSizeInput", TVInputID: "in_22", Type: "int", Default: 5}`.
- **Historical JS input `orderBlockFilterInput` (`in_23`) missing in Go.** Consider adding `InputDef{Name: "orderBlockFilterInput", TVInputID: "in_23", Type: "string", Default: Atr}`.
- **Historical JS input `orderBlockMitigationInput` (`in_24`) missing in Go.** Consider adding `InputDef{Name: "orderBlockMitigationInput", TVInputID: "in_24", Type: "string", Default: HIGHLOW}`.
- **Historical JS input `internalBullishOrderBlockColor` (`in_25`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `internalBearishOrderBlockColor` (`in_26`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `swingBullishOrderBlockColor` (`in_27`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `swingBearishOrderBlockColor` (`in_28`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `equalHighsLowsLengthInput` (`in_30`) missing in Go.** Consider adding `InputDef{Name: "equalHighsLowsLengthInput", TVInputID: "in_30", Type: "int", Default: 3}`.
- **Historical JS input `equalHighsLowsThresholdInput` (`in_31`) missing in Go.** Consider adding `InputDef{Name: "equalHighsLowsThresholdInput", TVInputID: "in_31", Type: "float", Default: 0.1}`.
- **Historical JS input `equalHighsLowsSizeInput` (`in_32`) missing in Go.** Consider adding `InputDef{Name: "equalHighsLowsSizeInput", TVInputID: "in_32", Type: "string", Default: TINY}`.
- **Historical JS input `fairValueGapsTimeframeInput` (`in_35`) missing in Go.** Consider adding `InputDef{Name: "fairValueGapsTimeframeInput", TVInputID: "in_35", Type: "timeframe", Default: }`.
- **Historical JS input `fairValueGapsBullColorInput` (`in_36`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `fairValueGapsBearColorInput` (`in_37`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `fairValueGapsExtendInput` (`in_38`) missing in Go.** Consider adding `InputDef{Name: "fairValueGapsExtendInput", TVInputID: "in_38", Type: "int", Default: 1}`.
- **Historical JS input `showDailyLevelsInput` (`in_39`) missing in Go.** Consider adding `InputDef{Name: "showDailyLevelsInput", TVInputID: "in_39", Type: "bool", Default: false}`.
- **Historical JS input `dailyLevelsStyleInput` (`in_40`) missing in Go.** Consider adding `InputDef{Name: "dailyLevelsStyleInput", TVInputID: "in_40", Type: "string", Default: SOLID}`.
- **Historical JS input `dailyLevelsColorInput` (`in_41`) missing in Go.** Consider adding `InputDef{Name: "dailyLevelsColorInput", TVInputID: "in_41", Type: "string", Default: BLUE}`.
- **Historical JS input `showWeeklyLevelsInput` (`in_42`) missing in Go.** Consider adding `InputDef{Name: "showWeeklyLevelsInput", TVInputID: "in_42", Type: "bool", Default: false}`.
- **Historical JS input `weeklyLevelsStyleInput` (`in_43`) missing in Go.** Consider adding `InputDef{Name: "weeklyLevelsStyleInput", TVInputID: "in_43", Type: "string", Default: SOLID}`.
- **Historical JS input `weeklyLevelsColorInput` (`in_44`) missing in Go.** Consider adding `InputDef{Name: "weeklyLevelsColorInput", TVInputID: "in_44", Type: "string", Default: BLUE}`.
- **Historical JS input `showMonthlyLevelsInput` (`in_45`) missing in Go.** Consider adding `InputDef{Name: "showMonthlyLevelsInput", TVInputID: "in_45", Type: "bool", Default: false}`.
- **Historical JS input `monthlyLevelsStyleInput` (`in_46`) missing in Go.** Consider adding `InputDef{Name: "monthlyLevelsStyleInput", TVInputID: "in_46", Type: "string", Default: SOLID}`.
- **Historical JS input `monthlyLevelsColorInput` (`in_47`) missing in Go.** Consider adding `InputDef{Name: "monthlyLevelsColorInput", TVInputID: "in_47", Type: "string", Default: BLUE}`.
- **Historical JS input `showPremiumDiscountZonesInput` (`in_48`) missing in Go.** Consider adding `InputDef{Name: "showPremiumDiscountZonesInput", TVInputID: "in_48", Type: "bool", Default: false}`.
- **Historical JS input `premiumZoneColorInput` (`in_49`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `equilibriumZoneColorInput` (`in_50`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `discountZoneColorInput` (`in_51`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Reference payload has rich keys not in Go SkillResult:** `_parserMeta, active, levels, recent`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.

#### Captured `--help` output

```
◇ injected env (13) from .env // tip: ⌘ enable debugging { debug: true }

Smart Money Concepts (LuxAlgo) — Standalone Runner
Usage: node smart-money-concepts.cjs <SYMBOL> [options]
Options: --tf, --bars, --input key=value, --json, --agent, --out, --verbose, --dry-run, --silent, --help
Inputs: modeInput, styleInput, showTrendInput, showInternalsInput, showInternalBullInput, internalBullColorInput, showInternalBearInput, internalBearColorInput, internalFilterConfluenceInput, internalStructureSize, showStructureInput, showSwingBullInput, swingBullColorInput, showSwingBearInput, swingBearColorInput, swingStructureSize, showSwingsInput, swingsLengthInput, showHighLowSwingsInput, showInternalOrderBlocksInput, internalOrderBlocksSizeInput, showSwingOrderBlocksInput, swingOrderBlocksSizeInput, orderBlockFilterInput, orderBlockMitigationInput, internalBullishOrderBlockColor, internalBearishOrderBlockColor, swingBullishOrderBlockColor, swingBearishOrderBlockColor, showEqualHighsLowsInput, equalHighsLowsLengthInput, equalHighsLowsThresholdInput, equalHighsLowsSizeInput, showFairValueGapsInput, fairValueGapsThresholdInput, fairValueGapsTimeframeInput, fairValueGapsBullColorInput, fairValueGaps
```

#### Original SKILL.md excerpt (first 1500 chars)

```markdown
---
name: smart-money-concepts
description: |
  Use the Smart Money Concepts [LuxAlgo] TradingView indicator to analyze market structure breaks (BOS/CHoCH), fair value gaps (FVG), order blocks (OB), and equal highs/lows for institutional-grade trade setups.
version: 1.0.0
license: MIT
author: TradingView Pine Skills
compatibility: Node.js 18+ with tv-optimized.cjs, tv.cjs, agent-output.cjs and .env (SESSION, SIGNATURE) at project root
metadata:
  hermes:
    tags: [trading, tradingview, pine-script, smc, order-blocks, fvg]
    category: trading
required_environment_variables:
  - name: SESSION
    prompt: TradingView session cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid
    required_for: full functionality
  - name: SIGNATURE
    prompt: TradingView signature cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid_sign
    required_for: full functionality
---

# Smart Money Concepts [LuxAlgo] — Trading Opportunity Finder

## When to Use

Helps the user run the standalone `smart-money-concepts.cjs` script against any TradingView symbol and timeframe, then interprets the structured output to surface high-probability trading setups based on institutional market structure analysis. The output includes:

- **Structure Events** — BOS (Break of Structure), CHoCH (Change of Character), Internal and Swing variants
- **Fair Value Gaps** — Bullish/Bearish FVGs for entry zones
- **Order Bl
```

---

### 2.11 `tv sniper` — `precision-sniper`

- **Synopsis:** Precision Sniper — EMA confluence with grade signals
- **Pine ID:** `PUB;1fc29950178c42a1a88f52a18161dd53`  (reference Pine ID: `PUB;1fc29950178c42a1a88f52a18161dd53`)
- **Workflow ID:** `ema-confluence-sniper`  (captured reference: `ema-confluence-sniper`)
- **Go parser:** `internal/skill/parsers/sniper.go`  → func `parseSniper` / format `formatSniper`
- **Historical JS runner:** `/Volumes/ExMac/code/tradingview/js-experiment06/precision-sniper.cjs`
- **Historical sample command:** `node precision-sniper.cjs BTCUSDT --preset auto --tf 15m --bars 300 --agent --json --silent`
- **Captured reference run:** rc=0 parsed_via=delimiter hint=None
- **Reference payload top-level keys (15):** `_parserMeta, agentContext, conformance, execution, exitCode, market, narrative, opportunities, schemaVersion, signals, status, structure, timestamp, tradePlan, validation`

#### Inputs (Go)

| name | tv_input_id | type | default |
|------|-------------|------|---------|
| `sourceInput` | `in_0` | source | `"close"` |
| `htfInput` | `in_1` | timeframe | `""` |
| `presetInput` | `in_2` | string | `"Auto"` |
| `emaFastLenInput` | `in_3` | int | `9` |
| `emaSlowLenInput` | `in_4` | int | `21` |
| `emaTrendLenInput` | `in_5` | int | `55` |
| `minScoreInput` | `in_6` | int | `5` |
| `rsiLenInput` | `in_7` | int | `13` |
| `gradeFilterInput` | `in_8` | string | `"All"` |
| `atrLenInput` | `in_10` | int | `14` |
| `slMultInput` | `in_11` | float | `1.5` |
| `tp1MultInput` | `in_12` | float | `1` |
| `tp2MultInput` | `in_13` | float | `2` |
| `tp3MultInput` | `in_14` | float | `3` |

#### Inputs (historical JS reference — INPUT_MAP)

| variable | tvInputId | type | default |
|----------|-----------|------|---------|
| `sourceInput` | `in_0` | `source` | `close` |
| `htfInput` | `in_1` | `timeframe` | `` |
| `presetInput` | `in_2` | `string` | `Auto` |
| `emaFastLenInput` | `in_3` | `int` | `9` |
| `emaSlowLenInput` | `in_4` | `int` | `21` |
| `emaTrendLenInput` | `in_5` | `int` | `55` |
| `minScoreInput` | `in_6` | `int` | `5` |
| `rsiLenInput` | `in_7` | `int` | `13` |
| `gradeFilterInput` | `in_8` | `string` | `All` |
| `hideCGradeInput` | `in_9` | `bool` | `true` |
| `atrLenInput` | `in_10` | `int` | `14` |
| `slMultInput` | `in_11` | `float` | `1.5` |
| `tp1MultInput` | `in_12` | `float` | `1` |
| `tp2MultInput` | `in_13` | `float` | `2` |
| `tp3MultInput` | `in_14` | `float` | `3` |
| `useTrailInput` | `in_15` | `bool` | `true` |
| `useStructureSLInput` | `in_16` | `bool` | `true` |
| `swingLookbackInput` | `in_17` | `int` | `10` |
| `themeInput` | `in_18` | `string` | `Auto` |
| `showSignalsInput` | `in_19` | `bool` | `true` |
| `signalSizeInput` | `in_20` | `string` | `Small` |
| `showTPSLInput` | `in_21` | `bool` | `true` |
| `showRibbonInput` | `in_22` | `bool` | `true` |
| `showTrailInput` | `in_23` | `bool` | `true` |
| `showBgInput` | `in_24` | `bool` | `false` |
| `showWatermarkInput` | `in_25` | `bool` | `true` |
| `showGradeInput` | `in_26` | `bool` | `true` |
| `labelOffsetInput` | `in_27` | `int` | `20` |
| `showDashInput` | `in_28` | `bool` | `true` |
| `showBtDashInput` | `in_29` | `bool` | `true` |
| `dashPosStr` | `in_30` | `string` | `Top Right` |
| `webhookInput` | `in_31` | `bool` | `false` |
| `bullColorInput` | `in_32` | `color` | `#00E676` |
| `bearColorInput` | `in_33` | `color` | `#FF5252` |
| `neutralColorInput` | `in_34` | `color` | `#FFEB3B` |

#### Input Comparison

| Go name | Go TV id | Reference variable | Reference tvInputId | Name match | TV id match |
|---------|----------|-------------|--------------|------------|-------------|
| `sourceInput` | `in_0` | `sourceInput` | `in_0` | OK | OK |
| `htfInput` | `in_1` | `htfInput` | `in_1` | OK | OK |
| `presetInput` | `in_2` | `presetInput` | `in_2` | OK | OK |
| `emaFastLenInput` | `in_3` | `emaFastLenInput` | `in_3` | OK | OK |
| `emaSlowLenInput` | `in_4` | `emaSlowLenInput` | `in_4` | OK | OK |
| `emaTrendLenInput` | `in_5` | `emaTrendLenInput` | `in_5` | OK | OK |
| `minScoreInput` | `in_6` | `minScoreInput` | `in_6` | OK | OK |
| `rsiLenInput` | `in_7` | `rsiLenInput` | `in_7` | OK | OK |
| `gradeFilterInput` | `in_8` | `gradeFilterInput` | `in_8` | OK | OK |
| `atrLenInput` | `in_10` | `atrLenInput` | `in_10` | OK | OK |
| `slMultInput` | `in_11` | `slMultInput` | `in_11` | OK | OK |
| `tp1MultInput` | `in_12` | `tp1MultInput` | `in_12` | OK | OK |
| `tp2MultInput` | `in_13` | `tp2MultInput` | `in_13` | OK | OK |
| `tp3MultInput` | `in_14` | `tp3MultInput` | `in_14` | OK | OK |
| — | — | `hideCGradeInput` | `in_9` | **MISSING in Go** | — |
| — | — | `useTrailInput` | `in_15` | **MISSING in Go** | — |
| — | — | `useStructureSLInput` | `in_16` | **MISSING in Go** | — |
| — | — | `swingLookbackInput` | `in_17` | **MISSING in Go** | — |
| — | — | `themeInput` | `in_18` | **MISSING in Go** | — |
| — | — | `showSignalsInput` | `in_19` | **MISSING in Go** | — |
| — | — | `signalSizeInput` | `in_20` | **MISSING in Go** | — |
| — | — | `showTPSLInput` | `in_21` | **MISSING in Go** | — |
| — | — | `showRibbonInput` | `in_22` | **MISSING in Go** | — |
| — | — | `showTrailInput` | `in_23` | **MISSING in Go** | — |
| — | — | `showBgInput` | `in_24` | **MISSING in Go** | — |
| — | — | `showWatermarkInput` | `in_25` | **MISSING in Go** | — |
| — | — | `showGradeInput` | `in_26` | **MISSING in Go** | — |
| — | — | `labelOffsetInput` | `in_27` | **MISSING in Go** | — |
| — | — | `showDashInput` | `in_28` | **MISSING in Go** | — |
| — | — | `showBtDashInput` | `in_29` | **MISSING in Go** | — |
| — | — | `dashPosStr` | `in_30` | **MISSING in Go** | — |
| — | — | `webhookInput` | `in_31` | **MISSING in Go** | — |
| — | — | `bullColorInput` | `in_32` | color (cosmetic, safe to omit) | — |
| — | — | `bearColorInput` | `in_33` | color (cosmetic, safe to omit) | — |
| — | — | `neutralColorInput` | `in_34` | color (cosmetic, safe to omit) | — |

#### Presets

| preset | go | js | match |
|--------|----|----|-------|
| `aggressive` | `{}` | `{'presetInput': 'Aggressive', 'minScoreInput': 3, 'slMultInput': 1.2}` | OK js-only:{'slMultInput', 'minScoreInput', 'presetInput'} |
| `auto` | `{}` | `{'presetInput': 'Auto'}` | OK js-only:{'presetInput'} |
| `conservative` | `{}` | `{'presetInput': 'Conservative', 'minScoreInput': 7, 'slMultInput': 2.0}` | OK js-only:{'slMultInput', 'minScoreInput', 'presetInput'} |
| `crypto` | `{}` | `{'presetInput': 'Crypto 24/7', 'slMultInput': 2.0}` | OK js-only:{'slMultInput', 'presetInput'} |
| `default` | `{}` | `{'presetInput': 'Auto', 'emaFastLenInput': 9, 'emaSlowLenInput': 21, 'emaTrendLenInput': 55, 'minScoreInput': 5, 'rsiLenInput': 13, 'atrLenInput': 14, 'slMultInput': 1.5, 'tp1MultInput': 1.0, 'tp2MultInput': 2.0, 'tp3MultInput': 3.0, 'useTrailInput': True, 'useStructureSLInput': True, 'swingLookbackInput': 10}` | OK js-only:{'atrLenInput', 'swingLookbackInput', 'rsiLenInput', 'emaSlowLenInput', 'emaTrendLenInput', 'slMultInput', 'presetInput', 'tp1MultInput', 'tp2MultInput', 'useTrailInput', 'emaFastLenInput', 'minScoreInput', 'tp3MultInput', 'useStructureSLInput'} |
| `scalping` | `{}` | `{'presetInput': 'Scalping', 'emaFastLenInput': 5, 'emaSlowLenInput': 13, 'atrLenInput': 10, 'slMultInput': 1.2}` | OK js-only:{'atrLenInput', 'emaFastLenInput', 'slMultInput', 'emaSlowLenInput', 'presetInput'} |
| `swing` | `{}` | `{'presetInput': 'Swing', 'emaFastLenInput': 21, 'emaSlowLenInput': 55, 'emaTrendLenInput': 200, 'atrLenInput': 21, 'slMultInput': 2.5}` | OK js-only:{'atrLenInput', 'emaFastLenInput', 'slMultInput', 'emaSlowLenInput', 'emaTrendLenInput', 'presetInput'} |

#### Go parser reads from periods[] (getField aliases)

`Buy_Signal`, `EMA_Fast`, `EMA_Slow`, `EMA_Trend`, `Sell_Signal`, `plot_0`, `plot_2`, `plot_5`, `plot_8`, `plot_9`

#### Go Structure keys produced

`buySignal`, `emaFast`, `emaSlow`, `emaTrend`, `score`, `sellSignal`

#### Reference payload schema (from captured dump)

Dump: `skill-analysis/dumps/precision-sniper/payload.json`

| key | type/shape |
|-----|-----------|
| `status` | `str` |
| `exitCode` | `int` |
| `timestamp` | `str` |
| `execution` | `object{durationMs,attempts}` |
| `agentContext` | `object{workflow,htfTimeframe,modelVersion,symbol,timeframe}` |
| `market` | `object{lastPrice,bias,htfBias,score,adx,volatility}` |
| `structure` | `object{}` |
| `signals` | `object{grades,markers}` |
| `opportunities` | `list[object{rank,setup,direction,confidence,confluenceScore}]` |
| `tradePlan` | `object{direction,entry,sl,tp1,tp2,tp3...}` |
| `narrative` | `object{marketStructure,primaryOpportunity,warnings,watchlist}` |
| `validation` | `object{passed,checks,warnings}` |
| `conformance` | `object{hasValidStructure,hasQualitySignal,htfAligned,agenticScore}` |
| `schemaVersion` | `str` |
| `_parserMeta` | `object{schemaVersion,emittedAt,deterministic}` |

#### Go `SkillResult` schema (target output)

| field | type |
|-------|------|
| `status` | string |
| `workflow` | string |
| `market.lastPrice` | any |
| `market.bias` | string |
| `structure` | map[string]any (per-skill keys above) |
| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |
| `narrative.marketStructure` | string |
| `narrative.primaryOpportunity` | string |
| `narrative.warnings[]` | string |
| `narrative.watchlist[]` | string |
| `validation.passed` | bool |
| `validation.warnings[]` | string |
| `conformance.hasValidData` | bool |
| `conformance.agenticScore` | float64 |
| `raw` | map (omitted in agent output) |

#### Discrepancies & fix recommendations

- **Historical JS input `hideCGradeInput` (`in_9`) missing in Go.** Consider adding `InputDef{Name: "hideCGradeInput", TVInputID: "in_9", Type: "bool", Default: true}`.
- **Historical JS input `useTrailInput` (`in_15`) missing in Go.** Consider adding `InputDef{Name: "useTrailInput", TVInputID: "in_15", Type: "bool", Default: true}`.
- **Historical JS input `useStructureSLInput` (`in_16`) missing in Go.** Consider adding `InputDef{Name: "useStructureSLInput", TVInputID: "in_16", Type: "bool", Default: true}`.
- **Historical JS input `swingLookbackInput` (`in_17`) missing in Go.** Consider adding `InputDef{Name: "swingLookbackInput", TVInputID: "in_17", Type: "int", Default: 10}`.
- **Historical JS input `themeInput` (`in_18`) missing in Go.** Consider adding `InputDef{Name: "themeInput", TVInputID: "in_18", Type: "string", Default: Auto}`.
- **Historical JS input `showSignalsInput` (`in_19`) missing in Go.** Consider adding `InputDef{Name: "showSignalsInput", TVInputID: "in_19", Type: "bool", Default: true}`.
- **Historical JS input `signalSizeInput` (`in_20`) missing in Go.** Consider adding `InputDef{Name: "signalSizeInput", TVInputID: "in_20", Type: "string", Default: Small}`.
- **Historical JS input `showTPSLInput` (`in_21`) missing in Go.** Consider adding `InputDef{Name: "showTPSLInput", TVInputID: "in_21", Type: "bool", Default: true}`.
- **Historical JS input `showRibbonInput` (`in_22`) missing in Go.** Consider adding `InputDef{Name: "showRibbonInput", TVInputID: "in_22", Type: "bool", Default: true}`.
- **Historical JS input `showTrailInput` (`in_23`) missing in Go.** Consider adding `InputDef{Name: "showTrailInput", TVInputID: "in_23", Type: "bool", Default: true}`.
- **Historical JS input `showBgInput` (`in_24`) missing in Go.** Consider adding `InputDef{Name: "showBgInput", TVInputID: "in_24", Type: "bool", Default: false}`.
- **Historical JS input `showWatermarkInput` (`in_25`) missing in Go.** Consider adding `InputDef{Name: "showWatermarkInput", TVInputID: "in_25", Type: "bool", Default: true}`.
- **Historical JS input `showGradeInput` (`in_26`) missing in Go.** Consider adding `InputDef{Name: "showGradeInput", TVInputID: "in_26", Type: "bool", Default: true}`.
- **Historical JS input `labelOffsetInput` (`in_27`) missing in Go.** Consider adding `InputDef{Name: "labelOffsetInput", TVInputID: "in_27", Type: "int", Default: 20}`.
- **Historical JS input `showDashInput` (`in_28`) missing in Go.** Consider adding `InputDef{Name: "showDashInput", TVInputID: "in_28", Type: "bool", Default: true}`.
- **Historical JS input `showBtDashInput` (`in_29`) missing in Go.** Consider adding `InputDef{Name: "showBtDashInput", TVInputID: "in_29", Type: "bool", Default: true}`.
- **Historical JS input `dashPosStr` (`in_30`) missing in Go.** Consider adding `InputDef{Name: "dashPosStr", TVInputID: "in_30", Type: "string", Default: Top Right}`.
- **Historical JS input `webhookInput` (`in_31`) missing in Go.** Consider adding `InputDef{Name: "webhookInput", TVInputID: "in_31", Type: "bool", Default: false}`.
- **Historical JS input `bullColorInput` (`in_32`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `bearColorInput` (`in_33`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `neutralColorInput` (`in_34`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Reference payload has rich keys not in Go SkillResult:** `_parserMeta, signals, tradePlan`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.

#### Captured `--help` output

```
Precision Sniper — Standalone Runner
=====================================

Usage:
  node precision-sniper.cjs <SYMBOL> [options]

Arguments:
  SYMBOL                    Trading pair (default: BTCUSDT)

Options:
  --tf <timeframe>          Timeframe (default: 15m)
  --bars <n>                Number of chart bars (default: 500)
  --preset <name>           Preset: auto, conservative, default, aggressive, scalping, swing, crypto (default: auto)
  --json                    Output JSON
  --agent                   Agent mode
  --out <file>              Write JSON to file
  --verbose, -v             Verbose output
  --dry-run                 Skip connection
  --help, -h                Show this help

Examples:
  node precision-sniper.cjs BTCUSDT
  node precision-sniper.cjs ETHUSDT --preset scalping --tf 5m
  node precision-sniper.cjs BTCUSDT --agent
```

#### Original SKILL.md excerpt (first 1500 chars)

```markdown
---
name: precision-sniper
description: |
  Use the Precision Sniper TradingView indicator to analyze any symbol/timeframe and extract grade-based confluence signals.
version: 1.0.0
license: MIT
author: TradingView Pine Skills
compatibility: Node.js 18+ with tv-optimized.cjs, tv.cjs, agent-output.cjs and .env (SESSION, SIGNATURE) at project root
metadata:
  hermes:
    tags: [trading, tradingview, pine-script, ema, rsi, confluence, graded-signals]
    category: trading
required_environment_variables:
  - name: SESSION
    prompt: TradingView session cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid
    required_for: full functionality
  - name: SIGNATURE
    prompt: TradingView signature cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid_sign
    required_for: full functionality
---

# Precision Sniper — Trading Opportunity Finder

## When to Use

Helps the user run the standalone `precision-sniper.cjs` script against any TradingView symbol and timeframe, then interprets the structured output to surface grade-based trading setups. The output includes:

- **Grade Signals** — Long/Short with A+, A, B, C letter grades
- **Trend State** — EMA fast/slow/trend alignment, HTF bias, ADX
- **Confluence Score** — 0-10 composite score for signal quality
- **Trade Levels** — ENTRY, SL, TP1-3 with trailing stop logic
- **EMA Configuration** — Fast, Slow, and Trend EMA values

The skill con
```

---

### 2.12 `tv sr-breaks` — `support-resistance-breaks`

- **Synopsis:** Support/Resistance Breaks — pivot-based S/R detection
- **Pine ID:** `PUB;NXS6SoOdr880Hrvh9vA36UcAjC14bOkc`  (reference Pine ID: `PUB;NXS6SoOdr880Hrvh9vA36UcAjC14bOkc`)
- **Workflow ID:** `support-resistance-breaks`  (captured reference: `support-resistance-breaks`)
- **Go parser:** `internal/skill/parsers/sr_breaks.go`  → func `parseSRBreaks` / format `formatSRBreaks`
- **Historical JS runner:** `/Volumes/ExMac/code/tradingview/js-experiment06/support-resistance-breaks.cjs`
- **Historical sample command:** `node support-resistance-breaks.cjs BTCUSDT --tf 1h --bars 300 --agent --json`
- **Captured reference run:** rc=0 parsed_via=bracket hint=support-resistance-breaks
- **Reference payload top-level keys (13):** `agentContext, breaks, confluence, conformance, execution, exitCode, levels, narrative, opportunities, schemaVersion, srBroken, status, timestamp`

#### Inputs (Go)

| name | tv_input_id | type | default |
|------|-------------|------|---------|
| `pivotLookback` | `in_0` | int | `5` |
| `pivotStrength` | `in_1` | int | `3` |
| `showSupport` | `in_2` | bool | `true` |
| `showResistance` | `in_3` | bool | `true` |
| `showBreaks` | `in_4` | bool | `true` |
| `breakIntensity` | `in_5` | int | `2` |
| `alertOnBreak` | `in_6` | bool | `true` |

#### Inputs (historical JS reference — INPUT_MAP)

| variable | tvInputId | type | default |
|----------|-----------|------|---------|
| `pivotLookback` | `in_0` | `int` | `5` |
| `pivotStrength` | `in_1` | `int` | `3` |
| `showSupport` | `in_2` | `bool` | `true` |
| `showResistance` | `in_3` | `bool` | `true` |
| `showBreaks` | `in_4` | `bool` | `true` |
| `breakIntensity` | `in_5` | `int` | `2` |
| `alertOnBreak` | `in_6` | `bool` | `true` |
| `srColor` | `in_7` | `color` | `#2196f3` |
| `breakColor` | `in_8` | `color` | `#ff5722` |
| `lineWidth` | `in_9` | `int` | `2` |

#### Input Comparison

| Go name | Go TV id | Reference variable | Reference tvInputId | Name match | TV id match |
|---------|----------|-------------|--------------|------------|-------------|
| `pivotLookback` | `in_0` | `pivotLookback` | `in_0` | OK | OK |
| `pivotStrength` | `in_1` | `pivotStrength` | `in_1` | OK | OK |
| `showSupport` | `in_2` | `showSupport` | `in_2` | OK | OK |
| `showResistance` | `in_3` | `showResistance` | `in_3` | OK | OK |
| `showBreaks` | `in_4` | `showBreaks` | `in_4` | OK | OK |
| `breakIntensity` | `in_5` | `breakIntensity` | `in_5` | OK | OK |
| `alertOnBreak` | `in_6` | `alertOnBreak` | `in_6` | OK | OK |
| — | — | `srColor` | `in_7` | color (cosmetic, safe to omit) | — |
| — | — | `breakColor` | `in_8` | color (cosmetic, safe to omit) | — |
| — | — | `lineWidth` | `in_9` | **MISSING in Go** | — |

#### Go parser reads from periods[] (getField aliases)

`Close`, `Resistance`, `ResistanceLevel`, `Support`, `SupportLevel`, `close`, `resistance`, `support`

#### Go Structure keys produced

`bias`, `price`, `resistance`, `support`

#### Reference payload schema (from captured dump)

Dump: `skill-analysis/dumps/support-resistance-breaks/payload.json`

| key | type/shape |
|-----|-----------|
| `status` | `str` |
| `exitCode` | `int` |
| `timestamp` | `str` |
| `execution` | `object{durationMs,attempts}` |
| `agentContext` | `object{workflow,modelVersion,symbol,timeframe,htfTimeframe}` |
| `levels` | `object{resistance,support,nearestResistance,nearestSupport,positionToSR}` |
| `breaks` | `object{totalEvents,recentBreaks,maxIntensity,avgIntensity}` |
| `confluence` | `list[object{price,count}]` |
| `srBroken` | `list[object{supportBroken,resistanceBroken}]` |
| `opportunities` | `list[object{rank,setup,direction,confidence,confluenceScore}]` |
| `narrative` | `object{marketStructure,primaryOpportunity,warnings,watchlist}` |
| `conformance` | `object{hasValidData,agenticScore}` |
| `schemaVersion` | `str` |

#### Go `SkillResult` schema (target output)

| field | type |
|-------|------|
| `status` | string |
| `workflow` | string |
| `market.lastPrice` | any |
| `market.bias` | string |
| `structure` | map[string]any (per-skill keys above) |
| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |
| `narrative.marketStructure` | string |
| `narrative.primaryOpportunity` | string |
| `narrative.warnings[]` | string |
| `narrative.watchlist[]` | string |
| `validation.passed` | bool |
| `validation.warnings[]` | string |
| `conformance.hasValidData` | bool |
| `conformance.agenticScore` | float64 |
| `raw` | map (omitted in agent output) |

#### Discrepancies & fix recommendations

- **Historical JS input `srColor` (`in_7`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `breakColor` (`in_8`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `lineWidth` (`in_9`) missing in Go.** Consider adding `InputDef{Name: "lineWidth", TVInputID: "in_9", Type: "int", Default: 2}`.
- **Reference payload has rich keys not in Go SkillResult:** `breaks, confluence, levels, srBroken`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.

#### Captured `--help` output

```
◇ injected env (13) from .env // tip: ⌁ auth for agents [www.vestauth.com]

Support Resistance Breaks — Standalone Runner
Usage: node support-resistance-breaks.cjs <SYMBOL> [options]
Options: --tf, --bars, --input key=value, --json, --agent, --out, --verbose, --dry-run, --help
Inputs: pivotLookback, pivotStrength, showSupport, showResistance, showBreaks, breakIntensity, alertOnBreak, srColor, breakColor, lineWidth
```

#### Original SKILL.md excerpt (first 1500 chars)

```markdown
---
name: support-resistance-breaks
description: |
  Use the Support and Resistance Breaks TradingView indicator to detect pivot-based S/R level breaks, measure break intensity, and identify price position relative to key structural levels.
version: 1.0.0
license: MIT
author: TradingView Pine Skills
compatibility: Node.js 18+ with tv-optimized.cjs, tv.cjs, agent-output.cjs and .env (SESSION, SIGNATURE) at project root
metadata:
  hermes:
    tags: [trading, tradingview, pine-script, support-resistance, pivot]
    category: trading
required_environment_variables:
  - name: SESSION
    prompt: TradingView session cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid
    required_for: full functionality
  - name: SIGNATURE
    prompt: TradingView signature cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid_sign
    required_for: full functionality
---

# Support and Resistance Breaks — Trading Opportunity Finder

## When to Use

Helps the user run the standalone `support-resistance-breaks.cjs` script against any TradingView symbol and timeframe, then interprets the structured output to surface high-probability trading setups based on pivot S/R break detection. The output includes:

- **Break Counts** — total breaks, support breaks, resistance breaks
- **Break Intensity** — frequency of breaks (breaks per bar)
- **Current Levels** — active support and resistance prices
- **Price Posit
```

---

### 2.13 `tv swingarm` — `swingarm-atr-trend-indicator`

- **Synopsis:** SwingArm ATR Trend — trailing stop with Fibonacci levels
- **Pine ID:** `PUB;GdkmXaTINI8knwuCrctQD1pB5dFaRnyr`  (reference Pine ID: `PUB;GdkmXaTINI8knwuCrctQD1pB5dFaRnyr`)
- **Workflow ID:** `swingarm-atr-trend`  (captured reference: none — the historical JS runner does not emit an `agentContext.workflow` envelope)
- **Go parser:** `internal/skill/parsers/swingarm.go`  → func `parseSwingArm` / format `formatSwingArm`
- **Historical JS runner:** `/Volumes/ExMac/code/tradingview/js-experiment06/swingarm-atr-trend-indicator.cjs`
- **Historical sample command:** `node swingarm-atr-trend-indicator.cjs BTCUSDT --tf 1h --bars 300 --agent --json --silent`
- **Captured reference run:** rc=0 parsed_via=bracket hint=None
- **Reference payload top-level keys (9):** `backgroundActive, confidence, indicator, pineId, price, rationale, signal, trailingStop, trend`

> ⚠️ The captured reference payload does not include an `agentContext.workflow` envelope. The Go `swingarm-atr-trend` workflow ID is authoritative; the captured reference is incomplete here.

#### Inputs (Go)

| name | tv_input_id | type | default |
|------|-------------|------|---------|
| `trailType` | `in_0` | string | `"modified"` |
| `ATRPeriod` | `in_1` | int | `28` |
| `ATRFactor` | `in_2` | float | `5.0` |
| `show_fib_entries` | `in_3` | bool | `true` |

#### Inputs (historical JS reference — INPUT_MAP)

| variable | tvInputId | type | default |
|----------|-----------|------|---------|
| `trailType` | `in_0` | `string` | `modified` |
| `ATRPeriod` | `in_1` | `int` | `28` |
| `ATRFactor` | `in_2` | `int` | `5` |
| `show_fib_entries` | `in_3` | `bool` | `true` |

#### Input Comparison

| Go name | Go TV id | Reference variable | Reference tvInputId | Name match | TV id match |
|---------|----------|-------------|--------------|------------|-------------|
| `trailType` | `in_0` | `trailType` | `in_0` | OK | OK |
| `ATRPeriod` | `in_1` | `ATRPeriod` | `in_1` | OK | OK |
| `ATRFactor` | `in_2` | `ATRFactor` | `in_2` | OK | OK |
| `show_fib_entries` | `in_3` | `show_fib_entries` | `in_3` | OK | OK |

#### Go parser reads from periods[] (getField aliases)

`Extremum`, `Fib_1`, `Fib_2`, `Fib_3`, `Trailingstop`, `plot_0`, `plot_2`, `plot_4`, `plot_5`, `plot_6`, `plot_8`, `plot_9`

#### Go Structure keys produced

`bias`, `extremum`, `fib1`, `fib2`, `fib3`, `signal`, `trailingStop`

#### Reference payload schema (from captured dump)

Dump: `skill-analysis/dumps/swingarm-atr-trend-indicator/payload.json`

| key | type/shape |
|-----|-----------|
| `indicator` | `str` |
| `pineId` | `str` |
| `trend` | `str` |
| `signal` | `str` |
| `confidence` | `str` |
| `price` | `float` |
| `trailingStop` | `float` |
| `rationale` | `str` |
| `backgroundActive` | `bool` |

#### Go `SkillResult` schema (target output)

| field | type |
|-------|------|
| `status` | string |
| `workflow` | string |
| `market.lastPrice` | any |
| `market.bias` | string |
| `structure` | map[string]any (per-skill keys above) |
| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |
| `narrative.marketStructure` | string |
| `narrative.primaryOpportunity` | string |
| `narrative.warnings[]` | string |
| `narrative.watchlist[]` | string |
| `validation.passed` | bool |
| `validation.warnings[]` | string |
| `conformance.hasValidData` | bool |
| `conformance.agenticScore` | float64 |
| `raw` | map (omitted in agent output) |

#### Discrepancies & fix recommendations

- The captured reference payload does not include `agentContext.workflow`. The Go `swingarm-atr-trend` workflow ID is authoritative.
- **Input `ATRFactor` type mismatch.** Go=`float` JS=`int`.
- **Input `ATRFactor` default mismatch.** Go=`5.0` JS=`5`.
- **Reference payload has rich keys not in Go SkillResult:** `backgroundActive, confidence, indicator, pineId, price, rationale, signal, trailingStop, trend`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.

#### Captured `--help` output

```
◇ injected env (13) from .env // tip: ⌘ enable debugging { debug: true }

SwingArm ATR Trend Indicator — Standalone Runner
Usage: node swingarm-atr-trend-indicator.cjs <SYMBOL> [options]

Options:
  --symbol <SYMBOL>    Trading symbol (e.g., BTCUSDT, ETHUSDT) [default: BTCUSDT]
  --tf <TIMEFRAME>     Chart timeframe (e.g., 15m, 1h, 1D) [default: 15m]
  --bars <NUMBER>       Number of bars to fetch [default: 500]
  --input <key=value>   Override indicator inputs (e.g., ATRPeriod=21)
  --json                Output full JSON results
  --agent               Output deterministic agent-mode JSON
  --out <FILE>          Write output to file
  --dry-run             Simulate execution without API calls
  --silent              Suppress non-essential output
  --verbose, -v         Show detailed error stacks
  --help, -h            Show this help message

Inputs:
  trailType         Trail type (modified/unmodified) [default: modified]
  ATRPeriod         ATR Period (1-100) [default: 28, recommended: 21]
  ATRFactor         ATR Factor [default: 5]
  show_fib_entries  Show Fib entries [default: true]

Examples:
  node swingarm-atr-trend-indicator.cjs BTCUSDT
  node swingarm-atr-trend-indicator.c
```

#### Original SKILL.md excerpt (first 1500 chars)

```markdown
---
name: swingarm-atr-trend-indicator
description: ATR-based trend indicator for TradingView that uses background colors and trailing stop levels to generate buy/sell signals
version: 1.0.0
author: Hermes Agent
compatibility: ["tradingview", "tv-optimized.cjs", "node >= 16"]
---

# SwingArm ATR Trend Indicator Skill

## Overview
The SwingArm ATR Trend Indicator is a TradingView indicator (Pine ID: `PUB;GdkmXaTINI8knwuCrctQD1pB5dFaRnyr`) that uses Average True Range (ATR) to identify trend direction. It visualizes trends via chart background colors and trailing stop levels:
- **GREEN background** + price above green area → BUY signal
- **RED background** + price below red area → SELL signal

This skill provides a standalone runner script to fetch indicator data, parse signals, and output structured results for automated trading systems or manual analysis.

## Indicator Behavior (from Video Tutorial)
- **Core Logic**: Calculates ATR-based trailing stop levels. When price is above the trailing stop, the background turns green (bullish trend). When price is below, background turns red (bearish trend).
- **Key Input**: ATR Period (default 28, video recommends 21 for better responsiveness)
- **Additional Features**: Optional Fibonacci entry levels (LS1-LS3 for buy, SS1-SS3 for sell) and modified/unmodified trailing stop calculation.
- **Signal Confirmation**: Wait for background color to stabilize and price to clearly break above/below the trailing stop area.

## CLI Usage
```bash
```

---

### 2.14 `tv trend` — `self-aware-trend-system`

- **Synopsis:** Self-Aware Trend System — adaptive SuperTrend with TQI
- **Pine ID:** `PUB;0f80bcf05d544d4c98fde06faab1c976`  (reference Pine ID: `PUB;0f80bcf05d544d4c98fde06faab1c976`)
- **Workflow ID:** `adaptive-supertrend-quality`  (captured reference: `adaptive-supertrend-quality`)
- **Go parser:** `internal/skill/parsers/trend.go`  → func `parseTrend` / format `formatTrend`
- **Historical JS runner:** `/Volumes/ExMac/code/tradingview/js-experiment06/self-aware-trend-system.cjs`
- **Historical sample command:** `node self-aware-trend-system.cjs BTCUSDT --preset default --tf 15m --bars 300 --agent --json --silent`
- **Captured reference run:** rc=0 parsed_via=delimiter hint=None
- **Reference payload top-level keys (15):** `_parserMeta, agentContext, conformance, execution, exitCode, market, narrative, opportunities, performance, schemaVersion, status, structure, timestamp, tradePlan, validation`

#### Inputs (Go)

| name | tv_input_id | type | default |
|------|-------------|------|---------|
| `presetInput` | `in_0` | string | `"Auto"` |
| `atrLenInput` | `in_1` | int | `13` |
| `baseMultInput` | `in_2` | float | `2` |
| `sourceInput` | `in_3` | source | `"close"` |
| `useTqiInput` | `in_8` | bool | `true` |
| `useCharFlipInput` | `in_15` | bool | `true` |
| `useAsymBandsInput` | `in_12` | bool | `true` |
| `useStructureInput` | `in_25` | bool | `true` |
| `useRsiInput` | `in_27` | bool | `true` |
| `useVolInput` | `in_32` | bool | `true` |

#### Inputs (historical JS reference — INPUT_MAP)

| variable | tvInputId | type | default |
|----------|-----------|------|---------|
| `presetInput` | `in_0` | `string` | `Auto` |
| `atrLenInput` | `in_1` | `int` | `13` |
| `baseMultInput` | `in_2` | `float` | `2` |
| `sourceInput` | `in_3` | `source` | `close` |
| `useAdaptiveInput` | `in_4` | `bool` | `true` |
| `erLengthInput` | `in_5` | `int` | `20` |
| `adaptStrengthInput` | `in_6` | `float` | `0.5` |
| `atrBaselineLenInput` | `in_7` | `int` | `100` |
| `useTqiInput` | `in_8` | `bool` | `true` |
| `qualityStrengthInput` | `in_9` | `float` | `0.4` |
| `qualityCurveInput` | `in_10` | `float` | `1.5` |
| `multSmoothInput` | `in_11` | `bool` | `true` |
| `useAsymBandsInput` | `in_12` | `bool` | `true` |
| `asymStrengthInput` | `in_13` | `float` | `0.5` |
| `useEffAtrInput` | `in_14` | `bool` | `true` |
| `useCharFlipInput` | `in_15` | `bool` | `true` |
| `charFlipMinAgeInput` | `in_16` | `int` | `5` |
| `charFlipHighInput` | `in_17` | `float` | `0.55` |
| `charFlipLowInput` | `in_18` | `float` | `0.25` |
| `tqiWeightErInput` | `in_19` | `float` | `0.35` |
| `tqiWeightVolInput` | `in_20` | `float` | `0.2` |
| `tqiWeightStructInput` | `in_21` | `float` | `0.25` |
| `tqiWeightMomInput` | `in_22` | `float` | `0.2` |
| `tqiStructLenInput` | `in_23` | `int` | `20` |
| `tqiMomLenInput` | `in_24` | `int` | `10` |
| `useStructureInput` | `in_25` | `bool` | `true` |
| `pivotLenInput` | `in_26` | `int` | `3` |
| `useRsiInput` | `in_27` | `bool` | `true` |
| `rsiLenInput` | `in_28` | `int` | `14` |
| `rsiOBInput` | `in_29` | `int` | `70` |
| `rsiOSInput` | `in_30` | `int` | `30` |
| `rsiLookbackInput` | `in_31` | `int` | `20` |
| `useVolInput` | `in_32` | `bool` | `true` |
| `volLenInput` | `in_33` | `int` | `20` |
| `minScoreInput` | `in_34` | `int` | `60` |
| `showRiskInput` | `in_35` | `bool` | `true` |
| `slAtrMultInput` | `in_36` | `float` | `1.5` |
| `tpModeInput` | `in_37` | `string` | `Fixed` |
| `tp1RInput` | `in_38` | `float` | `1` |
| `tp2RInput` | `in_39` | `float` | `2` |
| `tp3RInput` | `in_40` | `float` | `3` |
| `dynTpTqiWeightInput` | `in_41` | `float` | `0.6` |
| `dynTpVolWeightInput` | `in_42` | `float` | `0.4` |
| `dynTpMinScaleInput` | `in_43` | `float` | `0.5` |
| `dynTpMaxScaleInput` | `in_44` | `float` | `2` |
| `dynTpFloorR1Input` | `in_45` | `float` | `0.5` |
| `dynTpCeilR3Input` | `in_46` | `float` | `8` |
| `labelOffsetInput` | `in_47` | `int` | `10` |
| `showHitsInput` | `in_48` | `bool` | `true` |
| `tradeMaxAgeInput` | `in_49` | `int` | `100` |
| `useAutoCalibInput` | `in_50` | `bool` | `false` |
| `calibWindowInput` | `in_51` | `int` | `20` |
| `calibBadRInput` | `in_52` | `float` | `0` |
| `calibGoodRInput` | `in_53` | `float` | `0.7` |
| `calibStepQInput` | `in_54` | `float` | `0.05` |
| `calibCooldownInput` | `in_55` | `int` | `5` |
| `calibMinQInput` | `in_56` | `float` | `0.1` |
| `calibMaxQInput` | `in_57` | `float` | `0.9` |
| `resetLearningInput` | `in_58` | `bool` | `false` |
| `themeInput` | `in_59` | `string` | `Auto` |
| `showBandsInput` | `in_60` | `bool` | `true` |
| `showTqiColorInput` | `in_61` | `bool` | `true` |
| `showSignalsInput` | `in_62` | `bool` | `true` |
| `showBgInput` | `in_63` | `bool` | `false` |
| `showWatermarkInput` | `in_64` | `bool` | `true` |
| `showDashInput` | `in_65` | `bool` | `true` |
| `showTqiBreakdownInput` | `in_66` | `bool` | `true` |
| `showBreakdownInput` | `in_67` | `bool` | `false` |
| `showPerfInput` | `in_68` | `bool` | `true` |
| `dashPosStr` | `in_69` | `string` | `Top Right` |
| `bullColorInput` | `in_70` | `color` | `#00E676` |
| `bearColorInput` | `in_71` | `color` | `#FF5252` |
| `slColorInput` | `in_72` | `color` | `#FF1744` |
| `tpColorInput` | `in_73` | `color` | `#00E676` |
| `enableAlertsInput` | `in_74` | `bool` | `true` |
| `webhookInput` | `in_75` | `bool` | `false` |

#### Input Comparison

| Go name | Go TV id | Reference variable | Reference tvInputId | Name match | TV id match |
|---------|----------|-------------|--------------|------------|-------------|
| `presetInput` | `in_0` | `presetInput` | `in_0` | OK | OK |
| `atrLenInput` | `in_1` | `atrLenInput` | `in_1` | OK | OK |
| `baseMultInput` | `in_2` | `baseMultInput` | `in_2` | OK | OK |
| `sourceInput` | `in_3` | `sourceInput` | `in_3` | OK | OK |
| `useTqiInput` | `in_8` | `useTqiInput` | `in_8` | OK | OK |
| `useCharFlipInput` | `in_15` | `useCharFlipInput` | `in_15` | OK | OK |
| `useAsymBandsInput` | `in_12` | `useAsymBandsInput` | `in_12` | OK | OK |
| `useStructureInput` | `in_25` | `useStructureInput` | `in_25` | OK | OK |
| `useRsiInput` | `in_27` | `useRsiInput` | `in_27` | OK | OK |
| `useVolInput` | `in_32` | `useVolInput` | `in_32` | OK | OK |
| — | — | `useAdaptiveInput` | `in_4` | **MISSING in Go** | — |
| — | — | `erLengthInput` | `in_5` | **MISSING in Go** | — |
| — | — | `adaptStrengthInput` | `in_6` | **MISSING in Go** | — |
| — | — | `atrBaselineLenInput` | `in_7` | **MISSING in Go** | — |
| — | — | `qualityStrengthInput` | `in_9` | **MISSING in Go** | — |
| — | — | `qualityCurveInput` | `in_10` | **MISSING in Go** | — |
| — | — | `multSmoothInput` | `in_11` | **MISSING in Go** | — |
| — | — | `asymStrengthInput` | `in_13` | **MISSING in Go** | — |
| — | — | `useEffAtrInput` | `in_14` | **MISSING in Go** | — |
| — | — | `charFlipMinAgeInput` | `in_16` | **MISSING in Go** | — |
| — | — | `charFlipHighInput` | `in_17` | **MISSING in Go** | — |
| — | — | `charFlipLowInput` | `in_18` | **MISSING in Go** | — |
| — | — | `tqiWeightErInput` | `in_19` | **MISSING in Go** | — |
| — | — | `tqiWeightVolInput` | `in_20` | **MISSING in Go** | — |
| — | — | `tqiWeightStructInput` | `in_21` | **MISSING in Go** | — |
| — | — | `tqiWeightMomInput` | `in_22` | **MISSING in Go** | — |
| — | — | `tqiStructLenInput` | `in_23` | **MISSING in Go** | — |
| — | — | `tqiMomLenInput` | `in_24` | **MISSING in Go** | — |
| — | — | `pivotLenInput` | `in_26` | **MISSING in Go** | — |
| — | — | `rsiLenInput` | `in_28` | **MISSING in Go** | — |
| — | — | `rsiOBInput` | `in_29` | **MISSING in Go** | — |
| — | — | `rsiOSInput` | `in_30` | **MISSING in Go** | — |
| — | — | `rsiLookbackInput` | `in_31` | **MISSING in Go** | — |
| — | — | `volLenInput` | `in_33` | **MISSING in Go** | — |
| — | — | `minScoreInput` | `in_34` | **MISSING in Go** | — |
| — | — | `showRiskInput` | `in_35` | **MISSING in Go** | — |
| — | — | `slAtrMultInput` | `in_36` | **MISSING in Go** | — |
| — | — | `tpModeInput` | `in_37` | **MISSING in Go** | — |
| — | — | `tp1RInput` | `in_38` | **MISSING in Go** | — |
| — | — | `tp2RInput` | `in_39` | **MISSING in Go** | — |
| — | — | `tp3RInput` | `in_40` | **MISSING in Go** | — |
| — | — | `dynTpTqiWeightInput` | `in_41` | **MISSING in Go** | — |
| — | — | `dynTpVolWeightInput` | `in_42` | **MISSING in Go** | — |
| — | — | `dynTpMinScaleInput` | `in_43` | **MISSING in Go** | — |
| — | — | `dynTpMaxScaleInput` | `in_44` | **MISSING in Go** | — |
| — | — | `dynTpFloorR1Input` | `in_45` | **MISSING in Go** | — |
| — | — | `dynTpCeilR3Input` | `in_46` | **MISSING in Go** | — |
| — | — | `labelOffsetInput` | `in_47` | **MISSING in Go** | — |
| — | — | `showHitsInput` | `in_48` | **MISSING in Go** | — |
| — | — | `tradeMaxAgeInput` | `in_49` | **MISSING in Go** | — |
| — | — | `useAutoCalibInput` | `in_50` | **MISSING in Go** | — |
| — | — | `calibWindowInput` | `in_51` | **MISSING in Go** | — |
| — | — | `calibBadRInput` | `in_52` | **MISSING in Go** | — |
| — | — | `calibGoodRInput` | `in_53` | **MISSING in Go** | — |
| — | — | `calibStepQInput` | `in_54` | **MISSING in Go** | — |
| — | — | `calibCooldownInput` | `in_55` | **MISSING in Go** | — |
| — | — | `calibMinQInput` | `in_56` | **MISSING in Go** | — |
| — | — | `calibMaxQInput` | `in_57` | **MISSING in Go** | — |
| — | — | `resetLearningInput` | `in_58` | **MISSING in Go** | — |
| — | — | `themeInput` | `in_59` | **MISSING in Go** | — |
| — | — | `showBandsInput` | `in_60` | **MISSING in Go** | — |
| — | — | `showTqiColorInput` | `in_61` | **MISSING in Go** | — |
| — | — | `showSignalsInput` | `in_62` | **MISSING in Go** | — |
| — | — | `showBgInput` | `in_63` | **MISSING in Go** | — |
| — | — | `showWatermarkInput` | `in_64` | **MISSING in Go** | — |
| — | — | `showDashInput` | `in_65` | **MISSING in Go** | — |
| — | — | `showTqiBreakdownInput` | `in_66` | **MISSING in Go** | — |
| — | — | `showBreakdownInput` | `in_67` | **MISSING in Go** | — |
| — | — | `showPerfInput` | `in_68` | **MISSING in Go** | — |
| — | — | `dashPosStr` | `in_69` | **MISSING in Go** | — |
| — | — | `bullColorInput` | `in_70` | color (cosmetic, safe to omit) | — |
| — | — | `bearColorInput` | `in_71` | color (cosmetic, safe to omit) | — |
| — | — | `slColorInput` | `in_72` | color (cosmetic, safe to omit) | — |
| — | — | `tpColorInput` | `in_73` | color (cosmetic, safe to omit) | — |
| — | — | `enableAlertsInput` | `in_74` | **MISSING in Go** | — |
| — | — | `webhookInput` | `in_75` | **MISSING in Go** | — |

#### Presets

| preset | go | js | match |
|--------|----|----|-------|
| `auto` | `{}` | `{'presetInput': 'Auto', 'atrLenInput': 13, 'baseMultInput': 2.0}` | OK js-only:{'baseMultInput', 'atrLenInput', 'presetInput'} |
| `crypto` | `{}` | `{'presetInput': 'Crypto 24/7', 'atrLenInput': 13, 'baseMultInput': 2.0}` | OK js-only:{'baseMultInput', 'atrLenInput', 'presetInput'} |
| `default` | `{}` | `{'presetInput': 'Default', 'atrLenInput': 13, 'baseMultInput': 2.0, 'sourceInput': 'close', 'useTqiInput': True, 'qualityStrengthInput': 0.4, 'useCharFlipInput': True}` | OK js-only:{'baseMultInput', 'atrLenInput', 'presetInput', 'useTqiInput', 'qualityStrengthInput', 'useCharFlipInput', 'sourceInput'} |
| `scalping` | `{}` | `{'presetInput': 'Scalping', 'atrLenInput': 10, 'baseMultInput': 1.5, 'qualityStrengthInput': 0.5}` | OK js-only:{'baseMultInput', 'atrLenInput', 'qualityStrengthInput', 'presetInput'} |
| `swing` | `{}` | `{'presetInput': 'Swing', 'atrLenInput': 21, 'baseMultInput': 2.5, 'qualityStrengthInput': 0.3}` | OK js-only:{'baseMultInput', 'atrLenInput', 'qualityStrengthInput', 'presetInput'} |

#### Go parser reads from periods[] (getField aliases)

`Close`, `QualityIndex`, `Regime`, `TQI`, `Trend`, `TrendDirection`, `close`, `regime`, `tqi`, `trendDirection`

#### Go Structure keys produced

`regime`, `tqi`, `trendDirection`

#### Reference payload schema (from captured dump)

Dump: `skill-analysis/dumps/self-aware-trend-system/payload.json`

| key | type/shape |
|-----|-----------|
| `status` | `str` |
| `exitCode` | `int` |
| `timestamp` | `str` |
| `execution` | `object{durationMs,attempts}` |
| `agentContext` | `object{workflow,htfTimeframe,modelVersion,symbol,timeframe}` |
| `market` | `object{lastPrice,bias,regime,tqi,quality}` |
| `structure` | `object{trend,tqiBreakdown,regime}` |
| `performance` | `object{winRate,avgR,windowDrawdown,streaks,regimeEdge,regimeSampleSize...}` |
| `opportunities` | `list[object{rank,setup,direction,confidence,confluenceScore}]` |
| `tradePlan` | `object{direction,entry,sl,tp1,tp2,tp3...}` |
| `narrative` | `object{marketStructure,primaryOpportunity,warnings,watchlist}` |
| `validation` | `object{passed,checks,warnings}` |
| `conformance` | `object{hasValidStructure,hasQualityTrend,tqiLevel,agenticScore}` |
| `schemaVersion` | `str` |
| `_parserMeta` | `object{schemaVersion,emittedAt,deterministic}` |

#### Go `SkillResult` schema (target output)

| field | type |
|-------|------|
| `status` | string |
| `workflow` | string |
| `market.lastPrice` | any |
| `market.bias` | string |
| `structure` | map[string]any (per-skill keys above) |
| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |
| `narrative.marketStructure` | string |
| `narrative.primaryOpportunity` | string |
| `narrative.warnings[]` | string |
| `narrative.watchlist[]` | string |
| `validation.passed` | bool |
| `validation.warnings[]` | string |
| `conformance.hasValidData` | bool |
| `conformance.agenticScore` | float64 |
| `raw` | map (omitted in agent output) |

#### Discrepancies & fix recommendations

- **Historical JS input `useAdaptiveInput` (`in_4`) missing in Go.** Consider adding `InputDef{Name: "useAdaptiveInput", TVInputID: "in_4", Type: "bool", Default: true}`.
- **Historical JS input `erLengthInput` (`in_5`) missing in Go.** Consider adding `InputDef{Name: "erLengthInput", TVInputID: "in_5", Type: "int", Default: 20}`.
- **Historical JS input `adaptStrengthInput` (`in_6`) missing in Go.** Consider adding `InputDef{Name: "adaptStrengthInput", TVInputID: "in_6", Type: "float", Default: 0.5}`.
- **Historical JS input `atrBaselineLenInput` (`in_7`) missing in Go.** Consider adding `InputDef{Name: "atrBaselineLenInput", TVInputID: "in_7", Type: "int", Default: 100}`.
- **Historical JS input `qualityStrengthInput` (`in_9`) missing in Go.** Consider adding `InputDef{Name: "qualityStrengthInput", TVInputID: "in_9", Type: "float", Default: 0.4}`.
- **Historical JS input `qualityCurveInput` (`in_10`) missing in Go.** Consider adding `InputDef{Name: "qualityCurveInput", TVInputID: "in_10", Type: "float", Default: 1.5}`.
- **Historical JS input `multSmoothInput` (`in_11`) missing in Go.** Consider adding `InputDef{Name: "multSmoothInput", TVInputID: "in_11", Type: "bool", Default: true}`.
- **Historical JS input `asymStrengthInput` (`in_13`) missing in Go.** Consider adding `InputDef{Name: "asymStrengthInput", TVInputID: "in_13", Type: "float", Default: 0.5}`.
- **Historical JS input `useEffAtrInput` (`in_14`) missing in Go.** Consider adding `InputDef{Name: "useEffAtrInput", TVInputID: "in_14", Type: "bool", Default: true}`.
- **Historical JS input `charFlipMinAgeInput` (`in_16`) missing in Go.** Consider adding `InputDef{Name: "charFlipMinAgeInput", TVInputID: "in_16", Type: "int", Default: 5}`.
- **Historical JS input `charFlipHighInput` (`in_17`) missing in Go.** Consider adding `InputDef{Name: "charFlipHighInput", TVInputID: "in_17", Type: "float", Default: 0.55}`.
- **Historical JS input `charFlipLowInput` (`in_18`) missing in Go.** Consider adding `InputDef{Name: "charFlipLowInput", TVInputID: "in_18", Type: "float", Default: 0.25}`.
- **Historical JS input `tqiWeightErInput` (`in_19`) missing in Go.** Consider adding `InputDef{Name: "tqiWeightErInput", TVInputID: "in_19", Type: "float", Default: 0.35}`.
- **Historical JS input `tqiWeightVolInput` (`in_20`) missing in Go.** Consider adding `InputDef{Name: "tqiWeightVolInput", TVInputID: "in_20", Type: "float", Default: 0.2}`.
- **Historical JS input `tqiWeightStructInput` (`in_21`) missing in Go.** Consider adding `InputDef{Name: "tqiWeightStructInput", TVInputID: "in_21", Type: "float", Default: 0.25}`.
- **Historical JS input `tqiWeightMomInput` (`in_22`) missing in Go.** Consider adding `InputDef{Name: "tqiWeightMomInput", TVInputID: "in_22", Type: "float", Default: 0.2}`.
- **Historical JS input `tqiStructLenInput` (`in_23`) missing in Go.** Consider adding `InputDef{Name: "tqiStructLenInput", TVInputID: "in_23", Type: "int", Default: 20}`.
- **Historical JS input `tqiMomLenInput` (`in_24`) missing in Go.** Consider adding `InputDef{Name: "tqiMomLenInput", TVInputID: "in_24", Type: "int", Default: 10}`.
- **Historical JS input `pivotLenInput` (`in_26`) missing in Go.** Consider adding `InputDef{Name: "pivotLenInput", TVInputID: "in_26", Type: "int", Default: 3}`.
- **Historical JS input `rsiLenInput` (`in_28`) missing in Go.** Consider adding `InputDef{Name: "rsiLenInput", TVInputID: "in_28", Type: "int", Default: 14}`.
- **Historical JS input `rsiOBInput` (`in_29`) missing in Go.** Consider adding `InputDef{Name: "rsiOBInput", TVInputID: "in_29", Type: "int", Default: 70}`.
- **Historical JS input `rsiOSInput` (`in_30`) missing in Go.** Consider adding `InputDef{Name: "rsiOSInput", TVInputID: "in_30", Type: "int", Default: 30}`.
- **Historical JS input `rsiLookbackInput` (`in_31`) missing in Go.** Consider adding `InputDef{Name: "rsiLookbackInput", TVInputID: "in_31", Type: "int", Default: 20}`.
- **Historical JS input `volLenInput` (`in_33`) missing in Go.** Consider adding `InputDef{Name: "volLenInput", TVInputID: "in_33", Type: "int", Default: 20}`.
- **Historical JS input `minScoreInput` (`in_34`) missing in Go.** Consider adding `InputDef{Name: "minScoreInput", TVInputID: "in_34", Type: "int", Default: 60}`.
- **Historical JS input `showRiskInput` (`in_35`) missing in Go.** Consider adding `InputDef{Name: "showRiskInput", TVInputID: "in_35", Type: "bool", Default: true}`.
- **Historical JS input `slAtrMultInput` (`in_36`) missing in Go.** Consider adding `InputDef{Name: "slAtrMultInput", TVInputID: "in_36", Type: "float", Default: 1.5}`.
- **Historical JS input `tpModeInput` (`in_37`) missing in Go.** Consider adding `InputDef{Name: "tpModeInput", TVInputID: "in_37", Type: "string", Default: Fixed}`.
- **Historical JS input `tp1RInput` (`in_38`) missing in Go.** Consider adding `InputDef{Name: "tp1RInput", TVInputID: "in_38", Type: "float", Default: 1}`.
- **Historical JS input `tp2RInput` (`in_39`) missing in Go.** Consider adding `InputDef{Name: "tp2RInput", TVInputID: "in_39", Type: "float", Default: 2}`.
- **Historical JS input `tp3RInput` (`in_40`) missing in Go.** Consider adding `InputDef{Name: "tp3RInput", TVInputID: "in_40", Type: "float", Default: 3}`.
- **Historical JS input `dynTpTqiWeightInput` (`in_41`) missing in Go.** Consider adding `InputDef{Name: "dynTpTqiWeightInput", TVInputID: "in_41", Type: "float", Default: 0.6}`.
- **Historical JS input `dynTpVolWeightInput` (`in_42`) missing in Go.** Consider adding `InputDef{Name: "dynTpVolWeightInput", TVInputID: "in_42", Type: "float", Default: 0.4}`.
- **Historical JS input `dynTpMinScaleInput` (`in_43`) missing in Go.** Consider adding `InputDef{Name: "dynTpMinScaleInput", TVInputID: "in_43", Type: "float", Default: 0.5}`.
- **Historical JS input `dynTpMaxScaleInput` (`in_44`) missing in Go.** Consider adding `InputDef{Name: "dynTpMaxScaleInput", TVInputID: "in_44", Type: "float", Default: 2}`.
- **Historical JS input `dynTpFloorR1Input` (`in_45`) missing in Go.** Consider adding `InputDef{Name: "dynTpFloorR1Input", TVInputID: "in_45", Type: "float", Default: 0.5}`.
- **Historical JS input `dynTpCeilR3Input` (`in_46`) missing in Go.** Consider adding `InputDef{Name: "dynTpCeilR3Input", TVInputID: "in_46", Type: "float", Default: 8}`.
- **Historical JS input `labelOffsetInput` (`in_47`) missing in Go.** Consider adding `InputDef{Name: "labelOffsetInput", TVInputID: "in_47", Type: "int", Default: 10}`.
- **Historical JS input `showHitsInput` (`in_48`) missing in Go.** Consider adding `InputDef{Name: "showHitsInput", TVInputID: "in_48", Type: "bool", Default: true}`.
- **Historical JS input `tradeMaxAgeInput` (`in_49`) missing in Go.** Consider adding `InputDef{Name: "tradeMaxAgeInput", TVInputID: "in_49", Type: "int", Default: 100}`.
- **Historical JS input `useAutoCalibInput` (`in_50`) missing in Go.** Consider adding `InputDef{Name: "useAutoCalibInput", TVInputID: "in_50", Type: "bool", Default: false}`.
- **Historical JS input `calibWindowInput` (`in_51`) missing in Go.** Consider adding `InputDef{Name: "calibWindowInput", TVInputID: "in_51", Type: "int", Default: 20}`.
- **Historical JS input `calibBadRInput` (`in_52`) missing in Go.** Consider adding `InputDef{Name: "calibBadRInput", TVInputID: "in_52", Type: "float", Default: 0}`.
- **Historical JS input `calibGoodRInput` (`in_53`) missing in Go.** Consider adding `InputDef{Name: "calibGoodRInput", TVInputID: "in_53", Type: "float", Default: 0.7}`.
- **Historical JS input `calibStepQInput` (`in_54`) missing in Go.** Consider adding `InputDef{Name: "calibStepQInput", TVInputID: "in_54", Type: "float", Default: 0.05}`.
- **Historical JS input `calibCooldownInput` (`in_55`) missing in Go.** Consider adding `InputDef{Name: "calibCooldownInput", TVInputID: "in_55", Type: "int", Default: 5}`.
- **Historical JS input `calibMinQInput` (`in_56`) missing in Go.** Consider adding `InputDef{Name: "calibMinQInput", TVInputID: "in_56", Type: "float", Default: 0.1}`.
- **Historical JS input `calibMaxQInput` (`in_57`) missing in Go.** Consider adding `InputDef{Name: "calibMaxQInput", TVInputID: "in_57", Type: "float", Default: 0.9}`.
- **Historical JS input `resetLearningInput` (`in_58`) missing in Go.** Consider adding `InputDef{Name: "resetLearningInput", TVInputID: "in_58", Type: "bool", Default: false}`.
- **Historical JS input `themeInput` (`in_59`) missing in Go.** Consider adding `InputDef{Name: "themeInput", TVInputID: "in_59", Type: "string", Default: Auto}`.
- **Historical JS input `showBandsInput` (`in_60`) missing in Go.** Consider adding `InputDef{Name: "showBandsInput", TVInputID: "in_60", Type: "bool", Default: true}`.
- **Historical JS input `showTqiColorInput` (`in_61`) missing in Go.** Consider adding `InputDef{Name: "showTqiColorInput", TVInputID: "in_61", Type: "bool", Default: true}`.
- **Historical JS input `showSignalsInput` (`in_62`) missing in Go.** Consider adding `InputDef{Name: "showSignalsInput", TVInputID: "in_62", Type: "bool", Default: true}`.
- **Historical JS input `showBgInput` (`in_63`) missing in Go.** Consider adding `InputDef{Name: "showBgInput", TVInputID: "in_63", Type: "bool", Default: false}`.
- **Historical JS input `showWatermarkInput` (`in_64`) missing in Go.** Consider adding `InputDef{Name: "showWatermarkInput", TVInputID: "in_64", Type: "bool", Default: true}`.
- **Historical JS input `showDashInput` (`in_65`) missing in Go.** Consider adding `InputDef{Name: "showDashInput", TVInputID: "in_65", Type: "bool", Default: true}`.
- **Historical JS input `showTqiBreakdownInput` (`in_66`) missing in Go.** Consider adding `InputDef{Name: "showTqiBreakdownInput", TVInputID: "in_66", Type: "bool", Default: true}`.
- **Historical JS input `showBreakdownInput` (`in_67`) missing in Go.** Consider adding `InputDef{Name: "showBreakdownInput", TVInputID: "in_67", Type: "bool", Default: false}`.
- **Historical JS input `showPerfInput` (`in_68`) missing in Go.** Consider adding `InputDef{Name: "showPerfInput", TVInputID: "in_68", Type: "bool", Default: true}`.
- **Historical JS input `dashPosStr` (`in_69`) missing in Go.** Consider adding `InputDef{Name: "dashPosStr", TVInputID: "in_69", Type: "string", Default: Top Right}`.
- **Historical JS input `bullColorInput` (`in_70`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `bearColorInput` (`in_71`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `slColorInput` (`in_72`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `tpColorInput` (`in_73`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `enableAlertsInput` (`in_74`) missing in Go.** Consider adding `InputDef{Name: "enableAlertsInput", TVInputID: "in_74", Type: "bool", Default: true}`.
- **Historical JS input `webhookInput` (`in_75`) missing in Go.** Consider adding `InputDef{Name: "webhookInput", TVInputID: "in_75", Type: "bool", Default: false}`.
- **Reference payload has rich keys not in Go SkillResult:** `_parserMeta, performance, tradePlan`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.

#### Captured `--help` output

```
◇ injected env (13) from .env // tip: ⌘ suppress logs { quiet: true }

Self-Aware Trend System [WillyAlgoTrader] — Standalone Runner
===============================================================

Usage:
  node self-aware-trend-system.cjs <SYMBOL> [options]

Arguments:
  SYMBOL                    Trading pair (default: BTCUSDT)

Options:
  --tf <timeframe>          Timeframe: 1m, 5m, 15m, 1h, 4h, 1D (default: 15m)
  --bars <n>                Number of chart bars (default: 500)
  --preset <name>           Preset: auto, scalping, default, swing, crypto (default: default)
  --json                    Output JSON instead of table
  --agent                   Agent mode (simplified JSON, optimized for AI agents)
  --out <file>              Write JSON to file
  --verbose, -v             Verbose output for debugging
  --silent                  Suppress all non-JSON stdout (use with --json or --agent)
  --dry-run                 Skip TradingView connection, show parsed args only
  --help, -h                Show this help

Examples:
  node self-aware-trend-system.cjs BTCUSDT
  node self-aware-trend-system.cjs ETHUSDT --tf 1h --bars 1000
  node self-aware-trend-system.cjs SOLUSDT --preset sca
```

#### Original SKILL.md excerpt (first 1500 chars)

```markdown
---
name: self-aware-trend-system
description: |
  Use the Self-Aware Trend System [WillyAlgoTrader] TradingView indicator to analyze any symbol/timeframe and extract adaptive trend-following signals.
version: 1.0.0
license: MIT
author: TradingView Pine Skills
compatibility: Node.js 18+ with tv-optimized.cjs, tv.cjs, agent-output.cjs and .env (SESSION, SIGNATURE) at project root
metadata:
  hermes:
    tags: [trading, tradingview, pine-script, supertrend, tqi, regime]
    category: trading
required_environment_variables:
  - name: SESSION
    prompt: TradingView session cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid
    required_for: full functionality
  - name: SIGNATURE
    prompt: TradingView signature cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid_sign
    required_for: full functionality
---

# Self-Aware Trend System [WillyAlgoTrader] — Trading Opportunity Finder

## When to Use

Helps the user run the standalone `self-aware-trend-system.cjs` script against any TradingView symbol and timeframe, then interprets the structured output to surface high-probability trend-following setups. The output includes:

- **Trend State** — current direction (bullish/bearish/neutral), TQI quality, regime classification
- **TQI Breakdown** — Efficiency, Volatility, Structure, Momentum Persistence components
- **Trade Signals** — BUY/SELL signals with scores (0-30), tooltip metadata
```

---

### 2.15 `tv ust` — `ultra-sensitive-supertrend`

- **Synopsis:** Ultra Sensitive SuperTrend — dual ST alignment
- **Pine ID:** `PUB;fc33f2d98699414a8585923116dbd959`  (reference Pine ID: `PUB;fc33f2d98699414a8585923116dbd959`)
- **Workflow ID:** `ultra-sensitive-supertrend`  (captured reference: `ultra-sensitive-supertrend`)
- **Go parser:** `internal/skill/parsers/ust.go`  → func `parseUST` / format `formatUST`
- **Historical JS runner:** `/Volumes/ExMac/code/tradingview/js-experiment06/ultra-sensitive-supertrend.cjs`
- **Historical sample command:** `node ultra-sensitive-supertrend.cjs BTCUSDT --tf 1h --bars 300 --agent --json`
- **Captured reference run:** rc=0 parsed_via=bracket hint=ultra-sensitive-supertrend
- **Reference payload top-level keys (13):** `agentContext, conformance, execution, exitCode, narrative, opportunities, schemaVersion, signalHistory, signals, st1Crosses, status, timestamp, trend`

#### Inputs (Go)

| name | tv_input_id | type | default |
|------|-------------|------|---------|
| `atrPeriod1` | `in_0` | int | `10` |
| `multiplier1` | `in_1` | float | `1.0` |
| `atrPeriod2` | `in_2` | int | `5` |
| `multiplier2` | `in_3` | float | `0.5` |
| `useHeikenAshi` | `in_4` | bool | `true` |
| `showLabels` | `in_5` | bool | `true` |
| `showBG` | `in_6` | bool | `true` |

#### Inputs (historical JS reference — INPUT_MAP)

| variable | tvInputId | type | default |
|----------|-----------|------|---------|
| `atrPeriod1` | `in_0` | `int` | `10` |
| `multiplier1` | `in_1` | `float` | `1` |
| `atrPeriod2` | `in_2` | `int` | `5` |
| `multiplier2` | `in_3` | `float` | `0.5` |
| `useHeikenAshi` | `in_4` | `bool` | `true` |
| `showLabels` | `in_5` | `bool` | `true` |
| `showBG` | `in_6` | `bool` | `true` |

#### Input Comparison

| Go name | Go TV id | Reference variable | Reference tvInputId | Name match | TV id match |
|---------|----------|-------------|--------------|------------|-------------|
| `atrPeriod1` | `in_0` | `atrPeriod1` | `in_0` | OK | OK |
| `multiplier1` | `in_1` | `multiplier1` | `in_1` | OK | OK |
| `atrPeriod2` | `in_2` | `atrPeriod2` | `in_2` | OK | OK |
| `multiplier2` | `in_3` | `multiplier2` | `in_3` | OK | OK |
| `useHeikenAshi` | `in_4` | `useHeikenAshi` | `in_4` | OK | OK |
| `showLabels` | `in_5` | `showLabels` | `in_5` | OK | OK |
| `showBG` | `in_6` | `showBG` | `in_6` | OK | OK |

#### Go parser reads from periods[] (getField aliases)

`BUY`, `Background_Color`, `SELL`, `ST1`, `ST2`, `ST2_colorer`, `ULTRA_BUY`, `ULTRA_SELL`, `plot_0`, `plot_2`, `plot_3`, `plot_4`

#### Go Structure keys produced

`aligned`, `background`, `buySignals`, `combined`, `currentBuy`, `currentSell`, `currentUltraBuy`, `currentUltraSell`, `sellSignals`, `st1`, `st2`, `ultraBuy`, `ultraSell`

#### Reference payload schema (from captured dump)

Dump: `skill-analysis/dumps/ultra-sensitive-supertrend/payload.json`

| key | type/shape |
|-----|-----------|
| `status` | `str` |
| `exitCode` | `int` |
| `timestamp` | `str` |
| `execution` | `object{durationMs,attempts}` |
| `agentContext` | `object{workflow,modelVersion,symbol,timeframe,htfTimeframe}` |
| `trend` | `object{combined,aligned,st1,st2,background}` |
| `signals` | `object{buy,sell,ultraBuy,ultraSell,currentBuy,currentSell}` |
| `st1Crosses` | `list[any]` |
| `signalHistory` | `object{lastBuy,lastSell,lastUltraBuy,lastUltraSell}` |
| `opportunities` | `list[any]` |
| `narrative` | `object{marketStructure,primaryOpportunity,warnings,watchlist}` |
| `conformance` | `object{hasValidData,agenticScore}` |
| `schemaVersion` | `str` |

#### Go `SkillResult` schema (target output)

| field | type |
|-------|------|
| `status` | string |
| `workflow` | string |
| `market.lastPrice` | any |
| `market.bias` | string |
| `structure` | map[string]any (per-skill keys above) |
| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |
| `narrative.marketStructure` | string |
| `narrative.primaryOpportunity` | string |
| `narrative.warnings[]` | string |
| `narrative.watchlist[]` | string |
| `validation.passed` | bool |
| `validation.warnings[]` | string |
| `conformance.hasValidData` | bool |
| `conformance.agenticScore` | float64 |
| `raw` | map (omitted in agent output) |

#### Discrepancies & fix recommendations

- **Input `multiplier1` default mismatch.** Go=`1.0` JS=`1`.
- **Reference payload has rich keys not in Go SkillResult:** `signalHistory, signals, st1Crosses, trend`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.

#### Captured `--help` output

```
Ultra Sensitive SuperTrend — Standalone Runner
Usage: node ultra-sensitive-supertrend.cjs <SYMBOL> [options]
Options: --tf, --bars, --input key=value, --json, --agent, --out, --verbose, --dry-run, --help
Inputs: atrPeriod1, multiplier1, atrPeriod2, multiplier2, useHeikenAshi, showLabels, showBG
```

#### Original SKILL.md excerpt (first 1500 chars)

```markdown
---
name: ultra-sensitive-supertrend
description: |
  Use the Ultra Sensitive SuperTrend TradingView indicator to analyze dual SuperTrend alignment, detect ultra buy/sell signals, and identify high-confidence trend entries based on double confirmation.
version: 1.0.0
license: MIT
author: TradingView Pine Skills
compatibility: Node.js 18+ with tv-optimized.cjs, tv.cjs, agent-output.cjs and .env (SESSION, SIGNATURE) at project root
metadata:
  hermes:
    tags: [trading, tradingview, pine-script, supertrend, dual]
    category: trading
required_environment_variables:
  - name: SESSION
    prompt: TradingView session cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid
    required_for: full functionality
  - name: SIGNATURE
    prompt: TradingView signature cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid_sign
    required_for: full functionality
---

# Ultra Sensitive SuperTrend — Trading Opportunity Finder

## When to Use

Helps the user run the standalone `ultra-sensitive-supertrend.cjs` script against any TradingView symbol and timeframe, then interprets the structured output to surface high-probability trading setups based on dual SuperTrend confirmation. The output includes:

- **Combined Trend** — BULLISH/BEARISH/MIXED from dual ST alignment
- **Alignment Status** — whether ST1 and ST2 agree
- **Signal Counts** — buy, sell, ultraBuy, ultraSell in lookback
- **Signal Histor
```

---

### 2.16 `tv vgaps` — `volume-gaps-imbalances-zeiierman`

- **Synopsis:** Volume Gaps & Imbalances — zero-volume voids and order flow
- **Pine ID:** `PUB;ff1a0136336340f38e908eeb12ea33aa`  (reference Pine ID: `PUB;ff1a0136336340f38e908eeb12ea33aa`)
- **Workflow ID:** `trend-following-gap-rejection`  (captured reference: `trend-following-gap-rejection`)
- **Go parser:** `internal/skill/parsers/vgaps.go`  → func `parseVGaps` / format `formatVGaps`
- **Historical JS runner:** `/Volumes/ExMac/code/tradingview/js-experiment06/volume-gaps-imbalances-zeiierman.cjs`
- **Historical sample command:** `node volume-gaps-imbalances-zeiierman.cjs BTCUSDT --preset scalping --tf 5m --bars 300 --agent --json`
- **Captured reference run:** rc=0 parsed_via=bracket hint=None
- **Reference payload top-level keys (17):** `agentContext, conformance, currentBar, execution, exitCode, keyLevels, market, narrative, opportunities, recentBars, schemaVersion, signals, status, structure, summary, timestamp, validation`

#### Inputs (Go)

| name | tv_input_id | type | default |
|------|-------------|------|---------|
| `prd` | `in_0` | int | `200` |
| `rows` | `in_1` | int | `50` |
| `src` | `in_2` | source | `"hlc3"` |
| `width` | `in_3` | int | `100` |
| `sum_sections` | `in_7` | int | `20` |
| `sum_panel_w` | `in_8` | int | `40` |
| `sum_gap_x` | `in_9` | int | `4` |
| `delta_min_frac` | `in_15` | float | `0.2` |

#### Inputs (historical JS reference — INPUT_MAP)

| variable | tvInputId | type | default |
|----------|-----------|------|---------|
| `prd` | `in_0` | `int` | `200` |
| `rows` | `in_1` | `int` | `50` |
| `src` | `in_2` | `source` | `hlc3` |
| `width` | `in_3` | `int` | `100` |
| `bull_color` | `in_4` | `color` | `color.new(color.blue, 30)` |
| `bear_color` | `in_5` | `color` | `color.new(color.orange, 30)` |
| `zone_color` | `in_6` | `color` | `color.new(color.navy, 50)` |
| `sum_sections` | `in_7` | `int` | `20` |
| `sum_panel_w` | `in_8` | `int` | `40` |
| `sum_gap_x` | `in_9` | `int` | `4` |
| `sum_show_label` | `in_10` | `bool` | `true` |
| `delta_pos_color` | `in_11` | `color` | `color.new(color.lime, 20)` |
| `delta_neg_color` | `in_12` | `color` | `color.new(color.red,  20)` |
| `delta_neutral_bg` | `in_13` | `color` | `color.new(color.gray, 90)` |
| `delta_text_color` | `in_14` | `color` | `color.white` |
| `delta_min_frac` | `in_15` | `float` | `0.2` |

#### Input Comparison

| Go name | Go TV id | Reference variable | Reference tvInputId | Name match | TV id match |
|---------|----------|-------------|--------------|------------|-------------|
| `prd` | `in_0` | `prd` | `in_0` | OK | OK |
| `rows` | `in_1` | `rows` | `in_1` | OK | OK |
| `src` | `in_2` | `src` | `in_2` | OK | OK |
| `width` | `in_3` | `width` | `in_3` | OK | OK |
| `sum_sections` | `in_7` | `sum_sections` | `in_7` | OK | OK |
| `sum_panel_w` | `in_8` | `sum_panel_w` | `in_8` | OK | OK |
| `sum_gap_x` | `in_9` | `sum_gap_x` | `in_9` | OK | OK |
| `delta_min_frac` | `in_15` | `delta_min_frac` | `in_15` | OK | OK |
| — | — | `bull_color` | `in_4` | color (cosmetic, safe to omit) | — |
| — | — | `bear_color` | `in_5` | color (cosmetic, safe to omit) | — |
| — | — | `zone_color` | `in_6` | color (cosmetic, safe to omit) | — |
| — | — | `sum_show_label` | `in_10` | **MISSING in Go** | — |
| — | — | `delta_pos_color` | `in_11` | color (cosmetic, safe to omit) | — |
| — | — | `delta_neg_color` | `in_12` | color (cosmetic, safe to omit) | — |
| — | — | `delta_neutral_bg` | `in_13` | color (cosmetic, safe to omit) | — |
| — | — | `delta_text_color` | `in_14` | color (cosmetic, safe to omit) | — |

#### Presets

| preset | go | js | match |
|--------|----|----|-------|
| `default` | `{'prd': '200'}` | `{'prd': 200, 'rows': 50, 'src': 'hlc3', 'width': 100, 'sum_sections': 20, 'sum_panel_w': 40, 'sum_gap_x': 4, 'sum_show_label': True, 'delta_min_frac': 0.2}` | OK js-only:{'rows', 'sum_gap_x', 'sum_show_label', 'width', 'src', 'delta_min_frac', 'sum_sections', 'sum_panel_w'} |
| `scalping` | `{'prd': '100'}` | `{'prd': 100, 'rows': 30, 'src': 'hlc3', 'width': 60, 'sum_sections': 10, 'sum_panel_w': 20, 'sum_gap_x': 2, 'sum_show_label': True, 'delta_min_frac': 0.3}` | OK js-only:{'rows', 'sum_gap_x', 'sum_show_label', 'width', 'src', 'delta_min_frac', 'sum_sections', 'sum_panel_w'} |
| `swing` | `{'prd': '400'}` | `{'prd': 1000, 'rows': 150, 'src': 'hlc3', 'width': 150, 'sum_sections': 25, 'sum_panel_w': 60, 'sum_gap_x': 6, 'sum_show_label': True, 'delta_min_frac': 0.15}` | PARTIAL js-only:{'rows', 'sum_gap_x', 'sum_show_label', 'width', 'src', 'delta_min_frac', 'sum_sections', 'sum_panel_w'} |

#### Go parser reads from periods[] (getField aliases)

`Close`, `GapCount`, `Gaps`, `close`, `gapCount`, `plot_0`, `plot_1`

#### Go Structure keys produced

`gapCount`, `price`

#### Reference payload schema (from captured dump)

Dump: `skill-analysis/dumps/volume-gaps-imbalances-zeiierman/payload.json`

| key | type/shape |
|-----|-----------|
| `status` | `str` |
| `exitCode` | `int` |
| `timestamp` | `str` |
| `execution` | `object{durationMs,attempts}` |
| `agentContext` | `object{workflow,htfTimeframe,modelVersion,symbol,timeframe}` |
| `market` | `object{lastPrice,bias,dominantFlow,regime}` |
| `structure` | `object{gaps,profile,delta}` |
| `opportunities` | `list[object{rank,setup,direction,confidence,confluenceScore}]` |
| `keyLevels` | `object{poc,valueAreaHigh,valueAreaLow,nearestSupport,nearestResistance,structuralGaps}` |
| `narrative` | `object{marketStructure,primaryOpportunity,warnings,watchlist}` |
| `validation` | `object{passed,checks,warnings}` |
| `conformance` | `object{hasValidStructure,hasDirectionalImpulse,profileBalance,agenticScore}` |
| `schemaVersion` | `str` |
| `summary` | `object{status,bias,dominantFlow,hasStructuralGaps,gapCount,largestGapHeight...}` |
| `currentBar` | `object{time,close}` |
| `recentBars` | `list[object{priceTop,priceBottom,direction,xStart,xEnd}]` |
| `signals` | `list[object{rank,setupType,direction,entryZone,optimalEntry}]` |

#### Go `SkillResult` schema (target output)

| field | type |
|-------|------|
| `status` | string |
| `workflow` | string |
| `market.lastPrice` | any |
| `market.bias` | string |
| `structure` | map[string]any (per-skill keys above) |
| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |
| `narrative.marketStructure` | string |
| `narrative.primaryOpportunity` | string |
| `narrative.warnings[]` | string |
| `narrative.watchlist[]` | string |
| `validation.passed` | bool |
| `validation.warnings[]` | string |
| `conformance.hasValidData` | bool |
| `conformance.agenticScore` | float64 |
| `raw` | map (omitted in agent output) |

#### Discrepancies & fix recommendations

- **Historical JS input `bull_color` (`in_4`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `bear_color` (`in_5`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `zone_color` (`in_6`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `sum_show_label` (`in_10`) missing in Go.** Consider adding `InputDef{Name: "sum_show_label", TVInputID: "in_10", Type: "bool", Default: true}`.
- **Historical JS input `delta_pos_color` (`in_11`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `delta_neg_color` (`in_12`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `delta_neutral_bg` (`in_13`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Historical JS input `delta_text_color` (`in_14`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).
- **Reference payload has rich keys not in Go SkillResult:** `currentBar, keyLevels, recentBars, signals, summary`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.

#### Captured `--help` output

```
◇ injected env (13) from .env // tip: ◈ secrets for agents [www.dotenvx.com]

Volume Gaps & Imbalances (Zeiierman) — Standalone Runner
===========================================================

Usage:
  node volume-gaps-imbalances-zeiierman.cjs <SYMBOL> [options]

Arguments:
  SYMBOL                    Trading pair (default: BTCUSDT)

Options:
  --tf <timeframe>          Timeframe: 1m, 5m, 15m, 1h, 4h, 1D (default: 15m)
  --bars <n>                Number of chart bars (default: 500)
  --preset <name>           Preset: default, scalping, swing (default: default)
  --lookback <n>            Override preset lookback
  --rows <n>                Override preset rows
  --json                    Output JSON instead of table
  --agent                   Agent mode (simplified JSON, optimized for AI agents)
  --out <file>              Write JSON to file
  --verbose, -v             Verbose output for debugging
  --dry-run                 Skip TradingView connection, show parsed args only
  --help, -h                Show this help

Examples:
  node volume-gaps-imbalances-zeiierman.cjs BTCUSDT
  node volume-gaps-imbalances-zeiierman.cjs ETHUSDT --tf 1h --bars 1000
  node volume-gaps-imbalance
```

#### Original SKILL.md excerpt (first 1500 chars)

```markdown
---
name: volume-gaps-imbalances-zeiierman
description: |
  Use the Volume Gaps & Imbalances (Zeiierman) TradingView indicator to analyze any symbol/timeframe and extract structural trading signals.
version: 1.0.0
license: MIT
author: TradingView Pine Skills
compatibility: Node.js 18+ with tv-optimized.cjs, tv.cjs, agent-output.cjs and .env (SESSION, SIGNATURE) at project root
metadata:
  hermes:
    tags: [trading, tradingview, pine-script, volume-gaps, imbalances]
    category: trading
required_environment_variables:
  - name: SESSION
    prompt: TradingView session cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid
    required_for: full functionality
  - name: SIGNATURE
    prompt: TradingView signature cookie
    help: Extract from browser DevTools → Application → Cookies → tradingview.com → sessionid_sign
    required_for: full functionality
---

# Volume Gaps & Imbalances (Zeiierman) — Trading Opportunity Finder

## When to Use

Helps the user run the standalone `volume-gaps-imbalances-zeiierman.cjs` script against any TradingView symbol and timeframe, then interprets the structured output to surface high-probability trading setups. The output includes:

- **Zero-Volume Gaps** — structural voids where no liquidity changed hands (deeper than candle-based FVGs)
- **Bull/Bear Volume Profile** — per-price-row dominance reading
- **Delta Panel** — sector-based buy/sell pressure percentage

The skill connects raw indicator ou
```

---

## 3. Historical JS Runners Without a Go Command

These JS runners exist in `js-experiment06` but have no dedicated `tv <skill>` Go command.
They are either meta-utilities or not yet ported.

| Historical JS runner | Type | Notes |
|----------------------|------|-------|
| `generic-indicator` | meta/utility | Universal Pine runner. Go equivalent: `tv run <pineId> --signals`. |
| `tv-cron-orchestrator` | meta/utility | Schedules recurring scans + position monitor. Not a Pine indicator. |
| `nlm-cli-skill` | meta/utility | Meta-skill (NLM CLI wrapper). Not a Pine indicator. |
| `tv-indicator` | meta/utility | tvcli.js — parent CLI. Replaced by this Go binary. |
| `youtube-to-tv-pine` | meta/utility | Converts YouTube videos to Pine scripts. Not a Pine indicator. |

## 4. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  tvcli (Go binary)                                                   │
│                                                                     │
│  cmd/tvcli/main.go                                                   │
│    └─ cli.Root → cmd.RegisterAll                                     │
│                                                                     │
│  internal/cmd/skillcmd.go   ← generic skill command runner           │
│    1. resolve inputs (defaults → preset → flags → passthrough)       │
│    2. service.RunScript (PineID, symbol, tf, bars, inputs)           │
│    3. skill.ParseOutput(periods, graphic, tf, symbol, args)         │
│    4. FormatText or JSON / agent-ready envelope                      │
│                                                                     │
│  internal/skill/skill.go   ← Skill, InputDef, SkillResult, AgentResult│
│  internal/skill/registry.go ← global Register/Get/All                │
│  internal/skill/parsers/*.go ← per-skill ParseOutput implementations │
│                                                                     │
│  service.RunScript (pinefacade) → TradingView WS                     │
│    returns: periods []map[string]any, graphic map[string]map[string]any│
└─────────────────────────────────────────────────────────────────────┘

Historical JS reference runner: `js-experiment06/<skill>.cjs`
  1. parseArgs → INPUT_MAP → Pine inputs
  2. tv.cjs WS fetch (same raw periods/graphic)
  3. parseOutput(periods, graphic) → structured object
  4. transformForAgentMode → agent-ready-v2 envelope
  5. stdout: pretty table OR JSON (with --agent --json)
```

Both Go and the historical JS runner read the same raw `periods[]` / `graphic` from the same
TradingView Pine WS. The captured JS `--agent` output in `skill-analysis/dumps/<skill>/payload.json`
is the reference target schema; the Go parser must reproduce the equivalent `SkillResult` so that
`ToAgent()` produces a comparable `agent-ready-v2` envelope.

## 5. SkillResult vs reference payload mapping

| Go `SkillResult` field | Reference payload key | Notes |
|------------------------|----------------------|-------|
| `status`               | `status`             | direct |
| `workflow`             | `agentContext.workflow` | Go sets; reference payload stores it in `agentContext` |
| `market.lastPrice`      | (per-skill: `latest.close`, `currentBar.close`, etc.) | Go consolidates |
| `market.bias`          | `volume.dominanceRatio`→bias, `mtf.overallBias`, `combinedTrend`, etc. | per-skill |
| `structure`            | per-skill (e.g. `volume`, `mtf`, `clusters`, `trend`, `signals`) | **mirror reference payload here** |
| `opportunities[]`      | `opportunities[]`    | direct; match `setup/direction/confidence/confluenceScore/rationale` |
| `narrative.marketStructure` | `narrative.marketStructure` | direct |
| `narrative.primaryOpportunity` | `narrative.primaryOpportunity` | direct |
| `narrative.warnings[]` | `narrative.warnings`/`watchlist` | direct |
| `conformance.hasValidData` | `conformance.hasValidData` | direct |
| `conformance.agenticScore` | `conformance.agenticScore` | direct |
| _not in Go_            | `agentContext`, `execution`, `timestamp`, `exitCode`, `schemaVersion` | added by `Skill.ToAgent()` envelope |
| _not in Go_            | per-skill rich keys (`latestBars`, `recentCrosses`, `clusters`, `trendLabels`, `grades`, `tradePlan`, `tqiBreakdown`, etc.) | **should be mirrored into `Structure` or `Raw`** |

## 6. How to Use This Index When Fixing a Go Skill

**Companion docs:**
- [`PARSING_PROTOCOL_FOR_GO.md`](PARSING_PROTOCOL_FOR_GO.md) — skill command invocation, payload envelope, routing.
- [`PINE_RESPONSE_SKILL.md`](PINE_RESPONSE_SKILL.md) — raw response anatomy, schema usage, input filtering.

Steps:

1. Open the per-skill section above (e.g. `2.5 tv sniper — precision-sniper`).
2. **Try the generic extractor first.** It uses Pine `metaInfo` and often bypasses field-name alias bugs.
   ```
   ./tvcli <go-skill> --symbol <SYM> --tf <TF> --signals --agent --json > /tmp/generic.json
   ```
   If this output is good enough for your use case, you can stop here or use it as the target while fixing the custom parser.
3. Confirm Pine ID and Workflow match. Fix the constants in the Go parser file if not.
4. Diff the Go `Inputs` table vs the historical JS `INPUT_MAP` table. Fix names/TV input IDs/defaults/types. Skip cosmetic color/visibility inputs (see `PINE_RESPONSE_SKILL.md`).
5. Read `skill-analysis/dumps/<pine-script-name>/payload.json` to see the captured reference payload.
6. Identify rich reference payload keys that have no Go `Structure`/`Raw` counterpart — decide whether to port them.
7. Run the Go skill and diff against the captured reference:
   ```
   ./tvcli <go-skill> --symbol <SYM> --tf <TF> --json --agent > /tmp/go.json
   diff <(jq -S . /tmp/go.json) <(jq -S . skill-analysis/dumps/<pine-script-name>/payload.json)
   ```
8. Iterate the Go `ParseOutput` function until the diff converges (allow for live data drift).

### Regenerating this index

```bash
# Re-run all JS scripts and refresh dumps/meta (slow — hits TradingView live)
python3 analyze_skills.py

# Re-extract payloads from existing stdout.txt dumps (fixes input-echo vs real-payload issues)
python3 skill-analysis/reextract_payloads.py

# Re-build this index after changes to Go parsers or JS scripts
python3 skill-analysis/build_reference_index.py
```

## 7. Verification Status

| Skill | Reference payload captured | Meta captured | Go parser |
|-------|---------------------------|---------------|-----------|
| `tv anchored-vp` | yes | yes | `internal/skill/parsers/anchored_vp.go` |
| `tv bsv` | yes | yes | `internal/skill/parsers/bsv.go` |
| `tv dvi` | yes | yes | `internal/skill/parsers/dvi.go` |
| `tv ema-atr` | yes | yes | `internal/skill/parsers/ema_atr.go` |
| `tv golden` | yes | yes | `internal/skill/parsers/golden.go` |
| `tv ict` | yes | yes | `internal/skill/parsers/ict.go` |
| `tv mtf` | yes | yes | `internal/skill/parsers/mtf.go` |
| `tv quantum` | yes | yes | `internal/skill/parsers/quantum.go` |
| `tv shemar` | yes | yes | `internal/skill/parsers/shemar.go` |
| `tv smc` | yes | yes | `internal/skill/parsers/smc.go` |
| `tv sniper` | yes | yes | `internal/skill/parsers/sniper.go` |
| `tv sr-breaks` | yes | yes | `internal/skill/parsers/sr_breaks.go` |
| `tv swingarm` | yes | yes | `internal/skill/parsers/swingarm.go` |
| `tv trend` | yes | yes | `internal/skill/parsers/trend.go` |
| `tv ust` | yes | yes | `internal/skill/parsers/ust.go` |
| `tv vgaps` | yes | yes | `internal/skill/parsers/vgaps.go` |

---

_End of index. Regenerate via `python3 skill-analysis/build_reference_index.py`._