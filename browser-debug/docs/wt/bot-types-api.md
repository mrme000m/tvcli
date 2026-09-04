# WunderTrading Bot Types API — Signal / Grid / DCA / Market Neutral / Multi-Pair Grid

Companion to [grid-bot-api.md](grid-bot-api.md) (Grid bot deep-dive). All
surfaces below verified live 2026-09-04 on the `demo-hype` paper profile
(HYPERLIQUID_SWAP) with full create→manage→delete round-trips. Unified CLI:
`node browser-debug/wt-bots.mjs <signal|grid|dca|mn|mp> <list|create|stop|start|close-all|delete|positions|init>`.

Transport for ALL types (same as Grid):
- session cookies + `X-W-CSRF-Token: window.baseServerConfig.appCsrfToken`
  on POST/PUT/DELETE, fetch-in-page only (Cloudflare).
- Bot lists embed per-bot HAL `actions` (`method` + `link`) with
  `can.violations` explaining any disabled action — read them; the URL
  scheme is deterministic per type (below).

## Common per-type action matrix

| Type | list | create/edit | stop | start | close-all | delete | positions |
|---|---|---|---|---|---|---|---|
| Signal | `GET /en/trader/signal_bots/grid?` | `POST …/signal_bots/upsert?code=` | `…/{c}/stop?stopCondition=…` | `…/{c}/start` | `…/{c}/close-all` | `DELETE …/{c}/delete` | `…/{c}/positions/grid` |
| Grid | `GET …/grid_bots/grid?` | `POST …/grid_bots/upsert?gridMarket=&code=` | `…/{c}/stop?stopCondition=…&awaitStartSignal=true` | `…/{c}/restart` | `…/{c}/close-all` | `DELETE …/{c}/delete` | `…/{c}/positions/grid` |
| DCA | `GET …/dca_bots/grid?` | `POST …/dca_bots/upsert?code=` | `…/{c}/switch-pause?stopCondition=…&awaitStartSignal=true` | `…/{c}/switch-pause` | `…/{c}/close-all` | `DELETE …/{c}/delete` | `…/{c}/positions/grid` |
| Market Neutral | `GET …/market_neutral/grid` | `POST …/market_neutral/upsert?code=` | `…/{c}/pause` | `…/{c}/pause` (toggle) | `…/{c}/close-all` | `DELETE …/{c}/delete` | `…/positions/grid/{c}` |
| Multi-Pair Grid | `GET …/multi_pair_grid_bot/grid?limit=200` | `POST …/multi_pair_grid_bot/upsert?code=` | `…/{c}/switch-pause?stopCondition=…&awaitStartSignal=true` | `…/{c}/switch-pause` | `…/{c}/close-all` | `DELETE …/{c}/delete` | `…/positions/grid/{c}` |

All types: `GET …/{c}/positions-history/grid` (MN/MP: `positions/history/grid/{c}`);
delete requires stopped + no open positions (403 "Forbidden" otherwise);
`close-all` accepts `{}` body. DCA/MP `switch-pause` REQUIRES
`?stopCondition=stop_only&awaitStartSignal=true` query params on the stop
direction (violation: "stopCondition should not be blank").

## DCA Bot — `POST /en/trader/dca_bots/upsert` (verified create)

```json
{
  "exchangeCode": "HYPERLIQUID_SWAP", "pairsCodes": ["159"],
  "profilesCodes": ["<profile>"], "dcaTradingType": "long",
  "amountPerTrade": 20, "amountPerTradeType": "base",
  "orderType": "market", "entrySignalCondition": "immediate",
  "applyDcaForFirstSafetyOrder": false,       // true when dcaMode=order_averaging
  "extraOrderCount": 3, "extraOrderDeviation": 0.02,
  "extraOrderVolumeMultiplier": 1.4, "extraOrderDeviationMultiplier": 1,
  "takeProfitBaseOn": "average_price",         // entry_order | average_price
  "stopLossBaseOn": "average_price",
  "maxRequiredAmount": null, "extraOrderCostAveraging": "base",  // base | quote
  "leverage": 1, "investmentBase": 0, "investmentRef": 0,
  "takeProfits": [{ "priceDeviation": 0.015, "portfolio": 1 }]
}
```
Init data (`GET …/dca_bots/upsert`): supportedExchanges, exchangesProfiles,
activeDcaBots, maxActiveDcaBots, platformMinTradeAmountInUSD. Statuses seen:
`active`, `paused`, `paused_with_unrealized`.
**Programmatic DCA backtest/optimization** — the client-side engine
(`DCABot/runBacktest` → module 909695) is ported verbatim into
`wt-backtest.mjs` (DCA ladder from module 75976, RSI/BB/MACD/PriceChange
entry-indicator wrappers, 0.2% fee per round trip, TP/SL on average vs entry
price, same input mapping as the thunk):

