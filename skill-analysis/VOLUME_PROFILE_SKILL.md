# Volume Profile Skill

Based on the YouTube video **"The Secret To Using The Volume Profile"** and the public TradingView Pine scripts that implement it.

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

Pine script: **`PUB;aea729456b7a44e09661b70ce9e4e987`** (Volume Profile / Fixed Range by LonesomeTheBlue)

Because this indicator only draws graphic objects (no numeric `periods[]`), the skill rebuilds the profile from the `dwgboxes` in the raw response. Each box's `x2-x1` encodes the relative volume and its `y1-y2` band encodes the price level.

### CLI usage

```bash
# Weekly bias (video's preferred timeframe)
./tvcli vp --symbol BTCUSDT --tf 1W --bars 52 --preset weekly --agent --json

# Intraday profile
./tvcli vp --symbol BTCUSDT --tf 1h --bars 48 --length 48 --agent --json

# Default run
./tvcli vp --symbol BTCUSDT --tf 1h --bars 50
```

### Inputs mapped from the script

| CLI flag | Pine input | Default | What it controls |
|---|---|---|---|
| `--rows` | `in_0` | `150` | Histogram rows (price granularity) |
| `--length` | `in_1` | `24` | Lookback bars for the fixed range |
| `--value-area` | `in_2` | `70` | Value-area percentage (`70` = 70%) |
| `--show-poc` | `in_9` | `true` | Show the POC label/line |

### Presets

```bash
./tvcli vp --preset weekly   # rows=150, length=52
./tvcli vp --preset daily    # rows=150, length=30
./tvcli vp --preset intraday # rows=100, length=24
./tvcli vp --preset scalping # rows=100, length=12
```

### Example output

```json
{
  "structure": {
    "poc": 64734.89,
    "vah": 65049.47,
    "val": 62847.37,
    "valueArea": 70,
    "hvn": [...],
    "lvn": [...]
  },
  "opportunities": [
    {
      "setup": "vp_levels",
      "rationale": "POC=64734.89 VAH=65049.47 VAL=62847.37 HVN=8 LVN=8"
    }
  ],
  "conformance": { "agenticScore": 0.75 }
}
```

### Known limitation

The `PUB;aea...` script has no numeric price output, so the skill **cannot receive the current market price** and therefore cannot automatically bias itself as bullish/bearish/oversold/overbought. It always reports the structural levels; you compare them to the current price yourself.

## Better volume-profile script (recommended)

Search surfaced a script that exposes **numeric POC, VAH, and VAL** fields directly, which makes it much easier to consume than parsing graphics:

- **Pine ID:** `PUB;a4e251b831084685afecaa9192f2a3c5`
- **Title:** *Fixed Range Volume Profile Zones (with Dynamic Percentile Buffers)*
- **Author:** RWCS_LTD

It emits clean period fields named `POC`, `VAH`, `VAL`, `Max_Price`, `Min_Price`, `Above_VAH_Buffer`, and `Below_VAL_Buffer`.

### Quick test

```bash
./tvcli run "PUB;a4e251b831084685afecaa9192f2a3c5" \
  --symbol BTCUSDT --tf 1h --bars 50 --signals --json
```

Sample values (BTCUSDT, 1h):

```text
POC  = 64141.09
VAH  = 64818.82
VAL  = 63925.44
Max  = 64834.22
Min  = 63910.04
```

### Why it is better

1. No graphic parsing — the levels are regular Pine `plot` values.
2. `periods[]` contain 150+ bars, so generic signal extraction works out of the box.
3. Built-in buffers (`Above_VAH_Buffer`, `Below_VAL_Buffer`) can be used as breakout/mean-reversion triggers.
4. Less fragile than parsing box widths and labels.

### Other candidates found

| Pine ID | Title | Why it is interesting |
|---|---|---|
| `PUB;c500dd16982849b48caf2123c919c81c` | Anchored Volume Profile Confluence — POC, Value Area, HVN/LVN & Value Migration | Also numeric; emits `EXP_POC`, `EXP_VAH`, `EXP_VAL`, plus VWAP and migration signals |
| `PUB;TFpVVPsMEJV84zM8wHIBQVCZ79v6beC8` | Volume Profile Free Ultra SLI by RRB | Very popular, but data arrives as per-price volume arrays; needs custom reconstruction |
| `PUB;a7xsrJkK2RpZFIR18wBX02FVlE3wpHW2` | Volume Profile Free Pro by RRB | Similar to above; requires reconstruction |

## Future work

The current `vp` skill could be upgraded to use the numeric `PUB;a4e251b831084685afecaa9192f2a3c5` script and a much smaller parser that simply reads `POC`, `VAH`, `VAL` from the last bar and adds mean-reversion/breakout opportunities using the built-in buffer fields.
