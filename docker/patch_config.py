#!/usr/bin/env python3
"""Idempotent grid-autonomy config.yaml patcher for the Docker image.

Rewrites watch.browser_launch_cmd / watch.wt_restore_cmd when they reference
a path that does not exist inside the container (e.g. the macOS dev values
"node /Volumes/ExMac/.../launch.mjs") to the in-image locations:

    watch.browser_launch_cmd: "node /app/browser-debug/launch.mjs"
    watch.wt_restore_cmd:     "node /app/browser-debug/wt.mjs"

- stdlib-only, no PyYAML: regex line surgery preserving indentation + any
  trailing comment.
- Backs up to <config>.bak ONLY on the first change (the .bak is never
  overwritten by later runs).
- No-op (exit 0, no output) when the file is already correct or the keys
  are absent.

Usage: patch_config.py [path/to/config.yaml]
       (default: agents/grid-autonomy/config.yaml relative to /app or CWD)
"""
import os
import re
import sys

CONTAINER_CMD = {
    "browser_launch_cmd": "node /app/browser-debug/launch.mjs",
    "wt_restore_cmd": "node /app/browser-debug/wt.mjs",
}


def _referenced_path_missing(value: str) -> bool:
    """True when the command references a filesystem path that is absent here.

    Extracts the first token that looks like an existing-style absolute or
    repo-relative path (contains a '/' and ends in a known script-ish suffix)
    and stats it. A plain "node /app/browser-debug/launch.mjs" on a host where
    that path exists is left alone — that is exactly the already-correct case.
    """
    for tok in value.split():
        if "/" not in tok:
            continue
        if tok.startswith("node"):
            continue
        if not re.search(r"\.(mjs|js|py|sh)$", tok):
            continue
        return not os.path.exists(tok)
    return False


def patch(path: str) -> bool:
    """Patch in place; return True if the file was modified."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines(keepends=True)
    except FileNotFoundError:
        # Nothing to patch — not an error for our caller.
        return False

    changed = False
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)(browser_launch_cmd|wt_restore_cmd):(\s*)\"([^\"]*)\"(.*)$",
                     line.rstrip("\n"))
        if not m:
            continue
        key, value = m.group(2), m.group(4)
        if value == CONTAINER_CMD[key]:
            continue  # already correct
        if not _referenced_path_missing(value):
            continue  # points at something that exists here — leave it alone
        newline = (f"{m.group(1)}{key}:{m.group(3)}\"{CONTAINER_CMD[key]}\"{m.group(5)}\n")
        if not changed:
            # First change: back up the pristine file (never overwrite the .bak).
            bak = path + ".bak"
            if not os.path.exists(bak):
                with open(bak, "w", encoding="utf-8") as f:
                    f.writelines(lines)
        lines[i] = newline
        changed = True

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        print(f"patch_config: rewrote browser_launch_cmd/wt_restore_cmd in {path} "
              f"(backup: {path}.bak)")
    return changed


def main() -> int:
    default = "agents/grid-autonomy/config.yaml"
    path = sys.argv[1] if len(sys.argv) > 1 else default
    if not os.path.isabs(path) and not os.path.exists(path) and os.path.exists(os.path.join("/app", path)):
        path = os.path.join("/app", path)
    patch(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
