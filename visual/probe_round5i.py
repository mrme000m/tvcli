#!/usr/bin/env python3
"""Round-5i probe (final verification):
1) applyDTO restore with JS-native Map DTO (no JSON round-trip).
2) loadLayoutState with parsed content envelope (full chart replace, verified).

Run from visual/:  python3 probe_round5i.py
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

VERIFY_JS = (
    "(function(){ var api = %s; var chart = %s; var res = {}; "
    "res.shapes = api.getAllShapes().map(function(s){ return { id: s.id, name: s.name }; }); "
    "try { res.symbol = chart.model().mainSeries().properties().childs().symbol.value(); } catch(e) { res.symbol = 'err ' + e.message; } "
    "try { res.resolution = chart.model().mainSeries().properties().childs().interval.value(); } catch(e) {} "
    "res.studies = (api.getAllStudies() || []).map(function(s){ return s.id; }); "
    "return res; })()" % (CHART_API, CHART_WIDGET)
)


def study_items_js():
    return (
        "(function(){ var chart = %s; var sources = chart.model().model().dataSources(); "
        "for (var si = 0; si < sources.length; si++) { var s = sources[si]; var nm = null; "
        "try { nm = s.metaInfo ? s.metaInfo().description : null; } catch(e) {} "
        "if (nm !== 'XAUUSD Scalping Confluence Engine') continue; "
        "var items = s.data()._items; var n = items.length; "
        "return { n: n, closed: { time: items[n-2].value[0], sl: items[n-2].value[11], rsi: items[n-2].value[4] }, "
        "live: { time: items[n-1].value[0], sl: items[n-1].value[11], rsi: items[n-1].value[4] } }; } "
        "return null; })()" % CHART_WIDGET
    )


def find_source_js(probe_id):
    return (
        "(function(){ var exp = window.TradingViewApi._saveChartService.saveToJSON(); "
        "var pid = %s; var found = null; "
        "function walk(o) { if (!o || typeof o !== 'object') return; "
        "if (o.id === pid && o.type && o.type.indexOf('LineTool') === 0) { found = o; return; } "
        "for (var k in o) { if (o.hasOwnProperty(k)) { var v = o[k]; "
        "if (typeof v === 'string' && v.length > 10 && v.charAt(0) === '{') { try { walk(JSON.parse(v)); } catch(e) {} } "
        "else if (v && typeof v === 'object') walk(v); } } } "
        "walk(exp); "
        "if (!found) return { error: 'not found in export' }; "
        "return { source: JSON.parse(JSON.stringify(found)) }; })()" % json.dumps(probe_id)
    )


def apply_dto_map_js(probe_src):
    """Build Map DTO in-page from the captured (injected) source object, applyDTO."""
    return (
        "(function(){ var api = %s; var sync = api._chartWidget.lineToolsSynchronizer(); "
        "var base = sync.getDTO(); "
        "var src = %s; "
        "var dto = { sources: new Map([[src.id, src]]), groups: new Map(), clientId: base.clientId, "
        "lineToolsToValidate: [src.id], groupsToValidate: [] }; "
        "return new Promise(function(resolve) { sync.applyDTO(dto).then(function(){ resolve({ ok: true, type: src.type }); }, "
        "function(e) { resolve({ error: String(e) }); }); }); })()" % (CHART_API, json.dumps(probe_src))
    )


def load_layout_state_js():
    """Full envelope with parsed content."""
    return (
        "(function(){ var c = window.TradingViewApi._chartWidgetCollection; var exp = window.TradingViewApi._saveChartService.saveToJSON(); "
        "var content = typeof exp.content === 'string' ? JSON.parse(exp.content) : exp.content; "
        "var env = { id: exp.id || null, uid: exp.uid || null, name: exp.name || '', description: exp.description || '', "
        "username: exp.username || '', lastModified: exp.lastModified || null, isPrivate: exp.isPrivate !== false, "
        "chartWidgetCollectionState: content }; "
        "return new Promise(function(resolve) { c.loadLayoutState(env).then(function(){ resolve({ ok: true }); }, function(e) { resolve({ error: String(e) }); }); }); })()"
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
        v0 = ch.eval(VERIFY_JS)
        out["verify_before"] = v0

        # ---- draw probe shape ----
        si = ch.eval(study_items_js())
        out["study_items"] = si
        probe_id = None
        if si:
            closed, live = si["closed"], si["live"]
            ch.draw_trend_line([int(closed["time"]) - 10 * 300, float(closed["sl"])], [int(live["time"]), float(closed["sl"])], label="probe5i")
            time.sleep(1.5)
            after = ch.list_drawings()
            probe_id = next((s["id"] for s in after["shapes"] if s["name"] == "trend_line" and s["id"] not in [x["id"] for x in (v0.get("shapes") or [])]), None)
        out["probe_id"] = probe_id

        # ---- capture source (while shape exists) -> remove -> applyDTO(Map) restore ----
        if probe_id:
            cap = ch.eval(find_source_js(probe_id))
            probe_src = (cap or {}).get("source")
            if probe_src:
                (KB / "probe_trendline_source.json").write_text(json.dumps(probe_src, indent=1))
                out["probe_source_keys"] = sorted(probe_src.keys())
            else:
                out["probe_source_error"] = cap
            ch.remove_drawing(probe_id)
            time.sleep(1)
            out["verify_after_remove"] = ch.eval(VERIFY_JS)
            if probe_src:
                js = apply_dto_map_js(probe_src)
                out["apply_dto_result"] = ch.eval(js, await_promise=True)
                time.sleep(2)
                out["verify_after_applydto"] = ch.eval(VERIFY_JS)

        # ---- full loadLayoutState (corrected) ----
        out["load_layout_state_result"] = ch.eval(load_layout_state_js(), await_promise=True)
        time.sleep(3)
        out["verify_after_loadlayout"] = ch.eval(VERIFY_JS)

    (KB / "round5i.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str)[:9500])
    print("\n[saved] %s/round5i.json" % KB)


if __name__ == "__main__":
    main()
