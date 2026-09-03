# TradingView Backend API (as used by tvcli)

This documents the **actual** TradingView backend that `tvcli` talks to, as
reverse-engineered and implemented in `pkg/pinefacade`, `pkg/tradingview`, and
`pkg/tradingview/auth`. All endpoints, message names, and cookie names below
were read from the current source — not from memory.

tvcli uses **three** backend surfaces:

1. **Pine Facade** — an HTTPS REST API for compiling, saving, fetching, and
   searching Pine scripts.
2. **The `data` WebSocket** — the realtime chart/study socket that runs a
   Pine study and streams OHLCV, graphics, and strategy reports.
3. **Cookie → auth_token scraping** — loading the chart page HTML with the
   session cookies to extract the `auth_token` the WebSocket needs.

All three are credential-parameterized (cookie header + username passed per
call), so the same code path serves one account or the whole
`accounts.json` pool.

---

## 1. Pine Facade (HTTP)

Base URL (config `PineFacadeURL`, env `PINE_FACADE_BASE_URL`):
```
https://pine-facade.tradingview.com/pine-facade
```
Client: `pkg/pinefacade.Client` (`NewClient(baseURL, userName, timeout,
WithProxy(...))`). Every method takes the cookie header (and uses `userName`
for `X-Userid`) so it is multi-account safe. Common request headers
(`baseHeaders`): `Cookie`, `Origin: https://www.tradingview.com`,
`Referer: https://www.tradingview.com/`, a desktop Safari `User-Agent`, and
`X-Requested-With: XMLHttpRequest` (plus `X-Userid: <username>` when set).

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/translate/{pineID}/{version}` | **Compiled IL + metaInfo** (inputs, plots, styles, palettes). This is what the WS study needs. `/get/` only returns source and causes server-side compile errors, so `Get()` always uses `/translate/`. |
| GET | `/get/{pineID}/{version}` | Human-readable Pine **source** + `scriptName`/`version`/`scriptAccess` (`GetSource`). |
| GET | `/versions/{pineID}` | Version list (parsed + semver-compared for "latest"). |
| GET | `/list?filter=saved` | The current user's saved scripts (`ListSaved`). |
| POST | `/translate_light?user_name=<u>&v=3` | **Compile** Pine source (multipart field `source`). |
| POST | `/save/new?name=<n>&user_name=<u>&allow_overwrite=true` | **Save new** private script (multipart `source`). Returns `pineId`/`scriptIdPart`. |
| POST | `/save/next/{pineID}?user_name=<u>` | Save next version of an existing script. |
| POST | `/delete/{pineID}?user_name=<u>` | Delete a script. |
| GET | search/public-library endpoint (`SearchPublicScripts`) | Search the public script library. |

### Pine ID namespaces (`pkg/pinefacade/access.go`)

The token before the first `;` classifies access (offline, always available):

| Prefix | Access | Notes |
|--------|--------|-------|
| `PUB;` | **public** — runs on any tier, including free | |
| `USER;` | **private** — owned account script; must appear in the user's `/list?filter=saved` or it 401s ("no source") | |
| `PRIVATE;`, `STD;`, `PRO;` | **invite-only** — server-gated, needs invitation | |
| *(none/unknown)* | treated as `public` so a working public script is never wrongly blocked | |

`GetScriptAccess` upgrades the offline classification with a live public
library search (`access` field: `public`/`private`; `type`: `study`/
`strategy`/`indicator`). `UserScripts`/`UserOwnsScript` are the ownership
precheck that gates private skills (and `/run-skill` private-script handling).

### Response parsing (`parseFetchResponse` / `extractSource`)

Source is found in priority: `source` > `scriptSource` >
`result.scriptSource` > base64-decoded `result.IL` (TradingView intermediate
language; URL-safe base64 with `-`/`_` remapped to `+`/`/`). metaInfo is read
from `result.metaInfo` (incl. `scriptIdPart`).

---

## 2. The `data` WebSocket

URL (config server `data`, `WithServer` override):
```
wss://data.tradingview.com/socket.io/websocket?from=chart&type=chart
```
Client: `pkg/tradingview.WSClient` (`NewClient(WithToken, WithSignature,
WithDeviceToken, WithProxy, WithDebug, WithServer)`). Framing is
socket.io-style: `~m~<len>~m~<json>` packets; `Protocol.ParseWSPacket` /
`FormatWSPacket` handle encode/decode.

### Auth handshake

On `Connect()` the client sends `set_auth_token` with:
- the `auth_token` scraped from the chart page (see §3), **or**
- `"unauthorized_user_token"` when no cookies are present.

Anonymous sessions are limited to **0 studies** — useful only for raw
OHLCV `fetch`, not for running indicators. A `session_id` handshake frame
from the server seeds the per-session dispatch map.

### Session types & lifecycle messages

| Message | Direction | Purpose |
|---------|-----------|---------|
| `set_auth_token` | → | authenticate the socket |
| `chart_create_session` | → | open a chart session (id) |
| `chart_delete_session` | → | close a chart session (frees indicator slots) |
| `resolve_symbol` | → | load a symbol with options: `timeframe`, `range` (bar count), `to` (unix-sec past-time anchor) |
| `create_series` / `modify_series` | → | create/modify the main OHLCV series (series id) |
| `request_more_data` | → | backfill older bars (`--deep`/`--all`) |
| `create_study` | → | run a Pine study (pineId/IL + inputs) |
| `remove_study` | → | remove a study (frees a slot) |
| `replay_create_session` / `replay_delete_session` | → | bar-replay sessions |
| `quote_create_session` / `quote_delete_session` | → | quote sessions |
| `symbol_resolved` | ← | symbol loaded ok |
| `timescale_update` / `du` | ← | **data updates**: OHLCV periods, study periods, graphics, strategy report |
| `study_completed` | ← | study finished computing |
| `study_error` | ← | study failed (incl. study-limit/timeout) |
| `symbol_error` / `series_error` / `critical_error` | ← | symbol/series failures |

Inbound frames are dispatched by `session_id` to the owning
`ChartSession`/`Study`/quote/replay handler. On `Close()`, the client sends
`*_delete_session` for every active session **before** closing the socket, so
TradingView releases indicator slots promptly (stale sessions from a dropped
connection otherwise block new ones).

### `to` / point-in-time anchoring

`resolve_symbol` accepts a `to` unix-seconds value; the chart window's **last
bar closes at `to`**, so studies compute over a historical, no-lookahead window
(the headless equivalent of bar replay). This is exposed on the CLI
(`fetch --to`, `run --to`) and on every server data endpoint (`/fetch`,
`/run`, `/run-skill`, `/hunt`).

---

## 3. Cookie → auth_token scraping

Package `pkg/tradingview/auth`. Cookies (from `.env` or an `accounts.json`
account):

| Env var(s) | Cookie | Required for |
|------------|--------|--------------|
| `SESSION` / `SESSION_ID` / `TV_SESSION` | `sessionid` | all authenticated calls |
| `SIGNATURE` / `SESSION_SIGN` / `TV_SIGNATURE` | `sessionid_sign` | all authenticated calls |
| `DEVICE_T` / `TV_DEVICE_T` | `device_t` | **study creation** (saving scripts) |
| `TV_USER` / `TV_USERNAME` | (username, not a cookie) | save/delete (`X-Userid`) |

> Cookies **must** be extracted from the `/chart/` page, not the home page —
> the `device_t` cookie is only present there.

`FetchAuthInfo(session, signature, location, deviceT, opts)` GETs
`https://www.tradingview.com/chart/` (default `location`) with the cookie
header, then regexes `"auth_token":"([^"]+)"` out of the HTML → the
`auth_token` used for the WS handshake. It also reports
`Authenticated`/`Pro`/`Plan`/`Username`. `FetchToken` is the convenience
wrapper returning just the token. `FetchMyCharts` reads
`https://www.tradingview.com/my-charts/?limit=N` for saved chart layouts.

