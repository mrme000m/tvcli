# bdg TV guide — using the TradingView-specialized commands

This is the agent-facing manual for the TradingView specialization added to
the local bdg build in `browser-debug/bdg`.

## Which binary to run

The local, TradingView-specialized build is:

```
node ./bdg/dist/index.js <command>
```

`bdg` on PATH may resolve to a *global* install (a global npm install)
that predates the `tv` command group — always use the local path above
(or link it). All commands talk to the same bdg daemon, which must be
attached to a TradingView chart tab.

## Commands

### `bdg tv chart` — what is on this chart

Flags: `-j/--json` (raw JSON result).

```
$ bdg tv chart
TradingView chart

url:         https://www.tradingview.com/chart/dvv4N29P/
title:       XAUUSD 4,604.80 ▲ +1.9% XAUUSD 2H
chartId:     dvv4N29P
mainSeries:  _seriesId (XAUUSD · Pepperstone, 2h)
dataSources: 12
```

Use first to confirm you are attached to the right chart and to get the
chart id + main symbol.

### `bdg tv studies` — list studies

Flags: `-j/--json`.

```
$ bdg tv studies
6 studies on https://www.tradingview.com/chart/dvv4N29P/:

  E7gFVY | pine [strategy] | RSI Strategy — RSI Strategy (pine: StrategyScript$STD;RSI%1Strategy@tv-scripting)
  ESD$TV_DIVIDENDS | event | Dividends — Dividends (pine: Dividends@tv-basicstudies)
  ESD$TV_SPLITS | event | Splits — Splits (pine: Splits@tv-basicstudies)
  ESD$TV_EARNINGS | event | Earnings — Earnings (pine: Earnings@tv-basicstudies)
  ESD$TV_ROLLDATES | event | Dates calculator for continuous — Dates calculator for continuous (pine: BarSetContinuousRollDates@tv-corestudies)
  5bRAOh | other | Fixed range volume profile
```