```bash
node browser-debug/wt-backtest.mjs dca <dca-config.json>          # one run
node browser-debug/wt-backtest.mjs dca-sweep <cfg.json> --dev 1:4:1 --tp 1:3:1 [--rank-by profit]
# sweeps dcaOrderPriceDeviation x takeProfit; ranked by realized profit
```
The config is the same JSON as `wt-bots.mjs dca create` (extraOrderCount,
extraOrderDeviation, extraOrderVolumeMultiplier, takeProfits[0].priceDeviation,
takeProfitBaseOn…). Verified sample (HYPE-USDC long, 20 USDC, 3×2%/×1.4 DCA,
TP 1.5% avg): 81 realized trades / 844.77 USDC over 31 days; sweep best
dev 2% × TP 3% → 1191.62 USDC. Parity basis: same engine module + same input
mapping as the UI thunk (the DCA configurator tab crashed the renderer
repeatedly during live diffing, so numbers were not cross-checked digit-for-
digit — trust the grid engine's exact-parity precedent).

## Market Neutral Bot — `POST /en/trader/market_neutral/upsert` (verified create)

```json
{
  "source": "hyperliquid_swap_spread_ai_bot",   // from contexts (per exchange)
  "refExchangeCode": "HYPERLIQUID_SWAP", "baseExchangeCode": "HYPERLIQUID_SWAP",
  "refExchangeProfilesCodes": ["<profile>"], "baseExchangeProfilesCodes": ["<profile>"],
  "pause": false, "fixedAmount": 60,              // >= minCapitalRequirement (50)
  "maxOpenPositions": 1, "orderType": null,       // null = auto
  "type": "two_legs", "volatility": "both",
  "quantileGroup": 1,                             // 1 | 5 | 10
  "tradeDirection": "mean_reverse",
  "zScoreRollingWindow": 2880,                    // 288 | 2880
  "entryCondition": "into_the_channel",
  "minScore": null, "leverage": 1
}
```
Init data (`GET …/market_neutral/upsert`): `contexts` (source per exchange +
minCapitalRequirement — Hyperliquid supported), exchangesProfiles, form.
Extras: `GET …/market_neutral/pnl` (stats), `GET …/ai-bot/last_trades`.

## Multi-Pair Grid Bot — `POST /en/trader/multi_pair_grid_bot/upsert` (verified create)

```json
{
  "exchangeCode": "HYPERLIQUID_SWAP", "profilesCodes": ["<profile>"],
  "riskLevel": "mid",                              // low | mid | high
  "investment": 250,                               // >= riskLevelsMinInvestments
  "pairSelectionMode": "auto",                      // auto | custom (+ pairsCodesWhitelist)
  "stopLossRatio": 0.5, "takeProfitRatio": 0.5,
  "validateOnly": false
}
```
Init data: riskLevelsMinInvestments (low 400 / mid 200 / high 100),
riskLevelsRecommendedInvestments (4000/2000/1000), minAmountPerTrade (20),
amountPerTradeRatio (0.05/0.1/0.2). Custom pairs:
`GET …/multi_pair_grid_bot/custom-mode-markets/{exchange}`.

## Signal Bot — `POST /en/trader/signal_bots/upsert` (schema extracted)

Payload (from the SPA bundle; **requires a user-owned `signalCode`** — the
code is assigned when binding a TradingView alert/signal, so creation is only
possible after that binding exists):

```json
{
  "signalCode": "<from signal binding>", "name": "...", "about": "...",
  "time": "15m",                                  // timeframeValue + Period
  "exchanges": [/* prepareMultipleExchanges: per-exchange profiles+amount */],
  "amountPerTrade": 20, "amountPerTradeType": "base",
  "takeProfits": [{ "portfolio": 1, "priceDeviation": 0.015 }],
  "takeProfitBaseOn": "average_price", "stopLossBaseOn": "average_price",
  "stopLoss": null, "stopLossMove": null, "stopLossMoveExecute": null,
  "trailingStopActivation": null, "trailingStopExecute": null,
  "multipleEntries": false, "dynamicPair": false, "swingTrade": false,
  "orderType": "market", "reduceOnly": false, "keepPositionOpen": false,
  "maxOpenPositions": 2, "alertSettingsType": "message",
  "signalSource": "trading_view", "maxCapitalUsdCents": null,
  "placeConditionalOrdersOnExchange": false,
  ["dca block": extraOrderCount, extraOrderDeviation, extraOrderVolumeMultiplier,
   extraOrderDeviationMultiplier, applyDcaForFirstSafetyOrder, extraOrderCostAveraging],
  ["limit block": timeToLive, limitOrderPriceType, limitOrderPriceDeviation(+Type)]
}
```
Init data (`GET …/signal_bots/upsert`): activeSignalBots, maxActiveSignalBots,
signalBot (current form state, empty when none). For the TradingView-alert →
execution bridge the platform ALSO exposes the classic-strategy MCP/REST
surface (see the wundertrading skill) — prefer that for agent-driven entries.

## Programmatic parameter optimization (all types)

- **Grid**: full UI-parity backtest + optimize + sweep → `wt-backtest.mjs`
  (see grid-bot-api.md). This is the only type with a ported engine so far.
- **DCA**: client-side backtest engine identified (module 909695) — inputs
  documented above; port on demand.
- **Market Neutral / Multi-Pair**: server-driven AI bots (no client backtest
  engine); the MP init data carries the risk/investment matrices agents
  should respect when choosing riskLevel × investment.
- **Signal**: no backtest (alert-driven).

## Safety

- All verification was done on the `demo-hype` **paper** profile
  (`paperTrading: true`). Mirror that pattern for automation tests.
- One grid bot per pair per profile (server-validated), and each type counts
  against its own active-bots limit from the init data.
