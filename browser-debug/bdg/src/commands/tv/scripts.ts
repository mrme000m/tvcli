/**
 * TradingView-specific scripts executed in the page via `bdg dom eval` / CDP.
 *
 * These are the reusable building blocks behind the `bdg tv` commands. Agents
 * can also import and run them directly (see `docs/tv/bdg-tv-guide.md`).
 *
 * Conventions:
 * - Plain ES5-ish JS (no template literals, no `async`): the page runs them
 *   verbatim via `Runtime.evaluate`.
 * - Each script is a single IIFE expression that evaluates to a JSON-serializable
 *   value (object/array). CDP `returnByValue` deep-copies it back.
 * - TradingView exposes its internal chart widget at
 *   `window._exposed_chartWidgetCollection.activeChartWidget._value`; the
 *   chart model is at `._modelWV._value` and exposes `dataSources()` (all
 *   sources), `panes()`, and `mainSeries()`.
 */

/**
 * WebSocket wrapper installed via `Page.addScriptToEvaluateOnNewDocument`.
 * Records every socket's URL and frames (sent/received) to `window.__tv_ws`
 * so the `~m~<len>~m~<json>` TradingView protocol can be captured live.
 *
 * Installed before a reload so the fresh page's sockets are created through
 * the wrapper; removed afterwards. Frames are capped at `MAX_PAYLOAD` chars to
 * bound memory for short captures (large enough to keep `du` payloads intact
 * for study-series key extraction).
 */
export const WS_PROBE_SCRIPT = `(function(){
  if (window.__tv_ws_installed) return;
  window.__tv_ws_installed = true;
  window.__tv_ws = [];
  var MAX_PAYLOAD = 200000;
  var Orig = window.WebSocket;
  function cap(s){
    if (typeof s === 'string') return s.length > MAX_PAYLOAD ? s.slice(0, MAX_PAYLOAD) + '...' + s.length : s;
    try { return '<bin:' + (s.byteLength || s.size || 0) + '>'; } catch (e) { return '<bin>'; }
  }
  function W(url, protocols){
    var ws = (protocols === undefined) ? new Orig(url) : new Orig(url, protocols);
    var rec = { url: String(url), frames: [] };
    window.__tv_ws.push(rec);
    var os = ws.send.bind(ws);
    ws.send = function(m){ try { rec.frames.push({ dir: 'out', d: cap(m) }); } catch (e) {} return os(m); };
    ws.addEventListener('message', function(e){ try { rec.frames.push({ dir: 'in', d: cap(e.data) }); } catch (err) {} });
    ws.addEventListener('close', function(){ rec.closed = true; });
    return ws;
  }
  W.prototype = Orig.prototype;
  W.CONNECTING = 0; W.OPEN = 1; W.CLOSING = 2; W.CLOSED = 3;
  try { W.name = 'WebSocket'; } catch (e) {}
  Object.defineProperty(window, 'WebSocket', { value: W, configurable: true, writable: true });
})();`;

/**
 * Build the read script that parses `window.__tv_ws` into a structured
 * capture: per-connection message flow plus a protocol summary.
 *
 * @param full - when true, also include the raw frame payloads (capped) per
 *   connection for deep inspection.
 * @returns JS expression evaluating to `{ connections, summary }`.
 */
