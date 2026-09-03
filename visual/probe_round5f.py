#!/usr/bin/env python3
"""Round-5f probe: getDTO arg semantics — _prepareDTOItem source, flag variants, prepareDTO, getLineToolsState.

Run from visual/:  python3 probe_round5f.py
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


def dto_src_js():
    return (
        "(function(){ var api = %s; var sync = api._chartWidget.lineToolsSynchronizer(); var res = {}; "
        "try { res.prepareDTO_src = sync.prepareDTO.toString().slice(0, 500); } catch(e) { res.prepareDTO_src = 'err ' + e.message; } "
        "try { res._prepareDTOItem_src = sync._prepareDTOItem.toString().slice(0, 1500); } catch(e) { res._prepareDTOItem_src = 'err ' + e.message; } "
        "return res; })()" % CHART_API
    )


def dto_variants_js():
    """Try getDTO with flag variants + prepareDTO + getLineToolsState; report source counts + samples."""
    return (
        "(function(){ var api = %s; var sync = api._chartWidget.lineToolsSynchronizer(); "
        "function summary(dto) { if (!dto) return null; var srcs = dto.sources; var arr = Array.isArray(srcs) ? srcs : (srcs ? Object.values(srcs) : []); "
        "return { count: arr.length, ids: arr.map(function(s){ return s.id + ':' + s.type + (s.ownerSource ? ':' + s.ownerSource : ''); }), "
        "toValidate: (dto.lineToolsToValidate || []).length, clientId: dto.clientId || null }; } "
        "var res = {}; "
        "function call(label, fn) { try { res[label] = summary(fn()); } catch(e) { res[label] = 'err ' + e.message.slice(0, 100); } } "
        "call('g_0_0_0', function(){ return sync.getDTO(0, false, false); }); "
        "call('g_0_1_0', function(){ return sync.getDTO(0, true, false); }); "
        "call('prepareDTO', function(){ return sync.prepareDTO(); }); "
        "call('gls', function(){ return api.getLineToolsState(); }); "
        "call('g_0_0_1_INVAL', function(){ return sync.getDTO(0, false, true); }); "
        "call('g_0_1_1_INVAL', function(){ return sync.getDTO(0, true, true); }); "
        "return res; })()" % CHART_API
    )


def full_dto_js():
    """Dump the non-empty variant fully (first one that has sources)."""
    return (
        "(function(){ var api = %s; var sync = api._chartWidget.lineToolsSynchronizer(); "
        "function clean(o) { try { return JSON.parse(JSON.stringify(o, function(k,v){ return typeof v === 'function' ? undefined : v; })); } catch(e) { return null; } } "
        "var cands = [function(){ return sync.getDTO(0, true, false); }, function(){ return sync.prepareDTO(); }, function(){ return api.getLineToolsState(); }, function(){ return sync.getDTO(0, true, true); }]; "
        "for (var i = 0; i < cands.length; i++) { var dto = null; try { dto = cands[i](); } catch(e) { continue; } "
        "if (dto && dto.sources) { var n = Array.isArray(dto.sources) ? dto.sources.length : Object.keys(dto.sources).length; "
        "if (n > 0) return { used: i, clean: clean(dto) }; } } return { used: -1, clean: null }; })()" % CHART_API
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
        out["shapes_before"] = ch.list_drawings()
        now = int(time.time()) - (int(time.time()) % 300)
        ch.draw_trend_line([now - 10 * 300, 3300.0], [now, 3300.0], label="probe5f")
        time.sleep(1.5)
        out["shapes_after_draw"] = ch.list_drawings()

        out["dto_src"] = ch.eval(dto_src_js())
        out["dto_variants"] = ch.eval(dto_variants_js())
        full = ch.eval(full_dto_js())
        out["full_dto_used"] = (full or {}).get("used")
        clean = (full or {}).get("clean")
        if clean:
            (KB / "dto_nonempty.json").write_text(json.dumps(clean, indent=1))
            srcs = clean.get("sources") or {}
            arr = srcs if isinstance(srcs, list) else list(srcs.values())
            out["full_dto_summary"] = {
                "top_keys": sorted(clean.keys()),
                "sources": [{ "id": s.get("id"), "type": s.get("type"), "owner": s.get("ownerSource"),
                              "keys": sorted(s.keys())[:15],
                              "points": (s.get("points") or [{}])[0] if s.get("points") else None } for s in arr][:5],
            }

    (KB / "round5f.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str)[:9000])
    print("\n[saved] %s/round5f.json" % KB)


if __name__ == "__main__":
    main()
