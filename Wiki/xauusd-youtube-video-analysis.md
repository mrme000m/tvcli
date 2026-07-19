---
type: Research
title: XAUUSD YouTube Video Analysis — Implementation Notes for Skill Commands
description: NotebookLM-extracted details from seven XAUUSD/TradingView YouTube tutorials mapped to volume profile, liquidity, market structure, order flow, volatility/trend, price action, and sentiment/gap analysis.
tags: [tradingview, pine-script, xauusd, youtube, notebooklm, skill-commands]
timestamp: 2026-07-19T06:30:00Z
okf_publish: false
---

# XAUUSD YouTube Video Analysis — Implementation Notes for Skill Commands

This page contains the structured takeaways from seven YouTube videos about TradingView / XAUUSD analysis. The videos were processed with Google NotebookLM via the `notebooklm` CLI, and the results are organized here so skill commands can turn them into quantitative signals.

## What we did

1. Created a NotebookLM notebook: `XAUUSD Pine Scripts YouTube Details`.
2. Added public YouTube URLs as sources.
3. Used `notebooklm ask` with explicit per-aspect prompts to extract:
   - Key concepts
   - Indicator/strategy name
   - Signal generation rules
   - Confidence/scoring approach
   - Best timeframe
   - Practical setup steps
4. Merged and cleaned the answers into this implementation-ready reference.

> **Raw extracts are also saved in the workspace:**
> - `notebooklm_youtube_extract_structured.json`
> - `notebooklm_gap_extract.json`
> - `notebooklm_orderflow_extract.json`
> - `notebooklm_youtube_extract_final.json` (cleaned, final version)

## Video-to-aspect mapping

| # | Aspect | Video title | YouTube URL |
|---|---|---|---|
| 1 | Volume Profile | I Built a New Kind Of Volume Profile — It Won An Award (Free Tool) | https://www.youtube.com/watch?v=NKf_6vVmvCI |
| 2 | Liquidity | ITC Liquidity Sweep Indicator Explained — Smart Money Liquidity Sweeps with TradingView Pine Script | https://www.youtube.com/watch?v=qFb7uEF_NX4 |
| 3 | Market Structure | Smart Money Market Structure Tool — BoS + CHoCH in TradingView | https://www.youtube.com/watch?v=NH4fzhUoENc |
| 4 | Order Flow | Detecting High-Frequency Trading: Pine Script Guide for Intraday Volume Spikes | https://www.youtube.com/watch?v=6UHNKSfwdk4 |
| 5 | Volatility/Trend | Pine Script Tutorial — Developing Real Trading Strategies On TradingView | https://www.youtube.com/watch?v=DJ97c8VkZaY |
| 6 | Price Action | Insane 1-Min Scalping Indicator! RSI Divergence | https://www.youtube.com/watch?v=_-kuNOfDEhU |
| 7 | Sentiment/Gap | Profitable GOLD Gap Trading Strategy Explained — XAUUSD | https://www.youtube.com/watch?v=Q9AafRH2BWg |

---

## 1. Volume Profile

**Video:** I Built a New Kind Of Volume Profile — It Won An Award (Free Tool)

### Key concepts
- Auction Market Theory
- Point of Control (POC)
- Value Area
- Volume Delta
- Session-based analysis

### Indicator/strategy
`10x Bull vs Bear VP Intraday Sessions` (award-winning TradingView indicator).

### Signal generation
- Identifies fair-value agreements through repeated/increasing POCs across sessions.
- Volume Delta tracks net limit-order fills at specific price/tick levels.
- Session overlaps are used to locate higher-volume / higher-volatility expansion zones.

### Confidence / scoring
- Award/recognition status is used as a social-proof filter.
- Overlapping sessions with rising POC volume increase confidence of a valid auction level.

### Best timeframe
Demonstrated on a 5-minute chart (Bitcoin example in the video), but the session logic transfers directly to XAUUSD.

### Setup steps
1. Search for `10X` in the TradingView indicators panel.
2. Configure up to 10 custom market sessions (e.g., London, New York, Tokyo).
3. Enable Volume Delta and Naked POC / Value Area for untested levels.

### Skill-command use
Map to a `volume_profile_score` feature:
```text
S_volume = 50 * inside_value_area
         + 30 * (1 - normalized_distance_to_POC)
         + 20 * normalized_volume_delta
```

---

## 2. Liquidity

**Video:** ITC Liquidity Sweep Indicator Explained — Smart Money Liquidity Sweeps with TradingView Pine Script

### Key concepts
- Liquidity zones
- Stop-loss hunting
- Swing highs / swing lows
- ICT-style smart-money concepts

### Indicator/strategy
`ITC Liquidity Sweep Indicator` (Pine Script v5)

### Signal generation
- Labels print when price sweeps (touches and reverses) a liquidity zone built from previous swing points.
- Zones are removed/invalidated once price breaks cleanly through the level.

