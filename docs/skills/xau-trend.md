# XAU-Trend — XAUUSD Trend Strategy

**Pine ID:** `PUB;a4e47455574243fe9731423c4ddb50ca`
**Workflow:** `xau-trend`
**Category:** Trend + Volatility

## Description

Dual EMA with Bollinger Bands for trend and volatility analysis. Detects EMA crossovers and Bollinger band positions to identify trend direction and potential breakout/reversal zones.

## Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `emaShort` | float | Short EMA value |
| `emaLong` | float | Long EMA value |
| `bollingerBasis` | float | Bollinger middle band |
| `bollingerUpper` | float | Bollinger upper band |
| `bollingerLower` | float | Bollinger lower band |
| `bandWidth` | float | Bollinger band width |
| `crossover` | string | EMA crossover direction |
| `bias` | string | `bullish`, `bearish`, or `neutral` |

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `emaShort` | `in_0` | int | 9 | **Short EMA period** |
| `emaLong` | `in_1` | int | 21 | **Long EMA period** |
| `bollingerPeriod` | `in_5` | int | 20 | **Bollinger period** |
| `bollingerMult` | `in_6` | float | 2.0 | **Bollinger multiplier** |

## Presets

| Preset | EMA Short | EMA Long | Use Case |
|--------|-----------|----------|----------|
| `scalping` | 5 | 13 | Fast entries |
| `default` | 9 | 21 | General purpose |
| `swing` | 21 | 55 | Swing/position |

## Key Inputs to Vary by Market

- **`emaShort` / `emaLong`**: 5/13 for crypto, 9/21 for forex, 12/26 for equities
- **`bollingerMult`**: 1.5-2.0 for crypto (volatile), 2.0-2.5 for forex (smoother)
