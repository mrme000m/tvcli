# wtclient

Modular, validated, type-safe Python client for WunderTrading. It replaces the
monolithic `scripts/wt_httpx.py` with small, focused modules and adds the Grid
bot surface that previously required `scripts/wt_browser.py`.

## Why

- **No browser when possible** — the HMAC `/open_api` REST and the `:2083/mcp`
  streamable-HTTP surfaces work over plain `httpx`. Only the Cloudflare-
  fingerprinted `/en/trader` and `:2087` surfaces need a browser, and even
  those have a best-effort raw replay (`SessionTransport` / `MarketTransport`)
  for when `cf_clearance` is fresh.
- **Type-safe payloads** — request models mirror the live MCP `tools/list`
  schemas plus the cross-field rules in `references/mcp-tools.md` and
  `references/grid-bot.md`. Invalid payloads fail before any HTTP call.
- **Common parts reused** — one `Response`, one query encoder, one HMAC
  signer, one curl redactor, one error hierarchy, one credential loader.
- **Extendable** — adding a new WunderTrading surface means adding a
  transport (`transport/`), optional models (`models/`), a client
  (`clients/`), and wiring it into the facade; the rest is shared.

## Layout

```
wtclient/
├── cli.py            # CLI (backward-compatible with wt_httpx.py)
├── clients/          # typed high-level clients per surface
├── models/           # pydantic request models + enums + geometry helpers
├── transport/        # one transport per surface (httpx, MCP, session, market, browser)
├── config.py         # origins, UA fingerprint, time helpers
├── curl.py           # redacted curl-equivalent rendering
├── errors.py         # WunError hierarchy
├── query.py          # comma-list query encoding, JSON body/arg helpers
├── response.py       # normalized Response + typed raise_for_status
├── secrets.py        # credential loading with precedence
└── tests/            # offline unit tests (python3 -m unittest)
```

## Install / requirements

Core dependencies: `httpx`, `pydantic`. The browser transport additionally
needs `websockets` and is imported lazily so the rest of the package works
without it.

```bash
cd .agents/skills/wundertrading/scripts
python3 -m unittest discover -s wtclient/tests -t . -v
```

## Library use

```python
from wtclient import WunderTrading

wun = WunderTrading()                 # reads provisioned secret files
wun.rest.exchanges()                  # HMAC REST (no browser)
wun.mcp.supported_exchanges()         # MCP (no browser)
wun.mcp.api_profiles(limit=5)
wun.mcp.export_strategies_history(statuses=["completed"])

# Browser-backed surfaces (needs a running CloakBrowser tab)
wun = WunderTrading(browser=True)
wun.grid.list()
wun.grid.analyze("HYPERLIQUID_SWAP:191")
wun.market.ohlc_last("HYPERLIQUID_SWAP:191", timeframe=15)
```

Validated payload example:

```python
from wtclient.models import PlaceStrategyTrade

trade = PlaceStrategyTrade.model_validate({
    "exchangeCode": "HYPERLIQUID_SWAP",
    "pairCode": "191",
    "profilesCodes": ["c629f5ba3a643a82137e7864"],
    "side": "long",
    "orderType": "market",
    "amountPerTrade": 50,
    "amountPerTradeType": "quote",
    "takeProfits": [
        {"priceDeviation": "2%", "portfolio": "50%"},
        {"priceDeviation": "4%", "portfolio": "50%"},
    ],
    "stopLoss": "3%",
})
trade.payload()   # canonical JSON-ready dict
```

## CLI

```bash
python3 wt_httpx.py open_api GET /open_api/exchanges
python3 wt_httpx.py mcp get_exchange_markets --params '{"exchanges":["HYPERLIQUID_SWAP"]}'
python3 wt_httpx.py grid list --transport browser
python3 wt_httpx.py grid analyze HYPERLIQUID_SWAP:191 --transport browser
python3 wt_httpx.py grid create cfg.json --transport browser --grid-market derivative
python3 wt_httpx.py grid stop <code> --transport browser
python3 wt_httpx.py market /supported-markets --transport browser
```

Run `python3 wt_httpx.py --help` for the full surface list.

## Extending

1. Add a transport in `transport/` implementing `request()` -> `Response`.
2. Add request models in `models/` using the enums/percent helpers from
   `models/common.py`.
3. Add a client in `clients/` subclassing `BaseClient`.
4. Export it from `clients/__init__.py` and `__init__.py`.
5. Add an offline test under `tests/` for the new validation/encoding logic.

## Verified live (2026-09-04)

- `rest.exchanges()`, `rest.markets(...)`, `rest.api_profiles(...)` — OK.
- `mcp.supported_exchanges()`, `mcp.api_profiles(...)`, `mcp.live_strategies(...)` — OK.
- `grid list/analyze/positions/presets/profiles` via browser transport — OK.
- `market /supported-markets` via browser transport — OK.
- Raw `session`/`market` surfaces correctly map Cloudflare 403 to
  `WunCloudflareError` with remediation.
