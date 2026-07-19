#!/usr/bin/env python3
"""
Cross-reference Go skill parsers against JS skill scripts and dumps.

Inputs:
- /Volumes/ExMac/code/tradingview/go/internal/skill/parsers/*.go    (Go parsers)
- /Volumes/ExMac/code/tradingview/go/internal/skill/skill.go         (SkillResult schema)
- /Volumes/ExMac/code/tradingview/go/internal/cmd/skillcmd.go        (skill command runner)
- /Volumes/ExMac/code/tradingview/go/skill-analysis/meta/*.json      (JS script metadata)
- /Volumes/ExMac/code/tradingview/go/skill-analysis/dumps/*/payload.json (actual JS agent output)
- /Volumes/ExMac/code/tradingview/go/skill-analysis/dumps/*/help.txt
- /Volumes/ExMac/code/tradingview/js-experiment06/<skill>/SKILL.md   (skill docs)

Output:
- /Volumes/ExMac/code/tradingview/go/skill-analysis/SKILL_REFERENCE_INDEX.md

The index is a one-stop reference for fixing the Go skill commands: for each
Go skill command, it shows the JS skill it is based on, Pine ID match, input
mismatches, preset mismatches, field-by-field comparison of the JS agent
payload vs the Go SkillResult, and concrete fix recommendations.
"""
from __future__ import annotations

import json
import os
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

GO_ROOT = Path("/Volumes/ExMac/code/tradingview/go")
JS_ROOT = Path("/Volumes/ExMac/code/tradingview/js-experiment06")
PARSER_DIR = GO_ROOT / "internal" / "skill" / "parsers"
META_DIR = GO_ROOT / "skill-analysis" / "meta"
DUMPS_DIR = GO_ROOT / "skill-analysis" / "dumps"
OUT_FILE = GO_ROOT / "skill-analysis" / "SKILL_REFERENCE_INDEX.md"

# Map Go skill name -> JS skill dir name (the ones that differ)
GO_TO_JS_NAME = {
    "anchored-vp": "anchored-clusters-vp",
    "ema-atr": "ema-atr-pro-engine",
    "sr-breaks": "support-resistance-breaks",
    "vgaps": "volume-gaps-imbalances-zeiierman",
    "mtf": "xauusd-mtf-trend",
    "sniper": "precision-sniper",
    "smc": "smart-money-concepts",
    "golden": "golden-rule-strategy",
    "trend": "self-aware-trend-system",
    "ict": "ict-auto-validated-smc",
    "shemar": "shemar-smc-confidence",
    "quantum": "quantum-ribbon",
    "ust": "ultra-sensitive-supertrend",
    "dvi": "delta-volume-intensity",
    "bsv": "buying-selling-volume",
    "swingarm": "swingarm-atr-trend-indicator",
}

# Skills that exist in js-experiment06 but have no Go parser command
JS_ONLY_SKILLS = ["generic-indicator", "tv-cron-orchestrator", "nlm-cli-skill",
                  "tv-indicator", "youtube-to-tv-pine"]


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return f"(read error: {e})"


# ---------- Go parser extraction ----------

def extract_go_skills() -> List[Dict[str, Any]]:
    """Parse Go parser files to extract Skill definitions."""
    out: List[Dict[str, Any]] = []
    for f in sorted(PARSER_DIR.glob("*.go")):
        if f.name in ("helpers.go",):
            continue
        text = read_text(f)
        # Skip registry.go and skill.go (different files)
        # Only parse files that define a *Skill var
        if not re.search(r"&skill\.Skill\{", text):
            continue

        skill: Dict[str, Any] = {"file": str(f.relative_to(GO_ROOT)), "filename": f.name}

        m = re.search(r'Name:\s*"([^"]+)"', text)
        skill["name"] = m.group(1) if m else "?"
        m = re.search(r'Synopsis:\s*"([^"]+)"', text, re.S)
        skill["synopsis"] = (m.group(1).strip() if m else "").replace("\n", " ")
        m = re.search(r'PineID:\s*"([^"]+)"', text)
        skill["pine_id"] = m.group(1) if m else ""

        # Inputs
        inputs: List[Dict[str, Any]] = []
        im = re.search(r"Inputs:\s*\[\]skill\.InputDef\{(.*?)\},\s*\n\s*ParseOutput:", text, re.S)
        if im:
            body = im.group(1)
            for entry in re.finditer(
                r"\{Name:\s*\"([^\"]+)\"\s*,\s*TVInputID:\s*\"([^\"]+)\"\s*,\s*Type:\s*\"([^\"]+)\"\s*,\s*Default:\s*([^,}]+?)[\},]",
                body):
                inputs.append({
                    "name": entry.group(1),
                    "tv_input_id": entry.group(2),
                    "type": entry.group(3),
                    "default": entry.group(4).strip(),
                })
        skill["inputs"] = inputs

        # Presets
        presets: Dict[str, Dict[str, Any]] = {}
        pm = re.search(r"Presets:\s*map\[string\]map\[string\]any\{(.*?)\n\s*\},\s*\n\s*ParseOutput:", text, re.S)
        if pm:
            body = pm.group(1)
            for preset_match in re.finditer(r'"([^"]+)":\s*\{([^}]+)\}', body):
                pname = preset_match.group(1)
                pbody = preset_match.group(2)
                kv: Dict[str, Any] = {}
                for kv_match in re.finditer(r'"([^"]+)":\s*([^,}]+?)[,}]', pbody):
                    kv[kv_match.group(1)] = kv_match.group(2).strip()
                presets[pname] = kv
        skill["presets"] = presets

        # ParseOutput function name
        pm = re.search(r"ParseOutput:\s*(\w+),", text)
        skill["parse_func"] = pm.group(1) if pm else ""

        # FormatText function name
        fm = re.search(r"FormatText:\s*(\w+),", text)
        skill["format_func"] = fm.group(1) if fm else ""

        # Field names read from periods via getField(p, []string{...})
        period_fields = set()
        for fm in re.finditer(r'getField\([^,]+,\s*\[\]string\{([^}]+)\}', text):
            for k in re.findall(r'"([^"]+)"', fm.group(1)):
                period_fields.add(k)
        # Also direct field access like getField(last, ...)
        for fm in re.finditer(r'getField\(\w+,\s*\[\]string\{([^}]+)\}', text):
            for k in re.findall(r'"([^"]+)"', fm.group(1)):
                period_fields.add(k)
        skill["period_fields_read"] = sorted(period_fields)

        # toFloat(getField(...)) - just the same fields
        # Graphic reads
        graphic_fields = set()
        for fm in re.finditer(r'graphic\[\s*"([^"]+)"\s*\]', text):
            graphic_fields.add(fm.group(1))
        skill["graphic_fields_read"] = sorted(graphic_fields)

        # Structure keys produced
        struct_keys = set()
        sm = re.search(r"Structure:\s*map\[string\]any\{(.*?)\},\s*\n", text, re.S)
        if sm:
            for k in re.findall(r'"([^"]+)":', sm.group(1)):
                struct_keys.add(k)
        skill["structure_keys"] = sorted(struct_keys)

        # Workflow string used
        wm = re.search(r'Workflow:\s*"([^"]+)"', text)
        skill["workflow"] = wm.group(1) if wm else ""

        out.append(skill)
    return out