export function buildWsReadScript(full: boolean): string {
  return `(function(){
  var conns = window.__tv_ws || [];
  function pkts(s){
    var out = [], str = s || "";
    while (true) {
      var idx = str.indexOf("~m~"); if (idx < 0) break;
      var after = str.slice(idx + 3); var e = after.indexOf("~m~"); if (e < 0) break;
      var len = parseInt(after.slice(0, e), 10); if (isNaN(len)) break;
      var st = idx + 3 + e + 3; var content = str.slice(st, st + len); str = str.slice(st + len);
      if (content === "") continue;
      var j = null; try { j = JSON.parse(content); } catch (x) { j = null; }
      if (j && typeof j === "object" && j.m) { out.push(j); }
      else { var mt = content.match(/"m":"([^"]+)"/); out.push({ m: mt ? mt[1] : "~raw~", p: [content.slice(0, 40)] }); }
    }
    return out;
  }
  var allOut = [], allIn = [];
  var connections = conns.map(function(rec){
    var fr = rec.frames || [];
    var outRaw = fr.filter(function(f){ return f.dir === "out"; }).map(function(f){ return f.d; }).join("");
    var inRaw = fr.filter(function(f){ return f.dir === "in"; }).map(function(f){ return f.d; }).join("");
    var outP = pkts(outRaw), inP = pkts(inRaw);
    allOut = allOut.concat(outP); allIn = allIn.concat(inP);
    var trim = function(p){ return p.map(function(x){
      var p0 = (Array.isArray(x.p) && x.p.length) ? x.p[0] : undefined;
      var pLen = Array.isArray(x.p) ? x.p.length : 0;
      if (typeof p0 === "string" && p0.length > 48) p0 = p0.slice(0, 48) + "...";
      return { m: x.m, p0: p0, pLen: pLen };
    });};
    var conn = { url: rec.url, closed: !!rec.closed, framesOut: outP.length, framesIn: inP.length, out: trim(outP), in: trim(inP) };
    ${full ? 'conn.outRaw = fr.filter(function(f){ return f.dir === "out"; }).map(function(f){ return f.d; }).slice(0, 80); conn.inRaw = fr.filter(function(f){ return f.dir === "in"; }).map(function(f){ return f.d; }).slice(0, 80);' : ''}
    return conn;
  });
  var sat = allOut.find(function(x){ return x.m === "set_auth_token"; });
  var duKeys = (function(){ var keys = {}; allIn.filter(function(x){ return x.m === "du" || x.m === "timescale_update"; }).forEach(function(x){ if (x.p && x.p[1] && typeof x.p[1] === "object" && !Array.isArray(x.p[1])) { Object.keys(x.p[1]).forEach(function(k){ keys[k] = 1; }); } }); return Object.keys(keys); })();
  var summary = {
    setAuthToken: sat ? (sat.p && sat.p[0] ? String(sat.p[0]).length : 0) : false,
    chartCreateSession: allOut.filter(function(x){ return x.m === "chart_create_session"; }).map(function(x){ return x.p && x.p[0]; }),
    resolveSymbol: allOut.filter(function(x){ return x.m === "resolve_symbol"; }).map(function(x){ return { ser: x.p && x.p[1], sym: (x.p && typeof x.p[2] === "string") ? x.p[2].slice(0, 80) : (x.p && x.p[2]) }; }),
    createStudy: allOut.filter(function(x){ return x.m === "create_study"; }).map(function(x){ return { sid: x.p && x.p[1], studyType: x.p && x.p[4] }; }),
    removeStudy: allOut.filter(function(x){ return x.m === "remove_study"; }).map(function(x){ return x.p && x.p[1]; }),
    studyCompleted: allIn.filter(function(x){ return x.m === "study_completed"; }).map(function(x){ return x.p && x.p[1]; }),
    duKeys: duKeys
  };
  return { connections: connections, summary: summary };
})()`;
}

/** Clears the probe globals left by `WS_PROBE_SCRIPT`. */
export const WS_CLEAR_SCRIPT = `window.__tv_ws = null; try { window.__tv_ws_installed = false; } catch (e) {} 'cleared'`;

/** Built-in dataSources that are NOT studies (excluded by `STUDIES_SCRIPT`). */
export const NON_STUDY_SOURCE_IDS = [
  '_seriesId',
  'PublishedChartsTimeline',
  'futures_contract_expiration',
  'latestUpdates',
  'ChartEventsSource',
];

