---
name: tv-scout
description: >
  Progressively scout, instrument, and drive the live TradingView web chart so
  the agent can visually REPRESENT its market understanding for humans and
  CONFIRM it with screenshots (vision-model confluence). Use when working with
  the headful tvvisual channel (CloakBrowser + CDP at ../visual), when pushing
  Pine scripts (e.g. xau-scalp) onto the real chart, when validating chart
  state against tvcli-computed numbers, or when extending the recipe/scout
  knowledge base with newly discovered, verified UI capabilities.
license: MIT
metadata:
  author: ch99q
  version: "0.1"
---

# tv-scout — TradingView visual channel scouting & representation loop

Three complementary channels exist in this workspace:

| Channel | Where | Role |
|---------|-------|------|
| `tvcli` | repo root (`./tvcli`) | Headless numbers + Pine CRUD: `create`/`push`/`eval`/`run`, JSON signals. |
| `tvvisual` | `../visual` (`python3 -m tvvisual`) | Headful eyes + hands: real chart in stealth Chromium over CDP. |
| `bdg` | `bdg` CLI (`/Volumes/ExMac/code/tools/browser-debugger-cli`) | Standalone CDP client: attach to a RUNNING CloakBrowser (`--chrome-ws-url`) or launch its own headless Chrome — `cdp`/`dom`/`network` for independent debugging + cross-verification of tvvisual. |

**Division of labor (scouted 2026-08-20):** Pine source management is tvcli's
job — `tvcli create/push` uploads `xau-scalp.pine` (currently
`USER;ed4cf60ef3fb43f6b91565afe52a3a4b` = "XAU Scalp Engine v2"). The chart
adds it via the indicators dialog "My scripts" section
(`add_indicator_from_search`). Do NOT use `_pineEditorApi.open()` (server
error for user scripts) and do NOT drive the Monaco Pine editor by DOM
(fiber layout drifts; the toolbar button is a toggle).

`tvvisual` reads the same `.env` (pass `--env ../go/.env` from `visual/`).
Cookies must be valid; never print them.

**Account drift:** the persistent profile keeps the last logged-in account.
The `.env` account is `siner15` (verify: `tvcli check-auth --json`). The
recipe's `relogin` step forces the profile onto the `.env` account; `state()`
reports the logged-in `account` — always check it before trusting a run.

## The confluence workflow (represent + confirm)

**One command** (preferred — runs the whole loop and emits a drift report):

```bash
cd ../visual
python3 -m tvvisual confluence xau-scalp --env ../go/.env --tf 5
# -> {confluence: true|false,
#     metrics: [{metric, class: exact-closedbar|exact|approximate, source,
#               tvcli, chart, drift, within_tol}],
#     bar_alignment: {chart_closed_bar, tvcli_closed_bar, rechecks: []},
#     screenshot, params, tvcli: {bias, lastPrice, lastBarTime, opportunities}}
```

Or step by step:

1. **Compute** with tvcli: `./tvcli xau-scalp --symbol OANDA:XAUUSD --tf 5 --json --agent`
   → extract `composite`, `rsi`, `slLevel`, `tpLevel`, narrative.
2. **Represent** on the live chart with the recipe:
   ```bash
   cd ../visual
   python3 -m tvvisual recipe run xau-scalp \
     --env ../go/.env \
     --param '{"rsi": <value>, "composite": <value>, "timeframe": "5"}'
   ```
   The recipe opens the chart, relogins to the `.env` account, adds "XAU Scalp
   Engine v2" from My scripts, draws SL/TP/bias (pass `sl`, `tp`, `bias`
   params), hides chrome, zooms, screenshots to `visual/xau-scalp-latest.png`,
   and reads EXACT last-closed-bar plot values (`closed_bar_values`) + validates
   the drawn levels. Confluence is now EXACT for oscillators (SOLVED 2026-08-20,
   round 5): the chart's study row buffer `source.data()._items[n-2].value` =
   `[bar_time, plot_0..n]` is the last CLOSED bar — the same bar tvcli computes
   on. With bar alignment (tvcli `market.lastBarTime` == chart closed-bar time;
   `run_confluence` recomputes tvcli if a bar rolled over), verified drift is
   **+0.0000** for RSI/Composite/SL/TP (`confluence-exact-verified`). The old
   data-window reads tracked the LIVE forming bar (drifted) and are only a
   fallback now.
3. **Confirm visually**: read the screenshot with your vision capability
   (`read_image`). Check the pane shows the engine's plots (Composite, Signal
   histogram, SL/TP) consistent with the JSON narrative. Mismatch → scout the
   cause (study missing? compile error? wrong symbol?) and repair.
