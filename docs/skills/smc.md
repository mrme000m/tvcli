# SMC — Smart Money Concepts [LuxAlgo]

**Pine ID:** `PUB;6daafb2cabe6419d98ae25229d2327f8`
**Workflow:** `smart-money-concepts`
**Category:** Market Structure

## Description

Institutional market structure analysis detecting BOS (Break of Structure), CHoCH (Change of Character), Fair Value Gaps, Order Blocks, and Equal Highs/Lows. Counts bullish/bearish variants across bars.

## Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `bosCount` | int | Total BOS events |
| `chochCount` | int | Total CHoCH events |
| `fvgCount` | int | Total FVG events |
| `obCount` | int | Total Order Block events |
| `bullishBOS` | int | Bullish BOS count |
| `bearishBOS` | int | Bearish BOS count |
| `bullishCHoCH` | int | Bullish CHoCH count |
| `bearishCHoCH` | int | Bearish CHoCH count |
| `bullishFVG` | int | Bullish FVG count |
| `bearishFVG` | int | Bearish FVG count |
| `bullishOB` | int | Bullish OB count |
| `bearishOB` | int | Bearish OB count |

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `showStructureInput` | `in_10` | bool | true | Show BOS/CHoCH |
| `showSwingBullInput` | `in_11` | string | `ALL` | Show bullish swings |
| `showSwingBearInput` | `in_13` | string | `ALL` | Show bearish swings |
| `showInternalOrderBlocksInput` | `in_19` | bool | true | Show internal OBs |
| `showSwingOrderBlocksInput` | `in_21` | bool | false | Show swing OBs |
| `showFairValueGapsInput` | `in_33` | bool | true | Show FVGs |
| `fairValueGapsThresholdInput` | `in_34` | bool | true | FVG threshold filter |
| `showEqualHighsLowsInput` | `in_29` | bool | true | Show EQH/EQL |
| `swingsLengthInput` | `in_17` | int | 50 | **Swing detection length** |

## Key Inputs to Vary by Market

- **`swingsLengthInput`**: 10-20 for crypto (fast structure), 30-50 for forex, 50-100 for swing
- **`showSwingOrderBlocksInput`**: Enable for swing trading, disable for scalping
