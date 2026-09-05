#!/usr/bin/env python3
"""Grid-autonomy reflection layer — decision journal, memory retrieval, run cards.

Implements the TradingAgents "reflection on past decisions" loop plus
vibetrading-style run cards:

  record_decision(ticket, brief, action, payloads=None) -> decision_id
      append one JSON line to state/decisions.jsonl
  record_outcome(decision_id, final)
      attach a closing outcome to the matching line (atomic replace)
  memories_for(brief, k=3) -> list[dict]
      rank past decisions WITH an outcome: exact symbol+venue first, then
      venue+regime-family, then most recent; return compact memory dicts
  write_run_card(cycle_report) -> path
      write state/reports/<UTC-ts>-<kind>.json and a human-readable .md

Stdlib only, no network, no secrets. `payloads` are never stored verbatim:
only an md5 digest plus small key fields go into the journal line.

State directory resolution (read at CALL time, not import time):
  GRID_STATE_DIR env var, else <repo>/agents/grid-autonomy/state.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
# Make `from reflect import ...` work when the agents/ dir is on sys.path
# (mirrors swarm.py). This module itself has no package dependencies.
sys.path.insert(0, HERE)

DECISIONS = "decisions.jsonl"
REPORTS_DIR = "reports"

# chop/squeeze/neutral harvest ranges; trend_up/trend_down harvest direction.
_RANGE_FAMILY = {"chop_high_volatility", "squeeze", "neutral"}
_TREND_FAMILY = {"trend_up", "trend_down"}

# ── PocketBase write-through (optional, non-fatal) ────────────────────
# The decisions.jsonl file is the system of record; this mirrors each decision
# and outcome into the PocketBase side channel when it is configured and up.
try:
    from pbclient import PB as _PB  # noqa: F401
    _HAS_PB = True
except Exception:
    _HAS_PB = False
    _PB = None

_pb = None


def _pb_mirror():
    global _pb
    # GRID_STATE_DIR set (test isolation) => never touch the live side
    # channel, even when ambient PB_URL/PB_TOKEN env is present.
    if os.environ.get("GRID_STATE_DIR"):
        return None
    if not _HAS_PB or _PB is None:
        return None
    if _pb is None:
        try:
            _pb = _PB()
        except Exception:
            _pb = False
    return _pb or None


def _state_dir():
    """Effective state dir — env override wins, else the repo default."""
    env = os.environ.get("GRID_STATE_DIR")
    if env:
        return env
    # reflect.py lives in agents/ → ../state == agents/grid-autonomy/state
    return os.path.normpath(os.path.join(HERE, "..", "state"))


def _decisions_path():
    return os.path.join(_state_dir(), DECISIONS)


def _utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append_json_line(path, obj):
    """Append one JSON line. flock when available; otherwise rely on the
    documented single-process assumption (the daemon runs cycles
    single-threaded, and O_APPEND single-write lines are atomic in practice)."""
    line = (json.dumps(obj, sort_keys=True) + "\n").encode()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    locked = False
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            locked = True
        except Exception:
            pass  # no fcntl/flock support: rely on documented single writer
        os.write(fd, line)
    finally:
        if locked:
            try:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
            except Exception:
                pass
        os.close(fd)


def _next_decision_id(path):
    """Monotonic per-UTC-day id: dYYYYMMDD-NNN."""
    prefix = "d" + datetime.now(timezone.utc).strftime("%Y%m%d") + "-"
    max_n = 0
    if os.path.isfile(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                ident = obj.get("id") if isinstance(obj, dict) else None
                if isinstance(ident, str) and ident.startswith(prefix):
                    try:
                        max_n = max(max_n, int(ident.split("-", 1)[1]))
                    except Exception:
                        pass
    return f"{prefix}{max_n + 1:03d}"


def _get(d, key, default=None):
    return d.get(key, default) if isinstance(d, dict) else default


def _payload_digest(payloads):
    if payloads is None:
        return None
    try:
        raw = json.dumps(payloads, sort_keys=True, default=str)
    except Exception:
        raw = repr(payloads)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _channel_from(payloads, brief):
    if isinstance(payloads, dict):
        gb = payloads.get("grid_bot")
        if isinstance(gb, dict) and gb.get("channel"):
            return gb["channel"]
    return _get(brief, "channel")


def _risk_multipliers(ticket):
    if not isinstance(ticket, dict):
        return {}
    out = {}
    if "max_alloc_mult" in ticket:
        out["max_alloc_mult"] = ticket.get("max_alloc_mult")
    if "step_mult" in ticket:
        out["step_mult"] = ticket.get("step_mult")
    risk = ticket.get("risk")
    if isinstance(risk, dict) and risk:
        stances = {}
        for stance, r in risk.items():
            if isinstance(r, dict):
                stances[stance] = {"max_alloc_mult": r.get("max_alloc_mult"),
                                   "step_mult": r.get("step_mult")}
        if stances:
            out["stances"] = stances
    return out


def record_decision(ticket, brief, action, payloads=None):
    """Append one decision line; return its decision_id."""
    path = _decisions_path()
    decision_id = _next_decision_id(path)
    if not isinstance(ticket, dict):
        ticket = {}
    if not isinstance(brief, dict):
        brief = {}
    slot = brief.get("slot")
    if isinstance(slot, dict):
        slot = slot.get("slot")
    step_pct = brief.get("step")
    if step_pct is None and isinstance(payloads, dict):
        gb = payloads.get("grid_bot")
        if isinstance(gb, dict):
            step_pct = gb.get("profit_per_grid_pct")
    stagnation = _get(brief, "stagnation_policy")
    if stagnation is None and isinstance(payloads, dict):
        stagnation = payloads.get("stagnation_policy")
    line = {
        "id": decision_id,
        "at": _utcnow(),
        "symbol": ticket.get("symbol", brief.get("symbol")),
        "venue": ticket.get("venue", brief.get("venue")),
        "regime": ticket.get("regime", brief.get("regime")),
        "grid_type": ticket.get("grid_type"),
        "decision": ticket.get("decision"),
        "score_final": brief.get("score_final"),
        "step_pct": step_pct,
        "slot": slot,
        "action": action,
        "rationale": ticket.get("rationale"),
        "risk_multipliers": _risk_multipliers(ticket),
        "llm_degraded": ticket.get("llm_degraded", False),
        "stagnation_policy": stagnation,
        "channel": _channel_from(payloads, brief),
        "payload_digest": _payload_digest(payloads),
    }
    _append_json_line(path, line)
    _pb = _pb_mirror()
    if _pb is not None:
        try:
            _pb.decision(line)
        except Exception:
            pass
    return decision_id


def record_outcome(decision_id, final):
    """Attach `final` as line["outcome"] via read-modify-write (atomic replace).

    final: {closed_at, reason, realized_pnl, fills, holding_h, observed}.
    Raises KeyError when decision_id is not in the journal.
    """
    path = _decisions_path()
    if not os.path.isfile(path):
        raise KeyError(decision_id)
    final = dict(final or {})
    if not final.get("closed_at"):
        final["closed_at"] = _utcnow()
    found = False
    lines = []
    with open(path) as f:
        for line in f:
            stripped = line.rstrip("\n")
            if not stripped.strip():
                lines.append(stripped)
                continue
            try:
                obj = json.loads(stripped)
            except Exception:
                lines.append(stripped)
                continue
            if obj.get("id") == decision_id:
                obj = dict(obj)
                obj["outcome"] = final
                found = True
            lines.append(json.dumps(obj, sort_keys=True))
    if not found:
        raise KeyError(decision_id)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines))
        if lines:
            f.write("\n")
    os.replace(tmp, path)
    _pb = _pb_mirror()
    if _pb is not None:
        try:
            _pb.decision_outcome(decision_id, final)
        except Exception:
            pass


def _regime_family(regime):
    if regime in _RANGE_FAMILY:
        return "range"
    if regime in _TREND_FAMILY:
        return "trend"
    return None


def _norm(s):
    return str(s or "").strip().casefold()


def _ts_key(value):
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except Exception:
        return 0.0


def _compact(mem):
    outcome = mem.get("outcome") if isinstance(mem.get("outcome"), dict) else {}
    return {
        "symbol": mem.get("symbol"),
        "venue": mem.get("venue"),
        "regime": mem.get("regime"),
        "grid_type": mem.get("grid_type"),
        "action": mem.get("action"),
        "outcome_pnl": outcome.get("realized_pnl"),
        "reason": outcome.get("reason"),
        "holding_h": outcome.get("holding_h"),
        "at": mem.get("at"),
        "llm_degraded": mem.get("llm_degraded"),
    }


def memories_for(brief, k=3):
    """Scan the journal; return up to k compact outcome memories.

    Rank: (0) exact symbol+venue, (1) same venue + regime family,
    (2) everything else with an outcome. Each tier is ordered by most recent
    close. Decisions without an outcome are never returned.
    """
    path = _decisions_path()
    if not os.path.isfile(path):
        return []
    symbol = _norm(_get(brief, "symbol"))
    venue = _norm(_get(brief, "venue"))
    family = _regime_family(_get(brief, "regime"))
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict) or not obj.get("outcome"):
                continue
            tier = 2
            if symbol and _norm(obj.get("symbol")) == symbol and \
                    venue and _norm(obj.get("venue")) == venue:
                tier = 0
            elif venue and _norm(obj.get("venue")) == venue and \
                    family and _regime_family(obj.get("regime")) == family:
                tier = 1
            outcome = obj.get("outcome") if isinstance(obj.get("outcome"), dict) else {}
            recent = max(_ts_key(outcome.get("closed_at")), _ts_key(obj.get("at")))
            rows.append((tier, -recent, _compact(obj)))
    rows.sort(key=lambda r: (r[0], r[1]))
    return [r[2] for r in rows[:max(int(k), 0)]]


# ── run cards ─────────────────────────────────────────────────────────
def _safe_component(text):
    out = []
    for ch in str(text or ""):
        out.append(ch if ch.isalnum() or ch in "-_" else "-")
    return "".join(out).strip("-") or "cycle"


def _stamp(cycle_report):
    at = _get(cycle_report, "at")
    if at:
        try:
            dt = datetime.fromisoformat(str(at))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        except Exception:
            return _safe_component(at)
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _md_cell(v):
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\n", " ")


def _md_table(headers, rows):
    if not rows:
        return "| " + " | ".join(_md_cell(h) for h in headers) + " |\n" + \
               "| " + " | ".join("---" for _ in headers) + " |\n"
    lines = ["| " + " | ".join(_md_cell(h) for h in headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_md_cell(v) for v in row) + " |")
    return "\n".join(lines) + "\n"


def _md_table_any(headers, rows):
    """Always return a real markdown table (placeholder row when empty)."""
    return _md_table(headers, rows if rows else [["—"] * len(headers)])


def _render_route(rep):
    screen = rep.get("screen") if isinstance(rep.get("screen"), dict) else {}
    rows = [["cycle_kind", _get(rep, "cycle_kind", "cycle")],
            ["at", _get(rep, "at", _utcnow())],
            ["candidates_screened", _get(screen, "n_candidates", "n/a")]]
    return "## Route\n" + _md_table_any(["field", "value"], rows)


def _render_ground(rep):
    screen = rep.get("screen") if isinstance(rep.get("screen"), dict) else {}
    top3 = screen.get("top3") or []
    rows = []
    for i, c in enumerate(top3, 1):
        if not isinstance(c, dict):
            continue
        rows.append([i, _get(c, "venue"), _get(c, "symbol"), _get(c, "regime"),
                     _get(c, "score_final"), _get(c, "step", _get(c, "step_pct"))])
    md = "## Ground\n"
    md += f"- candidates: {_get(screen, 'n_candidates', 'n/a')}\n"
    md += _md_table_any(["#", "venue", "symbol", "regime", "score_final",
                         "step_pct"], rows)
    return md


def _render_deliberate(rep):
    rows = []
    for d in rep.get("deliberations") or []:
        if not isinstance(d, dict):
            continue
        rows.append([_get(d, "symbol"), _get(d, "decision"),
                     _get(d, "confidence"), bool(d.get("llm_degraded")),
                     bool(d.get("veto"))])
    return "## Deliberate\n" + _md_table_any(
        ["symbol", "decision", "confidence", "llm_degraded", "veto"], rows)


def _render_guard(rep):
    rows = []
    for g in rep.get("guard") or []:
        if not isinstance(g, dict):
            continue
        rows.append([_get(g, "symbol"), bool(g.get("ok")),
                     "; ".join(g.get("violations") or [])])
    return "## Guard\n" + _md_table_any(["symbol", "ok", "violations"], rows)


def _render_deploy(rep):
    md = "## Deploy\n"
    rows = []
    for d in rep.get("deployments") or []:
        if not isinstance(d, dict):
            continue
        rows.append([_get(d, "slot"), _get(d, "symbol"), _get(d, "venue"),
                     _get(d, "grid_type"), _get(d, "step_pct"),
                     _get(d, "amount"), _get(d, "multiplier")])
    md += "### Deployments\n" + _md_table_any(
        ["slot", "symbol", "venue", "grid_type", "step_pct", "amount",
         "multiplier"], rows)
    rows = []
    for r in rep.get("rotations") or []:
        if not isinstance(r, dict):
            continue
        rows.append([_get(r, "slot"), _get(r, "from"), _get(r, "to"),
                     "; ".join(r.get("reasons") or [])])
    md += "### Rotations\n" + _md_table_any(
        ["slot", "from", "to", "reasons"], rows)
    return md


def _render_observe(rep):
    rows = []
    obs = rep.get("observed")
    if isinstance(obs, dict):
        for slot, o in obs.items():
            if not isinstance(o, dict):
                o = {}
            rows.append([slot, _get(o, "fills_24h"), _get(o, "realized_ratio"),
                         _get(o, "dd_vs_atr_band")])
    return "## Observe\n" + _md_table_any(
        ["slot", "fills_24h", "realized_ratio", "dd_vs_atr_band"], rows)


def _render_reflect(rep):
    rel = rep.get("reliability")
    rows = []
    if isinstance(rel, dict) and rel:
        rows = [[k, rel[k]] for k in sorted(str(k) for k in rel)]
    else:
        rows = [["reliability", "none"]]
    return "## Reflect\n" + _md_table_any(["metric", "value"], rows)


def _render_caveats(rep):
    caveats = [str(c) for c in (rep.get("caveats") or [])]
    paper = bool(_get(rep, "paper"))
    degraded = False
    for d in rep.get("deliberations") or []:
        if isinstance(d, dict) and d.get("llm_degraded"):
            degraded = True
            break
    for d in rep.get("deployments") or []:
        if isinstance(d, dict) and d.get("paper"):
            paper = True
    if paper and "paper mode" not in caveats:
        caveats.append("paper mode")
    if degraded and "llm_degraded" not in caveats:
        caveats.append("llm_degraded")
    md = "## Caveats\n"
    if caveats:
        md += "\n".join(f"- {_md_cell(c)}" for c in caveats) + "\n"
    else:
        md += "- none\n"
    return md


def _tldr(rep):
    kind = _get(rep, "cycle_kind", "cycle")
    screen = rep.get("screen") if isinstance(rep.get("screen"), dict) else {}
    n = _get(screen, "n_candidates")
    n_dep = len(rep.get("deployments") or [])
    n_rot = len(rep.get("rotations") or [])
    flags = []
    if any(isinstance(d, dict) and d.get("llm_degraded")
           for d in (rep.get("deliberations") or [])):
        flags.append("llm_degraded")
    if _get(rep, "paper"):
        flags.append("paper mode")
    line = f"{kind}: screened {n} candidates, {n_dep} deployments, " \
           f"{n_rot} rotations"
    if flags:
        line += " (" + ", ".join(flags) + ")"
    return line + "."


def write_run_card(cycle_report):
    """Write state/reports/<UTC-ts>-<kind>.json + .md; return the .md path."""
    rep = dict(cycle_report or {})
    kind = _safe_component(_get(rep, "cycle_kind", "cycle"))
    stamp = _stamp(rep)
    reports = os.path.join(_state_dir(), REPORTS_DIR)
    os.makedirs(reports, exist_ok=True)
    base = os.path.join(reports, f"{stamp}-{kind}")
    json_path = base + ".json"
    md_path = base + ".md"
    with open(json_path, "w") as f:
        json.dump(rep, f, indent=2)
        f.write("\n")
    title = f"# Run card — {kind} — {_get(rep, 'at', stamp)}"
    md = "\n\n".join([
        title,
        f"**TL;DR** {_tldr(rep)}",
        _render_route(rep),
        _render_ground(rep),
        _render_deliberate(rep),
        _render_guard(rep),
        _render_deploy(rep),
        _render_observe(rep),
        _render_reflect(rep),
        _render_caveats(rep),
    ]) + "\n"
    with open(md_path, "w") as f:
        f.write(md)
    return md_path


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp(prefix="reflect-demo-")
    os.environ["GRID_STATE_DIR"] = tmp
    brief = {"symbol": "PUMP", "venue": "hyperliquid", "regime": "squeeze",
             "score_final": 111.0, "step": 1.087, "slot": {"slot": 1},
             "stagnation_policy": {"cooldown_h": 18.0},
             "market_context": {"funding_rate": 0.0001}}
    ticket = {"symbol": "PUMP", "venue": "hyperliquid", "regime": "squeeze",
              "grid_type": "neutral", "decision": "GO", "rationale": "demo",
              "llm_degraded": False,
              "max_alloc_mult": 0.8, "step_mult": 1.0,
              "risk": {"conservative": {"max_alloc_mult": 0.8, "step_mult": 1.0}}}
    payloads = {"grid_bot": {"channel": {"low": 0.95, "mid": 1.0, "high": 1.05},
                             "profit_per_grid_pct": 1.087, "grids": 12}}
    did = record_decision(ticket, brief, "deploy", payloads)
    record_outcome(did, {"reason": "rotated", "realized_pnl": 3.21,
                         "fills": 12, "holding_h": 26.5, "observed": {}})
    memories = memories_for(brief)
    report = {"at": _utcnow(), "cycle_kind": "rescreen",
              "screen": {"n_candidates": 5,
                         "top3": [{"venue": "hyperliquid", "symbol": "PUMP",
                                   "regime": "squeeze", "score_final": 111.0,
                                   "step": 1.087}]},
              "deliberations": [{"symbol": "PUMP", "decision": "GO",
                                 "confidence": 0.7, "llm_degraded": False,
                                 "veto": False}],
              "guard": [{"symbol": "PUMP", "ok": True, "violations": []}],
              "deployments": [{"slot": 1, "symbol": "PUMP",
                               "venue": "hyperliquid", "grid_type": "neutral",
                               "step_pct": 1.087, "amount": 10.0,
                               "multiplier": 0.8}],
              "rotations": [], "observed": {}, "reliability": None,
              "caveats": ["demo fixture"]}
    card = write_run_card(report)
    print(json.dumps({"decision_id": did, "memories": memories,
                      "run_card": card, "state_dir": tmp}, indent=2))
