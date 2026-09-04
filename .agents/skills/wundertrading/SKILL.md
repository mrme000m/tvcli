---
name: wundertrading
description: WunderTrading (wun) trading platform skill — find a reliable strategy, pick a token, and build the exact trade config for that token's current market conditions, then deploy/monitor/adjust it through WunderTrading's MCP tools or REST API. Use when asked to create or manage wun strategies, analyze strategy/order history, place or edit strategy trades, connect accounts/exchanges, or run an AI trading bot loop where the agent is the strategy brain and WunderTrading executes.
---

# wundertrading — strategy brain for the WunderTrading execution layer

WunderTrading is an execution platform: it connects your exchange accounts
(Hyperliquid, Binance, Bybit, OKX, … — 35 exchange codes) and runs **strategy
primitives** (classic/DCA entries, TP ladders, stop-loss, break-even move,
trailing stop, swing flip) that you configure. **You are the strategy brain**
— the agent decides the token, direction, and config from market conditions;
WunderTrading executes and holds the orders.

Three interfaces, one account:

- **MCP** (primary): `mcp__wundertrading__*` tools in a configured client
  (zcode: `~/.zcode/cli/config.json`; PrimeAgent: `~/.prime/agent/settings.json`),
  or the curl recipe below. 15 tools; full schemas + live-verified quirks in
  [references/mcp-tools.md](references/mcp-tools.md). Also via `scripts/wt_httpx.py mcp` (httpx without browser, same `X-API-Key`/`X-Secret-Key`).
- **REST HMAC** (scripting/automation): `https://wundertrading.com/open_api/*`,
  HMAC-SHA256 signing — verified recipe in [references/rest-api.md](references/rest-api.md), also via `scripts/wt_httpx.py open_api` (pure httpx, no browser).
- **Session web** (grid bots, no HMAC): Cloudflare-fingerprinted `/en/trader/*` configurator — grid bots live here only (see [references/grid-bot.md](references/grid-bot.md)). Use `scripts/wt_browser.py` (httpx **with** CloakBrowser via CDP `fetch()` in-page, `node browser-debug/wt-grid.mjs` parity, verified live) or best-effort `scripts/wt_httpx.py session` (raw httpx replay; needs fresh `cf_clearance`, else 403 `Just a moment…`). Launch CloakBrowser with `node browser-debug/launch.mjs` so the Xvfb/WebGL robustness flags (`--ignore-gpu-blocklist`, `--disable-dev-shm-usage`, etc.) are set; otherwise the WunderTrading /login and grid configurator may stay blank.

Keys: created in the cabinet API page (max 10; **expire in 3 months unless
IP-whitelisted**; shown once). MCP auth = `X-API-Key` + `X-Secret-Key`
headers. Export as `WUN_API_KEY` / `WUN_SECRET_KEY` for the shell recipes —
never paste key values into committed files.

## Python package (`scripts/wtclient`)

The importable, validated, type-safe client library behind the CLI. Prefer it
for new automation instead of shelling out to scripts:

```python
from wtclient import WunderTrading

wun = WunderTrading()                      # reads provisioned secret files
wun.rest.exchanges()                       # HMAC REST, no browser
wun.mcp.api_profiles(limit=5)              # MCP, no browser
wun.mcp.export_strategies_history(statuses=["completed"])

wun = WunderTrading(browser=True)          # needs a running CloakBrowser tab; start it with node browser-debug/launch.mjs
wun.grid.list()
wun.grid.analyze("HYPERLIQUID_SWAP:191")
wun.market.ohlc_last("HYPERLIQUID_SWAP:191", timeframe=15)
```

Request models mirror the live MCP schemas + cross-field rules and fail before
any HTTP call (`PlaceStrategyTrade`, `EditTradeStrategy`, `GridUpsertPayload`,
`takeProfits` portfolios sum to 1, limit-vs-market rules, grid geometry).
Percent fields accept `0.6` / `"60%"` / `"60"` and canonicalize to `"0.6"`.
The CLI (`scripts/wt_httpx.py`) is now a thin shim over this package and keeps
the same command shapes, plus `grid` (browser or raw session) and
`market --transport browser`. Full layout/extension guide:
[scripts/wtclient/README.md](scripts/wtclient/README.md). Tests:

```bash
cd .agents/skills/wundertrading/scripts
python3 -m unittest discover -s wtclient/tests -t . -v
```

## Core workflow

### Phase A — recon (read-only, always first)

