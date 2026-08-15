## Overview

This report catalogs seven public TradingView indicators covering the core technical dimensions of gold (XAUUSD) analysis — Volume Profile, Liquidity/Order Blocks, Market Structure/Breaker Blocks, Fair Value Gaps, Supply/Demand Zones, Multi-Timeframe Bias, and ATR/Volatility Stop-Run Detection. Each entry includes quantitative outputs (scores, dashboards, probability reads) suitable for systematic speculation, alongside a matched YouTube tutorial and recommended timeframe usage for scalping versus swing trading.

## 1. Volume Profile

**Script:** Gold VP (Volume Profile Signal Indicator) — Pine Script indicator by KIMOOO1987, published on TradingView as a precision signal tool built specifically for intraday reversal entries on XAUUSD. A closely related open-source alternative is Volume Profile Area [BigBeluga], which nests a secondary Value Area profile inside the main volume distribution to expose liquidity concentration and quantifies Point of Control (POC), Value Area High/Low (VAH/VAL), and traded-volume labels at each node.[1][2]

**YouTube tutorial:** "The Easiest Gold Volume Profile Trading Strategy That Works!" demonstrates trading the Point of Control on XAUUSD charts using TradingView's volume profile tools. A complementary walkthrough, "Introduction to Volume Profiles on TradingView: Tutorial," covers the fundamentals of reading profile shape and node density.[3][4]

**Timeframes:** Scalping XAUUSD works best on the 5m–15m chart anchored to an intraday (Session) volume profile, watching for price rejection at POC/VAH/VAL. Swing trading uses a Daily or Weekly anchored profile viewed on the 4H–1D chart to locate high-volume nodes acting as multi-day support/resistance.[1]

**Key inputs/outputs:** Inputs include lookback period, Value Area percentage (default 50–70%), and PoC selection mode (Main/Area/Average). Outputs are numeric VAH/VAL price levels, POC price and volume, and node volume labels that quantify liquidity concentration for entry/exit placement.[1]

## 2. Liquidity / Order Blocks

**Script:** Advanced Order Block & Liquidity Mapping Tool by obtrading (OBHOW-TO), which combines 3- and 5-bar fractal order block detection with dynamic and static liquidity zone mapping, adapted from TFlab's open-source LiquidityFinderLibrary. It auto-removes broken order blocks and optionally validates blocks against Fair Value Gaps for higher-quality signals.[5]

**YouTube tutorial:** "LuxAlgo BEST Order Block Indicator Predicts 100% Perfect Reversals" walks through applying an order-block/SMC toolkit across gold, forex, and crypto for identifying institutional reversal zones. "Smart Money Concepts LuxAlgo Indicator" also demonstrates order block and liquidity detection with kill-zone overlays applicable to XAUUSD sessions.[6][7]

**Timeframes:** Scalpers apply order block detection on the 1m–15m charts during London/New York kill zones for precision entries. Swing traders use 4H–1D order blocks as directional bias zones, holding trades until the block is mitigated.[5][6]

**Key inputs/outputs:** Configurable fractal length for OB detection, FVG-validation toggle, and time-based label logic. Output is a set of bullish/bearish OB rectangles with automatic invalidation flags, functioning as quantifiable support/resistance zones for stop and target placement.[5]

## 3. Market Structure / Breaker Blocks

**Script:** Market Structure (Breakers) by LuxAlgo, an open-source indicator that formalizes "Breaker Market Structure" detection — extending broken swing levels into tradeable support/resistance and tracking the Maximum Breaks and Breaker Maximum Duration a structure can withstand before invalidation. Breaker Blocks by fluxchart is a comparable option that adds volumetric strength scoring to each breaker block.[8][9]

**YouTube tutorial:** "Try This New LuxAlgo Smart Money Concepts Indicator In Tradingview Market Structure Breakers LuxAlgo" is a direct tutorial on configuring and reading this exact script. "Advanced Trading with LuxAlgo's Market Structure & Inducements Indicator" extends the discussion into inducement-based confirmation.[10][11]

**Timeframes:** Scalping suits the 5m–15m chart to catch fast breaker reactions following BOS/CHoCH events; swing trading favors 1H–4H swing-period settings for durable breaker structures that persist over multiple sessions.[8]

**Key inputs/outputs:** Inputs include Swings Period, Maximum Breaks, and Breaker Maximum Duration (bars). Outputs are quantified breaker levels with break counts, plus green/red candle-border highlights flagging live support/resistance interaction events.[8]

## 4. Fair Value Gaps / Imbalances

**Script:** XAUUSD 1H – FVG Buy/Sell Signals by Gainsv50, a signal-only Pine v6 script purpose-built for gold that detects 3-bar FVGs, requires a configurable retest depth, and confirms with candle-body strength (ATR multiple) plus EMA-50/200 trend and Wilder ADX filters before firing BUY/SELL arrows. For broader configurability, the FVG Detector Library by TFlab offers four filter tiers (Very Aggressive to Very Defensive) for tuning signal quality.[12][13]

**YouTube tutorial:** "How to Trade Fair Value Gaps (FVG) in Forex | XAUUSD Strategy" explains spotting and trading FVGs specifically on gold[14], while "the ultimate fair value gap trading strategy (full masterclass)" walks through a full BOS→FVG→CHoCH gold trade using 1H/15M analysis with 1-minute entries[15].

**Timeframes:** The script is tuned for the 1H chart by default for scalping-to-intraday signals; for swing trading, widen the Min FVG size and Retest Depth settings and apply on 4H, using the ADX/EMA trend filter to hold positions longer.[13]

