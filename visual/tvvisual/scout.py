"""Scout — progressive instrumentation of the TradingView web interface.

This module is how an agent *learns* the live TradingView UI and turns what it
learned into reliable, repeatable, configurable code:

  1. scout_surface()  — dump the live programmatic surface (window.TradingViewApi,
     the active chart widget's methods, collections, replay API) into the KB.
  2. scout_dom()      — map the interactive DOM (data-name / aria-label / buttons,
     grouped by layout region) so selectors are discovered, not guessed.
  3. scout_probe()    — run an arbitrary JS probe and record the result, so a
     hypothesis about the UI becomes a dated, repeatable observation.
  4. recipes          — named step-lists (open chart, push Pine, compile, draw,
     screenshot, validate) executed by run_recipe(). Every run records telemetry
     (success/fail, last-verified) so the agent can tell trusted recipes from
     rotting ones and repair them.
  5. codegen_recipe() — render a verified recipe as a standalone Python script,
     i.e. the agent generates its own reusable code from what it proved works.

Knowledge base layout (default ~/.tvvisual/scout/, override with kb_root):
  surface.json   — latest API surface dump (+ history kept in probes.jsonl)
  dom.json       — latest DOM interaction map
  recipes.json   — the agent-maintained recipe store (curated + discovered)
  probes.jsonl   — append-only log of every probe/recipe run

Packaged, version-controlled recipes live in <repo>/visual/recipes/*.json and
take precedence by name; the KB store is where the agent promotes its own.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

_js = json.dumps

KB_DEFAULT = Path.home() / ".tvvisual" / "scout"
RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes"

# ---------------------------------------------------------------------------
# JS probes
# ---------------------------------------------------------------------------

_SURFACE_JS = r"""
(function() {
  function methodNames(obj, depth) {
    var names = {};
    var seen = [];
    var cur = obj;
    for (var d = 0; d < depth && cur; d++) {
      var props = Object.getOwnPropertyNames(cur);
      for (var i = 0; i < props.length; i++) {
        var p = props[i];
        if (seen.indexOf(p) !== -1) continue;
        seen.push(p);
        try {
          var v = cur[p];
          names[p] = typeof v;
        } catch (e) { names[p] = 'throw'; }
      }
      cur = Object.getPrototypeOf(cur);
    }
    return names;
  }
  var api = window.TradingViewApi || {};
  var out = { api_keys: [], chart_methods: {}, collections: {}, has_replay: false };
  try { out.api_keys = Object.keys(api); } catch (e) {}
  try {
    var chart = api._activeChartWidgetWV.value();
    out.chart_methods = methodNames(chart, 4);
    var widget = chart._chartWidget;
    if (widget) out.widget_methods = methodNames(widget, 3);
  } catch (e) { out.chart_error = String(e); }
  try {
    var cwc = api._chartWidgetCollection;
    if (cwc) out.collections._chartWidgetCollection = Object.keys(cwc);
  } catch (e) {}
  try { out.has_replay = !!api._replayApi; } catch (e) {}
  try {
    var st = chartState();
    function chartState() {
      var chart = api._activeChartWidgetWV.value();
      return { symbol: chart.symbol(), resolution: chart.resolution(),
               chartType: chart.chartType() };
    }
    out.state = st;
  } catch (e) {}
  return out;
})()
"""

_DOM_JS = r"""
(function() {
  var regions = {
    top:    document.querySelector('[class*="layout__area--top"]'),
    left:   document.querySelector('[class*="layout__area--left"]'),
    right:  document.querySelector('[class*="layout__area--right"]'),
    bottom: document.querySelector('[class*="layout__area--bottom"]'),
    center: document.querySelector('[class*="layout__area--center"]') || document.body
  };
  function mapRegion(root, cap) {
    if (!root) return [];
    var out = [];
    var els = root.querySelectorAll('[data-name],[aria-label],button,[role="button"]');
    for (var i = 0; i < els.length && out.length < cap; i++) {
      var el = els[i];
      if (!el.offsetParent && el.offsetWidth === 0) continue;  // hidden
      out.push({
        tag: el.tagName.toLowerCase(),
        data_name: el.getAttribute('data-name') || null,
        aria: el.getAttribute('aria-label') || null,
        text: (el.textContent || '').trim().slice(0, 60) || null
      });
    }
    return out;
  }
  var out = {};
  for (var k in regions) out[k] = mapRegion(regions[k], 200);
  var dlg = document.querySelector('[data-name="dialog"],[class*="dialog"]');
  out.open_dialog = dlg ? mapRegion(dlg.parentElement || dlg, 200) : null;
  return out;
})()
"""


# ---------------------------------------------------------------------------
# knowledge base
# ---------------------------------------------------------------------------

def kb_root(root=None):
    p = Path(root) if root else KB_DEFAULT
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def _save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _log(kb, event):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **event}
    with open(Path(kb) / "probes.jsonl", "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------------------
# scouting
# ---------------------------------------------------------------------------

def scout_surface(chart, kb=None):
    """Dump the live TradingViewApi surface; persist to surface.json."""
    kb = kb_root(kb)
    data = chart.eval(_SURFACE_JS)
    _save_json(kb / "surface.json", {"ts": time.time(), "surface": data})
    _log(kb, {"kind": "surface", "api_keys": (data or {}).get("api_keys", [])})
    return {"success": data is not None, "surface": data,
            "kb": str(kb / "surface.json")}


def scout_dom(chart, kb=None):
    """Map the interactive DOM by layout region; persist to dom.json."""
    kb = kb_root(kb)
    data = chart.eval(_DOM_JS)
    counts = {k: len(v) for k, v in (data or {}).items() if isinstance(v, list)}
    _save_json(kb / "dom.json", {"ts": time.time(), "dom": data})
    _log(kb, {"kind": "dom", "counts": counts})
    return {"success": data is not None, "counts": counts, "dom": data,
            "kb": str(kb / "dom.json")}


def scout_probe(chart, name, js, kb=None, await_promise=False):
    """Run a one-off JS probe and append the observation to probes.jsonl."""
    kb = kb_root(kb)
    try:
        result = chart.eval(js, await_promise=await_promise)
        _log(kb, {"kind": "probe", "name": name, "ok": True, "result": result})
        return {"success": True, "name": name, "result": result}
    except Exception as e:
        _log(kb, {"kind": "probe", "name": name, "ok": False, "error": str(e)})
        return {"success": False, "name": name, "error": str(e)}


# ---------------------------------------------------------------------------
# recipes
# ---------------------------------------------------------------------------

def _recipe_paths(name, kb=None):
    """Candidate locations for a recipe, packaged first then the KB store."""
    yield RECIPES_DIR / f"{name}.json"
    yield kb_root(kb) / "recipes" / f"{name}.json"


def load_recipe(name, kb=None):
    for p in _recipe_paths(name, kb):
        if p.exists():
            r = json.loads(p.read_text())
            r.setdefault("name", name)
            r["_path"] = str(p)
            return r
    store = _load_json(kb_root(kb) / "recipes.json", {})
    if name in store:
        r = store[name]
        r.setdefault("name", name)
        return r
    raise FileNotFoundError(f"recipe not found: {name!r} "
                            f"(looked in {RECIPES_DIR} and {kb_root(kb)})")


def list_recipes(kb=None):
    """Packaged recipes + KB-store recipes, with health metadata."""
    out = {}
    if RECIPES_DIR.exists():
        for p in sorted(RECIPES_DIR.glob("*.json")):
            try:
                r = json.loads(p.read_text())
            except Exception as e:
                r = {"error": str(e)}
            out[p.stem] = {"origin": "packaged", "path": str(p),
                           "description": r.get("description"),
                           "steps": len(r.get("steps", [])),
                           "meta": r.get("meta", {}), "error": r.get("error")}
    store_path = kb_root(kb) / "recipes.json"
    for name, r in _load_json(store_path, {}).items():
        out.setdefault(name, {"origin": "kb", "path": str(store_path),
                              "description": r.get("description"),
                              "steps": len(r.get("steps", [])),
                              "meta": r.get("meta", {})})
    return {"success": True, "count": len(out), "recipes": out}


def save_recipe(recipe, kb=None):
    """Promote a recipe into the agent-maintained KB store."""
    kb = kb_root(kb)
    store = _load_json(kb / "recipes.json", {})
    recipe = dict(recipe)
    recipe.pop("_path", None)
    recipe.setdefault("meta", {})["saved"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    store[recipe["name"]] = recipe
    _save_json(kb / "recipes.json", store)
    return {"success": True, "name": recipe["name"], "store": str(kb / "recipes.json")}


_MISSING = object()  # sentinel: a referenced param was not provided


def _resolve(value, params):
    """Resolve '$params.key' / '$env.KEY' references inside step args.
    Whole-string references resolve to the raw value (numbers stay numeric);
    embedded references are interpolated into the string. A referenced but
    absent/None $params key resolves to the _MISSING sentinel so callers can
    skip optional steps instead of executing with None."""
    import re

    if isinstance(value, str):
        whole = re.fullmatch(r"\$params\.([A-Za-z_][A-Za-z0-9_]*)", value)
        if whole:
            v = params.get(whole.group(1), _MISSING)
            return _MISSING if v is None else v
        if value.startswith("$env."):
            import os
            return os.environ.get(value[len("$env."):])
        # embedded interpolation: "Bias: $params.bias"
        def _sub(m):
            v = params.get(m.group(1), _MISSING)
            if v is _MISSING or v is None:
                return ""
            return str(v)
        return re.sub(r"\$params\.([A-Za-z_][A-Za-z0-9_]*)", _sub, value)
    if isinstance(value, list):
        return [_resolve(v, params) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, params) for k, v in value.items()}
    return value


def _has_missing(value):
    if value is _MISSING:
        return True
    if isinstance(value, dict):
        return any(_has_missing(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_missing(v) for v in value)
    return False


def _step(chart, action, args):
    """Execute one recipe step against a Chart; returns the step's result."""
    if action == "open":
        chart.open(args.get("symbol"), args.get("timeframe"))
        return chart.state()
    if action == "timeframe":
        return chart.set_timeframe(args["timeframe"])
    if action == "symbol":
        return chart.set_symbol(args["symbol"])
    if action == "hide_chrome":
        return chart.hide_chrome()
    if action == "show_chrome":
        return chart.show_chrome()
    if action == "zoom_bars":
        return chart.zoom_bars(args.get("bars", 150))
    if action == "add_indicator":
        return chart.add_indicator(args["name"], args.get("inputs"), args.get("script"))
    if action == "add_indicator_search":
        # verified path for user scripts ("My scripts" section of the dialog)
        return chart.add_indicator_from_search(
            args["name"], args.get("match"), args.get("section"))
    if action == "search_indicators":
        return chart.search_indicators(args["query"], args.get("limit", 25))
    if action == "remove_indicator":
        return chart.remove_indicator(args["entity_id"])
    if action == "draw":
        return chart.draw(args)
    if action == "draw_levels":
        return chart.draw_levels(args.get("levels"))
    if action == "fit_price":
        return chart.fit_price()
    if action == "clear_drawings":
        return chart.clear_drawings()
    if action == "pine_new":
        return chart.pine_new(args.get("type", "indicator"))
    if action == "pine_set":
        src = args.get("source")
        if args.get("file"):
            src = Path(args["file"]).read_text()
        return chart.pine_set_source(src)
    if action == "pine_compile":
        return chart.pine_compile()
    if action == "pine_smart_compile":
        return chart.pine_smart_compile()
    if action == "pine_errors":
        return chart.pine_get_errors()
    if action == "pine_save":
        return chart.pine_save()
    if action == "screenshot":
        return chart.screenshot(args.get("path"), args.get("region", "chart"))
    if action == "validate":
        return chart.validate(args.get("checks", []))
    if action == "state":
        return chart.state()
    if action == "studies":
        return chart.studies()
    if action == "study_values":
        return chart.study_values()
    if action == "tables":
        return chart.tables(args.get("filter", ""))
    if action == "lines":
        return chart.lines(args.get("filter", ""))
    if action == "eval":
        return chart.eval(args["expression"],
                          await_promise=args.get("await_promise", False))
    if action == "sleep":
        time.sleep(args.get("seconds", 1))
        return {"slept": args.get("seconds", 1)}
    if action == "account":
        return {"account": chart.account()}
    if action == "relogin":
        return chart.relogin()
    if action == "layout_export":
        return chart.layout_export(args.get("path"))
    if action == "layout_list":
        return chart.layout_list()
    if action == "layout_switch":
        return chart.layout_switch(args["name"])
    if action == "closed_bar_values":
        return chart.closed_bar_values(args.get("study"))
    if action == "drawings_snapshot":
        return chart.drawings_snapshot(args.get("path"))
    if action == "drawings_restore":
        return chart.drawings_restore(args["path"])
    if action == "chart_restore":
        return chart.chart_restore(args["path"])
    raise ValueError(f"unknown recipe action: {action!r}")


