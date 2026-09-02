# mtf-confluence

Multi-timeframe confluence engine: computes the full XAU-SCALP component set on
the chart timeframe plus `request.security` reads for two higher timeframes —
all in ONE study run (one account slot, one anchor moment).

- **Source**: embedded (`pkg/skill/parsers/mtf_confluence.pine`, mirrored in
  faber0's `channel_verifier/pine/`) — no facade PineID, runs on any pool
  account and under `/hunt`. The server saves it per account on first use and
  reuses the saved script via the `/run` script cache.
- **Run**: `tvcli mtf-confluence --symbol OANDA:XAUUSD --tf 15`
  or `POST /run-skill {"skill": "mtf-confluence", ...}`.

## Key inputs

| Input | Default | Notes |
|-------|---------|-------|
| `htf1` / `htf2` | `60` / `240` | Higher timeframes (Pine timeframe strings) |
| `wChart` / `wHtf1` / `wHtf2` | `0.5` / `0.3` / `0.2` | MTF composite weights (normalized) |
| `ema1Len..ema4Len` | `3/8/21/55` | EMA stack (per TF context) |
| `stAtrLen`, `stFactor` | `10`, `3.0` | SuperTrend |
| `rsiLen` | `14` | RSI |
| `bbLen/bbMult/kcLen/kcMult` | `20/2.0/20/1.5` | Squeeze / Bollinger |
| `volLen` | `20` | Volume MA |

## Output (structure)

`composite`, `emaStack`, `stDir`, `rsi`, `atr`, `signal` (grade ±0..3; ±3 =
strong + MTF-aligned), `slLevel`/`tpLevel`, per-HTF blocks `htf1`/`htf2`
(composite/emaStack/stDir/rsi), `mtfComposite`, `mtfAligned` (±1 when all three
composites share a sign), `squeezeOn`, `volRatio`.

Signal-time semantics: read the last bar; with a `to` anchor the window ends at
the anchored moment. HTF values are forming-bar reads on the live/anchor bar.

Free-tier note: the 180-bar chart cap bounds HTF warmup depth (a 15m chart
sees ~45 1h bars); direction/alignment reads are robust, exact HTF composite
magnitudes can drift ~1–2% vs a dedicated chart.
