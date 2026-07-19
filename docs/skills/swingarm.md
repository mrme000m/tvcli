# Swingarm — SwingArm ATR Trend

**Pine ID:** `PUB;GdkmXaTINI8knwuCrctQD1pB5dFaRnyr`
**Workflow:** `swingarm-atr-trend`
**Category:** Trend + Risk Management

## Description

ATR-based trailing stop system with Fibonacci entry levels. Detects trend direction from trailing stop position and provides Fibonacci-based entry zones for pullback trades.

## Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `trailStop` | float | ATR trailing stop level |
| `extremum` | float | Current swing extremum |
| `fib1`-`fib3` | float | Fibonacci retracement levels |
| `bias` | string | `bullish`, `bearish`, or `neutral` |

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `trailType` | `in_0` | string | `modified` | Trailing stop type |
| `ATRPeriod` | `in_1` | int | 28 | **ATR period** — volatility measurement |
| `ATRFactor` | `in_2` | int | 5 | **ATR multiplier** — trail distance |
| `show_fib_entries` | `in_3` | bool | true | Show Fibonacci entries |

## Key Inputs to Vary by Market

- **`ATRPeriod`**: 10-14 for crypto, 14-28 for forex, 28+ for swing
- **`ATRFactor`**: 2-3 for crypto (tight), 3-5 for forex, 5-8 for swing (wide)