# ---------- JS meta extraction ----------

def load_js_meta(js_name: str) -> Optional[Dict[str, Any]]:
    p = META_DIR / f"{js_name}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None


def load_js_payload(js_name: str) -> Optional[Dict[str, Any]]:
    p = DUMPS_DIR / js_name / "payload.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None


def load_js_help(js_name: str) -> str:
    p = DUMPS_DIR / js_name / "help.txt"
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="ignore")


def find_js_script(js_name: str) -> Optional[Path]:
    """Locate the JS script for a skill (mirror of analyze_skills.py logic)."""
    # 1. root <name>.cjs
    p = JS_ROOT / f"{js_name}.cjs"
    if p.exists():
        return p
    # 2. <name>/scripts/<name>.cjs
    p = JS_ROOT / js_name / "scripts" / f"{js_name}.cjs"
    if p.exists():
        return p
    # 3. <name>/<name>.cjs
    p = JS_ROOT / js_name / f"{js_name}.cjs"
    if p.exists():
        return p
    return None


def extract_js_input_map(text: str) -> List[Dict[str, Any]]:
    """Extract INPUT_MAP array-of-objects from JS source."""
    items: List[Dict[str, Any]] = []
    # Array form: const INPUT_MAP = [ { variable:'x', tvInputId:'in_0', type:'int', default:10 }, ... ];
    m = re.search(r"const\s+INPUT_MAP\s*=\s*\[(.*?)\];", text, re.S)
    if m:
        body = m.group(1)
        for entry in re.finditer(r"\{([^}]+)\}", body):
            block = entry.group(1)
            item: Dict[str, Any] = {}
            for kv in re.finditer(r"(\w+)\s*:\s*('[^']*'|\"[^\"]*\"|[^,}\n]+)", block):
                k = kv.group(1)
                v = kv.group(2).strip().rstrip(",")
                if (v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"')):
                    v = v[1:-1]
                else:
                    try:
                        v = json.loads(v)
                    except Exception:
                        pass
                item[k] = v
            if item:
                items.append(item)
        return items
    # Object form: const INPUT_MAP = { variableName: { tvInputId:'in_0', type:'int', default:10 }, ... };
    m = re.search(r"const\s+INPUT_MAP\s*=\s*\{(.*?)\n\};", text, re.S)
    if m:
        body = m.group(1)
        for entry in re.finditer(r"(\w+)\s*:\s*\{([^}]+)\}", body):
            outer_key = entry.group(1)
            block = entry.group(2)
            item: Dict[str, Any] = {"variable": outer_key}
            for kv in re.finditer(r"(\w+)\s*:\s*('[^']*'|\"[^\"]*\"|[^,}\n]+)", block):
                k = kv.group(1)
                v = kv.group(2).strip().rstrip(",")
                if (v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"')):
                    v = v[1:-1]
                else:
                    try:
                        v = json.loads(v)
                    except Exception:
                        pass
                item[k] = v
            items.append(item)
    return items


def extract_js_presets(text: str) -> Dict[str, Dict[str, Any]]:
    """Extract PRESETS object from JS source."""
    presets: Dict[str, Dict[str, Any]] = {}
    m = re.search(r"const\s+PRESETS\s*=\s*\{(.*?)\n\};", text, re.S)
    if not m:
        return presets
    body = m.group(1)
    for pm in re.finditer(r"(\w+)\s*:\s*\{([^}]+)\}", body):
        pname = pm.group(1)
        block = pm.group(2)
        kv: Dict[str, Any] = {}
        for kvc in re.finditer(r"(\w+)\s*:\s*('[^']*'|\"[^\"]*\"|[^,}\n]+)", block):
            k = kvc.group(1)
            v = kvc.group(2).strip().rstrip(",")
            if (v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"')):
                v = v[1:-1]
            else:
                try:
                    v = json.loads(v)
                except Exception:
                    pass
            kv[k] = v
        if kv:
            presets[pname] = kv
    return presets


def extract_js_workflow_from_script(text: str) -> Optional[str]:
    """Extract workflow ID from JS source (agentContext.workflow assignment)."""
    # transformForAgentMode often has: workflow: 'xxx' or agentContext: { workflow: 'xxx' }
    for pat in [
        r"workflow\s*:\s*['\"`]([A-Za-z0-9_-]+)['\"`]",
        r"agentContext\s*:\s*\{[^}]*workflow\s*:\s*['\"`]([A-Za-z0-9_-]+)['\"`]",
    ]:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


def first_n_lines(text: str, n: int) -> str:
    return "\n".join(text.splitlines()[:n])


def js_payload_top_level_schema(payload: Dict[str, Any]) -> Dict[str, str]:
    """Return {key: type} for top-level payload keys."""
    schema: Dict[str, str] = {}
    for k, v in payload.items():
        if isinstance(v, dict):
            sub = list(v.keys())[:6]
            schema[k] = "object{" + ",".join(sub) + ("..." if len(v) > 6 else "") + "}"
        elif isinstance(v, list):
            if v and isinstance(v[0], dict):
                sub = list(v[0].keys())[:5]
                schema[k] = f"list[object{{{','.join(sub)}}}]"
            else:
                t = type(v[0]).__name__ if v else "any"
                schema[k] = f"list[{t}]"
        else:
            schema[k] = type(v).__name__
    return schema


# ---------- Build the index ----------

def go_input_map_str(inputs: List[Dict[str, Any]]) -> str:
    if not inputs:
        return "_none_"
    lines = ["| name | tv_input_id | type | default |", "|------|-------------|------|---------|"]
    for i in inputs:
        lines.append(f"| `{i['name']}` | `{i['tv_input_id']}` | {i['type']} | `{i['default']}` |")
    return "\n".join(lines)


def _normalize_default(v: Any) -> str:
    """Normalize a default value to a stable string for comparison."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        # Normalize numeric: int 5 and float 5.0 should match
        try:
            f = float(v)
            if f == int(f):
                return str(int(f))
            return str(f)
        except Exception:
            return str(v)
    if isinstance(v, str):
        # Strip surrounding quotes (Go source keeps them, JS source drops them)
        s = v
        if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
            s = s[1:-1]
        # JS bool string form (sometimes regex kept literal "True"/"False")
        if s in ("True", "False"):
            return s.lower()
        return s
    return str(v)


def js_input_map_str(js_script_text: str, meta: Optional[Dict[str, Any]]) -> str:
    items = extract_js_input_map(js_script_text) if js_script_text else []
    if not items and meta:
        items = meta.get("input_map") or []
    if not items:
        return "_none extracted — JS uses non-INPUT_MAP style_"
    lines = ["| variable | tvInputId | type | default |", "|----------|-----------|------|---------|"]
    for it in items:
        v = it.get("variable") or it.get("name", "")
        ti = it.get("tvInputId", "")
        t = it.get("type", "")
        d = it.get("default", "")
        d_str = _normalize_default(d) if not isinstance(d, str) or d not in ("True", "False") else d.lower()
        if isinstance(d, str) and not (d.startswith("'") or d.startswith('"')) and d not in ("True", "False"):
            d_str = d
        lines.append(f"| `{v}` | `{ti}` | `{t}` | `{d_str}` |")
    return "\n".join(lines)


def preset_compare_table(go_presets: Dict[str, Dict[str, Any]],
                         js_presets: Dict[str, Dict[str, Any]],
                         meta: Optional[Dict[str, Any]]) -> str:
    if not go_presets and not js_presets:
        return "_none_"
    # Also load preset JSON files from JS skill dir (some skills ship files)
    js_preset_files: Dict[str, Dict[str, Any]] = {}
    if meta:
        js_dir = Path(meta.get("skill_dir", ""))
        if js_dir.exists():
            for pf in js_dir.glob("*.json"):
                try:
                    js_preset_files[pf.stem] = json.loads(pf.read_text())
                except Exception:
                    pass
    # Merge JS source presets with file presets (file overrides source if same name)
    merged_js = dict(js_presets)
    merged_js.update(js_preset_files)
    all_names = sorted(set(list(go_presets.keys()) + list(merged_js.keys())))
    out = ["| preset | go | js | match |", "|--------|----|----|-------|"]
    for pname in all_names:
        gokv = go_presets.get(pname)
        jskv = merged_js.get(pname)
        if gokv is None:
            out.append(f"| `{pname}` | _missing_ | `{jskv}` | **JS-only — consider adding** |")
            continue
        if jskv is None:
            out.append(f"| `{pname}` | `{gokv}` | _missing_ | **Go-only — verify** |")
            continue
        # Normalize both sides for comparison
        def norm_kv(kv):
            return {k: _normalize_default(v) for k, v in kv.items()}
        go_n = norm_kv(gokv)
        js_n = norm_kv(jskv)
        matches = all(js_n.get(k) == go_n.get(k) for k in go_n if k in js_n)
        go_only = set(go_n.keys()) - set(js_n.keys())
        js_only = set(js_n.keys()) - set(go_n.keys())
        match = "OK"
        if not matches: match = "PARTIAL"
        if go_only: match += f" go-only:{go_only}"
        if js_only: match += f" js-only:{js_only}"
        out.append(f"| `{pname}` | `{gokv}` | `{jskv}` | {match} |")
    return "\n".join(out)


def main() -> None:
    go_skills = extract_go_skills()
    print(f"Extracted {len(go_skills)} Go skill parsers")

    # Build cross-reference table rows
    cross_rows: List[List[str]] = []
    for gs in go_skills:
        js_name = GO_TO_JS_NAME.get(gs["name"], gs["name"])
        meta = load_js_meta(js_name)
        payload = load_js_payload(js_name)
        js_script_path = find_js_script(js_name)
        js_script_text = read_text(js_script_path) if js_script_path else ""

        js_workflow = None
        js_has_envelope = False
        if payload and isinstance(payload.get("agentContext"), dict):
            js_workflow = payload["agentContext"].get("workflow")
            js_has_envelope = True
        if not js_workflow:
            js_workflow = extract_js_workflow_from_script(js_script_text) or (meta.get("workflow") if meta else "")
        js_workflow_display = js_workflow if js_workflow else "(none — JS does not emit agent-ready-v2 envelope)"
        js_pine = meta.get("pine_id") if meta else "?"
        pine_match = "OK" if js_pine == gs["pine_id"] else f"MISMATCH js={js_pine}"
        wf_match = "OK" if js_workflow == gs["workflow"] else f"DIFF js={js_workflow_display}"

        js_inputs = extract_js_input_map(js_script_text)
        js_input_count = len(js_inputs) or (len(meta.get("input_map") or []) if meta else 0)
        go_input_count = len(gs["inputs"])
        input_match = "OK" if js_input_count == go_input_count else f"DIFF js={js_input_count} go={go_input_count}"

        payload_keys = sorted(payload.keys()) if payload else []
        payload_status = f"{len(payload_keys)} keys" if payload else "NO PAYLOAD"

        cross_rows.append([
            gs["name"], js_name, gs["pine_id"], pine_match, wf_match,
            input_match, payload_status, gs["file"],
        ])

    # Build full markdown
    md: List[str] = []
    md.append("# TradingView Skill Reference Index — Go ↔ JS Cross-Reference")
    md.append("")
    md.append("**Generated:** 2026-07-19  ")
    md.append("**Source JS skills:** `/Volumes/ExMac/code/tradingview/js-experiment06`  ")
    md.append("**Go parsers:** `/Volumes/ExMac/code/tradingview/go/internal/skill/parsers`  ")
    md.append("**Dumps:** `skill-analysis/dumps/<skill>/` (stdout.txt, stderr.txt, help.txt, payload.json)  ")
    md.append("**Meta:** `skill-analysis/meta/<skill>.json`")
    md.append("")
    md.append("Use this index to fix Go skill commands. For each Go skill command (`tv <name>`),")
    md.append("the doc shows the source JS skill, Pine ID, whether the Go parser correctly")
    md.append("mirrors the JS output schema, and concrete discrepancies that need fixing.")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## 1. Quick Cross-Reference Table")
    md.append("")
    md.append("| Go skill | JS skill | Pine ID | PineID match | Workflow match | Inputs match | JS payload | Go parser file |")
    md.append("|----------|----------|--------|--------------|----------------|--------------|------------|----------------|")
    for r in cross_rows:
        md.append(f"| `tv {r[0]}` | `{r[1]}` | `{r[2]}` | {r[3]} | {r[4]} | {r[5]} | {r[6]} | `{r[7]}` |")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## 2. Per-Skill Detail")
    md.append("")
    for gs in go_skills:
        js_name = GO_TO_JS_NAME.get(gs["name"], gs["name"])
        meta = load_js_meta(js_name)
        payload = load_js_payload(js_name)
        js_help = load_js_help(js_name)
        js_script_path = find_js_script(js_name)
        js_script_text = read_text(js_script_path) if js_script_path else ""
        js_inputs_from_src = extract_js_input_map(js_script_text)
        js_presets_from_src = extract_js_presets(js_script_text)
        # Authoritative workflow from payload, fall back to source, then meta
        js_workflow = None
        js_has_envelope = False
        if payload and isinstance(payload.get("agentContext"), dict):
            js_workflow = payload["agentContext"].get("workflow")
            js_has_envelope = True
        if not js_workflow:
            js_workflow = extract_js_workflow_from_script(js_script_text) or (meta.get("workflow") if meta else "")
        js_workflow_display = js_workflow if js_workflow else "(none — JS does not emit agent-ready-v2 envelope)"
        js_md = ""
        if meta and meta.get("skill_dir"):
            js_md = read_text(Path(meta["skill_dir"]) / "SKILL.md")[:1500]

        md.append(f"### 2.{go_skills.index(gs)+1} `tv {gs['name']}` → JS `{js_name}`")
        md.append("")
        md.append(f"- **Synopsis:** {gs['synopsis']}")
        md.append(f"- **Pine ID:** `{gs['pine_id']}`  (JS: `{meta.get('pine_id') if meta else '?'}`)")
        md.append(f"- **Workflow ID:** `{gs['workflow']}`  (JS authoritative: `{js_workflow_display}`)")
        md.append(f"- **Go parser:** `{gs['file']}`  → func `{gs['parse_func']}` / format `{gs['format_func']}`")
        md.append(f"- **JS script:** `{js_script_path or (meta.get('script') if meta else '?')}`")
        md.append(f"- **Sample JS command:** `{meta.get('sample_command') if meta else '?'}`")
        sr = meta.get("sample_run", {}) if meta else {}
        md.append(f"- **JS sample run:** rc={sr.get('rc')} parsed_via={sr.get('parsed_via')} hint={sr.get('schema_hint')}")
        if sr.get("payload_keys"):
            md.append(f"- **JS payload top-level keys ({len(sr['payload_keys'])}):** `{', '.join(sr['payload_keys'])}`")
        md.append("")

        # Pine ID check
        if meta and meta.get("pine_id") != gs["pine_id"]:
            md.append(f"> ⚠️ **PINE ID MISMATCH** — Go `{gs['pine_id']}` ≠ JS `{meta.get('pine_id')}`. Fix: update Go `PineID` or update JS.")
            md.append("")
        # Workflow check (authoritative)
        if not js_workflow:
            md.append(f"> ⚠️ **JS DOES NOT EMIT agent-ready-v2 ENVELOPE** — JS payload has no `agentContext.workflow`. Go `{gs['workflow']}` is the authoritative identifier. Consider updating the JS script to emit `agentContext: {{ workflow: '{gs['workflow']}' }}` for parity.")
            md.append("")
        elif js_workflow != gs["workflow"]:
            md.append(f"> ⚠️ **WORKFLOW MISMATCH** — Go `{gs['workflow']}` ≠ JS `{js_workflow}` (authoritative from live JS agent payload). The Go parser should set `Workflow: \"{js_workflow}\"` to produce agent-compatible output.")
            md.append("")

        # Inputs
        md.append("#### Inputs (Go)")
        md.append("")
        md.append(go_input_map_str(gs["inputs"]))
        md.append("")
        md.append("#### Inputs (JS — INPUT_MAP from source)")
        md.append("")
        md.append(js_input_map_str(js_script_text, meta))
        md.append("")

        # Compare inputs one by one
        if gs["inputs"] and js_inputs_from_src:
            md.append("#### Input Comparison")
            md.append("")
            md.append("| Go name | Go TV id | JS variable | JS tvInputId | Name match | TV id match |")
            md.append("|---------|----------|-------------|--------------|------------|-------------|")
            js_by_name = {(ji.get("variable") or ji.get("name", "")): ji for ji in js_inputs_from_src}
            for gi in gs["inputs"]:
                matched = js_by_name.get(gi["name"])
                if matched:
                    jvar = matched.get("variable") or matched.get("name", "")
                    jti = matched.get("tvInputId", "")
                    name_ok = "OK" if jvar == gi["name"] else "DIFF"
                    ti_ok = "OK" if str(jti) == str(gi["tv_input_id"]) else f"DIFF js={jti}"
                    md.append(f"| `{gi['name']}` | `{gi['tv_input_id']}` | `{jvar}` | `{jti}` | {name_ok} | {ti_ok} |")
                else:
                    md.append(f"| `{gi['name']}` | `{gi['tv_input_id']}` | — | — | **MISSING in JS** | — |")
            # Also list JS inputs not in Go
            go_names = {gi["name"] for gi in gs["inputs"]}
            for jn, ji in js_by_name.items():
                if jn not in go_names:
                    ji_type = ji.get("type", "")
                    if ji_type == "color":
                        marker = "color (cosmetic, safe to omit)"
                    else:
                        marker = "**MISSING in Go**"
                    md.append(f"| — | — | `{jn}` | `{ji.get('tvInputId','')}` | {marker} | — |")
            md.append("")

        # Presets
        if gs["presets"] or js_presets_from_src:
            md.append("#### Presets")
            md.append("")
            md.append(preset_compare_table(gs["presets"], js_presets_from_src, meta))
            md.append("")

        # Parsing logic
        md.append("#### Go parser reads from periods[] (getField aliases)")
        md.append("")
        if gs["period_fields_read"]:
            md.append("`" + "`, `".join(gs["period_fields_read"]) + "`")
        else:
            md.append("_none — parser does not use getField_")
        md.append("")
        if gs["graphic_fields_read"]:
            md.append(f"**Graphic keys read:** `{', '.join(gs['graphic_fields_read'])}`")
            md.append("")

        # Structure produced
        md.append("#### Go Structure keys produced")
        md.append("")
        if gs["structure_keys"]:
            md.append("`" + "`, `".join(gs["structure_keys"]) + "`")
        else:
            md.append("_parser does not populate Structure map_")
        md.append("")

        # JS payload schema
        if payload:
            md.append("#### JS agent payload schema (from live dump)")
            md.append("")
            md.append("Dump: `skill-analysis/dumps/" + js_name + "/payload.json`")
            md.append("")
            md.append("| key | type/shape |")
            md.append("|-----|-----------|")
            for k, v in js_payload_top_level_schema(payload).items():
                md.append(f"| `{k}` | `{v}` |")
            md.append("")

        # Go SkillResult schema (from struct definition)
        md.append("#### Go `SkillResult` schema (target output)")
        md.append("")
        md.append("| field | type |")
        md.append("|-------|------|")
        md.append("| `status` | string |")
        md.append("| `workflow` | string |")
        md.append("| `market.lastPrice` | any |")
        md.append("| `market.bias` | string |")
        md.append("| `structure` | map[string]any (per-skill keys above) |")
        md.append("| `opportunities[]` | {rank, setup, direction, confidence, confluenceScore, distanceFromPrice, isStale, rationale} |")
        md.append("| `narrative.marketStructure` | string |")
        md.append("| `narrative.primaryOpportunity` | string |")
        md.append("| `narrative.warnings[]` | string |")
        md.append("| `narrative.watchlist[]` | string |")
        md.append("| `validation.passed` | bool |")
        md.append("| `validation.warnings[]` | string |")
        md.append("| `conformance.hasValidData` | bool |")
        md.append("| `conformance.agenticScore` | float64 |")
        md.append("| `raw` | map (omitted in agent output) |")
        md.append("")

        # Discrepancies / fix recommendations
        md.append("#### Discrepancies & fix recommendations")
        md.append("")
        recs: List[str] = []

        # Pine ID
        if meta and meta.get("pine_id") != gs["pine_id"]:
            recs.append(f"- **Pine ID mismatch.** Set Go `PineID = \"{meta.get('pine_id')}\"` (current: `{gs['pine_id']}`).")

        # Workflow (authoritative from payload)
        if not js_workflow:
            recs.append(f"- **JS does not emit `agentContext.workflow`.** Go `{gs['workflow']}` is the authoritative workflow ID. Optionally update the JS script to emit `agentContext: {{ workflow: '{gs['workflow']}' }}`.")
        elif js_workflow != gs["workflow"]:
            recs.append(f"- **Workflow mismatch.** Set Go `Workflow: \"{js_workflow}\"` (current: `{gs['workflow']}`). Authoritative source: JS agent payload `agentContext.workflow`.")

        # Missing inputs in Go vs JS (using source-extracted INPUT_MAP)
        if js_inputs_from_src:
            js_names = {(ji.get("variable") or ji.get("name", "")): ji for ji in js_inputs_from_src}
            for gi in gs["inputs"]:
                if gi["name"] not in js_names:
                    recs.append(f"- **Go input `{gi['name']}` (`{gi['tv_input_id']}`) not in JS INPUT_MAP.** Verify or remove.")
            for jn, ji in js_names.items():
                if not any(gi["name"] == jn for gi in gs["inputs"]):
                    ji_type = ji.get("type", "")
                    if ji_type == "color":
                        recs.append(f"- **JS input `{jn}` (`{ji.get('tvInputId','')}`) is type `color` — cosmetic, safe to omit in Go (CLI doesn't render chart colors).")
                    else:
                        recs.append(f"- **JS input `{jn}` (`{ji.get('tvInputId','')}`) missing in Go.** Consider adding `InputDef{{Name: \"{jn}\", TVInputID: \"{ji.get('tvInputId','')}\", Type: \"{ji_type or 'string'}\", Default: {_normalize_default(ji.get('default'))}}}`.")

            # TV input ID mismatches
            for gi in gs["inputs"]:
                ji = js_names.get(gi["name"])
                if ji and str(ji.get("tvInputId", "")) != str(gi["tv_input_id"]):
                    recs.append(f"- **Input `{gi['name']}` TV input ID mismatch.** Go=`{gi['tv_input_id']}` JS=`{ji.get('tvInputId')}`.")
            # Type mismatches
            for gi in gs["inputs"]:
                ji = js_names.get(gi["name"])
                if ji and ji.get("type") and ji.get("type") != gi["type"]:
                    recs.append(f"- **Input `{gi['name']}` type mismatch.** Go=`{gi['type']}` JS=`{ji.get('type')}`.")
            # Default mismatches (normalized)
            for gi in gs["inputs"]:
                ji = js_names.get(gi["name"])
                if ji and ji.get("default") is not None:
                    js_def = _normalize_default(ji.get("default"))
                    go_def = _normalize_default(gi["default"])
                    if js_def != go_def:
                        recs.append(f"- **Input `{gi['name']}` default mismatch.** Go=`{gi['default']}` JS=`{ji.get('default')}`.")

        # Preset mismatches
        if gs["presets"] and js_presets_from_src:
            for pname, gokv in gs["presets"].items():
                jskv = js_presets_from_src.get(pname)
                if not jskv:
                    recs.append(f"- **Preset `{pname}` not in JS source.** Verify or remove.")
                else:
                    for k, v in gokv.items():
                        if k in jskv and _normalize_default(jskv[k]) != _normalize_default(v):
                            recs.append(f"- **Preset `{pname}.{k}` mismatch.** Go=`{v}` JS=`{jskv[k]}`.")
            for pname, jskv in js_presets_from_src.items():
                if pname not in gs["presets"]:
                    recs.append(f"- **JS preset `{pname}` missing in Go.** Consider adding.")

        # Payload coverage — which JS top-level keys are missing in Go output
        if payload:
            go_outputs = set(["status","workflow","market","structure","opportunities","narrative","validation","conformance","raw"])
            js_keys = set(payload.keys())
            # agent envelope keys (handled by ToAgent())
            agent_envelope = {"agentContext","execution","exitCode","timestamp","schemaVersion"}
            missing = js_keys - go_outputs - agent_envelope
            if missing:
                recs.append(f"- **JS payload has rich keys not in Go SkillResult:** `{', '.join(sorted(missing))}`. Decide for each: mirror into `Structure`, expose via `Raw`, or drop if redundant.")
            # Check that payload's agentContext.workflow matches
            ac = payload.get("agentContext")
            if isinstance(ac, dict) and ac.get("workflow") and ac.get("workflow") != gs["workflow"]:
                # already noted above
                pass

        if not recs:
            recs.append("- ✅ No structural discrepancies detected. Verify numeric parsing by running `tv <skill> --json --agent` and diffing against `skill-analysis/dumps/<js-skill>/payload.json`.")
        md.extend(recs)
        md.append("")

        # Help output
        if js_help.strip():
            md.append("#### JS `--help` output")
            md.append("")
            md.append("```")
            md.append(js_help.strip()[:1200])
            md.append("```")
            md.append("")

        # SKILL.md excerpt
        if js_md:
            md.append("#### JS SKILL.md excerpt (first 1500 chars)")
            md.append("")
            md.append("```markdown")
            md.append(js_md)
            md.append("```")
            md.append("")

        md.append("---")
        md.append("")

    # JS-only skills (no Go parser)
    md.append("## 3. JS Skills Without a Go Command")
    md.append("")
    md.append("These JS skills exist in `js-experiment06` but have no dedicated `tv <skill>` Go command.")
    md.append("They are either meta-utilities or not yet ported.")
    md.append("")
    md.append("| JS skill | Type | Notes |")
    md.append("|----------|------|-------|")
    notes = {
        "generic-indicator": "Universal Pine runner. Go equivalent: `tv run <pineId> --signals`.",
        "tv-cron-orchestrator": "Schedules recurring scans + position monitor. Not a Pine indicator.",
        "nlm-cli-skill": "Meta-skill (NLM CLI wrapper). Not a Pine indicator.",
        "tv-indicator": "tvcli.js — parent CLI. Replaced by this Go binary.",
        "youtube-to-tv-pine": "Converts YouTube videos to Pine scripts. Not a Pine indicator.",
    }
    for js_name in JS_ONLY_SKILLS:
        md.append(f"| `{js_name}` | meta/utility | {notes.get(js_name,'')} |")
    md.append("")

    # Architecture overview
    md.append("## 4. Architecture Overview")
    md.append("")
    md.append("```")
    md.append("┌─────────────────────────────────────────────────────────────────────┐")
    md.append("│  tvcli (Go binary)                                                   │")
    md.append("│                                                                     │")
    md.append("│  cmd/tvcli/main.go                                                   │")
    md.append("│    └─ cli.Root → cmd.RegisterAll                                     │")
    md.append("│                                                                     │")
    md.append("│  internal/cmd/skillcmd.go   ← generic skill command runner           │")
    md.append("│    1. resolve inputs (defaults → preset → flags → passthrough)       │")
    md.append("│    2. service.RunScript (PineID, symbol, tf, bars, inputs)           │")
    md.append("│    3. skill.ParseOutput(periods, graphic, tf, symbol, args)         │")
    md.append("│    4. FormatText or JSON / agent-ready envelope                      │")
    md.append("│                                                                     │")
    md.append("│  internal/skill/skill.go   ← Skill, InputDef, SkillResult, AgentResult│")
    md.append("│  internal/skill/registry.go ← global Register/Get/All                │")
    md.append("│  internal/skill/parsers/*.go ← per-skill ParseOutput implementations │")
    md.append("│                                                                     │")
    md.append("│  service.RunScript (pinefacade) → TradingView WS                     │")
    md.append("│    returns: periods []map[string]any, graphic map[string]map[string]any│")
    md.append("└─────────────────────────────────────────────────────────────────────┘")
    md.append("")
    md.append("JS reference: js-experiment06/<skill>.cjs")
    md.append("  1. parseArgs → INPUT_MAP → Pine inputs")
    md.append("  2. tv.cjs WS fetch (same raw periods/graphic)")
    md.append("  3. parseOutput(periods, graphic) → structured object")
    md.append("  4. transformForAgentMode → agent-ready-v2 envelope")
    md.append("  5. stdout: pretty table OR JSON (with --agent --json)")
    md.append("```")
    md.append("")
    md.append("Both Go and JS read the same raw `periods[]` / `graphic` from the same")
    md.append("TradingView Pine WS. The JS script's `--agent` output is the authoritative")
    md.append("schema; the Go parser must reproduce the equivalent `SkillResult` so that")
    md.append("`ToAgent()` produces a comparable `agent-ready-v2` envelope.")
    md.append("")
    md.append("## 5. SkillResult vs JS agent payload mapping")
    md.append("")
    md.append("| Go `SkillResult` field | JS agent payload key | Notes |")
    md.append("|------------------------|----------------------|-------|")
    md.append("| `status`               | `status`             | direct |")
    md.append("| `workflow`             | `agentContext.workflow` | Go sets, JS in `agentContext` |")
    md.append("| `market.lastPrice`      | (per-skill: `latest.close`, `currentBar.close`, etc.) | Go consolidates |")
    md.append("| `market.bias`          | `volume.dominanceRatio`→bias, `mtf.overallBias`, `combinedTrend`, etc. | per-skill |")
    md.append("| `structure`            | per-skill (e.g. `volume`, `mtf`, `clusters`, `trend`, `signals`) | **mirror JS payload here** |")
    md.append("| `opportunities[]`      | `opportunities[]`    | direct; match `setup/direction/confidence/confluenceScore/rationale` |")
    md.append("| `narrative.marketStructure` | `narrative.marketStructure` | direct |")
    md.append("| `narrative.primaryOpportunity` | `narrative.primaryOpportunity` | direct |")
    md.append("| `narrative.warnings[]` | `narrative.warnings`/`watchlist` | direct |")
    md.append("| `conformance.hasValidData` | `conformance.hasValidData` | direct |")
    md.append("| `conformance.agenticScore` | `conformance.agenticScore` | direct |")
    md.append("| _not in Go_            | `agentContext`, `execution`, `timestamp`, `exitCode`, `schemaVersion` | added by `Skill.ToAgent()` envelope |")
    md.append("| _not in Go_            | per-skill rich keys (`latestBars`, `recentCrosses`, `clusters`, `trendLabels`, `grades`, `tradePlan`, `tqiBreakdown`, etc.) | **should be mirrored into `Structure` or `Raw`** |")
    md.append("")
    md.append("## 6. How to Use This Index When Fixing a Go Skill")
    md.append("")
    md.append("**Companion doc:** [`PARSING_PROTOCOL_FOR_GO.md`](PARSING_PROTOCOL_FOR_GO.md) — describes the JS skill output protocol (delimiter vs heuristic extraction), the `AgentPayload` Go struct shape, and the routing/dispatch rules. Read it first.")
    md.append("")
    md.append("Steps:")
    md.append("")
    md.append("1. Open the per-skill section above (e.g. `2.5 tv sniper → JS precision-sniper`).")
    md.append("2. Confirm Pine ID and Workflow match. Fix the constants in the Go parser file if not.")
    md.append("3. Diff the Go `Inputs` table vs the JS `INPUT_MAP` table. Fix names/TV input IDs/defaults/types.")
    md.append("4. Read `skill-analysis/dumps/<js-skill>/payload.json` to see the authoritative JS agent output.")
    md.append("5. Identify rich JS payload keys that have no Go `Structure`/`Raw` counterpart — decide whether to port them.")
    md.append("6. Run the Go skill and diff:")
    md.append("   ```")
    md.append("   ./tvcli <go-skill> --symbol <SYM> --tf <TF> --json --agent > /tmp/go.json")
    md.append("   diff <(jq -S . /tmp/go.json) <(jq -S . skill-analysis/dumps/<js-skill>/payload.json)")
    md.append("   ```")
    md.append("7. Iterate the Go `ParseOutput` function until the diff converges (allow for live data drift).")
    md.append("")
    md.append("### Regenerating this index")
    md.append("")
    md.append("```bash")
    md.append("# Re-run all JS scripts and refresh dumps/meta (slow — hits TradingView live)")
    md.append("python3 analyze_skills.py")
    md.append("")
    md.append("# Re-extract payloads from existing stdout.txt dumps (fixes input-echo vs real-payload issues)")
    md.append("python3 skill-analysis/reextract_payloads.py")
    md.append("")
    md.append("# Re-build this index after changes to Go parsers or JS scripts")
    md.append("python3 skill-analysis/build_reference_index.py")
    md.append("```")
    md.append("")
    md.append("## 7. Verification Status")
    md.append("")
    md.append("| Skill | JS payload dumped | JS meta | Go parser |")
    md.append("|-------|-------------------|---------|-----------|")
    for gs in go_skills:
        js_name = GO_TO_JS_NAME.get(gs["name"], gs["name"])
        has_payload = (DUMPS_DIR / js_name / "payload.json").exists()
        has_meta = (META_DIR / f"{js_name}.json").exists()
        md.append(f"| `tv {gs['name']}` | {'yes' if has_payload else 'NO'} | {'yes' if has_meta else 'NO'} | `{gs['file']}` |")
    md.append("")
    md.append("---")
    md.append("")
    md.append("_End of index. Regenerate via `python3 skill-analysis/build_reference_index.py`._")

    OUT_FILE.write_text("\n".join(md), encoding="utf-8")
    print(f"Wrote {OUT_FILE} ({len(md)} lines)")
    # also print summary of mismatches
    print("\n=== Summary of mismatches ===")
    for r in cross_rows:
        flags = []
        if "MISMATCH" in r[3]: flags.append("PINE_ID")
        if "DIFF" in r[4]: flags.append("WORKFLOW")
        if "DIFF" in r[5]: flags.append("INPUTS")
        if flags:
            print(f"  {r[0]:<14} -> {' '.join(flags)}")


if __name__ == "__main__":
    main()