### Confidence / scoring
- A level that has been swept but not broken adds confidence for a reversal/continuation play.
- Freshness matters: old levels can be filtered out.

### Best timeframe
Not explicitly stated; typical ICT intraday sweeps apply to M5–H1.

### Setup steps
1. Set `Swing Length` (e.g., 20 candles) to define pivot levels.
2. Set `Max Active Levels` to keep only recent liquidity zones.
3. Add alerts for Bullish/Bearish Sweep or Inside Zone conditions.

### Skill-command use
```text
S_liquidity = 60 * sweep_flag + 40 * min(volume / volumeAvg, 2) / 2
```

---

## 3. Market Structure

**Video:** Smart Money Market Structure Tool — BoS + CHoCH in TradingView

### Key concepts
- Break of Structure (BoS)
- Change of Character (CHoCH)
- Higher High / Higher Low (HH/HL) mapping
- Market indecision

### Indicator/strategy
`Smart Money Market Structure` indicator — auto-detects BoS/CHoCH and labels HH, HL, LH, LL.

### Signal generation
- Automatically marks structural swing points.
- Prints BoS/CHoCH labels when price breaks a mapped structural level.
- Mixed HH/LL patterns are flagged as market indecision.

### Confidence / scoring
- Clean, alternating HH/HL or LH/LL trends produce higher-confidence structural bias.
- Indecision (mixed structure) reduces confidence and warns of chop/range.

### Best timeframe
Demonstrated on a 5-minute chart (Nifty example), but the logic scales to gold on M5–H4.

### Setup steps
1. Add the indicator from the TradingView community library.
2. Use BoS/CHoCH labels to determine current bullish/bearish bias.
3. Combine structural labels with higher-timeframe trend direction.

### Skill-command use
```text
S_structure = 0.25*BoS + 0.25*CHoCH + 0.20*FVG + 0.15*trendline_break + 0.15*volume_spike
```
Each component normalized to 0–100.

---

## 4. Order Flow

**Video:** Detecting High-Frequency Trading: Pine Script Guide for Intraday Volume Spikes

### Key concepts
- High-frequency trading footprint detection
- Intraday volume spikes
- Market-hours filtering
- Volume multipliers
- Directional price bias

### Indicator/strategy
`Algo Detector` — a volume-spike detector with directional coloring.

### Signal generation
- Current volume must exceed the average volume over a lookback period by a user-defined multiplier.
- Signals are limited to defined market hours.
- Price direction (close vs previous close) labels the spike as upward (green) or downward (red).

### Confidence / scoring
- Confidence rises with the magnitude of the volume spike relative to average volume.
- Directional bias acts as a secondary filter: a spike aligned with price movement is stronger.

### Best timeframe
Intraday; demonstrated around market-open timestamps (e.g., 9:30 AM, 12:25 PM).

### Setup steps
1. Define market hours (default 9:30 AM–4:00 PM; adapt to XAUUSD sessions).
2. Choose average-volume lookback period.
3. Set the multiplier threshold that defines a significant spike.
4. Enable background coloring and alerts.

### Skill-command use
```text
S_orderflow = 70 * min(volume / volumeAvg, 2) / 2
            + 30 * directional_breakout_strength
```

---

## 5. Volatility / Trend

**Video:** Pine Script Tutorial — Developing Real Trading Strategies On TradingView

### Key concepts
- Series data in Pine Script
- Moving-average crossovers
- Average True Range (ATR)
- Trend following vs mean reversion

### Indicator/strategy
Custom EMA/SMA crossover strategy built from scratch in the Pine Editor.

### Signal generation
- Long when fast EMA crosses above slow EMA/SMA.
- Exits use ATR-based stop losses or a price cross back below a 200-period MA.
- Short rules are symmetrical.

### Confidence / scoring
- Strategies are backtested against Buy & Hold returns.
- Sharpe ratio is used as a risk-adjusted performance filter.

### Best timeframe
Demonstrated on a 1-hour chart (Bitcoin example); transferable to XAUUSD H1–H4.

### Setup steps
1. Open the Pine Editor and define fast/slow moving-average variables.
2. Use `overlay=true` to plot signals on price bars.
3. Define entries with `strategy.entry` and exits with `strategy.close`.

### Skill-command use
```text
S_trend = 40 * trend_component
        + 30 * momentum_component
        + 30 * volatility_component

trend_component     = clip((emaFast - emaSlow) / ATR, -1, 1) mapped 0–100
momentum_component  = |RSI - 50| * 2
volatility_component = (band_width / avg_band_width - 1) normalized 0–100
```

---

## 6. Price Action

**Video:** Insane 1-Min Scalping Indicator! — RSI Divergence

### Key concepts
- RSI divergence
- Regular vs hidden divergence
- Convergence
- Scalping

### Indicator/strategy
RSI Divergence indicator optimized for 1-minute scalping on gold.

