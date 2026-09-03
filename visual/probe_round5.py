#!/usr/bin/env python3
"""Round-5 live probe: (A) closed-bar study plot reads, (B) drawing state DTO + restore.

Run from visual/:  python3 probe_round5.py
Dumps full detail to ~/.tvvisual/scout/probes/round5.json and prints a summary.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from tvvisual.session import Chart, CHART_API, CHART_WIDGET  # noqa: E402

KB = Path.home() / ".tvvisual" / "scout" / "probes"
KB.mkdir(parents=True, exist_ok=True)

out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S")}


def intro_js():
    """Walk the prototype chain of a study source and probe candidate data accessors."""
    return (
        "(function(){ var chart = %s; var sources = chart.model().model().dataSources(); "
        "var res = []; "
        "for (var si = 0; si < sources.length; si++) { "
        "var s = sources[si]; var entry = {}; "
        "try { var m = s.metaInfo(); entry.name = m ? (m.description || m.shortDescription || '') : null; } catch(e) { entry.meta_error = e.message; } "
        "try { var proto = s; var methods = []; while (proto && proto !== Object.prototype) { var ns = Object.getOwnPropertyNames(proto); for (var i = 0; i < ns.length; i++) { if (typeof s[ns[i]] === 'function' && methods.indexOf(ns[i]) === -1) methods.push(ns[i]); } proto = Object.getPrototypeOf(proto); } entry.methods = methods.slice(0, 200); } catch(e) { entry.methods_error = e.message; } "
        "var cands = {}; "
        "['plot','plotData','plots','series','seriesList','source','data','dataView','barData','buffer','value','getValue','item','items','plotIndex','paneIndex'].forEach(function(n) { "
        "try { if (typeof s[n] === 'function') { var r = s[n](); cands[n] = Array.isArray(r) ? ('array len ' + r.length) : (r === null ? 'null' : (typeof r)); } else if (s[n] !== undefined) { cands[n] = typeof s[n]; } } catch(e) { cands[n] = 'throws: ' + e.message.slice(0, 60); } "
        "}); entry.candidates = cands; res.push(entry); } return res; })()" % CHART_WIDGET
    )


def dwnv_js():
    """Inspect one study's dataWindowView items: keys, _index, whether value is callable per-index."""
    return (
        "(function(){ var chart = %s; var sources = chart.model().model().dataSources(); "
        "for (var si = 0; si < sources.length; si++) { var s = sources[si]; "
        "try { var dwv = s.dataWindowView(); if (!dwv) continue; var items = dwv.items(); if (!items || !items.length) continue; "
        "var first = items[0]; "
        "var dwvProto = Object.getOwnPropertyNames(Object.getPrototypeOf(dwv) || {}); "
        "var sample = { name: (s.metaInfo()||{}).description, keys: Object.keys(first).slice(0,40), protoKeys: Object.getOwnPropertyNames(Object.getPrototypeOf(first) || {}).slice(0,60), "
        "item0_title: first._title, item0_value: first._value, item0_index: first._index, "
        "callable_value: typeof first.value, items_len: items.length, dwv_methods: dwvProto.slice(0,60) }; "
        "var r = { name: sample.name, dwv: { item0: sample } }; "
        "['item','items','index','setIndex','getIndex','value','update','refresh'].forEach(function(n) { "
        "try { if (typeof dwv[n] === 'function') { var rr = dwv[n](); r.dwv['call_' + n] = Array.isArray(rr) ? ('array len ' + rr.length) : (rr === null ? 'null' : (typeof rr)); } } catch(e) { r.dwv['call_' + n] = 'throws ' + e.message.slice(0,50); } "
        "}); "
        "try { if (typeof dwv.item === 'function') { var it5 = dwv.item(5); r.dwv.item5 = it5 ? { title: it5._title, value: it5._value, index: it5._index } : null; var itN = dwv.item(items.length - 2); r.dwv.item_closed = itN ? { title: itN._title, value: itN._value, index: itN._index } : null; } } catch(e) { r.dwv.item_err = e.message.slice(0,60); } "
        "r.items = []; "
        "for (var i = 1; i < Math.min(items.length, 6); i++) { r.items.push({ title: items[i]._title, value: items[i]._value, index: items[i]._index }); } "
        "return r; } catch(e) {} } return null; })()" % CHART_WIDGET
    )


