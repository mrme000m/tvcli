---
type: Research
title: XAUUSD Pine Script Implementations for Skill Commands
description: Public TradingView Pine scripts covering 7 market-analysis dimensions, including Pine IDs, YouTube tutorials, and signal-scoring recipes for scalping-to-swing XAUUSD trading.
tags: [tradingview, pine-script, xauusd, skill-commands, market-analysis]
timestamp: 2026-07-19T06:02:34Z
okf_publish: false
---

# XAUUSD Pine Script Implementations for Skill Commands

This document captures the public Pine Script indicators and strategies identified for building skill commands that analyze XAUUSD across seven market dimensions. Each script has a verified Pine ID from the workspace `tv` CLI, a tutorial link, and a recipe for turning its output into a quantitative confidence score.

## Purpose

Provide the workspace skill layer with:

1. A curated set of public TradingView Pine scripts covering volume profile, liquidity, market structure, order flow, volatility/trend, price action, and sentiment/gaps.
2. Exact Pine IDs so skill commands can call `./tvcli run <pineId>` deterministically.
3. A scoring framework that maps script outputs to 0–100 confidence values, then blends them into a composite signal.

## How the data was gathered

- **Initial discovery** performed with the PPLX CLI plugin (`pplx search --mode auto`) to find XAUUSD-relevant public Pine scripts and YouTube tutorials.
- **Pine ID verification** performed with the Go CLI:
  `./tvcli search "<script name>" --limit 10 --json`
- Currency/CFD symbol assumed as `OANDA:XAUUSD` (auto-resolves in `tvcli`).

## Legend

| Field | Meaning |
|---|---|
| **Pine ID** | The value used by `tvcli run`, e.g. `PUB;0999ba6cd86e4709ad54bfa93034f5db`. Pass the whole string including `PUB;`. |
| **Type** | `indicator` (overlay/oscillator) or `strategy` (has entry/exit/backtest logic). |
| **Source** | `open` = access 1, source visible in Pine Editor; `closed` = access 2, usable but code hidden. In the table below *all selected scripts return access 1/open*, so they can be opened and inspected. |
| **Score** | Suggested 0–100 component formula for that dimension. |

## CLI load-test verdict

After capturing raw TradingView responses with `./tvcli run <pine-id> --raw`, four of the seven scripts are good immediate CLI targets. The other three are either graphics-only or time out even with `TV_TIER=ultimate`:

| Aspect | Status | Notes |
|---|---|---|
| Volume Profile | ⚠️ Graphics-only | Returns 0 periods; all output is in `dwgboxes`/`dwglabels`. Use the existing numeric `vp` skill instead. |
| Liquidity | ✅ Implemented | `Bullish_Sweep_Shape` / `Bearish_Sweep_Shape` event flags. Skill: `tv liq-sweep`. |
| Market Structure | ❌ Timeout | `Mistab XAUUSD Strength Dashboard` fails to return data within 100 s. Use existing `smc`/`ict`. |
| Order Flow | ✅ Implemented | `bell` / `sell` 0/1 alert flags. Skill: `tv order-flow`. |
| Volatility/Trend | ✅ Implemented | `EMA_Court_Terme`, `EMA_Long_Terme`, `Bollinger_*` numeric plots. Skill: `tv xau-trend`. |
| Price Action | ✅ Implemented | `Bullish_Divergence` / `Bearish_Divergence` flags + `RSI`. Skill: `tv gold-divergence`. |
| Sentiment/Gap | ❌ Timeout | `XAUUSD Weekly Gap` fails to return data. Use existing `vgaps` or a custom script. |

Field maps were captured during load testing.

## The seven scripts

