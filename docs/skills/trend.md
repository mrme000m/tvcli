# Trend — Self-Aware Trend System

**Pine ID:** `PUB;0f80bcf05d544d4c98fde06faab1c976`
**Workflow:** `self-aware-trend`
**Category:** Adaptive Trend

**Status:** Heavy script (78 inputs). Times out under free tier. Use `--signals --agent --json` with paid `TV_TIER`.

## Description

Adaptive trend system with character flip detection, asymmetric bands, and quality indexing. Self-adjusts to market regime (trending vs ranging). Uses TQI (Trend Quality Index) for signal filtering.

## Inputs (Key)

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `presetInput` | `in_0` | string | `Auto` | **Preset selector** — Auto/Default/Scalping/Swing/Crypto |
| `atrLenInput` | `in_1` | int | 13 | **ATR period** |
| `baseMultInput` | `in_2` | float | 2 | **ATR multiplier** |
| `sourceInput` | `in_3` | source | close | Price source |
| `useTqiInput` | `in_8` | bool | true | Use Trend Quality Index |
| `useCharFlipInput` | `in_15` | bool | true | Use character flip detection |
| `useAsymBandsInput` | `in_12` | bool | true | Use asymmetric bands |
| `useStructureInput` | `in_25` | bool | true | Use market structure |
| `useRsiInput` | `in_27` | bool | true | Use RSI filter |
| `useVolInput` | `in_32` | bool | true | Use volume filter |

## Presets

| Preset | Use Case |
|--------|----------|
| `Auto` | Automatically adapts to market |
| `Default` | General purpose |
| `Scalping` | Fast signals |
| `Swing` | Slower, higher quality |
| `Crypto` | Optimized for crypto volatility |

## Key Inputs to Vary by Market

- **`presetInput`**: Use `Crypto` for crypto, `Scalping` for forex scalping, `Swing` for position trading
- **`atrLenInput`**: 7-10 for crypto, 13-14 for forex, 20+ for swing
- **`baseMultInput`**: 1.5-2 for crypto, 2-3 for forex
