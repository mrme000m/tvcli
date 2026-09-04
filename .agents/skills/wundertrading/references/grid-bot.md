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

## 9. Programmatic API (reverse-engineered, verified 2026-09-04)

The Grid bot is now **fully API-reachable** through the session-auth web
surface (NOT the HMAC `/open_api` — that stays classic/DCA only). Full
reference with payload schemas, enums and gotchas:
[**docs/wt/grid-bot-api.md**](../../../../browser-debug/docs/wt/grid-bot-api.md)
in the repo (`browser-debug/docs/wt/grid-bot-api.md`). Ready-made CLI:
`node browser-debug/wt-grid.mjs <list|analyze|create|stop|restart|close-all|delete|positions|profiles>`
(fetch-in-page via the logged-in CloakBrowser; CSRF handled automatically).

Key facts (all live-verified on the `demo-hype` paper profile):

- **Auth**: session cookies + `X-W-CSRF-Token: window.baseServerConfig.appCsrfToken`
  on every POST/DELETE; must run as fetch inside the logged-in page (`wt.mjs eval` /
  `wt-grid.mjs`). Raw HTTP gets Cloudflare-403.
- **Create/edit**: `POST /en/trader/grid_bots/upsert?gridMarket=spot|derivative[&code=<botCode>]`
  with the full payload (exchangeCode, pairCode, profilesCodes, gridType
  `interval|infinite`, gridMethod `classic|two_way`, gridTradingType
  `long|short|neutral`, gridPercentStep, gridLevels, mid/initPrice,
  closestHigh/LowLevelPrice, high/lowPrice, amountPerTrade(+Type
  `base|percents`), startCondition `immediate|indicator|webhook_alert`,
  stopCondition `stop_only|stop_and_close_all|stop_and_close_all_and_convert…`,
  optional TP/SL/trailing/pumpProtection/positions-SL blocks).
  Response returns `gridBotCode`.
- **Management** (per-bot, deterministic URLs):
  `GET …/grid_bots/{code}/positions/grid` (live) and `…/positions-history/grid`;
  `POST …/{code}/stop?stopCondition=…&awaitStartSignal=true`;
  `POST …/{code}/restart`; `POST …/{code}/close-all`;
  `DELETE …/{code}/delete` (requires stopped + no open positions).
  The bots list (`GET …/grid_bots/grid`) embeds per-bot `actions` links with
  `can.violations` explaining any disabled action.
- **Symbol analysis** (public, no auth, port **2087**): `GET :2087/all-markets`,
  `:2087/market?marketCode=`, `:2087/ohlc/last?code=`, `:2087/ohlc?code=&from=&timeframe=&limit=`,
  `:2087/ohlc/low-high`. Hyperliquid pairCodes are numeric strings
  (BTC=0, ETH=1, SOL=5, HYPE=159, HYPER=191) — resolve via `all-markets`.
- **Backtest/Optimize are client-side** in the configurator over that OHLC —
  no dedicated endpoint; agents replicate with the same data (or tvcli fetch).
- **Validation**: one grid bot per pair per API profile (paper profiles too);
  errors return `violations[]` per field.
- **Paper testing**: `paperTrading: true` profiles (e.g. `demo-hype` on
  HYPERLIQUID_SWAP) take the same API with virtual balances — the safe
  end-to-end automation test target.

## 10. Gaps vs the MCP/REST surface

- Grid bots are **not** reachable through `place_strategy_trade` /
  `edit_trade_strategy` (those are classic/DCA) — use the web-session API above.
- The `/open_api` HMAC surface still does not cover grid bots.
- Every live deployment still requires the Phase E checklist and explicit
  user confirmation (see SKILL.md) — the webhook only *arms/triggers* an
  already-approved, already-configured bot.