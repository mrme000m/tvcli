# wtclient

Modular, validated, type-safe Python client for WunderTrading — with
**discovery** and **debug** machinery baked in.

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
- **Discovery baked in** — every transport can be wrapped with a
  `Recorder` that captures redacted request/response rows into a queryable
  `EndpointCatalog`. A `Probe` tries any `(method, path)` across all
  surfaces and reports which one answered. Zero overhead when disabled.
- **Debug baked in** — `WT_DEBUG=1` auto-installs a redacted stderr logger
  + dump-on-failure writer. `wtclient.debug.trace(wun)` wraps every
  transport on a `WunderTrading` instance for end-to-end capture.
- **Extendable** — adding a new WunderTrading surface means adding a
  transport (`transport/`), optional models (`models/`), a client
  (`clients/`), and wiring it into the facade; the rest is shared.

## Layout

```
wtclient/
├── cli.py            # CLI (backward-compatible with wt_httpx.py + discover/debug)
├── clients/          # typed high-level clients per surface
│   ├── bots.py       # signal/dca/mn/mp bots (session-auth)
│   ├── client.py     # WunderTrading facade
│   ├── exchanges.py  # my-exchanges profiles + account limits (session-auth)
│   ├── grid.py       # grid bots
│   ├── market.py     # public market data
│   ├── mcp.py        # MCP tools
│   └── open_api.py   # HMAC REST
├── models/           # pydantic request models + enums + geometry helpers
├── transport/        # one transport per surface (httpx, MCP, session, market, browser)
├── config.py         # origins, UA fingerprint, time helpers
├── curl.py           # redacted curl-equivalent rendering
├── debug.py          # state, logger, dump-on-failure, trace() wrapper
├── discovery.py      # Recorder, EndpointCatalog, Probe, surface_index
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
# from this directory
python3 -m unittest discover -s wtclient/tests -t . -v
```

The package is also installable as a wheel:

```bash
cd .agents/skills/wundertrading/scripts
pip install -e .                # editable install (uses pyproject.toml)
pip install -e .[browser]       # add websockets for the browser transport
pip install -e .[dev]           # add pytest, respx, etc.
```

## Library use

```python
from wtclient import WunderTrading

wun = WunderTrading()                 # reads provisioned secret files
wun.rest.exchanges()                  # HMAC REST (no browser)
wun.mcp.supported_exchanges()         # MCP (no browser)
wun.mcp.api_profiles(limit=5)
wun.mcp.export_strategies_history(statuses=["completed"])
wun.bots.list_active("dca")           # cabinet signal/dca/mn/mp (browser)

# Browser-backed surfaces (needs a running CloakBrowser tab)
wun = WunderTrading(browser=True)
wun.grid.list()
wun.grid.analyze("HYPERLIQUID_SWAP:191")
wun.market.ohlc_last("HYPERLIQUID_SWAP:191", timeframe=15)

# Exchange-profile management (my-exchanges, session-auth)
wun.exchanges.list_profiles()            # -> [Profile(...)] (paper + live)
wun.exchanges.account_limits()           # -> raw plan-limits payload
result = wun.exchanges.create_paper_profile("demo-hype", "HYPERLIQUID")
# -> {"created": bool, "already_exists": bool, "status": int|None,
#     "message": str|None, "violations": [...], "response": raw}  (never raises)
wun.exchanges.ensure_paper_profiles({"hyperliquid": ["demo-hype"]})
# -> {"ok": bool, "venues": {...}, "created": ["hyperliquid/demo-hype"],
#     "errors": [...]}  (idempotent; never mutates wrong-shape profiles)

# Deletion (verified live): DELETE master-api-profile/{code}/delete
wun.exchanges.delete_profile("47ca341d5a01c1df91b8b9ed")   # hex resource.code
wun.exchanges.delete_profile_by_name("stale-paper")        # lookup by name;
# paper_only=True (default) refuses NON-paper profiles — a live exchange
# connection is never deleted by a name match.
```

Paper profiles need **no real exchange keys** — the WT UI itself submits
random 32-hex placeholders for `api`/`secret` and `paper_profile_body()`
generates the same locally. `BINANCE` paper resolves to `BINANCE_FUTURES`
(USDT-M); Binance spot has no paper mode. There is no way to set a paper
balance (fixed $10k demo). **Account cap: only 2 paper trading accounts
are allowed** — a third create returns HTTP 400
`"Limit reached. Only 2 Paper trading accounts are allowed."`; free a
stale slot with `delete_profile_by_name` first.

### Discovery

```python
from wtclient import Recorder, EndpointCatalog

rec = Recorder()
# wrap any transport you want to observe (or call wtclient.debug.trace(wun))
# … drive UI / run a loop …
catalog = rec.catalog()              # grouped by surface/method/path
for endpoint in catalog:
    print(endpoint.surface, endpoint.method, endpoint.path,
          endpoint.calls, endpoint.success_rate)

# Probe an arbitrary endpoint across all surfaces
from wtclient import Probe
probe = Probe(wun)
results = probe.try_method("GET", "/open_api/api_profiles?limit=5")
for r in results:
    print(r.surface, r.status, f"{r.elapsed_ms:.0f}ms", r.error or "")
```

