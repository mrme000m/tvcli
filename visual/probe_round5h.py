#!/usr/bin/env python3
"""Round-5h probe: (1) verify account layout survived the loadLayoutState wipe; recover from
saved export if needed; (2) real applyDTO restore of a drawn shape; (3) corrected loadLayoutState
envelope (chartWidgetCollectionState = exp.content).

Run from visual/:  python3 probe_round5h.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from tvvisual.session import Chart, CHART_API, CHART_WIDGET  # noqa: E402

KB = Path.home() / ".tvvisual" / "scout" / "probes"
KB.mkdir(parents=True, exist_ok=True)
LAYOUTS = Path.home() / ".tvvisual" / "scout" / "layouts"
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
    """Last two per-bar rows of the xau study (time at value[0], SL at value[11])."""
    return (
        "(function(){ var chart = %s; var sources = chart.model().model().dataSources(); "
        "for (var si = 0; si < sources.length; si++) { var s = sources[si]; var nm = null; "
        "try { nm = s.metaInfo ? s.metaInfo().description : null; } catch(e) {} "
        "if (nm !== 'XAUUSD Scalping Confluence Engine') continue; "
        "var items = s.data()._items; var n = items.length; "
        "return { n: n, closed: { time: items[n-2].value[0], sl: items[n-2].value[11], tp: items[n-2].value[12], rsi: items[n-2].value[4], composite: items[n-2].value[1] }, "
        "live: { time: items[n-1].value[0], sl: items[n-1].value[11], tp: items[n-1].value[12], rsi: items[n-1].value[4], composite: items[n-1].value[1] } }; } "
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


def apply_dto_js(probe_src):
    return (
        "(function(){ var api = %s; var sync = api._chartWidget.lineToolsSynchronizer(); "
        "var base = sync.getDTO(); var src = %s; "
        "var dto = { sources: {}, groups: {}, clientId: base.clientId, lineToolsToValidate: [src.id], groupsToValidate: [] }; "
        "dto.sources[src.id] = src; "
        "return new Promise(function(resolve) { sync.applyDTO(dto).then(function(){ resolve({ ok: true }); }, function(e) { resolve({ error: String(e) }); }); }); })()"
        % (CHART_API, json.dumps(probe_src))
    )


def load_layout_state_js():
    """CORRECTED envelope: chartWidgetCollectionState = exp.content."""
    return (
        "(function(){ var c = window.TradingViewApi._chartWidgetCollection; var exp = window.TradingViewApi._saveChartService.saveToJSON(); "
        "var env = { id: exp.id || null, uid: exp.uid || null, name: exp.name || '', description: exp.description || '', "
        "username: exp.username || '', lastModified: exp.lastModified || null, isPrivate: exp.isPrivate !== false, "
        "chartWidgetCollectionState: exp.content }; "
        "return new Promise(function(resolve) { c.loadLayoutState(env).then(function(){ resolve({ ok: true }); }, function(e) { resolve({ error: String(e) }); }); }); })()"
    )


def main():
    with Chart(env_path="../.env") as ch:
        ch.relogin()
        out["account"] = ch.account()
        ch.open("PEPPERSTONE:XAUUSD", "5")
        time.sleep(3)
        out["verify_after_relogin"] = ch.eval(VERIFY_JS)
        v0 = out["verify_after_relogin"] or {}

        # ---- (1) recover if the account layout was wiped ----
        if not (v0.get("studies") or []):
            out["recover_needed"] = True
            layout_file = sorted(LAYOUTS.glob("PEPPERSTONE-XAUUSD_*.json"))[-1]
            out["recover_from"] = str(layout_file)
            exp = json.loads(layout_file.read_text())
            content = exp["content"] if isinstance(exp.get("content"), dict) else json.loads(exp["content"])
            r = ch.eval("(function(){ var c = window.TradingViewApi._chartWidgetCollection; "
                        "return new Promise(function(resolve) { c.loadContent(%s).then(function(){ resolve({ok:true}); }, function(e){ resolve({error:String(e)}); }); }); })()" % json.dumps(content))
            out["recover_loadContent"] = r
            time.sleep(3)
            out["verify_after_recover"] = ch.eval(VERIFY_JS)

        r = ch.add_indicator_from_search("XAU Scalp Engine v2")
        out["add_indicator"] = {"success": r.get("success"), "entity_id": r.get("entity_id")}
        time.sleep(5)

        # ---- (2) draw probe shape from study _items, then applyDTO restore ----
        si = ch.eval(study_items_js())
        out["study_items"] = si
        probe_id = None
        if si:
            closed, live = si["closed"], si["live"]
            price = float(closed["sl"])
            ch.draw_trend_line([int(closed["time"]) - 10 * 300, price], [int(live["time"]), price], label="probe5h")
            time.sleep(1.5)
            after = ch.list_drawings()
            probe_id = next((s["id"] for s in after["shapes"] if s["name"] == "trend_line"), None)
        out["probe_id"] = probe_id
        probe_src = None
        if probe_id:
            cap = ch.eval(find_source_js(probe_id))
            if cap and cap.get("source"):
                probe_src = cap["source"]
                (KB / "probe_trendline_source.json").write_text(json.dumps(probe_src, indent=1))
                out["probe_source_keys"] = sorted(probe_src.keys())
            else:
                out["probe_source_error"] = cap
        if probe_src:
            ch.remove_drawing(probe_id)
            time.sleep(1)
            out["verify_after_remove"] = ch.eval(VERIFY_JS)
            out["apply_dto_result"] = ch.eval(apply_dto_js(probe_src), await_promise=True)
            time.sleep(2)
            out["verify_after_applydto"] = ch.eval(VERIFY_JS)

        # ---- (3) corrected full loadLayoutState test ----
        out["load_layout_state_result"] = ch.eval(load_layout_state_js(), await_promise=True)
        time.sleep(3)
        out["verify_after_loadlayout"] = ch.eval(VERIFY_JS)

    (KB / "round5h.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str)[:9500])
    print("\n[saved] %s/round5h.json" % KB)


if __name__ == "__main__":
    main()