4. **Draw findings** for the human (optional): `python3 -m tvvisual draw level
   <price> --label "PDH"` / `draw zone <high> <low>` / `open ... --validate`.
5. **Snapshot the whole chart** (repeatable configuration):
   `python3 -m tvvisual recipe run chart-snapshot` exports the full layout JSON
   (symbol, resolution, panes, every study's inputs/overrides/styles, all
   drawings, scales) to `~/.tvvisual/scout/layouts/` plus a screenshot. Or
   `tvvisual layout export [--out f.json]`. Recipe-ordering rule: `relogin`
   reloads the chart and restores the account's saved layout — always put
   `relogin` BEFORE the `open(symbol, tf)` step.

## The progressive scouting loop (extend the system itself)

TradingView's web internals change without notice. The scout KB is how this
skill stays reliable — run it whenever a recipe fails, a selector is guessed,
or you need a capability that has no recipe yet.

Knowledge base: `~/.tvvisual/scout/` — `surface.json`, `dom.json`,
`recipes.json` (agent-promoted recipes), `probes.jsonl` (every observation),
`generated/` (codegen output). Packaged recipes: `visual/recipes/*.json`.

Loop:

1. **Scout the surface** — `python3 -m tvvisual scout surface --env ...`
   dumps `window.TradingViewApi` keys + chart-widget method tables.
   `scout dom` maps interactive elements (`data-name`, aria labels) by region.
   Compare with previous KB snapshots to detect UI drift.
2. **Probe a hypothesis** — `scout probe <name> --js '<expression>'`
   records a dated observation (success or failure) in `probes.jsonl`. Use
   probes to find new selectors/methods before writing a recipe.
3. **Compose a recipe** — a JSON step-list (see `visual/recipes/xau-scalp.json`
   for the schema: `params` + `steps[]` of `{action, args, required}`).
   Actions: open, timeframe, hide_chrome, zoom_bars, add_indicator, draw,
   pine_new/set/compile/smart_compile/errors/save, screenshot, validate,
   study_values, closed_bar_values, drawings_snapshot, drawings_restore,
   chart_restore, tables, lines, eval, sleep. Args support `$params.key`.
   Test it with `recipe run <name>`, then promote it:
   `scout save <file.json>` writes it into the KB store.
4. **Verify health** — `scout verify [name]` re-runs stored recipes and
   tracks `meta.runs` / `meta.failures` / `last_verified`. A recipe with
   failures: probe the failing step, fix the recipe, re-verify.
5. **Generate code** — once a recipe verifies cleanly, `recipe codegen <name>`
   emits a standalone Python script into `~/.tvvisual/scout/generated/`.
   That script is the reliable, repeatable, configurable artifact — import it,
   wrap it, or commit it back into `tvvisual/` when it proves stable.

## bdg — standalone CDP client (two modes)

**Mode A — attach to a RUNNING CloakBrowser (debug / cross-verify tvvisual).**
Launch the same stealth binary tvvisual uses, with a CDP port, on the logged-in
profile; then attach bdg to its page WebSocket. The repo's `browser-debug/launch.mjs`
wraps this with the robust container flags (`--ignore-gpu-blocklist`,
`--disable-dev-shm-usage`, anti-throttling) that make TradingView hydrate reliably
inside the Xvfb display:
```bash
node browser-debug/launch.mjs        # headful CloakBrowser on :9222 with robust defaults
WS=$(curl -s http://127.0.0.1:9222/json | python3 -c "import json,sys;print([p['webSocketDebuggerUrl'] for p in json.load(sys.stdin) if p.get('type')=='page'][0])")
bdg --chrome-ws-url "$WS" --no-headless -t 3600 "https://www.tradingview.com/chart/"
# then:
bdg cdp Runtime.evaluate --params '{"expression":"(/* TradingViewApi reads */)()","returnByValue":true}'
bdg network list --type XHR,Fetch -j
bdg dom eval '(/* expr */)()' ; bdg status ; bdg stop
```
If you use the bare binary directly, pass the same defaults to avoid a blank chart:
```bash
BIN=$(python3 -c "from cloakbrowser import ensure_binary; print(ensure_binary())")
"$BIN" --remote-debugging-port=9222 --user-data-dir=~/.tvvisual/profile \
       --no-sandbox --disable-setuid-sandbox --disable-dev-shm-usage \
       --ignore-gpu-blocklist --disable-background-timer-throttling \
       --disable-backgrounding-occluded-windows --disable-renderer-backgrounding \
       --no-first-run --no-default-browser-check about:blank &   # keep alive
```
This reproduced the entire confluence pipeline from an INDEPENDENT CDP client —
exact drift +0.0000 vs tvcli on the same bar (`bdg-exact-confluence-verified`).
**GOTCHA: `bdg <url>` NAVIGATES at session start** (attach == navigate) — it
wipes existing chart state (symbol/resolution/user studies). Start bdg ONCE,
then do login/symbol/study setup afterward (or pass the exact current URL);
never restart the worker mid-investigation.

