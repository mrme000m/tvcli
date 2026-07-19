# BSV — Buy/Sell Volume

**Pine ID:** `PUB;28a4da159ce246dab2cb6524c25f950f`
**Workflow:** `buying-selling-volume`
**Category:** Volume Analysis

## Description

Measures buying vs selling volume pressure using dual moving averages of volume. Compares buy-dominant bars against sell-dominant bars to determine consensus bias. Outputs background color consensus, dominance ratio, and recent MA crosses.

## Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `bgConsensus` | string | Background color consensus: `bullish`, `bearish`, or `neutral` |
| `buyDominant` | int | Count of bars where buy volume > sell volume |
| `sellDominant` | int | Count of bars where sell volume > buy volume |
| `dominanceRatio` | float | `(buy - sell) / total` — positive = bullish |
| `neutral` | int | Count of bars with no clear dominance |
| `recentCrosses` | int | Number of recent MA crosses |
| `totalBars` | int | Total bars analyzed |

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `lengthMA1` | `in_0` | int | 10 | **MA1 period for buy volume** — shorter = more responsive |
| `lengthMA2` | `in_1` | int | 10 | **MA2 period for sell volume** — shorter = more responsive |
| `maType` | `in_2` | string | `SMA` | Moving average type: `SMA` or `EMA` |

## Presets

| Preset | MA1 | MA2 | Type | Use Case |
|--------|-----|-----|------|----------|
| `scalping` | 9 | 21 | EMA | Fast scalp entries |
| `default` | 10 | 10 | SMA | General purpose |
| `swing` | 50 | 200 | SMA | Swing/position trading |

## Key Inputs to Vary by Market

- **`lengthMA1` / `lengthMA2`**: Shorter periods (5-10) for volatile crypto, longer (20-50) for stable forex/equities
- **`maType`**: EMA for faster reaction on crypto, SMA for smoother signals on forex
