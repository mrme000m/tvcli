# WunderTrading Grid Bot — session-auth web API (reverse-engineered)

Verified live 2026-09-04 by driving the headful CloakBrowser configurator
(`/en/trader/grid_bots`) with real input events while recording the CDP network
layer (`dumps/wt-grid-investigation-2026-09-04.jsonl`,
`dumps/wt-grid-endpoints-2026-09-04.json`). Every fact below was observed on
the wire or replayed successfully.

## Transport

- All `/en/trader/*` endpoints are **session-authenticated** (PHPSESSID) and
  **Cloudflare-fingerprinted**: raw HTTP gets 403 "Just a moment…". The only
  reliable client is `fetch()` **inside the logged-in browser page** over CDP
  (`wt.mjs api` / `wt.mjs eval`) — real TLS + cookies + fingerprint.
- **CSRF:** every non-safe method (POST/PUT/DELETE) needs the header
  `X-W-CSRF-Token: <window.baseServerConfig.appCsrfToken>` (present on every
  cabinet page; ~105 chars). Without it: `Invalid CSRF token provided`.
- Public market data lives on a separate origin/port **`https://wundertrading.com:2087`**
  (no cookies, `credentials: "omit"`; plain fetch, no custom headers — custom
  headers trigger a CORS failure).

## Market data / symbol analysis (`:2087`, public)

| Call | Purpose |
|---|---|
| `GET :2087/all-markets?market=spot\|derivative&marketExpiryGroup=infinite` | full market map: `EXCH:code → {unifiedCode, pair}` |
| `GET :2087/market?marketCode=HYPERLIQUID_SWAP:159` | metadata: precision, `limits.cost.min`, `limits.leverage.max`, `type`, timeframes |
| `GET :2087/ohlc/last?code=<EXCH:pairCode>&timeframe=15` | last candle (o/h/l/c/volume/openInterest) |
| `GET :2087/ohlc?code=<EXCH:pairCode>&from=<ms>&timeframe=15&limit=2976` | history (30 days of 15m = 2976 candles) |
| `GET :2087/ohlc/low-high?code=&timeframe=&limit=` | historical high/low (Optimize source) |
| `GET :2087/supported-markets` | supported market list |

Hyperliquid pairCodes are **numeric strings**: `0`=BTC, `1`=ETH, `5`=SOL,
`159`=HYPE, `191`=HYPER (resolve from `all-markets`, never hand-write).

## Configurator boot (`GET`, session)

| Call | Purpose |
|---|---|
| `GET /en/trader/grid_bots/upsert` | form init data: `exchangesProfiles` (with profile codes + balances + `paperTrading`), `supportedExchanges`, `activeGridBots`, `maxActiveGridBots`, `signalSchemas` (`grid_bot:base_json:start/stop`), `isGranted` |
| `GET /en/trader/grid_bots/grid?page=1&limit=10&criteria[statuses][value][]=active` | bot list; each item carries `resource` + **`actions` links** (see Management) |
| `GET /en/trader/grid_bots/presets?page=1&limit=5` | preset grid configs (backtested ROI-ranked) |
| `POST /en/trader/my-exchanges/api-profile/fetch_profiles_leverage` | `{profilesCodes:[…], pairsCodes:["159"], exchangeCode}` → leverage state |
| `POST /en/trader/grid_bots/find_notional_prices` | `{exchangeCode, currencies:["USDC"]}` → cross rates for the investment panel |

## Create / edit — `POST /en/trader/grid_bots/upsert`