Use to check the free-tier study cap (2 user studies; ESD$ event overlays
don't count — see [studies-and-indicators.md](studies-and-indicators.md)).

### `bdg tv drawings` — list drawings + layer capabilities

Flags: `-j/--json`.

```
$ bdg tv drawings
0 drawing(s) on https://www.tradingview.com/chart/dvv4N29P/:

  (no user drawings — create a trendline/shape in the UI to populate)

Drawing layer:
lineToolSynchronizer: present (drawings autosave via REST layout service)
executeActionById: available (chart actions incl. drawing tools)
```

### `bdg tv ws` — capture the WS protocol

Flags: `-s/--seconds <n>` (default 8), `--no-reload`, `--full`
(include raw frame payloads), `-j/--json`.

What it does: installs a `WebSocket` wrapper via
`Page.addScriptToEvaluateOnNewDocument` → reloads the page (the data socket
reconnects through the wrapper; the saved layout restores from the URL) →
waits `n` seconds → parses the `~m~` frames in-page → reports per-connection
traffic, the handshake summary, and the OUT/IN message flow → removes the
probe and clears the capture.

```
$ bdg tv ws --seconds 7
TradingView WS capture: 3 connection(s)

[0] open | out 66 | in 149 | wss://data.tradingview.com/socket.io/websocket?from=chart%2Fdvv4N29P%2F...
[1] open | out 0 | in 0 | wss://pushstream.tradingview.com/message-pipe-ws/private_feed
[2] open | out 0 | in 0 | wss://pushstream.tradingview.com/message-pipe-ws/public

Handshake summary
set_auth_token: 853 chars
chart_create_session: cs_IIulGxXtqnl8, cs_sWrNtuYBWtMT
resolve_symbol: sds_sym_1 -> ={"adjustment":"splits",..., sds_sym_2 -> INTERNAL:SEASONALS, ss_1 -> PEPPERSTONE:XAUUSD
create_study: E7gFVY (StrategyScript@tv-scripting-101!), IKwFXa (Dividends@tv-basicstudies-276), ...
study_completed: E7gFVY, IKwFXa, ...
remove_study: st1
du series keys: sds_1, E7gFVY, ..., pointset_1
```

Security: the auth token is never printed — only its byte length. Keep it
that way in anything you build on top.

`--no-reload` skips the reload step. Since the probe only runs on *new*
documents, this is useful when the page will navigate by itself during the
capture window, or when you must not disturb the current view.

### `bdg tv study` — add any script with custom input values, read values

Flags: `-j/--json`, and for `add`: `-i/--inputs '<json>'` (default `{}`) and
`-p/--pine <id>`; for `values`/`graphics`: `-f/--filter <name>`.

This is the **frontend-facing counterpart** to the go client's study runs:
the tvcli Go client drives studies headlessly over the
WebSocket API, while these commands drive the LIVE chart widget so the
analysis is visible on the chart. Subcommands:

- `bdg tv study add "<name>" [--inputs '{"length": 21}']` — add an
  indicator/strategy/event overlay **by display name** with custom input
  overrides. Uses `createStudy(name, false, false, [{id, value}])` — the same
  path the community MCP servers (`atilaahmettaner-tradingview-mcp`,
  `tradingview-mcp`) attempted, verified live here.
- `bdg tv study add "<name>" --pine "USER;…|PUB;…" [--inputs '{"in_15": 30}']`
  — add **any saved/public Pine script by id** via the descriptor
  `createStudy({type:"pine", pineId, pineVersion:"1.0"})` (no indicators
  dialog needed). Input overrides are then applied through
  `getInputValues()`/`setInputValues()`, matched case-insensitively against
  each input's id **or** title.
- `bdg tv study inputs <entityId>` — list a study's CURRENT input ids/values
  (the authoritative in-page `in_N` mapping — see the offset caveat below).
- `bdg tv study values [-f <name>]` — read plot metadata + last row buffer of
  every study on the chart.
- `bdg tv study graphics [-f <name>]` — read Pine-drawn graphics
  (line.new/label.new/box.new/table.new) per study.
- `bdg tv study remove <entityId>` — remove a study/entity by id (from
  `tv studies`).

```
$ bdg tv study add "Dividends"
Added study → entity id: ESD$TV_DIVIDENDS

$ bdg tv study add "SMC" --pine "PUB;6daafb2cabe6419d98ae25229d2327f8" \
    --inputs '{"in_15": 30}'
Added study → entity id: 2JKf75
inputs applied: in_15=30

$ bdg tv study graphics -f "Smart Money"
1 study with Pine graphics:
f42ds0 | Smart Money Concepts [LUX]
lines (326): dwglines#14 [lines] x1 1 y1 2656.1 x2 3 y2 2656.1 …
labels (329): …
boxes (8): dwgboxes#3 [boxes] x1 897 y1 4489.41 x2 900 y2 4450.63 …

$ bdg tv study remove 2JKf75
Removed study.
```

Verified live (chart `dvv4N29P`, free tier): the SMC script added by pine id,
with `--inputs '{"in_15": 30}'` reported `inputs applied: in_15=30` and the
swing-length input confirmed changed in the model (metaInfo defval 50 →
override 30). `tv study graphics` read **326 lines / 329 labels / 8 boxes**
from the LuxAlgo SMC script — the Pine-graphics read the community MCP
servers implemented but never verified end-to-end.

**Input-apply vs graphics caveat (verified live):** applying inputs through the
exposed surface's `setInputValues()` works (the study recomputes, plot values
keep flowing) but **can stall Pine-graphics materialization** on heavy scripts
— after `--inputs`, `tv study graphics` may report nothing for that study even
after 45s+. Graphics render normally on a pristine add (no `--inputs`). If you
need BOTH custom inputs AND graphics from the same chart-side study, prefer
the go client's WS `create_study` path (`tvcli run --input in_N=…`), which
sends inputs at creation, or apply inputs and read `tv study values` for
confirmation instead of graphics.

Caveats (all verified live):

- **The exposed `getAllStudies()` only lists user studies** — event overlays
  and volume profiles are invisible there. Always detect additions through the
  model `dataSources()` (the `tv studies` enumeration).
- **`createStudy` is async**: the new dataSource registers a beat later, so the
  add script polls the multiplicity for up to 4s before reporting.
- **Event overlays (ESD$*) are not deduped** — adding "Dividends" repeatedly
  creates duplicate sources with the same id. Remove extras the same way.
- **Free tier: 2 user studies per chart.** Adding a third user indicator
  silently no-ops on the exposed surface; remove one first (or add an event
  overlay, which does not count).
