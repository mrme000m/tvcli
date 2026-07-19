# Liq Sweep — Institutional Liquidity Sweep

**Pine ID:** `PUB;b9372355c2e6483f952ca49a21d2ebbb`
**Workflow:** `liquidity-sweep`
**Category:** Liquidity

## Description

Detects institutional liquidity sweeps — when price breaks a swing high/low to grab liquidity before reversing. Identifies bull/bear sweeps with volume confirmation.

## Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `bullSweeps` | int | Count of bullish sweeps (swept lows) |
| `bearSweeps` | int | Count of bearish sweeps (swept highs) |
| `sweepDominance` | string | `bullish`, `bearish`, or `neutral` |
| `latestSweep` | string | `bullish`, `bearish`, or `none` |

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `swingLookback` | `in_0` | int | 20 | **Lookback for swing highs/lows** |
| `volumeMultiplier` | `in_1` | float | 1.5 | **Volume spike threshold** |
| `showLabels` | `in_2` | bool | true | Show sweep labels |

## Presets

| Preset | Lookback | Volume Mult | Use Case |
|--------|----------|-------------|----------|
| `scalping` | 10 | 1.2 | Fast sweep detection |
| `default` | 20 | 1.5 | General purpose |
| `swing` | 50 | 2.0 | Significant sweeps only |

## Key Inputs to Vary by Market

- **`swingLookback`**: 5-10 for crypto (frequent sweeps), 20-30 for forex, 50+ for swing
- **`volumeMultiplier`**: 1.2-1.5 for crypto (high volume), 2.0-3.0 for forex (lower volume)
