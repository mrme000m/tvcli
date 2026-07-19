# Order Flow — Volume Spike Strategy

**Pine ID:** `PUB;7uP2LNPDc8I150lUzs3Aqa5ju9usF0ZN`
**Workflow:** `order-flow`
**Category:** Volume + Order Flow

## Description

Detects volume spikes that exceed a multiple of the volume moving average. Classifies spikes as buy or sell based on candle direction. Identifies institutional order flow.

## Output Fields

| Field | Type | Description |
|-------|------|-------------|
| `bullSpikes` | int | Count of bullish volume spikes |
| `bearSpikes` | int | Count of bearish volume spikes |
| `spikeDominance` | string | `bullish`, `bearish`, or `neutral` |
| `latestSpike` | string | `bullish`, `bearish`, or `none` |
| `totalSpikes` | int | Total spike count |

## Inputs

| Input | TV ID | Type | Default | Description |
|-------|-------|------|---------|-------------|
| `vmaLength` | `in_0` | int | 20 | **Volume MA period** — baseline for spike detection |
| `volumeMultiplier` | `in_1` | int | 500 | **Spike threshold** — multiplier of volume MA |
| `coinMaLength` | `in_2` | int | 5 | Coin/candle MA length |
| `showSells` | `in_3` | bool | true | Show sell signals |

## Presets

| Preset | VMA | Multiplier | Use Case |
|--------|-----|------------|----------|
| `scalping` | 10 | 300 | More frequent spikes |
| `default` | 20 | 500 | General purpose |
| `swing` | 50 | 700 | Major spikes only |

## Key Inputs to Vary by Market

- **`volumeMultiplier`**: 200-300 for crypto (high volume), 500-700 for forex, 300-500 for equities
- **`vmaLength`**: 10-15 for fast markets, 20-30 for slower markets
