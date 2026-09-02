# TradingView network protocol

This documents the private WebSocket protocol TradingView's chart page uses
to stream quotes, create chart sessions, resolve symbols, attach studies, and
deliver study data (`du`). It was reconstructed from two sources:

- the reference Go client in the tvcli repo
  (`pkg/tradingview/protocol.go`, `client.go`, `chart.go`, `study.go`,
  `auth/auth.go`), and
- live capture via `bdg tv ws` (in-page `WebSocket` wrapper) on a real
  logged-in browser session.

## Transport

### Frame format

Every WebSocket text frame is one or more packets:

```
~m~<byte-length-of-payload>~m~<payload-json>
```

Multiple `~m~` packets can be concatenated in one frame. A packet like
`~m~214~m~{"m":"chart_create_session","p":["cs_123"]}` carries a 214-byte
JSON payload. The parser: find first `~m~`, read the length, take that many
bytes, repeat.

### Heartbeat

The server pushes `~h~<seconds>` frames, and the client echoes them back
(`~h~<n>` is the "cleaner" string in `protocol.go`). Echoing is what keeps
the connection alive; the go client re-sends exactly what it received.

### Endpoints

| Who | URL |
| --- | --- |
| Go client | `wss://<server>.tradingview.com/socket.io/websocket?from=chart&type=chart` |
| Browser (observed live) | `wss://data.tradingview.com/socket.io/websocket?from=chart%2F<chartId>%2F&date=<ISO>&type=chart&auth=sessionid` |

The browser encodes the chart id in the `from` query param
(`chart/<chartId>/`, URL-encoded) and appends `date=<ISO-8601>` and
`auth=sessionid`. Both are socket.io's EIO v3 transport under the hood.

**Side channels (browser only):** after the main socket opens, the page also
opens two push-stream connections used for notifications/timeline events —
not for chart data:

- `wss://pushstream.tradingview.com/message-pipe-ws/private_feed`
- `wss://pushstream.tradingview.com/message-pipe-ws/public`

Live capture showed both with zero frames during a normal chart session;
the main data socket carried all protocol traffic.

## Authentication

### HTTP side

The authenticated session is carried by three cookies
(`sessionid`, `sessionid_sign`, `device_t`). The chart page embeds a
`"auth_token":"<token>"` JSON field, which the client scrapes and forwards
over the WS as its very first message.

Go reference flow (`pkg/tradingview/auth/auth.go`):

1. `GenCookies(session, signature, deviceT)` builds
   `sessionid=<s>;sessionid_sign=<sig>;device_t=<d>`.
2. `FetchToken(...)` GETs `https://www.tradingview.com/` with those cookies
   and regex-scrapes `"auth_token":"([^"]+)"`.
3. `device_t` is required: without it the scrape fails and the client falls
   back to an `unauthorized_user_token`, which authenticates as anonymous
   and triggers **strict study limits** on free accounts (see
   [studies-and-indicators.md](studies-and-indicators.md)).

The Go client distinguishes two failure modes explicitly
(`AuthInfo.Authenticated` vs plan/limits): *cookies expired* and
*study limit reached*.

### WebSocket side

First client message: `set_auth_token` with the scraped token. Live capture
shows this happens before anything else; the token payload was 853 bytes on
the observed session. **Never print this value** — `bdg tv ws` reports only
its length.

Anonymous fallback: `set_auth_token` with `"unauthorized_user_token"` →
0 user studies allowed.

## Chart session lifecycle

Observed live client→server sequence on page load (with the go client's
simpler flow noted):

1. `set_auth_token` — authenticate the socket.
2. `set_locale` — UI locale (go sends this too).
3. `chart_create_session` `[chart_session_id]` — open a chart session.
   Browser: `["cs_<id>"]`; go: `["cs_<id>"]`. (On reload the browser tears
   down the replaced session with `chart_delete_session`; two
   `chart_create_session` calls may be observed across a reconnect.)
4. `quote_create_session` `[quote_session_id]` — open a quote session.
5. `quote_set_fields` `[fields...]` — subscribe to quote fields
   (e.g. `lp` = last price).
6. `quote_add_symbols` `[quote_session_id, "<symbol>"]` — subscribe symbols.
7. `switch_timezone` — browser sends this early (timezone of the session).
8. `resolve_symbol` `[chart_session_id, symbol_id, "="+json]` — resolve the
   symbol on the chart. The third element is `"="` concatenated with the
   JSON of `{symbol, adjustment:"splits"}` (and, in the browser, extra keys
   like `currency-id`). Observed browser symbol ids: `sds_sym_1` (main
   symbol), `sds_sym_2` (`INTERNAL:SEASONALS`), `ss_1` (the `s1` series
   re-resolve).
