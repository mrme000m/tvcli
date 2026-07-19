# MTF — XAUUSD Multi-Timeframe Trend Dashboard

**Pine ID:** `PUB;d1ad30c0261f49f297357f8aa2a7854a`
**Workflow:** `xauusd-mtf-trend`
**Category:** Multi-TF

**Status:** XAUUSD-specific. Emits 0 periods on BTCUSD. Reports `no_data`.

## Description

Multi-timeframe trend dashboard showing M15/M30/H1/H4/D1 trend alignment. Uses EMA crossover and RSI to classify each timeframe as bullish/bearish. Aggregates into OverallBias.

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `show_M15` | `in_0` | bool | true | Show M15 timeframe |
| `show_M30` | `in_1` | bool | true | Show M30 timeframe |
| `show_H1` | `in_2` | bool | true | Show H1 timeframe |
| `show_H4` | `in_3` | bool | true | Show H4 timeframe |
| `show_D1` | `in_4` | bool | true | Show D1 timeframe |
| `fastLength` | `in_5` | int | 10 | **Fast EMA length** |
| `slowLength` | `in_6` | int | 20 | **Slow EMA length** |
| `rsiLength` | `in_7` | int | 14 | **RSI length** |

## Key Inputs to Vary by Market

- **`fastLength` / `slowLength`**: 5/13 for crypto, 10/20 for forex, 12/26 for equities
- **`rsiLength`**: 7 for crypto, 14 for forex/equities
