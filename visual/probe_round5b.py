#!/usr/bin/env python3
"""Round-5b probe: drawing-state DTO + restore; main-series/study .data structure (closed-bar route).

Run from visual/:  python3 probe_round5b.py
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

COLL = "window.TradingViewApi._chartWidgetCollection"


def series_data_js():
    """Probe main series + xau-scalp study source .data/.plots/.series structures."""
    return (
        "(function(){ var chart = %s; var sources = chart.model().model().dataSources(); "
        "var res = {}; "
        "function probeSource(s) { var r = {}; "
        "try { var d = s.data; r.data_type = Array.isArray(d) ? ('array len ' + d.length) : (d === null ? 'null' : typeof d); "
        "if (d && typeof d === 'object' && !Array.isArray(d)) r.data_keys = Object.keys(d).slice(0, 30); "
        "if (Array.isArray(d) && d.length) { var last = d[d.length-1], prev = d[d.length-2]; "
        "r.last = { keys: Object.keys(last).slice(0,25), time: (last.time!==undefined?last.time:last.t), close: (last.close!==undefined?last.close:last.c), id: last.id }; "
        "r.prev = { time: (prev.time!==undefined?prev.time:prev.t), close: (prev.close!==undefined?prev.close:prev.c) }; } "
        "} catch(e) { r.data_err = e.message.slice(0,80); } "
        "try { var p = s.plots; r.plots_type = Array.isArray(p) ? ('array len ' + p.length) : (p === null ? 'null' : typeof p); "
        "if (p && typeof p === 'object' && !Array.isArray(p)) r.plots_keys = Object.keys(p).slice(0, 30); "
        "if (Array.isArray(p) && p.length) { r.plot0 = { keys: Object.keys(p[0]).slice(0, 30) }; "
        "var pd = p[0].data !== undefined ? p[0].data : p[0].values; r.plot0_data = Array.isArray(pd) ? ('array len ' + pd.length) : (pd === null ? 'null' : typeof pd); } "
        "} catch(e) { r.plots_err = e.message.slice(0,80); } "
        "try { var sr = s.series; r.series_type = Array.isArray(sr) ? ('array len ' + sr.length) : (sr === null ? 'null' : typeof sr); "
        "if (sr && typeof sr === 'object' && !Array.isArray(sr)) r.series_keys = Object.keys(sr).slice(0, 30); "
        "} catch(e) { r.series_err = e.message.slice(0,80); } "
        "return r; } "
        "for (var si = 0; si < sources.length; si++) { var s = sources[si]; var nm = null; "
        "try { nm = (s.metaInfo && s.metaInfo()) ? (s.metaInfo().description || 'mainseries') : null; } catch(e) { nm = 'noname_' + si; } "
        "if (!nm && si === 1) nm = 'mainseries_guess'; "
        "if (nm === 'XAUUSD Scalping Confluence Engine') res.xau = probeSource(s); "
        "else if (nm === 'mainseries_guess' || si === 1) res.mains = probeSource(s); "
        "} return res; })()" % CHART_WIDGET
    )


def dto_probe_js():
    """lineToolsAndGroupsDTO structure + synchronizer methods + getLineToolsState with shapes present."""
    return (
        "(function(){ var api = %s; var w = api._chartWidget; var res = {}; "
        "try { var dto = w.lineToolsAndGroupsDTO(); res.dto_keys = Object.keys(dto); "
        "res.sources_len = dto.sources ? dto.sources.length : null; "
        "if (dto.sources && dto.sources[0]) { res.source0 = { keys: Object.keys(dto.sources[0]), id: dto.sources[0].id, type: dto.sources[0].type, ownerSource: dto.sources[0].ownerSource || null }; } "
        "res.groups = dto.groups ? (Array.isArray(dto.groups) ? dto.groups.length : typeof dto.groups) : null; "
        "res.clientId = dto.clientId || null; } catch(e) { res.dto_err = e.message.slice(0,120); } "
        "try { var st = api.getLineToolsState(); res.gls_sources = st.sources ? st.sources.length : null; "
        "if (st.sources && st.sources[0]) res.gls_source0_keys = Object.keys(st.sources[0]); } catch(e) { res.gls_err = e.message.slice(0,120); } "
        "try { var sync = w.lineToolsSynchronizer(); if (!sync) { res.sync = 'null'; return res; } "
        "var proto = sync, methods = []; "
        "while (proto && proto !== Object.prototype) { var ns = Object.getOwnPropertyNames(proto); for (var i = 0; i < ns.length; i++) { if (typeof sync[ns[i]] === 'function' && methods.indexOf(ns[i]) === -1) methods.push(ns[i]); } proto = Object.getPrototypeOf(proto); } "
        "res.sync_methods = methods.slice(0, 120); } catch(e) { res.sync_err = e.message.slice(0,120); } "
        "try { res.apply_sig = w.applyLineToolUpdateNotification.toString().slice(0, 400); } catch(e) {} "
        "try { res.applySaved_sig = w._applySavedModelState.toString().slice(0, 400); } catch(e) {} "
        "return res; })()" % CHART_API
    )


def coll_probe_js():
    """Top-level _chartWidgetCollection: confirm loadLayoutState + signature."""
    return (
        "(function(){ var c = %s; var res = {}; "
        "try { res.exists = !!c; if (!c) return res; res.has_loadLayoutState = typeof c.loadLayoutState; "
        "if (typeof c.loadLayoutState === 'function') res.loadLayoutState_src = c.loadLayoutState.toString().slice(0, 600); "
        "} catch(e) { res.err = e.message.slice(0,120); } return res; })()" % COLL
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

        out["collection_probe"] = ch.eval(coll_probe_js())
        out["series_data"] = ch.eval(series_data_js())

        # ---- drawing DTO with a fresh shape on the chart ----
        out["shapes_before"] = ch.list_drawings()
        # draw using last closed bar info if we got it, else a relative guess from quote
        sd = out.get("series_data") or {}
        prev = ((sd.get("mains") or {}).get("prev")) or {}
        last = ((sd.get("mains") or {}).get("last")) or {}
        drawn = None
        if prev.get("time") and last.get("time"):
            t0 = prev["time"] - 20 * 300
            price = prev.get("close") or last.get("close")
            drawn = ch.draw_trend_line([t0, price], [last["time"], price], label="probe5b")
            time.sleep(1)
        out["draw_attempt"] = drawn
        out["shapes_after_draw"] = ch.list_drawings()
        out["dto_probe"] = ch.eval(dto_probe_js())

        # ---- restore test on the drawn shape ----
        try:
            snap = ch.eval("(function(){ return JSON.stringify(window.TradingViewApi._saveChartService.saveToJSON()); })()")
            out["snapshot_bytes"] = len(snap or "")
            before = ch.eval("(function(){ return %s.getAllShapes().map(function(s){return {id:s.id,name:s.name};}); })()" % CHART_API)
            # remove ONLY the probe shape we drew
            probe_ids = [s["id"] for s in (out.get("shapes_after_draw") or {}).get("shapes", []) if s["id"] not in [b["id"] for b in (out.get("shapes_before") or {}).get("shapes", [])]]
            out["probe_shape_ids"] = probe_ids
            for pid in probe_ids:
                ch.remove_drawing(pid)
            time.sleep(1)
            mid = ch.eval("(function(){ return %s.getAllShapes().map(function(s){return {id:s.id,name:s.name};}); })()" % CHART_API)
            ch.eval("(function(){ %s.loadLayoutState(JSON.parse(%s)); })()" % (COLL, json.dumps(snap)))
            time.sleep(2)
            after = ch.eval("(function(){ return %s.getAllShapes().map(function(s){return {id:s.id,name:s.name};}); })()" % CHART_API)
            out["restore_test"] = {"before": before, "after_remove": mid, "after_restore": after,
                                   "probe_restored": all(any(a["id"] == pid for a in after) for pid in probe_ids) if probe_ids else None}
        except Exception as e:  # noqa: BLE001
            out["restore_test"] = {"error": str(e)}

    (KB / "round5b.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str)[:8000])
    print("\n[saved] %s/round5b.json" % KB)


if __name__ == "__main__":
    main()