9. `create_series` `[chart_session_id, "$prices", "s1", series_id, timeframe, calcRange]`
   — attach the price series. Go: `calcRange` is either an int bar count or
   `["bar_count", to, range]`. Browser: `"sds_1"` in place of `"$prices"`,
   and `p[3]` carries a series-suffix id.
10. `modify_series` `[chart_session_id, "$prices", "s1", series_id, timeframe, ""]`
    — subsequent timeframe changes reuse the series id.
    **Verified live (chart dvv4N29P, 2h→15m→2h):** the browser sends
    `modify_series` with a per-change series-instance id that INCREMENTS
    (`create_series` used `s1`, the first change used `s2`, the next `s3`,
    …) and the numeric resolution in the 5th element (`"15"`, `"120"`; daily
    and above use `"1D"/"1W"/"1M"`). Server replies `series_loading` →
    `series_completed` → `timescale_update` with the new bars. A timeframe
    switch generates NO HTTP traffic — it is pure WebSocket (verified with
    an in-page XHR/fetch probe during the switch; only a telemetry POST
    fires). The UI's internal call path is the symbol source's
    `setInterval(resolution)` → `setSymbolParams({interval})` → this message.
11. `create_study` — attach a study (see below).
    **Symbol change (verified live, chart dvv4N29P, BTCUSDT→XAUUSD):** the
    browser sends, in order: `quote_remove_symbols` (old symbol) →
    `quote_add_symbols` (new symbol as `=<json-descriptor>`,
    `{"adjustment":"splits","currency-id":"USD","session":"regular",
    "symbol":"PEPPERSTONE:XAUUSD"}`) → `resolve_symbol` with a NEW
    incrementing symbol id (`sds_sym_1` initial, then `sds_sym_3`,
    `sds_sym_4`, …) → `modify_series [chartSession,"sds_1",newSeriesInstance,
    newSymbolId,resolution,""]` → `quote_add_symbols` (plain ticker). Server
    replies `symbol_resolved` → `series_loading` → `series_completed` →
    `timescale_update`. Pure WebSocket — no HTTP for the change itself.
    Programmatic: `window._exposed_chartWidgetCollection.setSymbol(sym)`
    (full, updates toolbar too); `mainSeries()._symbolSourceWV._value
    .setSymbol(sym)` is data-only (toolbar stays on the old text).
12. `request_more_tickmarks`, `quote_fast_symbols`, `quote_hibernate_all`,
    `set_future_tickmarks_mode`, `create_pointset` — browser-only
    bookkeeping messages observed live (tick data, quote batching,
    futures roll dates, volume-profile point sets). The go client never
    sends these; they are not required to run studies.

Server→client during setup:

- `symbol_resolved` — symbol resolution succeeded (go triggers
  `OnSymbolLoaded`).
- `series_loading` / `series_completed` — price series streaming.
- `qsd` — quote symbol data pushes (price/volume updates per subscribed
  symbol).
- `study_loading` / `study_completed` — study lifecycle.
- `du` (a.k.a. `timescale_update`) — study/price data updates.
- `study_error` — study failed; `p[3]` carries the message.

Teardown (browser, on reload/leave): `remove_study` `[chart_session_id, study_id]`,
`remove_series`, `chart_delete_session` `[chart_session_id]`.

## create_study payload

```
create_study [chart_session_id, study_id, "st1", "$prices"|"sds_1", studyType, inputs]
```

- `study_id` — client-generated (`st<counter>` in go; browser uses
  `st1`-style ids for transient studies and persistent ids like `E7gFVY`
  for saved layouts).
- `studyType` — e.g. `Script@tv-scripting-101!` for arbitrary Pine,
  `StrategyScript$STD;RSI%1Strategy@tv-scripting` for the RSI Strategy,
  `Dividends@tv-basicstudies-276`, `BarSetContinuousRollDates@tv-corestudies-44`.
- `inputs` — map of parameter name → `{v: value, f: isFake, t: type}`
  (see [studies-and-indicators.md](studies-and-indicators.md)).
- The 4th element names the data stream the study attaches to: go uses
  `"$prices"`, the browser uses `"sds_1"` (the resolved series' data-source
  id).

## modify_study — changing a study's inputs

Verified live (chart dvv4N29P, RSI Strategy `E7gFVY`, input `in_0` 14→7):

```
modify_study [chart_session_id, study_id, study_instance_id, {FULL inputs map}]
```

- `study_instance_id` — increments per modification (`st1`, `st4`, `st5`, …).
- `inputs` — the COMPLETE input map (every input, not just the change), in
  the same `{name: {v, f, t}}` encoding `create_study` uses. The UI's
  `setInputValues()` sends exactly this.
- Server replies `study_loading` → `du` with the fully recomputed study
  (for a strategy: the entire performance report + trades, tens of KB).
- The pane redraws — the change reflects visually (verified: 0.3% of chart
  pixels changed in a before/after screenshot diff).