def bars_js():
    return (
        "(function(){ var chart = %s; var ms = chart.model().mainSeries(); var out = {}; "
        "try { var bd = ms.barData ? ms.barData() : null; if (bd) { out.len = bd.length; "
        "out.last = { time: bd[bd.length-1].time, close: bd[bd.length-1].close }; "
        "out.closed = { time: bd[bd.length-2].time, close: bd[bd.length-2].close }; "
        "out.bar_keys = Object.keys(bd[bd.length-1]); } else { out.len = null; } } catch(e) { out.err = e.message; } "
        "try { out.bars_len = ms.bars().length; } catch(e) {} "
        "return out; })()" % CHART_WIDGET
    )


def drawing_scan_js():
    """Enumerate drawing/sync/state related members on chart widget + collection + load service."""
    return (
        "(function(){ var api = %s; var w = api._chartWidget; var res = {}; "
        "function scan(obj, label) { if (!obj) { res[label] = 'missing'; return; } "
        "var hits = {}; var proto = obj; var names = []; "
        "while (proto && proto !== Object.prototype) { names = names.concat(Object.getOwnPropertyNames(proto)); proto = Object.getPrototypeOf(proto); } "
        "for (var i = 0; i < names.length; i++) { var n = names[i]; if (/draw|lineTool|sync|layout|state/i.test(n)) { try { var t = typeof obj[n]; hits[n] = (t === 'function') ? 'fn args~' + obj[n].length : t; } catch(e) { hits[n] = 'err'; } } } "
        "res[label] = hits; } "
        "scan(w, 'chartWidget'); "
        "scan(api._chartWidgetCollection, 'chartWidgetCollection'); "
        "scan(api._chartWidgetCollection, 'chartWidgetCollection'); "
        "scan(window.TradingViewApi._loadChartService, 'loadChartService'); "
        "scan(window.TradingViewApi._saveChartService, 'saveChartService'); "
        "try { res.lineToolsStateKeys = (function(){ var st = api.getLineToolsState ? api.getLineToolsState() : null; if (!st) return 'no-getLineToolsState'; var o = {}; o.keys = Object.keys(st); if (st.sources) o.sources_len = st.sources.length; if (st.sources && st.sources[0]) o.source0_keys = Object.keys(st.sources[0]); if (st.state) o.state_keys = Object.keys(st.state); return o; })(); } catch(e) { res.lineToolsStateKeys = 'throws: ' + e.message; } "
        "return res; })()" % CHART_API
    )


def load_sig_js():
    """Show the source of loadLayoutState (first 400 chars) to learn its options param."""
    return (
        "(function(){ var api = %s; var c = api._chartWidgetCollection; var res = {}; "
        "try { res.loadLayoutState = c.loadLayoutState ? c.loadLayoutState.toString().slice(0, 500) : 'missing'; } catch(e) { res.loadLayoutState = 'err ' + e.message; } "
        "try { res.getRecentLayouts = typeof c.getRecentLayouts; } catch(e) {} "
        "try { var ls = window.TradingViewApi._loadChartService; res.ls_methods = (function(){ var o = {}; var proto = ls; var names = []; while (proto && proto !== Object.prototype) { names = names.concat(Object.getOwnPropertyNames(proto)); proto = Object.getPrototypeOf(proto); } for (var i = 0; i < names.length; i++) { if (/load|layout|json/i.test(names[i]) && typeof ls[names[i]] === 'function') o[names[i]] = ls[names[i]].length; } return o; })(); } catch(e) { res.ls_methods = 'err ' + e.message; } "
        "return res; })()" % CHART_API
    )


