# Gold Divergence — RSI Divergence Detector

**Pine ID:** `PUB;6fdd3132a2bf842ad40c0f74a1589a74`
**Workflow:** `gold-divergence`
**Category:** Momentum + Divergence

## Description

Detects bullish and bearish RSI divergences. Identifies when price makes new highs/lows but RSI doesn't confirm — a classic reversal signal. Counts divergence types and reports latest divergence type.

## Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `rsi` | float | Current RSI value |
| `divergenceBias` | string | `bullish`, `bearish`, or `neutral` from divergence count |
| `bullDivergences` | int | Count of bullish divergences |
| `bearDivergences` | int | Count of bearish divergences |
| `latestDivergence` | string | `bullish`, `bearish`, or `none` |

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `rsiLength` | `in_0` | int | 14 | **RSI period** — shorter = more signals |
| `showDivergence` | `in_5` | bool | true | Show divergence markers |

## Presets

| Preset | RSI Length | Use Case |
|--------|-----------|----------|
| `scalping` | 7 | Fast divergence detection |
| `default` | 14 | General purpose |
| `swing` | 21 | Slower, more reliable divergences |

## Key Inputs to Vary by Market

- **`rsiLength`**: 7 for crypto scalping (volatile), 14 for general, 21 for swing/forex
