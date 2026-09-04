#!/usr/bin/env python3
"""Worker A — read-only observation of live WunderTrading grid bots.

All WunderTrading calls go through the `wt_browser.py` subprocess with a
timeout and never raise: callers get structured error fields or empty
collections instead. This module is imported defensively by the daemon, so
imports must stay stdlib-only and side-effect free.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
GRID_HOME = os.path.dirname(HERE)
WUN_SCRIPTS = os.path.normpath(os.path.join(
    GRID_HOME, "..", "..", ".agents", "skills", "wundertrading", "scripts"))
WT_BROWSER = os.path.join(WUN_SCRIPTS, "wt_browser.py")

POSITIONS_LIMIT = 50
HISTORY_LIMIT = 200
FILLS_WINDOW_S = 24 * 3600


def _run_wt(args, timeout=90):
    """Run wt_browser.py; never raises. Returns a result dict."""
    cmd = [sys.executable, WT_BROWSER, *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"ok": proc.returncode == 0, "rc": proc.returncode,
                "stdout": proc.stdout or "", "stderr": proc.stderr or ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "rc": None, "stdout": "", "stderr": "timeout"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "rc": None, "stdout": "", "stderr": str(exc)[:200]}


def _api_json(path, timeout=90):
    """GET `path` through wt_browser.py and return the parsed JSON body.

    Returns None when the call fails or the body is not JSON.
    """
    res = _run_wt(["api", "GET", path], timeout=timeout)
    if not res["ok"] or not res["stdout"].strip():
        return None
    try:
        return json.loads(res["stdout"])
    except Exception:
        return None


def _resources(raw):
    """Extract `_embedded.items[].resource` (or a top-level list)."""
    out = []
    if isinstance(raw, list):
        out = [it.get("resource", it) if isinstance(it, dict) else it
               for it in raw]
    elif isinstance(raw, dict):
        items = ((raw.get("_embedded") or {}).get("items")) or []
        for it in items:
            if isinstance(it, dict):
                out.append(it.get("resource", it))
    return out


def _grid_resources():
    raw = _api_json("/en/trader/grid_bots/grid?page=1&limit=50")
    return _resources(raw)


def grid_status():
    """List of active bots: code/status/paperTrading/exchange/pair/pairCode."""
    out = []
    for res in _grid_resources():
        pair = res.get("pair") or {}
        exchange = res.get("exchange") or {}
        out.append({
            "code": res.get("code"),
            "status": res.get("status"),
            "paperTrading": bool(res.get("paperTrading")),
            "exchange": exchange.get("code"),
            "pair": pair.get("viewSymbol") or pair.get("unifiedCode"),
            "pairCode": pair.get("code"),
        })
    return out


def _balance_usd(balance):
    """Extract a numeric USD balance from a profile's balance object.

    Never touches `ccxt` (which may contain apiKey).
    """
    if not isinstance(balance, dict):
        return None
    nb = balance.get("notionalBalances") or {}
    total = nb.get("total") if isinstance(nb, dict) else None
    if isinstance(total, dict):
        usd = total.get("USD")
        if isinstance(usd, (int, float)):
            return round(float(usd), 2)
    assets = balance.get("assets") or {}
    if isinstance(assets, dict):
        for asset in assets.values():
            if isinstance(asset, dict) and isinstance(asset.get("total"), (int, float)):
                return round(float(asset["total"]), 2)
    return None


_INIT_CACHE = {"at": 0.0, "raw": None, "fn": None}  # 60s TTL
_INIT_TTL = 60.0


def _upsert_init():
    """Cached GET /en/trader/grid_bots/upsert init data (never raises).

    The cache is keyed on the identity of `_api_json` so tests that patch
    it get a guaranteed cache miss (no cross-test pollution).
    """
    fn = _api_json
    now = time.time()
    if (_INIT_CACHE["raw"] is not None
            and _INIT_CACHE.get("fn") is fn
            and now - _INIT_CACHE["at"] < _INIT_TTL):
        return _INIT_CACHE["raw"]
    raw = fn("/en/trader/grid_bots/upsert")
    if isinstance(raw, dict) and isinstance(raw.get("data"), dict):
        _INIT_CACHE.update({"at": now, "raw": raw, "fn": fn})
    return raw


def grid_capacity():
    """Plan capacity from the upsert init data. Returns {} when unavailable.

    Shape (verified live 2026-09-05 on the free plan):
        max_active: {"other": 1, "premium": 200}
        active:     {"other": 1, "premium": {"HYPERLIQUID_SWAP": 2}}
        used_pairs: {EXCHANGE: {profileCode: [pairCode, ...]}}

    Semantics: "premium" is an exchange tier (HYPERLIQUID_SWAP qualifies),
    not a plan tier — non-premium exchanges share one active-grid-bot cap
    (`other`); premium exchanges have their own, much larger cap. The
    account-limits dashboard endpoint (gridBots 3/200) does NOT reflect the
    per-tier cap actually enforced by grid_bots/upsert.
    """
    raw = _upsert_init()
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, dst in (("maxActiveGridBots", "max_active"),
                     ("activeGridBots", "active"),
                     ("exchangesUsedPairs", "used_pairs")):
        val = data.get(key)
        if isinstance(val, dict):
            out[dst] = val
    return out


def account_limits():
    """Plan limits from the trader dashboard (never raises; {} on failure).

    GET /en/trader/dashboard/account-limits returns per-bot-type usage:
        {"openPositions": {...}, "gridBots": {"active": n, "max": m},
         "dcaBots": {...}, "signalBots": {...}, "aiSpreadBots": {...},
         "aiBots": {...}}
    NOTE (verified 2026-09-05): this is the DASHBOARD view. The cap actually
    enforced by grid_bots/upsert is per exchange tier (see grid_capacity()):
    Hyperliquid is premium via WT's 0.035% builder-fee arrangement; every
    other exchange runs on the Free plan (1 active grid bot).
    """
    raw = _api_json("/en/trader/dashboard/account-limits")
    return raw if isinstance(raw, dict) else {}


def grid_profiles():
    """List of connected trading profiles with code/name/exchange/paper/balance."""
    raw = _upsert_init()
    data = raw.get("data") if isinstance(raw, dict) else None
    profiles = (data or {}).get("exchangesProfiles") or {}
    out = []
    if not isinstance(profiles, dict):
        return out
    for exchange, accounts in profiles.items():
        if not isinstance(accounts, dict):
            continue
        for code, acc in accounts.items():
            if not isinstance(acc, dict):
                continue
            out.append({
                "code": code,
                "name": acc.get("name_of_account"),
                "exchange": exchange,
                "paperTrading": bool(acc.get("paperTrading")),
                "balance": _balance_usd(acc.get("balance")),
            })
    return out


def _positions_open(code):
    raw = _api_json(
        f"/en/trader/grid_bots/{code}/positions/grid?page=1&limit={POSITIONS_LIMIT}")
    if isinstance(raw, dict):
        rows = raw.get("rows") or []
        return [r for r in rows if isinstance(r, dict)]
    return []


def _positions_history(code, limit=HISTORY_LIMIT):
    raw = _api_json(
        f"/en/trader/grid_bots/{code}/positions-history/grid?page=1&limit={limit}")
    return _resources(raw)


def _ts_epoch(value):
    """Parse an ISO-8601 (or epoch-ms) timestamp to a UTC epoch float."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000 if v > 1e12 else v
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc).timestamp()
    except Exception:
        try:
            v = float(s)
            return v / 1000 if v > 1e12 else v
        except Exception:
            return None