**Mode B — its own headless Chrome (quick state reads, no stealth browser).**
```bash
bdg stop; bdg https://www.tradingview.com/chart/ --headless -q   # start session
bdg cdp Network.setCookies --params '{"cookies":[...]}'          # inject .env session
bdg cdp Page.navigate --params '{"url":"https://www.tradingview.com/chart/"}'
bdg dom eval '(/* read TradingViewApi state */)()'
bdg dom screenshot /tmp/chart.png
bdg stop
```
Reads symbol, resolution, layoutId (`_saveChartService.layoutId()`), viewport,
`timeScale().barSpacing()`, price-scale mode/range, and the study list. bdg
reuses its own session dir — a stale login there is normal; inject cookies
every run. bdg emits node warning noise on stdout: extract JSON with a
balanced-brace parser, never `json.loads` the raw output.

**Timeframe + symbol accessors (both modes):** `chart.setResolution('5', {})`
is the reliable TF setter — `timeScale().applyOptions({interval})` silently
no-ops (chart stays at the prior resolution). `a.symbol()` returns a STRING
(e.g. `"OANDA:XAUUSD"`), not an object; use `ms.symbol()` / `ms.proSymbol()`
where `ms = chart.model().mainSeries()` (`chart = window.TradingViewApi
._activeChartWidgetWV.value()`).

## Confluence methodology (learned 2026-08-20; EXACT as of round 5)

- **Exact (oscillators AND levels)**: the recipe reads last-closed-bar values
  via `closed_bar_values` (study row buffer `_items[n-2].value`, titles from
  `dataWindowView().items()._title`) and draws SL/TP/bias. `run_confluence`
  aligns bars first: if the chart's closed-bar time != tvcli `market.lastBarTime`,
  a bar rolled over between compute and read, so it recomputes tvcli until
  aligned. Verified drift **+0.0000** for RSI/Composite/SL/TP. A large drift
  WITH matching bars = real divergence; mismatched bars = roll-over (guard fixes
  it). Data-window reads (live forming bar) are only the fallback class
  (`approximate`).
- **Data plane**: chart market data is a **WebSocket**, not XHR. XHR surface is
  tiny/stable: `data.tradingview.com/ping`, news flow, `telemetry.tradingview
  .com/*`, `www.tradingview.com/savesettings/`. To monitor the live stream,
  attach a CDP client and listen for Network **webSocket\* events**
  (`webSocketCreated`/`FrameSent`/`FrameReceived`) after `Network.enable` —
  they are events, so they never appear in `bdg cdp --list` (methods only).
  (`tv-data-plane-ws` in probes.jsonl.)
- **Layout restore (SOLVED)**: `chart_restore(path)` / `layout restore <file>`
  calls `_chartWidgetCollection.loadLayoutState(env)` (top-level
  `window.TradingViewApi`, NOT the active widget) with
  `env.chartWidgetCollectionState = JSON.parse(exp.content)` — a FULL replace
  (`_cancelAllDrawings()` then `loadContent`). The `content` from
  `saveToJSON()` is an EMBEDDED JSON STRING; you MUST `JSON.parse` it or it
  throws AND wipes the chart.
- **Drawing restore (SOLVED)**: `drawings_snapshot` / `drawings restore` — set
  every shape to Layout sharing (`sharingMode().setValue(0)`, fresh drawings
  default to Account=1 and are filtered out), `sync.invalidateAll()` +
  `sync.getDTO(0,0,false)` (TV's own serializer; `dto.sources` is a **JS Map** —
  `Object.values()` on it is `[]`), then `applyDTO({sources:new Map(...),
  clientId, lineToolsToValidate})` restores by original id with exact points.
  Recipe `chart-restore` proves a 3-tool round-trip after `removeAllShapes()`.

## Rules

- Always `--env` the real cookies; a blank chart usually means an expired
  `SESSION` — say so, don't retry blindly.
- Free tier: max 2 studies per chart. Prefer the consolidated `xau-scalp`
  study over stacking indicators.
- Pine compile/upload is tvcli's job (`create`/`push`); the visual channel
  adds the owned script from My scripts and never hand-edits Pine in the DOM.
- Never commit screenshots with visible account info beyond the chart; the
  `xau-scalp-latest.png` artifact is a local working file.
- Every scouting session should leave the KB strictly better: a new probe, a
  refreshed surface/dom map, a repaired recipe, or a generated script.