/**
 * List the studies (indicators/strategies/event studies) on the active chart.
 * Classifies each dataSource: `pine` (user Pine script), `event` (earnings/
 * dividends/splits overlays), or `other` (e.g. volume profile).
 */
export const STUDIES_SCRIPT = `(function(){
  try {
    var cwc = window._exposed_chartWidgetCollection;
    var cw = cwc && cwc.activeChartWidget && cwc.activeChartWidget._value;
    var m = cw && cw._modelWV && cw._modelWV._value;
    if (!m) return { error: "no chart model" };
    var NON = { _seriesId:1, PublishedChartsTimeline:1, futures_contract_expiration:1, latestUpdates:1, ChartEventsSource:1 };
    var studies = [];
    m.dataSources().forEach(function(d){
      var id = null; try { id = d.id ? d.id() : d._id; } catch (e) {}
      var name = null; try { name = (typeof d.name === "function") ? d.name() : d.name; } catch (e) {}
      if (NON[id]) return;
      if (name === "Crosshair") return;
      var mi = null; try { mi = d._metaInfo && d._metaInfo._value; } catch (e) {}
      var kind = "other";
      if (mi) {
        if (mi.isTVScriptStrategy || mi.isTVScript) kind = "pine";
        else if ((id && String(id).indexOf("ESD$") === 0) || mi.linkedToSeries) kind = "event";
        else kind = "builtin";
      }
      studies.push({
        id: id, name: name, kind: kind,
        description: mi ? (mi.description || mi.shortDescription) : null,
        pineId: mi ? (mi.id || mi.shortId || mi.pineId) : null,
        isStrategy: mi ? !!mi.isTVScriptStrategy : false
      });
    });
    return { chart: location.href, studyCount: studies.length, studies: studies };
  } catch (e) { return { error: String(e) }; }
})()`;

/**
 * List the drawings ("line tools") on the active chart and report whether the
 * line-tool synchronizer (the autosave/REST layer for drawings) is present.
 * Drawings are detected via line-tool markers; returns an empty list when the
 * chart has no user drawings.
 */
export const DRAWINGS_SCRIPT = `(function(){
  try {
    var cwc = window._exposed_chartWidgetCollection;
    var cw = cwc && cwc.activeChartWidget && cwc.activeChartWidget._value;
    var m = cw && cw._modelWV && cw._modelWV._value;
    if (!m) return { error: "no chart model" };
    var drawings = [];
    m.dataSources().forEach(function(d){
      var isLT = false, type = null;
      try { if (d._lineToolType) { isLT = true; type = d._lineToolType; } } catch (e) {}
      try { if (!isLT && typeof d.isLineTool === "function" && d.isLineTool()) isLT = true; } catch (e) {}
      try { if (!isLT && d._properties && d._properties._value && d._properties._value.lineToolType) { isLT = true; type = d._properties._value.lineToolType; } } catch (e) {}
      if (!isLT) return;
      var o = { id: null, name: null, type: type };
      try { o.id = d.id ? d.id() : d._id; } catch (e) {}
      try { o.name = (typeof d.name === "function") ? d.name() : d.name; } catch (e) {}
      drawings.push(o);
    });
    var hasSync = !!(cw && cw._lineToolsSynchronizer);
    var hasActions = !!(cw && typeof cw.executeActionById === "function");
    return { chart: location.href, drawingCount: drawings.length, drawings: drawings, lineToolSynchronizer: hasSync, canExecuteActions: hasActions };
  } catch (e) { return { error: String(e) }; }
})()`;

