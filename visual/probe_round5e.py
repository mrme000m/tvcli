#!/usr/bin/env python3
"""Round-5e probe: m_bars container methods; _items per-bar row dump (closed-bar values); DTO map fix.

Run from visual/:  python3 probe_round5e.py
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


def bars2_js():
    """m_bars container: prototype methods + size + bar(index) + time/price of last two bars."""
    return (
        "(function(){ var chart = %s; var md = chart.model().mainSeries().data(); var mb = md.m_bars; "
        "var res = {}; function clean2(o) { try { return JSON.parse(JSON.stringify(o, function(k,v){ return typeof v === 'function' ? '[fn]' : v; })); } catch(e) { return 'clean-err ' + e.message.slice(0,40); } } "
        "try { var proto = mb, methods = []; "
        "while (proto && proto !== Object.prototype) { var ns = Object.getOwnPropertyNames(proto); for (var i = 0; i < ns.length; i++) { if (typeof mb[ns[i]] === 'function' && methods.indexOf(ns[i]) === -1) methods.push(ns[i]); } proto = Object.getPrototypeOf(proto); } "
        "res.methods = methods.slice(0, 120); res.keys = Object.keys(mb).slice(0, 20); } catch(e) { res.err = e.message.slice(0,120); } "
        "function call(fn) { try { return mb[fn](); } catch(e) { return 'err ' + e.message.slice(0,60); } } "
        "['size','length','count','firstIndex','lastIndex'].forEach(function(fn) { if (methods.indexOf(fn) !== -1) res['c_' + fn] = call(fn); }); "
        "function barAt(i) { var cands = ['bar','get','barAt','item','at','value','row','point','bars']; "
        "for (var k = 0; k < cands.length; k++) { var fn = cands[k]; if (typeof mb[fn] === 'function') { try { var r = mb[fn](i); if (r !== undefined) { res['barAt_used'] = fn; return r; } } catch(e) { res['barAt_used'] = fn + ':err ' + e.message.slice(0,40); } } } return null; } "
        "var sz = (typeof res.c_size === 'number') ? res.c_size : (typeof res.c_count === 'number' ? res.c_count : null); "
        "if (sz && sz > 2) { var b1 = barAt(sz-1), b0 = barAt(sz-2); "
        "res.bar_last = (b1 && typeof b1 === 'object') ? clean2(b1) : ('not-object: ' + (b1 === null ? 'null' : typeof b1)); "
        "res.bar_prev = (b0 && typeof b0 === 'object') ? clean2(b0) : ('not-object: ' + (b0 === null ? 'null' : typeof b0)); "
        "res.bar_keys = (b1 && typeof b1 === 'object') ? Object.keys(b1) : null; } "
        "return res; })()" % CHART_WIDGET
    )


def items_dump_js():
    """Dump last two _items rows fully (index + value structure) for the xau study."""
    return (
        "(function(){ var chart = %s; var sources = chart.model().model().dataSources(); "
        "for (var si = 0; si < sources.length; si++) { var s = sources[si]; var nm = null; "
        "try { nm = s.metaInfo ? s.metaInfo().description : null; } catch(e) {} "
        "if (nm !== 'XAUUSD Scalping Confluence Engine') continue; "
        "var items = s.data()._items; var n = items.length; "
        "var out2 = { len: n }; "
        "function clean(o) { return JSON.parse(JSON.stringify(o, function(k,v){ return typeof v === 'function' ? '[fn]' : v; })); } "
        "out2.last = clean(items[n-1]); out2.prev = clean(items[n-2]); "
        "var v = items[n-2].value; "
        "out2.prev_value_type = Array.isArray(v) ? ('array len ' + v.length) : typeof v; "
        "return out2; } return null; })()" % CHART_WIDGET
    )


def dto_fix_js():
    """DTO with sources treated as a map; per-source summary; full clean JSON."""
    return (
        "(function(){ var api = %s; var dto = api._chartWidget.lineToolsSynchronizer().getDTO(); "
        "function clean(o) { return JSON.parse(JSON.stringify(o, function(k,v){ return typeof v === 'function' ? undefined : v; })); } "
        "var srcs = dto.sources; var arr = Array.isArray(srcs) ? srcs : (srcs ? Object.values(srcs) : []); "
        "var summary = { sources_type: Array.isArray(srcs) ? 'array' : 'map', count: arr.length, "
        "sources: arr.map(function(s){ return { id: s.id, type: s.type, ownerSource: s.ownerSource || null, points: s.points ? s.points.length : null }; }), "
        "groups_type: dto.groups ? (Array.isArray(dto.groups) ? 'array' : 'map') : 'none', clientId: dto.clientId || null }; "
        "return { summary: summary, full: clean(dto) }; })()" % CHART_API
    )


def main():
    with Chart(env_path="../.env") as ch:
        ch.relogin()
        out["account"] = ch.account()
        ch.open("PEPPERSTONE:XAUUSD", "5")
        time.sleep(3)
        r = ch.add_indicator_from_search("XAU Scalp Engine v2")
        out["add_indicator"] = {"success": r.get("success"), "entity_id": r.get("entity_id")}
        time.sleep(5)

        out["bars2"] = ch.eval(bars2_js())
        out["items_dump"] = ch.eval(items_dump_js())
        out["shapes_before"] = ch.list_drawings()

        # ---- draw probe shape ----
        b2 = out.get("bars2") or {}
        prev = b2.get("bar_prev") or {}
        last = b2.get("bar_last") or {}

        def f(bar, names):
            for n in names:
                if isinstance(bar, dict) and bar.get(n) is not None:
                    return bar[n]
            return None

        t_prev = f(prev, ["time", "t", "timestamp", "key"])
        t_last = f(last, ["time", "t", "timestamp", "key"])
        c_prev = f(prev, ["close", "c", "closeValue"])
        out["bar_fields"] = {"t_prev": t_prev, "t_last": t_last, "c_prev": c_prev}
        drawn = None
        if t_prev and t_last and c_prev:
            price = float(c_prev)
            drawn = ch.draw_trend_line([int(t_prev) - 20 * 300, price], [int(t_last), price], label="probe5e")
            time.sleep(1.5)
        else:
            # fallback: synthetic coords on the right edge of the visible window (DTO test only)
            now = int(time.time())
            now -= now % 300
            drawn = ch.draw_trend_line([now - 10 * 300, 3300.0], [now, 3300.0], label="probe5e-syn")
            time.sleep(1.5)
            out["draw_fallback"] = True
        out["draw_attempt"] = drawn
        out["shapes_after_draw"] = ch.list_drawings()

        # ---- DTO capture with probe present ----
        try:
            dto = ch.eval(dto_fix_js())
            out["dto_after_draw"] = (dto or {}).get("summary")
            (KB / "dto_with_probe.json").write_text(json.dumps((dto or {}).get("full") or {}, indent=1))
        except Exception as e:  # noqa: BLE001
            out["dto_after_draw"] = {"error": str(e)}

        # ---- restore cycle ----
        try:
            before = out.get("shapes_before") or {}
            probe_ids = [s["id"] for s in (out.get("shapes_after_draw") or {}).get("shapes", [])
                         if s["id"] not in [b["id"] for b in (before.get("shapes") or [])]]
            out["probe_shape_ids"] = probe_ids
            snap = ch.eval("(function(){ return JSON.stringify(%s._chartWidget.lineToolsSynchronizer().getDTO()); })()" % CHART_API)
            out["dto_bytes"] = len(snap or "")
            for pid in probe_ids:
                ch.remove_drawing(pid)
            time.sleep(1)
            mid = ch.eval("(function(){ return %s.getAllShapes().map(function(s){return {id:s.id,name:s.name};}); })()" % CHART_API)
            ch.eval("(function(){ var sync = %s._chartWidget.lineToolsSynchronizer(); sync.applyDTO(JSON.parse(%s)); })()" % (CHART_API, json.dumps(snap)))
            time.sleep(2)
            after = ch.eval("(function(){ return %s.getAllShapes().map(function(s){return {id:s.id,name:s.name};}); })()" % CHART_API)
            out["restore_test"] = {"before": before.get("shapes"), "after_remove": mid, "after_restore": after,
                                   "probe_restored": all(any(a["id"] == pid for a in after) for pid in probe_ids) if probe_ids else None}
        except Exception as e:  # noqa: BLE001
            out["restore_test"] = {"error": str(e)}

    (KB / "round5e.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str)[:9500])
    print("\n[saved] %s/round5e.json" % KB)


if __name__ == "__main__":
    main()
