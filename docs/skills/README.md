# Skill Commands Reference

Each file documents one registered `tvcli <skill>` command: its Pine Script, input variables, presets, and which inputs matter most when switching markets.

## Active Skills (15)

> Plus `cust` (ScalpQuant v2, private), `vp`, `ust`, `xau-trend` — see individual docs below.

| Skill | Category | Key Inputs to Vary |
|-------|----------|-------------------|
| [bsv](bsv.md) | Volume | `lengthMA1/2`, `maType` |
| [dvi](dvi.md) | Volume + Trend | `length_volatility`, `length_momentum` |
| [ema-atr](ema-atr.md) | Trend + Risk | `ema2Len/3Len`, `atrLen`, `atrMult` |
| [gold-divergence](gold-divergence.md) | Momentum | `rsiLength` |
| [liq-sweep](liq-sweep.md) | Liquidity | `swingLookback`, `volumeMultiplier` |
| [order-flow](order-flow.md) | Volume | `vmaLength`, `volumeMultiplier` |
| [quantum](quantum.md) | Trend Ribbon | All 8 EMA lengths |
| [shemar](shemar.md) | Trend + Confidence | `hmaLength`, `atrPeriod`, `factor` |
| [smc](smc.md) | Market Structure | `swingsLengthInput` |
| [sniper](sniper.md) | EMA + Signals | All 5 EMA lengths |
| [sr-breaks](sr-breaks.md) | Structure | `leftBars`, `rightBars` |
| [swingarm](swingarm.md) | Trend + Risk | `ATRPeriod`, `ATRFactor` |
| [ust](ust.md) | Trend | `atrPeriod1/2`, `multiplier1/2` |
| [vp](vp.md) | Volume Profile | `lookback`, `percentile` |
| [vp-pro](vp-pro.md) | Volume Profile | `mode`, `numBars`/`anchorOffset`, `rows`, `valueAreaPct` |
| [xau-trend](xau-trend.md) | Trend + Volatility | `emaShort/Long`, `bollingerMult` |

## Limited Skills (5)

| Skill | Issue | Workaround |
|-------|-------|------------|
| [golden](golden.md) | Wrong PineID | Reports `no_data` |
| [ict](ict.md) | Heavy script (80 inputs) | `--signals --agent --json` with paid tier |
| [mtf](mtf.md) | XAUUSD-specific | Reports `no_data` on other symbols |
| [trend](trend.md) | Heavy script (78 inputs) | `--signals --agent --json` with paid tier |
| [vgaps](vgaps.md) | Server-side timeout | `--signals --agent --json` with paid tier |
| [anchored-vp](anchored-vp.md) | Graphics-only script | Reports `no_data` |

## Market Tuning Guide

### Crypto (BTC, ETH)
- Shorter EMA periods (2-10 for fast, 10-50 for swing)
- Lower ATR multipliers (1.0-2.0)
- Tighter pivot lookbacks (5-15)
- Higher volume thresholds (volume is naturally high)

### Forex (XAUUSD, EURUSD)
- Medium EMA periods (9-21 for fast, 50-200 for swing)
- Standard ATR multipliers (1.5-3.0)
- Standard pivot lookbacks (15-20)
- Standard volume thresholds

### Equities / Swing
- Longer EMA periods (20-200)
- Wider ATR multipliers (3.0-5.0)
- Wider pivot lookbacks (20-50)
- Use `--bars 500` for sufficient data
