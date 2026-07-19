# ICT — ICT Auto-Validated SMC

**Pine ID:** `PUB;789a5c79bfe9443585da09e85ece73de`
**Workflow:** `ict-smc-structure`
**Category:** Market Structure

**Status:** Heavy script (80 inputs). Times out under free tier. Use `--signals --agent --json` with paid `TV_TIER`.

## Description

Institutional market structure analysis with auto-validated BOS/CHoCH, order blocks, fair value gaps, breaker blocks, inducement models, and confluence-scored trade signals. Comprehensive ICT/SMC framework.

## Inputs (Key)

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `swingLen` | `in_0` | int | 10 | **Swing detection length** |
| `internalLen` | `in_1` | int | 5 | Internal structure length |
| `showSwings` | `in_2` | bool | true | Show swing points |
| `showStructure` | `in_3` | bool | true | Show BOS/CHoCH |
| `useHTF` | `in_6` | bool | true | Use higher timeframe |
| `htfTimeframe` | `in_7` | timeframe | 240 | **HTF for confluence** |
| `showOB` | `in_10` | bool | true | Show order blocks |
| `showFVG` | `in_19` | bool | true | Show FVGs |
| `showBreakers` | `in_15` | bool | true | Show breaker blocks |
| `showOTE` | `in_49` | bool | true | Show optimal trade entry |
| `enableSignals` | `in_56` | bool | true | Enable signal scoring |
| `minSigScore` | `in_57` | int | 4 | **Minimum signal score** |

## Key Inputs to Vary by Market

- **`swingLen`**: 5-10 for crypto (fast structure), 10-20 for forex
- **`htfTimeframe`**: 240 (4h) for intraday, 1440 (1D) for swing
- **`minSigScore`**: 3-4 for more signals, 6-8 for higher quality only
