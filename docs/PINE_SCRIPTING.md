# Analyzing the Market Better — Giving `tvcli` Agent-Optimized Pine Scripts

`tvcli` can run **any** Pine Script (indicator or strategy) on arbitrary symbols/timeframes
via `eval`, then extract the results into an **agent-ready envelope** with clean
`events` (buy/sell), `levels` (support/resistance/band), and a directional `bias`.

This guide explains *how* to craft Pine scripts so the CLI produces maximum
signal quality, which scripts are bundled, and the paywalled differences that
gate certain techniques.

---

## 1. The core workflow

```bash
# Any Pine script, from a file
./tvcli eval my_script.pine --signals --agent --json \
  --symbol BINANCE:BTCUSDT --tf 15m --bars 365

# Or inline
./tvcli eval --script '//@version=5
indicator("x", overlay=true)
plot(close)' --signals --agent --json
```

Flow: `compile (translate_light) → SaveNew (real PineID) → WS create_study →
capture periods + graphics + strategy report → delete temp script`.

## 2. How script output becomes agent data

The extractor (`pkg/pipeline/extract.go`) classifies each `plot()` line and maps it:

| Plot style / value shape | Classified as | Agent output |
|---|---|---|
| Continuous prices near the dominant price (VWAP, EMAs, S/R) | `ClassPrice` | **levels** (support/resistance/band) |
| Discrete `-1/0/1` (positive=active) | `ClassSignal` | **buy events** |
| Discrete `-1/0/1` (negative=active) | `ClassSignal` | **sell events** |
| Boolean count / regime field | `ClassSignal` | events too |
| Small integer palette (`0..7` colors) | `ClassStyle` | ignored |
| High-magnitude stable floats (`>2000`) | `ClassPrice` | levels |
| Everything else (scores, percentages) | `ClassMetric` | `last` values |
| Float composite scores | `ClassMetric` | `last` (no event/level) |

The **agent `bias`** is derived from the *count of buy vs sell events*.

**⇒ Craft scripts to emit both**: (a) price-valued plot lines for level detection,
and (b) a +1/-1 signal line (or two 0/1 lines) for buy/sell event detection.

### Graphics-only scripts and the generic topology analyzer

Many powerful indicators (order blocks, FVG, volume profile, SMC visuals) emit
**no plot columns** — everything lives in the drawing layer (`dwgboxes`,
`dwglines`, `dwglinefills`, `dwglabels`). The universal analyzer handles these
with a **two-layer generic design** (no per-script matchers needed):

- **Layer 1** (`pkg/pipeline/extract.go`): flat signal extraction — reads
  every draw type (boxes→S/R levels, lines→horizontal levels, labels→events,
  tables→grids, hhists→volume bins). Universal, zero script-specific code.
- **Layer 2** (`internal/agent/graphics_generic.go`): **topology-based**
  structural analysis — groups graphic elements by geometric topology:
  - Boxes sharing a left edge → volume-profile stacks (POC = widest, VAH/VAL
    = stack bounds)
  - Narrow boxes (1-3 bars) → FVG/gap zones
  - Wide boxes extending right → order-block zones
  - Boxes with dashed extended bounding lines → breaker blocks
  - Vertical lines → session/sweep markers
  - Horizontal dashed lines → liquidity levels
  - Labels grouped by text → buy/sell/BOS/CHOCH/etc.

This means **any** Pine script's graphics are analyzed by the same universal
topology rules, regardless of how they arrange boxes, lines, and labels. When
a new arrangement is observed, add a **topology rule** in
`buildBoxTopology`/`buildLineTopology` — not a per-script matcher.

## Indicator vs strategy separation

`tvcli` explicitly distinguishes the two script kinds in its output, so an agent
can read trade-driven results differently from plot-driven results.

- **Detected from** the Pine `metaInfo` schema (`strategy()` declaration /
  strategy-specific inputs) and confirmed by a received strategy report
  (`performance`/`trades`).
- **Exposed** as `Structure.kind` and `Structure.meta.scriptType`
  (`"strategy" | "indicator"`).
- **Strategies** additionally get:
  - a `Structure.strategy` summary (net profit, win rate, total/losing/winning
    trades, profit factor, drawdown, sharpe/sortino, per-trade list);
  - **trade-derived events** — each executed `strategy.entry` becomes a
    `buy`/`sell` event (a long entry → `buy`, short entry → `sell`), so the
    agent reacts to *actual* fills, not just plotted lines;
  - a **bias** taken from the *last executed trade side* (long → `long`,
    short → `short`) when trade history exists.
