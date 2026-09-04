# WunderTrading strategy playbook

The operating model: **you (the agent) are the strategy brain; WunderTrading
is the execution layer.** WunderTrading exposes strategy primitives — classic
or DCA entries, take-profit ladders, stop-loss, break-even move, trailing
stop, swing flip — and the exchange account; it does not decide *when* or
*what*. This playbook covers: (1) picking a token, (2) reading its market
conditions, (3) mapping conditions → strategy config, (4) proving a strategy
is *reliable* before scaling it, (5) monitoring and adapting.

## 1. Token selection

Hard constraints first, edge second:

1. **Account constraints** — `get_api_profiles` (MCP) or
   `GET /open_api/api_profiles` (REST): which exchanges have `status:
   "active"` and balance? Only active profiles can trade. `no_assets` = fund
   first. `demo: true` profiles are paper accounts — perfect for validation
   with zero risk.
2. **Tradability** — `get_exchange_markets` for the exchange: confirm the
   market exists, note `currencies` (quote = USDT/USDC), and compute
   **spread% = (ask − bid)/last**. Skip anything above ~0.1% spread for
   entries; majors (BTC/ETH/SOL) are usually ~0.00–0.03%.
3. **Liquidity bias** — prefer majors unless the thesis is specifically about
   a smaller token. Majors have deeper books (less slippage on DCA steps and
   market entries), and on Hyperliquid the major pairCodes are stable
   (BTC=0, ETH=1, SOL=5, DOGE=12, XRP=25, HYPE=159).
4. **Regime fit** — run the classifier (below) over 2–5 candidates and pick
   the token whose regime you have a *working strategy archetype* for, not
   the most exciting chart. A token in `trend_up` beats a choppy favorite.

```bash
# from this skill's dir:
./scripts/market_regime.py hyperliquid BTC  --interval 1h
./scripts/market_regime.py hyperliquid ETH  --interval 1h
./scripts/market_regime.py binance SOLUSDT --interval 1h --market futures
```

## 2. Market-conditions assessment

`scripts/market_regime.py` (stdlib-only) fetches public OHLCV
(Hyperliquid `candleSnapshot` or Binance klines) and computes:
EMA20/50/200, ATR14%, ADX14, RSI14, Bollinger width percentile,
Δ24h/Δ7d. Classification thresholds:

| Regime | Signature | Meaning |
|---|---|---|
| `trend_up` | EMA20>EMA50(>EMA200) **and** ADX ≥ 20 | directional drift — trend-ride works |
| `trend_down` | EMA stack inverted **and** ADX ≥ 20 | bear drift — shorts (futures) or flat (spot) |
| `chop_high_volatility` | ADX < 20 **and** ATR% ≥ 1.5 (1h bars) | no direction, big swings — DCA/mean-reversion |
| `squeeze` | BB width ≤ 20th pctile **and** ADX < 20 | compression — breakout pending or dead tape |
| `neutral` | everything else | no edge — probe small or stay flat |

Interval matters: classify on the interval you'll trade (1h for intraday
swings, 4h/1d for position trades). When in doubt run 1h and 4h; they must
agree on direction before you take a directional strategy.

**Alternative/adjacent data sources** (the WunderTrading API has no
candlestick endpoint — markets data is live ticks only):
- TradingView via this workspace's tvcli: `./tvcli fetch --symbol BINANCE:BTCUSDT --tf 60 --limit 300` (no auth) for OHLCV; `tvcli mtf-confluence`/`xau-scalp` (auth) for richer confluence reads.
- Hyperliquid `POST https://api.hyperliquid.xyz/info` `{"type":"predictFundings"}`, `{"type":"metaAndAssetCtxs"}` for funding/open-interest context (crowd positioning).
- News/sentiment (the AI-bot pattern): any feed the host agent has; sentiment extremes + squeeze = breakout fuel.

## 3. Regime → strategy config matrix

Full skeletons are emitted by the classifier script; the reasoning:

| Regime | Strategy | Entry | Take profit | Stop | Trailing / BE | DCA |
|---|---|---|---|---|---|---|
| `trend_up` | classic LONG | market (or limit at EMA20 pullback) | ladder 2/4/8% (40/30/30) vs entry_order | 3% | BE at +2%; trail 4%→2% | none |
| `trend_down` | classic SHORT (futures only) | market | mirror ladder | 3% | mirror | none |
| `chop_high_volatility` | DCA LONG (futures) | market | 1.5% vs **average_price** | none–8% (wide) | none (mean-revert exits fast) | 6 orders, 2.5% step, ×1.4 vol, ×1.5 step growth |
| `squeeze` | range limit at band edge — or wait | **limit** at range low, TTL ≤ 1 day | 2% | 3% tight | none | none (or 2 safety orders) |
| `neutral` | flat preferred; else probe DCA | market | 1.5% average_price | 8% | none | 4 orders, 2% step |
| squeeze→expansion breakout | classic LONG | market on expansion + volume | let trailing manage | 2.5% | trail 3%→1.5% | none |

Why each shape:

- **Trend:** laddered TPs bank partials while the trailing stop lets the last
  30% ride; break-even move (`stopLossMove` 2% → `stopLossMoveExecute` 0%)
  makes the trade free after +2%. No DCA — averaging down in a trend you
  called correctly is paying for a wrong premise.
- **Chop:** DCA is the correct primitive — the market oscillates, so buy the
  dips mechanically and exit into any mean reversion. `takeProfitBaseOn:
  "average_price"` is essential (TP measured from the averaged cost, not the
  first entry). Wide/no stop is deliberate: the DCA ladder *is* the risk
  management. Never run this without sizing math (below).
