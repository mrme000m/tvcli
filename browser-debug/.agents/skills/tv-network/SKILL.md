---
name: tv-network
description: >
  Investigate, analyze, and extend TradingView's network-level API and chart
  frontend using the local bdg build (`browser-debug/bdg`)
  with its `tv` command group, and drive the live chart with custom input
  values of ANY script (PineScript indicator/strategy/event overlay). Use when
  debugging or extending the Go tvcli network client, capturing the
  chart-data WebSocket
  protocol (set_auth_token, chart_create_session, resolve_symbol,
  create_study, du), listing/reading/adding/removing studies on the chart, or
  building frontend tools that show agent analysis on the chart. Complements
  the `tvcli` (headless Pine runs) and `tv-scout` (visual confluence) skills.
license: MIT
metadata:
  author: dsh-agent
  version: "1.0"
---

# tv-network — TradingView network API + chart frontend investigation

Two surfaces of tradingview.com matter for market analysis:

| Surface | What it is | Tools |
|---|---|---|
| Network API | Private WebSocket chart-data protocol (`~m~<len>~m~<json>` frames) + auth cookies | `bdg tv ws`, `bdg network websockets`, the tvcli Go client |
| Chart frontend | The live chart widget (`window.TradingViewApi`, `window._exposed_chartWidgetCollection`) | `bdg tv studies`, `bdg tv study`, `bdg tv drawings`, `bdg tv chart` |

Documented knowledge (read before acting): `bdg/docs/tv/` — `network-protocol.md`
(framing, endpoints, auth, session lifecycle), `message-catalog.md` (every wire
message), `studies-and-indicators.md` (study types, Pine input encoding
`{v,f,t}`, free-tier limits), `drawings.md` (line-tool layer), `bdg-tv-guide.md`
(command manual). The reference Go client is the tvcli repo
(`pkg/tradingview/*.go`, `cmd/tvcli`).

## Which binary

Always use the local build (the `tv` command group lives there):

```bash
node ./bdg/dist/index.js <command>
# or, from the bdg dir:  npm run build  (after editing src/)
```

`bdg` on PATH may resolve to a global install WITHOUT the `tv` group. All
commands talk to the same daemon, which must be attached to a TradingView
chart tab (page-level CDP target — survives reloads).

## Core commands

```bash
# Network API — capture the WS protocol (reloads the page; layout restores)
node …/bdg/dist/index.js tv ws -s 8              # handshake + OUT/IN flow summary
node …/bdg/dist/index.js tv ws --full -s 10 -j   # raw frame payloads (deep inspect)
node …/bdg/dist/index.js network websockets -j   # passive WS connections (no reload)

# Chart frontend — what is on the chart
node …/bdg/dist/index.js tv chart                # URL, chartId, main series, dataSources
node …/bdg/dist/index.js tv studies              # studies: pine/event/other + pineIds
node …/bdg/dist/index.js tv drawings             # drawings + drawing-layer capabilities

# Frontend tool — add ANY script with custom input values, read values/graphics
node …/bdg/dist/index.js tv study add "Relative Strength Index" --inputs '{"length": 21}'
node …/bdg/dist/index.js tv study add "SMC" --pine "PUB;6daafb2cabe6419d98ae25229d2327f8" \
  --inputs '{"in_15": 30}'            # any saved/public script by Pine id
# NOTE: the pine descriptor uses pineVersion:"last" (NOT "1.0") —
# createStudy({type:"pine", pineId, pineVersion:"last"}) resolves correctly;
# pineVersion:"1.0" causes "Cannot get study" + "Failed to create study" errors.
node …/bdg/dist/index.js tv study inputs <entityId>   # authoritative in-page in_N map
node …/bdg/dist/index.js tv study values -f RSI       # plot metadata + last row
node …/bdg/dist/index.js tv study graphics -f SMC     # Pine-drawn lines/labels/boxes
node …/bdg/dist/index.js tv study remove <entityId>
```

## Investigation workflows

**Understand the protocol for a new client.** `bdg tv ws --full -s 10`, compare
OUT/IN with the go client's minimal flow (documented in `network-protocol.md`).
The browser's extra messages (`switch_timezone`, `request_more_tickmarks`,
`quote_fast_symbols`, …) are optional for driving studies; the go client
implements the minimal sufficient set.

**Debug the go client.** When `tvcli run` misbehaves, capture the browser's
`create_study` payload with `bdg tv ws` and compare studyType + input encoding
(`{v,f,t}`) with what `go/pkg/tradingview` sends. `bdg tv studies` shows the
live `@library-build` studyType strings — do not hardcode build numbers.
Cross-client input ids diverge: `go --input` uses `tvcli inputs` `in_N`
(verified `in_17=30` works); the browser in-page uses offset `in_N` (`tv
study inputs`). Use `--debug` to trace wire messages and `--raw`/`--raw-out`
to dump full graphics primitives headlessly.

