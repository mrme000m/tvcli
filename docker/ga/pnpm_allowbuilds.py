#!/usr/bin/env python3
"""pnpm_allowbuilds.py — merge pnpm's demanded build-script allowlist keys
into a dsh profile's pnpm-workspace.yaml (image-build remedy for
ERR_PNPM_GIT_DEP_PREPARE_NOT_ALLOWED).

pnpm blocks git-hosted dependency build scripts and prints the exact
`name@<tarball-url-with-sha>` keys it wants; those keys change with every
plugin release, so they are PARSED from the failed install log rather than
hardcoded. Two pnpm generations print (and honor) two formats, both are
produced:

  pnpm 10.x:  onlyBuiltDependencies:        (list form)
                - "name@https://codeload…/<sha>"
  pnpm >= 11: allowBuilds:                  (map form — the prime_stack
                "name@https://codeload…/<sha>": true     plugin stage's
                                                     verified format)

Extends the stage's pure functions (bootstrapping/python/prime_stack/
stages/plugin.py — parse_allowbuilds_keys / merge_allowbuilds_lines) with
the pnpm 10.x list format observed in CI runs 34169182646 + 34169913735.
Quoted YAML keys are required (the key contains ':' and '/').

Usage: pnpm_allowbuilds.py PROFILE_DIR LOG_FILE
Exit 0 when at least one key was merged (caller retries the install),
exit 1 when no key could be parsed (caller fails the build).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

WORKSPACE_BASE = "packages:\n  - .\nnodeLinker: hoisted\n"

_KEY_RE = r"[^\s\"']+@https?://[^\s\"']+"


def parse_allowbuilds_keys(log_text: str) -> list:
    """Extract the allowlist keys pnpm demands from a failure log.

    Format A (pnpm >= 11): an `allowBuilds:` block whose entries end in
    `: true` (mirrors the prime_stack stage: sed -n '/^allowBuilds:/,$p'
    | grep -E ': true$').
    Format B (pnpm 10.x): an `onlyBuiltDependencies:` block of
    `- "name@https://…"` list items.
    """
    keys: list = []
    lines = log_text.splitlines()

    # Format A — allowBuilds map entries
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("allowBuilds:"))
        for line in lines[start + 1:]:
            m = re.match(r'^\s+["\']?(.+?)["\']?:\s+true\s*$', line)
            if not m:
                break
            keys.append(m.group(1))
    except StopIteration:
        pass

    # Format B — onlyBuiltDependencies list items (any quoted name@url
    # list item in the log; the heading line itself has no URL)
    for line in lines:
        m = re.match(r'^\s+-\s+["\']?(' + _KEY_RE + r')["\']?\s*$', line)
        if m:
            keys.append(m.group(1))

    # de-dup, stable order
    return [k for k in dict.fromkeys(keys)]


def _merge_section(lines: list, section: str, render, present) -> tuple:
    """Append missing keys under `section` (created when absent).

    render(key) -> the line to insert; present(lines, key) -> bool.
    Returns (lines, changed).
    """
    changed = False
    keys_to_add = [k for k in render.pending if not present(lines, k)]
    if not keys_to_add:
        return lines, False
    idx = next((i for i, l in enumerate(lines) if l.startswith(section)), None)
    if idx is None:
        lines += [section] + [render(k) for k in keys_to_add]
    else:
        lines[idx + 1:idx + 1] = [render(k) for k in keys_to_add]
    return lines, True


def merge_allowbuilds_lines(existing_text, keys) -> tuple:
    """Merge keys into BOTH the allowBuilds map (pnpm >= 11) and the
    onlyBuiltDependencies list (pnpm 10.x) in pnpm-workspace.yaml text.

    `existing_text` may be None (file absent → base workspace scaffold is
    created). Returns (new_text, changed). Keys already present (in any
    quoting style) are left untouched.
    """
    keys = [k for k in dict.fromkeys(keys)]
    if not keys:
        return existing_text, False
    lines = (existing_text if existing_text is not None else WORKSPACE_BASE).splitlines()
    changed = False

    def _present_map(lines, key):
        pat = re.compile(r"^\s*[\"']?" + re.escape(key) + r"[\"']?\s*:\s*true\s*$")
        return any(pat.match(l) for l in lines)

    def _present_list(lines, key):
        pat = re.compile(r"^\s*-\s*[\"']?" + re.escape(key) + r"[\"']?\s*$")
        return any(pat.match(l) for l in lines)

    class _MapRender:
        pending = keys
        def __call__(self, key):
            return f'  "{key}": true'

    class _ListRender:
        pending = keys
        def __call__(self, key):
            return f'  - "{key}"'

    lines, c1 = _merge_section(lines, "allowBuilds:", _MapRender(), _present_map)
    lines, c2 = _merge_section(lines, "onlyBuiltDependencies:", _ListRender(), _present_list)
    changed = c1 or c2
    if not changed:
        return existing_text, False
    return "\n".join(lines) + "\n", True


def main(argv: list) -> int:
    profile_dir = Path(argv[1])
    log_text = Path(argv[2]).read_text(errors="replace")
    keys = parse_allowbuilds_keys(log_text)
    if not keys:
        print("pnpm_allowbuilds: no allowlist keys found in the install log", file=sys.stderr)
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
