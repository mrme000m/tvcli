"""Confluence runner — one command for the full represent+confirm loop.

`tvvisual confluence xau-scalp --tf 5` does, in one shot:

  1. COMPUTE    — runs tvcli (headless) for the skill, parses the agent-ready
                  JSON into chart params (rsi, composite, sl, tp, bias).
  2. REPRESENT  — runs the packaged recipe of the same name: opens the chart,
                  relogins, adds the study, draws SL/TP/bias, screenshots.
  3. CONFIRM    — compares tvcli's numbers against the chart's read-back
                  (study data-window values + drawn shapes) and emits a single
                  confluence report with per-metric drift.

Methodology note (scouted 2026-08-20, round 5): tvcli computes on the last
CLOSED bar. The chart data window shows the LIVE forming bar, but the study
row buffer (`source.data()._items[n-2]`) exposes the exact last-closed-bar
plot values — so when the recipe includes a `closed_bar_values` step the
oscillator metrics are EXACT (tol 0.1, tiny margin for a bar rolling over
between the tvcli run and the chart read). Data-window values remain as a
fallback and are classified approximate. Drawn levels (SL/TP) always read
back exactly. The report always includes the measured drift so a vision
model or human can judge.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from . import scout as _scout

_pkg_root = Path(__file__).resolve().parent.parent.parent  # repo root (vendored) or workspace parent (legacy sibling)
# Vendored inside the tvcli repo (go/visual/tvvisual): binary sits at <repo>/tvcli.
# Legacy sibling workspace (tradingview/visual): binary sits at <workspace>/go/tvcli.
_vendored_tvcli = _pkg_root / "tvcli"
TVCLI_DEFAULT = str(_vendored_tvcli if _vendored_tvcli.exists() else _pkg_root / "go" / "tvcli")

# tvcli xau-scalp structure{} keys -> recipe params
# Generic level vocabulary: any parser can emit these structure{} keys and
# confluence will draw them as a labeled horizontal line. This is ONE shared
# map — no per-skill code — so adding a level to a parser lights it up here.
# (structure key, label, color)
_LEVEL_SPEC = [
    ("nearestLiquidityAbove", "Liq Above", "#ef5350"),
    ("nearestLiquidityBelow", "Liq Below", "#26a69a"),
    ("nearestSweepPrice", "Sweep", "#ff9800"),
    ("poc", "POC", "#ef5350"),
    ("vah", "VAH", "#26a69a"),
    ("val", "VAL", "#42a5f5"),
    ("slLevel", "SL", "#ef5350"),
    ("tpLevel", "TP", "#26a69a"),
    ("stopLoss", "SL", "#ef5350"),
    ("tp1", "TP1", "#26a69a"),
    ("tp2", "TP2", "#26a69a"),
    ("tp3", "TP3", "#26a69a"),
    ("entry", "Entry", "#42a5f5"),
    ("resistance", "Resistance", "#ef5350"),
    ("support", "Support", "#26a69a"),
]

# how far a level may sit from last price before it's treated as a stale
# artifact (e.g. old sweep clusters) and skipped from the visual
_LEVEL_MAX_DIST_RATIO = 0.05


def _json_from_tvcli_output(stdout):
    """tvcli prints a human banner before the JSON on some flags; parse the
    trailing JSON object from the first '{' onward."""
    i = stdout.find("{")
    if i < 0:
        raise ValueError(f"no JSON in tvcli output: {stdout[:200]!r}")
    return json.loads(stdout[i:])


def skill_inputs(skill, tvcli=TVCLI_DEFAULT):
    """Discover a skill's input definitions from tvcli: [{'Name','TVInputID',
    'Type','Default'}, ...]. Single source of truth — no per-skill hardcoding."""
    p = subprocess.run([tvcli, "agent", "--list-inputs", "--skills", skill, "--json"],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        return []
    try:
        return (json.loads(p.stdout).get(skill) or [])
    except Exception:
        return []


def skill_meta(skill, tvcli=TVCLI_DEFAULT):
    """Discover the script identity (pineId, version, name) from tvcli schema."""
    p = subprocess.run([tvcli, skill, "--schema", "--json", "--allow-private"],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        return {}
    try:
        d = _json_from_tvcli_output(p.stdout)
    except Exception:
        return {}
    return {"pine_id": d.get("pineId"),
            "pine_version": d.get("version") or "1.0",
            "script_name": d.get("name")}


def _str_val(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _coerce(v, typ):
    """Coerce a CLI override (string) to the input's declared type."""
    if typ == "int":
        return int(v)
    if typ == "float":
        return float(v)
    if typ == "bool":
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)
    return v


