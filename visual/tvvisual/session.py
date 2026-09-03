"""Chart session — the programmatic foundation for construct + visualize + validate.

A `Chart` is a long-lived headful CloakBrowser session over CDP. It lets an agent
construct a chart from numerical findings (set symbol/timeframe, add indicators,
draw levels/zones/labels), visualize it (screenshot for humans/vision models),
and validate it (read-back + tolerance assertions so both agents and humans can
confirm what was computed actually appears on the chart).
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from cloakbrowser import ensure_binary, launch_persistent_context

from .creds import load_creds
from .tools import ChartTools

# TradingView web-app internals (window.TradingViewApi), ported from tradingview-mcp.
CHART_API = "window.TradingViewApi._activeChartWidgetWV.value()"  # control surface
CHART_WIDGET = CHART_API + "._chartWidget"                        # internal model
BARS_PATH = CHART_WIDGET + ".model().mainSeries().bars()"

DEFAULT_PROFILE = Path.home() / ".tvvisual" / "profile"

_js = json.dumps  # emit a Python value as a JS literal (None->null, str->"..", num->123)


def _round(v):
    return None if v is None else round(v * 1e8) / 1e8


class Chart(ChartTools):
    """Headful TradingView chart session (construct / visualize / validate)."""

    def __init__(self, creds=None, env_path=None, profile_dir=None,
                 headless=False, humanize=False, viewport=None):
        self._creds = creds if creds is not None else load_creds(env_path)
        self._profile = Path(profile_dir or DEFAULT_PROFILE)
        self._headless = headless
        self._humanize = humanize
        self._viewport = viewport
        self.ctx = None
        self.page = None
        self.cdp = None

    # ------------------------------------------------------------------ lifecycle

    def _ensure_open(self):
        if self.ctx is not None:
            return
        kwargs = {"headless": self._headless, "humanize": self._humanize}
        if self._viewport is not None:
            kwargs["viewport"] = self._viewport
        self.ctx = launch_persistent_context(str(self._profile), **kwargs)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.page.goto("https://www.tradingview.com/", wait_until="domcontentloaded")
        self._inject_session()
        self.page.goto("https://www.tradingview.com/chart/", wait_until="domcontentloaded")
        time.sleep(3)  # let the chart widget materialize
        self.cdp = self.ctx.new_cdp_session(self.page)
        self.cdp.send("Runtime.enable")
        self.cdp.send("Page.enable")

    def open(self, symbol=None, timeframe=None):
        """Ensure the browser is up, navigate to the chart, apply symbol + timeframe."""
        self._ensure_open()
        if symbol:
            self.page.goto(
                "https://www.tradingview.com/chart/?symbol=" + symbol,
                wait_until="domcontentloaded")
            time.sleep(3)
        if timeframe:
            self.set_timeframe(timeframe)
        return self

    def close(self):
        if self.ctx is not None:
            try:
                self.ctx.close()
            except Exception:
                pass
            self.ctx = self.page = self.cdp = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _inject_session(self):
        c = self._creds
        cookies = []
        if c.get("session"):
            cookies.append({"name": "sessionid", "value": c["session"],
                            "domain": ".tradingview.com", "path": "/"})
        if c.get("signature"):
            cookies.append({"name": "sessionid_sign", "value": c["signature"],
                            "domain": ".tradingview.com", "path": "/"})
        if c.get("device_t"):
            cookies.append({"name": "device_t", "value": c["device_t"],
                            "domain": ".tradingview.com", "path": "/"})
        if cookies:
            self.ctx.add_cookies(cookies)

    def account(self):
        """Logged-in TradingView username (from the user menu aria-label), or None."""
        self._ensure_open()
        return self.eval("""(function() {
          var btn = document.querySelector('[aria-label^="Logged in as"]');
          if (!btn) return null;
          return (btn.getAttribute('aria-label') || '').split('\\n')[0].replace('Logged in as ', '').trim();
        })()""")

    def relogin(self):
        """Force the profile onto the .env account: clear TV cookies, re-inject
        the env session, reload, and report the resulting account. Needed because
        the persistent profile otherwise keeps whichever account last logged in,
        which can silently diverge from the .env account tvcli uses."""
        self._ensure_open()
        try:
            self.ctx.clear_cookies()
        except Exception:
            pass
        self._inject_session()
        self.page.goto("https://www.tradingview.com/chart/", wait_until="domcontentloaded")
        time.sleep(6)
        self.cdp = self.ctx.new_cdp_session(self.page)
        self.cdp.send("Runtime.enable")
        self.cdp.send("Page.enable")
        user = self.account()
        return {"success": user is not None, "account": user}

    def eval(self, expression, await_promise=False):
        """Evaluate JS in the chart page over CDP; return the value (by-value)."""
        self._ensure_open()
        params = {"expression": expression, "returnByValue": True}
        if await_promise:
            params["awaitPromise"] = True
        res = self.cdp.send("Runtime.evaluate", params)
        if res.get("exceptionDetails"):
            exc = res["exceptionDetails"].get("exception", {})
            raise RuntimeError(exc.get("description") or exc.get("text") or "JS error")
        return res.get("result", {}).get("value")

    # ------------------------------------------------------------------ construct

    def set_symbol(self, symbol):
        self.eval("""
        (function() {
          var chart = %s;
          return new Promise(function(resolve) {
            chart.setSymbol(%s, {});
            setTimeout(resolve, 500);
          });
        })()
        """ % (CHART_API, _js(symbol)), await_promise=True)
        return {"success": True, "symbol": symbol}

    def set_timeframe(self, timeframe):
        self.eval("""
        (function() { var chart = %s; chart.setResolution(%s, {}); })()
        """ % (CHART_API, _js(timeframe)))
        time.sleep(1.5)
        return {"success": True, "timeframe": timeframe}

    def add_indicator(self, name, inputs=None, script=None):
        """Add a study to the chart.

        `name` is a built-in title/study-ID. To apply a custom Pine script the
        same way tvcli's WS `create_study` does (descriptor {type:'pine',
        pineId, pineVersion}, then setInputValues), pass `script` as
        {pineId, pineVersion?} and `inputs` as flat in_N overrides. This avoids
        the indicators dialog entirely. Verified descriptor shape (scouted live
        from model().createStudyInserter during a dialog add):
        {type:'pine', pineId:'PUB;...', pineVersion:'1.0'}.
        """
        before = self.eval("%s.getAllStudies().map(function(s){return s.id;})" % CHART_API) or []
        if script:
            desc = {
                "type": "pine",
                "pineId": script.get("pineId", ""),
                "pineVersion": script.get("pineVersion", "1.0"),
            }
            self.eval("""
            (function() {
              var chart = %s;
              chart.createStudy(%s);
            })()
            """ % (CHART_API, _js(desc)))
        else:
            self.eval("""
            (function() { var chart = %s; chart.createStudy(%s, false, false, %s); })()
            """ % (CHART_API, _js(name), _js(inputs or [])))
        time.sleep(2.5)
        after = self.eval("%s.getAllStudies().map(function(s){return s.id;})" % CHART_API) or []
        new_ids = [i for i in after if i not in before]
        entity_id = new_ids[0] if new_ids else None
        if entity_id and inputs:
            self.set_indicator_inputs(entity_id, inputs)
        return {"success": bool(new_ids), "action": "add", "indicator": name,
                "entity_id": entity_id, "script": bool(script)}

    def set_indicator_inputs(self, entity_id, inputs):
        self.eval("""
        (function() {
          var chart = %s;
          var study = chart.getStudyById(%s);
          if (!study || typeof study.getInputValues !== 'function') return { error: 'inputs unsupported' };
          var cur = study.getInputValues();
          var overrides = %s;
          var byId = {};
          for (var i = 0; i < cur.length; i++) byId[cur[i].id] = true;
          for (var k in overrides) {
            for (var j = 0; j < cur.length; j++) { if (cur[j].id === k) cur[j].value = overrides[k]; }
          }
          study.setInputValues(cur);
          return { applied: overrides };
        })()
        """ % (CHART_API, _js(entity_id), _js(inputs)))
        return {"success": True, "entity_id": entity_id, "inputs": inputs}

    def remove_indicator(self, entity_id):
        self.eval("""
        (function() { var chart = %s; chart.removeEntity(%s); })()
        """ % (CHART_API, _js(entity_id)))
        return {"success": True, "action": "remove", "entity_id": entity_id}

    # --- drawings (agent-computed numbers -> visible shapes) ---

    def _bar_range(self):
        r = self.eval("""
        (function() {
          var bars = %s;
          if (!bars || typeof bars.lastIndex !== 'function') return null;
          var f = bars.valueAt(bars.firstIndex());
          var l = bars.valueAt(bars.lastIndex());
          return { first: f ? f[0] : null, last: l ? l[0] : null };
        })()
        """ % BARS_PATH) or {}
        return r.get("first"), r.get("last")

    def draw_level(self, price, label=None, color="#ff9800", width=2):
        """Horizontal line spanning the loaded bars at `price`."""
        f, l = self._bar_range()
        overrides = {"linecolor": color, "linewidth": width}
        self.eval("""
        %s.createMultipointShape(
          [{ time: %s, price: %s }, { time: %s, price: %s }],
          { shape: 'horizontal_line', overrides: %s, text: %s })
        """ % (CHART_API, _js(f), _js(price), _js(l), _js(price),
               _js(overrides), _js(label or "")))
        time.sleep(0.3)
        return {"success": True, "kind": "level", "price": price, "label": label}

    def draw_zone(self, high, low, label=None, color="#2962ff"):
        """Rectangle spanning the loaded bars between `high` and `low`."""
        f, l = self._bar_range()
        overrides = {"linecolor": color, "fillcolor": color, "transparency": 70}
        self.eval("""
        %s.createMultipointShape(
          [{ time: %s, price: %s }, { time: %s, price: %s }],
          { shape: 'rectangle', overrides: %s, text: %s })
        """ % (CHART_API, _js(f), _js(high), _js(l), _js(low),
               _js(overrides), _js(label or "")))
        time.sleep(0.3)
        return {"success": True, "kind": "zone", "high": high, "low": low, "label": label}

    def draw_label(self, text, price):
        """Text annotation at `price` (right-aligned near the latest bar)."""
        f, l = self._bar_range()
        self.eval("""
        %s.createShape(
          { time: %s, price: %s },
          { shape: 'text', overrides: {}, text: %s })
        """ % (CHART_API, _js(l), _js(price), _js(text)))
        time.sleep(0.3)
        return {"success": True, "kind": "label", "text": text, "price": price}

    def draw(self, spec):
        """Dispatch a draw request: {kind: level|zone|label, ...}."""
        kind = spec.get("kind")
        if kind == "level":
            return self.draw_level(spec["price"], spec.get("label"), spec.get("color", "#ff9800"))
        if kind == "zone":
            return self.draw_zone(spec["high"], spec["low"], spec.get("label"), spec.get("color", "#2962ff"))
        if kind == "label":
            return self.draw_label(spec["text"], spec.get("price"))
        raise ValueError(f"unknown draw kind: {kind!r}")

    def draw_levels(self, levels, offset_ratio=0.002):
        """Draw a list of agent-computed levels: each = horizontal line (colored)
        plus a visible text label "NAME PRICE" slightly above the line.

        levels: [{price, name, color?}]  — a line + label
                [{kind:'label', label, price}] — text-only (e.g. bias)
        """
        out = []
        for lv in levels or []:
            price = lv.get("price")
            if price is None:
                continue
            if lv.get("kind") == "label":
                out.append(self.draw_label(lv.get("label") or "", price))
                continue
            name = lv.get("name") or lv.get("label")
            color = lv.get("color", "#ff9800")
            out.append(self.draw_level(price, name, color))
            if name:
                off = max(abs(price) * offset_ratio, 0.5)
                out.append(self.draw_label(f"{name} {price}", price + off))
        return {"success": True, "kind": "levels", "count": len(levels or []),
                "drawn": out}

    def fit_price(self):
        """Ensure price auto-scale is on so visible bars + drawn levels fit."""
        r = self.eval("""
        (function() {
          var m = %s.model();
          var ps = m.mainSeries().priceScale();
          var before = (ps && typeof ps.isAutoScale === 'function') ? ps.isAutoScale() : null;
          if (before === false) { m.togglePriceScaleAutoScaleMode(ps); }
          return { was_auto: before,
                   auto: (ps && typeof ps.isAutoScale === 'function') ? ps.isAutoScale() : null };
        })()
        """ % CHART_WIDGET)
        time.sleep(0.5)
        return {"success": True, "fit": r}

    def clear_drawings(self):
        self.eval("%s.removeAllShapes()" % CHART_API)
        return {"success": True, "action": "all_shapes_removed"}

    # ------------------------------------------------------------------ read

    def state(self):
        return self.eval("""
        (function() {
          var chart = %s;
          var studies = [];
          try {
            var all = chart.getAllStudies();
            studies = all.map(function(s) {
              return { id: s.id, name: s.name || s.title || 'unknown' };
            });
          } catch(e) {}
          var account = null;
          try {
            var btn = document.querySelector('[aria-label^="Logged in as"]');
            if (btn) account = (btn.getAttribute('aria-label') || '').split('\\n')[0].replace('Logged in as ', '').trim();
          } catch(e) {}
          return {
            account: account,
            symbol: chart.symbol(),
            resolution: chart.resolution(),
            chartType: chart.chartType(),
            studies: studies
          };
        })()
        """ % CHART_API)

    def ohlcv(self, count=100, summary=False):
        js = """
        (function() {
          var bars = %s;
          if (!bars || typeof bars.lastIndex !== 'function') return null;
          var result = [];
          var end = bars.lastIndex();
          var start = Math.max(bars.firstIndex(), end - %d + 1);
          for (var i = start; i <= end; i++) {
            var v = bars.valueAt(i);
            if (v) result.push({time: v[0], open: v[1], high: v[2], low: v[3], close: v[4], volume: v[5] || 0});
          }
          return { bars: result, total_bars: bars.size() };
        })()
        """ % (BARS_PATH, count)
        data = self.eval(js)
        if not data or not data.get("bars"):
            return {"success": False, "error": "Could not extract OHLCV (chart still loading?)"}
        bars = data["bars"]
        if summary:
            first, last = bars[0], bars[-1]
            return {"success": True, "bar_count": len(bars),
                    "period": {"from": first["time"], "to": last["time"]},
                    "open": first["open"], "close": last["close"],
                    "high": max(b["high"] for b in bars),
                    "low": min(b["low"] for b in bars),
                    "last_5_bars": bars[-5:]}
        return {"success": True, "bar_count": len(bars),
                "total_available": data.get("total_bars"), "bars": bars}

    def studies(self):
        data = self.eval("""
        (function() {
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
        })()
        """ % CHART_WIDGET)
        return {"success": True, "study_count": len(data or []), "studies": data or []}

    def _graphics(self, collection, map_key, filter_=""):
        return self.eval("""
        (function() {
          var chart = %s;
          var model = chart.model();
          var sources = model.model().dataSources();
          var results = [];
          var filter = %s;
          for (var si = 0; si < sources.length; si++) {
            var s = sources[si];
            if (!s.metaInfo) continue;
            try {
              var meta = s.metaInfo();
              var name = meta.description || meta.shortDescription || '';
              if (!name) continue;
              if (filter && name.indexOf(filter) === -1) continue;
              var g = s._graphics;
              if (!g || !g._primitivesCollection) continue;
              var pc = g._primitivesCollection;
              var items = [];
              try {
                var outer = pc.%s;
                if (outer) {
                  var inner = outer.get(%s);
                  if (inner) {
                    var coll = inner.get(false);
                    if (coll && coll._primitivesDataById && coll._primitivesDataById.size > 0) {
                      coll._primitivesDataById.forEach(function(v, id) { items.push({id: id, raw: v}); });
                    }
                  }
                }
              } catch(e) {}
              if (items.length > 0) results.push({ name: name, count: items.length, items: items });
            } catch(e) {}
          }
          return results;
        })()
        """ % (CHART_WIDGET, _js(filter_ or ""), collection, _js(map_key)))

    def lines(self, filter_=""):
        raw = self._graphics("dwglines", "lines", filter_) or []
        studies = []
        for s in raw:
            levels, seen = [], {}
            for item in s["items"]:
                v = item["raw"]
                y = _round(v.get("y1"))
                if y is not None and v.get("y1") == v.get("y2") and y not in seen:
                    levels.append(y); seen[y] = True
            levels.sort(reverse=True)
            studies.append({"name": s["name"], "total_lines": s["count"], "horizontal_levels": levels})
        return {"success": True, "study_count": len(studies), "studies": studies}

    def labels(self, filter_="", max_labels=50):
        raw = self._graphics("dwglabels", "labels", filter_) or []
        studies = []
        for s in raw:
            labs = [{"text": item["raw"].get("t") or "", "price": _round(item["raw"].get("y"))}
                    for item in s["items"]]
            labs = [l for l in labs if l["text"] or l["price"] is not None]
            studies.append({"name": s["name"], "total_labels": s["count"],
                            "showing": len(labs[-max_labels:]), "labels": labs[-max_labels:]})
        return {"success": True, "study_count": len(studies), "studies": studies}

    def boxes(self, filter_=""):
        raw = self._graphics("dwgboxes", "boxes", filter_) or []
        studies = []
        for s in raw:
            zones, seen = [], {}
            for item in s["items"]:
                v = item["raw"]
                y1, y2 = v.get("y1"), v.get("y2")
                if y1 is None or y2 is None:
                    continue
                high, low = _round(max(y1, y2)), _round(min(y1, y2))
                key = f"{high}:{low}"
                if key not in seen:
                    zones.append({"high": high, "low": low}); seen[key] = True
            zones.sort(key=lambda z: -z["high"])
            studies.append({"name": s["name"], "total_boxes": s["count"], "zones": zones})
        return {"success": True, "study_count": len(studies), "studies": studies}

    def tables(self, filter_=""):
        raw = self._graphics("dwgtablecells", "tableCells", filter_) or []
        studies = []
        for s in raw:
            tables = {}
            for item in s["items"]:
                v = item["raw"]
                tid = v.get("tid", 0)
                tables.setdefault(tid, {}).setdefault(v.get("row"), {})[v.get("col")] = v.get("t") or ""
            formatted = []
            for tid, rows in tables.items():
                lines = []
                for rn in sorted(rows, key=lambda k: (k is None, k)):
                    cols = rows[rn]
                    lines.append(" | ".join(cols[cn] for cn in sorted(cols, key=lambda k: (k is None, k)) if cols[cn]))
                formatted.append({"rows": lines})
            studies.append({"name": s["name"], "tables": formatted})
        return {"success": True, "study_count": len(studies), "studies": studies}

    def read_shapes(self):
        """All drawn shapes (drawings) with their price points — for validation."""
        shapes = self.eval("""
        (function() {
          var api = %s;
          var all = api.getAllShapes();
          var out = [];
          for (var i = 0; i < all.length; i++) {
            var s = all[i];
            var e = { id: s.id, name: s.name || null, points: [] };
            try {
              var sh = api.getShapeById(s.id);
              if (sh && sh.getPoints) {
                e.points = sh.getPoints().map(function(p) { return { time: p.time, price: p.price }; });
              }
            } catch(err) {}
            out.push(e);
          }
          return out;
        })()
        """ % CHART_API) or []
        return {"success": True, "count": len(shapes), "shapes": shapes}

    # ------------------------------------------------------------------ visualize

    def screenshot(self, path=None, region="chart"):
        params = {"format": "png"}
        if region == "chart":
            bounds = self.eval("""
            (function() {
              var el = document.querySelector('[data-name="pane-canvas"]')
                || document.querySelector('[class*="chart-container"]')
                || document.querySelector('canvas');
              if (!el) return null;
              var r = el.getBoundingClientRect();
              return { x: r.x, y: r.y, width: r.width, height: r.height, scale: 2 };
            })()
            """)
            if bounds:
                params["clip"] = bounds
        res = self.cdp.send("Page.captureScreenshot", params)
        data = base64.b64decode(res["data"])
        if path is None:
            ts = time.strftime("%Y%m%d-%H%M%S")
            path = f"tv_{region}_{ts}.png"
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)
        return {"success": True, "file_path": str(path), "region": region, "size_bytes": len(data)}

    # ------------------------------------------------------------ display / chrome

    def hide_chrome(self):
        """Show only the chart: enter TradingView fullscreen (hides header + left
        toolbar) and close the right watchlist rail — TradingView recomputes layout."""
        done = self.eval("""
        (function() {
          var done = [];
          var header = document.querySelector('[class*="layout__area--top"]');
          var fs = document.querySelector('[data-name="header-toolbar-fullscreen"]');
          if (header && fs && header.offsetHeight > 5) { fs.click(); done.push('fullscreen'); }
          var right = document.querySelector('[class*="layout__area--right"]');
          var base = document.querySelector('[data-name="base"]');
          if (right && base && right.offsetWidth > 60) { base.click(); done.push('watchlist'); }
          return done;
        })()
        """)
        time.sleep(2.5)  # let TradingView recompute layout + re-render the canvas
        return {"success": True, "actions": done or ["already_clean"]}

    def show_chrome(self):
        """Exit fullscreen (restore header/left toolbar)."""
        done = self.eval("""
        (function() {
          var done = [];
          var header = document.querySelector('[class*="layout__area--top"]');
          var fs = document.querySelector('[data-name="header-toolbar-fullscreen"]');
          if (header && fs && header.offsetHeight <= 5) { fs.click(); done.push('exit-fullscreen'); }
          return done;
        })()
        """)
        time.sleep(2)
        return {"success": True, "actions": done or ["already_visible"]}

    def zoom_bars(self, bars=150):
        """Zoom the chart to the last `bars` candles (optimized, readable scaling)."""
        r = self.eval("""
        (function() {
          var chart = %s;
          var ts = chart.model().timeScale();
          var b = chart.model().mainSeries().bars();
          var end = b.lastIndex();
          var start = Math.max(b.firstIndex(), end - %d + 1);
          ts.zoomToBarsRange(start, end);
          return { start: start, end: end };
        })()
        """ % (CHART_WIDGET, bars))
        time.sleep(1)
        return {"success": True, "bars": bars, "range": r}

    def show(self, cfg):
        """Construct a clean chart view from a config dict:
        {symbol, timeframe, hide_chrome, bars, screenshot, region}. Returns a summary."""
        symbol, timeframe = cfg.get("symbol"), cfg.get("timeframe")
        self.open(symbol, timeframe)
        if cfg.get("hide_chrome", True):
            self.hide_chrome()
        if cfg.get("bars"):
            self.zoom_bars(cfg["bars"])
        shot = None
        if cfg.get("screenshot"):
            shot = self.screenshot(cfg["screenshot"], cfg.get("region", "chart"))
        return {"success": True, "symbol": symbol, "timeframe": timeframe,
                "state": self.state(), "shot": shot}

    # ------------------------------------------------------------------ validate

    @staticmethod
    def _near(a, b, tol):
        return a is not None and b is not None and abs(a - b) <= tol

    def validate(self, checks):
        """Confirm agent-computed numbers/text appear on the chart (read-back + tolerance).

        checks: list of dicts —
          {"kind":"level","value":4518.7,"tol":0.5}        -> a drawn horizontal line
          {"kind":"zone","high":4520,"low":4480,"tol":0.5}-> a drawn rectangle
          {"kind":"label","value":"Bias Long"}            -> a shape/label with this text
          {"kind":"study","value":55.0,"tol":1.0}         -> an indicator data-window value
        Returns per-check {name, kind, expect, actual, ok} plus overall success.
        """
        shapes = self.read_shapes().get("shapes", [])
        studies = self.studies().get("studies", [])

        shape_prices, shape_texts, shape_zones = [], [], []
        for s in shapes:
            pts = [p.get("price") for p in s.get("points", []) if p.get("price") is not None]
            shape_prices.extend(pts)
            if len(pts) >= 2:
                shape_zones.append({"high": max(pts), "low": min(pts)})
            if s.get("name"):
                shape_texts.append(s["name"])

        # Per-study numeric values, so a `study` check can be scoped to the
        # study it names instead of matching any value on the chart.
        study_entries = []
        for st in studies:
            vals = []
            for v in st.get("values", {}).values():
                try:
                    vals.append(float(str(v).replace(",", "")))
                except (TypeError, ValueError):
                    pass
            study_entries.append({"name": st.get("name"), "values": vals})

        results = []
        for c in checks:
            kind = c.get("kind")
            tol = c.get("tol", 0)
            expect = c.get("value")
            name = c.get("name", kind)
            actual, ok = None, False

            if kind == "level":
                for p in shape_prices:
                    if self._near(p, expect, tol):
                        actual, ok = p, True
                        break
            elif kind == "zone":
                hi, lo = c.get("high"), c.get("low")
                expect = {"high": hi, "low": lo}
                for z in shape_zones:
                    if self._near(z["high"], hi, tol) and self._near(z["low"], lo, tol):
                        actual, ok = z, True
                        break
            elif kind == "label":
                if expect is None:
                    actual = "no value to match"
                else:
                    needle = str(expect)
                    for t in shape_texts:
                        if needle in t:
                            actual, ok = t, True
                            break
            elif kind == "study":
                target = None
                if name and name != "study":
                    target = name
                for entry in study_entries:
                    if target is not None and entry["name"] != target:
                        continue
                    for v in entry["values"]:
                        if self._near(v, expect, tol):
                            actual, ok = v, True
                            break
                    if ok:
                        break
            else:
                actual = f"unknown kind {kind!r}"

            results.append({"name": name, "kind": kind, "expect": expect,
                            "actual": actual, "ok": ok})

        return {"success": all(r["ok"] for r in results), "checks": results}


def install() -> str:
    """Download the stealth Chromium binary (idempotent)."""
    return ensure_binary()