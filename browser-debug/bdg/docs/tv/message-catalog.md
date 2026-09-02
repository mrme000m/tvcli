# TradingView WS message catalog

Every message type observed on the chart data socket, with direction,
purpose, and payload shape. Sources: live `bdg tv ws` capture on chart
`dvv4N29P` (browser) and the reference client in
the tvcli Go client (annotated `go`).

Legend: `cs` = chart session id, `qs` = quote session id, `sid` = study id,
`ser` = series id, `sym` = symbol id, `"$prices"|"sds_1"` = data-source id of
the price series (go sends `$prices`, the browser sends `sds_1`).

## Client → server

### Auth & locale

| Message | Payload | Purpose |
| --- | --- | --- |
| `set_auth_token` | `[token]` | Authenticate the socket. MUST be first. Browser: token scraped from page HTML; anonymous fallback is the literal `"unauthorized_user_token"`. bdg reports only the payload *length*. |
| `set_locale` | `[locale]` | UI/formatting locale. |

### Chart session

| Message | Payload | Purpose |
| --- | --- | --- |
| `chart_create_session` | `[cs]` (browser: `[cs, ""]`) | Open a chart session. All later chart ops reference this id. |
| `chart_delete_session` | `[cs]` | Close the chart session. Sent by the browser when a layout is replaced/reloaded. |

### Quotes

| Message | Payload | Purpose |
| --- | --- | --- |
| `quote_create_session` | `[qs]` | Open a quote session (price streaming for the symbol list/watchlist). |
| `quote_set_fields` | `[field...]` | Subscribe to quote fields per symbol (e.g. `lp` last price). |
| `quote_add_symbols` | `[qs, sym]` | Subscribe one symbol to a quote session. |
| `quote_remove_symbols` | `[qs, sym]` | Unsubscribe (sent in bursts when the layout changes). |
| `quote_fast_symbols` | `[sym...]` | Batch subscribe (browser only; faster path used during layout restore). |
| `quote_hibernate_all` | — | Suspend all quote sessions (browser only; sent before layout switches). |
| `request_more_tickmarks` | — | Ask for extra tick marks on the series (browser only). |

### Symbol & series

| Message | Payload | Purpose |
| --- | --- | --- |
| `switch_timezone` | `[tz]` | Set session timezone (browser only). |
| `resolve_symbol` | `[cs, sym, "="+json]` | Resolve `sym`. The 3rd element is `"="` + JSON of `{symbol, adjustment:"splits"}` (browser adds `currency-id` etc.). Observed syms: `sds_sym_1`, `sds_sym_2` (`INTERNAL:SEASONALS`), `ss_1` (`PEPPERSTONE:XAUUSD`). |
| `create_series` | `[cs, "$prices"\|"sds_1", "s1", ser, timeframe, calcRange]` | Attach the price series. `calcRange`: int bar count (most-recent N bars), or `["bar_count", to, range]`. Browser adds a trailing `""`. |
| `modify_series` | `[cs, "$prices"\|"sds_1", "s1", ser, timeframe, ""]` | Change timeframe/range of an existing series. |
| `request_more_data` | `[cs, "$prices"\|"sds_1", count]` | **Backfill deep history.** Prepends `count` additional older bars before the earliest loaded bar. Repeatable; server replies `series_loading` → `du`/`timescale_update` → `series_completed` each time. |
| `remove_series` | `[cs, ser]` | Detach a series (browser teardown). |
| `set_future_tickmarks_mode` | — | Futures roll-dates mode (browser only). |

### Studies & pointsets

| Message | Payload | Purpose |
| --- | --- | --- |
| `create_study` | `[cs, sid, "st1", "$prices"\|"sds_1", studyType, inputs]` | Attach a study. See [studies-and-indicators.md](studies-and-indicators.md) for `studyType` and `inputs`. |
| `remove_study` | `[cs, sid]` | Detach a study (transient studies are removed right after restore). |
| `create_pointset` | `[cs, …]` | Create a point set (volume profile / price-range data), browser only. |

## Server → client

| Message | Payload | Purpose |
| --- | --- | --- |
| `~raw~` (frame) | socket.io session raw | Observed as the first inbound frame right after connect (socket.io `~raw~` packets are relayed as `~m~` JSON after the handshake). |
| `symbol_resolved` | `[cs, …]` | Symbol resolution completed; series setup may proceed. |
| `series_loading` | `[cs, …]` | Price series is streaming in (once per create_series and per request_more_data). |
| `series_completed` | `[cs, …]` | Price series load finished — all bars for that request have arrived. |
| `qsd` | quote data | Per-symbol quote updates for subscribed fields. |
| `du` / `timescale_update` | `[cs, {id: {st, ns:{d}, t}}]` | Data update keyed by source id (see [network-protocol.md](network-protocol.md)). `du` is the browser's shorthand; go matches both names. |
| `study_loading` | `[cs, sid, …]` | Study registered, computing. |
| `study_completed` | `[cs, sid, …]` | Study ready; plots/values start flowing via `du`. |
| `study_error` | `[cs, sid, …, msg]` | Study failed; go reads the message from `p[3]`. |

## Heartbeat (both directions)

`~h~<seconds>` — server pushes, client echoes byte-for-byte. No JSON
payload.

## Live sequence (observed, 2026-08-21)

Out: `set_auth_token → set_locale → chart_create_session →
quote_create_session → quote_set_fields → switch_timezone →
quote_create_session → quote_add_symbols → resolve_symbol → create_series →
quote_add_symbols → … → create_study (×6: layout restore incl. transient
Seasonals) → quote_hibernate_all → … → create_pointset → remove_study →
remove_series → chart_delete_session`.

In: `symbol_resolved`, `series_loading`, `qsd`, `du` (keys: `sds_1`,
`sds_2`, `pointset_1`, one per study id), `study_completed` per study.

Notable: the layout restore briefly creates a **Seasonals** study
(`st1`, `Seasonals@tv-basicstudies-238!`, resolving `INTERNAL:SEASONALS`),
marks it completed, then immediately removes it — a transient TV-internal
study, not a user study.