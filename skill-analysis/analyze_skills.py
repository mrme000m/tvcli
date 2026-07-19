#!/usr/bin/env python3
"""
Analyze TradingView indicator skills in js-experiment06.
Produces reference docs + raw response dumps in the current Go workspace.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SRC_ROOT = Path("/Volumes/ExMac/code/tradingview/js-experiment06")
OUT_ROOT = Path("/Volumes/ExMac/code/tradingview/go/skill-analysis")
DUMPS_DIR = OUT_ROOT / "dumps"
META_DIR = OUT_ROOT / "meta"
EXCLUDED_DIRS = {"node_modules", ".git", ".vscode", ".claude", ".firecrawl", "docs"}

# Override sample commands per skill (base command, arguments appended by caller for mode flags)
SAMPLE_ARGS: Dict[str, List[str]] = {
    "generic-indicator": [
        "--builtin", "RSI", "--symbol", "BTCUSDT", "--tf", "1h", "--bars", "300",
    ],
    "tv-indicator": [
        "run", "PUB;ff1a0136336340f38e908eeb12ea33aa", "BTCUSDT", "--format=json", "--quiet", "--range", "200",
    ],
    "volume-gaps-imbalances-zeiierman": ["BTCUSDT", "--preset", "scalping", "--tf", "5m", "--bars", "300"],
    "buying-selling-volume": ["BTCUSDT", "--preset", "scalping", "--tf", "5m", "--bars", "300"],
    "precision-sniper": ["BTCUSDT", "--preset", "auto", "--tf", "15m", "--bars", "300"],
    "self-aware-trend-system": ["BTCUSDT", "--preset", "default", "--tf", "15m", "--bars", "300"],
    "golden-rule-strategy": ["BTCUSDT", "--tf", "1h", "--bars", "500"],
    "ict-auto-validated-smc": ["BTCUSDT", "--tf", "1h", "--bars", "500"],
    "smart-money-concepts": ["BTCUSDT", "--tf", "1h", "--bars", "500"],
    "xauusd-mtf-trend": ["XAUUSD", "--tf", "1h", "--bars", "500"],
    "tv-cron-orchestrator": ["watchlist", "--symbols", "BTCUSDT,ETHUSDT", "--indicators", "precision-sniper", "--tf", "1h"],
}

# Scripts inside skill dirs that aren't rooted at project root
SKILL_SCRIPT_OVERRIDES: Dict[str, Path] = {
    "tv-cron-orchestrator": SRC_ROOT / "tv-cron-orchestrator" / "scripts" / "tv-cron-orchestrator.cjs",
    "youtube-to-tv-pine": SRC_ROOT / "youtube-to-tv-pine" / "scripts" / "youtube-to-tv-pine.cjs",
    "nlm-cli-skill": None,
    "tv-indicator": SRC_ROOT / "tv-indicator" / "scripts" / "tvcli.js",
}


def discover_skills() -> List[Path]:
    return sorted([d for d in SRC_ROOT.iterdir() if d.is_dir() and (d / "SKILL.md").exists() and d.name not in EXCLUDED_DIRS])


def read_text(path: Optional[Path], limit: Optional[int] = None) -> str:
    if path is None:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return text[:limit] if limit else text
    except Exception as e:
        return f"(error reading {path}: {e})"


def extract_pine_id(text: str) -> Optional[str]:
    m = re.search(r"const\s+PINE_ID\s*=\s*['\"]([^'\"]+)['\"]", text)
    if m:
        return m.group(1)
    m = re.search(r"Pine\s*ID[:\s]+['\"]([^'\"]+)['\"]", text, re.IGNORECASE)
    return m.group(1) if m else None


def extract_block(text: str, name: str) -> Optional[str]:
    """Extract a top-level const/function block by brace counting."""
    m = re.search(r"(const\s+" + re.escape(name) + r"\s*=\s*\{|function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{)", text)
    if not m:
        return None
    start = m.start()
    depth = 0
    in_string = None
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_string:
            escaped = True
            continue
        if in_string:
            if ch == in_string and (i == 0 or text[i - 1] != "\\"):
                in_string = None
            continue
        if ch in ('"', "'", "`"):
            in_string = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_input_map(text: str) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    raw = extract_block(text, "INPUT_MAP")
    items: List[Dict[str, Any]] = []
    if not raw:
        return None, items
    # Heuristic: capture { variable: '...', tvInputId: '...', type: '...', default: ... } entries
    for m in re.finditer(r"\{\s*([^}]+)\s*\}", raw):
        block = m.group(1)
        entry: Dict[str, Any] = {}
        for kv in re.finditer(r"([A-Za-z0-9_$]+)\s*:\s*([^,]+)", block):
            k, v = kv.group(1), kv.group(2).strip()
            v = v.rstrip(",")
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            else:
                try:
                    v = json.loads(v)
                except Exception:
                    pass
            entry[k] = v
        if entry:
            items.append(entry)
    # If no array-of-objects found, try object-style keys
    if not items:
        for m in re.finditer(r"([A-Za-z0-9_$]+)\s*:\s*\{\s*([^}]+)\s*\}", raw):
            key = m.group(1)
            block = m.group(2)
            entry = {"name": key}
            for kv in re.finditer(r"([A-Za-z0-9_$]+)\s*:\s*([^,]+)", block):
                k, v = kv.group(1), kv.group(2).strip().rstrip(",")
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                else:
                    try:
                        v = json.loads(v)
                    except Exception:
                        pass
                entry[k] = v
            items.append(entry)
    return raw, items


def extract_transform_keys(text: str) -> Tuple[Optional[str], Optional[List[str]]]:
    body = extract_block(text, "transformForAgentMode")
    if not body:
        return None, None
    # find the returned object literal and pull top-level keys
    m = re.search(r"return\s*\{\s*", body)
    if not m:
        return body, None
    idx = m.end()
    depth = 0
    in_str = None
    escaped = False
    keys = []
    stack: List[Dict] = [OrderedDict()]
    current_key = None
    i = idx
    n = len(body)
    while i < n:
        ch = body[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if ch == "\\" and in_str:
            escaped = True
            i += 1
            continue
        if in_str:
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ('"', "'", "`"):
            in_str = ch
            i += 1
            continue
        if ch == "{":
            if depth == 0:
                pass
            depth += 1
            i += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                return body, list(stack[0].keys())
            i += 1
            continue
        if depth == 1:
            # at top level of the return object
            if ch == ":":
                # preceding token is current key
                # walk back to collect identifier or string
                j = i - 1
                while j >= idx and body[j] in " \t\n\r":
                    j -= 1
                if body[j] in ('"', "'"):
                    q = body[j]
                    k_end = j
                    j -= 1
                    while j >= idx and body[j] != q:
                        j -= 1
                    current_key = body[j + 1 : k_end]
                    if current_key not in stack[0]:
                        stack[0][current_key] = None
                else:
                    k_end = j + 1
                    while j >= idx and (body[j].isalnum() or body[j] in "_$"):
                        j -= 1
                    current_key = body[j + 1 : k_end]
                    if current_key not in stack[0]:
                        stack[0][current_key] = None
            if ch == ",":
                current_key = None
        i += 1
    return body, list(stack[0].keys()) if stack[0] else None


def extract_workflow_id(text: str) -> Optional[str]:
    for pat in [r"workflow:\s*['\"]([^'\"]+)['\"]", r"workflow\s*:\s*['\"]([^'\"]+)['\"]"]:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


def extract_parsing_function_names(text: str) -> List[str]:
    return sorted(set(re.findall(r"function\s+(parse[A-Za-z0-9_]+)\s*\(", text)))


def extract_parse_output_field_names(text: str) -> List[str]:
    body = extract_block(text, "parseOutput") or ""
    fields: List[str] = []
    for line in body.splitlines():
        line = line.strip()
        if line.lower().startswith("return"):
            continue
        m = re.search(r"^([A-Za-z0-9_$]+):", line)
        if m:
            fields.append(m.group(1))
    return fields


def _ensure_str(v) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="ignore")
    return str(v) if v is not None else ""


def run_node(script: Path, args: List[str], timeout: int = 60) -> Tuple[int, str, str]:
    if not script or not script.exists():
        return -3, "", f"script not found: {script}"
    cmd = ["node", str(script)] + args
    env = os.environ.copy()
    env["NODE_NO_WARNINGS"] = "1"
    try:
        proc = subprocess.run(cmd, cwd=str(SRC_ROOT), capture_output=True, text=True, timeout=timeout, env=env)
        return proc.returncode, _ensure_str(proc.stdout), _ensure_str(proc.stderr)
    except subprocess.TimeoutExpired as e:
        return -1, _ensure_str(e.stdout), _ensure_str(e.stderr) or f"Timeout after {timeout}s"
    except Exception as e:
        return -2, "", str(e)


def parse_payload(stdout: str) -> Tuple[Optional[Dict], str]:
    start = stdout.find("<<<AGENT_JSON_START>>>")
    end = stdout.find("<<<AGENT_JSON_END>>>")
    if start != -1 and end != -1 and end > start:
        txt = stdout[start + len("<<<AGENT_JSON_START>>>") : end].strip()
        try:
            return json.loads(txt), "delimiter"
        except Exception:
            pass
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line[0] not in "{[":
            continue
        try:
            return json.loads(line), "line"
        except Exception:
            continue
    depth = 0
    start_i = -1
    for i, ch in enumerate(stdout):
        if ch == "{":
            if depth == 0:
                start_i = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start_i != -1:
                try:
                    cand = stdout[start_i : i + 1]
                    obj = json.loads(cand)
                    if isinstance(obj, dict) and len(obj) > 2:
                        return obj, "bracket"
                except Exception:
                    pass
                start_i = -1
    return None, "none"


def schema_hint(payload: Optional[Dict]) -> Optional[str]:
    if not payload:
        return None
    pm = payload.get("_parserMeta")
    if isinstance(pm, dict) and pm.get("workflow"):
        return pm["workflow"]
    ac = payload.get("agentContext")
    if isinstance(ac, dict) and ac.get("workflow"):
        return ac["workflow"]
    keys = set(payload.keys())
    diag = {
        "adaptive-supertrend-quality": {"tqiBreakdown", "tradePlan", "regime"},
        "smart-money-concepts": {"bosCount", "fvgCount", "active"},
        "ema-confluence-sniper": {"grades", "emaFast"},
        "ema-atr-structure": {"trailTrend", "combinedTrend"},
    }
    for wid, need in diag.items():
        if any(k in keys for k in need):
            return wid
    return None


def find_pine_files(skill_dir: Path) -> List[Path]:
    return sorted(skill_dir.rglob("*.pine"))


def pine_inputs_snippet(path: Path) -> List[str]:
    text = read_text(path, 40_000)
    # grab input statement lines and nearby group comments
    out = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.search(r"^\s*\w+\s*\w+\s*=\s*input\.", line) or re.search(r"input\.", line):
            snippet = "\n".join(lines[max(0, i - 2) : i + 1]).rstrip()
            out.append(snippet)
    return out[:30]


def find_skill_script(skill_dir: Path) -> Optional[Path]:
    name = skill_dir.name
    if name in SKILL_SCRIPT_OVERRIDES:
        return SKILL_SCRIPT_OVERRIDES[name]
    root = SRC_ROOT / f"{name}.cjs"
    if root.exists():
        return root
    alt = skill_dir / "scripts" / f"{name}.cjs"
    if alt.exists():
        return alt
    return None


def analyze_skill(skill_dir: Path) -> Dict[str, Any]:
    name = skill_dir.name
    print(f"[analyze] {name}", flush=True)
    md = read_text(skill_dir / "SKILL.md", 8_000)
    script = find_skill_script(skill_dir)
    script_text = read_text(script, 120_000) if script else ""

    pine_id = extract_pine_id(script_text) or extract_pine_id(md)
    input_map_raw, input_map_items = extract_input_map(script_text)
    transform_body, transform_keys = extract_transform_keys(script_text)
    workflow = extract_workflow_id(script_text)
    parsing_funcs = extract_parsing_function_names(script_text)
    parse_output_fields = extract_parse_output_field_names(script_text)
    pine_files = find_pine_files(skill_dir)

    # run --help
    help_rc, help_out, help_err = -1, "", ""
    if script:
        help_rc, help_out, help_err = run_node(script, ["--help"], timeout=15)
    supports_silent = "--silent" in help_out or "--silent" in script_text
    supports_agent = "--agent" in help_out or "transformForAgentMode" in script_text

    # run sample invocation
    sample_cmd: List[str] = []
    sample_rc, sample_out, sample_err = -1, "", ""
    if script:
        base = SAMPLE_ARGS.get(name, ["BTCUSDT", "--tf", "1h", "--bars", "300"])
        sample_cmd = list(base)
        if supports_agent:
            sample_cmd.extend(["--agent", "--json"])
        if supports_silent:
            sample_cmd.append("--silent")
        sample_rc, sample_out, sample_err = run_node(script, sample_cmd, timeout=75)

    payload, parsed_via = parse_payload(sample_out)
    hint = schema_hint(payload)

    # write dump files
    dump_base = DUMPS_DIR / name
    dump_base.mkdir(parents=True, exist_ok=True)
    (dump_base / "stdout.txt").write_text(_ensure_str(sample_out), encoding="utf-8", errors="ignore")
    (dump_base / "stderr.txt").write_text(_ensure_str(sample_err), encoding="utf-8", errors="ignore")
    (dump_base / "help.txt").write_text(_ensure_str(help_out), encoding="utf-8", errors="ignore")
    if payload is not None:
        (dump_base / "payload.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8", errors="ignore")

    script_path_str = str(script) if script else None
    meta = OrderedDict([
        ("name", name),
        ("skill_dir", str(skill_dir)),
        ("script", script_path_str),
        ("pine_id", pine_id),
        ("workflow", workflow),
        ("supports_agent", supports_agent),
        ("supports_silent", supports_silent),
        ("input_map", input_map_items),
        ("transform_keys", transform_keys),
        ("parsing_functions", parsing_funcs),
        ("parse_output_fields", parse_output_fields),
        ("sample_command", ("node " + (str(script.relative_to(SRC_ROOT)) if script else "N/A") + " " + " ".join(sample_cmd)) if script else "N/A"),
        ("sample_run", OrderedDict([
            ("rc", sample_rc),
            ("parsed_via", parsed_via),
            ("schema_hint", hint),
            ("stdout_chars", len(sample_out)),
            ("stderr_chars", len(sample_err)),
            ("payload_keys", sorted(payload.keys()) if payload else None),
        ])),
        ("pine_files", [str(p.relative_to(SRC_ROOT)) for p in pine_files]),
        ("skill_md_excerpt", md[:2500]),
        ("help_output", help_out[:2000]),
        ("stderr_excerpt", sample_err[:2000]),
    ])

    meta_path = META_DIR / f"{name}.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8", errors="ignore")
    print(f"[done] {name} rc={sample_rc} payload={payload is not None} hint={hint}", flush=True)
    return meta


def build_index(skills: List[Dict[str, Any]]) -> None:
    lines = [
        "# TradingView Skill Command Reference Index",
        "",
        "Generated from `/Volumes/ExMac/code/tradingview/js-experiment06` for fixing the corresponding Go skill commands.",
        "",
        "## Quick Reference",
        "",
        "| Skill | Script | Pine ID | Workflow | Agent | Silent | Sample RC | Payload? | Dump |",
        "|-------|--------|---------|----------|-------|------|-----------|----------|------|",
    ]
    for s in skills:
        name = s["name"]
        script_path = s.get("script") or None
        if script_path and script_path == "None":
            script_path = None
        script_rel = str(Path(script_path).relative_to(SRC_ROOT)) if script_path else "N/A"
        dump = f"[dumps](dumps/{name}/)"
        meta = f"[meta](meta/{name}.json)"
        agent = "✅" if s.get("supports_agent") else "❌"
        silent = "✅" if s.get("supports_silent") else "❌"
        rc = s.get("sample_run", {}).get("rc")
        payload = "✅" if s.get("sample_run", {}).get("payload_keys") else "❌"
        lines.append(
            f"| {name} | `{script_rel}` | {s.get('pine_id') or '-'} | {s.get('workflow') or '-'} | {agent} | {silent} | {rc} | {payload} | {dump} / {meta} |"
        )

    lines.extend(["", "## Per-Skill Details", ""])
    for s in skills:
        name = s["name"]
        lines.extend([
            f"### {name}",
            "",
            f"- **Script**: `{s.get('script') if s.get('script') and s.get('script') != 'None' else 'N/A'}`", 
            f"- **Pine ID**: `{s.get('pine_id') or 'N/A'}`",
            f"- **Workflow ID**: `{s.get('workflow') or 'N/A'}`",
            f"- **Agent mode**: {'yes' if s.get('supports_agent') else 'no'} — **Silent**: {'yes' if s.get('supports_silent') else 'no'}",
            f"- **Sample command**: `{s.get('sample_command') if s.get('sample_command') and s.get('sample_command') != 'N/A' else 'see SKILL.md / help output'}`",
            f"- **Sample exit code**: {s.get('sample_run', {}).get('rc')}",
            f"- **Payload parser method**: {s.get('sample_run', {}).get('parsed_via')}",
            f"- **Schema hint from payload**: {s.get('sample_run', {}).get('schema_hint') or 'N/A'}",
            "",
            "#### Input Map",
            "",
        ])
        items = s.get("input_map", [])
        if items:
            lines.append("| variable | tvInputId | type | default |")
            lines.append("|----------|-----------|------|---------|")
            for it in items:
                lines.append(f"| {it.get('variable') or it.get('name','')} | {it.get('tvInputId','')} | {it.get('type','')} | {it.get('default','')} |")
        else:
            lines.append("_No INPUT_MAP extracted._")
        lines.extend(["", "#### Agent payload top-level keys", ""])
        keys = s.get("sample_run", {}).get("payload_keys")
        if keys:
            lines.append("`" + "`, `".join(keys) + "`")
        else:
            lines.append("_No payload parsed (run failed or non-JSON output)._")
        lines.extend(["", "#### Parsing functions", "", "`" + "`, `".join(s.get("parsing_functions", []) or ["none"]) + "`", ""])
        lines.extend(["#### ParseOutput object fields", "", "`" + "`, `".join(s.get("parse_output_fields", []) or ["none"]) + "`", ""])
        lines.extend(["#### Stderr excerpt (first 500 chars)", "", "```", (s.get("stderr_excerpt") or "(none)")[:500], "```", ""])
        lines.extend(["#### Help output", "", "```text", (s.get("help_output") or "(none)")[:1200], "```", ""])
        lines.extend(["#### SKILL.md excerpt", "", (s.get("skill_md_excerpt") or "")[:800], ""])

    (OUT_ROOT / "SKILL_REFERENCE_INDEX.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Re-run live samples even if cached meta exists")
    args = parser.parse_args()

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)
    skill_dirs = discover_skills()
    print(f"Discovered {len(skill_dirs)} skills")
    results: List[Dict[str, Any]] = []
    FORCE_REFRESH = ("generic-indicator", "tv-indicator", "youtube-to-tv-pine")
    for d in skill_dirs:
        cache_path = META_DIR / f"{d.name}.json"
        cached = cache_path.exists() and d.name not in FORCE_REFRESH and not args.force
        if cached:
            try:
                cached_meta = json.loads(cache_path.read_text(encoding="utf-8", errors="ignore"))
                print(f"[cache] {d.name}", flush=True)
                results.append(cached_meta)
                continue
            except Exception as e:
                print(f"[cache-error] {d.name}: {e}", flush=True)
        try:
            results.append(analyze_skill(d))
        except Exception as e:
            print(f"[error] {d.name}: {e}", file=sys.stderr)
    build_index(results)
    print(f"Wrote reference index to {OUT_ROOT / 'SKILL_REFERENCE_INDEX.md'}")


if __name__ == "__main__":
    main()