def _resolve_inputs(skill, tvcli, overrides):
    """Defaults (from tvcli) merged with runtime overrides ->
    (resolved map {in_N: value}, def list [{name, tvInputID, type, default, value}]).
    """
    defs = skill_inputs(skill, tvcli)
    resolved, out = {}, []
    for d in defs:
        tv_id, name, typ = d.get("TVInputID"), d.get("Name"), d.get("Type")
        default = d.get("Default")
        val = default
        if tv_id in overrides:
            val = overrides[tv_id]
        elif name in overrides:
            val = overrides[name]
        try:
            val = _coerce(val, typ)
        except (TypeError, ValueError):
            val = default
        resolved[tv_id] = val
        out.append({"name": name, "tvInputID": tv_id, "type": typ,
                    "default": default, "value": val})
    return resolved, out


def _extract_levels(structure, last_price):
    """Pull drawable, near-price levels out of any skill's structure{} using the
    shared _LEVEL_SPEC vocabulary. Returns [{price, name, color}, ...]."""
    levels, seen = [], set()
    last_price = float(last_price) if isinstance(last_price, (int, float)) else None
    for key, label, color in _LEVEL_SPEC:
        v = structure.get(key)
        if not isinstance(v, (int, float)):
            continue
        v = float(v)
        if last_price and abs(v - last_price) / last_price > _LEVEL_MAX_DIST_RATIO:
            continue
        rv = round(v, 2)
        if rv in seen:
            continue
        seen.add(rv)
        levels.append({"price": rv, "name": label, "color": color})
    return levels


def compute_with_tvcli(skill, symbol, timeframe, tvcli=TVCLI_DEFAULT, extra=(),
                       input_overrides=None):
    """Run the tvcli skill headlessly; return (params, raw_report).

    Inputs, script identity, and levels are all DISCOVERED from tvcli — nothing
    is hand-crafted per skill. Runtime `input_overrides` ({name or in_N: value})
    pin non-default values and flow to both tvcli and the chart application.
    """
    input_overrides = input_overrides or {}
    resolved, input_defs = _resolve_inputs(skill, tvcli, input_overrides)
    meta = skill_meta(skill, tvcli)

    cmd = [tvcli, skill, "--symbol", symbol, "--tf", str(timeframe),
           "--json", "--agent", "--allow-private"]
    for k, v in resolved.items():
        cmd += ["--input", f"{k}={_str_val(v)}"]
    cmd += list(extra)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"tvcli failed ({p.returncode}): {p.stderr[-300:]}")
    raw = json.loads(p.stdout)
    structure = raw.get("structure") or {}
    market = raw.get("market") or {}

    last_price = market.get("lastPrice")
    levels = _extract_levels(structure, last_price)
    if market.get("bias"):
        levels.append({"kind": "label", "label": f"Bias: {market['bias']}",
                       "price": float(last_price) if isinstance(last_price, (int, float)) else None})

    params = {"symbol": symbol, "timeframe": str(timeframe),
              "bias": market.get("bias"), "lastPrice": last_price,
              "script_pine_id": meta.get("pine_id"),
              "script_version": meta.get("pine_version", "1.0"),
              "script_name": meta.get("script_name"),
              "inputs": resolved, "input_defs": input_defs,
              "levels": levels,
              "bars": 90,
              "shot": f"../{skill}-latest.png",
              "checks": [{"kind": "level", "name": lv["name"],
                          "value": lv["price"], "tol": 0.5}
                         for lv in levels if lv.get("kind") != "label"]}

    # Oscillator/text metrics for the confluence report (generic: rsi & composite
    # when present, so xau-scalp confluence keeps working without per-skill code).
    for src, dst in (("rsi", "rsi"), ("composite", "composite")):
        if isinstance(structure.get(src), (int, float)):
            params[dst] = round(structure[src], 2)
    return params, raw


def _flatten_study_values(studies):
    """name -> float for every parseable data-window value (handles unicode −)."""
    out = []
    for st in studies or []:
        for k, v in (st.get("values") or {}).items():
            try:
                out.append((k, float(str(v).replace(",", "").replace("−", "-"))))
            except (TypeError, ValueError):
                pass
    return out


def _flatten_closed_bar_values(studies):
    """name -> float for every parseable LAST-CLOSED-BAR plot value.

    These come from the study row buffer (source.data()._items[n-2]), i.e.
    the exact bar tvcli computed on — no live-bar drift.
    """
    out = []
    for st in studies or []:
        for k, v in (st.get("values") or {}).items():
            try:
                out.append((k, float(v)))
            except (TypeError, ValueError):
                pass
    return out


