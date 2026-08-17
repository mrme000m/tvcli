#!/usr/bin/env bash
# link-skills.sh — expose the canonical .agents/skills/ library to harnesses
# that do not natively scan it (e.g. Claude Code reads .claude/skills/).
#
# - Creates one symlink per skill (dirs containing SKILL.md) in .claude/skills/
# - Prunes stale symlinks whose target no longer exists
# - Idempotent; safe to run any time
#
# Usage: scripts/link-skills.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/.agents/skills"
DST="$ROOT/.claude/skills"

if [[ ! -d "$SRC" ]]; then
    echo "error: $SRC not found" >&2
    exit 1
fi

mkdir -p "$DST"

linked=0
for skill_dir in "$SRC"/*/; do
    name="$(basename "$skill_dir")"
    [[ -f "$skill_dir/SKILL.md" ]] || continue
    # ln -sfn cannot replace a real directory; remove mirror copies first
    if [[ -d "$DST/$name" && ! -L "$DST/$name" ]]; then
        rm -rf "$DST/$name"
        echo "replaced directory copy: .claude/skills/$name"
    fi
    ln -sfn "../../.agents/skills/$name" "$DST/$name"
    echo "linked: .claude/skills/$name -> .agents/skills/$name"
    linked=$((linked + 1))
done

pruned=0
for entry in "$DST"/*; do
    [[ -L "$entry" ]] || continue
    if [[ ! -e "$entry" ]]; then
        rm "$entry"
        echo "pruned stale symlink: $entry"
        pruned=$((pruned + 1))
    fi
done

echo "done: $linked skill(s) linked, $pruned stale symlink(s) pruned"
