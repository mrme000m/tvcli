"""ChartTools — the full programmatic surface for a loaded TradingView chart.

A mixin applied to `Chart` in `session.py`. It ports the tradingview-mcp
(CDP) tool surface onto the standalone headful CloakBrowser channel, and adds
a live hotkey/drawing reference.

Sections:
  chart navigation   — set type, zoom to dates/ranges, symbol info/search
  data               — quote, depth, study values, strategy results/trades/equity
  drawings           — trend lines, list/get/remove shapes
  indicators         — search dialog, add-from-search, toggle visibility
  pine               — write/inject/compile/debug/iterate Pine Script
  replay             — bar-by-bar practice mode with paper trades
  panes              — multi-chart layouts with per-pane symbols
  alerts             — price alerts (create/list/delete)
  ui                 — clicks, keys, hover, scroll, panels, layouts, hotkeys
  watchlist          — read/add/remove symbols
  tabs               — browser-tab management (CloakBrowser pages)
  stream             — JSONL monitoring of the running chart
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib import parse as _urlparse, request as _urlrequest

from .hotkeys import describe as _describe_hotkeys

_js = json.dumps

CHART_API = "window.TradingViewApi._activeChartWidgetWV.value()"
CHART_WIDGET = CHART_API + "._chartWidget"
BARS_PATH = CHART_WIDGET + ".model().mainSeries().bars()"
CWC = "window.TradingViewApi._chartWidgetCollection"
REPLAY_API = "window.TradingViewApi._replayApi"

_FIND_MONACO = r"""
  (function findMonacoEditor() {
    // Scouted 2026-08-20: TradingView renders MULTIPLE .monaco-editor.pine-editor-monaco
    // containers (visible editor + hidden leftovers with no React fiber). The env
    // lives at fiber depth ~11 as memoizedProps.value.monacoEnv (and as a direct
    // memoizedProps.monacoEnv prop at depth ~12). Iterate all containers and both
    // shapes; the old single-container/value-only probe returned null on live UIs.
    var containers = document.querySelectorAll('.monaco-editor.pine-editor-monaco');
    for (var ci = 0; ci < containers.length; ci++) {
      var container = containers[ci];
      var el = container;
      var fiberKey;
      for (var i = 0; i < 25; i++) {
        if (!el) break;
        var keys = Object.keys(el);
        fiberKey = null;
        for (var j = 0; j < keys.length; j++) {
          if (keys[j].indexOf('__reactFiber$') === 0) { fiberKey = keys[j]; break; }
        }
        if (fiberKey) break;
        el = el.parentElement;
      }
      if (!fiberKey) continue;
      var current = el[fiberKey];
      for (var d = 0; d < 22; d++) {
        if (!current) break;
        var mp = current.memoizedProps;
        var env = mp && mp.value && mp.value.monacoEnv;
        if (!env && mp && mp.monacoEnv) env = mp.monacoEnv;
        if (env && env.editor && typeof env.editor.getEditors === 'function') {
          var editors = env.editor.getEditors();
          if (editors.length > 0) return { editor: editors[0], env: env };
        }
        current = current.return;
      }
    }
    return null;
  })()
"""

_FIND_STRATEGY_JS = r"""
  function _reportOf(s) {
    try { var rd = s.reportData(); if (rd && typeof rd.value === 'function') rd = rd.value(); return rd; } catch (e) { return null; }
  }
  function findStrategies() {
    var chart = %s._chartWidget;
    var sources = chart.model().model().dataSources();
    var strategies = [];
    for (var i = 0; i < sources.length; i++) {
      var s = sources[i], mi = null;
      try { mi = s.metaInfo ? s.metaInfo() : null; } catch (e) {}
      var isStrat = mi && (mi.isTVScriptStrategy || mi.is_strategy);
      if ((isStrat || typeof s.reportData === 'function') && typeof s.reportData === 'function') {
        strategies.push({ s: s, name: mi ? mi.description : null });
      }
    }
    return strategies;
  }
  function findStrategy() {
    var strategies = findStrategies();
    for (var j = 0; j < strategies.length; j++) {
      var rd = _reportOf(strategies[j].s);
      if (rd && rd.performance) return { strat: strategies[j].s, report: rd, name: strategies[j].name, strategy_count: strategies.length };
    }
    if (strategies.length) return { strat: strategies[0].s, report: null, name: strategies[0].name, strategy_count: strategies.length };
    return null;
  }
  function unhideStrategies() {
    var unhidden = [];
    var strategies = findStrategies();
    for (var i = 0; i < strategies.length; i++) {
      var s = strategies[i].s;
      try {
        var vis = null;
        try { vis = s.properties().visible.value(); } catch (e) {}
        if (vis !== false) continue;
        var done = false;
        try { s.properties().visible.setValue(true); done = true; } catch (e) {}
        if (!done) {
          try { var st = %s.getStudyById(s.id()); if (st) { st.setVisible(true); done = true; } } catch (e) {}
        }
        if (done) unhidden.push(strategies[i].name || 'strategy');
      } catch (e) {}
    }
    return unhidden;
  }
""" % (CHART_API, CHART_API)


def _unwrap(path: str) -> str:
    return ("(function(){ var v = %s; return (v && typeof v === 'object' && "
            "typeof v.value === 'function') ? v.value() : v; })()" % path)


def _round(v):
    return None if v is None else round(v * 1e8) / 1e8


# --------------------------------------------------------------------------
# Offline / REST helpers (no chart connection required)
# --------------------------------------------------------------------------

def pine_analyze(source: str) -> dict:
    """Static Pine source analysis (offline). Ports tradingview-mcp's pine_analyze."""
    lines = source.split("\n")
    diagnostics = []

    is_v6 = False
    for line in lines:
        t = line.strip()
        if t.startswith("//@version=6"):
            is_v6 = True
            break
        if t.startswith("//@version="):
            break
        if t == "" or t.startswith("//"):
            continue
        break

    arrays = {}
    import re
    for i, line in enumerate(lines):
        m = re.search(r"(\w+)\s*=\s*array\.from\(([^)]*)\)", line)
        if m:
            name, args = m.group(1).strip(), m.group(2).strip()
            arrays[name] = {"size": 0 if args == "" else args.count(",") + 1, "line": i + 1}
            continue
        m = re.search(r"(\w+)\s*=\s*array\.new(?:<\w+>|_\w+)\((\d+)?", line)
        if m:
            arrays[m.group(1).strip()] = {"size": int(m.group(2)) if m.group(2) else None, "line": i + 1}

    for i, line in enumerate(lines):
        for m in re.finditer(r"array\.(get|set)\(\s*(\w+)\s*,\s*(-?\d+)", line):
            method, arr, idx = m.group(1), m.group(2), int(m.group(3))
            info = arrays.get(arr)
            if info and info["size"] is not None and (idx < 0 or idx >= info["size"]):
                diagnostics.append({"line": i + 1, "column": m.start() + 1,
                                    "message": f"array.{method}({arr}, {idx}) — index {idx} out of bounds (array size is {info['size']})",
                                    "severity": "error"})

    for i, line in enumerate(lines):
        for m in re.finditer(r"(\w+)\.(first|last)\(\)", line):
            arr = m.group(1)
            if arr == "array":
                continue
            info = arrays.get(arr)
            if info and info["size"] == 0:
                diagnostics.append({"line": i + 1, "column": m.start() + 1,
                                    "message": f"{arr}.{m.group(2)}() called on possibly empty array (declared with size 0)",
                                    "severity": "warning"})

    has_strategy_call = any("strategy.entry" in l or "strategy.close" in l for l in lines)
    has_strategy_decl = any(l.strip().startswith("strategy(") for l in lines)
    if has_strategy_call and not has_strategy_decl:
        i = next((i for i, l in enumerate(lines) if "strategy.entry" in l or "strategy.close" in l), 0) + 1
        diagnostics.append({"line": i, "column": 1,
                            "message": "strategy.entry/close used but no strategy() declaration found — did you mean indicator()?",
                            "severity": "error"})

    if not is_v6:
        m = re.search(r"//@version=(\d+)", source)
        if m and int(m.group(1)) < 5:
            diagnostics.append({"line": 1, "column": 1,
                                "message": f"Script uses Pine v{m.group(1)} — consider upgrading to v6 for latest features",
                                "severity": "info"})

    out = {"success": True, "issue_count": len(diagnostics), "diagnostics": diagnostics}
    if not diagnostics:
        out["note"] = "No static analysis issues found. Use pine_compile / pine_smart_compile for a full server-side compile check."
    return out


def pine_check(source: str) -> dict:
    """Server-side Pine compile via pine-facade translate_light (no chart needed)."""
    data = _urlparse.urlencode({"source": source}).encode()
    req = _urlrequest.Request(
        "https://pine-facade.tradingview.com/pine-facade/translate_light?user_name=Guest&pine_id=00000000-0000-0000-0000-000000000000",
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded",
                 "Referer": "https://www.tradingview.com/"},
    )
    with _urlrequest.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode())

    errors, warnings = [], []
    inner = result.get("result")
    if inner:
        for e in inner.get("errors2") or []:
            errors.append({"line": (e.get("start") or {}).get("line"), "column": (e.get("start") or {}).get("column"),
                           "message": e.get("message")})
        for w in inner.get("warnings2") or []:
            warnings.append({"line": (w.get("start") or {}).get("line"), "column": (w.get("start") or {}).get("column"),
                             "message": w.get("message")})
    if isinstance(result.get("error"), str):
        errors.append({"message": result["error"]})

    compiled = not errors
    out = {"success": True, "compiled": compiled, "error_count": len(errors), "warning_count": len(warnings)}
    if errors:
        out["errors"] = errors
    if warnings:
        out["warnings"] = warnings
    if compiled:
        out["note"] = "Pine Script compiled successfully."
    return out


