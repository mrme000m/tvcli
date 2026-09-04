# WunderTrading Grid bot — mechanics, sizing & the tvcli→webhook loop

Source: [How the Grid Bot Works: Strategy & Setup Guide](https://help.wundertrading.com/en/articles/8570328-how-the-grid-bot-works-strategy-setup-guide)
(help.wundertrading.com is Cloudflare-gated; fetch via a reader proxy when
re-reading).

The Grid bot is **not** part of the classic/DCA MCP/REST surface. It is a
web-UI configurator (`wundertrading.com/en/grid-bot`) with its own parameter
space, and it exposes a **webhook start condition** that TradingView signals
can trigger — that webhook is the clean headless bridge from tvcli/Pine
analysis into grid execution.

## 1. What it is

A grid places buy and sell orders at regular intervals above and below the
current price, harvesting each leg as price oscillates. Works on **Spot**
(you own the asset) and **Futures** (contracts, leverage available). The
tab is split into: asset chart, GRID Settings, and created bots.

## 2. Grid types

| Type | Behavior | Order semantics |
|---|---|---|
| `Long GRID` | only buy at each level (accumulate on dips) | limit enter, market TP |
| `Neutral GRID` | long zone below mid + short zone above mid; mid is draggable | limit enter, market TP |
| `Short GRID` | only sell at each level | limit enter, market TP |
| `Hedge GRID` | opens **two opposite positions** at every level simultaneously | market enter **and** market close |

A Neutral grid dragged to the bottom of the channel becomes a Long grid;
dragged to the top becomes a Short grid. `Mid Price` is the level above which
the bot opens Short trades and below which it opens Long trades.

## 3. Channel & step

- **GRID Size — `Interval`**: explicit `Higher Price` / `Lower Price` channel.
- **GRID Size — `Infinite`**: unbounded grid with a fixed profit per grid.
- **Profit per GRID**: percentage between levels — a **geometric** grid
  (arithmetic "coming soon").
- **GRIDs**: number of levels between grid lines.

## 4. Exits & risk controls

- **Take Profit / Stop Loss**: expressed in **$ on cumulative Total PnL**
  (Realized + Unrealized). When cumulative PnL hits the target, the bot closes
  all remaining positions and stops.
- **Trailing Stop**: `Activation Price` arms it, `Trailing Stop` trails it.
- **Stop Trigger**: auto-stops if price breaks the channel. Three spot
  conditions: **Stop only** (no new entries), **Stop and close all**, **Stop,
  close all and convert to profit currency**.
- **Pump Protection**: avoids chasing rapid pump/dump moves.

## 5. Start conditions (entry filter)

The bot's entry can be gated by a start condition; on completion it re-arms
the same condition in the same direction.

| Condition | Signal | Params |
|---|---|---|
| `Immediate` | enter regardless | — |
| `RSI` | Long enters oversold, Short enters overbought | period 14, ≤25 / ≥75 |
| `MACD` | line crosses signal (long: both < 0) | 3 / 21 / 9 |
| `Bollinger bands` | long: close below lower band then next candle closes above | 21 / 2.5 |
| `Price Change` | drop → long, rise → short | 15 min / 2% |
| `Webhook alert` | start/stop from an external alert (TradingView or any source) | Entry/Exit alert messages |

### The webhook path (tvcli → grid, headless)

Selecting `Webhook alert` yields a webhook URL + an `Entry alert message`
(unique per bot) that must appear in the alert. `Allow to restart the bot`
controls whether the entry message re-arms a stopped bot; `Stop alert action`
defines what the `Exit alert message` triggers. This means a tvcli skill can
emit a TradingView-compatible alert that **starts/stops a grid bot** with no
headful browser and no MCP/REST call. The consolidated grid-candidate tvcli
skill should emit exactly this payload.

## 6. Sizing (approximate — the investment panel is authoritative)

- Spot grid long side commits `amountPerTrade × numberOfGrids` in quote
  currency; a Neutral/Short grid additionally needs the base currency for the
  short side. The `Investment Information` panel computes the required amount
  per coin from profit-per-grid and grid count — trust it, don't hand-roll.
- Futures grids: per-grid notional is `amountPerTrade × leverage`; the
  `leverage` field is reference-only and set at the exchange.
- `Hedge` doubles exposure (two positions per level) — halve `amountPerTrade`
  relative to a Neutral grid for the same risk.
- Cap total commitment ≤ 50% of profile balance (same rule as the DCA ladder).

## 7. Backtest & Optimize (platform-side optimizer)

- **Backtest**: 30 days on the configured parameters; reports Positions Long /
  Unrealised Long / Positions Short / Unrealised Short and Realized/Unrealized/
  Total PnL (in $, based on the Investment Value).
- **Optimize**: runs multiple backtests to find the optimal **Profit per GRID**
  for the last 30 days (Interval → within the configured channel; Infinite →
  over historical high/low). Apply with "Apply optimized settings".
- **Profit-Optimized Pairs**: ROI-ranked backtest cards per pair. **Benchmark:
  $500 portfolio, $50/trade** — read ROI in that frame, not absolute.

Note: Optimize only sweeps Profit-per-GRID and is regime-agnostic. Use
`market_regime.py` for the regime/grid-type decision and Optimize only for the
step, then re-verify with `export_strategies_history`.

## 8. Regime → grid-type mapping

| Regime (`market_regime.py`) | Grid archetype | Rationale |
|---|---|---|
| `trend_up` | `Long GRID` (or classic via API) | accumulate limit buys on pullbacks, TP each leg |
| `trend_down` | `Short GRID` (futures) / flat (spot) | mirror of Long grid on the short side |
| `chop_high_volatility` | `Neutral GRID` centered at mid | the canonical grid regime — oscillates, harvest both sides |
| `squeeze` | `Neutral GRID` + tight channel at band edges + `Stop Trigger` | range-fade inside compression; stop on breakout |
| `neutral` | small probe `Neutral`/`Infinite` grid | half size, wide bounds, or flat |
| `multi-pair` (portfolio) | `Multi-Pair Grid` over token_screen top-N | per-pair weight by rank; same regime family only |

Start-condition pairing: `chop`/`squeeze` → `RSI` or `Bollinger`; `trend` →
`MACD` or `Webhook` (tvcli confluence); confirmed classified regimes with no
entry filter → `Immediate`.

## 9. Gaps vs the MCP/REST surface

- Grid bots are **not** reachable through `place_strategy_trade` /
  `edit_trade_strategy` (those are classic/DCA). Configure grids via the web
  UI (headful `wt-investigator` + bdg) or the webhook start condition.
- WunderTrading still has **no candles endpoint**; grid sizing/investment is
  computed platform-side from the settings you enter.
- Every live deployment still requires the Phase E checklist and explicit
  user confirmation (see SKILL.md) — the webhook only *arms/triggers* an
  already-approved, already-configured bot.