/**
 * Build the in-page script that adds a study (any indicator/strategy/event
 * overlay) to the active chart via `createStudy` and returns the new entity
 * id by diffing `dataSources()`.
 *
 * The `window.TradingViewApi._activeChartWidgetWV.value()` surface exposes
 * `createStudy`, but its `getAllStudies()` only lists user studies — so the
 * new id is detected through the chart model's `dataSources()` (the same
 * enumeration `bdg tv studies` uses), which includes event overlays and
 * volume profiles.
 *
 * Input overrides are applied in TWO ways:
 * - Built-in names: passed as `[{id, value}]` to `createStudy(name, false,
 *   false, inputs)` (the wire/`{v,f,t}`-compatible path).
 * - Pine-id scripts (`USER;…`/`PUB;…`): `createStudy` takes a descriptor
 *   `{type:"pine", pineId, pineVersion}` with NO inputs, so overrides are
 *   applied afterwards via `getStudyById(id).getInputValues()` →
 *   `setInputValues()`, matching each override key case-insensitively against
 *   the input's `id` and `title` (the created study's `in_N` ids can be
 *   offset from tvcli's listing, but titles are stable).
 *
 * @param name - study display name as the indicators dialog resolves it
 *   (e.g. "Relative Strength Index", "Dividends", or a saved Pine script).
 * @param inputsJson - JSON string of input overrides, `{ "<input-id|title>": value }`
 *   (e.g. `{"length": 20}` or `{"swing_length": 30}`).
 * @param pineId - optional Pine id (`USER;…` / `PUB;…`) to add a saved/public
 *   script by descriptor instead of a built-in display name.
 * @returns JS expression evaluating to
 *   `{ before, added, createResult, inputsApplied?, error? }`.
 */