### Signal generation
- Detects regular bullish/bearish divergences and hidden bullish/bearish divergences on the RSI oscillator.
- Divergences signal reversals (regular) or continuations (hidden).

### Confidence / scoring
- The video claims a high win rate for small scalp targets (e.g., 1000–2000 ticks).
- Cleaner divergence swings near RSI extremes increase confidence.

### Best timeframe
1-minute for gold scalping.

### Setup steps
1. Add the RSI Divergence script to the chart.
2. Hide unnecessary background lines for clarity.
3. Set take-profit parameters (e.g., 1000–2000 ticks) and test on demo.

### Skill-command use
```text
S_divergence = 40 * RSI_slope_quality
             + 30 * distance_from_extreme
             + 30 * leg_size_vs_ATR
```
Each term clipped to 0–1.

---

## 7. Sentiment / Gap

**Video:** Profitable GOLD Gap Trading Strategy Explained — XAUUSD

### Key concepts
- Opening/weekend gaps caused by news while markets are closed
- Gap Opening Level (last close before gap)
- Gap Closing Level (first open after gap)
- Gap fill expectation
- Liquidity/supply-demand zones
- Market-structure shift after consolidation

### Indicator/strategy
Gold Gap Trading Strategy — discretionary gap-fill model for XAUUSD.

### Signal generation
- **Gap down:** wait for price to test a significant demand/liquidity zone, consolidate under minor resistance, then enter long on bullish breakout + close above resistance.
- **Gap up:** wait for price to test a significant supply/liquidity zone, consolidate above minor support, then enter short on bearish breakout + close below support.
- Stop loss beyond the consolidation extreme; take profit near the Gap Opening Level.

### Confidence / scoring
- Most gaps on gold tend to fill, giving a baseline high-probability edge.
- Confidence increases when a bullish/bearish imbalance candle shows commitment and the reward/risk is at least 1.1:1.

### Best timeframe
1-hour or 4-hour charts.

### Setup steps
1. Identify a weekend gap-up or gap-down on XAUUSD.
2. Mark the Gap Opening Level as the primary take-profit target.
3. Wait for price to reach a liquidity zone (demand for gap-down, supply for gap-up).
4. Watch for a consolidation/accumulation range.
5. Enter on confirmed breakout and candle close beyond the range.
6. Stop loss beyond consolidation; target the gap-fill level.

### Skill-command use
```text
S_gap = 50 * normalized_gap_size
      + 30 * fill_progress_alignment
      + 20 * trend_alignment
```

---

## Composite score recipes

### Scalping composite (1 m–15 m)
```text
C_scalp = 0.30*S_liquidity
        + 0.25*S_orderflow
        + 0.20*S_structure
        + 0.15*S_volume
        + 0.10*S_divergence
```

### Swing composite (H1–D1)
```text
C_swing = 0.30*S_structure
        + 0.25*S_trend
        + 0.20*S_gap
        + 0.15*S_volume
        + 0.10*S_liquidity
```

### Decision thresholds
- `C >= 0.75` → high-confidence directional entry
- `0.55 <= C < 0.75` → medium confidence; require additional confluence
- `C < 0.55` → skip or reduce size

Pair with an ATR stop loss and minimum 1.5:1 reward/risk.

## Implementation checklist for skill commands

1. **Run Pine scripts** with the Go CLI:
   ```bash
   ./tvcli run PUB;0999ba6cd86e4709ad54bfa93034f5db \
       --symbol OANDA:XAUUSD --tf 5m --bars 500 --json
   ```
2. **Run NotebookLM research** on these YouTube sources when rules need updating or a new video is added.
3. **Parse JSON outputs** from both `tv run` and `notebooklm ask`.
4. **Normalize** each feature to 0–1 per bar.
5. **Blend** into `C_scalp` or `C_swing`.
6. **Emit signal object**:
   ```json
   {
     "symbol": "OANDA:XAUUSD",
     "tf": "5m",
     "scores": {
       "volume_profile": 62,
       "liquidity": 78,
       "market_structure": 55,
       "order_flow": 81,
       "trend_volatility": 48,
       "price_action": 33,
       "sentiment_gap": 40
     },
     "composite": 0.66,
     "bias": "long"
   }
   ```

## Caveats

- YouTube content is educational; claimed win rates should be treated as marketing until independently backtested.
- Some videos are demonstrated on Bitcoin or Nifty; the logic must be re-validated on XAUUSD market data.
- Sentiment/Gap video is discretionary; formalizing it into a bot requires robust gap-detection and pattern-recognition code.
- All scoring formulas are starting points. Tune weights, thresholds, and lookbacks with historical data.

## References

- NotebookLM CLI: `notebooklm --version` 0.7.3
- Go CLI: `./tvcli` in `/Volumes/ExMac/code/tradingview/go`
- Supporting research: `Wiki/xauusd-pine-scripts.md` (Pine IDs and script URLs)