**Key inputs/outputs:** Inputs include Min FVG size (points), Retest depth (%), Close buffer, Min body ≥ ATR multiplier, and Min ADX threshold. Outputs are non-repainting BUY/SELL arrows generated only on bar close, plus alert conditions for automation.[13]

## 5. Supply/Demand Zones (Support/Resistance)

**Script:** Supply and Demand Zones by s_b_j, which automatically detects and highlights fresh institutional supply (resistance) and demand (support) zones based on significant candle patterns and imbalance structure. Supply & Demand Zones [RealFact] is a similar open script that flags zones via imbalance, engulfing patterns, and structural shifts, color-coding green (demand) and red (supply).[16][17]

**YouTube tutorial:** "Supply & Demand on Gold (Live Backtesting + Strategy Explained)" demonstrates a full XAUUSD 5-minute scalping approach combining FVGs, supply/demand zones, liquidity, and structure with live backtested examples. "Supply and Demand Zones in Gold Trading" covers manual zone identification specific to gold.[18][19]

**Timeframes:** Scalping XAUUSD uses zones drawn on the 5m–15m chart for quick reaction trades; swing trading relies on 4H–Daily zones as macro reaction levels held over several days.[17][19]

**Key inputs/outputs:** Inputs cover zone detection sensitivity and adjustable zone length; output is auto-plotted, color-coded supply/demand rectangles usable as quantified reaction levels for stop-loss and target placement rather than standalone entry triggers.[17]

## 6. Multi-Timeframe Trend / Bias

**Script:** MTF Confluence Dashboard by PineProfits, a non-repainting multi-timeframe confluence tool that aggregates short-vs-long moving average bias across 1m through 1W into a single on-chart Trend/Bias table with an overall "AVG sentiment" score, using closed-bar higher-timeframe data for reliability in alerting. Multi-Timeframe Bias Dashboard by otwlv is an alternative that classifies bias as Bullish/Bearish/Neutral using high/low sweep and candle-close analysis across timeframes.[20][21]

**YouTube tutorial:** No script-specific walkthrough was found for this exact tool; the LuxAlgo all-in-one tutorial demonstrates comparable multi-timeframe trend/structure confirmation applicable to gold, forex, and indices.[22]

**Timeframes:** For scalping, monitor the dashboard's 5m/15m/1H rows for short-term alignment before entries; for swing trading, weight the 4H/1D/1W rows to establish the dominant directional bias before holding multi-day positions.[20]

**Key inputs/outputs:** Inputs are the selectable timeframe list (1m–1W), MA type (SMA/EMA/WMA/HMA), and MA lengths (e.g., 50/200). Outputs are a per-timeframe Up/Down classification table plus an aggregated average sentiment reading, quantifying cross-timeframe trend confluence.[20]

## 7. Volatility / ATR-Based Signals / Stop-Run Detection

**Script:** Liquidity Sweep & ATR Envelope, a non-repainting open-source tool that fires signals only when three conditions align on the same candle: a wick piercing a tracked swing level (liquidity grab), that wick clearing an ATR-based envelope by a defined buffer (volatility extreme), and a close that reclaims the level with a displacement guard measuring reclaim strength against the wick's penetration depth. An alternative with an explicit probability score is Liquidity Sweep Probability [JOAT], which tracks equal-high/low zones, sweep/reclaim events, and outputs an adaptive 5–95 probability-style score blending sample rate, sweep distance, zone age, and reclaim status.[23][24]

**YouTube tutorial:** No dedicated video exists for these specific scripts; "How to Trade Fair Value Gaps (FVG) in Forex | XAUUSD Strategy" author's companion content references a "detailed liquidity sweep strategy video," and the Institutional Liquidity Sweep & Volume Breakout script description explains the stop-hunt mechanic applicable to gold on lower timeframes[25][14].

**Timeframes:** Defaults are tuned for the H1 chart and carry unchanged to M15 for scalping stop-hunt reversals; on H4 and above (swing trading), reducing Pivot Left/Right to 3 increases event frequency since sweeps become naturally rarer at higher timeframes.[23]

**Key inputs/outputs:** Inputs include Pivot Left/Right length, ATR clearance buffer, and displacement/reclaim fraction. Outputs are JSON-ready directional alerts (direction/level/trigger), gradient sweep zones, and swept-history footprints — or, for the probability variant, a compact dashboard score (5–95) representing sweep-reclaim continuation likelihood.[24][23]

## Comparison Summary

| Aspect | Script | Scalping TF | Swing TF | Core Output |
|---|---|---|---|---|
| Volume Profile | Gold VP / Volume Profile Area [BigBeluga] | 5m–15m (session profile) | 4H–1D (daily/weekly profile) | POC, VAH/VAL, node volume[2][1] |
| Liquidity/Order Blocks | Advanced OB & Liquidity Mapping Tool | 1m–15m (kill zones) | 4H–1D | Bullish/bearish OB zones, invalidation flags[5] |
| Market Structure/Breakers | Market Structure (Breakers) [LuxAlgo] | 5m–15m | 1H–4H | Breaker levels, break counts[8] |
| Fair Value Gaps | XAUUSD 1H FVG Buy/Sell Signals | 1H (default) | 4H (widened filters) | Non-repainting BUY/SELL arrows[13] |
| Supply/Demand | Supply and Demand Zones (s_b_j) | 5m–15m | 4H–1D | Color-coded reaction zones[17] |
| MTF Bias | MTF Confluence Dashboard | 5m/15m/1H rows | 4H/1D/1W rows | Trend table + avg sentiment[20] |
| ATR/Stop-Run | Liquidity Sweep & ATR Envelope / [JOAT] | H1/M15 | H4+ (adjusted pivots) | Sweep alerts / probability score[23][24] |