| # | Aspect | Script (author) | Pine ID | TV page | YouTube tutorial |
|---|---|---|---|---|---|
| 1 | Volume Profile | 10x Bull Vs. Bear VP Intraday Sessions — KioseffTrading | `PUB;0999ba6cd86e4709ad54bfa93034f5db` | [scripts/bullvsbear](https://www.tradingview.com/scripts/bullvsbear/) | [I Built a New Kind Of Volume Profile](https://www.youtube.com/watch?v=NKf_6vVmvCI) |
| 2 | Liquidity | Institutional Liquidity Sweep & Volume Breakout [SMC] — VENTURA_GLOBAL | `PUB;b9372355c2e6483f952ca49a21d2ebbb` | [script/QzT88bTs-...](https://www.tradingview.com/script/QzT88bTs-Institutional-Liquidity-Sweep-Volume-Breakout-SMC/) | [ITC Liquidity Sweep Indicator Explained](https://www.youtube.com/watch?v=qFb7uEF_NX4) |
| 3 | Market Structure | XAUUSD Strength Dashboard with Volume — Mistab1009 | `PUB;427c58b6f07c451f8abc24afcf202f69` | [script/T4Aa1v9U-...](https://www.tradingview.com/script/T4Aa1v9U-Mistab-XAUUSD-Strength-Dashboard/) | [Market Structure & Order Block Dashboard](https://www.youtube.com/watch?v=B_8zhD7su74) |
| 4 | Order Flow | Volume Spike Strategy — xxattaxx | `PUB;7uP2LNPDc8I150lUzs3Aqa5ju9usF0ZN` | [script/7uP2LNPD...](https://www.tradingview.com/script/7uP2LNPDc8I150lUzs3Aqa5ju9usF0ZN/) | [Pine Script Tutorial — Real Strategies](https://www.youtube.com/watch?v=DJ97c8VkZaY) |
| 5 | Volatility / Trend | XAUUSD Trend Strategy — Marwanefxx | `PUB;a4e47455574243fe9731423c4ddb50ca` | [script/jRFTHVt7-...](https://www.tradingview.com/script/jRFTHVt7-XAUUSD-Trend-Strategy/) | [Bullish Entry Gold Strategy](https://www.youtube.com/watch?v=1qBMFLX34EE) |
| 6 | Price Action | Advanced Gold Scalping Strategy with RSI Divergence — atakhadivi | `PUB;779d25a800b242cf9e2ecbe6f350c366` | [script/hdiQlcK8-...](https://www.tradingview.com/script/hdiQlcK8-Advanced-Gold-Scalping-Strategy-with-RSI-Divergence/) | [Insane 1-Min Scalping Indicator](https://www.youtube.com/watch?v=_-kuNOfDEhU) |
| 7 | Sentiment / Gap | XAUUSD Weekly Gap Indicator — oberlunar_tr | `PUB;9cdc4275992a4521809d3417a0f7e9da` | [script/kjcJSG8V-...](https://www.tradingview.com/script/kjcJSG8V-XAUUSD-Weekly-Gap-Indicator-oberlunar/) | [VIP Algos / XAUUSD Premium Regimes](https://www.youtube.com/watch?v=SvBwspGtO8A) |

### 1. Volume Profile — Bull Vs. Bear VP Intraday Sessions

**What it measures:** Session-based volume profile with POC, value area, low-volume nodes, and bull/bear volume delta per session.

**Extractable values:**
- Session POC price
- Value-area high/low
- Current price distance from POC
- Net volume delta for the active session

**Suggested score** `S_volume` 0–100:
```text
S_volume = 50 * inside_value_area
         + 30 * (1 - normalized_distance_to_POC)
         + 20 * normalized_volume_delta
```
- `inside_value_area` = 1 if price is inside the value area, else 0
- `normalized_distance_to_POC` = distance to POC / ATR, clipped 0–1
- `normalized_volume_delta` = delta / max recent delta, clipped 0–1

**Timeframe fit:** 1 min–1 h for scalps; aggregate multiple daily sessions to create a swing-bias reading.

---

### 2. Liquidity — Institutional Liquidity Sweep & Volume Breakout [SMC]

**What it measures:** Stop-hunts/liquidity sweeps around swing highs/lows, confirmed by a volume breakout filter.

**Extractable values:**
- Bullish sweep flag
- Bearish sweep flag
- Volume ratio vs moving average
- Proximity to swept swing level

**Suggested score** `S_liquidity` 0–100:
```text
S_liquidity = 60 * sweep_flag
            + 40 * min(volume / volumeAvg, 2) / 2
```
- Bullish sweep + high volume → score ≥ 70 = rejection/continuation long
- Bearish sweep + high volume → score ≥ 70 = rejection/continuation short

**Timeframe fit:** 5 m–15 m for scalping; 1 h–4 h to confirm swing-level liquidity grabs.

---

### 3. Market Structure — XAUUSD Strength Dashboard with Volume

**What it measures:** Multi-timeframe structural events for gold: BOS, CHOCH, FVG, trend-line break, SMA 9/21 cross, and volume spikes.

**Extractable values:**
- Already-exposed 0–100% structure strength score (per timeframe)
- Booleans for BOS / CHOCH / FVG / volume spike

**Suggested score** `S_structure` 0–100:
Use the dashboard’s built-in score directly, or weight sub-components:
```text
S_structure = 0.25*BOS + 0.25*CHOCH + 0.20*FVG + 0.15*trendline_break + 0.15*volume_spike
```
Each component normalized to 0–100.

**Timeframe fit:** M1–M15 for scalp direction; H1–H4 as the swing trend anchor.

---

### 4. Order Flow — Volume Spike Strategy

**What it measures:** Abnormal volume participation combined with price-momentum confirmation.

**Note:** the originally-cited “Volume Spike with Entry & Stop Loss” could not be resolved in the `tv` search index, so this closest public open-source match is used instead.

**Extractable values:**
- Volume spike flag
- Volume vs average ratio
- Close relative to recent N-bar high/low (momentum breakout)

**Suggested score** `S_orderflow` 0–100:
```text
S_orderflow = 70 * min(volume / volumeAvg, 2) / 2
            + 30 * breakout_strength
```
where `breakout_strength = clip((close - n_bar_low) / ATR, 0, 1)` for bullish read.

**Timeframe fit:** 1 m–5 m scalps; 15 m–1 h for swing breakout entries.

---

### 5. Volatility / Trend — XAUUSD Trend Strategy (EMA + RSI + Bollinger)

**What it measures:** Trend direction via EMA cross, momentum via RSI, and volatility expansion via Bollinger Bands.

**Extractable values:**
- EMA fast above/below slow
- RSI distance from 50
- Band width vs recent average band width

**Suggested score** `S_trend` 0–100:
```text
S_trend = 40 * trend_component
        + 30 * momentum_component
        + 30 * volatility_component

trend_component     = clip((emaFast - emaSlow) / ATR, -1, 1) mapped 0–100
momentum_component  = |RSI - 50| * 2
volatility_component = (band_width - avg_band_width) / avg_band_width normalized 0–100
```
Sign the score by direction; take absolute value when using as a confidence magnitude.

**Timeframe fit:** 15 m–H4; lower timeframes for scalping entries, higher for swing bias.

---

### 6. Price Action — Advanced Gold Scalping Strategy with RSI Divergence

**What it measures:** RSI divergence against price pivots on 1-minute gold, with labeled entry/exit points.

**Extractable values:**
- Bullish / bearish divergence flags
- RSI pivot slope
- Distance of RSI from 30 / 70
- Pivot leg size relative to ATR

**Suggested score** `S_divergence` 0–100:
```text
S_divergence = 40 * RSI_slope_quality
             + 30 * distance_from_extreme
             + 30 * leg_size_vs_ATR
```
Each term clipped to 0–1. Clean, multi-pivot divergences near 30/70 score ≥ 80.

**Timeframe fit:** 1 m–5 m scalping; can also flag swing reversal risk on M15–H1.

---

### 7. Sentiment / Gap — XAUUSD Weekly Gap Indicator

**What it measures:** Friday close vs Monday open gap size, direction, and fill progress for gold.

**Extractable values:**
- Gap magnitude in price or percentage
- Gap direction (bull/bear)
- Distance remaining to fill the gap

**Suggested score** `S_gap` 0–100:
```text
S_gap = 50 * normalized_gap_size
      + 30 * fill_progress_alignment
      + 20 * trend_alignment
```
- `normalized_gap_size` = gap size / recent ATR, clipped
- `fill_progress_alignment` = 1 when price is moving in gap-fill direction, else 0
- `trend_alignment` = 1 when gap direction aligns with higher-TF trend, else 0

**Timeframe fit:** H1–D1 swing; especially useful after weekend geopolitical/macroeconomic events.

## Composite confidence recipes

Normalize every component to 0–1, then combine.

### Scalping composite (1 m–15 m)
```text
C_scalp = 0.30*S_liquidity + 0.25*S_orderflow + 0.20*S_structure
        + 0.15*S_volume + 0.10*S_divergence
```

### Swing composite (H1–D1)
```text
C_swing = 0.30*S_structure + 0.25*S_trend + 0.20*S_gap
        + 0.15*S_volume + 0.10*S_liquidity
```

### Trading rule of thumb
- `C >= 0.75` → high-confidence directional entry
- `0.55 <= C < 0.75` → medium confidence; require confluence from at least one other dimension
- `C < 0.55` → skip or reduce size

Pair every entry with an ATR-based stop and at least 1.5:1 reward/risk.

## Skill command integration pattern

Each skill command can:

1. Run the script for the requested symbol/timeframe:
   ```bash
   ./tvcli run PUB;0999ba6cd86e4709ad54bfa93034f5db \
       --symbol OANDA:XAUUSD --tf 5m --bars 500 --json
   ```
2. Extract the relevant plot values from the JSON output.
3. Apply the per-script normalization and scoring recipe.
4. Aggregate into `C_scalp` or `C_swing` and emit a signal object:
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

- YouTube links are the closest public tutorials found, not always official videos for that exact script.
- All Pine IDs resolve as access-level 1 (open) in the `tvcli` search. Re-run `./tvcli search` if TradingView updates access levels.
- The order-flow slot uses a substitute because the originally cited script was not indexed by the `tvcli` search.
- Real trading requires validation, slippage control, and account-risk management; these scores are research/implementation inputs, not guaranteed signals.

## References

- PPLX CLI research output used as starting evidence for script names and YouTube tutorials.
- `./tvcli search` results used to verify Pine IDs, authors, and public access.
- Script pages and descriptions on TradingView were used to derive scoring recipes.