class ChartTools:
    """Mixed into Chart — see module docstring for the surface."""

    # ---------------------------------------------------------------- helpers

    def _eval_js(self, expression):
        """Escape hatch: evaluate arbitrary JS in the chart page."""
        return self.eval(expression)

    def _key(self, key, modifiers=None, code=None, vk=None):
        mod = 0
        for m in (modifiers or []):
            mod |= {"alt": 1, "ctrl": 2, "meta": 4, "shift": 8}.get(m.lower(), 0)
        keymap = {
            "Enter": ("Enter", 13), "Escape": ("Escape", 27), "Tab": ("Tab", 9),
            "Backspace": ("Backspace", 8), "Delete": ("Delete", 46),
            "ArrowUp": ("ArrowUp", 38), "ArrowDown": ("ArrowDown", 40),
            "ArrowLeft": ("ArrowLeft", 37), "ArrowRight": ("ArrowRight", 39),
            "Space": ("Space", 32), "Home": ("Home", 36), "End": ("End", 35),
            "PageUp": ("PageUp", 33), "PageDown": ("PageDown", 34),
            "F1": ("F1", 112), "F2": ("F2", 113), "F5": ("F5", 116),
        }
        c, v = keymap.get(key, (code or ("Key" + key.upper()), vk or key.upper().encode("ascii", "ignore")[0] if key else 0))
        self.cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "modifiers": mod, "key": key, "code": c, "windowsVirtualKeyCode": v})
        self.cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": c})

    # ------------------------------------------------------- chart navigation

    def set_chart_type(self, chart_type):
        type_map = {"Bars": 0, "Candles": 1, "Line": 2, "Area": 3, "Renko": 4, "Kagi": 5,
                    "PointAndFigure": 6, "LineBreak": 7, "HeikinAshi": 8, "HollowCandles": 9}
        n = type_map.get(chart_type, chart_type)
        n = int(n)
        if n < 0 or n > 9:
            raise ValueError(f"Unknown chart type: {chart_type!r}. Use a name (Candles, Line...) or number 0-9.")
        self.eval("(function(){ var chart = %s; chart.setChartType(%d); })()" % (CHART_API, n))
        return {"success": True, "chart_type": chart_type, "type_num": n}

    def visible_range(self):
        r = self.eval("(function(){ var chart = %s; return { visible_range: chart.getVisibleRange(), bars_range: chart.getVisibleBarsRange() }; })()" % CHART_API)
        return {"success": True, "visible_range": r.get("visible_range"), "bars_range": r.get("bars_range")}

    def set_visible_range(self, from_ts, to_ts):
        f, t = float(from_ts), float(to_ts)
        # page back until enough history is loaded
        for _ in range(25):
            st = self.eval("""(function() {
              var ms = %s.model().mainSeries();
              var b = ms.bars(); var fv = b.valueAt(b.firstIndex());
              var more = true; try { more = ms.requestMoreDataAvailable(); } catch (e) {}
              return { firstTime: fv && fv[0], more: more };
            })()""" % CHART_WIDGET)
            if not st or st["firstTime"] is None or st["firstTime"] <= f or not st["more"]:
                break
            self.eval("(function(){ try { %s.model().mainSeries().requestMoreData(1000); } catch (e) {} })()" % CHART_WIDGET)
            time.sleep(1.8)
        self.eval("""(function() {
          var chart = %s;
          var m = chart._chartWidget.model();
          var ts = m.timeScale();
          var bars = m.mainSeries().bars();
          var startIdx = bars.firstIndex(), endIdx = bars.lastIndex();
          var fromIdx = startIdx, toIdx = endIdx;
          for (var i = startIdx; i <= endIdx; i++) {
            var v = bars.valueAt(i);
            if (v && v[0] >= %f && fromIdx === startIdx) fromIdx = i;
            if (v && v[0] <= %f) toIdx = i;
          }
          ts.zoomToBarsRange(fromIdx, toIdx);
        })()""" % (CHART_API, f, t))
        time.sleep(0.5)
        return {"success": True, "requested": {"from": f, "to": t}}

    def scroll_to_date(self, date):
        if isinstance(date, (int, float)):
            ts = float(date)
        elif str(date).isdigit():
            ts = float(date)
        else:
            ts = _date_to_ts(str(date))
        res = str(self.eval("%s.resolution()" % CHART_API) or "15")
        secs = {"D": 86400, "1D": 86400, "W": 604800, "1W": 604800, "M": 2592000, "1M": 2592000}.get(res)
        if secs is None:
            mins = float("".join(c for c in res if c.isdigit()) or "15")
            secs = mins * 60
        half = 25 * secs
        f, t = ts - half, ts + half
        self.set_visible_range(f, t)
        return {"success": True, "date": date, "centered_on": ts, "resolution": res, "window": {"from": f, "to": t}}

    def symbol_info(self):
        r = self.eval("""(function() {
          var chart = %s;
          var info = chart.symbolExt();
          return { symbol: info.symbol, full_name: info.full_name, exchange: info.exchange,
                   description: info.description, type: info.type, pro_name: info.pro_name,
                   typespecs: info.typespecs, resolution: chart.resolution(), chart_type: chart.chartType() };
        })()""" % CHART_API)
        return {"success": True, **r}

    def symbol_search(self, query, search_type=""):
        qs = _urlparse.urlencode({"text": query, "hl": "1", "exchange": "", "lang": "en",
                                  "search_type": search_type, "domain": "production"})
        req = _urlrequest.Request(
            "https://symbol-search.tradingview.com/symbol_search/v3/?" + qs,
            headers={"Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"})
        with _urlrequest.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())

        def strip(s):
            import re
            return re.sub(r"</?em>", "", s or "")

        results = []
        for r in (data.get("symbols") or data or [])[:15]:
            sym = strip(r.get("symbol"))
            results.append({"symbol": sym, "description": strip(r.get("description")),
                            "exchange": r.get("exchange") or r.get("prefix") or "", "type": r.get("type") or "",
                            "full_name": (r.get("exchange") + ":" + sym) if r.get("exchange") else sym})
        return {"success": True, "query": query, "source": "rest_api", "results": results, "count": len(results)}

    # ------------------------------------------------------------------ data

    def quote(self, symbol=None):
        requested = (symbol or "").strip()
        original, needs_restore = None, False
        if requested:
            try:
                original = self.eval("%s.symbol()" % CHART_API)
            except Exception:
                pass
            if _bare(original) != _bare(requested):
                needs_restore = True
                self.set_symbol(requested)
                time.sleep(2)
        try:
            data = self.eval("""(function() {
              var api = %s;
              var sym = '';
              try { sym = api.symbol(); } catch(e) {}
              if (!sym) { try { sym = api.symbolExt().symbol; } catch(e) {} }
              var ext = {};
              try { ext = api.symbolExt() || {}; } catch(e) {}
              var quote = { symbol: sym };
              var bars = %s;
              if (bars && typeof bars.lastIndex === 'function') {
                var last = bars.valueAt(bars.lastIndex());
                if (last) { quote.time = last[0]; quote.open = last[1]; quote.high = last[2]; quote.low = last[3]; quote.close = last[4]; quote.last = last[4]; quote.volume = last[5] || 0; }
              }
              if (ext.description) quote.description = ext.description;
              if (ext.exchange) quote.exchange = ext.exchange;
              if (ext.type) quote.type = ext.type;
              return quote;
            })()""" % (CHART_API, BARS_PATH))
            if not data or (not data.get("last") and not data.get("close")):
                raise RuntimeError("Could not retrieve quote. The chart may still be loading.")
            return {"success": True, **data}
        finally:
            if needs_restore and original:
                try:
                    self.set_symbol(original)
                except Exception:
                    pass

    def depth(self):
        data = self.eval("""(function() {
          var domPanel = document.querySelector('[class*="depth"]')
            || document.querySelector('[class*="orderBook"]')
            || document.querySelector('[class*="dom-"]')
            || document.querySelector('[data-name="dom"]');
          if (!domPanel) return { found: false, error: 'DOM / Depth panel not found.' };
          var bids = [], asks = [];
          var rows = domPanel.querySelectorAll('[class*="row"], tr');
          for (var i = 0; i < rows.length; i++) {
            var row = rows[i];
            var priceEl = row.querySelector('[class*="price"]');
            var sizeEl = row.querySelector('[class*="size"], [class*="volume"], [class*="qty"]');
            if (!priceEl) continue;
            var price = parseFloat(priceEl.textContent.replace(/[^0-9.\\-]/g, ''));
            var size = sizeEl ? parseFloat(sizeEl.textContent.replace(/[^0-9.\\-]/g, '')) : 0;
            if (isNaN(price)) continue;
            var cls = row.className || '', html = row.innerHTML || '';
            if (/bid|buy/i.test(cls) || /bid|buy/i.test(html)) bids.push({ price: price, size: size });
            else if (/ask|sell/i.test(cls) || /ask|sell/i.test(html)) asks.push({ price: price, size: size });
            else if (i < rows.length / 2) asks.push({ price: price, size: size });
            else bids.push({ price: price, size: size });
          }
          bids.sort(function(a, b) { return b.price - a.price; });
          asks.sort(function(a, b) { return a.price - b.price; });
          var spread = null;
          if (asks.length && bids.length) spread = +(asks[0].price - bids[0].price).toFixed(6);
          return { found: true, bids: bids, asks: asks, spread: spread };
        })()""")
        if not data or not data.get("found"):
            raise RuntimeError((data or {}).get("error") or "DOM panel not found.")
        return {"success": True, "bid_levels": len(data.get("bids", [])), "ask_levels": len(data.get("asks", [])),
                "spread": data.get("spread"), "bids": data.get("bids", []), "asks": data.get("asks", [])}

    def study_values(self):
        data = self.eval("""(function() {
          var chart = %s;
          var model = chart.model();
          var sources = model.model().dataSources();
          var results = [];
          for (var si = 0; si < sources.length; si++) {
            var s = sources[si];
            if (!s.metaInfo) continue;
            try {
              var meta = s.metaInfo();
              var name = meta.description || meta.shortDescription || '';
              if (!name) continue;
              var values = {};
              try {
                var dwv = s.dataWindowView();
                if (dwv) {
                  var items = dwv.items();
                  if (items) {
                    for (var i = 0; i < items.length; i++) {
                      var item = items[i];
                      if (item._value && item._value !== '∅' && item._title) values[item._title] = item._value;
                    }
                  }
                }
              } catch(e) {}
              var id = null;
              try { id = s.id ? s.id() : null; } catch(e) {}
              if (Object.keys(values).length > 0) results.push({ id: id, name: name, values: values });
            } catch(e) {}
          }
          return results;
        })()""" % CHART_WIDGET)
        return {"success": True, "study_count": len(data or []), "studies": data or []}

    def _strategy_ready(self, max_wait=6.0):
        unhidden = self.eval("(function(){ %s try { var bwb = window.TradingView && window.TradingView.bottomWidgetBar; if (bwb && typeof bwb.showWidget === 'function') bwb.showWidget('backtesting'); } catch (e) {} return unhideStrategies(); })()" % _FIND_STRATEGY_JS)
        deadline = time.time() + max_wait
        status = "timeout"
        while time.time() < deadline:
            ready = self.eval("(function(){ %s var f = findStrategy(); if (!f) return 'no-strategy'; return f.report && f.report.performance ? 'ready' : 'pending'; })()" % _FIND_STRATEGY_JS)
            if ready in ("ready", "no-strategy"):
                status = ready
                break
            time.sleep(0.5)
        return status, unhidden or []

    def strategy_results(self):
        ready, unhidden = self._strategy_ready()
        r = self.eval("""(function() {
          %s
          try {
            var found = findStrategy();
            if (!found) return { metrics: {}, error: 'No strategy found on chart. Add a strategy first.' };
            var rd = found.report;
            if (!rd || !rd.performance) return { metrics: {}, error: 'Strategy report not computed yet. Open the Strategy Tester panel and retry.' };
            var perf = rd.performance, all = perf.all || {};
            var metrics = {
              net_profit: all.netProfit, net_profit_percent: all.netProfitPercent,
              gross_profit: all.grossProfit, gross_loss: all.grossLoss,
              profit_factor: all.profitFactor, max_drawdown: perf.maxStrategyDrawDown,
              max_drawdown_percent: perf.maxStrategyDrawDownPercent,
              total_trades: (all.numberOfWiningTrades || 0) + (all.numberOfLosingTrades || 0),
              winning_trades: all.numberOfWiningTrades, losing_trades: all.numberOfLosingTrades,
              percent_profitable: all.percentProfitable, avg_trade: all.avgTrade,
              largest_win: all.largestWinTrade, largest_loss: all.largestLosTrade,
              commission_paid: all.commissionPaid, sharpe_ratio: perf.sharpeRatio,
              sortino_ratio: perf.sortinoRatio, buy_hold_return: perf.buyHoldReturn, open_pl: perf.openPL
            };
            var clean = {};
            for (var k in metrics) if (metrics[k] !== null && metrics[k] !== undefined) clean[k] = metrics[k];
            return { metrics: clean, currency: rd.currency || null, strategy: found.name };
          } catch(e) { return { metrics: {}, error: e.message }; }
        })()""" % _FIND_STRATEGY_JS)
        out = {"success": bool(r.get("metrics")), "metric_count": len(r.get("metrics") or {}),
               "strategy": r.get("strategy"), "currency": r.get("currency"), "metrics": r.get("metrics") or {}}
        if unhidden:
            out["unhidden_strategies"] = unhidden
            out["note"] = "Strategy was hidden; made visible so the report could compute."
        if r.get("error"):
            out["error"] = r["error"]
        return out

    def trades(self, max_trades=20):
        limit = min(int(max_trades), 20)
        ready, unhidden = self._strategy_ready()
        r = self.eval("""(function() {
          %s
          try {
            var found = findStrategy();
            if (!found) return { trades: [], error: 'No strategy found on chart.' };
            var strat = found.strat;
            var orders = strat.ordersData(); if (orders && typeof orders.value === 'function') orders = orders.value();
            if (!orders || !Array.isArray(orders)) return { trades: [], total_orders: 0, error: 'Strategy orders not computed yet.' };
            var total = orders.length;
            var start = Math.max(0, total - %d);
            var result = [];
            for (var t = start; t < total; t++) {
              var o = orders[t];
              if (o && typeof o === 'object') {
                result.push({ id: o.id, type: o.tp, side: o.b ? 'buy' : 'sell', entry: o.e, price: o.p, qty: o.q, time_index: o.tm });
              }
            }
            return { trades: result, total_orders: total };
          } catch(e) { return { trades: [], error: e.message }; }
        })()""" % (_FIND_STRATEGY_JS, limit))
        out = {"success": bool(r.get("trades")), "trade_count": len(r.get("trades") or []),
               "total_orders": r.get("total_orders", 0), "trades": r.get("trades") or []}
        if unhidden:
            out["unhidden_strategies"] = unhidden
        if r.get("error"):
            out["error"] = r["error"]
        return out

    def equity(self):
        ready, unhidden = self._strategy_ready()
        r = self.eval("""(function() {
          %s
          try {
            var found = findStrategy();
            if (!found) return { data: [], error: 'No strategy found on chart.' };
            var rd = found.report;
            if (!rd) return { data: [], error: 'Strategy report not computed yet.' };
            var curve = rd.equity || rd.equityChart || null;
            if (Array.isArray(curve)) return { data: curve };
            if (Array.isArray(rd.buyHold)) return { data: [], buy_hold_points: rd.buyHold.length,
              note: 'Per-bar equity curve not exposed; buyHold baseline has ' + rd.buyHold.length + ' points.' };
            return { data: [], note: 'Equity curve not available via API; use strategy_results.' };
          } catch(e) { return { data: [], error: e.message }; }
        })()""" % _FIND_STRATEGY_JS)
        out = {"success": bool(r.get("data")), "data_points": len(r.get("data") or []), "data": r.get("data") or []}
        if r.get("buy_hold_points"):
            out["buy_hold_points"] = r["buy_hold_points"]
        if r.get("note"):
            out["note"] = r["note"]
        if unhidden:
            out["unhidden_strategies"] = unhidden
        if r.get("error"):
            out["error"] = r["error"]
        return out

    # ---------------------------------------------------------------- drawings

    def draw_trend_line(self, p1, p2, label=None, color="#2962ff", width=2):
        overrides = {"linecolor": color, "linewidth": width}
        self.eval("""%s.createMultipointShape(
          [{ time: %s, price: %s }, { time: %s, price: %s }],
          { shape: 'trend_line', overrides: %s, text: %s })""" % (
            CHART_API, _js(p1[0]), _js(p1[1]), _js(p2[0]), _js(p2[1]), _js(overrides), _js(label or "")))
        time.sleep(0.3)
        return {"success": True, "kind": "trend_line", "p1": p1, "p2": p2, "label": label}

    def draw_rectangle(self, high, low, label=None, color="#2962ff"):
        return self.draw_zone(high, low, label, color)

    def list_drawings(self):
        shapes = self.eval("""(function() {
          var api = %s;
          return api.getAllShapes().map(function(s) { return { id: s.id, name: s.name }; });
        })()""" % CHART_API) or []
        return {"success": True, "count": len(shapes), "shapes": shapes}

    def get_drawing(self, entity_id):
        r = self.eval("""(function() {
          var api = %s;
          var eid = %s;
          var shape = api.getShapeById(eid);
          if (!shape) return { error: 'Shape not found: ' + eid };
          var props = { entity_id: eid };
          try { props.points = shape.getPoints(); } catch(e) { props.points_error = e.message; }
          try { props.properties = shape.getProperties(); } catch(e) { try { props.properties = shape.properties(); } catch(e2) { props.properties_error = e2.message; } }
          try { props.visible = shape.isVisible(); } catch(e) {}
          try { props.locked = shape.isLocked(); } catch(e) {}
          var all = api.getAllShapes();
          for (var i = 0; i < all.length; i++) if (all[i].id === eid) { props.name = all[i].name; break; }
          return props;
        })()""" % (CHART_API, _js(str(entity_id))))
        if r.get("error"):
            raise RuntimeError(r["error"])
        return {"success": True, **r}

    def remove_drawing(self, entity_id):
        r = self.eval("""(function() {
          var api = %s;
          var eid = %s;
          var before = api.getAllShapes();
          var found = false;
          for (var i = 0; i < before.length; i++) if (before[i].id === eid) { found = true; break; }
          if (!found) return { removed: false, error: 'Shape not found: ' + eid };
          api.removeEntity(eid);
          var after = api.getAllShapes();
          return { removed: true, entity_id: eid, remaining_shapes: after.length };
        })()""" % (CHART_API, _js(str(entity_id))))
        if r.get("error"):
            raise RuntimeError(r["error"])
        return {"success": True, "entity_id": r.get("entity_id"), "removed": r.get("removed"),
                "remaining_shapes": r.get("remaining_shapes")}

    # -------------------------------------------------------------- indicators

    # --- indicators dialog helpers (poll-until-ready; fixed sleeps were flaky) ---

    def _open_indicators_dialog(self, timeout=8.0):
        """Click the indicators toolbar button and poll until the dialog + input exist."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.eval("(function(){ var b = document.querySelector('[data-name=\"open-indicators-dialog\"]'); if (b) b.click(); })()")
            time.sleep(0.6)
            ready = self.eval("(function(){ var d = document.querySelector('[data-name=\"indicators-dialog\"]'); return !!(d && d.querySelector('input')); })()")
            if ready:
                return True
        return False

    def _close_indicators_dialog(self):
        self.eval("(function(){ var dlg = document.querySelector('[data-name=\"indicators-dialog\"]'); if (dlg) { var c = dlg.querySelector('[data-name=\"close\"], [class*=\"close\"] button'); if (c) c.click(); } })()")

    def _type_in_dialog(self, query, settle=1.6):
        self.eval("""(function() {
          var inp = document.querySelector('[data-name="indicators-dialog"] input');
          if (!inp) return false;
          inp.focus();
          var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
          setter.call(inp, %s);
          inp.dispatchEvent(new Event('input', { bubbles: true }));
          return true;
        })()""" % _js(str(query)))
        time.sleep(settle)

    def search_indicators(self, query, limit=25):
        if not query or not str(query).strip():
            raise ValueError("query is required.")
        if not self._open_indicators_dialog():
            raise RuntimeError("indicators dialog did not open")
        self._type_in_dialog(query)
        res = self.eval("""(function() {
          var dlg = document.querySelector('[data-name="indicators-dialog"]');
          if (!dlg) return { open: false };
          var scroll = dlg.querySelector('[class*="scroll"]') || dlg;
          var rows = scroll.querySelectorAll('[class*="container"]');
          var results = [], section = null;
          for (var i = 0; i < rows.length; i++) {
            var r = rows[i];
            var h3 = r.querySelector('h3');
            if (h3 && r.contains(h3) && h3.parentElement === r) { section = (h3.textContent || '').trim(); continue; }
            var titleEl = r.querySelector('[class*="title"]');
            if (!titleEl) continue;
            var title = (titleEl.textContent || '').trim();
            if (!title) continue;
            results.push({ title: title, section: section });
          }
          return { open: true, results: results };
        })()""")
        self._close_indicators_dialog()
        results = (res or {}).get("results") or []
        return {"success": True, "query": query, "count": len(results[:limit]), "results": results[:limit]}

    def add_indicator_from_search(self, query, match=None, section=None):
        want = str(match or query).strip()
        before = self.eval("%s.getAllStudies().map(function(s){return s.id;})" % CHART_API) or []
        if not self._open_indicators_dialog():
            raise RuntimeError("indicators dialog did not open")
        self._type_in_dialog(query)
        clicked = self.eval("""(function() {
          var dlg = document.querySelector('[data-name="indicators-dialog"]');
          if (!dlg) return { error: 'dialog closed' };
          var scroll = dlg.querySelector('[class*="scroll"]') || dlg;
          var want = %s.toLowerCase();
          var rows = scroll.querySelectorAll('[class*="container"]');
          var section = null, exact = null, contains = null;
          for (var i = 0; i < rows.length; i++) {
            var r = rows[i];
            var h3 = r.querySelector('h3');
            if (h3 && h3.parentElement === r) { section = (h3.textContent || '').trim().toLowerCase(); continue; }
            var titleEl = r.querySelector('[class*="title"]');
            if (!titleEl) continue;
            var t = (titleEl.textContent || '').trim();
            var tl = t.toLowerCase();
            if (tl === want && !exact) exact = { row: r, title: t };
            if (tl.indexOf(want) !== -1 && !contains) contains = { row: r, title: t };
          }
          var pick = exact || contains;
          if (!pick) return { error: 'No result matching "' + want + '" found.' };
          pick.row.click();
          return { clicked: pick.title };
        })()""" % _js(want.lower()))
        if clicked.get("error"):
            self._close_indicators_dialog()
            raise RuntimeError(clicked["error"])
        time.sleep(1.5)
        self._close_indicators_dialog()
        after = self.eval("%s.getAllStudies().map(function(s){return s.id;})" % CHART_API) or []
        new_ids = [i for i in after if i not in before]
        return {"success": bool(new_ids), "added_from_search": clicked.get("clicked"),
                "entity_id": new_ids[0] if new_ids else None, "added_count": len(new_ids)}

    def toggle_indicator(self, entity_id, visible):
        r = self.eval("""(function() {
          var chart = %s;
          var study = chart.getStudyById(%s);
          if (!study) return { error: 'Study not found: ' + %s };
          study.setVisible(%s);
          return { visible: study.isVisible() };
        })()""" % (CHART_API, _js(str(entity_id)), _js(str(entity_id)), "true" if visible else "false"))
        if r.get("error"):
            raise RuntimeError(r["error"])
        return {"success": True, "entity_id": entity_id, "visible": r.get("visible")}

    # ------------------------------------------------------------------- pine

    def _monaco_ready(self):
        try:
            return bool(self.eval("(function(){ return %s !== null; })()" % _FIND_MONACO))
        except Exception:
            return False

    def ensure_pine_editor(self):
        # Scouted 2026-08-20: bottomWidgetBar.activateScriptEditorTab() reliably
        # opens the Pine editor; the [data-name="pine-dialog-button"] button is a
        # TOGGLE — clicking it after activation CLOSES the panel again. Never
        # click it when the editor is already up or right after activating.
        if self._monaco_ready():
            return True
        self.eval("""(function() {
          try {
            var bwb = window.TradingView && window.TradingView.bottomWidgetBar;
            if (!bwb) return 'no-bwb';
            if (typeof bwb.activateScriptEditorTab === 'function') { bwb.activateScriptEditorTab(); return 'activated'; }
            if (typeof bwb.showWidget === 'function') { bwb.showWidget('pine-editor'); return 'showWidget'; }
          } catch (e) { return 'err'; }
          return 'noop';
        })()""")
        for _ in range(60):  # 12s — editor + monaco boot can be slow
            time.sleep(0.2)
            if self._monaco_ready():
                return True
        # last resort: the toolbar toggle button (only when still closed)
        self.eval("""(function() {
          var btn = document.querySelector('[data-name="pine-dialog-button"]') || document.querySelector('[aria-label="Pine"]');
          if (btn) btn.click();
        })()""")
        for _ in range(50):
            time.sleep(0.2)
            if self._monaco_ready():
                return True
        return False

    def pine_get_source(self):
        if not self.ensure_pine_editor():
            raise RuntimeError("Could not open Pine Editor / Monaco not found.")
        src = self.eval("(function(){ var m = %s; return m ? m.editor.getValue() : null; })()" % _FIND_MONACO)
        if src is None:
            raise RuntimeError("Monaco editor found but getValue() returned null.")
        return {"success": True, "source": src, "line_count": src.count("\n") + 1, "char_count": len(src)}

    def pine_set_source(self, source):
        if not self.ensure_pine_editor():
            raise RuntimeError("Could not open Pine Editor.")
        ok = self.eval("(function(){ var m = %s; if (!m) return false; m.editor.setValue(%s); return true; })()" % (_FIND_MONACO, _js(source)))
        if not ok:
            raise RuntimeError("Monaco found but setValue() failed.")
        return {"success": True, "lines_set": source.count("\n") + 1}

    def _pine_click_save(self):
        return self.eval("""(function() {
          var btns = document.querySelectorAll('button');
          var addBtn = null, updateBtn = null, saveBtn = null;
          for (var i = 0; i < btns.length; i++) {
            var text = btns[i].textContent.trim();
            if (/save and add to chart/i.test(text)) { btns[i].click(); return 'Save and add to chart'; }
            if (!addBtn && /^add to chart$/i.test(text)) addBtn = btns[i];
            if (!updateBtn && /^update on chart$/i.test(text)) updateBtn = btns[i];
            if (!saveBtn && btns[i].className.indexOf('saveButton') !== -1 && btns[i].offsetParent !== null) saveBtn = btns[i];
          }
          if (addBtn) { addBtn.click(); return 'Add to chart'; }
          if (updateBtn) { updateBtn.click(); return 'Update on chart'; }
          if (saveBtn) { saveBtn.click(); return 'Pine Save'; }
          return null;
        })()""")

    def pine_compile(self):
        if not self.ensure_pine_editor():
            raise RuntimeError("Could not open Pine Editor.")
        clicked = self._pine_click_save()
        if not clicked:
            self._key("Enter", ["ctrl"])
        time.sleep(2)
        return {"success": True, "button_clicked": clicked or "keyboard_shortcut"}

    def pine_smart_compile(self):
        if not self.ensure_pine_editor():
            raise RuntimeError("Could not open Pine Editor.")
        before = self.eval("%s.getAllStudies().length" % CHART_API)
        clicked = self._pine_click_save()
        if not clicked:
            self._key("Enter", ["ctrl"])
        time.sleep(2.5)
        errors = self.eval("""(function() {
          var m = %s;
          if (!m) return [];
          var model = m.editor.getModel();
          if (!model) return [];
          var markers = m.env.editor.getModelMarkers({ resource: model.uri });
          return markers.map(function(mk) { return { line: mk.startLineNumber, column: mk.startColumn, message: mk.message, severity: mk.severity }; });
        })()""" % _FIND_MONACO)
        after = self.eval("%s.getAllStudies().length" % CHART_API)
        return {"success": True, "button_clicked": clicked or "keyboard_shortcut",
                "has_errors": bool(errors), "errors": errors or [],
                "study_added": (after > before) if (before is not None and after is not None) else None}

    def pine_get_errors(self):
        if not self.ensure_pine_editor():
            raise RuntimeError("Could not open Pine Editor.")
        errors = self.eval("""(function() {
          var m = %s;
          if (!m) return [];
          var model = m.editor.getModel();
          if (!model) return [];
          var markers = m.env.editor.getModelMarkers({ resource: model.uri });
          return markers.map(function(mk) { return { line: mk.startLineNumber, column: mk.startColumn, message: mk.message, severity: mk.severity }; });
        })()""" % _FIND_MONACO)
        return {"success": True, "has_errors": bool(errors), "error_count": len(errors or []), "errors": errors or []}

    def pine_get_console(self):
        if not self.ensure_pine_editor():
            raise RuntimeError("Could not open Pine Editor.")
        entries = self.eval("""(function() {
          var results = [];
          var rows = document.querySelectorAll('[class*="consoleRow"], [class*="log-"], [class*="consoleLine"]');
          if (!rows.length) {
            var area = document.querySelector('[class*="layout__area--bottom"]') || document.querySelector('[class*="bottom-widgetbar-content"]');
            if (area) rows = area.querySelectorAll('[class*="message"], [class*="log"], [class*="console"]');
          }
          for (var i = 0; i < rows.length; i++) {
            var text = rows[i].textContent.trim();
            if (!text) continue;
            var ts = null;
            var m = text.match(/^(\\d{4}-\\d{2}-\\d{2}\\s+)?\\d{2}:\\d{2}:\\d{2}/);
            if (m) ts = m[0];
            var type = 'info';
            var cls = rows[i].className || '';
            if (/error/i.test(cls)) type = 'error';
            else if (/warn/i.test(cls)) type = 'warning';
            results.push({ timestamp: ts, type: type, message: text });
          }
          return results;
        })()""")
        return {"success": True, "entries": entries or [], "entry_count": len(entries or [])}

    def pine_save(self):
        if not self.ensure_pine_editor():
            raise RuntimeError("Could not open Pine Editor.")
        self._key("s", ["ctrl"])
        time.sleep(0.8)
        handled = self.eval("""(function() {
          var btns = document.querySelectorAll('button');
          for (var i = 0; i < btns.length; i++) {
            var text = btns[i].textContent.trim();
            if (text === 'Save' && btns[i].offsetParent !== null) {
              var parent = btns[i].closest('[class*="dialog"], [class*="modal"], [class*="popup"], [role="dialog"]');
              if (parent) { btns[i].click(); return true; }
            }
          }
          return false;
        })()""")
        return {"success": True, "action": "saved_with_dialog" if handled else "Ctrl+S_dispatched"}

    def pine_new(self, type="indicator"):
        templates = {
            "indicator": "//@version=6\nindicator(\"My script\")\nplot(close)",
            "strategy": "//@version=6\nstrategy(\"My strategy\", overlay=true)\n",
            "library": "//@version=6\n// @description TODO: add library description here\nlibrary(\"MyLibrary\")\n",
        }
        return self.pine_set_source(templates.get(type, templates["indicator"]))

    def pine_list_scripts(self):
        scripts = self.eval("""fetch('https://pine-facade.tradingview.com/pine-facade/list/?filter=saved', { credentials: 'include' })
          .then(function(r) { return r.json(); })
          .then(function(data) {
            if (!Array.isArray(data)) return { scripts: [], error: 'Unexpected response from pine-facade' };
            return { scripts: data.map(function(s) { return { id: s.scriptIdPart || null, name: s.scriptName || s.scriptTitle || 'Untitled', title: s.scriptTitle || null, version: s.version || null, modified: s.modified || null }; }) };
          })
          .catch(function(e) { return { scripts: [], error: e.message }; })""", await_promise=True)
        return {"success": True, "scripts": (scripts or {}).get("scripts") or [],
                "count": len((scripts or {}).get("scripts") or []), "error": (scripts or {}).get("error")}

    def pine_open_script(self, name):
        if not self.ensure_pine_editor():
            raise RuntimeError("Could not open Pine Editor.")
        target = name.lower()
        result = self.eval("""(function() {
          var target = %s;
          return fetch('https://pine-facade.tradingview.com/pine-facade/list/?filter=saved', { credentials: 'include' })
            .then(function(r) { return r.json(); })
            .then(function(scripts) {
              if (!Array.isArray(scripts)) return { error: 'pine-facade returned unexpected data' };
              var match = null;
              for (var i = 0; i < scripts.length; i++) {
                var sn = (scripts[i].scriptName || '').toLowerCase();
                var st = (scripts[i].scriptTitle || '').toLowerCase();
                if (sn === target || st === target) { match = scripts[i]; break; }
              }
              if (!match) {
                for (var j = 0; j < scripts.length; j++) {
                  var sn2 = (scripts[j].scriptName || '').toLowerCase();
                  var st2 = (scripts[j].scriptTitle || '').toLowerCase();
                  if (sn2.indexOf(target) !== -1 || st2.indexOf(target) !== -1) { match = scripts[j]; break; }
                }
              }
              if (!match) return { error: 'Script "' + target + '" not found.' };
              var id = match.scriptIdPart;
              var ver = match.version || 1;
              return fetch('https://pine-facade.tradingview.com/pine-facade/get/' + id + '/' + ver, { credentials: 'include' })
                .then(function(r2) { return r2.json(); })
                .then(function(data) {
                  var source = data.source || '';
                  if (!source) return { error: 'Script source is empty', name: match.scriptName || match.scriptTitle };
                  var m = %s;
                  if (m) { m.editor.setValue(source); return { success: true, name: match.scriptName || match.scriptTitle, id: id, lines: source.split('\\n').length }; }
                  return { error: 'Monaco editor not found to inject source' };
                });
            })
            .catch(function(e) { return { error: e.message }; });
        })()""" % (_js(target), _FIND_MONACO), await_promise=True)
        if result.get("error"):
            raise RuntimeError(result["error"])
        return {"success": True, "name": result.get("name"), "script_id": result.get("id"),
                "lines": result.get("lines"), "source": "internal_api", "opened": True}

    # ------------------------------------------------------------------ replay

    def replay_start(self, date=None):
        avail = self.eval(_unwrap(REPLAY_API + ".isReplayAvailable()"))
        if not avail:
            raise RuntimeError("Replay is not available for the current symbol/timeframe.")
        self.eval("%s.showReplayToolbar()" % REPLAY_API)
        if date:
            ts = _date_to_ts(str(date)) * 1000
            self.eval("%s.selectDate(%d).then(function() { return 'ok'; })" % (REPLAY_API, ts), await_promise=True)
        else:
            self.eval("%s.selectFirstAvailableDate()" % REPLAY_API)
        started, current = False, None
        for _ in range(30):
            started = self.eval(_unwrap(REPLAY_API + ".isReplayStarted()"))
            current = self.eval(_unwrap(REPLAY_API + ".currentDate()"))
            if started and current is not None:
                break
            time.sleep(0.25)
        if not started:
            try:
                self.eval("%s.stopReplay()" % REPLAY_API)
            except Exception:
                pass
            raise RuntimeError("Replay failed to start. Try a more recent date or a higher timeframe.")
        return {"success": True, "replay_started": True, "date": date or "(first available)", "current_date": current}

    def replay_step(self):
        started = self.eval(_unwrap(REPLAY_API + ".isReplayStarted()"))
        if not started:
            raise RuntimeError("Replay is not started. Use replay_start first.")
        before = self.eval(_unwrap(REPLAY_API + ".currentDate()"))
        self.eval("%s.doStep()" % REPLAY_API)
        current = before
        for _ in range(12):
            time.sleep(0.25)
            current = self.eval(_unwrap(REPLAY_API + ".currentDate()"))
            if current != before:
                break
        return {"success": True, "action": "step", "current_date": current}

    def replay_autoplay(self, speed=None):
        valid = [100, 143, 200, 300, 1000, 2000, 3000, 5000, 10000]
        if speed and int(speed) not in valid:
            raise ValueError(f"Invalid autoplay delay {speed}ms. Valid: {valid}")
        started = self.eval(_unwrap(REPLAY_API + ".isReplayStarted()"))
        if not started:
            raise RuntimeError("Replay is not started. Use replay_start first.")
        if speed:
            self.eval("%s.changeAutoplayDelay(%d)" % (REPLAY_API, int(speed)))
        self.eval("%s.toggleAutoplay()" % REPLAY_API)
        active = self.eval(_unwrap(REPLAY_API + ".isAutoplayStarted()"))
        delay = self.eval(_unwrap(REPLAY_API + ".autoplayDelay()"))
        return {"success": True, "autoplay_active": bool(active), "delay_ms": delay}

    def replay_stop(self):
        started = self.eval(_unwrap(REPLAY_API + ".isReplayStarted()"))
        if not started:
            return {"success": True, "action": "already_stopped"}
        self.eval("%s.stopReplay()" % REPLAY_API)
        return {"success": True, "action": "replay_stopped"}

    def replay_trade(self, action):
        started = self.eval(_unwrap(REPLAY_API + ".isReplayStarted()"))
        if not started:
            raise RuntimeError("Replay is not started. Use replay_start first.")
        if action == "buy":
            self.eval("%s.buy()" % REPLAY_API)
        elif action == "sell":
            self.eval("%s.sell()" % REPLAY_API)
        elif action == "close":
            self.eval("%s.closePosition()" % REPLAY_API)
        else:
            raise ValueError("Invalid action. Use buy, sell, or close.")
        pos = self.eval(_unwrap(REPLAY_API + ".position()"))
        pnl = self.eval(_unwrap(REPLAY_API + ".realizedPL()"))
        return {"success": True, "action": action, "position": pos, "realized_pnl": pnl}

    def replay_status(self):
        st = self.eval("""(function() {
          var r = %s;
          function unwrap(v) { return (v && typeof v === 'object' && typeof v.value === 'function') ? v.value() : v; }
          return {
            is_replay_available: unwrap(r.isReplayAvailable()),
            is_replay_started: unwrap(r.isReplayStarted()),
            is_autoplay_started: unwrap(r.isAutoplayStarted()),
            replay_mode: unwrap(r.replayMode()),
            current_date: unwrap(r.currentDate()),
            autoplay_delay: unwrap(r.autoplayDelay())
          };
        })()""" % REPLAY_API)
        pos = self.eval(_unwrap(REPLAY_API + ".position()"))
        pnl = self.eval(_unwrap(REPLAY_API + ".realizedPL()"))
        return {"success": True, **st, "position": pos, "realized_pnl": pnl}

    # ------------------------------------------------------------------- panes

    def pane_list(self):
        r = self.eval("""(function() {
          var cwc = %s;
          var layoutType = cwc._layoutType;
          if (typeof layoutType === 'object' && layoutType && typeof layoutType.value === 'function') layoutType = layoutType.value();
          var count = cwc.inlineChartsCount;
          if (typeof count === 'object' && count && typeof count.value === 'function') count = count.value();
          var all = cwc.getAll();
          var panes = [];
          for (var i = 0; i < all.length; i++) {
            try {
              var c = all[i];
              var model = c.model ? c.model() : null;
              var ms = model ? model.mainSeries() : null;
              panes.push({ index: i, symbol: ms ? ms.symbol() : 'unknown', resolution: ms ? ms.interval() : null });
            } catch(e) { panes.push({ index: i, error: e.message }); }
          }
          var activeChart = window.TradingViewApi._activeChartWidgetWV.value();
          var activeIndex = null;
          for (var j = 0; j < all.length; j++) {
            try { if (all[j].model && activeChart._chartWidget && all[j] === activeChart._chartWidget) { activeIndex = j; break; } } catch(e) {}
          }
          return { layout: layoutType, chart_count: count, active_index: activeIndex, panes: panes };
        })()""" % CWC)
        names = {"s": "1 chart", "2h": "2 horizontal", "2v": "2 vertical", "2-1": "2 top, 1 bottom",
                 "1-2": "1 top, 2 bottom", "3h": "3 horizontal", "3v": "3 vertical", "3s": "3 custom",
                 "4": "2x2 grid", "4h": "4 horizontal", "4v": "4 vertical", "4s": "4 custom",
                 "6": "6 charts", "8": "8 charts", "10": "10 charts", "12": "12 charts",
                 "14": "14 charts", "16": "16 charts"}
        return {"success": True, "layout": r.get("layout"), "layout_name": names.get(r.get("layout"), r.get("layout")),
                "chart_count": r.get("chart_count"), "active_index": r.get("active_index"), "panes": r.get("panes")}

    def pane_set_layout(self, layout):
        aliases = {"single": "s", "1": "s", "1x1": "s", "2x1": "2h", "1x2": "2v",
                   "2x2": "4", "grid": "4", "quad": "4", "3x1": "3h", "1x3": "3v"}
        code = aliases.get(str(layout).lower().replace(" ", ""), str(layout).lower().replace(" ", ""))
        self.eval("%s.setLayout(%s)" % (CWC, _js(code)), await_promise=True)
        time.sleep(0.5)
        state = self.pane_list()
        return {"success": True, "layout": code, "chart_count": state["chart_count"], "panes": state["panes"]}

    def pane_focus(self, index):
        idx = int(index)
        r = self.eval("""(function() {
          var cwc = %s;
          var all = cwc.getAll();
          if (%d >= all.length) return { error: 'Pane index %d out of range (have ' + all.length + ' panes)' };
          var chart = all[%d];
          if (chart._mainDiv) chart._mainDiv.click();
          return { focused: %d, total: all.length };
        })()""" % (CWC, idx, idx, idx, idx))
        if r.get("error"):
            raise RuntimeError(r["error"])
        return {"success": True, "focused_index": r.get("focused"), "total_panes": r.get("total")}

    def pane_set_symbol(self, index, symbol):
        self.pane_focus(index)
        time.sleep(0.3)
        self.set_symbol(symbol)
        return {"success": True, "index": int(index), "symbol": symbol}

    # ------------------------------------------------------------------ alerts

    def alert_create(self, condition="crossing", price=None, message=None):
        if price is None:
            raise ValueError("price is required.")
        p = float(price)
        cond = {"crossing": "cross", "cross": "cross", "greater_than": "greater", "greater": "greater",
                "above": "greater", ">": "greater", "less_than": "less", "less": "less", "below": "less",
                "<": "less"}.get(str(condition).strip().lower(), "cross")
        r = self.eval("""(function() {
          try {
            var ms = window.TradingViewApi._activeChartWidgetWV.value()._chartWidget.model().mainSeries();
            var sym = (ms.proSymbol && ms.proSymbol()) || (ms.symbol && ms.symbol());
            if (!sym) return { success: false, error: 'Could not read current chart symbol.' };
            var price = %s, condType = %s, msg = %s;
            if (!msg) {
              var verb = condType === 'greater' ? 'above' : (condType === 'less' ? 'below' : 'crossing');
              msg = sym.split(':').pop() + ' ' + verb + ' ' + price;
            }
            var cond = { type: condType, frequency: 'on_first_fire', series: [{ type: 'barset' }, { type: 'value', value: price }], resolution: '1' };
            var payload = {
              conditions: [cond], symbol: '={"symbol":"' + sym + '"}', resolution: '1', message: msg,
              sound_file: 'alert/fired', sound_duration: 0, popup: true, auto_deactivate: true,
              email: false, sms_over_email: false, mobile_push: true, web_hook: null, name: null,
              expiration: new Date(Date.now() + 30 * 24 * 3600 * 1000).toISOString(), active: true, ignore_warnings: true
            };
            var x = new XMLHttpRequest();
            x.open('POST', 'https://pricealerts.tradingview.com/create_alert', false);
            x.withCredentials = true;
            x.setRequestHeader('Content-Type', 'text/plain;charset=UTF-8');
            x.send(JSON.stringify({ payload: payload }));
            var data = {}; try { data = JSON.parse(x.responseText); } catch (e) {}
            if (data.s === 'ok') return { success: true, symbol: sym, price: price, condition: condType, message: msg, alert_id: (data.r && data.r.alert_id) || null };
            return { success: false, error: (data.err && data.err.code) || data.errmsg || ('HTTP ' + x.status) };
          } catch (e) { return { success: false, error: e.message }; }
        })()""" % (_js(p), _js(cond), _js(message or "")))
        if not r.get("success"):
            raise RuntimeError(r.get("error") or "alert create failed")
        return r

    def alert_list(self):
        r = self.eval("""fetch('https://pricealerts.tradingview.com/list_alerts', { credentials: 'include' })
          .then(function(resp) { return resp.json(); })
          .then(function(data) {
            if (data.s !== 'ok' || !Array.isArray(data.r)) return { alerts: [], error: data.errmsg || 'Unexpected response' };
            return { alerts: data.r.map(function(a) {
              var sym = '';
              try { sym = JSON.parse(a.symbol.replace(/^=/, '')).symbol || a.symbol; } catch(e) { sym = a.symbol; }
              return { alert_id: a.alert_id, symbol: sym, type: a.type, message: a.message, active: a.active,
                       condition: a.condition, resolution: a.resolution, created: a.create_time,
                       last_fired: a.last_fire_time, expiration: a.expiration };
            }) };
          })
          .catch(function(e) { return { alerts: [], error: e.message }; })""", await_promise=True)
        return {"success": True, "alert_count": len((r or {}).get("alerts") or []),
                "source": "internal_api", "alerts": (r or {}).get("alerts") or [], "error": (r or {}).get("error")}

    def alert_delete(self, alert_id=None, alert_ids=None, delete_all=False):
        ids = []
        if alert_ids:
            ids.extend(alert_ids)
        if alert_id is not None:
            ids.append(alert_id)
        if delete_all:
            ids = [a["alert_id"] for a in self.alert_list().get("alerts", [])]
        ids = [x for x in ids if x is not None]
        if not ids:
            return {"success": False, "error": delete_all and "No alerts to delete." or "Provide delete_all or an alert_id."}
        r = self.eval("""(function() {
          try {
            var x = new XMLHttpRequest();
            x.open('POST', 'https://pricealerts.tradingview.com/delete_alerts', false);
            x.withCredentials = true;
            x.setRequestHeader('Content-Type', 'text/plain;charset=UTF-8');
            x.send(JSON.stringify({ payload: { alert_ids: %s } }));
            var data = {}; try { data = JSON.parse(x.responseText); } catch (e) {}
            return { ok: data.s === 'ok', status: x.status };
          } catch (e) { return { ok: false, error: e.message }; }
        })()""" % _js(ids))
        if r.get("ok"):
            return {"success": True, "deleted_count": len(ids), "alert_ids": ids}
        return {"success": False, "alert_ids": ids, "error": r.get("error") or "delete failed"}

    # ---------------------------------------------------------------------- ui

    def key_press(self, key, modifiers=None):
        self._ensure_open()
        self._key(key, modifiers)
        return {"success": True, "key": key, "modifiers": modifiers or []}

    def type_text(self, text):
        self._ensure_open()
        self.cdp.send("Input.insertText", {"text": text})
        return {"success": True, "typed": text[:100], "length": len(text)}

    def click(self, by="text", value=None):
        r = self.eval("""(function() {
          var by = %s, value = %s, el = null;
          if (by === 'aria-label') el = document.querySelector('[aria-label="' + value.replace(/"/g, '\\\\"') + '"]');
          else if (by === 'data-name') el = document.querySelector('[data-name="' + value.replace(/"/g, '\\\\"') + '"]');
          else if (by === 'text') {
            var cands = document.querySelectorAll('button, a, [role="button"], [role="menuitem"], [role="tab"]');
            for (var i = 0; i < cands.length; i++) {
              var text = cands[i].textContent.trim();
              if (text === value || text.toLowerCase() === value.toLowerCase()) { el = cands[i]; break; }
            }
          }
          else if (by === 'class-contains') el = document.querySelector('[class*="' + value.replace(/"/g, '\\\\"') + '"]');
          if (!el) return { found: false };
          el.click();
          return { found: true, tag: el.tagName.toLowerCase(), text: (el.textContent || '').trim().substring(0, 80),
                   aria_label: el.getAttribute('aria-label') || null, data_name: el.getAttribute('data-name') || null };
        })()""" % (_js(by), _js(value)))
        if not r or not r.get("found"):
            raise RuntimeError(f"No matching element for {by}={value!r}")
        return {"success": True, "clicked": r}

    def hover(self, by="text", value=None):
        r = self.eval("""(function() {
          var by = %s, value = %s, el = null;
          if (by === 'aria-label') el = document.querySelector('[aria-label="' + value.replace(/"/g, '\\\\"') + '"]');
          else if (by === 'data-name') el = document.querySelector('[data-name="' + value.replace(/"/g, '\\\\"') + '"]');
          else if (by === 'text') {
            var cands = document.querySelectorAll('button, a, [role="button"], [role="menuitem"], [role="tab"], span, div');
            for (var i = 0; i < cands.length; i++) { var t = cands[i].textContent.trim(); if (t === value || t.toLowerCase() === value.toLowerCase()) { el = cands[i]; break; } }
          }
          else if (by === 'class-contains') el = document.querySelector('[class*="' + value.replace(/"/g, '\\\\"') + '"]');
          if (!el) return null;
          var rect = el.getBoundingClientRect();
          return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, tag: el.tagName.toLowerCase() };
        })()""" % (_js(by), _js(value)))
        if not r:
            raise RuntimeError(f"Element not found for {by}={value!r}")
        self.cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": r["x"], "y": r["y"]})
        return {"success": True, "hovered": {"by": by, "value": value, "tag": r["tag"], "x": r["x"], "y": r["y"]}}

    def scroll(self, direction="down", amount=300):
        px = int(amount)
        center = self.eval("""(function() {
          var el = document.querySelector('[data-name="pane-canvas"]') || document.querySelector('[class*="chart-container"]') || document.querySelector('canvas');
          if (!el) return { x: window.innerWidth / 2, y: window.innerHeight / 2 };
          var rect = el.getBoundingClientRect();
          return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
        })()""")
        dx = dy = 0
        if direction == "up": dy = -px
        elif direction == "down": dy = px
        elif direction == "left": dx = -px
        elif direction == "right": dx = px
        self.cdp.send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": center["x"], "y": center["y"], "deltaX": dx, "deltaY": dy})
        return {"success": True, "direction": direction, "amount": px}

    def mouse_click(self, x, y, button="left", double_click=False):
        btn = "right" if button == "right" else ("middle" if button == "middle" else "left")
        btn_num = 2 if btn == "right" else (1 if btn == "middle" else 0)
        self.cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        self.cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": btn, "buttons": btn_num, "clickCount": 1})
        self.cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": btn})
        if double_click:
            time.sleep(0.05)
            self.cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": btn, "buttons": btn_num, "clickCount": 2})
            self.cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": btn})
        return {"success": True, "x": x, "y": y, "button": btn, "double_click": bool(double_click)}

    def find_elements(self, query, strategy="text"):
        r = self.eval("""(function() {
          var query = %s, strategy = %s, results = [];
          if (strategy === 'css') {
            var els = document.querySelectorAll(query);
            for (var i = 0; i < Math.min(els.length, 20); i++) {
              var rect = els[i].getBoundingClientRect();
              results.push({ tag: els[i].tagName.toLowerCase(), text: (els[i].textContent || '').trim().substring(0, 80),
                             aria_label: els[i].getAttribute('aria-label') || null, data_name: els[i].getAttribute('data-name') || null,
                             x: rect.x, y: rect.y, width: rect.width, height: rect.height, visible: els[i].offsetParent !== null });
            }
          } else if (strategy === 'aria-label') {
            var els = document.querySelectorAll('[aria-label*="' + query.replace(/"/g, '\\\\"') + '"]');
            for (var i = 0; i < Math.min(els.length, 20); i++) {
              var rect = els[i].getBoundingClientRect();
              results.push({ tag: els[i].tagName.toLowerCase(), text: (els[i].textContent || '').trim().substring(0, 80),
                             aria_label: els[i].getAttribute('aria-label') || null, data_name: els[i].getAttribute('data-name') || null,
                             x: rect.x, y: rect.y, width: rect.width, height: rect.height, visible: els[i].offsetParent !== null });
            }
          } else {
            var all = document.querySelectorAll('button, a, [role="button"], [role="menuitem"], [role="tab"], input, select, label, span, div, h1, h2, h3, h4');
            for (var i = 0; i < all.length; i++) {
              var text = all[i].textContent.trim();
              if (text.toLowerCase().indexOf(query.toLowerCase()) !== -1 && text.length < 200) {
                var rect = all[i].getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                  results.push({ tag: all[i].tagName.toLowerCase(), text: text.substring(0, 80),
                                 aria_label: all[i].getAttribute('aria-label') || null, data_name: all[i].getAttribute('data-name') || null,
                                 x: rect.x, y: rect.y, width: rect.width, height: rect.height, visible: all[i].offsetParent !== null });
                  if (results.length >= 20) break;
                }
              }
            }
          }
          return results;
        })()""" % (_js(query), _js(strategy)))
        return {"success": True, "query": query, "strategy": strategy, "count": len(r or []), "elements": r or []}

    def open_panel(self, panel, action="toggle"):
        if panel in ("pine-editor", "strategy-tester"):
            widget = "pine-editor" if panel == "pine-editor" else "backtesting"
            r = self.eval("""(function() {
              var bwb = window.TradingView && window.TradingView.bottomWidgetBar;
              if (!bwb) return { error: 'bottomWidgetBar not available' };
              var panel = %s, widget = %s, action = %s;
              var bottomArea = document.querySelector('[class*="layout__area--bottom"]');
              var isOpen = !!(bottomArea && bottomArea.offsetHeight > 50);
              if (panel === 'pine-editor') { var me = document.querySelector('.monaco-editor.pine-editor-monaco'); isOpen = isOpen && !!me; }
              if (panel === 'strategy-tester') { var sp = document.querySelector('[data-name="backtesting"]'); isOpen = isOpen && !!(sp && sp.offsetParent); }
              var performed = 'none';
              if (action === 'open' || (action === 'toggle' && !isOpen)) {
                if (panel === 'pine-editor') { if (typeof bwb.activateScriptEditorTab === 'function') bwb.activateScriptEditorTab(); else if (typeof bwb.showWidget === 'function') bwb.showWidget(widget); }
                else { if (typeof bwb.showWidget === 'function') bwb.showWidget(widget); }
                performed = 'opened';
              } else if (action === 'close' || (action === 'toggle' && isOpen)) {
                if (typeof bwb.hideWidget === 'function') bwb.hideWidget(widget);
                else if (typeof bwb.close === 'function') bwb.close();
                else if (typeof bwb.hide === 'function') bwb.hide();
                performed = 'closed';
              }
              return { was_open: isOpen, performed: performed };
            })()""" % (_js(panel), _js(widget), _js(action)))
            if r.get("error"):
                raise RuntimeError(r["error"])
            return {"success": True, "panel": panel, "action": action, "was_open": r.get("was_open"), "performed": r.get("performed")}
        sel = {"watchlist": {"dataNames": ["base-watchlist-widget-button", "base"], "ariaLabels": ["Watchlist", "Watchlist, details, and news"]},
               "alerts": {"dataNames": ["alerts-button", "alerts"], "ariaLabels": ["Alerts"]},
               "trading": {"dataNames": ["trading-button"], "ariaLabels": ["Trading Panel"]}}.get(panel)
        if not sel:
            raise ValueError(f"Unknown panel: {panel!r}")
        r = self.eval("""(function() {
          var dataNames = %s, ariaLabels = %s, action = %s, btn = null;
          for (var d = 0; d < dataNames.length && !btn; d++) btn = document.querySelector('[data-name="' + dataNames[d] + '"]');
          for (var a = 0; a < ariaLabels.length && !btn; a++) btn = document.querySelector('[aria-label="' + ariaLabels[a] + '"]');
          if (!btn) return { error: 'Button not found for panel: ' + %s };
          var isActive = btn.getAttribute('aria-pressed') === 'true' || btn.classList.contains('isActive') || btn.classList.toString().indexOf('active') !== -1 || btn.classList.toString().indexOf('Active') !== -1;
          var rightArea = document.querySelector('[class*="layout__area--right"]');
          var sidebarOpen = !!(rightArea && rightArea.offsetWidth > 50);
          var isOpen = isActive && sidebarOpen;
          var performed = 'none';
          if (action === 'open' && !isOpen) { btn.click(); performed = 'opened'; }
          else if (action === 'close' && isOpen) { btn.click(); performed = 'closed'; }
          else if (action === 'toggle') { btn.click(); performed = isOpen ? 'closed' : 'opened'; }
          else performed = isOpen ? 'already_open' : 'already_closed';
          return { was_open: isOpen, performed: performed };
        })()""" % (_js(sel["dataNames"]), _js(sel["ariaLabels"]), _js(action), _js(panel)))
        if r.get("error"):
            raise RuntimeError(r["error"])
        return {"success": True, "panel": panel, "action": action, "was_open": r.get("was_open"), "performed": r.get("performed")}

    def fullscreen(self):
        r = self.eval("""(function() {
          var btn = document.querySelector('[data-name="header-toolbar-fullscreen"]');
          if (!btn) return { found: false };
          btn.click();
          return { found: true };
        })()""")
        if not r or not r.get("found"):
            raise RuntimeError("Fullscreen button not found")
        return {"success": True, "action": "fullscreen_toggled"}

    def layout_list(self):
        r = self.eval("""new Promise(function(resolve) {
          try {
            window.TradingViewApi.getSavedCharts(function(charts) {
              if (!charts || !Array.isArray(charts)) { resolve({ layouts: [], error: 'getSavedCharts returned no data' }); return; }
              resolve({ layouts: charts.map(function(c) { return { id: c.id || c.chartId || null, name: c.name || c.title || 'Untitled', symbol: c.symbol || null, resolution: c.resolution || null, modified: c.timestamp || c.modified || null }; }) });
            });
            setTimeout(function() { resolve({ layouts: [], error: 'getSavedCharts timed out' }); }, 5000);
          } catch(e) { resolve({ layouts: [], error: e.message }); }
        })""", await_promise=True)
        return {"success": True, "layout_count": len((r or {}).get("layouts") or []),
                "layouts": (r or {}).get("layouts") or [], "error": (r or {}).get("error")}

    def layout_export(self, path=None):
        """Export the COMPLETE chart configuration as JSON (scouted 2026-08-20:
        _saveChartService.saveToJSON() returns a sync object; legs/content/
        study_meta_info_map arrive as embedded JSON strings and are parsed here).

        The export carries resolution, symbol, and `content.charts[].panes[].sources[]`
        — every study's exact inputs/overrides/styles plus all drawings — so an
        agent can read back precisely how the chart is configured, diff configs,
        and rebuild the same chart later. Default path: ~/.tvvisual/scout/layouts/.
        """
        raw = self.eval("""(function() {
          try {
            var v = window.TradingViewApi._saveChartService.saveToJSON();
            return { ok: true, json: JSON.stringify(v) };
          } catch (e) { return { ok: false, error: String(e) }; }
        })()""")
        if not raw or not raw.get("ok"):
            raise RuntimeError((raw or {}).get("error") or "saveToJSON failed")
        data = json.loads(raw["json"])
        for key in ("legs", "study_meta_info_map", "content"):
            if isinstance(data.get(key), str):
                try:
                    data[key] = json.loads(data[key])
                except (ValueError, TypeError):
                    pass
        if path is None:
            d = Path.home() / ".tvvisual" / "scout" / "layouts"
            d.mkdir(parents=True, exist_ok=True)
            sym = str(data.get("symbol", "chart")).replace(":", "-")
            path = d / f"{sym}_{data.get('resolution')}_{time.strftime('%Y%m%d-%H%M%S')}.json"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        content = data.get("content") or {}
        charts = content.get("charts") or []
        panes = charts[0].get("panes", []) if charts else []
        sources = panes[0].get("sources", []) if panes else []
        return {"success": True, "file_path": str(path),
                "size_bytes": path.stat().st_size,
                "symbol": data.get("symbol"), "resolution": data.get("resolution"),
                "layout_name": data.get("name"),
                "sources": [s.get("type") for s in sources]}

    def layout_switch(self, name):
        result = self.eval("""new Promise(function(resolve) {
          try {
            var target = %s;
            if (/^\\d+$/.test(target)) { window.TradingViewApi.loadChartFromServer(target); resolve({ success: true, id: target }); return; }
            window.TradingViewApi.getSavedCharts(function(charts) {
              if (!charts || !Array.isArray(charts)) { resolve({ success: false, error: 'getSavedCharts returned no data' }); return; }
              var match = null;
              for (var i = 0; i < charts.length; i++) { var cname = charts[i].name || charts[i].title || ''; if (cname === target || cname.toLowerCase() === target.toLowerCase()) { match = charts[i]; break; } }
              if (!match) { for (var j = 0; j < charts.length; j++) { var cn = (charts[j].name || charts[j].title || '').toLowerCase(); if (cn.indexOf(target.toLowerCase()) !== -1) { match = charts[j]; break; } } }
              if (!match) { resolve({ success: false, error: 'Layout "' + target + '" not found.' }); return; }
              var chartId = match.id || match.chartId;
              window.TradingViewApi.loadChartFromServer(chartId);
              resolve({ success: true, id: chartId, name: match.name || match.title });
            });
            setTimeout(function() { resolve({ success: false, error: 'getSavedCharts timed out' }); }, 5000);
          } catch(e) { resolve({ success: false, error: e.message }); }
        })""" % _js(str(name)), await_promise=True)
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "layout switch failed")
        time.sleep(0.5)
        dismissed = self.eval("""(function() {
          var btns = document.querySelectorAll('button');
          for (var i = 0; i < btns.length; i++) {
            var text = btns[i].textContent.trim();
            if (/open anyway|don't save|discard/i.test(text)) { btns[i].click(); return true; }
          }
          return false;
        })()""")
        if dismissed:
            time.sleep(1)
        return {"success": True, "layout": result.get("name") or name, "layout_id": result.get("id"),
                "action": "switched", "unsaved_dialog_dismissed": bool(dismissed)}

    # ------------------------------------------- state capture / restore (round 5, verified 2026-08-20)

    def closed_bar_values(self, study=None):
        """Exact LAST-CLOSED-BAR values for every study plot on the chart.

        Reads the study source row buffer: `source.data()._items[n-2].value`
        = [bar_time, plot_0, ..., plot_n]. Unlike the data window (which
        follows the crosshair / live forming bar), this returns the last
        CLOSED bar — the same bar tvcli computes against, so confluence
        checks become exact. Plot names come from the dataWindowView titles
        (index-aligned with metaInfo().plots). Also returns the live-bar row
        for context.
        """
        data = self.eval("""(function() {
          var chart = %s;
          var want = %s;
          var sources = chart.model().model().dataSources();
          var out = [];
          for (var si = 0; si < sources.length; si++) {
            var s = sources[si];
            var name = null;
            try { var m = s.metaInfo(); name = m ? (m.description || m.shortDescription || '') : null; } catch(e) { continue; }
            if (!name) continue;
            if (want && name.indexOf(want) === -1) continue;
            var entry = { name: name };
            try {
              var items = s.data()._items;
              if (!items || items.length < 3) { entry.error = 'no row buffer yet'; out.push(entry); continue; }
              var row = items[items.length - 2];
              var live = items[items.length - 1];
              var titles = [];
              try { var dwv = s.dataWindowView(); var its = dwv.items(); for (var i = 0; i < its.length; i++) titles.push(its[i]._title); } catch(e) {}
              var closed = {}, liveVals = {};
              var n = Math.min(titles.length, row.value.length - 1);
              for (var j = 0; j < n; j++) { closed[titles[j]] = row.value[1 + j]; liveVals[titles[j]] = live.value[1 + j]; }
              entry.bar_time = row.value[0];
              entry.live_bar_time = live.value[0];
              entry.values = closed;
              entry.live_values = liveVals;
            } catch(e) { entry.error = e.message; }
            out.push(entry);
          }
          return out;
        })()""" % (CHART_WIDGET, _js(study)))
        return {"success": True, "studies": data or []}

    def drawings_snapshot(self, path=None):
        """Capture EVERY line tool on the chart (user drawings + study-owned
        tools) as a restorable DTO file.

        Verified 2026-08-20 (round 5): line tools must be LAYOUT-shared
        (sharingMode 0) for the serializer to include them — fresh drawings
        default to Account sharing (1) and are filtered out. The snapshot
        therefore switches every shape to Layout sharing (this is what makes
        the state travel with the chart), then `sync.invalidateAll()` +
        `sync.getDTO(0, 0, false)` makes TradingView's own serializer emit
        `{id, ownerSource, state: {type, points, state, metaInfo, ...}, symbol}`
        for each tool. Restore later with `drawings_restore(path)` (applyDTO)
        — same ids, exact points.
        """
        data = self.eval("""(function() {
          var api = %s;
          var model = api._chartWidget.model();
          var sync = api._chartWidget.lineToolsSynchronizer();
          api.getAllShapes().forEach(function(s) {
            var l = model.dataSourceForId(s.id);
            try { if (l && l.sharingMode) l.sharingMode().setValue(0); } catch(e) {}
          });
          try { sync.invalidateAll(); } catch(e) {}
          var dto = sync.getDTO(0, 0, false);
          var items = [];
          (dto.sources || new Map()).forEach(function(v, k) {
            if (!v) return;
            items.push(JSON.parse(JSON.stringify(v)));
          });
          return { ok: true, client_id: dto.clientId || null, count: items.length,
                   items: items,
                   shapes: api.getAllShapes().map(function(s) { return { id: s.id, name: s.name }; }) };
        })()""" % CHART_API)
        if not data or not data.get("ok"):
            raise RuntimeError("drawings snapshot failed: %r" % (data,))
        if path is None:
            d = Path.home() / ".tvvisual" / "scout" / "layouts" / "drawings"
            d.mkdir(parents=True, exist_ok=True)
            path = d / ("drawings_%s.json" % time.strftime("%Y%m%d-%H%M%S"))
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1))
        return {"success": True, "file_path": str(path), "count": data["count"],
                "tools": [i.get("state", {}).get("type") for i in data["items"]],
                "shapes": data.get("shapes", [])}

    def drawings_restore(self, path):
        """Restore line tools from a `drawings_snapshot()` file.

        Re-applies every captured tool under its ORIGINAL id via
        `sync.applyDTO` (Map-based DTO; verified 2026-08-20 round 5).
        Tools already present with the same id are replaced.
        """
        snap = json.loads(Path(path).read_text())
        items = snap.get("items") or []
        if not items:
            raise RuntimeError("no tools in snapshot file: %s" % path)
        res = self.eval("""(function() {
          var api = %s;
          var sync = api._chartWidget.lineToolsSynchronizer();
          var items = %s;
          var m = new Map();
          for (var i = 0; i < items.length; i++) m.set(items[i].id, items[i]);
          var base = sync.getDTO();
          var dto = { sources: m, groups: new Map(), clientId: base.clientId || null,
                      lineToolsToValidate: items.map(function(x) { return x.id; }),
                      groupsToValidate: [] };
          return new Promise(function(resolve) {
            sync.applyDTO(dto).then(function() {
              setTimeout(function() {
                resolve({ ok: true, shapes: api.getAllShapes().map(function(s) { return { id: s.id, name: s.name }; }) });
              }, 1000);
            }, function(e) { resolve({ ok: false, error: String(e) }); });
          });
        })()""" % (CHART_API, json.dumps(items)), await_promise=True)
        if not res or not res.get("ok"):
            raise RuntimeError("drawings restore failed: %r" % (res,))
        return {"success": True, "restored": len(items),
                "shapes": res.get("shapes", [])}

    def chart_restore(self, path):
        """Restore the COMPLETE chart state from a `layout_export()` file.

        Full replacement (verified 2026-08-20 round 5): `loadLayoutState`
        cancels current drawings then loads the saved content — symbol,
        resolution, every study with exact inputs/overrides/styles, and all
        line tools. The file's `content` may be an embedded JSON string
        (parsed here); the envelope needs id/name/username/lastModified plus
        `chartWidgetCollectionState = content`.
        """
        exp = json.loads(Path(path).read_text())
        for key in ("legs", "study_meta_info_map", "content"):
            if isinstance(exp.get(key), str):
                try:
                    exp[key] = json.loads(exp[key])
                except (ValueError, TypeError):
                    pass
        env = {
            "id": exp.get("id"), "uid": exp.get("uid"),
            "name": exp.get("name") or "", "description": exp.get("description") or "",
            "username": exp.get("username") or "", "lastModified": exp.get("lastModified"),
            "isPrivate": exp.get("isPrivate") is not False,
            "chartWidgetCollectionState": exp.get("content"),
        }
        res = self.eval("""(function() {
          var c = window.TradingViewApi._chartWidgetCollection;
          var env = %s;
          return new Promise(function(resolve) {
            c.loadLayoutState(env).then(function() { resolve({ ok: true }); },
                                        function(e) { resolve({ ok: false, error: String(e) }); });
          });
        })()""" % json.dumps(env), await_promise=True)
        time.sleep(2)
        if not res or not res.get("ok"):
            raise RuntimeError("chart restore failed: %r" % (res,))
        st = self.state()
        return {"success": True, "layout": exp.get("name"),
                "symbol": st.get("symbol"), "resolution": st.get("resolution"),
                "studies": st.get("studies", [])}

    def hotkeys(self, scrape=False, mac=False):
        """Return the drawing/hotkey reference. scrape=True reads TradingView's own sheet (best-effort)."""
        ref = _describe_hotkeys(mac=mac)
        if not scrape:
            return ref
        live = self.eval("""(function() {
          var dialog = document.querySelector('[data-name="keyboard-shortcuts-dialog"], [class*="hotkey"]')
            || Array.prototype.slice.call(document.querySelectorAll('[class*="dialog"], [role="dialog"]'))
                 .filter(function(d) { return /shortcut|hotkey/i.test(d.textContent || ''); })[0];
          if (!dialog) return { scraped: false };
          var groups = [];
          var kbd = dialog.querySelectorAll('kbd, [class*="hotkey-keys"]');
          var rows = dialog.querySelectorAll('[class*="row"], tr');
          var seen = {};
          for (var i = 0; i < rows.length; i++) {
            var keys = [];
            var kbs = rows[i].querySelectorAll('kbd');
            for (var k = 0; k < kbs.length; k++) keys.push(kbs[k].textContent.trim());
            var label = (rows[i].textContent || '').replace(keys.join(''), '').trim();
            if (keys.length && label && !seen[label]) { seen[label] = true; groups.push({ keys: keys, action: label }); }
          }
          return { scraped: true, count: groups.length, groups: groups };
        })()""")
        if live and live.get("scraped"):
            ref["source"] = "live_scrape"
            ref["live"] = {"count": live.get("count"), "groups": live.get("groups")}
            ref["note"] = "Live scrape of TradingView's keyboard-shortcuts UI."
        else:
            ref["note"] = "Live scrape failed/not found — curated reference only. TradingView's in-app sheet is authoritative."
        return ref

    # ---------------------------------------------------------------- watchlist

    def _watchlist_button(self):
        return """(document.querySelector('[data-name="base-watchlist-widget-button"]')
          || document.querySelector('[aria-label="Watchlist, details, and news"]')
          || document.querySelector('[aria-label^="Watchlist"]'))"""

    def watchlist(self):
        self.eval("(function(){ var btn = %s; if (btn && btn.getAttribute('aria-pressed') !== 'true') btn.click(); })()" % self._watchlist_button())
        for _ in range(20):
            ready = self.eval("""!!(document.querySelector('[data-name="add-symbol-button"]') || document.querySelector('[class*="layout__area--right"] [data-symbol-full]'))""")
            if ready:
                break
            time.sleep(0.25)
        data = self.eval("""(function() {
          function norm(t) { return t.replace(/\\u2212/g, '-').trim(); }
          var container = document.querySelector('[class*="layout__area--right"]');
          if (!container) return { symbols: [] };
          var results = [], seen = {};
          var symEls = container.querySelectorAll('[data-symbol-full]');
          for (var i = 0; i < symEls.length; i++) {
            var sym = symEls[i].getAttribute('data-symbol-full');
            if (!sym || seen[sym]) continue;
            seen[sym] = true;
            var row = symEls[i].closest('[class*="row"]') || symEls[i].parentElement;
            var cells = row ? row.querySelectorAll('[class*="cell"], [class*="column"]') : [];
            var texts = [];
            for (var j = 0; j < cells.length; j++) texts.push(norm(cells[j].textContent));
            results.push({ symbol: sym, last: texts[1] || null, change: texts[2] || null, change_percent: texts[3] || null, volume: texts[4] || null });
          }
          return { symbols: results };
        })()""")
        return {"success": True, "count": len((data or {}).get("symbols") or []), "symbols": (data or {}).get("symbols") or []}

    def watchlist_add(self, symbol):
        self.watchlist()
        clicked = self.eval("""(function() {
          var btn = document.querySelector('[data-name="add-symbol-button"]')
            || document.querySelector('[aria-label="Add symbol"]')
            || document.querySelector('[aria-label*="Add symbol"]');
          if (!btn || btn.offsetParent === null) return { found: false };
          btn.click();
          return { found: true };
        })()""")
        if not clicked.get("found"):
            raise RuntimeError("Add symbol button not found in watchlist panel")
        time.sleep(0.4)
        self.type_text(str(symbol))
        time.sleep(0.7)
        self._key("Enter")
        time.sleep(0.4)
        self._key("Escape")
        time.sleep(0.4)
        bare = str(symbol).split(":")[-1].upper()
        verified = self.eval("""(function() {
          var rows = document.querySelectorAll('[class*="layout__area--right"] [data-symbol-full]');
          for (var i = 0; i < rows.length; i++) {
            var s = rows[i].getAttribute('data-symbol-full') || '';
            if (s.toUpperCase() === %s || s.split(':').pop().toUpperCase() === %s) return s;
          }
          return null;
        })()""" % (_js(str(symbol).upper()), _js(bare)))
        return {"success": bool(verified), "symbol": symbol, "added_as": verified,
                "action": "added" if verified else "not_verified"}

    def watchlist_add_bulk(self, symbols):
        results = []
        for sym in symbols:
            try:
                r = self.watchlist_add(sym)
                results.append({"symbol": sym, "success": r["success"], "added_as": r.get("added_as")})
            except Exception as e:
                results.append({"symbol": sym, "success": False, "error": str(e)})
        added = sum(1 for r in results if r["success"])
        return {"success": added > 0, "added": added, "failed": len(results) - added, "results": results}

    def _watchlist_active_list_info(self):
        """React-fiber probe: active watchlist id/name/symbols (for REST removal)."""
        return self.eval("""(function() {
          var panel = document.querySelector('[class*="layout__area--right"]');
          if (!panel) return null;
          var rows = panel.querySelectorAll('[data-symbol-full]');
          if (!rows.length) return null;
          var row = rows[0];
          var reactKey = Object.keys(row).find(function(k) { return k.indexOf('__reactFiber') === 0; });
          if (!reactKey) return null;
          var fiber = row[reactKey];
          var count = 0;
          while (fiber && count < 45) {
            if (fiber.memoizedProps && fiber.memoizedProps.current && fiber.memoizedProps.current.id) {
              var cur = fiber.memoizedProps.current;
              return { id: cur.id, name: cur.name, symbols: cur.symbols || [] };
            }
            fiber = fiber.return;
            count++;
          }
          return null;
        })()""")

    def watchlist_remove(self, symbols):
        self.watchlist()
        list_info = self._watchlist_active_list_info()
        if not list_info:
            raise RuntimeError("Cannot read active watchlist metadata (React fiber probe failed).")

        if isinstance(symbols, str):
            symbols = [symbols]
        to_remove, skipped = [], []
        for sym in symbols:
            if ":" in sym:
                (to_remove if sym in list_info["symbols"] else skipped).append(sym)
            else:
                match = next((s for s in list_info["symbols"]
                              if s.split(":")[-1].upper() == sym.upper()), None)
                (to_remove if match else skipped).append(match or sym)
        if not to_remove:
            return {"success": False, "removed": [], "skipped": skipped,
                    "error": "No matching symbols in the active watchlist"}

        resp = self.eval("""fetch('https://www.tradingview.com/api/v1/symbols_list/custom/' + %s + '/remove/', {
            method: 'POST', credentials: 'include',
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
            body: JSON.stringify(%s)
          }).then(function(r) { return r.text().then(function(t) { return { status: r.status, ok: r.ok, body: t.substring(0, 300) }; }); })
            .catch(function(e) { return { status: 0, ok: false, body: String(e) }; })""" % (
            _js(list_info["id"]), _js(to_remove)), await_promise=True)
        if not resp or not resp.get("ok"):
            raise RuntimeError(f"Watchlist remove REST call failed: {resp and resp.get('body')}")

        # The widget doesn't live-sync API removals — remount it, then verify.
        for _ in range(2):
            self.eval("(function(){ var btn = %s; if (btn) btn.click(); })()" % self._watchlist_button())
            time.sleep(0.4)

        still = to_remove
        deadline = time.time() + 5
        while time.time() < deadline:
            time.sleep(0.5)
            still = self.eval("""(function() {
              var rows = document.querySelectorAll('[class*="layout__area--right"] [data-symbol-full]');
              var present = {};
              for (var i = 0; i < rows.length; i++) present[rows[i].getAttribute('data-symbol-full')] = true;
              return %s.filter(function(s) { return present[s]; });
            })()""" % _js(to_remove)) or []
            if not still:
                break
        return {"success": True, "removed": to_remove, "skipped": skipped,
                "verified": not still, "still_present": still,
                "list_id": list_info["id"], "list_name": list_info["name"], "api": "rest"}

    # --------------------------------------------------------------------- tabs

    def tab_list(self):
        self._ensure_open()
        tabs = []
        for i, page in enumerate(self.ctx.pages or []):
            try:
                tabs.append({"index": i, "url": page.url, "title": page.title()})
            except Exception as e:
                tabs.append({"index": i, "error": str(e)})
        return {"success": True, "tab_count": len(tabs), "tabs": tabs, "active_index": self.ctx.pages.index(self.page) if self.page in (self.ctx.pages or []) else None}

    def tab_new(self, url=None):
        self._ensure_open()
        page = self.ctx.new_page()
        page.goto(url or "https://www.tradingview.com/chart/", wait_until="domcontentloaded")
        time.sleep(2)
        return {"success": True, "url": page.url}

    def tab_switch(self, index):
        self._ensure_open()
        pages = self.ctx.pages or []
        if int(index) >= len(pages):
            raise RuntimeError(f"Tab index {index} out of range (have {len(pages)} tabs)")
        self.page = pages[int(index)]
        self.page.bring_to_front() if hasattr(self.page, "bring_to_front") else None
        self.cdp = self.ctx.new_cdp_session(self.page)
        self.cdp.send("Runtime.enable")
        self.cdp.send("Page.enable")
        return {"success": True, "index": int(index), "url": self.page.url}

    def tab_close(self, index):
        self._ensure_open()
        pages = self.ctx.pages or []
        if int(index) >= len(pages):
            raise RuntimeError(f"Tab index {index} out of range (have {len(pages)} tabs)")
        if len(pages) <= 1:
            raise RuntimeError("Cannot close the last tab.")
        page = pages[int(index)]
        if page is self.page:
            self.page = pages[0] if pages[0] is not page else pages[1]
        page.close()
        return {"success": True, "index": int(index)}

    # ------------------------------------------------------------------- stream

    def _stream(self, label, fetch_js, interval=0.5, dedupe=True):
        """Blocking poll-and-diff JSONL emitter. Ctrl+C to stop."""
        last = None
        import sys
        sys.stderr.write(f"[stream:{label}] started, interval={interval}s, Ctrl+C to stop\\n")
        try:
            while True:
                try:
                    data = self.eval(fetch_js)
                except Exception as e:
                    sys.stderr.write(f"[stream:{label}] error: {e}\\n")
                    time.sleep(2)
                    continue
                if not data:
                    time.sleep(interval)
                    continue
                key = json.dumps(data, sort_keys=True, default=str) if dedupe else None
                if (not dedupe) or key != last:
                    last = key
                    print(json.dumps({**data, "_ts": int(time.time() * 1000), "_stream": label}, default=str))
                time.sleep(interval)
        except KeyboardInterrupt:
            sys.stderr.write(f"[stream:{label}] stopped\\n")

    def stream_quote(self, interval=0.3):
        self._stream("quote", """(function() {
          var chart = %s; var m = %s;
          var bars = m.mainSeries().bars(); var last = bars.lastIndex(); var v = bars.valueAt(last);
          if (!v) return null;
          return { symbol: chart.symbol(), time: v[0], open: v[1], high: v[2], low: v[3], close: v[4], volume: v[5] || 0 };
        })()""" % (CHART_API, CHART_WIDGET), interval)

    def stream_bars(self, interval=0.5):
        self._stream("bars", """(function() {
          var chart = %s; var m = %s;
          var bars = m.mainSeries().bars(); var last = bars.lastIndex(); var v = bars.valueAt(last);
          if (!v) return null;
          return { symbol: chart.symbol(), resolution: chart.resolution(), bar_time: v[0], open: v[1], high: v[2], low: v[3], close: v[4], volume: v[5] || 0, bar_index: last };
        })()""" % (CHART_API, CHART_WIDGET), interval)

    def stream_values(self, interval=0.5):
        self._stream("values", """(function() {
          var chart = %s; var studies = chart.getAllStudies(); var results = [];
          for (var i = 0; i < studies.length; i++) {
            try {
              var study = chart.getStudyById(studies[i].id);
              if (!study || !study.isVisible()) continue;
              var src = study._study || study;
              var data = src._lastBarValues || src._data;
              if (!data) continue;
              var vals = {};
              for (var k in data) if (typeof data[k] === 'number' && !isNaN(data[k])) vals[k] = data[k];
              if (Object.keys(vals).length) results.push({ name: studies[i].name, values: vals });
            } catch(e) {}
          }
          return { symbol: chart.symbol(), study_count: results.length, studies: results };
        })()""" % CHART_API, interval)

    def stream_lines(self, interval=1.0, filter=""):
        f = _js(filter or "")
        self._stream("lines", """(function() {
          var filter = %s; var chart = %s; var studies = chart.getAllStudies(); var results = [];
          for (var i = 0; i < studies.length; i++) {
            var s = studies[i];
            if (filter && (s.name || '').toLowerCase().indexOf(filter.toLowerCase()) === -1) continue;
            try {
              var study = chart.getStudyById(s.id); if (!study) continue;
              var src = study._study || study;
              var g = src._graphics || (src._source && src._source._graphics);
              if (!g || !g._primitivesCollection || !g._primitivesCollection.dwglines) continue;
              var data = g._primitivesCollection.dwglines.get('lines').get(false);
              if (!data || !data._primitivesDataById) continue;
              var levels = [], seen = {};
              data._primitivesDataById.forEach(function(line) {
                var p1 = line.points && line.points[0] ? line.points[0].price : null;
                var p2 = line.points && line.points[1] ? line.points[1].price : null;
                var price = (p1 !== null && p1 === p2) ? p1 : (p1 || p2);
                if (price !== null && !seen[price]) { seen[price] = true; levels.push(price); }
              });
              levels.sort(function(a, b) { return b - a; });
              if (levels.length) results.push({ study: s.name, levels: levels });
            } catch(e) {}
          }
          return { symbol: chart.symbol(), study_count: results.length, studies: results };
        })()""" % (f, CHART_API), interval)

    def stream_labels(self, interval=1.0, filter=""):
        f = _js(filter or "")
        self._stream("labels", """(function() {
          var filter = %s; var chart = %s; var studies = chart.getAllStudies(); var results = [];
          for (var i = 0; i < studies.length; i++) {
            var s = studies[i];
            if (filter && (s.name || '').toLowerCase().indexOf(filter.toLowerCase()) === -1) continue;
            try {
              var study = chart.getStudyById(s.id); if (!study) continue;
              var src = study._study || study;
              var g = src._graphics || (src._source && src._source._graphics);
              if (!g || !g._primitivesCollection || !g._primitivesCollection.dwglabels) continue;
              var data = g._primitivesCollection.dwglabels.get('labels').get(false);
              if (!data || !data._primitivesDataById) continue;
              var labels = [];
              data._primitivesDataById.forEach(function(lbl) {
                var text = lbl.text || '';
                var price = lbl.points && lbl.points[0] ? lbl.points[0].price : null;
                if (text) labels.push({ text: text, price: price });
              });
              if (labels.length) results.push({ study: s.name, labels: labels.slice(0, 50) });
            } catch(e) {}
          }
          return { symbol: chart.symbol(), study_count: results.length, studies: results };
        })()""" % (f, CHART_API), interval)

    def stream_tables(self, interval=2.0, filter=""):
        f = _js(filter or "")
        self._stream("tables", """(function() {
          var filter = %s; var chart = %s; var studies = chart.getAllStudies(); var results = [];
          for (var i = 0; i < studies.length; i++) {
            var s = studies[i];
            if (filter && (s.name || '').toLowerCase().indexOf(filter.toLowerCase()) === -1) continue;
            try {
              var study = chart.getStudyById(s.id); if (!study) continue;
              var src = study._study || study;
              var g = src._graphics || (src._source && src._source._graphics);
              if (!g || !g._primitivesCollection || !g._primitivesCollection.ownFirstValue) continue;
              var tableMap = g._primitivesCollection.ownFirstValue();
              if (!tableMap || typeof tableMap.forEach !== 'function') continue;
              var tables = [];
              tableMap.forEach(function(table) {
                if (!table || !table.data) return;
                var rows = [];
                for (var r = 0; r < table.data.length; r++) {
                  var row = [];
                  for (var c = 0; c < table.data[r].length; c++) row.push(table.data[r][c].text || '');
                  rows.push(row);
                }
                tables.push({ rows: rows });
              });
              if (tables.length) results.push({ study: s.name, tables: tables });
            } catch(e) {}
          }
          return { symbol: chart.symbol(), study_count: results.length, studies: results };
        })()""" % (f, CHART_API), interval)

    def stream_all_panes(self, interval=0.5):
        self._stream("all-panes", """(function() {
          var cwc = %s;
          var all = cwc.getAll();
          var count = cwc.inlineChartsCount;
          if (typeof count === 'object' && count && typeof count.value === 'function') count = count.value();
          var panes = [];
          for (var i = 0; i < Math.min(all.length, count || all.length); i++) {
            try {
              var c = all[i];
              var model = c.model();
              var ms = model.mainSeries();
              var bars = ms.bars();
              var v = bars.valueAt(bars.lastIndex());
              if (!v) { panes.push({ index: i, symbol: ms.symbol(), error: 'no bars' }); continue; }
              panes.push({ index: i, symbol: ms.symbol(), resolution: ms.interval(), time: v[0], open: v[1], high: v[2], low: v[3], close: v[4], volume: v[5] || 0 });
            } catch(e) { panes.push({ index: i, error: e.message }); }
          }
          return { pane_count: panes.length, panes: panes };
        })()""" % CWC, interval)


def _bare(s):
    return (s or "").split(":")[-1].upper()


def _date_to_ts(date):
    """Parse YYYY-MM-DD (or common ISO) to a unix timestamp in seconds."""
    import datetime as _dt
    s = str(date).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return _dt.datetime.strptime(s[:19], fmt).timestamp()
        except ValueError:
            continue
    raise ValueError(f"Could not parse date: {date!r}. Use YYYY-MM-DD.")
