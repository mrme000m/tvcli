# VP — Volume Profile

**Pine ID:** `PUB;a4e251b831084685afecaa9192f2a3c5`
**Workflow:** `volume-profile`
**Category:** Volume + Structure

## Description

Volume profile analysis with POC (Point of Control), VAH (Value Area High), and VAL (Value Area Low). Identifies where most volume was traded and whether price is above/below value area.

## Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `poc` | float | Point of Control — highest volume price |
| `vah` | float | Value Area High |
| `val` | float | Value Area Low |
| `bias` | string | `bullish` (above VAH), `bearish` (below VAL), `neutral` |
| `aboveVAHBuffer` | bool | Price above VAH with buffer |
| `belowVALBuffer` | bool | Price below VAL with buffer |
| `maxPrice` | float | Maximum price in range |
| `minPrice` | float | Minimum price in range |

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `lookback` | `in_0` | int | 30 | **Lookback period** — bars for profile |
| `percentile` | `in_1` | int | 30 | **Value area percentile** |
| `upperBuffer` | `in_2` | float | 95 | Upper buffer percentage |
| `lowerBuffer` | `in_3` | float | 5 | Lower buffer percentage |

## Presets

| Preset | Lookback | Use Case |
|--------|----------|----------|
| `weekly` | 52 | Weekly profile |
| `daily` | 30 | Daily profile |
| `intraday` | 24 | Intraday profile |
| `scalping` | 12 | Fast profile |

## Key Inputs to Vary by Market

- **`lookback`**: 12-24 for intraday, 30 for daily, 52 for weekly — scale to your timeframe
- **`percentile`**: 30 is standard (70% of volume), adjust for wider/narrower value areas
