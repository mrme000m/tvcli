# UST — Ultra Sensitive SuperTrend

**Pine ID:** `PUB;fc33f2d98699414a8585923116dbd959`
**Workflow:** `ultra-sensitive-supertrend`
**Category:** Trend

## Description

Dual SuperTrend system with Heiken Ashi smoothing. Uses two SuperTrend lines with different sensitivities to detect trend alignment. When both agree, the trend is confirmed.

## Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `st1` | float | SuperTrend 1 line price |
| `st2` | float | SuperTrend 2 line price |
| `aligned` | bool | Both SuperTrends agree |
| `background` | string | `BULLISH` or `BEARISH` |
| `buySignals` | int | Count of buy signals |
| `sellSignals` | int | Count of sell signals |
| `combined` | string | `BULLISH`, `BEARISH`, or `MIXED` |

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `atrPeriod1` | `in_0` | int | 10 | **ST1 ATR period** (faster) |
| `multiplier1` | `in_1` | float | 1.0 | **ST1 multiplier** (tighter) |
| `atrPeriod2` | `in_2` | int | 5 | **ST2 ATR period** (slower) |
| `multiplier2` | `in_3` | float | 0.5 | **ST2 multiplier** (wider) |
| `useHeikenAshi` | `in_4` | bool | true | Use Heiken Ashi smoothing |
| `showLabels` | `in_5` | bool | true | Show signal labels |
| `showBG` | `in_6` | bool | true | Show background color |

## Key Inputs to Vary by Market

- **`atrPeriod1` / `multiplier1`**: 7/0.8 for crypto, 10/1.0 for general, 14/1.5 for swing
- **`atrPeriod2` / `multiplier2`**: 5/0.5 for fast confirmation, 10/1.0 for slower
- **`useHeikenAshi`**: Enable for noisy markets (crypto), disable for cleaner markets (forex)
