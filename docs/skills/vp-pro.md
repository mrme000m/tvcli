# VP Pro — Fixed + Anchored Volume Profile

**Pine ID:** `USER;63108df4f6a04d7bb426bc74cab3b4ee`
**Source:** `vp-pro.pine` (repo root) — consolidated fixed-range + anchored volume profile
**Workflow:** `volume-profile`
**Category:** Volume + Structure

## Description

Consolidated volume profile engine: fixed-range (rolling lookback) or anchored
(profile from an anchor bar offset) in one script. Bins each candle's volume
(body / upper wick / lower wick) into price rows, splits up vs down volume,
draws the profile, and surfaces POC (Point of Control), VAH (Value Area High)
and VAL (Value Area Low). Adapted from LonesomeTheBlue "Volume Profile / Fixed
Range" (MPL-2.0) with anchored semantics from DGT "Anchored Volume Profile".

Levels come from the graphic layer: the profile is drawn as boxes/lines/labels,
and the parser reads POC/VAH/VAL from the label layer (lines as fallback)
because the numeric POC/VAH/VAL plots are gated on the last bar. An ungated,
non-displayed `Close` plot carries the reference price, so the parser computes
real bias and distance-to-level signals without a separate OHLCV fetch.

## Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `poc` | float | Point of Control — highest-volume price |
| `vah` | float | Value Area High |
| `val` | float | Value Area Low |
| `valueAreaWidth` | float | `vah - val` (omitted when VAH/VAL unavailable) |
| `price` | float | Last closed-bar close from the `Close` plot |
| `pricePosition` | string | `above_value_area` / `inside_value_area` / `below_value_area` |
| `distToPOCPct` / `distToVAHPct` / `distToVALPct` | float | Signed % distance from price to each level |
| `bias` | string | `bullish` above VAH, `bearish` below VAL, else `neutral` |

`market.lastPrice` in the skill envelope is back-filled from OHLCV (real close).
In the generic `--signals` envelope it comes from the `Close` plot. Status is
`partial` (with warnings) when display toggles remove some levels from the
graphic layer; opportunities are only emitted for levels that actually parsed.

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `mode` | `in_0` | string | `Fixed Range` | `Fixed Range` or `Anchored` |
| `numBars` | `in_1` | int | 150 | Lookback bars (fixed-range mode) |
| `anchorOffset` | `in_2` | int | 60 | Anchor bars back (anchored mode) |
| `rows` | `in_3` | int | 24 | Price rows / resolution |
| `valueAreaPct` | `in_4` | float | 70 | Value-area volume % |

> **Lookback vs `--bars`:** the script clamps the lookback to available history
> at runtime (`min(lookback, bar_index + 1)`). Free-tier runs fetch ≤280 bars,
> so a `numBars`/`anchorOffset` larger than the fetched range silently profiles
> only the fetched bars — keep `--bars` ≥ your lookback for full coverage.

## Presets

| Preset | Mode | Lookback | Rows | Value Area |
|--------|------|----------|------|------------|
| `fixed` | Fixed Range | 150 | 24 | 70% |
| `anchored` | Anchored | 100 | 40 | 68% |
| `scalping` | Fixed Range | 60 | 20 | 70% |

## Key Inputs to Vary by Market

- **`mode`**: `Anchored` to anchor the profile to a fixed bar offset (swing/event
  anchor); `Fixed Range` for a rolling window.
- **`rows`**: more rows = finer price resolution (costs more boxes; free tier caps
  at 2 studies).
- **`valueAreaPct`**: 68–70% is standard; raise for a wider value area.

## Usage

```bash
./tvcli vp-pro --symbol BINANCE:BTCUSDT --tf 1H --agent --json
./tvcli vp-pro --symbol OANDA:XAUUSD --tf 1H --preset anchored --agent --json
./tvcli vp-pro --symbol BTCUSDT --tf 1H --input in_3=40 --agent --json
```
