# Golden — Golden Rule Strategy

**Pine ID:** `PUB;6daafb2cabe6419d98ae25229d2327f8`
**Workflow:** `golden-rule-strategy`
**Category:** Multi-TF Alignment

**Status:** Wrong PineID — Verdict field missing. Reports `no_data`.

## Description

Multi-timeframe alignment strategy checking weekly/daily/4H confluence. Outputs a Verdict (PASS/FAIL/BULLISH/BEARISH) based on structure, FVGs, and order block alignment.

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `showStructureInput` | `in_10` | bool | true | Show market structure |
| `showFairValueGapsInput` | `in_33` | bool | true | Show FVGs |
| `showInternalOrderBlocksInput` | `in_19` | bool | true | Show internal OBs |
| `swingsLengthInput` | `in_17` | int | 50 | **Swing detection length** |
