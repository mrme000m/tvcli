#!/usr/bin/env python3
"""Retroactive ledger repair — rebuild recorded history to WT ground truth.

Audit 2026-09-06 (state/reports/audit-20260906-wt-history.md and
audit-20260906-profit-ledger.md §d) proved the recorded ledger had an
optimistic bias: `panic_exited` round-trips (stop/close-all leftovers with
REAL profitLoss) were dropped from realized PnL. The watchdog already fixed
the GOING-FORWARD read path (observe / reliability_grid CLOSED_STATUSES);
this script repairs the ALREADY-RECORDED history:

  (a) state/decisions.jsonl — for every closed trip whose bot code can be
      resolved, patch the outcome with the WT ground-truth split
      (realized_pnl_completed / realized_pnl_panic / trips_completed /
      trips_panic), set realized_pnl to the true total, keep the old value
      as realized_pnl_pre_repair, and mark the record
      {repair: {at, source: "audit-20260906"}}. Untouched lines are
      rewritten byte-identical.
  (b) state/reliability_archive.json — union of the existing archive with
      the repaired bots' full WT trip history (completed + panic_exited,
      deduped by strategy_id + close_ts), with backfill-N seed rows
      persisted with synthetic:true.
  (c) state/reliability.json — recomputed from the rebuilt archive with
      synthetic rows EXCLUDED from every stat (samples / synthetic_samples
      split), merged over the existing ledger like the 24h cron does.

History sources, offline-first: the audit's raw WT dumps
(hist_*.json — WT keeps deleted bots' positions-history reachable by code,
so a dump is full-life ground truth). With --live, a code NOT covered by a
dump is fetched read-only through wt_browser.py's session API GET (never a
mutation, never the daemon's browser tab).

DRY RUN BY DEFAULT: prints a before/after table and writes NOTHING. --apply
writes decisions.jsonl + both reliability files atomically (temp file +
rename) and only when content actually changes — re-running --apply after
a repair is a no-op (repair marker + content comparison). NEVER touches
WunderTrading beyond the optional read-only fetch, and never writes
state/state.json (read only, for bot-code resolution).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
GRID_HOME = os.path.dirname(HERE)
DEFAULT_STATE_DIR = os.path.join(GRID_HOME, "state")
DEFAULT_DUMPS = "/tmp/wt_audit/dumps"

sys.path.insert(0, os.path.join(GRID_HOME, "execution"))
import reliability_grid as rg  # noqa: E402  (one ledger semantics)

REPAIR_SOURCE = "audit-20260906"
CODE_RE = re.compile(r"\b([0-9a-f]{24})\b")
# A decision may be patched with a bot's history only when no trip closed
# (well) after the daemon recorded the close: a LATER bot with the same
# symbol must never be folded into an old decision (e.g. the pre-reset ARB
# of d-001 vs the fleet-era ARB of d-005).
CLOSE_GRACE_S = 300.0
# outcomes that are not closed round-trips we can truth-reconcile
NON_TRIP_REASONS = ("reset-wt", "deploy-failed", "deploy-fail")

# (symbol, venue) -> bot code — the audit's verified mapping, cross-checked
# against each dump's own _links.self.href (ARB ran on BOTH venues under
# different codes; ROBO's hex looks like ARB-BN's but is NOT — the dumps'
# _links are the authority, audit-20260906-wt-history.md table b).
AUDIT_CODES = {
    ("ARB", "hyperliquid"): "c629f5ba3a643a8239c5f58a",
    ("ARB", "binance"): "c629f5ba3a643a82b2edf0cb",
    ("FARTCOIN", "hyperliquid"): "c629f5ba3a643a8264b4a3e6",
    ("UNI", "hyperliquid"): "c629f5ba3a643a82ae8efd56",
    ("ROBO", "binance"): "c629f5ba3a643a823fef4348",
    ("XVG", "binance"): "c629f5ba3a643a82816b52cf",
}


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _load_lines(path):
    """(raw_lines, records) of a JSONL file; ([], []) when absent."""
    if not os.path.exists(path):
        return [], []
    with open(path, encoding="utf-8") as fh:
        raw = fh.readlines()
    recs = []
    for line in raw:
        line = line.strip()
        try:
            recs.append(json.loads(line) if line else None)
        except ValueError:
            recs.append(None)  # keep alignment with raw lines
    return raw, recs


def load_dumps(dumps_dir):
    """{code: {symbol, source, items}} from the audit's raw hist_*.json.

    The bot code comes from the response's own _links.self.href (never from
    the filename), so a misnamed file cannot spoof the mapping; the
    filename only supplies the human-readable symbol.
    """
    out = {}
    if not dumps_dir or not os.path.isdir(dumps_dir):
        return out
    for name in sorted(os.listdir(dumps_dir)):
        if not (name.startswith("hist_") and name.endswith(".json")):
            continue
        raw = _load_json(os.path.join(dumps_dir, name))
        body = (raw or {}).get("body") if isinstance(raw, dict) else raw
        if not isinstance(body, dict):
            continue
        href = ((body.get("_links") or {}).get("self") or {}).get("href", "")
        m = re.search(r"/grid_bots/([0-9a-f]+)/", str(href))
        if not m:
            continue
        items = ((body.get("_embedded") or {}).get("items")) or []
        symbol = name[len("hist_"):-len(".json")]
        if symbol.endswith("_deleted"):
            symbol = symbol[:-len("_deleted")]
        out[m.group(1)] = {
            "symbol": symbol,
            "source": os.path.join(dumps_dir, name),
            "items": [it.get("resource", it) for it in items
                      if isinstance(it, dict)],
        }
    return out


def live_history(code):
    """Read-only positions-history fetch for one bot code (never mutates).

    Same session-API GET path the daemon reads through; used only with
    --live for codes not covered by a dump. Returns [] on any failure.
    """
    wt = os.path.normpath(os.path.join(
        GRID_HOME, "..", "..", ".agents", "skills", "wundertrading",
        "scripts", "wt_browser.py"))
    path = (f"/en/trader/grid_bots/{code}/positions-history/grid"
            f"?page=1&limit=500")
    try:
        proc = subprocess.run([sys.executable, wt, "api", "GET", path],
                              capture_output=True, text=True, timeout=120)
        raw = json.loads(proc.stdout or "")
    except Exception:
        return []
    body = (raw or {}).get("body") if isinstance(raw, dict) else raw
    if not isinstance(body, dict):
        return []
    items = ((body.get("_embedded") or {}).get("items")) or []
    return [it.get("resource", it) for it in items if isinstance(it, dict)]


def bot_truth(items):
    """Per-bot ground truth from positions-history resources.

    Returns {trips, completed_n, completed_pnl, panic_n, panic_pnl,
    total_pnl}: counts/sums split by close status (completed vs
    panic_exited), both USD (profitLoss is PNL-scaled 1e4).
    """
    trips = rg.parse_trades(items)
    # split by the close status straight off the resources
    panic_ids = {it.get("strategyId") or it.get("clientId")
                 for it in items or []
                 if isinstance(it, dict)
                 and it.get("status") == "panic_exited"}
    panic_ids.discard(None)
    completed = [t for t in trips if t.get("strategy_id") not in panic_ids]
    panic = [t for t in trips if t.get("strategy_id") in panic_ids]
    return {
        "trips": trips,
        "completed_n": len(completed),
        "completed_pnl": round(sum(t["pnl_usd"] for t in completed), 4),
        "panic_n": len(panic),
        "panic_pnl": round(sum(t["pnl_usd"] for t in panic), 4),
        "total_pnl": round(sum(t["pnl_usd"] for t in trips), 4),
    }


def journal_map(state_path, records):
    """{(symbol, venue): [code, ...]} from adopted-bot decisions and the
    daemon journal's rotation-delete entries (read-only)."""
    out = {}

    def add(sym, venue, code):
        if sym and venue and code:
            out.setdefault((sym, venue), [])
            if code not in out[(sym, venue)]:
                out[(sym, venue)].append(code)

    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        act = rec.get("action") or {}
        m = CODE_RE.search(str(act.get("msg") or ""))
        if act.get("kind") == "adopted" and m:
            add(rec.get("symbol"), rec.get("venue"), m.group(1))
    st = _load_json(state_path) or {}
    slots = {}
    for slot, bot in (st.get("active_bots") or {}).items():
        if isinstance(bot, dict) and bot.get("bot_code"):
            slots[str(slot)] = bot
    for e in (st.get("journal") or []):
        if not isinstance(e, dict):
            continue
        m = CODE_RE.search(str(e.get("msg") or ""))
        if not m:
            continue
        slot = str(e.get("slot") or "")
        bot = slots.get(slot) or {}
        add(bot.get("symbol"), bot.get("venue"), m.group(1))
    return out