**Check auth health.** `bdg tv ws -s 4`: `set_auth_token` with a real length +
studies listed = authenticated; `unauthorized_user_token` = cookie/`device_t`
problem (see `network-protocol.md`). Never print the token value — length only.

**Show analysis on the chart with custom inputs.** The Go client runs studies
headlessly; the live-chart counterpart is `bdg tv study add` (same
`createStudy(name, false, false, [{id, value}])` path the community MCP
servers attempted, plus `--pine <id>` for ANY saved/public script via the
`{type:"pine", pineId, pineVersion}` descriptor). Free tier caps at 2 user
studies per chart — remove one first or add an event overlay (does not count).
Event overlays are NOT deduped: adding "Dividends" repeatedly stacks
duplicates with the same id.

**Input ids are offset in-page — and differ between clients.** Three id
spaces exist for the same Pine input:
- `tvcli inputs <pineId>` / `go --input` → canonical `in_N` (e.g. swing
  length of the SMC script = `in_17`; verified `--input in_17=30` works).
- Browser in-page (`tv study inputs <entityId>`) → offset `in_N` (the same
  input = `in_15`, shifted by hidden inputs). Use this map for
  `tv study add --inputs`.
- Display names / Pine `inline` ids also resolve in both (go `SetOption`
  matches id, `in_` shorthand, inline id, then display name case-insensitively).