def _closed_round_trips(history):
    """Completed round-trip resources with a UTC close time, newest first."""
    trips = []
    for res in history:
        if not isinstance(res, dict):
            continue
        if res.get("status") != "completed":
            continue
        close = _ts_epoch(res.get("exitedAt") or res.get("updatedAt")
                          or res.get("enteredAt"))
        if close is None:
            continue
        trips.append({"res": res, "close": close})
    trips.sort(key=lambda t: t["close"], reverse=True)
    return trips


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ladder_full(bot, res, open_positions):
    """True when open positions >= 80% of grid levels on one side.

    Grid-level count prefers the bot's configured ladder depth
    (`channel.grids`, then `upsert.gridLevels`) over the live resource
    `gridLevels`, which can lag an in-flight grid edit. Directional grids
    (long/short) keep the whole ladder on one side, so the full count is
    the side size; a neutral grid splits its levels across the buy and sell
    sides, so one side is half the total.
    """
    channel = bot.get("channel") or {}
    upsert = bot.get("upsert") or {}
    raw_grids = (channel.get("grids") or upsert.get("gridLevels")
                 or res.get("gridLevels"))
    try:
        grids = int(raw_grids)
    except (TypeError, ValueError):
        grids = 0
    if grids <= 0:
        return False
    grid_type = str(res.get("gridTradingType")
                    or (bot.get("ticket") or {}).get("grid_type")
                    or "").lower()
    side_levels = max(1, grids // 2) if grid_type == "neutral" else grids
    return len(open_positions) >= 0.8 * side_levels


def observe_all(active_bots):
    """`{slot: {status, price, fills_24h, realized_ratio, unrealized_pnl,
                 ladder_full, dd_vs_atr_band, error?}}` for active bots.

    `active_bots` maps slot keys to bot dicts carrying at least `bot_code`;
    `channel` and `stagnation_policy` are optional and improve the derived
    fields. Never raises.
    """
    resources = _grid_resources()
    by_code = {}
    for res in resources:
        code = res.get("code")
        if code:
            by_code[code] = res
    out = {}
    for slot, bot in (active_bots or {}).items():
        out[str(slot)] = _observe_one(bot, by_code)
    return out


def _observe_one(bot, by_code):
    bot = bot if isinstance(bot, dict) else {}
    bot_code = bot.get("bot_code") or bot.get("code")
    if not bot_code:
        return {"error": "no bot_code", "status": "unknown", "price": None,
                "fills_24h": 0, "realized_ratio": 0.0, "unrealized_pnl": None,
                "ladder_full": False, "dd_vs_atr_band": 0.0}
    res = by_code.get(bot_code) or {}
    status = res.get("status") or "unknown"

    open_rows = _positions_open(bot_code)
    history = _positions_history(bot_code)

    # open positions = rows not completed/closed
    open_positions = [r for r in open_rows
                      if (r.get("status") or "").lower() not in
                      ("completed", "closed", "cancelled", "canceled", "deleted")]
    unrealized = None
    if open_positions:
        pnl_sum = sum(_number(r.get("totalProfitLoss")) for r in open_positions)
        unrealized = round(pnl_sum / 10000.0, 4)
    if unrealized is None:
        up = res.get("unrealizedPnl") or {}
        if isinstance(up, dict) and up.get("pnlFiat") is not None:
            unrealized = round(_number(up.get("pnlFiat")), 4)

    price = None
    newest = sorted(open_positions,
                    key=lambda r: _ts_epoch(r.get("updatedAt")) or 0,
                    reverse=True)
    if newest:
        price = _number(newest[0].get("lastPrice"), None)
    if price is None:
        price = _number(res.get("currentPrice"), None)
    if price is None:
        channel = bot.get("channel") or {}
        price = _number(channel.get("mid"), None)

    fills_24h = 0
    now = time.time()
    for trip in _closed_round_trips(history):
        age = now - trip["close"]
        if 0 <= age <= FILLS_WINDOW_S:
            fills_24h += 1

    policy = bot.get("stagnation_policy") or {}
    expected = _number(policy.get("expected_fills_per_24h"), 0.0)
    realized_ratio = round(fills_24h / expected, 4) if expected > 0 else 0.0

    ladder_full = _ladder_full(bot, res, open_positions)

    dd_vs_atr_band = _dd_vs_atr_band(bot, price)

    obs = {
        "status": status,
        "price": price,
        "fills_24h": fills_24h,
        "realized_ratio": realized_ratio,
        "unrealized_pnl": unrealized,
        "ladder_full": ladder_full,
        "dd_vs_atr_band": dd_vs_atr_band,
    }
    if not res:
        obs["error"] = "grid resource not found in status list"
    return obs


def _dd_vs_atr_band(bot, price):
    """Drawdown below the channel mid, measured in ATR-band units."""
    channel = bot.get("channel") or {}
    mid = _number(channel.get("mid"), None)
    atr_pct = _number(channel.get("atr_pct"), None)
    if mid is None or price is None or not mid:
        return 0.0
    drawdown_pct = max(0.0, (mid - price) / mid * 100.0)
    if atr_pct is None or atr_pct <= 0:
        return 0.0
    return round(drawdown_pct / atr_pct, 4)


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Read-only observation of WunderTrading grid bots")
    ap.add_argument("--status", action="store_true", help="print grid_status()")
    ap.add_argument("--profiles", action="store_true", help="print grid_profiles()")
    ap.add_argument("--bot", help="observe one bot code")
    ap.add_argument("--history", action="store_true", help="print closed trips")
    args = ap.parse_args(argv)
    if args.status:
        print(json.dumps(grid_status(), indent=2, sort_keys=True))
        return 0
    if args.profiles:
        print(json.dumps(grid_profiles(), indent=2, sort_keys=True))
        return 0
    if args.bot:
        if args.history:
            hist = _positions_history(args.bot)
            trips = [{"close": t["close"],
                      "profitLoss": t["res"].get("profitLoss"),
                      "status": t["res"].get("status")}
                     for t in _closed_round_trips(hist)]
            print(json.dumps(trips, indent=2, sort_keys=True))
            return 0
        obs = observe_all({"0": {"bot_code": args.bot}})
        print(json.dumps(obs, indent=2, sort_keys=True))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
