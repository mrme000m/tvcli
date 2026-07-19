# Sniper — BS Buy & Sell Signals with EMA

**Pine ID:** `PUB;0287a71c10904118b75d4360a32c0579`
**Workflow:** `bs-buy-sell-ema`
**Category:** EMA + Signals

## Description

5-EMA system with buy/sell signal detection. Computes bias from EMA alignment (shortest > longest = bullish). Outputs support/resistance levels and take-profit targets.

## Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `score` | float | EMA separation score (0-5) |
| `ema1`-`ema5` | float | Individual EMA values |
| `buySignal` | bool | Buy signal triggered |
| `sellSignal` | bool | Sell signal triggered |
| `resistance` | float | Dynamic resistance level |
| `support` | float | Dynamic support level |

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `ema1Len` | `in_0` | int | 2 | **EMA 1 period** (fastest) |
| `ema2Len` | `in_1` | int | 4 | **EMA 2 period** |
| `ema3Len` | `in_2` | int | 6 | **EMA 3 period** |
| `ema4Len` | `in_3` | int | 8 | **EMA 4 period** |
| `ema5Len` | `in_4` | int | 10 | **EMA 5 period** (slowest) |

## Presets

| Preset | EMA Range | Use Case |
|--------|-----------|----------|
| `scalping` | 2-10 | Fast entries |
| `default` | 2-10 | General purpose |
| `swing` | 10-200 | Swing/position |

## Key Inputs to Vary by Market

- **All EMA lengths**: Scale proportionally — crypto uses 2-10, forex uses 5-20, equities/swing uses 10-200
- **Wider spread** (e.g., 5/50/200) = stronger trend confirmation, fewer signals
