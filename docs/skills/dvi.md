# DVI — Delta Volume Intensity

**Pine ID:** `PUB;bdd3bc54cf9f4dc6b42e6b2879b4eed2`
**Workflow:** `trend-following-sr-break`
**Category:** Volume + Trend

## Description

Combines ATR-based volatility measurement with ROC-based momentum to detect trend direction. Identifies support/resistance levels and emits trend alerts (Uptrend, Downtrend, Sideways). Useful for detecting trend changes and measuring momentum strength.

## Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `trend` | int | 1 = uptrend, -1 = downtrend, 0 = sideways |
| `volatility` | float | ATR-based volatility reading |
| `momentum` | float | Rate of change momentum |
| `support` | float | Dynamic support level |
| `resistance` | float | Dynamic resistance level |
| `sideways` | int | Sideways alert flag |

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `length_volatility` | `in_0` | int | 14 | **ATR period for volatility** — higher = smoother |
| `length_momentum` | `in_1` | int | 14 | **ROC period for momentum** — higher = slower reaction |
| `lookback_sr` | `in_2` | int | 7 | **Support/resistance lookback** — higher = more significant levels |

## Key Inputs to Vary by Market

- **`length_volatility`**: Use 7-10 for crypto (fast moves), 14-21 for forex, 20+ for equities
- **`length_momentum`**: Match to your timeframe — 7 for 5m, 14 for 1h, 21 for 4h+
- **`lookback_sr`**: Lower (5-7) for scalping, higher (15-20) for swing trading