## du / timescale_update — study data

Shape (both names are the same message; the browser sends `du`):

```
du [chart_session_id, { <series-or-study-id>: { st:[...], ns:{ d:"<json>" }, t:"s1_st1" } }]
```

- `p[1]` is a map keyed by data-source id — one entry per updated
  price series, study, or pointset.
- Each entry: `st` = status/load flags array, `ns` = node state containing
  `d`, a JSON string whose payload is either `graphicsCmds`-style drawing
  commands or, for strategies, a strategy report.
- `graphicsCmds` shape (verified live, `bdg tv ws --full`):
  ```
  {"graphicsCmds":{"create":{"<draw-type>":[{"data":[{...item...}]}]}}}
  ```
  Item fields depend on draw type: `vertlines`/`horizlines` carry
  `{id, index, startPrice, endPrice, extendTop, extendBottom}`;
  `dwglabels` carry `{t, x, y}`; `dwglines`/`dwgboxes` carry `{x1, y1, x2, y2}`.
  This is the shape the go client's `processGraphics` consumes — and it is
  DIFFERENT from the browser frontend's `_graphics._primitivesCollection[*].
  _primitivesDataById` (`x1/y1/x2/y2/ext/st/ci`, read by `bdg tv study
  graphics`). See [bdg-tv-guide.md](bdg-tv-guide.md) for the three-shape
  comparison table.
- `t` identifies the source type, e.g. `"s1_st1"`.

The go client (`study.go` `onData`) matches
`msgType ∈ {timescale_update, du}`, reads `data[1][studyID]`, and processes
that study's entry. Live capture on chart `dvv4N29P` showed `du` keys:
`sds_1`, `sds_2`, `pointset_1`, plus one key per attached study id.

## Historical data (OHLCV) — create_series + request_more_data

Verified live (chart `dvv4N29P`, plus the Go client). Historical bars travel
only over the WebSocket — there is no HTTP endpoint for OHLCV history (the
chart page's only HTTP data traffic is `data.tradingview.com/ping` +
telemetry).

**Initial load — the most recent N bars.** One `create_series` call:

```
create_series [chartSession, seriesDataSource, "s1", symbolId, resolution, N, ""]
```

- Browser (exact, captured): `["cs_XXX","sds_1","s1","sds_sym_1","120",300,""]`
  — `seriesDataSource="sds_1"`, `symbolId="sds_sym_1"` (same id as
  `resolve_symbol`), `resolution="120"` (numeric minutes), `N=300`, trailing
  `""`.
- Go client (equivalent, works): `["cs_XXX","$prices","s1","ser_1","120",N]` —
  no trailing empty element needed.

Server replies `series_loading` → `symbol_resolved` → `timescale_update` →
`series_completed`. The bars ride `timescale_update`/`du` under the series
data-source key, shape:

```
{ "s": [ { "i": <index>, "v": [time, open, high, low, close, volume] } ] }
```

**Deep history (backfill) — `request_more_data`.** Scrolling back in time
makes the browser send, repeatedly:

```
request_more_data [chartSession, seriesDataSource, count]   // count ≈ 50..80 per scroll
```

Each call triggers exactly one `series_loading` → `timescale_update`/`du`
(older bars) → `series_completed`. It is repeatable to walk arbitrarily far
back. `count` is the number of ADDITIONAL bars to prepend before the earliest
bar currently loaded. Bars arrive with negative `i` indices (offsets before the
initial window); they dedupe by timestamp.

**Server-side caps (free tier, observed):** a single `create_series` with a
huge `N` returns ~5–6k bars (e.g. 1h BINANCE:BTCUSDT → 5632, 5m → 5949,
1D → 3294) — the full available history for that symbol/timeframe on the
account, not a fixed universal number. `request_more_data` backfill reaches the
same boundary; once no more bars exist it keeps answering `series_completed`
without new data.

The Go client implements this in `ChartSession.RequestMoreData(count)` +
`service.FetchOHLCVBarsDeep` (`tvcli fetch --deep N` / `--all`).

## Differences: go client vs browser

| Aspect | Go client | Browser |
| --- | --- | --- |
| WS URL | `?from=chart&type=chart` | `?from=chart/<id>/&date=…&type=chart&auth=sessionid` |
| Series data-source id | `"$prices"` | `"sds_1"` |
| `chart_create_session` params | `[id]` | `[id, ""]` (extra empty element) |
| Extra messages | none | `switch_timezone`, `request_more_tickmarks`, `quote_fast_symbols`, `quote_hibernate_all`, `set_future_tickmarks_mode`, `create_pointset` |
| Push-stream sockets | none | `private_feed` + `public` |

Bottom line: the go client implements the minimal protocol sufficient to
drive studies on a chart; the browser sends additional traffic that is not
required for studies to work.