# TradingView backend API & frontend — findings

Documented knowledge about TradingView's **backend API** (the private
WebSocket protocol used by the chart page) and **frontend drawing
capabilities**, captured live with bdg's `tv` command group and cross-checked
against the reference client in the tvcli repo.

## What is documented where

| File | Contents |
| --- | --- |
| [network-protocol.md](network-protocol.md) | WS framing (`~m~…~m~`), endpoints, the full authenticated connection + chart-session lifecycle, symbol/series/study setup, `du` data delivery, teardown. Differences between the go client and the real browser page. |
| [message-catalog.md](message-catalog.md) | Every message type observed on the wire, with direction, purpose, payload shape, and where it appears in the session lifecycle. |
| [studies-and-indicators.md](studies-and-indicators.md) | Study taxonomy (`pine` / `event` / `other`), `studyType` naming conventions, Pine input encoding `{v,f,t}`, the `du` data structure, free-tier limits. |
| [drawings.md](drawings.md) | How drawing objects ("line tools") work in the frontend: internal ChartWidget API surface, the line-tools synchronizer, REST-based persistence, and what agents can and cannot do from page JS. |
| [bdg-tv-guide.md](bdg-tv-guide.md) | How agents use the specialized `bdg tv …` / `bdg network websockets` commands: flags, real output examples, workflows, caveats. |

## How this knowledge was obtained

1. **Static analysis** of the reference implementation in
   the tvcli repo (`pkg/tradingview/*.go`,
   `pkg/tradingview/auth/auth.go`), which speaks the same protocol the
   browser uses.
2. **Live capture** on a logged-in free-tier session (chart
   `dvv4N29P`, symbol `PEPPERSTONE:XAUUSD` 2h) using the
   `bdg tv ws` in-page WebSocket probe: a `WebSocket` constructor wrapper is
   injected via `Page.addScriptToEvaluateOnNewDocument`, the page is
   reloaded, and all `~m~` frames in both directions are parsed and
   summarized.
3. **In-page introspection** of the TradingView chart widget
   (`window._exposed_chartWidgetCollection.activeChartWidget._value`) via
   `bdg tv chart`, `bdg tv studies`, `bdg tv drawings`.

## Conventions & security

- Message payloads are shown in the same order/format the wire uses, but
  **auth tokens, session cookies, and `device_t` values are never included**
  in docs or command output. `bdg tv ws` prints only the *length* of the
  `set_auth_token` payload.
- `du` / `timescale_update` are the same message type; the browser sends the
  `du` shorthand, the go client matches both.
- Time is UTC; all observations below are from 2026-08-21/22 unless noted.

## Related commands

```
bdg tv ws        # capture + summarize the WS protocol (reloads the page)
bdg tv studies   # list studies on the active chart
bdg tv study     # add any script with custom input values / read values / remove
bdg tv drawings  # list drawings + drawing-layer capabilities
bdg tv chart     # one-line summary of the active chart
bdg network websockets   # WS connections seen by the bdg network collector
bdg vision describe/compare   # Mistral-vision UI description & change diff
```

See [bdg-tv-guide.md](bdg-tv-guide.md) for flags and full examples.