def resolve_code(rec, dumps, jmap, live=False):
    """Bot code for one closed decision, or None. Chain, most authoritative
    first: outcome bot_code, the audit's verified (symbol, venue) map,
    adopted-bot message, journal codes, then a unique symbol match among
    the dumps (same-symbol ambiguity broken by the old completed-only sum,
    which is exactly what the daemon used to record)."""
    oc = rec.get("outcome") or {}
    sym, venue = rec.get("symbol"), rec.get("venue")
    candidates = []
    if oc.get("bot_code"):
        candidates.append(oc["bot_code"])
    audit = AUDIT_CODES.get((sym, venue))
    if audit:
        candidates.append(audit)
    candidates.extend(jmap.get((sym, venue) or (), []))
    m = CODE_RE.search(str((rec.get("action") or {}).get("msg") or ""))
    if m:
        candidates.append(m.group(1))
    by_symbol = [c for c, d in dumps.items() if d["symbol"] == str(sym)]
    if len(by_symbol) == 1:
        candidates.append(by_symbol[0])
    for c in candidates:
        if c in dumps:
            return c
    if len(by_symbol) > 1:
        old = oc.get("realized_pnl")
        if isinstance(old, (int, float)):
            for c in by_symbol:
                truth = bot_truth(dumps[c]["items"])
                if abs(truth["completed_pnl"] - float(old)) < 1e-6:
                    return c
    if live and candidates:
        for c in candidates:
            hist = live_history(c)
            if hist:
                dumps[c] = {"symbol": str(sym), "source": "live",
                            "items": hist}
                return c
    return None


