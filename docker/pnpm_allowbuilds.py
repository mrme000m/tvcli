#!/usr/bin/env python3
"""pnpm_allowbuilds.py — merge pnpm's demanded allowBuilds keys into a dsh
profile's pnpm-workspace.yaml (image-build remedy for
ERR_PNPM_GIT_DEP_PREPARE_NOT_ALLOWED).

Self-contained port of the verified, unit-tested pure functions in
bootstrapping/python/prime_stack/stages/plugin.py (parse_allowbuilds_keys +
merge_allowbuilds_lines) — pnpm blocks git-hosted dependency build scripts
and prints the exact `name@<tarball-url-with-sha>` keys it wants; those keys
change with every plugin release, so they are PARSED from the failed install
log rather than hardcoded. Quoted YAML keys are required (the key contains
':' and '/').

Usage: pnpm_allowbuilds.py PROFILE_DIR LOG_FILE
Exit 0 when at least one key was merged (caller retries the install),
exit 1 when no key could be parsed (caller fails the build).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

WORKSPACE_BASE = "packages:\n  - .\nnodeLinker: hoisted\n"


def parse_allowbuilds_keys(log_text: str) -> list:
    """Extract the allowBuilds keys pnpm demands from a failure log.

    Mirrors: sed -n '/^allowBuilds:/,$p' | grep -E ': true$'
    """
    lines = log_text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("allowBuilds:"))
    except StopIteration:
        return []
    keys = []
    for line in lines[start + 1:]:
        m = re.match(r'^\s+["\']?(.+?)["\']?:\s+true\s*$', line)
        if not m:
            break
        keys.append(m.group(1))
    return keys


def _key_present(lines: list, key: str) -> bool:
    pattern = re.compile(r"^\s*[\"']?" + re.escape(key) + r"[\"']?\s*:\s*true\s*$")
    return any(pattern.match(l) for l in lines)


def merge_allowbuilds_lines(existing_text, keys) -> tuple:
    """Merge allowBuilds keys into pnpm-workspace.yaml text.

    `existing_text` may be None (file absent → base workspace scaffold is
    created). Returns (new_text, changed). Keys already present (in any
    quoting style) are left untouched.
    """
    keys = [k for k in dict.fromkeys(keys)]
    if not keys:
        return existing_text, False
    lines = (existing_text if existing_text is not None else WORKSPACE_BASE).splitlines()
    changed = False
    for key in keys:
        if _key_present(lines, key):
            continue
        idx = next((i for i, l in enumerate(lines) if l.startswith("allowBuilds:")), None)
        if idx is None:
            lines += ["allowBuilds:", f'  "{key}": true']
        else:
            lines.insert(idx + 1, f'  "{key}": true')
        changed = True
    if not changed:
        return existing_text, False
    return "\n".join(lines) + "\n", True


def main(argv: list) -> int:
    profile_dir = Path(argv[1])
    log_text = Path(argv[2]).read_text(errors="replace")
    keys = parse_allowbuilds_keys(log_text)
    if not keys:
        print("pnpm_allowbuilds: no allowBuilds keys found in the install log", file=sys.stderr)
        return 1
    workspace = profile_dir / "pnpm-workspace.yaml"
    existing = workspace.read_text() if workspace.is_file() else None
    new_text, changed = merge_allowbuilds_lines(existing, keys)
    if changed:
        workspace.write_text(new_text)
    for key in keys:
        print(f"pnpm_allowbuilds: allowed {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
