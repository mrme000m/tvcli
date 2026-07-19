# Volume Profile Skill

Based on the YouTube video **"The Secret To Using The Volume Profile"** and the public TradingView Pine script that implements it.

## What the video teaches

The speaker uses TradingView's **Fixed Range Volume Profile** to find the price levels where the most volume traded. The method is:

1. Add the **Fixed Range Volume Profile** indicator.
2. Go into the indicator settings and **enable VAH** (Volume Area High) and **VAL** (Volume Area Low).
3. Keep the **Value Area** at the default **70%** of traded volume.
4. Manually draw the tool from a **recent swing low to a recent swing high**.
5. Preferred timeframe: **weekly** for a strong institutional bias.

### Key levels

| Level | Meaning | Trading idea |
|---|---|---|
| **POC** | Point Of Control — price with the highest traded volume | Price tends to revert to the POC |
| **VAH** | Value Area High — top of the 70% volume zone | Above VAH = "expensive" → look for shorts back to POC/VAL |
| **VAL** | Value Area Low — bottom of the 70% volume zone | Below VAL = "cheap" → look for longs back to POC/VAH |
| **HVN** | High Volume Node — price tends to consolidate | Support/resistance congestion |
| **LVN** | Low Volume Node — price moves through quickly | Gaps that price can spike through |

## Implemented skill: `vp`

File: [`internal/skill/parsers/vp.go`](../internal/skill/parsers/vp.go)

Pine script: **`PUB;a4e251b831084685afecaa9192f2a3c5`** — *Fixed Range Volume Profile Zones (with Dynamic Percentile Buffers)* by RWCS_LTD

This script exposes the levels as regular Pine `plot` values (`POC`, `VAH`, `VAL`, `Max_Price`, `Min_Price`, `Above_VAH_Buffer`, `Below_VAL_Buffer`) instead of only drawing graphics. It also embeds the underlying chart OHLC, so the parser can read the current close and produce a directional bias plus mean-reversion / breakout opportunities.

### CLI usage

```bash
# Weekly bias (video's preferred timeframe)
./tvcli vp --symbol BTCUSDT --tf 1W --bars 52 --preset weekly --agent --json

# Intraday profile
./tvcli vp --symbol BTCUSDT --tf 1h --bars 48 --preset intraday --agent --json

# Default run (1h lookback)
./tvcli vp --symbol BTCUSDT --tf 1h --bars 50
```

### Inputs mapped from the script

The script has four inputs that control the fixed range and percentile buffers:

| CLI flag | Pine input | Default | What it controls |
|---|---|---|---|
| `--lookback` | `in_0` | `30` | Bars back over which to build the profile |
| `--percentile` | `in_1` | `30` | Percentile window used inside the value-area calculation |
| `--upper-buffer` | `in_2` | `95` | Upper dynamic percentile buffer (triggers `Above_VAH_Buffer`) |
| `--lower-buffer` | `in_3` | `5` | Lower dynamic percentile buffer (triggers `Below_VAL_Buffer`) |

### Presets

```bash
./tvcli vp --preset weekly   # lookback=52,  percentile=30
./tvcli vp --preset daily    # lookback=30,  percentile=30
./tvcli vp --preset intraday # lookback=24,  percentile=30
./tvcli vp --preset scalping # lookback=12,  percentile=30
```

### Example output

```bash
./tvcli vp --symbol BTCUSDT --tf 1h --bars 50 --agent --json
```

```json
{
  "market": {
    "lastPrice": 64720.17,
    "bias": "bullish"
  },
  "structure": {
    "poc": 64141.09,
    "vah": 64818.82,
    "val": 63925.44,
    "maxPrice": 64834.22,
    "minPrice": 63910.04,
    "rangeMid": 64372.13,
    "aboveVAHBuffer": false,
    "belowVALBuffer": false,
    "bias": "bullish"
  },
  "opportunities": [
    {
      "rank": 2,
      "setup": "vp_levels",
      "direction": "bullish",
      "confidence": "MED",
      "confluenceScore": 0.45,
      "rationale": "POC=64141.09 VAH=64818.82 VAL=63925.44 range[63910.04-64834.22]"
    }
  ],
  "conformance": {
    "agenticScore": 0.70
  }
}
```

### Trading logic

Price relative to the value area drives the bias and opportunities:

- Price **below VAL** (or `Below_VAL_Buffer` triggered) → long mean-reversion to POC → VAH.
- Price **above VAH** (or `Above_VAH_Buffer` triggered) → short mean-reversion to POC → VAL.
- Price **inside** the value area → neutral, watch for a move toward POC or a breakout.

## Why this script was chosen

The original video script (`PUB;aea729456b7a44e09661b70ce9e4e987`) is graphics-only and has no numeric output. To use it in the CLI we had to reconstruct the profile by decoding box widths and labels, which is brittle and could not receive the current market price.

The new script (`PUB;a4e251b831084685afecaa9192f2a3c5`) is better because:

1. **Numeric levels** — `POC`, `VAH`, `VAL` are plain period fields.
2. **Includes OHLC** — the current close is available as `plotcandle_0_ohlc_close`, so the parser can bias itself.
3. **Buffers** — `Above_VAH_Buffer` / `Below_VAL_Buffer` give explicit breakout/mean-reversion signals.
4. **More robust** — no graphic parsing, no manual label matching.

## Alternative candidates

| Pine ID | Title | Why it is interesting |
|---|---|---|
| `PUB;c500dd16982849b48caf2123c919c81c` | Anchored Volume Profile Confluence — POC, Value Area, HVN/LVN & Value Migration | Numeric `EXP_POC`, `EXP_VAH`, `EXP_VAL`, plus VWAP and value-migration signals |
| `PUB;TFpVVPsMEJV84zM8wHIBQVCZ79v6beC8` | Volume Profile Free Ultra SLI by RRB | Very popular, but data arrives as per-price volume arrays; needs custom reconstruction |
| `PUB;a7xsrJkK2RpZFIR18wBX02FVlE3wpHW2` | Volume Profile Free Pro by RRB | Similar to above; requires reconstruction |