- **Indicators** get neither the `strategy` block nor trade events; their
  events/levels/bias come from plotted signal lines and price levels only.

Example strategy envelope (trimmed):

```json
{
  "kind": "strategy",
  "meta": { "scriptType": "strategy", "periodCount": 465 },
  "events": [{"Field": "trade_Long", "Kind": "buy", "Value": 63151.58}],
  "strategy": {
    "totalTrades": 245, "winRate": 26.9, "netProfit": -13309.17,
    "trades": [{"Side": "buy", "Entry": 71408.9, "Price": 71268.01, "Profit": -140.89}]
  }
}
```

## 3. Bundled agent-analysis scripts (`scripts/agent/`)

All verified to return data on BINANCE:BTCUSDT 15m.

| Script | What it gives an agent |
|---|---|
| `volatility_regime.pine` | ATR%, Bollinger width, squeeze/expansion, EMA-alignment trend, ATR stops, **26 buy/sell events**, band levels |
| `liquidity_map.pine` | VWAP, day high/low, EMA50/200, POC, swing S/R, liquidity sweeps — **10 S/R levels** |
| `composite_bias.pine` | Composite -100..+100 bias score, RSI, EMA/MACD trend, Bollinger position, ATR volatility, pivot S/R — **resistance/support levels** |
| `key_levels.pine` | Pivot-based S/R, volume POC, anchored VWAP, EMA50/200, BB bands, ATR stops — **all key levels** |
| `order_flow_delta.pine` | Buy/sell pressure %, delta MA, cumulative delta, imbalances, climax, divergence, churn |

```bash
./tvcli eval scripts/agent/volatility_regime.pine --signals --agent --json --symbol BINANCE:BTCUSDT --tf 15m
./tvcli eval scripts/agent/liquidity_map.pine     --signals --json     --symbol BINANCE:BTCUSDT --tf 15m
```

## 4. Multi-timeframe analysis

Two ways:

**A) Run the CLI across timeframes (works on ALL tiers):**
```bash
for tf in 5m 15m 1H 4H; do
  ./tvcli eval scripts/agent/composite_bias.pine --signals --agent --json --symbol BTCUSDT --tf $tf
done
```

**B) Use `request.security()` inside one script for a true MTF dashboard —**
this unlocks a single-run richer MTF picture:

```pine
h1_close = request.security(syminfo.tickerid, "60", close)
h1_ema   = request.security(syminfo.tickerid, "60", ta.ema(close, 21))
```

**⚠ The essential-tier unlock:** `request.security()` scripts silently return
`PeriodCount:0` on the **free** tier (20s calc timeout, 180 bars), but run fine
on **essential** (40s, 365 bars): the MTF dashboard went from **0 → 465 periods**.

## 5. Known crafting issues on `tvcli`

- **Silent no-data**: a script that compiles and loads but returns `PeriodCount:0`
  produces `Bias:""` and empty `events/levels` with no error. **Always check
  `PeriodCount` before trusting output.**
- **Discrete ±1 signal plots are unreliable for data delivery**: scripts whose
  *only* informative output is a `0/+1`/`-1` signal line (e.g. a bare crossover)
  returned `PeriodCount:0` on BOTH free and essential in testing, even with a
  single price plot alongside. Prefer scripts with **multiple continuous /
  price-scale plots** and let event detection ride on those fields.
- **`plot.style_histogram` on overlay charts** and boolean-heavy scripts are the
  likeliest to no-op; if a script returns no data, add a continuous price-scale
  line (VWAP, EMA) and re-test.
- Keep scripts under the tier's calc budget: free = ~180 bars / 20s, essential =
  ~365 bars / 40s. Complex scripts (heavy `request.security`, arrays, DMI) need
  the paid tier.

## 6. Tier comparison (free vs essential)

| Capability | free | essential |
|---|---|---|
| Charts | 1 | 2 |
| Indicators / chart | 2 | 5 |
| WS connections | 2 | 10 |
| Historical bars | **180** | **365** |
| Calc timeout | **20s** | **40s** |
| `request.security` MTF | ❌ silent 0 | ✅ 465 periods |

Set tier in `.env`:
```env
TV_TIER=essential
```

## 7. Verifying a freshly crafted script

Run with `--signals --agent --json` and inspect:
- `Structure.events` — buy/sell signals found
- `Structure.levels` — support/resistance/band levels found
- `Market.Bias` — directional bias (long/short/neutral)
- `Meta.PeriodCount` — must be `> 0`, else the script produced no data

At least one of events/levels should be non-empty for a useful script.