```bash
# with MCP client tools, or the shell recipe from references/mcp-tools.md:
get_supported_exchanges '{}'                    # 35 exchange codes
get_api_profiles '{"limit": 100}'               # YOUR accounts: balance, status, demo flag
get_exchange_markets '{"exchanges": ["HYPERLIQUID_SWAP"]}'  # markets + live bid/ask/last
get_live_strategies '{"limit": 20}'             # anything already running
```

Only `status: "active"` profiles can trade. `demo: true` = paper account.
Market `code` values are the pairCodes you'll pass to trades — **on Hyperliquid
they are numeric strings** (BTC-USDC → `"0"`, ETH → `"1"`, SOL → `"5"`), on
Binance they are tickers (`ETHUSDT`). Never hand-write a pairCode; resolve it
from `get_exchange_markets`.

### Phase B — pick the token

```bash
# one command: classify all candidates, join live WunderTrading spreads, rank:
./scripts/token_screen.py hyperliquid --symbols BTC,ETH,SOL,HYPE --interval 1h \
  --wun-exchange HYPERLIQUID_SWAP
```

The screener ranks candidates by regime quality (trend strength, overheat,
spread) and prints the top pick's config skeleton. Under the hood: filter by
account (which exchange has balance), spread (`(ask−bid)/last` — skip >
~0.1%), and liquidity (prefer majors), then pick the one matching a strategy
archetype you can run — a clean `trend_up` BTC beats a choppy favorite alt.
See [examples/2026-09-04-btc-trendup.md](examples/2026-09-04-btc-trendup.md)
for a full worked run.

### Phase C — read market conditions

```bash
./scripts/market_regime.py hyperliquid BTC --interval 1h        # human summary
./scripts/market_regime.py binance BTCUSDT --interval 1h --market futures --json
```

The classifier (stdlib-only, public exchange APIs) computes EMA20/50/200,
ATR14%, ADX14, RSI14, Bollinger-width percentile, Δ24h/Δ7d and returns one of
`trend_up`, `trend_down`, `chop_high_volatility`, `squeeze`, `neutral`, plus a
ready-to-adapt config skeleton. WunderTrading has **no candles endpoint** —
OHLCV comes from the exchange public API (script does it) or this workspace's
tvcli (`./tvcli fetch --symbol BINANCE:BTCUSDT --tf 60 --limit 300`).

### Phase D — build the config

Map the regime through the matrix (full reasoning + sizing math in
[references/strategy-playbook.md](references/strategy-playbook.md)):

| Regime | Strategy | Key config |
|---|---|---|
| `trend_up` | classic LONG | TP ladder 2/4/8% (40/30/30) vs entry · SL 3% · BE move at +2% · trail 4%→2% |
| `trend_down` | classic SHORT (futures only; spot = FLAT) | mirror of trend_up |
| `chop_high_volatility` | DCA LONG | 6 orders · 2.5% step · ×1.4 vol · ×1.5 step growth · TP 1.5% vs **average_price** · wide/no SL |
| `squeeze` | range limit at band edge, or WAIT | limit + TTL ≤ 1d · TP 2% · SL 3% |
| `neutral` | flat preferred; else probe DCA | 4 orders · 2% step · TP 1.5% average_price · SL 8% |

**DCA sizing math is mandatory:** the chop ladder (6 × ×1.4) can commit
~16.3× `amountPerTrade` — cap so total ≤ 50% of profile balance. The
`leverage` field is reference-only (not applied at the exchange). `clientId`
(32–64 chars, `^[~=-a-zA-Z0-9]+$`) makes creates idempotent.

### Phase E — pre-flight, then deploy

```
CHECKLIST (all must be true before place_strategy_trade):
  1. User explicitly confirmed THIS trade: pair, side, size, config.   ← non-negotiable
  2. pairCode resolved from get_exchange_markets (not hand-written).
  3. profilesCodes from get_api_profiles; profile active with balance.
  4. Sizing math done: total worst-case commitment ≤ 50% of balance.
  5. Reliability ladder respected: paper (demo profile) → probe 5–10% → scale.
  6. If editing instead: get_strategy read, strategyGroupType honored
     (classic: NO extraOrder* fields; dca: placeConditionalOrdersOnExchange ignored).
```

`place_strategy_trade` **opens a real position immediately** (market entry)
or parks a limit order (with `timeToLive` 5–20160 min).

### Phase F — monitor & adapt