The add command reports unmatched keys so you can correct them. When a key
works in one client but not the other, the id spaces have diverged — re-read
that client's authoritative map. `tvcli input-map <pineId> --browser-entity
<entityId>` prints both maps side by side (go canonical vs live browser
`in_N`) with a per-input match column — run it before cross-client overrides.

**Screenshot the indicator visuals.** Two tvcli commands bridge to bdg's CDP:
- `tvcli screenshot [out.png]` — capture the current chart (viewport by
  default; `--full` full page, `--selector CSS` element, `--scroll` before).
- `tvcli visual <name|pineId> --pine <id> --inputs '<json>' --out shot.png`
  — ONE command for the whole "show my custom-input study on the chart"
  workflow: `bdg tv study add` with the input overrides → wait `--settle` ms
  for Pine graphics to render → `bdg dom screenshot` → remove the study
  (unless `--keep`). Verified live: SMC by pine id with `{"in_17":30}` →
  `inputs applied: in_17=30`, 262 KB screenshot, study removed after.

**Read Pine graphics.** `tv study graphics` reads
`_graphics._primitivesCollection[*]._primitivesDataById` — the REAL shape (the
community MCP servers assumed a `.value()` shape that does not exist). Verified
live: 326 lines / 329 labels / 8 boxes from the LuxAlgo SMC script.

**setInputValues stalls graphics (verified).** Applying inputs via the exposed
surface's `setInputValues()` recomputes the study (plot values keep flowing)
but can stall Pine-graphics materialization on heavy scripts — graphics render
normally only on a pristine add. When both custom inputs AND graphics are
needed, prefer the go client's WS `create_study` path (`tvcli run
--input in_N=…`), which sends inputs at creation time.

**Change the timeframe programmatically.** Verified with a WS probe + HTTP
capture during 2h→15m→2h switches (chart dvv4N29P):
- The switch is **pure WebSocket — zero XHR** (only a `telemetry.tradingview.com`
  POST fires; layout autosave rides the WS/state layer, not per-change HTTP).
- The one wire message: `{"m":"modify_series","p":[chartSessionId,"sds_1",
  seriesInstanceId,"sds_sym_1",resolution,""]}` where `seriesInstanceId`
  increments per change (`s1` = initial `create_series`, then `s2`, `s3`, …)
  and `resolution` is numeric minutes (`"15"`, `"120"`) or `"1D"/"1W"/"1M"`.
  Server replies `series_loading` → `series_completed` → `timescale_update`.
- The UI's own call path (what a dropdown selection invokes):
  `chartWidget._modelWV._value.m_model.mainSeries()._symbolSourceWV._value
  .setInterval('<resolution>')` → `setSymbolParams({interval})` → the same
  `modify_series` on the wire, AND updates toolbar/state.
- Lower-level data-only alternative: `chartApi.modifySeries(sessionId,"sds_1",
  nextSeriesId,"sds_sym_1",resolution,undefined,null,noop)` sends the same
  wire message but does NOT update the UI state (toolbar stays on the old
  interval — verified).
- `tvcli tf <timeframe>` wraps the model `setInterval` path (with dropdown
  UI-automation fallback); `tv tf 15m` / `tv tf 1h` / `tv tf 4h` / `tv tf 1D`.

**Change the symbol programmatically.** Same approach, verified live
(BTCUSDT → BINANCE:XAUUSD → PEPPERSTONE:XAUUSD on chart dvv4N29P):
- Pure WebSocket again — no XHR for the change itself. The client sends, in
  order: `quote_remove_symbols` (old symbol) → `quote_add_symbols` (new symbol
  as `=<json-descriptor>`, e.g. `{"adjustment":"splits","currency-id":"USD",
  "session":"regular","symbol":"PEPPERSTONE:XAUUSD"}`) → `resolve_symbol` with
  a NEW incrementing symbol id (`sds_sym_1` initial, then `sds_sym_3`,
  `sds_sym_4`, …) → `modify_series [chartSession,"sds_1",newSeriesInstance,
  newSymbolId,resolution,""]` → `quote_add_symbols` (plain ticker). Server
  replies `symbol_resolved` → `series_loading` → `series_completed` →
  `timescale_update`.
- Two programmatic levels (verified):
  - LOW (data only): `mainSeries()._symbolSourceWV._value.setSymbol(sym)` —
    resolves + streams the new symbol, but the toolbar button stays on the
    OLD text.
  - HIGH (full, what the UI symbol-search flow invokes):
    `window._exposed_chartWidgetCollection.setSymbol(sym)` — same wire
    messages AND updates toolbar button, legend, and document title.
- `tvcli sym <symbol>` wraps the HIGH path (auto-exchange for plain tickers:
  `BTCUSDT` → `BINANCE:BTCUSDT`); `tv sym BTCUSDT` / `tv sym OANDA:XAUUSD` /
  `tv sym NASDAQ:AAPL` (resolves to the listed exchange, e.g. BATS:AAPL).

**List studies / read inputs / change inputs (verified live, RSI Strategy
E7gFVY on chart dvv4N29P):**
- Listing studies (`dataSources()`) and reading a study's inputs
  (`getStudyById(id).getInputValues()`) is an in-page model read — NO network
  traffic. The inputs map is the same shape `create_study` carried on the
  wire: `{"in_0":{"v":14,"f":true,"t":"integer"}, ...}`.
- CHANGING an input sends ONE WS message (no XHR):
  `{"m":"modify_study","p":[chartSessionId, studyId, studyInstanceId, {FULL
  inputs map with the new values}]}` — the study instance id increments per
  change (`st1`, `st4`, `st5`, …). Server replies `study_loading` → `du`
  (a large recomputed payload; for a strategy, the full performance report +
  trades). Verified: RSI length `in_0` 14→7 → `modify_study` → 24KB `du` →
  0.30% of chart pixels changed in a before/after screenshot diff.
- Programmatic path (the exposed surface, verified): `chart.getStudyById(id)
  .getInputValues()` to read, then mutate values and `study.setInputValues(
  cur)` to apply — sends the same `modify_study` and recomputes. (Caveat:
  on heavy Pine-graphics scripts setInputValues can stall graphics
  materialization; use the go client's WS `create_study` path when both
  custom inputs AND graphics are needed.)
- `tvcli study` wraps it: `tv study list`, `tv study inputs <entityId>`,
  `tv study set <entityId> --inputs '{"in_0": 7}' --before a.png --after b.png`
  (screenshots verify the visual reflection; pixel-diff with magick).

**Strategies are NOT indicators — read the backtest, not plots (verified).**
A strategy (e.g. RSI Strategy `E7gFVY`) produces its output in the Strategy
Tester bottom panel: the performance summary (Net PnL, Profit Factor, Max
Drawdown, win rate, trade count) plus the buy/sell trade list. It has NO
per-bar plot row buffer (`tv study values` reports "no row buffer — strategy/
event studies report via du frames"). The buy/sell "signals" are the strategy
report's executed trades (`e.tp`: `le` = long entry / buy, `se` = short entry /
sell). In-page the report lives on the study dataSource's `_reportData`
(`performance.all` + `trades[]`) — the same payload the server recomputes via
`du` after each `modify_study`; reading it costs NO network.
- Verified parameter sweep (XAUUSD 2h, `in_0` = RSI length): 5 → −1643/0.75/220
  trades, 9 → +99/1.03/82, 14 → +299/1.14/40 (sweet spot), 21 → −519/0.75/12.
  `tv study report <id> --json` gives machine-readable numbers for the sweep.
- Opening the panel in the UI: the bottom tab bar's "Open panel" button
  (aria-label) reveals the Strategy Tester; the strategy tab is
  `[aria-label="Open strategy report"]`.

**Strategy-vs-indicator search (`tvcli scan`) — build robust sweep corpora.**
`tv scan <query...>` searches TradingView public scripts and classifies each as
strategy or indicator from the search API's `extra.kind` ("strategy" vs
"study"). `--verify` fetches each script's metaInfo (pine-facade /translate/)
and cross-checks the AUTHORITATIVE `pine.isStrategy` flag + input/plot counts,
flagging mismatches — search labels can be stale, metaInfo is truth.
- `tv scan RSI --type strategy --limit 10` — seed a strategy sweep corpus
- `tv scan "RSI,MACD,EMA" --type indicator --verify --limit 15` — indicators
  with input/plot counts for input-change tests
- Verified: "RSI" search → 4 strategies (Bollinger+RSI, MACD Long, …);
  "supertrend" → SuperTrend STRATEGY etc.; indicator verify confirmed
  kinds + counts (e.g. CM_Ultimate RSI MTF: 13 inputs / 11 plots).
- Feed results into `tv run <pineId> --signals` (headless backtest + buy/sell
  events) or `tv study set/report` (live chart). Robustness reality-check:
  of 3 scanned strategies on XAUUSD 1h, 2 returned full backtests (SuperTrend
  231 trades/36% win; Bollinger+RSI 16 trades/62.5% win) and 1 (MACD Long)
  returned 0 periods on the free tier — scanning more samples and probing
  headlessly surfaces which scripts are actually usable.

## Verified live facts (chart dvv4N29P, free tier)

- WS endpoint: `wss://data.tradingview.com/socket.io/websocket?from=chart/<id>/&date=<ISO>&type=chart&auth=sessionid`; plus two pushstream message-pipes.
- `du` and `timescale_update` are the same data-update message; the browser sends `du`.
- The exposed `getAllStudies()` lists only user studies — event overlays and
  volume profiles are invisible there; use the model `dataSources()` (what
  `tv studies` / `tv study` read).
