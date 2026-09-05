#!/usr/bin/env python3
"""Worker A — reliability ledger for closed grid round-trips.

Pure math (`parse_trades`, `archetype_stats`) is importable with no network
or browser dependency; `bot_trades` is the only network path and it goes
through the `wt_browser.py` subprocess with a timeout, never raising.

State is persisted as `state/reliability.json`:
    {"<archetype>": {"samples": n, "profit_factor": p, "recent_pf": r, ...}}
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GRID_HOME = os.path.dirname(HERE)
STATE_DIR = os.environ.get("GRID_STATE_DIR", os.path.join(GRID_HOME, "state"))
RELIABILITY_PATH = os.path.join(STATE_DIR, "reliability.json")
WUN_SCRIPTS = os.path.normpath(os.path.join(
    GRID_HOME, "..", "..", ".agents", "skills", "wundertrading", "scripts"))
WT_BROWSER = os.path.join(WUN_SCRIPTS, "wt_browser.py")

PROFIT_FACTOR_CAP = 99.0
RECENT_WINDOW = 20
PNL_SCALE = 10000.0  # positions-history profitLoss is USD × 1e4

# Canonical reliability-ledger keys. Fresh deploys key the ledger by the
# archetype LABEL the screen emits (universe_screen.ARCHETYPE), but adopted
# bots used to key it by the raw regime NAME — the same market regime wrote
# its stats under two different keys, splitting the sample base that gates
# sizing escalation and kill-flags. `ledger_key()` normalizes any regime
# name or archetype label to the one canonical key. Keep in sync with
# universe_screen.ARCHETYPE.
ARCHETYPE_LABELS = {
    "chop_high_volatility": "Neutral Grid (mean-reversion)",
    "squeeze": "Neutral Grid tight + Stop Trigger",
    "neutral": "probe Neutral/Infinite grid",
    "trend_up": "Long Grid / classic LONG",
    "trend_down": "Short Grid (futures) / flat (spot)",
}


def ledger_key(archetype):
    """Canonical ledger key: regime names map to their archetype label."""
    if not archetype:
        return "unknown"
    return ARCHETYPE_LABELS.get(str(archetype), str(archetype))

# ── PocketBase write-through (optional, non-fatal) ────────────────────
# reliability.json is the system of record; this mirrors the ledger into the
# PocketBase side channel when it is configured and up. pbclient.py lives in
# GRID_HOME, so add it to sys.path defensively.
try:
    sys.path.insert(0, GRID_HOME)
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


def _ts_epoch(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000 if v > 1e12 else v
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        from datetime import datetime, timezone
        return datetime.fromisoformat(s).astimezone(timezone.utc).timestamp()
    except Exception:
        try:
            v = float(s)
            return v / 1000 if v > 1e12 else v
        except Exception:
            return None


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_trades(items):
    """Completed round-trips from positions-history resources (pure).

    Each returned trade: {pnl_usd, close_ts, entered_at, strategy_id}.
    Sorted by close time ascending.
    """
    trades = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if it.get("status") != "completed":
            continue
        close = _ts_epoch(it.get("exitedAt") or it.get("updatedAt")
                          or it.get("enteredAt"))
        if close is None:
            continue
        raw_pnl = it.get("profitLoss")
        if raw_pnl is None:
            raw_pnl = it.get("weightedProfitLoss")
        trades.append({
            "pnl_usd": round(_num(raw_pnl) / PNL_SCALE, 6),
            "close_ts": close,
            "entered_at": it.get("enteredAt"),
            "strategy_id": it.get("strategyId") or it.get("clientId"),
        })
    trades.sort(key=lambda t: t["close_ts"])
    return trades


def _run_wt(args, timeout=120):
    cmd = [sys.executable, WT_BROWSER, *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"ok": proc.returncode == 0, "rc": proc.returncode,
                "stdout": proc.stdout or "", "stderr": proc.stderr or ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "rc": None, "stdout": "", "stderr": "timeout"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "rc": None, "stdout": "", "stderr": str(exc)[:200]}


def _history_resources(bot_code, limit=500):
    res = _run_wt(["api", "GET",
                   f"/en/trader/grid_bots/{bot_code}/positions-history/grid"
                   f"?page=1&limit={limit}"])
    if not res["ok"] or not res["stdout"].strip():
        return []
    try:
        raw = json.loads(res["stdout"])
    except Exception:
        return []
    items = ((raw.get("_embedded") or {}).get("items")) if isinstance(raw, dict) else []
    return [it.get("resource", it) for it in items if isinstance(it, dict)]


def bot_trades(bot_code):
    """Closed round-trips for one bot, fetched live. Never raises."""
    try:
        return parse_trades(_history_resources(bot_code))
    except Exception:  # noqa: BLE001
        return []


def _stats(trades):
    trades = list(trades or [])
    samples = len(trades)
    pnls = [_num(t.get("pnl_usd")) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 4)
    else:
        profit_factor = round(PROFIT_FACTOR_CAP if gross_profit > 0 else 0.0, 4)

    recent = pnls[-RECENT_WINDOW:]
    recent_wins = [p for p in recent if p > 0]
    recent_losses = [p for p in recent if p < 0]
    rgp = sum(recent_wins)
    rgl = abs(sum(recent_losses))
    if rgl > 0:
        recent_pf = round(rgp / rgl, 4)
    else:
        recent_pf = round(PROFIT_FACTOR_CAP if rgp > 0 else 0.0, 4)

    # max peak-to-trough drawdown of the cumulative PnL curve
    peak = 0.0
    cum = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        "samples": samples,
        "profit_factor": profit_factor,
        "recent_pf": recent_pf,
        "win_rate": round(len(wins) / samples, 4) if samples else 0.0,
        "expectancy_usd": round(sum(pnls) / samples, 4) if samples else 0.0,
        "max_dd_usd": round(max_dd, 4),
        "gross_profit_usd": round(gross_profit, 4),
        "gross_loss_usd": round(gross_loss, 4),
    }


def archetype_stats(bots_by_archetype):
    """`{archetype: [trade, ...]} -> {archetype: stats}` (pure math).

    Also accepts `{archetype: {bot_code: [trade, ...]}}` and flattens the
    inner values for convenience.
    """
    out = {}
    for archetype, trades in (bots_by_archetype or {}).items():
        flat = []
        if isinstance(trades, dict):
            for inner in trades.values():
                flat.extend(inner or [])
        else:
            flat = list(trades or [])
        out[archetype] = _stats(flat)
    return out


# Trades from bots that were rotated out (deleted on WunderTrading) are
# archived here so the 24h reliability recompute — which can only reach
# ACTIVE bots — does not silently drop them. Keyed by archetype, bounded.
ARCHIVE_PATH = os.path.join(STATE_DIR, "reliability_archive.json")
ARCHIVE_MAX_PER_ARCHETYPE = 500


def archive_trades(trades, archetype):
    """Append closed round-trips of a rotated-out bot under its archetype.

    The key is canonicalized through `ledger_key()` so regime-name and
    archetype-label callers write the same bucket. Bounded per archetype
    (oldest dropped). Never raises; returns True when the archive was
    updated.
    """
    archetype = ledger_key(archetype)
    trades = [t for t in (trades or []) if isinstance(t, dict)]
    if not trades:
        return False
    try:
        archive = {}
        try:
            with open(ARCHIVE_PATH, encoding="utf-8") as fh:
                archive = json.load(fh)
        except (OSError, ValueError):
            archive = {}
        rows = [r for r in archive.get(archetype, [])
                if isinstance(r, dict)]
        rows.extend(trades)
        archive[archetype] = rows[-ARCHIVE_MAX_PER_ARCHETYPE:]
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = ARCHIVE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(archive, fh, indent=1)
        os.replace(tmp, ARCHIVE_PATH)
        return True
    except Exception:
        return False


def archived_by_archetype():
    """Archived (rotated-out) trades grouped per archetype; {} when absent."""
    try:
        with open(ARCHIVE_PATH, encoding="utf-8") as fh:
            archive = json.load(fh)
        return {arch: [t for t in rows if isinstance(t, dict)]
                for arch, rows in (archive or {}).items()} if isinstance(archive, dict) else {}
    except (OSError, ValueError):
        return {}


def normalize_archive():
    """Re-key reliability_archive.json entries through `ledger_key()`.

    One-time (idempotent) migration for the split-key bug: archive rows
    written under raw regime names move under the canonical archetype
    label, merging with any rows already there. Returns True when the
    file changed. Never raises.
    """
    try:
        try:
            with open(ARCHIVE_PATH, encoding="utf-8") as fh:
                archive = json.load(fh)
        except (OSError, ValueError):
            return False
        if not isinstance(archive, dict) or not archive:
            return False
        out = {}
        changed = False
        for arch, rows in archive.items():
            key = ledger_key(arch)
            if key != arch:
                changed = True
            merged = [t for t in out.get(key, []) if isinstance(t, dict)]
            merged.extend(t for t in (rows or []) if isinstance(t, dict))
            # de-dup by (strategy_id, close_ts): the same bot's trades must
            # not be double-counted when both key spellings existed
            seen, dedup = set(), []
            for t in merged:
                marker = (t.get("strategy_id"), t.get("close_ts"))
                if marker in seen and marker != (None, None):
                    continue
                seen.add(marker)
                dedup.append(t)
            out[key] = dedup[-ARCHIVE_MAX_PER_ARCHETYPE:]
        if not changed:
            return False
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = ARCHIVE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
        os.replace(tmp, ARCHIVE_PATH)
        return True
    except Exception:
        return False


def save(data):
    """Atomically persist the reliability ledger. Never raises."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = RELIABILITY_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data if isinstance(data, dict) else {}, fh,
                      indent=2, sort_keys=True)
        os.replace(tmp, RELIABILITY_PATH)
        return True
    except Exception:
        return False
    finally:
        # Non-fatal write-through: mirror the ledger into PocketBase.
        _pb = _pb_mirror()
        if _pb is not None:
            try:
                _pb.reliability({"ledger": data if isinstance(data, dict) else {},
                                 "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            except Exception:
                pass


def load():
    """Read the reliability ledger; {} on any failure. Never raises."""
    try:
        with open(RELIABILITY_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Reliability ledger for closed grid round-trips")
    ap.add_argument("--bot", help="fetch closed trades for a bot code")
    ap.add_argument("--load", action="store_true", help="print current ledger")
    ap.add_argument("--compute", help="compute stats from a JSON file "
                    "of {archetype: [trade...]} (or - for stdin)")
    ap.add_argument("--save", action="store_true", help="save computed stats")
    args = ap.parse_args(argv)
    if args.bot:
        print(json.dumps(bot_trades(args.bot), indent=2, sort_keys=True))
        return 0
    if args.load:
        print(json.dumps(load(), indent=2, sort_keys=True))
        return 0
    if args.compute:
        if args.compute == "-":
            raw = sys.stdin.read()
        else:
            with open(args.compute, "r", encoding="utf-8") as fh:
                raw = fh.read()
        data = json.loads(raw)
        stats = archetype_stats(data)
        if args.save:
            save(stats)
        print(json.dumps(stats, indent=2, sort_keys=True))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