`get_live_strategies` to watch; `get_strategy` for detail (and mandatory
before `edit_trade_strategy`); `get_strategy_orders_history` for fills (keyed
by `profileStrategyId`); `edit_trade_strategy` to tighten trails / move
stops; `place_strategy_swing` to flip on exit (futures, no DCA);
`close_strategy_market` to exit; `cancel_strategy` to abort unentered.
Re-run the classifier when triggers fire (regime decay, full DCA ladder,
squeeze breakout, funding extremes) — details in the playbook.

### Phase G — prove reliability (the loop)

1. `export_strategies_history` → download `exportFileUrl` dataset
   (`get_strategies_history` is currently broken server-side — always export).
2. Group closed strategies by config archetype; compute win rate, profit
   factor, expectancy, max drawdown, panic-exit count.
3. Pass bar: ≥ 30 samples, PF ≥ 1.3 (DCA win rate ≥ 60% / trend ≥ 40% with
   R ≥ 1.5), drawdown survivable. Scale only then; kill on PF < 1.0 over the
   last 20 samples.

## Safety rails

- **Never execute without explicit user confirmation of the exact trade.**
  The tools themselves demand this; treat "set up a strategy" as prepare-only.
- Read-only tools (`get_*`, `export_*`) are always safe.
- DCA without sizing math is the classic account-killer — do the arithmetic.
- Spot accounts cannot short; `trend_down` on spot = stay flat.
- Rate limits: 1200 tokens/min per key, 400 req/10 s per IP (5-min ban if
  breached) — watch `RateLimit-Remaining` in loops.

## Deeper references

| File | Contents |
|---|---|
| [references/mcp-tools.md](references/mcp-tools.md) | All 15 MCP tools, full schemas, live-verified quirks, curl transport (also `scripts/wt_httpx.py mcp` httpx) |
| [references/rest-api.md](references/rest-api.md) | HMAC signing recipe, endpoint list, rate limits, gotchas (also `scripts/wt_httpx.py open_api` httpx) |
| [references/strategy-playbook.md](references/strategy-playbook.md) | Token selection, regime classification, config matrix + sizing math, reliability ladder, adaptation triggers |
| [references/grid-bot.md](references/grid-bot.md) | Grid bot mechanics + session web API (`browser-debug/docs/wt/grid-bot-api.md`); use `scripts/wt_browser.py` (httpx+CDP browser fetch, `wt-grid.mjs` parity) or `scripts/wt_httpx.py session` best-effort |
| [scripts/market_regime.py](scripts/market_regime.py) | Executable regime classifier → config skeleton |
| [scripts/token_screen.py](scripts/token_screen.py) | Candidate ranking across tokens (+ live spreads via MCP) → top pick + config |
| [scripts/universe_screen.py](scripts/universe_screen.py) | Full-universe liquidity-filtered screener with configurable scan presets (`scan_presets.json`: grid-neutral, grid-directional, trend-classic, squeeze, all) → ranked candidates with spreads |
| [scripts/grid_config.py](scripts/grid_config.py) | Per-symbol config generator — web-UI Grid-bot config + API-reachable `place_strategy_trade` DCA/classic payload (`--send --yes` to submit) |
| [scripts/wtclient/](scripts/wtclient/) | **Importable Python package** — validated models + typed clients for every surface (REST/MCP/session/grid/market/browser). Use `from wtclient import WunderTrading` |
| [scripts/wt_httpx.py](scripts/wt_httpx.py) | Thin CLI shim over `wtclient` (same command shapes as before): HMAC `/open_api` + MCP `:2083` without browser, `session` best-effort, `grid` browser/raw, `market` browser/raw |
| [scripts/wt_browser.py](scripts/wt_browser.py) | `httpx` **with** CloakBrowser (CDP `Runtime.evaluate` fetch-in-page) — Python port of `browser-debug/wt-grid.mjs`/`wt-bots.mjs`; the reliable grid-bot `create/list/stop/restart/...` without Node (kept for `grid_config.py` parity) |
| [scripts/reliability.py](scripts/reliability.py) | Phase G reliability report — export closed history, score archetypes (win rate / PF / expectancy) |
| [examples/2026-09-04-btc-trendup.md](examples/2026-09-04-btc-trendup.md) | Complete worked run (Phase A–D) with real numbers — use as a template |

Official docs: `wundertrading.com/docs/mcp`, `/docs/rest-api`,
`/docs/rest-api/auth`, `wundertrading.com/en/ai-trading-bot`.
