#!/usr/bin/env python3
"""
Re-extract the real agent payload from skill-analysis/dumps/<skill>/stdout.txt.

The analyze_skills.py parse_payload heuristic picked the wrong JSON block for
some skills (it grabbed the first `{...}` which is often the input-echo config).
This script scans stdout.txt for ALL balanced JSON blocks, scores each by the
presence of agent envelope keys (agentContext, execution, status, schemaVersion,
narrative, opportunities, conformance, market, structure), and overwrites
payload.json with the best-scoring block.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DUMPS = Path("/Volumes/ExMac/code/tradingview/go/skill-analysis/dumps")
ENVELOPE_KEYS = {"agentContext","execution","status","schemaVersion",
                 "narrative","opportunities","conformance","market",
                 "structure","signals","counts","summary","mtf","clusters",
                 "trend","zones","labels","bias","grades","tradePlan",
                 "tqiBreakdown","regime","signals"}


def candidate_blocks(stdout: str):
    """Yield (start, end, text) for every balanced top-level {...} block."""
    depth = 0
    start = -1
    in_str = None
    escaped = False
    for i, ch in enumerate(stdout):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_str:
            escaped = True
            continue
        if in_str:
            if ch == in_str:
                in_str = None
            continue
        if ch in ('"', "'", "`"):
            in_str = ch
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                yield start, i + 1, stdout[start:i+1]
                start = -1


def score_block(block_text: str):
    try:
        obj = json.loads(block_text)
    except Exception:
        return None, -1
    if not isinstance(obj, dict):
        return None, -1
    score = 0
    for k in obj.keys():
        if k in ENVELOPE_KEYS:
            score += 2
        if obj.get("status") == "ok":
            score += 3
        if isinstance(obj.get("agentContext"), dict):
            score += 3
        if isinstance(obj.get("execution"), dict):
            score += 1
    # also reward larger payloads (more keys = real output)
    score += min(len(obj), 20) * 0.1
    return obj, score


def reextract(skill_dir: Path):
    stdout_path = skill_dir / "stdout.txt"
    if not stdout_path.exists():
        return None
    stdout = stdout_path.read_text(encoding="utf-8", errors="ignore")
    best_obj, best_score, best_idx = None, -1, -1
    for idx, (s, e, txt) in enumerate(candidate_blocks(stdout)):
        obj, score = score_block(txt)
        if obj is not None and score > best_score:
            best_obj, best_score, best_idx = obj, score, idx
    return best_obj, best_score, best_idx


def main():
    fixed = []
    for d in sorted(DUMPS.iterdir()):
        if not d.is_dir():
            continue
        payload_path = d / "payload.json"
        existing = None
        if payload_path.exists():
            try:
                existing = json.loads(payload_path.read_text())
            except Exception:
                existing = None
        existing_has_ac = isinstance(existing, dict) and isinstance(existing.get("agentContext"), dict)
        if existing_has_ac and existing.get("status") == "ok":
            # already a real payload, skip (but verify no better block exists)
            pass

        obj, score, idx = reextract(d)
        if obj is None:
            print(f"{d.name:36s} (no candidate)")
            continue
        if existing is not None:
            # Compare
            old_keys = len(existing) if isinstance(existing, dict) else 0
            new_keys = len(obj)
            if existing_has_ac and existing.get("status") == "ok":
                # was fine — but check if re-extract found something better
                if score > 10 and new_keys > old_keys:
                    payload_path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
                    fixed.append((d.name, f"replaced (old keys={old_keys}, new keys={new_keys})"))
                    print(f"{d.name:36s} REPLACED (old keys={old_keys} new keys={new_keys} score={score})")
                else:
                    print(f"{d.name:36s} kept existing (keys={old_keys})")
            else:
                # was broken — replace with re-extracted
                payload_path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
                fixed.append((d.name, f"fixed (new keys={new_keys})"))
                print(f"{d.name:36s} FIXED (new keys={new_keys} score={score})")
        else:
            payload_path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
            fixed.append((d.name, f"created (keys={new_keys})"))
            print(f"{d.name:36s} CREATED (keys={new_keys} score={score})")

    print(f"\n{len(fixed)} files updated.")
    return fixed


if __name__ == "__main__":
    main()
