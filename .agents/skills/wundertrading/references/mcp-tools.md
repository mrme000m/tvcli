# WunderTrading MCP tools — complete reference

All facts below verified live against `https://wundertrading.com:2083/mcp`
(server: `wundertrading-tools` v1.1.0, protocol `2025-06-18`).

## Transport & auth

- **Transport: streamable HTTP** (NOT legacy SSE). Plain `POST` JSON-RPC 2.0 to
  `https://wundertrading.com:2083/mcp`; the response is `text/event-stream`
  carrying the JSON-RPC reply in an SSE `data:` line. No session id required
  (sessionless requests work), no `initialize` handshake needed for tools/call.
- **Headers (auth):** `X-API-Key` + `X-Secret-Key` (both required). These are
  the cabinet-created HMAC API keys — same key pair works for the REST API
  (different signing scheme, see `rest-api.md`).
- **Permissions:** keys carry scopes (e.g. `strategy:read`, `strategy:write`);
  trade-executing tools require `strategy:write`.
- Keys: max 10 per account, shown only once at creation, **expire after 3
  months unless IP-whitelisted** (IP-whitelisted keys never expire).
- The shell recipes use `WUN_API_KEY` / `WUN_SECRET_KEY` env vars. On this
  machine the live pair is in the zcode MCP config
  (`~/.zcode/cli/config.json` → `mcp.servers.wundertrading.headers`) —
  extract from there rather than asking the user; never commit key values.

### Calling tools from a shell (when no MCP client is configured)

```bash
wun() { curl -sS -m 60 -X POST "https://wundertrading.com:2083/mcp" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -H "X-API-Key: $WUN_API_KEY" -H "X-Secret-Key: $WUN_SECRET_KEY" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"$1\",\"arguments\":$2}}" \
  | sed -n 's/^data: //p'; }
wun get_supported_exchanges '{}'
```

Tools that only *read* are `get_*` / `export_*`; everything that starts with
`place_`, `cancel_`, `close_`, or `edit_` **executes or mutates live state**.

## Tool catalog (15 tools)

| Tool | Kind | Purpose |
|------|------|---------|
| `get_supported_exchanges` | read | All exchange codes (35 verified) |
| `get_exchange_markets` | read | Markets per exchange: code, viewSymbol, bid/ask/last |
| `get_api_profiles` | read | Connected exchange accounts + balances + status |
| `get_live_strategies` | read | Active/recent strategies (Cabinet Positions) |
| `get_strategy` | read | One strategy by id/clientId — read `strategyGroupType` before editing |
| `get_strategy_orders_history` | read | Filled orders for one strategy (by profileStrategyId) |
| `export_strategies_history` | read | Full 3-month history as downloadable JSON (server-side pagination) |
| `export_strategy_orders_history` | read | Full order history for one strategy as download |
| `place_strategy_trade` | **WRITE** | Create + start a new strategy (opens a position) |
| `place_strategy_market_enter` | **WRITE** | Force a LIMIT strategy's entry at market now |
| `place_strategy_swing` | **WRITE** | Flip an entered futures strategy to swing (opposite on exit) |
| `edit_trade_strategy` | **WRITE** | Edit TP/SL/trailing/DCA of a live strategy |
| `cancel_strategy` | **WRITE** | Cancel strategy (stops orders; position left as-is) |
| `close_strategy_market` | **WRITE** | Close the position at market immediately |

### ⚠️ Live-verified quirks

1. **`get_strategies_history` is currently BROKEN server-side** — every call
   returns `-32603 "This tool is currently unavailable. Please consider to use
   export_strategies_history instead."` Use `export_strategies_history`.
2. **`pairCode` is NOT the ticker on every exchange.** It is the `code` field
   from `get_exchange_markets` — a plain ticker on Binance (`ETHUSDT`) but a
   **numeric string on Hyperliquid** (BTC-USDC → `"0"`, ETH-USDC → `"1"`,
   SOL → `"5"`, DOGE → `"12"`, XRP → `"25"`, HYPE → `"159"`). Always resolve
   via `get_exchange_markets` first — never hand-write a Hyperliquid pairCode.
3. Market objects: `{"code", "viewSymbol", "type", "expiry", "ask", "bid",
   "last", "currencies": {"base", "quote"}}` — live bid/ask/last included, so
   `get_exchange_markets` doubles as a free price tick snapshot (spread check
   before entry).
4. `get_api_profiles` returns `{profiles: [{exchange, code, status, balance,
   name, demo}], has_more}`; statuses: `active`, `no_assets`, `error`,
   `disabled`. `demo: true` = paper account (safe to test on).
5. History is capped at the **last 3 months**; `export_strategies_history`
   returns `{recordCount, exportFileUrl, cached}` — download the URL for the
   dataset (do not expect inline records).
