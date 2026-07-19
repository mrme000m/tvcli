# Quantum — EMA Ribbon [Krypt]

**Pine ID:** `PUB;GOYNhZP4X9VEbYA54MRIYU5FPvyr5IJB`
**Workflow:** `quantum-ribbon`
**Category:** Trend + Ribbon

## Description

8-layer EMA ribbon showing trend alignment. All EMAs ascending = bullish, all descending = bearish. Measures ribbon spread as trend strength. Useful for identifying trend regime and potential reversals.

## Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `alignment` | int | -8 to +8 — fully bearish to fully bullish |
| `spread` | float | Ribbon spread as percentage |
| `score` | float | Trend strength score (0-5) |
| `ma1`-`ma8` | float | Individual EMA values |

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `len1` | `in_0` | int | 5 | **EMA 1 period** (shortest) |
| `len2` | `in_1` | int | 10 | **EMA 2 period** |
| `len3` | `in_2` | int | 15 | **EMA 3 period** |
| `len4` | `in_3` | int | 20 | **EMA 4 period** |
| `len5` | `in_4` | int | 25 | **EMA 5 period** |
| `len6` | `in_5` | int | 30 | **EMA 6 period** |
| `len7` | `in_6` | int | 35 | **EMA 7 period** |
| `len8` | `in_7` | int | 40 | **EMA 8 period** (longest) |

## Presets

| Preset | EMA Range | Use Case |
|--------|-----------|----------|
| `scalping` | 3-20 | Fast ribbon |
| `default` | 5-40 | General purpose |
| `swing` | 20-200 | Slow, significant ribbon |

## Key Inputs to Vary by Market

- **All EMA lengths**: Scale proportionally — crypto uses 3-20, forex uses 5-40, equities/swing uses 20-200
- **Ribbon spread**: Tighter ribbon (lower spread) = consolidation, wider = trending
