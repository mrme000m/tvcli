#!/usr/bin/env python3
"""config_lite — stdlib-only YAML-subset loader (extracted from daemon.py).

Parses exactly the YAML subset this repo's config.yaml uses: nested maps,
block/flow sequences, inline scalars, quoted strings, and `#` comments
(string-quote aware). No external dependency and no daemon coupling, so
`load_config` and tests can use it freely.

Public API:
    load_yaml(text) -> dict | list
    deep_merge(base, override) -> dict   (recursive, override wins)
"""

def _strip_yaml_comment(line):
    in_s = in_d = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d:
            return line[:i]
    return line


def _yaml_split_kv(s):
    depth = 0
    in_s = in_d = False
    for i, ch in enumerate(s):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == ":" and depth == 0 and not in_s and not in_d:
            return s[:i].strip(), s[i + 1:].strip()
    return s.strip(), ""


def _yaml_split_flow(s):
    parts = []
    depth = 0
    in_s = in_d = False
    cur = ""
    for ch in s:
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif not in_s and not in_d:
            if ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(cur.strip())
                cur = ""
                continue
        cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _yaml_scalar(s):
    s = s.strip()
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if s in ("null", "Null", "NULL", "~", ""):
        return None
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    if s.startswith("["):
        inner = s[1:-1].strip()
        return [_yaml_scalar(t) for t in _yaml_split_flow(inner)] if inner else []
    if s.startswith("{"):
        inner = s[1:-1].strip()
        out = {}
        if inner:
            for tok in _yaml_split_flow(inner):
                k, v = _yaml_split_kv(tok)
                out[_yaml_scalar(k)] = _yaml_scalar(v)
        return out
    try:
        if "." in s or "e" in s.lower():
            return float(s)
        return int(s)
    except ValueError:
        return s


def _yaml_block(lines, pos, indent):
    content = lines[pos][1]
    if content.startswith("- "):
        seq = []
        i = pos
        while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
            item = lines[i][1][2:].strip()
            i += 1
            if item == "" and i < len(lines) and lines[i][0] > indent:
                val, i = _yaml_block(lines, i, lines[i][0])
                seq.append(val)
            else:
                seq.append(_yaml_scalar(item))
        return seq, i
    out = {}
    i = pos
    while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("- "):
        key, rest = _yaml_split_kv(lines[i][1])
        i += 1
        if rest == "":
            if i < len(lines) and lines[i][0] > indent:
                val, i = _yaml_block(lines, i, lines[i][0])
            else:
                val = None
        else:
            val = _yaml_scalar(rest)
        out[key] = val
    return out, i


def load_yaml(text):
    """Parse the YAML subset used by config.yaml → nested dict/list."""
    lines = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        stripped = _strip_yaml_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((indent, stripped.strip()))
    if not lines:
        return {}
    value, _ = _yaml_block(lines, 0, lines[0][0])
    return value or {}


def deep_merge(base, override):
    """Recursive dict merge: `override` wins on conflicts."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out