def is_closed_trip(rec):
    """True for a decision outcome that is a closed round-trip of a bot
    whose PnL we can truth-reconcile (not a reset wipe or a failed deploy)."""
    oc = rec.get("outcome") or {}
    if not oc.get("closed_at"):
        return False
    if oc.get("realized_pnl") is None:
        return False
    reason = str(oc.get("reason") or "")
    if any(r in reason for r in NON_TRIP_REASONS):
        return False
    return True


def time_guard_ok(rec, truth):
    """No trip may close (well) after the decision's recorded close."""
    closed = rg._ts_epoch((rec.get("outcome") or {}).get("closed_at"))  # noqa: SLF001
    if closed is None:
        return False
    for t in truth["trips"]:
        if t["close_ts"] > closed + CLOSE_GRACE_S:
            return False
    return True


def plan_repairs(records, dumps, jmap, live=False):
    """Repairs: one per closed-trip decision whose bot history is known.

    Skips records already carrying the repair marker (idempotency) and
    mappings that fail the time guard (wrong-era bot).
    """
    out = []
    for idx, rec in enumerate(records):
        if not isinstance(rec, dict) or not is_closed_trip(rec):
            continue
        already = isinstance(rec.get("repair"), dict) and \
            rec["repair"].get("source") == REPAIR_SOURCE
        code = resolve_code(rec, dumps, jmap, live=live)
        entry = {"idx": idx, "rec": rec, "already": already}
        if not code:
            entry["skip"] = "no WT history for this bot (code unresolved)"
            out.append(entry)
            continue
        truth = bot_truth(dumps[code]["items"])
        entry.update(code=code, truth=truth, symbol=dumps[code]["symbol"])
        if not already and not time_guard_ok(rec, truth):
            entry["skip"] = ("time guard: bot history outlives the decision "
                            "close (different-era bot?) — not patched")
        out.append(entry)
    return out


def patch_outcome(rec, truth):
    """Apply the ground-truth split to one decision record (in place)."""
    oc = rec["outcome"]
    if "realized_pnl_pre_repair" not in oc:
        oc["realized_pnl_pre_repair"] = oc.get("realized_pnl")
    oc["realized_pnl"] = truth["total_pnl"]
    oc["realized_pnl_completed"] = truth["completed_pnl"]
    oc["realized_pnl_panic"] = truth["panic_pnl"]
    oc["trips_completed"] = truth["completed_n"]
    oc["trips_panic"] = truth["panic_n"]
    rec["repair"] = {"at": datetime.now(timezone.utc).isoformat(
        timespec="seconds"), "source": REPAIR_SOURCE}