def export_drawings_js():
    """saveToJSON then extract only the drawing sources (name/type/id + first 200 chars of content)."""
    return (
        "(function(){ var exp = window.TradingViewApi._saveChartService.saveToJSON(); "
        "var res = { top_keys: Object.keys(exp) }; "
        "var drawings = []; "
        "function walk(o, path) { if (!o || typeof o !== 'object') return; "
        "if (o.type === 'drawing' || o.kind === 'drawing') { var d = { path: path, id: o.id || null, name: o.name || null, keys: Object.keys(o).slice(0, 20) }; if (typeof o.content === 'string') { d.content_head = o.content.slice(0, 300); } drawings.push(d); return; } "
        "for (var k in o) { if (o.hasOwnProperty(k)) { if (typeof o[k] === 'string' && o[k].length > 10 && o[k].charAt(0) === '{') { try { walk(JSON.parse(o[k]), path + '.' + k + '(json)'); } catch(e) {} } else if (o[k] && typeof o[k] === 'object') { walk(o[k], path + '.' + k); } } } } "
        "walk(exp, 'root'); res.drawings = drawings; res.drawing_count = drawings.length; return res; })()")


def main():
    with Chart(env_path="../.env") as ch:
        ch.relogin()
        out["account"] = ch.account()
        ch.open("PEPPERSTONE:XAUUSD", "5")
        time.sleep(3)
        r = ch.add_indicator_from_search("XAU Scalp Engine v2")
        out["add_indicator"] = {"success": r.get("success"), "entity_id": r.get("entity_id")}
        time.sleep(5)

        # ---------- A: closed bar ----------
        out["bars"] = ch.eval(bars_js())
        out["study_introspection"] = ch.eval(intro_js())
        out["dwnv_item_probe"] = ch.eval(dwnv_js())

        # ---------- B: drawings ----------
        out["shapes_before"] = ch.list_drawings()
        bars = out.get("bars") or {}
        closed = (bars or {}).get("closed") or {}
        last = (bars or {}).get("last") or {}
        # draw a trend line 20 bars back -> last closed bar, at closed close price
        if closed and last:
            t0 = closed["time"] - 20 * 300
            ch.draw_trend_line([t0, closed["close"]], [last["time"], closed["close"]], label="probe5")
            time.sleep(1)
            out["shapes_after_draw"] = ch.list_drawings()
        out["drawing_scan"] = ch.eval(drawing_scan_js())
        out["load_sig"] = ch.eval(load_sig_js())
        out["export_drawings"] = ch.eval(export_drawings_js())

        # restore attempt: snapshot -> clear -> loadLayoutState(snapshot) -> verify
        shapes_js = "(function(){ return %s.getAllShapes().map(function(s){ return { id: s.id, name: s.name }; }); })()" % CHART_API
        try:
            snap = ch.eval("(function(){ return JSON.stringify(window.TradingViewApi._saveChartService.saveToJSON()); })()")
            out["snapshot_bytes"] = len(snap or "")
            before = ch.eval(shapes_js)
            ch.clear_drawings()
            time.sleep(1)
            after_clear = ch.eval(shapes_js)
            ch.eval("(function(){ %s._chartWidgetCollection.loadLayoutState(JSON.parse(%s)); })()" % (CHART_API, json.dumps(snap)))
            time.sleep(2)
            after_restore = ch.eval(shapes_js)
            out["restore_test"] = {
                "before": before, "after_clear": after_clear, "after_restore": after_restore,
                "restored_ids_match": sorted([s["id"] for s in before]) == sorted([s["id"] for s in after_restore]),
            }
        except Exception as e:  # noqa: BLE001
            out["restore_test"] = {"error": str(e)}

    (KB / "round5.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str)[:6000])
    print("\n[saved] %s/round5.json" % KB)


if __name__ == "__main__":
    main()