6. `get_strategy_orders_history` keys on `profileStrategyId` (e.g.
   `69427bcc690d5f842315543d`), NOT the top-level strategy id — get it from
   `get_strategy`/`get_live_strategies` output.

## `place_strategy_trade` — full schema

Required: `exchangeCode`, `pairCode`, `profilesCodes[]`, `side`, `orderType`,
`amountPerTrade`, `amountPerTradeType`.

| Field | Type / range | Notes |
|---|---|---|
| `clientId` | str 32–64, `^[~=-a-zA-Z0-9]+$` | Idempotency key — unique per strategy |
| `exchangeCode` | str | From `get_supported_exchanges` |
| `pairCode` | str | Market `code` from `get_exchange_markets` (see quirk 2) |
| `profilesCodes` | str[] ≥1 | API profile ids from `get_api_profiles` |
| `side` | `long` \| `short` | |
| `orderType` | `market` \| `limit` | |
| `price` | number | **Required for limit** — entry price |
| `timeToLive` | 5–20160 (min) | **Required for limit** — order lifetime |
| `amountPerTrade` | number > 0 | Accepts `0.6`, `60%`, `60` |
| `amountPerTradeType` | `quote` \| `base` \| `$` \| `percents` \| `contracts` | `percents` = share of profile balance; `quote`/`base` = quote/base ccy (ADA/USDT → USDT/ADA) |
| `amountPerTradeMultiplier` | 1–125 | |
| `leverage` | 1–125 | **Reference only — NOT applied to the exchange** |
| `extraOrderCount` | 1–30 | DCA: total orders incl. entry (1 = no DCA) |
| `extraOrderDeviation` | 0.001–0.2 | DCA price step (0.1%–20%) |
| `extraOrderVolumeMultiplier` | 0.1–10 | DCA volume growth per step |
| `extraOrderDeviationMultiplier` | 1–10 | DCA step growth factor |
| `extraOrderCostAveraging` | `base` \| `quote` \| null | DCA amount unit; null = exchange default |
| `applyDcaForFirstSafetyOrder` | bool | true = order averaging, false = position averaging |
| `takeProfits` | 1–10 items | `{price \| priceDeviation, portfolio}`; portfolios sum to 1 |
| `takeProfitBaseOn` | `entry_order` \| `average_price` | **Use `average_price` for DCA** |
| `stopLoss` | deviation str `>0`, (0–1] | e.g. `"3%"` / `0.03` |
| `stopLossPrice` | number | Exact trigger (alternative to deviation) |
| `stopLossBaseOn` | `entry_order` \| `average_price` | |
| `stopLossMove` | (0–1] | Break-even trigger deviation |
| `stopLossMoveExecute` | −1 … stopLossMove | Break-even execution deviation (needs `stopLossMove`) |
| `trailingStopActivation` | (0–1] | Trailing arms after this deviation |
| `trailingStopExecute` | (0–1] | Trailing executes after this pullback |
| `reduceOnly` | bool | Only closes positions |
| `keepPositionOpen` | bool | Spot only |
| `placeConditionalOrdersOnExchange` | bool | Put exit orders on-exchange (ignored when DCA on) |

## `edit_trade_strategy` — rules

**Precondition:** call `get_strategy` first and read `strategyGroupType`.

- `strategyGroupType = "classic"` → **do NOT send** `extraOrderCount`,
  `extraOrderDeviation`, `extraOrderVolumeMultiplier`,
  `extraOrderDeviationMultiplier`.
- `strategyGroupType = "dca"` → DCA fields allowed;
  `placeConditionalOrdersOnExchange` ignored.
- `takeProfits` portfolios must sum to 1; `[]` removes all TPs.
- Prices (not deviations) in edit: `takeProfits[].price`, `stopLossPrice`,
  `stopLossMovePrice`, `stopLossMoveExecutePrice` — compute from the strategy's
  current Entry/Last Price (from `get_strategy`) honoring the LONG/SHORT
  ranges in the schema (e.g. LONG TPs must be > Entry Price; LONG
  `stopLossPrice` must be < Last Price).
- `trailingStopActivation`/`trailingStopExecute` accept numbers or `null`
  (null = remove).
- If `stopLossMoveExecutePrice` is set, `stopLossMovePrice` must be set too.

## `get_strategies_history` / `export_strategies_history` filters

Both accept: `exchanges[]`, `apiProfiles[]`,
`statuses[] ∈ {new, entered, completed, canceled, cancelling, panic_exited,
panic_exiting, unlinked, failed}`. Strategy statuses across the API:
`new` → `entered` → `completed` | `canceled` | `panic_exited` | `failed`
(`unlinked` = profile disconnected). `panic_exited` = closed via emergency
"panic" button — treat as forced exits, not signal quality.
