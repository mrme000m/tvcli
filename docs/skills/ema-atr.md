# EMA-ATR — EMA + ATR Pro Engine

**Pine ID:** `PUB;7d5f8755ab67400899ef73a9898471e4`
**Workflow:** `ema-atr-structure`
**Category:** Trend + Risk Management

## Description

Dual EMA system with ATR-based trailing stops. Detects buy/sell signals from EMA crossovers confirmed by ATR volatility. Outputs trailing stop levels, reentry signals, and bias from EMA alignment.

## Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `plot0` | float | EMA trail stop line (primary signal) |
| `plot2` | float | Secondary EMA line |
| `bias` | string | `bullish`, `bearish`, or `neutral` from EMA alignment |
| `buySignal` | bool | Buy signal triggered |
| `sellSignal` | bool | Sell signal triggered |
| `buyReentry` | bool | Reentry buy signal |
| `sellReentry` | bool | Reentry sell signal |

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `ema2Len` | `in_0` | int | 20 | **Fast EMA length** — primary trend filter |
| `ema3Len` | `in_1` | int | 50 | **Slow EMA length** — secondary trend filter |
| `useEMA2` | `in_2` | bool | true | Enable fast EMA |
| `useEMA3` | `in_3` | bool | false | Enable slow EMA |
| `pivotLen` | `in_4` | int | 1 | Pivot detection length |
| `atrLen` | `in_5` | int | 7 | **ATR period** — volatility sensitivity |
| `atrMult` | `in_6` | float | 1.4 | **ATR multiplier** — wider = more room |
| `confirmClose` | `in_7` | bool | true | Require candle close confirmation |
| `fastMode` | `in_8` | bool | false | Faster signal generation |
| `enableReentry` | `in_9` | bool | false | Enable reentry signals |

## Key Inputs to Vary by Market

- **`ema2Len` / `ema3Len`**: 9/21 for crypto scalping, 20/50 for general, 50/200 for swing
- **`atrLen`**: 7-10 for crypto (volatile), 14-20 for forex (smoother)
- **`atrMult`**: 1.0-1.5 for tight stops (crypto), 2.0-3.0 for wide stops (forex)