def rebuild_archive(current, repairs):
    """Rebuilt archive: existing rows ∪ repaired bots' full WT trips.

    - keyed through ledger_key() (same canonicalization as the daemon)
    - dedup by (strategy_id, close_ts) — re-running cannot double-count
    - backfill-N seed rows persisted with synthetic:true
    - bounded per archetype, sorted by close_ts
    """
    current = current if isinstance(current, dict) else {}
    out = {}
    for arch, rows in current.items():
        key = rg.ledger_key(arch)
        for t in rows or []:
            if isinstance(t, dict):
                out.setdefault(key, []).append(
                    dict(t, synthetic=True) if rg.is_synthetic(t) else t)
    for r in repairs:
        if r.get("skip") or r.get("already"):
            continue
        arch = rg.ledger_key(r["rec"].get("regime"))
        bucket = out.setdefault(arch, [])
        existing = {(t.get("strategy_id"), t.get("close_ts"))
                    for t in bucket}
        for t in r["truth"]["trips"]:
            if (t.get("strategy_id"), t.get("close_ts")) not in existing:
                bucket.append(t)
                existing.add((t.get("strategy_id"), t.get("close_ts")))
    for arch in out:
        out[arch].sort(key=lambda t: t.get("close_ts") or 0)
        out[arch] = out[arch][-rg.ARCHIVE_MAX_PER_ARCHETYPE:]
    return out


def rebuild_reliability(current, archive):
    """Ledger from the rebuilt archive, real-samples only, merged over the
    existing ledger exactly like the daemon's 24h cron (non-empty stats
    replace; zero-sample archetypes never erase history)."""
    merged = dict(current if isinstance(current, dict) else {})
    by_arch = {rg.ledger_key(a): rows for a, rows in archive.items()}
    stats = rg.archetype_stats(by_arch) if by_arch else {}
    for arch, st in stats.items():
        if st.get("samples") or st.get("synthetic_samples"):
            merged[arch] = st
    return merged


def _atomic_write(path, text):
    tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False,
                                      dir=os.path.dirname(path) or ".",
                                      suffix=".tmp")
    try:
        tmp.write(text)
        tmp.close()
        os.replace(tmp.name, path)
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


def write_if_changed(path, text):
    """Atomic write, only when content actually changed. Returns True when
    the file was (re)written — the idempotency backbone."""
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                return False
    except OSError:
        pass
    _atomic_write(path, text)
    return True


def _fmt(v):
    if isinstance(v, (int, float)):
        return f"{v:+.4f}" if abs(v) >= 0.00005 else "0.0000"
    return str(v)