export function buildStudyAddScript(name: string, inputsJson: string, pineId?: string): string {
  const safeName = JSON.stringify(name);
  const safeInputs = JSON.stringify(inputsJson);
  const safePineId = pineId ? JSON.stringify(pineId) : 'null';
  return `(function(){
  try {
    var chart = window.TradingViewApi && window.TradingViewApi._activeChartWidgetWV
      && window.TradingViewApi._activeChartWidgetWV.value();
    if (!chart || typeof chart.createStudy !== "function") return { error: "createStudy unavailable" };
    // Multiplicity map id -> count: event overlays (ESD$*) can appear more than
    // once with the SAME id, so a membership diff would miss the new copy.
    var ids = function(){ var cwc = window._exposed_chartWidgetCollection; var cw = cwc && cwc.activeChartWidget && cwc.activeChartWidget._value; var m = cw && cw._modelWV && cw._modelWV._value; if (!m) return {}; var out = {}; m.dataSources().forEach(function(d){ var id = null; try { id = d.id ? d.id() : d._id; } catch (e) {} if (id != null) { var k = String(id); out[k] = (out[k] || 0) + 1; } }); return out; };
    var before = ids();
    var overrides = [];
    try { var parsed = JSON.parse(${safeInputs}); if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) { Object.keys(parsed).forEach(function(k){ overrides.push({ id: k, value: parsed[k] }); }); } } catch (e) {}
    var createResult = null;
    // A pine id (USER;… / PUB;…) adds ANY saved/public script by descriptor —
    // the same shape the visual channel's model inserter uses — instead of a
    // built-in display name.
    var createPromise = null;
    if (${safePineId}) {
      try { createPromise = chart.createStudy({ type: "pine", pineId: ${safePineId}, pineVersion: "last" }); } catch (e) { return { error: String(e) }; }
    } else {
      try { createPromise = chart.createStudy(${safeName}, false, false, overrides); } catch (e) { return { error: String(e) }; }
    }
    // createStudy returns a Promise<entityId|string> — await it before polling.
    // The resolved value is the entity id (e.g. "c6oJIa") or null on failure.
    return Promise.resolve(createPromise).then(function(promiseResult) {
      createResult = { started: true, promiseResult: promiseResult ? String(promiseResult) : null };
      // If the promise returned an entity id directly, use it as the target.
      var directId = (typeof promiseResult === "string") ? promiseResult : null;
    return new Promise(function(resolve){
      var deadline = Date.now() + 6000;
      var tick = function(){
        var after = ids();
        var added = [];
        Object.keys(after).forEach(function(k){
          var b = before[k] || 0, a = after[k] || 0;
          for (var i = b; i < a; i++) added.push(k);
        });
        if (added.length === 0 && Date.now() <= deadline) { setTimeout(tick, 300); return; }
        // Fallback when the multiplicity poll missed a slow registration
        // (e.g. right after a page reload): locate the study by pine id and
        // use it for input application.
        var targetId = directId || (added.length > 0 ? added[0] : null);
        if (!targetId && ${safePineId}) {
          try {
            var cwc0 = window._exposed_chartWidgetCollection;
            var cw0 = cwc0 && cwc0.activeChartWidget && cwc0.activeChartWidget._value;
            var m0 = cw0 && cw0._modelWV && cw0._modelWV._value;
            if (m0) {
              m0.dataSources().forEach(function(d){
                if (targetId) return;
                var mid = null; try { mid = d._metaInfo && d._metaInfo._value && d._metaInfo._value.id; } catch (e) {}
                if (mid && String(mid).indexOf(${safePineId}) !== -1) {
                  var did = null; try { did = d.id ? d.id() : d._id; } catch (e) {}
                  if (did) targetId = String(did);
                }
              });
            }
          } catch (e) {}
        }
        // Apply overrides post-creation for pine-id scripts (createStudy
        // descriptor carries no inputs). Match key against id OR title.
        var inputsApplied = null;
        if (targetId && overrides.length > 0 && ${safePineId}) {
          try {
            var study = chart.getStudyById(targetId);
            if (study && typeof study.getInputValues === "function" && typeof study.setInputValues === "function") {
              var cur = study.getInputValues() || [];
              var matched = [], unmatched = [];
              var norm = function(s){ return String(s).toLowerCase(); };
              overrides.forEach(function(ov){
                var found = null;
                for (var i = 0; i < cur.length; i++) {
                  var cid = cur[i].id != null ? norm(cur[i].id) : "";
                  var ctitle = cur[i].title != null ? norm(cur[i].title) : (cur[i].name != null ? norm(cur[i].name) : "");
                  if (cid === norm(ov.id) || ctitle === norm(ov.id)) { found = cur[i]; break; }
                }
                if (found) { found.value = ov.value; matched.push({ id: found.id, value: ov.value }); }
                else { unmatched.push(ov.id); }
              });
              if (matched.length) { study.setInputValues(cur); }
              inputsApplied = { matched: matched, unmatched: unmatched };
            }
          } catch (e) { inputsApplied = { error: String(e) }; }
        }
        resolve({ before: Object.keys(before), added: added, createResult: createResult, inputsApplied: inputsApplied, directId: directId });
      };
      tick();
    });
    }, function(e) {
      return { before: Object.keys(before), added: [], createResult: { error: String(e) }, inputsApplied: null };
    });
  } catch (e) { return { error: String(e) }; }
})()`;
}

/**
 * Read the latest computed values from every study on the active chart.
 *
 * Uses each dataSource's metaInfo plot descriptors (`plots[]` with `id`,
 * `title`) and the study's row buffer (`data()._items` — the tv-scout
 * documented source: `[bar_time, plot_0..n]`). Strategies without per-bar
 * plots report their plot metadata only; the strategy report travels in `du`
 * frames instead (see docs/tv/network-protocol.md).
 */