def run_recipe(chart, recipe, params=None, kb=None, record=True):
    """Execute a recipe step-list; record telemetry into the KB.

    Returns {success, name, steps: [{action, ok, result|error}], meta}.
    `params` override the recipe's own `params` defaults; step args may
    reference them as "$params.key".
    """
    kb = kb_root(kb)
    name = recipe.get("name", "unnamed")
    merged = dict(recipe.get("params", {}))
    merged.update(params or {})
    base = Path(recipe.get("_path", kb)).parent

    steps_out, ok_all = [], True
    for i, s in enumerate(recipe.get("steps", [])):
        action = s.get("action")
        args = _resolve(s.get("args", {}), merged)

        # skip steps referencing a $params key the caller did not provide
        # (e.g. optional SL/TP draws when no levels were computed)
        if _has_missing(args):
            steps_out.append({"i": i, "action": action, "ok": True,
                              "skipped": "missing $params value"})
            continue

        # recipe-relative file paths (pine source, screenshot)
        for key in ("file", "path"):
            if isinstance(args.get(key), str) and not args[key].startswith("/"):
                args[key] = str((base / args[key]).resolve())
        entry = {"i": i, "action": action}
        try:
            res = _step(chart, action, args)
            entry["ok"] = not (isinstance(res, dict) and res.get("success") is False)
            entry["result"] = res
        except Exception as e:
            entry["ok"] = False
            entry["error"] = str(e)
        ok_all = ok_all and entry["ok"]
        steps_out.append(entry)
        if entry["ok"] is False and s.get("required", True):
            break

    meta = {"last_run": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": ok_all}
    if record:
        _log(kb, {"kind": "recipe", "name": name, "ok": ok_all,
                  "steps": [{"action": s["action"], "ok": s["ok"]} for s in steps_out]})
        _update_recipe_health(kb, name, ok_all)
    return {"success": ok_all, "name": name, "params": merged,
            "steps": steps_out, "meta": meta}


def _update_recipe_health(kb, name, ok):
    store_path = kb / "recipes.json"
    store = _load_json(store_path, {})
    r = store.get(name)
    if r is None:
        return
    m = r.setdefault("meta", {})
    m["runs"] = m.get("runs", 0) + 1
    m["failures"] = m.get("failures", 0) + (0 if ok else 1)
    m["last_verified" if ok else "last_failed"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _save_json(store_path, store)


def verify_recipes(chart, kb=None, only=None):
    """Re-run KB-store recipes and refresh their health metadata."""
    kb = kb_root(kb)
    store = _load_json(kb / "recipes.json", {})
    names = [only] if only else sorted(store)
    results = {}
    for name in names:
        if name not in store:
            results[name] = {"success": False, "error": "not in KB store"}
            continue
        results[name] = run_recipe(chart, store[name], kb=kb)
    ok = all(r.get("success") for r in results.values())
    return {"success": ok, "verified": results}


# ---------------------------------------------------------------------------
# codegen — turn a verified recipe into standalone reusable Python
# ---------------------------------------------------------------------------

_TEMPLATE = '''"""Generated from recipe {name!r} — {desc}

Generated {ts} by tvvisual scout codegen. Re-verify with:
  tvvisual recipe run {name}
"""
import json
from tvvisual.session import Chart

PARAMS = {params!r}


def run(chart: Chart, params=None):
    p = dict(PARAMS)
    p.update(params or {{}})
    results = []
{body}
    return results


if __name__ == "__main__":
    chart = Chart()
    try:
        for r in run(chart):
            print(json.dumps(r, default=str))
    finally:
        chart.close()
'''


def codegen_recipe(name, kb=None, out_dir=None):
    """Render a recipe as a standalone Python script using Chart methods.

    This is the 'generate reliable repeatable code for itself' step: only
    recipes that verify cleanly should be promoted to code.
    """
    recipe = load_recipe(name, kb=kb)
    kb = kb_root(kb)
    out_dir = Path(out_dir) if out_dir else kb / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)

    base = Path(recipe.get("_path", kb)).parent
    lines = []
    for s in recipe.get("steps", []):
        action = s.get("action")
        args = dict(s.get("args", {}))
        # absolutize recipe-relative file paths so the generated script runs anywhere
        for key in ("file", "path"):
            if isinstance(args.get(key), str) and not args[key].startswith(("/", "$")):
                args[key] = str((base / args[key]).resolve())
        lines.append(f"    results.append({{'action': {action!r}, 'result': "
                     f"_step(chart, {action!r}, {args!r}, p)}})")
    helper = (
        "def _step(chart, action, args, p):\n"
        "    from tvvisual.scout import _resolve, _step as _s\n"
        "    return _s(chart, action, _resolve(args, p))\n"
    )
    body = "\n".join(lines) if lines else "    pass"
    code = _TEMPLATE.format(
        name=recipe.get("name", name),
        desc=(recipe.get("description") or "").strip(),
        ts=time.strftime("%Y-%m-%dT%H:%M:%S"),
        params=recipe.get("params", {}),
        body=body,
    ) + "\n" + helper
    path = out_dir / f"{recipe.get('name', name)}.py"
    path.write_text(code)
    _log(kb, {"kind": "codegen", "name": name, "path": str(path)})
    return {"success": True, "name": name, "file": str(path)}