### Debug

```bash
# Auto-install logging + dump-on-failure via env
WT_DEBUG=1 python3 -c "from wtclient import WunderTrading; WunderTrading().mcp.api_profiles()"
# … failing requests now dump full request + response (secrets redacted) to
# /tmp/wt-debug-dumps/<surface>-<ts>-<uuid>.json
WT_DEBUG_DUMP_DIR=/var/log/wtclient python3 ...
```

```python
# Programmatic instrumentation — wraps every transport on `wun`
import wtclient.debug as dbg
rec = dbg.trace(wun)                 # attach a recorder + logger
# … drive UI …
catalog = rec.catalog()              # what was actually called
dbg.unwrap(wun)                      # restore original transports
```

### Validated payloads

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
# Backward-compatible with wt_httpx.py
python3 wt_httpx.py open_api GET /open_api/exchanges
python3 wt_httpx.py mcp get_exchange_markets --params '{"exchanges":["HYPERLIQUID_SWAP"]}'
python3 wt_httpx.py grid list --transport browser
python3 wt_httpx.py grid analyze HYPERLIQUID_SWAP:191 --transport browser
python3 wt_httpx.py grid create cfg.json --transport browser --grid-market derivative
python3 wt_httpx.py grid stop <code> --transport browser
python3 wt_httpx.py market /supported-markets --transport browser

# Exchange profiles + plan limits (dry run by default; --execute to write)
python3 wt_httpx.py exchanges profiles --transport browser
python3 wt_httpx.py exchanges limits --transport browser
python3 wt_httpx.py exchanges create-paper demo-hype --family HYPERLIQUID
python3 wt_httpx.py exchanges create-paper demo-hype --family HYPERLIQUID --execute
python3 wt_httpx.py exchanges ensure --spec '{"hyperliquid":["demo-hype"]}' --execute

# Discovery + debug
python3 wt_httpx.py discover surfaces               # known endpoint index
python3 wt_httpx.py discover probe GET /open_api/api_profiles?limit=5
python3 wt_httpx.py debug enable --level DEBUG
python3 wt_httpx.py debug status
python3 wt_httpx.py debug list-dumps --limit 10
```

Run `python3 wt_httpx.py --help` for the full surface list, or
`python3 -m wtclient.cli discover --help` for the discovery subcommands.

## Extending

1. Add a transport in `transport/` implementing `request()` -> `Response`.
2. Add request models in `models/` using the enums/percent helpers from
   `models/common.py`.
3. Add a client in `clients/` subclassing `BaseClient`.
4. Export it from `clients/__init__.py` and `__init__.py`.
5. Add an offline test under `tests/` for the new validation/encoding logic.

To add a new surface to the **discovery** catalog:

1. Add the endpoint patterns to `PUBLIC_SURFACES` in `discovery.py`.
2. If the transport needs probing logic, add a branch to `Probe.try_method`.

## Adoption outside this repo

Other projects that want to drive WunderTrading can either:

1. **Use the library directly**: `pip install -e /path/to/scripts`
   (or `pip install wtclient` once published). Then:

   ```python
   from wtclient import WunderTrading
   wun = WunderTrading()
   ```

2. **Drive the CLI** with subprocess:

   ```python
   import subprocess
   r = subprocess.run(["python3", "-m", "wtclient.cli",
                       "mcp", "get_api_profiles",
                       "--params", '{"limit":5}'], capture_output=True, text=True)
   ```

The grid-autonomy daemon (`agents/grid-autonomy/execution/wt_library.py`)
uses option (1) — an in-process adapter exposing the same public surface as
the old subprocess wrappers but with one less fork per call and integrated
discovery/debug. The reference adapter is short enough (~250 lines) to be
copied into any other wt project.

## Verified live (2026-09-04 + later)

- `rest.exchanges()`, `rest.markets(...)`, `rest.api_profiles(...)` — OK.
- `mcp.supported_exchanges()`, `mcp.api_profiles(...)`, `mcp.live_strategies(...)` — OK.
- `grid list/analyze/positions/presets/profiles` via browser transport — OK.
- `market /supported-markets` via browser transport — OK.
- Raw `session`/`market` surfaces correctly map Cloudflare 403 to
  `WunCloudflareError` with remediation.
- `discovery.EndpointCatalog` aggregates 100+ observed endpoints into
  ~30 unique `(surface, method, path)` rows.
- `debug.trace(wun)` wraps every transport with no measurable latency when
  no requests are recorded; recorded rows are redacted (api keys, CSRF,
  cookies) before they touch disk.