def build_report(repairs, archive_before, archive_after,
                 ledger_before, ledger_after, dumps, active_codes,
                 state_dir, apply=False):
    """Human-readable before/after table (dry-run proof)."""
    lines = []
    lines.append("=" * 78)
    lines.append(f"LEDGER REPAIR — {REPAIR_SOURCE} "
                 f"({'APPLY' if apply else 'DRY RUN — no files written'})")
    lines.append("=" * 78)
    lines.append("")
    lines.append("Per-decision repair (state/decisions.jsonl):")
    lines.append(f"  {'id':<15} {'bot':<14} {'trips c/p':>9}  "
                 f"{'realized old':>12}  {'true':>9}  {'delta':>9}")
    tot_old = tot_new = 0.0
    patched = 0
    for r in repairs:
        rec = r["rec"]
        rid = rec.get("id") or f"line{r['idx'] + 1}"
        sym = r.get("symbol") or rec.get("symbol") or "?"
        venue = (rec.get("venue") or "?")[:4]
        oc = rec.get("outcome") or {}
        old = oc.get("realized_pnl")
        if r.get("skip"):
            lines.append(f"  {rid:<15} {sym + '/' + venue:<14} "
                         f"{'—':>9}  {_fmt(old) if old is not None else '—':>12}"
                         f"  {'SKIP':>9}  {r['skip']}")
            continue
        truth = r["truth"]
        if r.get("already"):
            old = oc.get("realized_pnl_pre_repair", old)
            note = " (already repaired)"
        else:
            patched += 1
            note = ""
        lines.append(f"  {rid:<15} {sym + '/' + venue:<14} "
                     f"{str(truth['completed_n']) + '/' + str(truth['panic_n']):>9}  "
                     f"{_fmt(old):>12}  {_fmt(truth['total_pnl']):>9}  "
                     f"{_fmt(truth['total_pnl'] - (old or 0)):>9}{note}")
        tot_old += old or 0
        tot_new += truth["total_pnl"]
    lines.append(f"  {'closed-trip ledger total':<41} {_fmt(tot_old):>12}  "
                 f"{_fmt(tot_new):>9}  {_fmt(tot_new - tot_old):>9}")
    lines.append("")

    def arch_line(ledger, arch):
        st = ledger.get(arch) or {}
        return (f"{st.get('samples', 0)} real "
                f"(+{st.get('synthetic_samples', 0)} seeded) "
                f"gp {_fmt(st.get('gross_profit_usd', 0))} "
                f"gl {_fmt(st.get('gross_loss_usd', 0))}")

    lines.append("Reliability ledger (state/reliability.json):")
    arches = sorted(set(ledger_before) | set(ledger_after))
    for arch in arches:
        lines.append(f"  {arch:<34} {arch_line(ledger_before, arch):>36}"
                     f"  ->  {arch_line(ledger_after, arch)}")
    lines.append("")

    # fleet-wide realized vs WT ground truth (all dumps, incl. active bots):
    # "before" = closed decisions (completed-only bias) + active bots'
    # completed-only sums — exactly what the daemon used to report.
    old_total = tot_old
    new_total = tot_new
    for code, d in dumps.items():
        if code in active_codes:
            truth = bot_truth(d["items"])
            old_total += truth["completed_pnl"]
            new_total += truth["total_pnl"]
    lines.append("Fleet realized vs WT ground truth (closed + active bots):")
    lines.append(f"  daemon (before) {_fmt(old_total)}  ->  "
                 f"WT truth {_fmt(new_total)}  (delta {_fmt(new_total - old_total)})")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Rebuild the recorded profit ledger to WT ground truth "
                    "(dry-run by default)")
    ap.add_argument("--apply", action="store_true",
                    help="write decisions.jsonl + reliability files "
                         "(default: dry run, write nothing)")
    ap.add_argument("--live", action="store_true",
                    help="fetch missing bot histories via the WT session "
                         "API (read-only, new tab) instead of skipping")
    ap.add_argument("--dumps", default=DEFAULT_DUMPS,
                    help=f"audit dump directory (default: {DEFAULT_DUMPS})")
    ap.add_argument("--state-dir", default=os.environ.get(
        "GRID_STATE_DIR", DEFAULT_STATE_DIR),
        help="state directory (default: env GRID_STATE_DIR or ./state)")
    args = ap.parse_args(argv)

    decisions_path = os.path.join(args.state_dir, "decisions.jsonl")
    state_path = os.path.join(args.state_dir, "state.json")
    archive_path = os.path.join(args.state_dir, "reliability_archive.json")
    reliability_path = os.path.join(args.state_dir, "reliability.json")

    raw, records = _load_lines(decisions_path)
    if not records:
        print(f"no decisions to repair at {decisions_path}", file=sys.stderr)
        return 1
    dumps = load_dumps(args.dumps)
    if not dumps:
        print(f"no audit dumps found at {args.dumps} "
              f"(pass --dumps or --live)", file=sys.stderr)
        return 1
    jmap = journal_map(state_path, records)
    active_codes = set()
    st = _load_json(state_path) or {}
    for bot in (st.get("active_bots") or {}).values():
        if isinstance(bot, dict) and bot.get("bot_code"):
            active_codes.add(bot["bot_code"])

    repairs = plan_repairs(records, dumps, jmap, live=args.live)
    if not repairs:
        print("no repairable closed trips found — nothing to do")
        return 0

    archive_before = _load_json(archive_path) or {}
    ledger_before = _load_json(reliability_path) or {}
    archive_after = rebuild_archive(archive_before, repairs)
    ledger_after = rebuild_reliability(ledger_before, archive_after)

    print(build_report(repairs, archive_before, archive_after,
                      ledger_before, ledger_after, dumps, active_codes,
                      args.state_dir, apply=args.apply))

    pending = [r for r in repairs if not r.get("skip")
               and not r.get("already")]
    if not args.apply:
        done = len(repairs) - len(pending) - \
            sum(1 for r in repairs if r.get("skip"))
        print(f"DRY RUN: {len(pending)} decision(s) would be patched "
              f"({done} already repaired); no files written. "
              f"Re-run with --apply to write them.")
        return 0

    # (a) decisions.jsonl — patch only the repaired lines, byte-preserving
    #     everything else
    new_lines = list(raw)
    for r in pending:
        patch_outcome(r["rec"], r["truth"])
        new_lines[r["idx"]] = json.dumps(r["rec"], ensure_ascii=False) + "\n"
    w1 = write_if_changed(decisions_path, "".join(new_lines))
    # (b) archive + (c) reliability ledger
    w2 = write_if_changed(archive_path, json.dumps(archive_after, indent=1))
    w3 = write_if_changed(reliability_path,
                          json.dumps(ledger_after, indent=2, sort_keys=True))
    wrote = [n for n, w in (("decisions.jsonl", w1),
                            ("reliability_archive.json", w2),
                            ("reliability.json", w3)) if w]
    print(f"APPLY: patched {len(pending)} decision(s); "
          + (", ".join(wrote) + " written" if wrote else
             "all files already up to date (idempotent no-op)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
