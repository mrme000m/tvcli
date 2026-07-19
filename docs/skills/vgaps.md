# VGaps — Volume Gaps & Imbalances

**Pine ID:** `PUB;ff1a0136336340f38e908eeb12ea33aa`
**Workflow:** `trend-following-gap-rejection`
**Category:** Volume + Gaps

**Status:** Server-side timeout (heavy script). Use `--signals --agent --json` with paid `TV_TIER`.

## Description

Detects zero-volume voids and order flow imbalances. Identifies volume gaps where no trading occurred, which often act as magnets for price. Analyzes delta volume for institutional order flow.

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `prd` | `in_0` | int | 200 | **Lookback period** — higher = more gaps |
| `rows` | `in_1` | int | 50 | **Profile rows** — resolution of volume profile |
| `src` | `in_2` | source | hlc3 | Price source |
| `width` | `in_3` | int | 100 | Display width |
| `sum_sections` | `in_7` | int | 20 | Summary sections |
| `sum_panel_w` | `in_8` | int | 40 | Summary panel width |
| `sum_gap_x` | `in_9` | int | 4 | Gap X offset |
| `delta_min_frac` | `in_15` | float | 0.2 | **Minimum delta fraction** |

## Presets

| Preset | PRD | Rows | Use Case |
|--------|-----|------|----------|
| `scalping` | 100 | 30 | Fast gap detection |
| `default` | 200 | 50 | General purpose |
| `swing` | 400 | 80 | Major gaps only |

## Key Inputs to Vary by Market

- **`prd`**: 100 for crypto (frequent gaps), 200-300 for forex, 400+ for swing
- **`rows`**: 30-50 for faster computation, 80-100 for more detail
- **`delta_min_frac`**: 0.1-0.2 for crypto, 0.2-0.3 for forex
