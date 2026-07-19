# SR Breaks — Support/Resistance Breaks

**Pine ID:** `PUB;NXS6SoOdr880Hrvh9vA36UcAjC14bOkc`
**Workflow:** `support-resistance-breaks`
**Category:** Structure

## Description

Detects pivot-based support and resistance level breaks. Measures break intensity and reports the last break direction and how many bars ago it occurred.

## Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `support` | float | Current support level |
| `resistance` | float | Current resistance level |
| `lastBreak` | string | `bullish` or `bearish` |
| `breakBarsAgo` | int | Bars since last break |
| `bias` | string | `bullish`, `bearish`, or `neutral` |

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `showBreaks` | `in_0` | bool | true | Show break markers |
| `leftBars` | `in_1` | int | 15 | **Left lookback for pivot** |
| `rightBars` | `in_2` | int | 15 | **Right lookback for pivot** |
| `volumeThreshold` | `in_3` | int | 20 | Volume threshold for confirmation |

## Key Inputs to Vary by Market

- **`leftBars` / `rightBars`**: 5-10 for crypto (fast pivots), 15-20 for forex, 20-30 for swing
- **`volumeThreshold`**: Lower for crypto (high volume), higher for forex