export const STUDY_VALUES_SCRIPT = `(function(){
  try {
    var cwc = window._exposed_chartWidgetCollection;
    var cw = cwc && cwc.activeChartWidget && cwc.activeChartWidget._value;
    var m = cw && cw._modelWV && cw._modelWV._value;
    if (!m) return { error: "no chart model" };
    var NON = { _seriesId:1, PublishedChartsTimeline:1, futures_contract_expiration:1, latestUpdates:1, ChartEventsSource:1 };
    var studies = [];
    m.dataSources().forEach(function(d){
      var id = null; try { id = d.id ? d.id() : d._id; } catch (e) {}
      var name = null; try { name = (typeof d.name === "function") ? d.name() : d.name; } catch (e) {}
      if (NON[id] || name === "Crosshair") return;
      var mi = null; try { mi = d._metaInfo && d._metaInfo._value; } catch (e) {}
      var plots = [];
      if (mi && mi.plots && Array.isArray(mi.plots)) {
        mi.plots.forEach(function(p){
          if (!p || typeof p !== "object") return;
          plots.push({ id: p.id != null ? String(p.id) : null, title: p.title || p.name || null });
        });
      }
      var lastRow = null;
      try {
        var dd = d.data();
        var items = dd && dd._items;
        if (items && items.length) {
          var last = items[items.length - 1];
          if (last && last.value !== undefined) lastRow = { n: items.length, value: Array.isArray(last.value) ? last.value : last.value };
        }
      } catch (e) {}
      studies.push({ id: id, name: name, plots: plots, lastRow: lastRow });
    });
    return { chart: location.href, studyCount: studies.length, studies: studies };
  } catch (e) { return { error: String(e) }; }
})()`;

/**
 * Build the in-page script that removes a study/entity by id via
 * `removeEntity(id)` on the chart widget.
 *
 * @param entityId - study/entity id as reported by `bdg tv studies`.
 * @returns JS expression evaluating to `{ removed: true, error? }`.
 */
export function buildStudyRemoveScript(entityId: string): string {
  const safeId = JSON.stringify(entityId);
  return `(function(){
  try {
    var chart = window.TradingViewApi && window.TradingViewApi._activeChartWidgetWV
      && window.TradingViewApi._activeChartWidgetWV.value();
    if (!chart || typeof chart.removeEntity !== "function") return { error: "removeEntity unavailable" };
    chart.removeEntity(${safeId});
    return { removed: true };
  } catch (e) { return { error: String(e) }; }
})()`;
}

/**
 * Read Pine-drawn graphics (line.new / label.new / box.new / table.new) from
 * every study on the active chart.
 *
 * The chart model's dataSource holds `_graphics._primitivesCollection`, a set
 * of per-kind maps (`dwglines`, `dwglabels`, `dwgboxes`, `dwgtablecells`).
 * Each kind maps a name (e.g. `lines`) to a wrapper whose
 * `_primitivesDataById` map holds the rendered primitives — the same surface
 * the community MCP servers attempted with
 * `data_get_pine_lines`/`labels`/`boxes`/`tables`, but with the real shape
 * (`_primitivesDataById`, not `.value()`): primitives carry x/y coordinates,
 * ext, style, color-index, and (for labels) text. Values are captured
 * conservatively to keep the payload bounded. A study that draws no Pine
 * graphics yields an empty list for every kind.
 */