- **Squeeze:** either mean-revert inside the range (limit at the edge, small
  TP, tight stop) or wait for expansion — do not market-enter into dead tape.

**DCA sizing math (mandatory before deploying chop/neutral configs):**
with `amountPerTrade` = A (as `percents` of profile) the worst-case fill uses
`A × (1 + v + v² + … + v^(N−1))` where N = `extraOrderCount` and v =
`extraOrderVolumeMultiplier` (steps spaced by `extraOrderDeviation ×
deviationMultiplier^(i−1)`). For the default chop config (6 × ×1.4):
`1+1.4+1.96+2.74+3.84+5.38 ≈ 16.3×` A — so `amountPerTrade: "6%"` can commit
~98% of the profile if every step fills. Cap A so total ≤ 50% of balance, and
check the token can plausibly move `2.5%×(1+1.5+2.25+…)` ≈ 27% against you
before the ladder completes — on 1h majors it can, so respect it.

**Leverage:** the `leverage` field is a *reference only* (not applied to the
exchange). Set actual leverage on the exchange/position; 2–3× is the sane
ceiling for these archetypes.

### Grid bots (separate configurator — not the MCP/REST surface)

The Grid bot is configured in the web UI (headful `wt-investigator` + bdg) or
armed/triggered via its **webhook start condition** (TradingView-compatible
alerts — the clean tvcli→grid bridge). It is **not** reachable through
`place_strategy_trade` / `edit_trade_strategy`. Full mechanics, sizing, risk
controls and the regime→grid-type matrix are in
[grid-bot.md](grid-bot.md). Quick map:

| Regime | Grid type | Note |
|---|---|---|
| `trend_up` | `Long GRID` | limit buys on pullbacks; TP each leg |
| `trend_down` | `Short GRID` (futures) / flat (spot) | mirror of Long |
| `chop_high_volatility` | `Neutral GRID` centered at mid | the canonical grid regime |
| `squeeze` | `Neutral GRID` + tight channel + `Stop Trigger` | range-fade; stop on breakout |
| `neutral` | small probe `Neutral`/`Infinite` grid | half size |

Grid sizing differs from DCA: spot long ≈ `amountPerTrade × numberOfGrids`
(Neutral/Short/Hedge add the base-currency side; Hedge doubles exposure).
The platform's **Optimize** already sweeps Profit-per-GRID over 30 days and
**Profit-Optimized Pairs** ranks pairs by ROI on a `$500`/`$50` benchmark —
use it for the step, but keep the regime decision in `market_regime.py`.

## 4. Reliability — proving a strategy before scaling it

"Reliable" = positive expectancy across ≥ 30 closed samples, surviving both
regimes it claims to work in, with drawdown you can sit through. Build the
evidence with WunderTrading's own history:

```bash
# MCP: full 3-month export (get_strategies_history is broken server-side)
wun export_strategies_history '{"statuses":["completed","panic_exited","canceled"]}'
# → {recordCount, exportFileUrl} → download the JSON dataset
```

Compute per config-archetype (group by side+DCA-shape+pair, not just pair):

- **Win rate** = completed-with-profit / all closed. DCA/mean-rev targets
  ≥ 60%; classic trend targets ≥ 40% with R ≥ 1.5.
- **Profit factor** = Σwins / |Σlosses| ≥ 1.3 (≥ 1.5 to scale size).
- **Expectancy** = (win% × avg win) − (loss% × avg loss) > 0 after fees.
- **Max drawdown** on the equity curve of grouped entries; DCA clusters that
  barely escaped = latent risk, count them as near-losses.
- **Panic exits** (`panic_exited`) = forced closes — a stress signal about
  the config's stop placement, not the market.
- **Duration distribution** — chop strategies should be hours; if they're
  holding for days, the DCA is catching falling knives.

**Ladder (never skip steps):**

1. **Paper** — run the config on a `demo: true` profile (free on Wunder's
   Free plan) for ≥ 2 weeks / ≥ 30 samples.
2. **Probe live** — 5–10% of profile in one strategy, real slippage.
3. **Scale** — only after step 2 matches paper expectancy within ~20%.
4. **Kill rules** — profit factor < 1.0 over the last 20 samples, or any
   single loss > 2× the historical worst → `close_strategy_market`, back to
   paper, re-classify regime (the regime probably changed, not the config).

## 5. Deploy, monitor, adapt

```bash
wun get_strategy '{"id": "<strategyId>"}'          # status, strategyGroupType, prices
wun get_live_strategies '{"limit": 20}'           # watch open positions
wun get_strategy_orders_history '{"profileStrategyId": "<id>", "limit": 100}'
wun edit_trade_strategy '{"id": "<id>", …}'       # tighten trail, move stop (see mcp-tools.md rules)
wun place_strategy_swing '{"id": "<id>", "clientId": "<new-id>"}'  # flip on exit (futures, no DCA)
wun place_strategy_market_enter '{"id": "<id>"}'   # force entry of a limit strategy now
wun close_strategy_market '{"id": "<id>"}'         # exit at market
wun cancel_strategy '{"id": "<id>"}'               # cancel unentered strategy
```

Adaptation triggers (re-run the classifier on a schedule or on fills):

- `trend_up` decays (ADX < 20, EMA20 loses EMA50) → tighten trailing
  (`edit_trade_strategy`), ladder TPs down.
- Chop DCA keeps hitting full ladder → regime is actually `trend_down`
  against you → close, re-enter short side (futures) or flat.
- Squeeze breaks out → cancel resting range limits, deploy breakout config.
- Funding is heavily against your side (Hyperliquid `predictFundings`) →
  halve size or skip — you're paying the crowd to stay wrong.
