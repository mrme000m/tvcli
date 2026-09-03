#!/usr/bin/env python3
"""Round-5g probe: reconstruct line-tool DTO from saveToJSON source -> applyDTO restore;
plus full loadLayoutState wrapper test (id/uid/name + chartWidgetCollectionState envelope).

Run from visual/:  python3 probe_round5g.py
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


def bar_at_js(offset_from_last):
    """Real bar {time,close} from main series m_bars.valueAt(lastIndex - offset)."""
    return (
        "(function(){ var mb = %s.model().mainSeries().data().m_bars; "
        "var b = mb.valueAt(mb.lastIndex() - %d); "
        "return JSON.parse(JSON.stringify(b, function(k,v){ return typeof v === 'function' ? undefined : v; })); })()" % (CHART_WIDGET, int(offset_from_last))
    )


def find_source_js(probe_id):
    """Locate the probe line tool's full source object inside saveToJSON content."""
    return (
        "(function(){ var exp = window.TradingViewApi._saveChartService.saveToJSON(); "
        "var pid = %s; var found = null; "
        "function walk(o) { if (!o || typeof o !== 'object') return; "
        "if (o.id === pid && o.type && o.type.indexOf('LineTool') === 0) { found = o; return; } "
        "for (var k in o) { if (o.hasOwnProperty(k)) { var v = o[k]; "
        "if (typeof v === 'string' && v.length > 10 && v.charAt(0) === '{') { try { walk(JSON.parse(v)); } catch(e) {} } "
        "else if (v && typeof v === 'object') walk(v); } } } "
        "walk(exp); "
        "if (!found) return { error: 'not found in export', top_keys: Object.keys(exp) }; "
        "return { source: JSON.parse(JSON.stringify(found)) }; })()" % json.dumps(probe_id)
    )


def load_content_sig_js():
    return (
        "(function(){ var c = window.TradingViewApi._chartWidgetCollection; var res = {}; "
        "try { res.loadContent_src = c.loadContent.toString().slice(0, 900); } catch(e) { res.loadContent_src = 'err ' + e.message; } "
        "return res; })()"
    )


def layout_state_js():
    """Wrap saveToJSON into the loadLayoutState envelope and apply it."""
    return (
        "(function(){ var c = window.TradingViewApi._chartWidgetCollection; var exp = window.TradingViewApi._saveChartService.saveToJSON(); "
        "var env = { id: exp.id || null, uid: exp.uid || null, name: exp.name || '', description: exp.description || '', "
        "username: exp.username || '', lastModified: exp.lastModified || null, isPrivate: exp.isPrivate !== false, "
        "chartWidgetCollectionState: exp }; "
        "return new Promise(function(resolve) { c.loadLayoutState(env).then(function(){ resolve({ ok: true }); }, function(e) { resolve({ error: String(e) }); }); }); })()"
    )


def verify_js():
    return (
        "(function(){ var api = %s; var chart = %s; var res = {}; "
        "res.shapes = api.getAllShapes().map(function(s){ return { id: s.id, name: s.name }; }); "
        "try { var st = chart.model(); res.symbol = st.mainSeries().properties().childs().symbol.value(); } catch(e) { res.symbol = 'err'; } "
        "try { res.resolution = st.mainSeries().properties().childs().interval.value(); } catch(e) {} "
        "res.studies = (api.getAllStudies() || []).map(function(s){ return s.id; }); "
        "return res; })()" % (CHART_API, CHART_WIDGET)
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
        out["verify_before"] = ch.eval(verify_js())

        # ---- draw probe trend line at real closed-bar coordinates ----
        bar_prev = ch.eval(bar_at_js(2)) or {}   # last CLOSED bar
        bar_last = ch.eval(bar_at_js(1)) or {}   # live bar

        def f(b, names):
            for n in names:
                if isinstance(b, dict) and b.get(n) is not None:
                    return b[n]
            return None

        t_prev, c_prev, t_last = f(bar_prev, ["time", "t"]), f(bar_prev, ["close", "c"]), f(bar_last, ["time", "t"])
        out["bar_fields"] = {"t_prev": t_prev, "c_prev": c_prev, "t_last": t_last,
                             "bar_prev_keys": sorted(bar_prev.keys()) if isinstance(bar_prev, dict) else None}
        probe_id = None
        if t_prev and c_prev and t_last:
            price = float(c_prev)
            ch.draw_trend_line([int(t_prev) - 20 * 300, price], [int(t_last), price], label="probe5g")
            time.sleep(1.5)
            after = ch.list_drawings()
            probe_id = next((s["id"] for s in after["shapes"] if s["name"] == "trend_line" and s["id"] not in [x["id"] for x in out["verify_before"]["shapes"]]), None)
        out["probe_id"] = probe_id

        # ---- capture probe source from saveToJSON ----
        probe_src = None
        if probe_id:
            cap = ch.eval(find_source_js(probe_id))
            if cap and cap.get("source"):
                probe_src = cap["source"]
                (KB / "probe_trendline_source.json").write_text(json.dumps(probe_src, indent=1))
                out["probe_source_keys"] = sorted(probe_src.keys())
            else:
                out["probe_source_error"] = cap
        # remove ALL probe-created shapes (trend_line) to test restore of one
        if probe_id:
            ch.remove_drawing(probe_id)
            time.sleep(1)
            out["verify_after_remove"] = ch.eval(verify_js())

        # ---- applyDTO restore ----
        if probe_src:
            js = (
                "(function(){ var api = %s; var sync = api._chartWidget.lineToolsSynchronizer(); "
                "var base = sync.getDTO(); var src = %s; "
                "var dto = { sources: {}, groups: {}, clientId: base.clientId, lineToolsToValidate: [src.id], groupsToValidate: [] }; "
                "dto.sources[src.id] = src; "
                "return new Promise(function(resolve) { sync.applyDTO(dto).then(function(){ resolve({ ok: true }); }, function(e) { resolve({ error: String(e) }); }); }); })()"
                % (CHART_API, json.dumps(probe_src))
            )
            out["apply_dto_result"] = ch.eval(js, await_promise=True)
            time.sleep(2)
            out["verify_after_applydto"] = ch.eval(verify_js())
        out["load_content_sig"] = ch.eval(load_content_sig_js())

        # ---- full loadLayoutState envelope test ----
        out["load_layout_state_result"] = ch.eval(layout_state_js(), await_promise=True)
        time.sleep(3)
        out["verify_after_loadlayout"] = ch.eval(verify_js())

    (KB / "round5g.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str)[:9500])
    print("\n[saved] %s/round5g.json" % KB)


if __name__ == "__main__":
    main()