- **Name resolution** follows the indicators dialog's display names (e.g.
  "Relative Strength Index", "Dividends"). A name that does not resolve
  reports no new entity id. For saved/public scripts use `--pine <id>`.
- **Input ids are offset in-page — and differ between clients**: the created
  study's `in_N` numbering can differ from the `tvcli inputs` listing (hidden
  inputs shift the index). Verified live on the SMC script: the swing-length
  input is `in_17` in `tvcli inputs` / `go --input` (works), but `in_15` on
  the browser side (`tv study inputs`). Read the authoritative map with
  `tv study inputs <entityId>` before overrides; the add command reports
  unmatched keys so you can correct them.
- **Strategies/event studies have no per-bar row buffer** — their data travels
  in `du` frames (see [network-protocol.md](network-protocol.md)); `values`
  then reports the plot metadata only.
- **Pine graphics** read via `_graphics._primitivesCollection[*]._primitivesDataById`
  (not the `.value()` shape the MCPs assumed) — primitives carry x/y
  coordinates, ext/style/color indices, and label text.

### `bdg network websockets` — WS connections via the network collector

Flags: `-v/--verbose` (show frame payloads), `--last <n>` (frames per
connection in verbose mode, default 20), `-j/--json`.

Reads the bdg network store (CDP `Network.webSocketCreated` etc.) through
the `worker_websockets` worker command and lists connections, frame counts,
and (verbose) payloads. This is a passive view that does not reload the
page.

Verified live (chart `dvv4N29P`): after a reload under the session it
listed 6 connections — the 3 pre-reload sockets (closed) and the 3
post-reload sockets (open): the data socket plus the two pushstream
message-pipes, with `~m~…~m~` heartbeats visible in `-v` mode.

Known caveats:

- The daemon must run a build that includes the live-connection store fix
  (`src/telemetry/network.ts`) and the `worker_websockets` command —
  restart the daemon if it predates them; an old daemon reports an empty
  list with a hint.
- CDP only reports sockets opened *after* `Network.enable` (i.e. after bdg
  attached). Reload the page under the active session to see the TV data
  socket; sockets that were replaced by the reload show as `closed`.
- Frame payloads are capped per frame/connection (truncation markers show
  the total size).

## Workflows

**Check auth health.** `bdg tv ws -s 4` and read the handshake summary:
`set_auth_token` with a real length + studies listed = authenticated;
`unauthorized_user_token` = cookie/`device_t` problem (see
[network-protocol.md](network-protocol.md)).

**See which studies a chart runs.** `bdg tv studies` — ids, kinds, and
`@library-build` versions. Combine with `bdg tv ws` to see the
`create_study` inputs the layout sends.

**Understand the protocol for a new client.** `bdg tv ws --full -s 10` and
compare OUT/IN with the go reference client's minimal flow (documented in
[network-protocol.md](network-protocol.md) and
[message-catalog.md](message-catalog.md)) — the browser's extra messages
are all optional for driving studies.

**Watch drawing saves.** Drawings persist over REST (see
[drawings.md](drawings.md)); watch `bdg network` (HTTP collection) for the
layout-save POST while a drawing is added in the UI.

**See what an interaction changed on screen.** `bdg dom screenshot a.png` →
run the interaction → `bdg dom screenshot b.png` → `bdg vision compare a.png
b.png` (Mistral vision diff; see [../vision.md](../vision.md)).

**Extend bdg.** In-page probes live in `src/commands/tv/scripts.ts` (IIFE
template literals), commands in `src/commands/tv/{ws,studies,drawings,chart}.ts`,
formatters in `src/ui/formatters/tv.ts`. Worker-side commands follow the
`worker_websockets` pattern: schema in `src/ipc/protocol/commands.ts`,
handler in `src/daemon/worker/commandRegistry.ts`, client helper in
`src/ipc/client.ts` (`sendCommand`). Rebuild with `npm run build`. Keep
probes free of backticks/`${` (template-literal embedding), cap payload
sizes, and never emit token/cookie values.

## Session checklist

- bdg daemon attached to the TradingView tab (page-level CDP target —
  survives reloads).
- Local dist build up to date (`npm run build`).
- For `network websockets`: daemon restarted *after* the
  `src/telemetry/network.ts` fix.
- Free tier: ≤2 user studies per chart; remove one before adding another.