`GenCookies(session, signature, deviceT)` builds the `Cookie` header string
used by both the Pine Facade client and the auth scraper.

---

## 4. Config defaults (`internal/config/config.go`)

| Env | Default | Meaning |
|-----|---------|---------|
| `PINE_FACADE_BASE_URL` | `https://pine-facade.tradingview.com/pine-facade` | Pine Facade base |
| `TV_BASE_URL` | `https://www.tradingview.com` | web base (auth scrape, layouts) |
| `TV_TIMEOUT_MS` | `120000` | HTTP/WS timeout |
| `TV_TIER` | `free` | subscription tier (caps bars/studies/concurrency) |
| `TV_PROXY` | *(none)* | SOCKS5/HTTP egress proxy for all WS+HTTP traffic |
| `TV_ACCOUNTS_FILE` | `accounts.json` | multi-account registry sidecar |
| `TV_ACCOUNT` / `--account` | registry default | active account override |

Tier limits (`internal/config/tiers.go`, free→ultimate): charts, indicators,
connections, bars, calc-timeout — the CLI auto-caps bars and cleans study
slots for `free` (2 indicators, 180d bars). See
[docs/TIER_LIMITS.md](TIER_LIMITS.md).

---

## 5. Putting it together: running a study

```
auth_token = auth.FetchAuthInfo(SESSION, SIGNATURE, "", DEVICE_T).Token
client  = tradingview.NewClient(WithToken(SESSION), WithSignature(SIGNATURE),
                                WithDeviceToken(DEVICE_T))
client.Connect()                         # set_auth_token(auth_token)
chart   = tradingview.NewChartSession(client)
chart.SetMarket("OANDA:XAUUSD", {"timeframe":"1H","range":180,"to":<unix>})
script  = pinefacade.Get(pineID, "last", cookie)   # compiled IL + metaInfo
study   = chart.Study(tradingview.NewPineIndicator({pineID, script.Source, metaInfo}))
# ... collect study.Periods() / study.Graphic() / study.StrategyReport()
chart.RemoveAllStudies(); chart.Delete(); client.Close()
```

`pkg/runner` adds retry-on-study-limit and schema-guided parsing;
`pkg/pipeline` does script-agnostic signal extraction (order blocks, FVGs,
levels); `pkg/skill` holds the 20 registered skills' per-script parsers;
`internal/server` wraps all of the above behind the 11 HTTP endpoints with a
per-account concurrency pool.
