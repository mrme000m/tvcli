#!/usr/bin/env python3
"""yaml_edit — path-aware, comment-preserving editor for config.yaml.

The daemon reads config.yaml once at startup; the console edits individual
leaf values in place so every surrounding comment survives. Only what this
module can prove it understood is written — an unmatched path returns None
and the caller refuses (fail-closed, like the rest of grid-autonomy).

Scope: the subset of YAML config.yaml actually uses —
  * block mappings, 2-space indent per level
  * `key: value` leaves (int/float/bool/str, optional trailing comment)
  * one flow-mapping level: `key: { k: v, k: v, ... }` (portfolio.venues)
No block lists / anchors / multiline scalars are edited (lists like
`live_profiles` are deliberately out of reach).

API:
    get_value(text, "portfolio.total_usd") -> (value, ok)
    set_value(text, "watch.interval_s", 30) -> new_text | None
"""
from __future__ import annotations

import re

_FLOW_LINE = re.compile(r"^(\s*)([\w.-]+)\s*:\s*\{(.*)\}\s*(#.*)?$")
_LEAF_LINE = re.compile(r"^(\s*)([\w.-]+)\s*:\s*([^#]*?)(\s*#.*)?$")
_KEY_LINE = re.compile(r"^(\s*)([\w.-]+)\s*:(\s*(#.*)?)?$")


def _fmt(value) -> str:
    """Render a scalar the way config.yaml writes it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, int):
        return str(value)
    s = str(value)
    reserved = ("true", "false", "null", "yes", "no", "on", "off")
    if s == "" or re.search(r"[\s:{}\[\],#]", s) or s.lower() in reserved:
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


def _split_flow(inner: str) -> dict:
    """Parse `k: v, k: v` pairs (values here never nest braces)."""
    out = {}
    for part in inner.split(","):
        part = part.strip()
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _parse_scalar(raw: str):
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~", ""):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _walk(text: str, path: str):
    """Find the line `path` lives on. Yields at most one candidate:
    (line_index, kind, match) where kind is 'leaf' (a `key: value` line
    whose true path equals `path`) or 'flow' (a `parent: {…}` mapping line
    whose true path equals path[:-1] — the leaf is an inner scalar).

    Every container key pushes onto the stack regardless of relevance, so
    sibling containers can never shadow a leaf: `cur` is always a line's
    true path.
    """
    keys = tuple(k.strip() for k in path.split(".") if k.strip())
    if not keys:
        return
    lines = text.splitlines()
    parent = keys[:-1]
    stack = []  # [(indent, key)]
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        while stack and indent <= stack[-1][0]:
            stack.pop()
        cur = tuple(k for _, k in stack)
        m_key = _KEY_LINE.match(line)
        if m_key:  # bare `key:` container line — must win over the leaf
            stack.append((indent, m_key.group(2)))
            continue
        m_flow = _FLOW_LINE.match(line)
        if m_flow:
            if len(keys) >= 2 and cur + (m_flow.group(2),) == parent:
                yield i, "flow", m_flow
            continue
        m_leaf = _LEAF_LINE.match(line)
        if m_leaf:
            if cur + (m_leaf.group(2),) == keys:
                yield i, "leaf", m_leaf
            continue


def get_value(text: str, path: str):
    """Return (value, True) or (None, False) when the path is not a leaf."""
    for i, kind, m in _walk(text, path):
        if kind == "leaf":
            return _parse_scalar(m.group(3)), True
        inner = _split_flow(m.group(3))
        leaf = path.split(".")[-1].strip()
        if leaf in inner:
            return _parse_scalar(inner[leaf]), True
        return None, False
    return None, False


def set_value(text: str, path: str, value) -> str | None:
    """Return config text with `path` set to `value`, or None if unmatched.

    On a flow-mapping parent (e.g. `hyperliquid: { balance_usd: 300.0, … }`)
    only the named inner scalar is replaced; every other pair on the line,
    including the trailing comment and flow lists such as
    `grids: [Long, Short, Neutral]`, is preserved byte-for-byte.
    """
    for i, kind, m in _walk(text, path):
        lines = text.splitlines()
        if kind == "leaf":
            lines[i] = m.group(1) + m.group(2) + ": " + _fmt(value) \
                + (m.group(4) or "")
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
        leaf = path.split(".")[-1].strip()
        if leaf not in _split_flow(m.group(3)):
            return None
        # rebuild the flow body, replacing only the target scalar; all other
        # parts keep their original spelling (commas inside flow lists ride
        # along untouched inside their part strings)
        new_parts = []
        for part in m.group(3).split(","):
            p = part.strip()
            if not p or ":" not in p:
                new_parts.append(part)
                continue
            k = p.split(":", 1)[0].strip()
            if k == leaf:
                new_parts.append(f" {k}: {_fmt(value)}")
            else:
                new_parts.append(part)
        lines[i] = m.group(1) + m.group(2) + ": {" + ",".join(new_parts) \
            + " }" + (m.group(4) or "")
        return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return None
