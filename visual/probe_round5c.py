#!/usr/bin/env python3
"""Round-5c probe: call .data()/.plots()/.series(); sync.getDTO/applyDTO; real restore test.

Run from visual/:  python3 probe_round5c.py
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


def data_call_js():
    """Call s.data() on main series and s.data()/.plots()/.series() on the xau study; dump shapes."""
    return (
        "(function(){ var chart = %s; var sources = chart.model().model().dataSources(); "
        "var res = {}; "
        "function dump(v, depth) { if (v === null) return 'null'; var t = typeof v; if (t !== 'object') return t; "
        "if (Array.isArray(v)) { return { array_len: v.length, sample: depth < 2 ? [dump(v[v.length-1], depth+1), dump(v[v.length-2], depth+1)] : null }; } "
        "var r = { object_keys: Object.keys(v).slice(0, 40) }; if (depth < 2) { for (var i = 0; i < Math.min(Object.keys(v).length, 12); i++) { var k = Object.keys(v)[i]; try { r['k_' + k] = dump(v[k], depth+1); } catch(e) { r['k_' + k] = 'err'; } } } return r; } "
        "for (var si = 0; si < sources.length; si++) { var s = sources[si]; "
        "if (si === 1) { try { res.mains_data = dump(s.data()); } catch(e) { res.mains_data_err = e.message.slice(0,100); } } "
        "var nm = null; try { nm = s.metaInfo ? s.metaInfo().description : null; } catch(e) {} "
        "if (nm === 'XAUUSD Scalping Confluence Engine') { "
        "try { res.xau_data = dump(s.data()); } catch(e) { res.xau_data_err = e.message.slice(0,100); } "
        "try { res.xau_plots = dump(s.plots()); } catch(e) { res.xau_plots_err = e.message.slice(0,100); } "
        "try { res.xau_series = dump(s.series()); } catch(e) { res.xau_series_err = e.message.slice(0,100); } "
        "} } return res; })()" % CHART_WIDGET
    )


def sync_dto_js():
    """synchronizer getDTO structure + applyDTO/getDTO source."""
    return (
        "(function(){ var api = %s; var w = api._chartWidget; var sync = w.lineToolsSynchronizer(); var res = {}; "
        "try { res.getDTO_src = sync.getDTO.toString().slice(0, 500); } catch(e) { res.getDTO_src_err = e.message; } "
        "try { res.applyDTO_src = sync.applyDTO.toString().slice(0, 800); } catch(e) { res.applyDTO_src_err = e.message; } "
        "try { var dto = sync.getDTO(); res.dto_keys = Object.keys(dto || {}); "
        "res.sources_len = dto && dto.sources ? dto.sources.length : null; "
        "if (dto && dto.sources && dto.sources.length) { var s0 = dto.sources[0]; res.source0 = { keys: Object.keys(s0), id: s0.id, type: s0.type, ownerSource: s0.ownerSource || null, points_len: s0.points ? s0.points.length : null }; "
        "for (var i = 0; i < dto.sources.length; i++) { res['src' + i] = { id: dto.sources[i].id, type: dto.sources[i].type, owner: dto.sources[i].ownerSource || null }; } } "
        "res.groups = dto && dto.groups ? (Array.isArray(dto.groups) ? dto.groups.map(function(g){return {id:g.id, name:g.name, sourcesLen:(g.sources||[]).length};}) : typeof dto.groups) : null; "
        "res.clientId = dto ? (dto.clientId || null) : null; } catch(e) { res.dto_err = e.message.slice(0,150); } "
        "return res; })()" % CHART_API
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

        out["data_calls"] = ch.eval(data_call_js())
        out["sync_dto"] = ch.eval(sync_dto_js())
        out["shapes_before"] = ch.list_drawings()

        # ---- draw a probe shape; get time/price from mains data() if possible ----
        dc = out.get("data_calls") or {}
        md = dc.get("mains_data") or {}
        sample = md.get("sample") or [] if isinstance(md, dict) else []
        last_bar, prev_bar = (sample[0] if sample else None), (sample[1] if sample else None)

        def bar_field(bar, names):
            if not isinstance(bar, dict):
                return None
            for n in names:
                if n in bar:
                    return bar[n]
            return None

        t_last = bar_field(last_bar, ["time", "t", "timestamp", "key"])
        c_last = bar_field(last_bar, ["close", "c", "closeValue"])
        t_prev = bar_field(prev_bar, ["time", "t", "timestamp", "key"])
        c_prev = bar_field(prev_bar, ["close", "c", "closeValue"])
        out["bar_fields"] = {"t_last": t_last, "c_last": c_last, "t_prev": t_prev, "c_prev": c_prev}
        drawn = None
        if t_prev and c_prev and t_last:
            price = float(c_prev)
            drawn = ch.draw_trend_line([int(t_prev) - 20 * 300, price], [int(t_last), price], label="probe5c")
            time.sleep(1)
        out["draw_attempt"] = drawn
        out["shapes_after_draw"] = ch.list_drawings()
        out["sync_dto_after_draw"] = ch.eval(sync_dto_js())

        # ---- restore test: getDTO -> remove probe shapes -> applyDTO -> verify ----
        try:
            snap = ch.eval("(function(){ var dto = %s._chartWidget.lineToolsSynchronizer().getDTO(); return JSON.stringify(dto); })()" % CHART_API)
            out["dto_bytes"] = len(snap or "")
            before = (out.get("shapes_after_draw") or {}).get("shapes")
            before_ids = [s["id"] for s in (out.get("shapes_before") or {}).get("shapes", [])]
            probe_ids = [s["id"] for s in (before or []) if s["id"] not in before_ids]
            out["probe_shape_ids"] = probe_ids
            for pid in probe_ids:
                ch.remove_drawing(pid)
            time.sleep(1)
            mid = ch.eval("(function(){ return %s.getAllShapes().map(function(s){return {id:s.id,name:s.name};}); })()" % CHART_API)
            ch.eval("(function(){ var sync = %s._chartWidget.lineToolsSynchronizer(); sync.applyDTO(JSON.parse(%s)); })()" % (CHART_API, json.dumps(snap)))
            time.sleep(2)
            after = ch.eval("(function(){ return %s.getAllShapes().map(function(s){return {id:s.id,name:s.name};}); })()" % CHART_API)
            out["restore_test"] = {"before": before, "after_remove": mid, "after_restore": after,
                                   "probe_restored": all(any(a["id"] == pid for a in after) for pid in probe_ids) if probe_ids else None}
        except Exception as e:  # noqa: BLE001
            out["restore_test"] = {"error": str(e)}

    (KB / "round5c.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str)[:9000])
    print("\n[saved] %s/round5c.json" % KB)


if __name__ == "__main__":
    main()