export const STUDY_GRAPHICS_SCRIPT = `(function(){
  try {
    var cwc = window._exposed_chartWidgetCollection;
    var cw = cwc && cwc.activeChartWidget && cwc.activeChartWidget._value;
    var m = cw && cw._modelWV && cw._modelWV._value;
    if (!m) return { error: "no chart model" };
    var NON = { _seriesId:1, PublishedChartsTimeline:1, futures_contract_expiration:1, latestUpdates:1, ChartEventsSource:1 };
    var KINDS = ["dwglines", "dwglabels", "dwgboxes", "dwgtablecells"];
    var studies = [];
    m.dataSources().forEach(function(d){
      var id = null; try { id = d.id ? d.id() : d._id; } catch (e) {}
      var name = null; try { name = (typeof d.name === "function") ? d.name() : d.name; } catch (e) {}
      if (NON[id] || name === "Crosshair") return;
      var pc = null; try { pc = d._graphics && d._graphics._primitivesCollection; } catch (e) {}
      var gfx = {};
      KINDS.forEach(function(kind){
        var items = [];
        try {
          var outer = pc && pc[kind];
          if (!outer || typeof outer.get !== "function") return;
          // outer: Map<name, Map<key, wrapper>> — walk every level down to the
          // wrapper whose _primitivesDataById holds the actual primitives.
          outer.forEach(function(mid, gname){
            var walk = function(node){
              if (!node) return;
              var data = node._primitivesDataById;
              if (data && typeof data.forEach === "function") {
                data.forEach(function(p){
                  if (!p || typeof p !== "object") return;
                  var o = { kind: kind, group: String(gname), id: p.id != null ? String(p.id) : null };
                  if (p.x1 != null) o.x1 = p.x1;
                  if (p.y1 != null) o.y1 = p.y1;
                  if (p.x2 != null) o.x2 = p.x2;
                  if (p.y2 != null) o.y2 = p.y2;
                  if (p.ext != null) o.ext = p.ext;
                  if (p.st != null) o.st = p.st;
                  if (p.ci != null) o.ci = p.ci;
                  if (typeof p.text === "string") o.text = p.text.length > 80 ? p.text.slice(0, 80) + "..." : p.text;
                  if (p.visible != null) o.visible = p.visible;
                  items.push(o);
                });
              }
              if (typeof node.forEach === "function") node.forEach(walk);
            };
            walk(mid);
          });
        } catch (e) {}
        if (items.length) gfx[kind] = items;
      });
      studies.push({ id: id, name: name, graphics: gfx });
    });
    return { chart: location.href, studyCount: studies.length, studies: studies };
  } catch (e) { return { error: String(e) }; }
})()`;

/**
 * Build the in-page script that lists a study's CURRENT input ids/values via
 * `getStudyById(id).getInputValues()`. Pine scripts expose inputs as `in_N`
 * ids whose numbering can differ from the `tvcli inputs` listing (hidden
 * inputs shift the index) — this is the authoritative in-page mapping to use
 * with `tv study add --inputs`.
 *
 * @param entityId - study/entity id from `tv studies`.
 * @returns JS expression evaluating to `{ id, count, inputs: [{id, value}] }`.
 */
export function buildStudyInputsScript(entityId: string): string {
  const safeId = JSON.stringify(entityId);
  return `(function(){
  try {
    var chart = window.TradingViewApi && window.TradingViewApi._activeChartWidgetWV
      && window.TradingViewApi._activeChartWidgetWV.value();
    if (!chart || typeof chart.getStudyById !== "function") return { error: "getStudyById unavailable" };
    var study = chart.getStudyById(${safeId});
    if (!study) return { error: "study not found" };
    if (typeof study.getInputValues !== "function") return { error: "getInputValues unavailable" };
    var iv = study.getInputValues() || [];
    var inputs = iv.map(function(x){ return { id: x.id != null ? String(x.id) : null, value: x.value }; });
    return { id: ${safeId}, count: inputs.length, inputs: inputs };
  } catch (e) { return { error: String(e) }; }
})()`;
}

/**
 * Summarize the active chart: URL/title, main series, and all dataSources.
 * Useful as a first "what is on this chart" probe.
 */
export const CHART_SCRIPT = `(function(){
  try {
    var cwc = window._exposed_chartWidgetCollection;
    var cw = cwc && cwc.activeChartWidget && cwc.activeChartWidget._value;
    var m = cw && cw._modelWV && cw._modelWV._value;
    var ds = m ? m.dataSources() : [];
    var main = null;
    var sources = ds.map(function(d){
      var o = {};
      try { o.id = d.id ? d.id() : d._id; } catch (e) {}
      try { o.name = (typeof d.name === "function") ? d.name() : d.name; } catch (e) {}
      if (o.id === "_seriesId" && !main) main = o;
      return o;
    });
    return {
      url: location.href, title: document.title,
      chartId: (location.pathname.match(/\\/chart\\/([^/?#]+)/) || [])[1] || null,
      mainSeries: main, dataSourceCount: sources.length, dataSources: sources
    };
  } catch (e) { return { error: String(e) }; }
})()`;
