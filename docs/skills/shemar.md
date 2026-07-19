# Shemar — SHEMAR HMA ST + SMC Confidence Filter

**Pine ID:** `PUB;70f6e4e05f9c439c9d1f8fe26019357e`
**Workflow:** `shemar-smc-confidence`
**Category:** Trend + Confidence

## Description

Combines HMA (Hull Moving Average) with Supertrend and kernel convergence for high-confidence filtered signals. Uses squeeze detection and confidence scoring to filter low-quality signals.

## Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `buySignal` | bool | Buy signal triggered |
| `sellSignal` | bool | Sell signal triggered |
| `buyCount` | int | Count of buy signals in window |
| `sellCount` | int | Count of sell signals in window |
| `confidence` | float | Signal confidence score |

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `hmaLength` | `in_0` | int | 50 | **HMA period** — primary trend |
| `atrPeriod` | `in_1` | int | 10 | **ATR period** — volatility |
| `factor` | `in_2` | int | 3 | ATR multiplier for Supertrend |
| `enableShorts` | `in_3` | bool | true | Enable short signals |
| `useStopEntry` | `in_4` | bool | true | Use stop-entry logic |
| `htfPeriod` | `in_6` | int | 50 | **HTF trend filter** |
| `sqzLength` | `in_7` | int | 20 | Squeeze detection length |
| `sqzMult` | `in_8` | int | 2 | Squeeze multiplier |
| `kernelPeriod` | `in_13` | int | 30 | **Kernel smoothing period** |
| `confidenceThresh` | `in_14` | int | 30 | **Minimum confidence threshold** |

## Key Inputs to Vary by Market

- **`hmaLength`**: 20-30 for crypto, 50 for general, 100 for swing
- **`atrPeriod`**: 7-10 for crypto, 14 for forex
- **`factor`**: 2-3 for crypto (volatile), 3-5 for forex (smoother)
- **`confidenceThresh`**: 20-30 for more signals, 40-50 for higher quality
