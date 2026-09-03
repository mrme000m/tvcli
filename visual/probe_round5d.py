#!/usr/bin/env python3
"""Round-5d probe: m_bars bar buffer; study _items per-plot series reads; real DTO restore cycle.

Run from visual/:  python3 probe_round5d.py
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


def bars_dump_js():
    """Dump mains.data().m_bars last two bars fully; xau _items series prototype + value reads."""
    return (
        "(function(){ var chart = %s; var sources = chart.model().model().dataSources(); "
        "var res = {}; "
        "try { var md = sources[1].data(); var mb = md.m_bars; "
        "res.m_bars_type = Array.isArray(mb) ? ('array len ' + mb.length) : typeof mb; "
        "if (Array.isArray(mb) && mb.length > 2) { "
        "res.last = JSON.parse(JSON.stringify(mb[mb.length-1], function(k, v){ return typeof v === 'function' ? '[fn]' : v; })); "
        "res.prev = JSON.parse(JSON.stringify(mb[mb.length-2], function(k, v){ return typeof v === 'function' ? '[fn]' : v; })); "
        "} } catch(e) { res.m_bars_err = e.message.slice(0, 150); } "
        "for (var si = 0; si < sources.length; si++) { var s = sources[si]; var nm = null; "
        "try { nm = s.metaInfo ? s.metaInfo().description : null; } catch(e) {} "
        "if (nm !== 'XAUUSD Scalping Confluence Engine') continue; "
        "try { var items = s.data()._items; res.xau_items_len = Array.isArray(items) ? items.length : typeof items; "
        "if (Array.isArray(items) && items.length) { var s0 = items[0]; "
        "res.xau_item0_type = Array.isArray(s0) ? ('array len ' + s0.length) : typeof s0; "
        "var proto = s0, methods = []; "
        "while (proto && proto !== Object.prototype) { var ns = Object.getOwnPropertyNames(proto); for (var i = 0; i < ns.length; i++) { if (typeof s0[ns[i]] === 'function' && methods.indexOf(ns[i]) === -1) methods.push(ns[i]); } proto = Object.getPrototypeOf(proto); } "
        "res.xau_item0_methods = methods.slice(0, 80); "
        "res.xau_item0_keys = Object.keys(s0).slice(0, 30); "
        "try { res.size0 = s0.size ? s0.size() : null; } catch(e) { res.size0 = 'err ' + e.message.slice(0,60); } "
        "try { res.val_last = s0.value ? s0.value(res.size0 - 1) : null; } catch(e) { res.val_last = 'err ' + e.message.slice(0,60); } "
        "try { res.val_prev = s0.value ? s0.value(res.size0 - 2) : null; } catch(e) { res.val_prev = 'err ' + e.message.slice(0,60); } "
        "try { res.firstValue0 = s0.firstValue ? s0.firstValue() : null; } catch(e) { res.firstValue0 = 'err'; } "
        "} } catch(e) { res.xau_items_err = e.message.slice(0, 150); } "
        "break; } return res; })()" % CHART_WIDGET
    )


def plot_names_js():
    """Map study plot indices to names via metaInfo().plots and dataWindowView titles."""
    return (
        "(function(){ var chart = %s; var sources = chart.model().model().dataSources(); "
        "for (var si = 0; si < sources.length; si++) { var s = sources[si]; var nm = null; "
        "try { nm = s.metaInfo ? s.metaInfo().description : null; } catch(e) {} "
        "if (nm !== 'XAUUSD Scalping Confluence Engine') continue; "
        "var r = {}; try { var mi = s.metaInfo(); r.meta_plots = (mi.plots || []).map(function(p){ return { id: p.id, name: p.name, type: p.type }; }); } catch(e) { r.meta_err = e.message.slice(0,80); } "
        "try { var dwv = s.dataWindowView(); r.dwv_titles = dwv.items().map(function(i){ return i._title; }); } catch(e) { r.dwv_err = e.message.slice(0,80); } "
        "return r; } return null; })()" % CHART_WIDGET
    )


def sync_dto_full_js():
    """Full DTO JSON (user line tools) + per-source summary."""
    return (
        "(function(){ var api = %s; var dto = api._chartWidget.lineToolsSynchronizer().getDTO(); "
        "var clean = JSON.parse(JSON.stringify(dto, function(k, v){ return typeof v === 'function' ? undefined : v; })); "
        "var summary = { bytes: JSON.stringify(clean).length, sources: (clean.sources || []).map(function(s){ return { id: s.id, type: s.type, ownerSource: s.ownerSource || null, points: s.points ? s.points.length : null }; }), "
        "groups: clean.groups && typeof clean.groups === 'object' ? Object.keys(clean.groups).length : null, clientId: clean.clientId || null }; "
        "return { summary: summary, full: clean }; })()" % CHART_API
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

        out["bars_dump"] = ch.eval(bars_dump_js())
        out["plot_names"] = ch.eval(plot_names_js())
        out["shapes_before"] = ch.list_drawings()

        # ---- draw probe shape from m_bars ----
        bd = out.get("bars_dump") or {}
        prev = bd.get("prev") or {}
        last = bd.get("last") or {}

        def f(bar, names):
            for n in names:
                if isinstance(bar, dict) and bar.get(n) is not None:
                    return bar[n]
            return None

        t_prev = f(prev, ["time", "t", "timestamp"])
        t_last = f(last, ["time", "t", "timestamp"])
        c_prev = f(prev, ["close", "c"])
        out["bar_fields"] = {"t_prev": t_prev, "t_last": t_last, "c_prev": c_prev}
        drawn = None
        if t_prev and t_last and c_prev:
            price = float(c_prev)
            drawn = ch.draw_trend_line([int(t_prev) - 20 * 300, price], [int(t_last), price], label="probe5d")
            time.sleep(1.5)
        out["draw_attempt"] = drawn
        out["shapes_after_draw"] = ch.list_drawings()

        # ---- DTO capture with probe shape present ----
        try:
            dto = ch.eval(sync_dto_full_js())
            out["dto_after_draw"] = (dto or {}).get("summary")
            full = (dto or {}).get("full") or {}
            (KB / "dto_with_probe.json").write_text(json.dumps(full, indent=1))
        except Exception as e:  # noqa: BLE001
            out["dto_after_draw"] = {"error": str(e)}

        # ---- explicit restore cycle: getDTO -> remove probe -> applyDTO -> verify ----
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

    (KB / "round5d.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str)[:9000])
    print("\n[saved] %s/round5d.json" % KB)


if __name__ == "__main__":
    main()