Query params: `?gridMarket=spot|derivative` (profile's market) and
`?code=<botCode>` for **edit** (edit uses the same body).

Body (verified-working create on demo profile, 200 + `gridBotCode`):

```json
{
  "exchangeCode": "HYPERLIQUID_SWAP",
  "pairCode": "191",
  "profilesCodes": ["<profile-code>"],
  "gridType": "interval",            // interval | infinite
  "gridMethod": "classic",           // classic | two_way (hedge)
  "gridTradingType": "long",         // long | short | neutral | two_way
  "gridPercentStep": 0.03,           // decimal (3% -> 0.03), ≤6 decimals
  "gridTickStep": null,
  "gridLevels": 21,                  // spreadLines.length (see geometry)
  "midPrice": 86.995,
  "initPrice": 87.009,               // last price at creation
  "closestHighLevelPrice": 87.049831,
  "closestLowLevelPrice": 84.514399,
  "amountPerTrade": 20,
  "amountPerTradeType": "base",      // base | percents
  "stopOnOutOfGrid": false,          // interval only
  "startCondition": "immediate",     // immediate | indicator | webhook_alert
  "signalCode": null,                // webhook start condition
  "maxRequiredAmount": null,
  "leverage": 1,
  "highPrice": 112.935,              // interval only
  "lowPrice": 61.055,                // interval only
  "stopCondition": "stop_only",      // stop_only | stop_and_close_all | stop_and_close_all_and_convert_to_profit_currency
  "profitCurrencyType": "base",     // spot: base|quote; derivative: base
  "pumpProtection": true,
  "pumpProtectionOrderType": "market",
  // optional, only when the UI toggles are on:
  "takeProfit": 100,                 // $ on cumulative Total PnL
  "stopLoss": 50,
  "stopLossPnlCompareType": "total",
  "trailingStopExecute": 2, "trailingStopActivation": 5, "trailingStopPnlCompareType": "total",
  "indicators": { "rsi": { "period": 14 } },  // when startCondition=indicator
  "signalSource": "trading_view",    // when startCondition=webhook_alert
  "strategyProfitCondition": "trailing_stop",          // positions trailing stop
  "strategyStopLossFixedPercentRatio": 0.05            // positions stop loss
}
```

**Grid line geometry** (client-side, replicate exactly): start `E = lowPrice`;
while `E <= highPrice` and `(highPrice-E)/E*100 >= step%`: push `E`, `E *=
1+step/100`; finally `push(highPrice)`. `gridLevels = lines.length`;
`closestLow/High` = the two lines bracketing `initPrice`.

Response: `{"result": {"data": <payload>, "status": "success", "gridBotCode": "…", "signalCode": null}}`

**Validation rules (server-verified):**
- One grid bot per pair per API profile: `HYPE-USDC pair is already used in
  another GRID bot with the same API!` (applies to paper/demo profiles too).
- `violations[]` with `propertyPath` for each field error.
- Paper profiles (`paperTrading: true`, e.g. `demo-hype` on HYPERLIQUID_SWAP)
  trade virtual balances — safe for end-to-end automation tests.

## Management (per-bot action links)

The `GET …/grid_bots/grid` list embeds **HAL-style `actions`** per bot with
`method` + `link` (and `can.violations` explaining why an action is disabled).
The URL scheme is deterministic:

| Action | Call |
|---|---|
| positions (live) | `GET /en/trader/grid_bots/{code}/positions/grid` |
| positions history | `GET /en/trader/grid_bots/{code}/positions-history/grid` |
| stop | `POST /en/trader/grid_bots/{code}/stop?stopCondition=stop_only&awaitStartSignal=true` |
| restart | `POST /en/trader/grid_bots/{code}/restart` (stopped bots) |
| close all positions | `POST /en/trader/grid_bots/{code}/close-all` (body `{}`) |
| edit | `POST /en/trader/grid_bots/upsert?code={code}` (same body as create) |
| delete | `DELETE /en/trader/grid_bots/{code}/delete` (requires stopped + no positions) |

Stop/restart/close-all/delete all verified live (200 + `{"status":"ok"}`).

## Backtest & Optimize — programmatic (engine ported, parity-verified)

`browser-debug/wt-backtest.mjs` is a **verbatim Node port of the
configurator's client-side backtest engine** (SPA module 534152). It fetches
the same `:2087/ohlc` history through the logged-in page (fetch-in-page; raw
HTTP is Cloudflare-403) and reproduces the UI's Backtest panel numbers
**exactly** (verified 2026-09-04 on HYPE-USDC: 103.6% realized / -1.6%
unrealized / 37+1 positions, digit-for-digit).

```bash
# config.json = the same payload you would POST to …/grid_bots/upsert
node browser-debug/wt-backtest.mjs backtest  cfg.json            # one run (UI parity)
node browser-debug/wt-backtest.mjs optimize  cfg.json           # platform sweep: step 1.2→3.0 %, best totalResult
node browser-debug/wt-backtest.mjs sweep     cfg.json --step 0.2:5:0.1 --widths 0.1,0.2,0.3
# agent sweep over profit-per-grid and channel half-widths; --rank-by totalResult|pnl

# DCA engine (same file): config = the wt-bots dca create payload
node browser-debug/wt-backtest.mjs dca       cfg.json            # one run
node browser-debug/wt-backtest.mjs dca-sweep cfg.json --dev 1:4:1 --tp 1:3:1
```

Engine semantics (extracted from the minified bundle — keep them when editing):
- **0.2% fee per closed grid position** (the `-.002` constant).
- Intra-candle path: down candle walks `[high, low, close]`, up candle
  `[low, high, close]` (a zigzag, not the OHLC open).
- Edge levels (first/last of the channel) never open positions.
- `long`/`short`/`neutral`/`two_way` open rules: LONG grids buy down-crossings,
  NEUTRAL longs ≤ mid / shorts > mid, two_way opens both sides; **pumpProtection
  flips the side filter** (longs open only on up-crossings).
- `stopOnOutOfGrid`: leading candles fully outside the channel are trimmed, and
  the sim halts when a candle breaks the channel high/low.
- Start bracket: if the first candle is outside the channel (M), the bracket
  starts at the near edge — for a price path that enters the grid from below
  and never dips, a LONG grid legitimately backtests all-zero.
- `optimize` (platform parity): sweep percents 1.2 → 3.0 step 0.01, keep the
  best `totalResult`; INFINITE grids use the 30-day hi/lo ±1% as bounds.
- `sweep` (agent extension): arbitrary step range + channel half-widths around
  the current price; rank by `totalResult` or realized `pnl` only
  (`--rank-by pnl` avoids ranking unrealized-heavy directional outcomes first).

Notes:
- There is **no server-side backtest endpoint** beyond `find_notional_prices`
  (notional rates for the USD conversions).
- Presets (`GET …/grid_bots/presets`) are the platform's pre-backtested
  configs (ROI-ranked; fields `grid_trading_type`, `grid_levels`,
  `grid_percent_step`, `high/low/mid_price`, `pnl`, `roi`, `timeframe`).

## Coverage matrix: help-center article vs API/CLI (audited 2026-09-04)

Audited against "How the Grid Bot Works: Strategy & Setup Guide"
(help.wundertrading.com article 8570328, fetched live through the browser).
Every web-interface capability and its programmatic equivalent:

| Web-UI capability (article) | API field / endpoint | CLI |
|---|---|---|
| Spot / Futures grid | `upsert?gridMarket=spot\|derivative` | `wt-grid create` (`gridMarketHint`) |
| Exchange account (incl. Paper) | `profilesCodes` (paper profiles: `paperTrading: true`) | `wt-grid profiles` |
| Pair | `pairCode` (resolve from `:2087/all-markets`) | `wt-grid analyze` |
| Long / Neutral / Short GRID | `gridTradingType: long\|short\|neutral` | `wt-grid create` |
| Hedge GRID (2 opposite per level, market enter/close) | `gridMethod: two_way` | `wt-grid create` |
| Change midpoint (Neutral) | `midPrice` | `wt-grid create` |
| Amount per trade | `amountPerTrade` + `amountPerTradeType: base\|percents` | `wt-grid create` |
| GRID size Interval / Infinite | `gridType: interval\|infinite` (+high/low for interval) | `wt-grid create` / `wt-backtest` |
| Higher / Lower price | `highPrice` / `lowPrice` | `wt-grid create` |
| Profit per GRID (geometric) | `gridPercentStep` (decimal, ≤6dp) | `wt-grid create` / `wt-backtest optimize` |
| GRIDs count | `gridLevels` | `wt-grid create` |
| Take Profit / Stop Loss ($ on cumulative Total PnL) | `takeProfit`, `stopLoss`, `stopLossPnlCompareType` | `wt-grid create` |
| Trailing Stop (activation + execute) | `trailingStopActivation`, `trailingStopExecute`, `trailingStopPnlCompareType` | `wt-grid create` |
| Stop Trigger + spot conditions | `stopOnOutOfGrid` + `stopCondition: stop_only\|stop_and_close_all\|stop_and_close_all_and_convert_to_profit_currency` | `wt-grid create` |
| Pump Protection | `pumpProtection`, `pumpProtectionOrderType` | `wt-grid create` |
| Start condition: Immediate | `startCondition: immediate` | `wt-grid create` |
| Start condition: RSI / MACD / Bollinger / Price Change | `startCondition: indicator` + `indicators: {rsi: {...}}` (periods per article) | `wt-grid create` |
| Start condition: Webhook alert | `startCondition: webhook_alert` + `signalSource`; response returns the `signalCode` for the alert | `wt-grid create` (read `signalCode` from response) |
| Allow to restart / stop alert action | `awaitStartSignal=true` on stop; stop alert action = `stopCondition` enum | `wt-grid stop` |
| Positions Trailing Stop (30% of grid step) | `strategyProfitCondition: trailing_stop` | `wt-grid create` |
| Positions Stop Loss | `strategyStopLossFixedPercentRatio` | `wt-grid create` |
| Spot Investment Information | `investmentRef`, `investmentBase`, `profitCurrencyType` + `find_notional_prices` (server-computed) | `wt-grid analyze` + create payload |
| Backtest (30d, positions/PnL, OL/CL/OS/CS chart) | client-side engine — ported verbatim | `wt-backtest backtest` (digit-for-digit parity) |
| Optimize (profit-per-GRID sweep, apply optimized) | client-side sweep — ported | `wt-backtest optimize` (platform parity) + `sweep` (superset) |
| Profit-Optimized Pairs (ROI cards, $500/$50 benchmark) | `GET …/grid_bots/presets` | `wt-grid presets [limit]` |
| Stop (leave trades / close all) | `POST …/{code}/stop?stopCondition=…&awaitStartSignal=true` | `wt-grid stop` |
| Positions (live + history) | `GET …/{code}/positions/grid`, `positions-history/grid` | `wt-grid positions` |
| Start (restart with pre-configured settings) | `POST …/{code}/restart` | `wt-grid restart` |
| Delete (stops + closes) | `DELETE …/{code}/delete` (requires stopped + flat) | `wt-grid delete` |
| Edit active bot's settings | `POST …/upsert?code={code}` — **geometry applies live (server re-anchors the channel around the current price); trade-size changes need stop → edit → restart** (verified live) | `wt-grid create`-style POST with `code` |
| Share | client-side modal (public stats URL); no dedicated API | — (not API-relevant) |
| Logs (webhook bots) | `GET …/signal_bots/{signalCode}/trading_view_logs/show` (action link `signals_logs`) | action links in `wt-grid list` |

**Verified edit semantics (live experiment, 2026-09-04):** POST `upsert?code=`
on an ACTIVE bot: the channel/step/levels apply immediately (server re-anchors
high/low/mid around the current price — send a fresh geometry); `amountPerTrade`
is echoed but NOT applied while active. Stop → edit → restart applies sizing
changes (verified: amount 20→30 took effect only after the stop-edit-restart
cycle).

Not covered by the API (by design / platform gap): the arithmetic grid step
("coming soon" per the article), and the chart-drag interaction itself (pure
UI convenience for setting `highPrice`/`lowPrice`/`midPrice` — the same
values are payload fields).

## Gotchas (verified 2026-09-04)

- The configurator SPA sometimes renders an empty shell after load (nav only).
  Recovery: `Page.navigate` refresh once or twice until the main content
  appears (browser-level navigate works even when the renderer is wedged).
- Long-lived WT tabs periodically wedge the renderer: `Page.captureScreenshot`
  → "Internal error", `Runtime.evaluate` hangs. Same recovery: refresh.
  (`dsh-cloak-panel` screenshot route now auto-recovers; `wt-ui.mjs` has
  per-command timeouts.)
- `POST :2087` fetches with custom headers fail CORS — use plain fetch,
  `credentials: "omit"` for the market-data origin.