def confluence_report(recipe_result, params):
    """Build the confirm-stage report from a finished recipe run.

    Prefers closed-bar study values (exact — same bar tvcli computed on)
    over data-window values (live bar, drifts with market speed) for the
    oscillator metrics; falls back to the data window when the recipe has
    no `closed_bar_values` step.
    """
    steps = recipe_result.get("steps", [])
    study_step = next((s for s in steps if s["action"] == "study_values"), None)
    closed_step = next((s for s in steps if s["action"] == "closed_bar_values"), None)
    validate_step = next((s for s in steps if s["action"] == "validate"), None)
    shot_step = next((s for s in steps if s["action"] == "screenshot"), None)

    on_chart = _flatten_study_values(
        (study_step or {}).get("result", {}).get("studies"))
    on_chart_closed = _flatten_closed_bar_values(
        (closed_step or {}).get("result", {}).get("studies"))

    metrics = []
    for key, tol, cls in (("rsi", 1.5, "approximate"),
                          ("composite", 5.0, "approximate")):
        expect = params.get(key)
        if expect is None:
            continue
        source = "closed_bar"
        pool = on_chart_closed
        if not pool:
            source = "data_window"
            pool = on_chart
        best = None
        for name, val in pool:
            if best is None or abs(val - expect) < abs(best[1] - expect):
                best = (name, val)
        if source == "closed_bar" and best is not None:
            cls, tol = "exact-closedbar", 0.1
        metrics.append({
            "metric": key, "class": cls, "source": source,
            "tvcli": expect,
            "chart": best[1] if best else None,
            "drift": round(best[1] - expect, 3) if best else None,
            "within_tol": bool(best) and abs(best[1] - expect) <= tol,
        })
    for c in (validate_step or {}).get("result", {}).get("checks", []):
        if c.get("kind") == "level":
            metrics.append({
                "metric": c.get("name"), "class": "exact",
                "expected": c.get("expect"), "chart": c.get("actual"),
                "drift": round(c["actual"] - c["expect"], 4)
                         if isinstance(c.get("actual"), (int, float)) else None,
                "within_tol": c.get("ok"),
            })
    return {
        "confluence": all(m.get("within_tol") for m in metrics) if metrics else None,
        "metrics": metrics,
        "screenshot": (shot_step or {}).get("result", {}).get("file_path"),
        "recipe_success": recipe_result.get("success"),
    }


def _closed_bar_time(recipe_result):
    """Chart's last-closed-bar time (epoch s) from the closed_bar_values step."""
    for s in recipe_result.get("steps", []):
        if s.get("action") != "closed_bar_values":
            continue
        for st in (s.get("result") or {}).get("studies", []) or []:
            bt = st.get("bar_time")
            if isinstance(bt, (int, float)) and bt > 0:
                return int(bt)
    return None


def run_confluence(chart, skill="xau-scalp", symbol="OANDA:XAUUSD", timeframe="5",
                   tvcli=TVCLI_DEFAULT, kb=None, max_bar_retries=1,
                   input_overrides=None):
    """Full loop: tvcli compute -> recipe represent -> confluence report.

    Everything is discovered from tvcli (inputs, script identity, levels); the
    one generic recipe `indicator` applies the script via createStudy with the
    resolved inputs and draws the computed levels. Runtime `input_overrides`
    ({name or in_N: value}) pin non-default values on both tvcli and the chart.
    """
    t0 = time.time()
    params, raw = compute_with_tvcli(skill, symbol, timeframe, tvcli,
                                     input_overrides=input_overrides)
    recipe = _scout.load_recipe("indicator", kb=kb)
    result = _scout.run_recipe(chart, recipe, params=params, kb=kb)
    report = confluence_report(result, params)

    bar_time = _closed_bar_time(result)
    tvcli_bar = (raw.get("market") or {}).get("lastBarTime")
    rechecks = []
    if bar_time and tvcli_bar and int(tvcli_bar) != bar_time:
        for attempt in range(max_bar_retries):
            params, raw = compute_with_tvcli(skill, symbol, timeframe, tvcli,
                                             input_overrides=input_overrides)
            tvcli_bar = (raw.get("market") or {}).get("lastBarTime")
            aligned = bool(tvcli_bar) and int(tvcli_bar) == bar_time
            rechecks.append({"attempt": attempt + 1, "tvcli_bar": tvcli_bar,
                             "chart_bar": bar_time, "aligned": aligned})
            report = confluence_report(result, params)
            if aligned:
                break

    report.update({
        "skill": skill, "symbol": symbol, "timeframe": str(timeframe),
        "params": params, "duration_s": round(time.time() - t0, 1),
        "bar_alignment": {"chart_closed_bar": bar_time,
                          "tvcli_closed_bar": int(tvcli_bar) if tvcli_bar else None,
                          "rechecks": rechecks},
        "tvcli": {"bias": (raw.get("market") or {}).get("bias"),
                  "lastPrice": (raw.get("market") or {}).get("lastPrice"),
                  "lastBarTime": tvcli_bar,
                  "opportunities": (raw.get("opportunities") or [])[:2]},
    })
    _scout._log(_scout.kb_root(kb),
                {"kind": "confluence", "skill": skill,
                 "confluence": report["confluence"],
                 "bar": [bar_time, int(tvcli_bar) if tvcli_bar else None, len(rechecks)],
                 "metrics": [(m["metric"], m.get("drift"), m.get("within_tol"))
                             for m in report["metrics"]]})
    return report
