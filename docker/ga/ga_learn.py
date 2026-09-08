#!/usr/bin/env python3
"""ga_learn.py — the GA agent's learnings-ledger journaling tool.

Writes distilled session learnings into docker/ga/learnings/ledger.md
(reverse-chronological; newest first). Pure, local, python3-stdlib-only:

  - NO network access, NO secrets, NO git operations (no auto-commit/push —
    committing and pushing stay subject to the existing push guardrail: no
    push to main without explicit human confirmation).
  - Writes ONLY inside docker/ga/learnings/ (the ledger file itself).

The ledger path is resolved as:
  1. $GA_REPO (default /srv/tvcli) + docker/ga/learnings/ledger.md
  2. otherwise, walking up from this script's own location to the nearest
     repo root containing docker/ga/learnings/

Usage:
  ga_learn.py add --title "Short imperative title" --body "What was learned" \
             [--changes "docker/ga/foo.py,docker/ga/bar.sh"]
  ga_learn.py tail [--count N]
"""

import argparse
import datetime
import os
import sys

DEFAULT_REPO = "/srv/tvcli"
LEDGER_REL = os.path.join("docker", "ga", "learnings")
LEDGER_NAME = "ledger.md"

USAGE = __doc__.strip() + "\n"


def _find_repo_root_from_script():
    """Walk upward from this script's dir to find a repo root that contains
    docker/ga/learnings/."""
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(d, LEDGER_REL)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def ledger_path():
    """Locate the ledger. Raises SystemExit with a loud error if not found."""
    candidates = []

    repo = os.environ.get("GA_REPO", DEFAULT_REPO)
    candidates.append(os.path.join(repo, LEDGER_REL))

    script_root = _find_repo_root_from_script()
    if script_root:
        candidates.append(os.path.join(script_root, LEDGER_REL))

    for c in candidates:
        if os.path.isdir(c):
            return os.path.join(c, LEDGER_NAME)

    sys.stderr.write(
        "ga_learn.py: ERROR — cannot locate %s/\n"
        "  tried: %s\n"
        "  set GA_REPO to the repo root (a checkout of the tvcli repo),\n"
        "  or run the script from inside that repo so it can self-locate.\n"
        % (LEDGER_REL, "; ".join(candidates))
    )
    raise SystemExit(2)


def parse_entries(text):
    """Split ledger text into (header, [entry, ...]). An entry starts at a
    '## ' heading line and runs to the next one (or EOF)."""
    lines = text.split("\n")
    header_end = None
    starts = [i for i, l in enumerate(lines) if l.startswith("## ")]
    if starts:
        header_end = starts[0]
        header = "\n".join(lines[:header_end]).rstrip("\n")
        entries = ["\n".join(lines[i:j]).rstrip("\n")
                   for i, j in zip(starts, starts[1:] + [len(lines)])]
    else:
        header = text.rstrip("\n")
        entries = []
    return header, entries


def format_entry(title, body, changes, date=None):
    date = date or datetime.date.today().isoformat()
    out = ["## %s — %s" % (date, title.strip()), ""]
    for para in (body or "").strip().split("\n\n"):
        para = para.strip()
        if para:
            out.append(para)
            out.append("")
    if changes:
        out.append("Changes:")
        for c in changes:
            c = c.strip()
            if c:
                out.append("- %s" % c)
        out.append("")
    return "\n".join(out)


def cmd_add(args):
    path = ledger_path()
    if not args.title.strip() or not args.body.strip():
        sys.stderr.write("ga_learn.py: ERROR — --title and --body are required and must be non-empty.\n")
        raise SystemExit(2)
    changes = [c.strip() for c in args.changes.split(",") if c.strip()] if args.changes else []

    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    header, entries = parse_entries(text)
    if not text:
        header = ("# GA learnings ledger\n\nDistilled knowledge from operating "
                 "and improving the GA stack. Reverse-chronological — newest "
                 "first. The loop contract, the entry format, and the write "
                 "rules live in [README.md](README.md).\n")

    entry = format_entry(args.title, args.body, changes)
    new_text = header.rstrip("\n") + "\n\n" + "\n\n".join([entry] + entries)
    if not new_text.endswith("\n"):
        new_text += "\n"

    # Safety: this tool only ever writes the ledger file itself — never
    # anything outside a docker/ga/learnings/ directory.
    if not os.path.normpath(path).endswith(
            os.path.join(LEDGER_REL, LEDGER_NAME)):
        sys.stderr.write("ga_learn.py: ERROR — refusing to write outside %s.\n" % LEDGER_REL)
        raise SystemExit(2)

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)
    print("Wrote learning to %s (%d entries total)" % (path, len(entries) + 1))
    print("No git operations performed — commit and (only with explicit human "
          "confirmation) push separately.")


def cmd_tail(args):
    path = ledger_path()
    if not os.path.exists(path):
        sys.stderr.write("ga_learn.py: ERROR — ledger not found at %s\n" % path)
        raise SystemExit(2)
    _, entries = parse_entries(open(path, encoding="utf-8").read())
    n = args.count if args.count > 0 else 0
    for e in entries[:n]:
        print(e)
        print()


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="ga_learn.py",
        description="Journal distilled learnings into docker/ga/learnings/ledger.md.",
        epilog="No git operations: committing/pushing is a separate, human-guarded step.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="command")

    p_add = sub.add_parser("add", help="prepend a new learning entry to the ledger")
    p_add.add_argument("--title", required=True, help="short imperative title")
    p_add.add_argument("--body", required=True, help="the verified knowledge: cause, effect, consequence")
    p_add.add_argument("--changes", default=None,
                       help="comma-separated list of files changed because of this learning")
    p_add.set_defaults(func=cmd_add)

    p_tail = sub.add_parser("tail", help="print the most recent ledger entries")
    p_tail.add_argument("--count", type=int, default=5, help="number of entries (default 5)")
    p_tail.set_defaults(func=cmd_tail)

    args = ap.parse_args(argv)
    if getattr(args, "func", None) is None:
        print(USAGE)
        return 0
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