- `createStudy` registers the new dataSource asynchronously (the `study add`
  script polls up to 6s, with a pine-id fallback lookup when the poll misses a
  slow registration); strategies/event studies have no per-bar row buffer
  (their data travels in `du` frames).
- Reloading the page (`Page.reload`) restores the saved layout from the URL —
  a clean way to undo transient study/drawing experiments.

## Graphics: three different shapes (verified live)

The same Pine graphics appear in THREE incompatible shapes depending on where
you read them — do not assume they are interchangeable:

| Where | Shape | Notes |
|---|---|---|
| go `tvcli run … --signals` | `extracted.graphicCounts` (`alert/box/line` counts) + `events[]`/`levels[]` extracted from label text | Verified: SMC script → 347 alerts / 5 boxes / 351 lines → 30 events (`label_CHoCH`, `label_BOS` at prices) + 50 levels (`dwglabels_EQH` resistance, `dwgboxes_top/bottom`) |
| go `tvcli run … --raw` | `graphic` map with FULL primitives: `dwglabels` `{t,x,y,yl,ci}`, `dwgboxes` `{x1,y1,x2,y2,ex,st,w}`, `dwglines` `{x1,y1,x2,y2}` | Verified: 319 labels / 319 lines / 5 boxes in the raw dump — the headless counterpart to `tv study graphics`. `--raw-out <file>` writes it to disk |
| go `du` wire (`ns.d` JSON) | `graphicsCmds.create.<type>[].data[]` with `{startPrice,endPrice,index}` / `{t,x,y}` / `{x1,y1,x2,y2}` | go's `processGraphics` stores per type; `--signals` reduces to counts, `--raw` surfaces everything |
| browser frontend (`tv study graphics`) | `_graphics._primitivesCollection[*]._primitivesDataById` with `{x1,y1,x2,y2,ext,st,ci}` | Verified: 326 lines / 329 labels / 8 boxes from SMC; includes label text and coordinates |

## Extending bdg

In-page probes live in `src/commands/tv/scripts.ts` (plain ES5 IIFEs, no
template-literal backticks inside; JSON-serializable return values). Commands
in `src/commands/tv/{ws,studies,study,drawings,chart}.ts`, formatters in
`src/ui/formatters/tv.ts`. Worker-side commands follow the `worker_websockets`
pattern: schema in `src/ipc/protocol/commands.ts`, handler in
`src/daemon/worker/commandRegistry.ts`, client helper in `src/ipc/client.ts`.
Rebuild with `npm run build` in the bdg dir; run `npm test` before finishing.
Never emit token/cookie values in output or docs.

## Rules

- Never print `sessionid`/`sessionid_sign`/`device_t`/auth_token values.
- Free tier: ≤2 user studies per chart; remove before adding.
- Don't hardcode `@library-build` numbers — capture them live.
- The daemon must be attached to the TradingView tab; a stale daemon predating
  the `worker_websockets`/live-connection fix reports an empty websocket list
  with a hint — restart it.