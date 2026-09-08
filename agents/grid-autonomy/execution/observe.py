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


def _grid_resources_ex():
    """(resources, fetch_ok). fetch_ok=False when the status API call itself
    failed (browser down, session expired, non-JSON body) — a very different
    situation from a healthy empty list, and one the caller must not confuse
    with "bot disappeared"."""
    raw = _api_json("/en/trader/grid_bots/grid?page=1&limit=50")
    if raw is None:
        return [], False
    return _resources(raw), True


def _grid_resources():
    return _grid_resources_ex()[0]


def _exit_fields(res):
    """Enriched exit/risk fields from a grid resource (additive projection).

    Mirrors wtclient ``GridClient.list()`` — exactly the fields
    ``GridClient.set_exits`` edits (live-verified 2026-09-07) — so
    daemon-side bot records can carry the bot's CURRENT exit profile.
    """
    return {
        "takeProfit": res.get("takeProfit"),
        "stopLoss": res.get("stopLoss"),
        "stopLossPnlCompareType": res.get("stopLossPnlCompareType"),
        "trailingStopActivation": res.get("trailingStopActivation"),
        "trailingStopExecute": res.get("trailingStopExecute"),
        "trailingStopPnlCompareType": res.get("trailingStopPnlCompareType"),
        "strategyProfitCondition": res.get("strategyProfitCondition"),
        "strategyStopLossFixedPercentRatio":
            res.get("strategyStopLossFixedPercentRatio"),
        "pumpProtection": res.get("pumpProtection"),
        "pumpProtectionOrderType": res.get("pumpProtectionOrderType"),
    }


def _channel_fields(res):
    """Grid geometry from a grid resource (additive projection, gap-report 2026-09-07).

    Mirrors what ``grid_adapter.compute_upsert`` writes on create/edit so
    adopted bots (which used to land with ``channel=None, upsert=None``)
    gain the deployed geometry after one health cycle. The position
    optimizer's ``revalue_grid()`` then computes real ``delta_drift_pct``
    / ``delta_step_pct`` / ``delta_grids`` deltas instead of zeros.

    Returns the raw resource shape (snake/camel as WT returns it) so the
    daemon merge in ``health_cycle`` can map into both ``bot["channel"]``
    (low/mid/high/step_pct/atr_pct/grids) and ``bot["upsert"]`` (the
    upsert payload shape). Empty when the resource has no geometry keys
    (transient observation glitch — caller must keep the existing values).
    """
    if not isinstance(res, dict):
        return {}
    return {
        "lowPrice": res.get("lowPrice"),
        "highPrice": res.get("highPrice"),
        "midPrice": res.get("midPrice"),
        "gridPercentStep": res.get("gridPercentStep"),
        "gridLevels": res.get("gridLevels"),
        "amountPerTrade": res.get("amountPerTrade"),
        "gridType": res.get("gridType"),
        "gridTradingType": res.get("gridTradingType"),
        "pairCode": ((res.get("pair") or {}).get("code")),
        "exchange": ((res.get("exchange") or {}).get("code")),
    }


def _channel_field_map(raw):
    """Map raw ``_channel_fields`` into the daemon's ``bot["channel"]`` shape
    (the convention ``grid_adapter.compute_upsert`` writes on create).

    Pure function. None-safe: returns {} when ``raw`` is None/empty.
    """
    if not isinstance(raw, dict):
        return {}
    step_pct = raw.get("gridPercentStep")
    try:
        step_pct_x100 = float(step_pct) * 100.0 if step_pct is not None else None
    except (TypeError, ValueError):
        step_pct_x100 = None
    out = {}
    if raw.get("lowPrice") is not None:
        try:
            out["low"] = float(raw["lowPrice"])
        except (TypeError, ValueError):
            pass
    if raw.get("highPrice") is not None:
        try:
            out["high"] = float(raw["highPrice"])
        except (TypeError, ValueError):
            pass
    if raw.get("midPrice") is not None:
        try:
            out["mid"] = float(raw["midPrice"])
        except (TypeError, ValueError):
            pass
    if step_pct_x100 is not None:
        out["step_pct"] = round(step_pct_x100, 4)
    if raw.get("gridLevels") is not None:
        try:
            out["grids"] = int(float(raw["gridLevels"]))
        except (TypeError, ValueError):
            pass
    if raw.get("amountPerTrade") is not None:
        try:
            out["amount_per_trade"] = float(raw["amountPerTrade"])
        except (TypeError, ValueError):
            pass
    return out


def _upsert_field_map(raw):
    """Map raw ``_channel_fields`` into the daemon's ``bot["upsert"]`` shape
    (the WT upsert payload convention).

    Pure function. None-safe. Used by the health-cycle merge so an adopted
    bot's upsert field mirrors what the create payload WOULD have been."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k_in, k_out in (("lowPrice", "lowPrice"),
                        ("highPrice", "highPrice"),
                        ("midPrice", "midPrice"),
                        ("gridPercentStep", "gridPercentStep"),
                        ("gridLevels", "gridLevels"),
                        ("amountPerTrade", "amountPerTrade"),
                        ("gridType", "gridType"),
                        ("gridTradingType", "gridTradingType"),
                        ("pairCode", "pairCode"),
                        ("exchange", "exchange")):
        v = raw.get(k_in)
        if v is not None:
            out[k_out] = v
    return out


def _has_geometry(raw):
    """True when ``raw`` carries enough fields to populate channel/upsert.

    Used to gate the adopted-bot backfill: a transient observation with
    only ``code`` + ``status`` must not clobber the existing geometry
    (a geometry edit could be in flight from a previous cycle)."""
    if not isinstance(raw, dict):
        return False
    return any(raw.get(k) is not None for k in
               ("lowPrice", "highPrice", "midPrice",
                "gridPercentStep", "gridLevels", "amountPerTrade"))


def grid_status():
    """List of active bots: code/status/paperTrading/exchange/pair/pairCode
    + the enriched exit fields (_exit_fields — takeProfit / stopLoss /
    trailing / positions exits) so callers see the CURRENT exit profile."""
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
            **_exit_fields(res),
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


# statuses that close a round-trip with REAL PnL — WT closes
# stop_and_close_all leftovers as "panic_exited" and their profitLoss must
# count toward realized_pnl (verified live 2026-09-06: reachable-history
# vocabulary is exactly {completed, panic_exited}). Same set as
# reliability_grid.CLOSED_STATUSES — keep the two in sync.
CLOSED_STATUSES = ("completed", "panic_exited")


def _closed_round_trips(history):
    """Closed round-trip resources with a UTC close time, newest first."""
    trips = []
    for res in history:
        if not isinstance(res, dict):
            continue
        if res.get("status") not in CLOSED_STATUSES:
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


def _line_pnl(row):
    """Net USD PnL one OPEN grid line would realize if closed now. None
    when the row lacks the fields to compute it honestly.

    Buy-side lines (type falsy): buy qty_in for entry_cost, sell qty_out
    back for exit_cost; remaining mark value = (qty_in − qty_out) × last.
    Sell-side lines (type truthy): sell qty_in for entry_cost, buy qty_out
    back for exit_cost; remaining cover cost = (qty_in − qty_out) × last.
    Entry + exit commissions are included so the number is what the line
    truly nets, not a mark-only approximation. (Verified live 2026-09-06
    on DOGE open rows: qty × last − cost matches the resource's
    unrealizedPnl.pnlFiat aggregate to the cent, while the row's own
    totalProfitLoss mis-scales ~700x and must never be used for this.)
    """
    try:
        last = row.get("lastPrice")
        if last is None:
            return None
        remaining = _number(row.get("totalEntryAmount")) - \
            _number(row.get("totalExitAmount"))
        if remaining <= 0:
            return None  # fully exited leg — nothing left to realize
        entry_cost = _number(row.get("totalEntryCost"))
        exit_cost = _number(row.get("totalExitCost"))
        if row.get("type"):  # sell/short side: entered by selling
            pnl = entry_cost - exit_cost - remaining * last
        else:                # buy/long side
            pnl = remaining * last + exit_cost - entry_cost
        pnl -= _number(row.get("totalEntryCommissionCost"))
        pnl -= _number(row.get("totalExitCommissionCost"))
        return round(pnl, 6)
    except Exception:
        return None


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
                 ladder_full, dd_vs_atr_band, open_lines, open_losing,
                 realized_pnl, trips_completed, trips_panic,
                 realized_pnl_completed, realized_pnl_panic, error?}}`
    for active bots.

    `trips_completed` / `trips_panic` and `realized_pnl_completed` /
    `realized_pnl_panic` split the closed-trip history by close status:
    clean grid round-trips (`completed`) vs stop/close-all exits
    (`panic_exited`). `realized_pnl` stays the TOTAL (completed+panic).

    `open_lines` / `open_losing`: count of open grid lines and how many of
    them would realize a NET loss if closed at the current mark (per-line
    entry/exit cost + commissions — see `_line_pnl`). None when a line's
    data can't be computed honestly; the aggregate `unrealized_pnl`
    remains the backstop signal then.

    `active_bots` maps slot keys to bot dicts carrying at least `bot_code`;
    `channel` and `stagnation_policy` are optional and improve the derived
    fields. Never raises. When the status list itself could not be fetched
    (browser/session down) every bot gets `error: "grid status list
    unavailable …"` so callers can tell blindness from removal.
    """
    resources, list_ok = _grid_resources_ex()
    by_code = {}
    for res in resources:
        code = res.get("code")
        if code:
            by_code[code] = res
    out = {}
    for slot, bot in (active_bots or {}).items():
        out[str(slot)] = _observe_one(bot, by_code, list_ok=list_ok)
    return out


def _observe_one(bot, by_code, list_ok=True):
    """One bot's observation. See observe_all() for the field contract."""
    bot = bot if isinstance(bot, dict) else {}
    bot_code = bot.get("bot_code") or bot.get("code")
    if not bot_code:
        return {"error": "no bot_code", "status": "unknown", "price": None,
                "fills_24h": 0, "realized_ratio": 0.0, "unrealized_pnl": None,
                "ladder_full": False, "dd_vs_atr_band": 0.0,
                "open_lines": 0, "open_losing": 0, "realized_pnl": 0.0,
                "trips_completed": 0, "trips_panic": 0,
                "realized_pnl_completed": 0.0, "realized_pnl_panic": 0.0}
    res = by_code.get(bot_code) or {}
    status = res.get("status") or "unknown"

    open_rows = _positions_open(bot_code)
    history = _positions_history(bot_code)

    # open positions = rows not completed/closed
    open_positions = [r for r in open_rows
                      if (r.get("status") or "").lower() not in
                      ("completed", "closed", "cancelled", "canceled", "deleted")]
    # Authoritative mark PnL is the grid resource's unrealizedPnl.pnlFiat
    # (verified live 2026-09-05): the open-position rows' totalProfitLoss
    # tracks entry commission, not mark PnL, and mis-scales ~10x when divided
    # by PNL_SCALE (that scaling IS correct for positions-history profitLoss).
    # Keep the positions sum only as a fallback for resource payloads without
    # an unrealizedPnl block.
    unrealized = None
    up = res.get("unrealizedPnl") or {}
    if isinstance(up, dict) and up.get("pnlFiat") is not None:
        unrealized = round(_number(up.get("pnlFiat")), 4)
    if unrealized is None and open_positions:
        pnl_sum = sum(_number(r.get("totalProfitLoss")) for r in open_positions)
        unrealized = round(pnl_sum / 10000.0, 4)

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
    realized_pnl = 0.0
    trips_completed = 0
    trips_panic = 0
    realized_completed = 0.0
    realized_panic = 0.0
    for trip in _closed_round_trips(history):
        age = now - trip["close"]
        if 0 <= age <= FILLS_WINDOW_S:
            fills_24h += 1
        # realized USD over the bot's WHOLE life (positions-history
        # profitLoss is PNL-scaled by 10000 — verified live); with the mark
        # PnL this is the cumulative Total PnL the profit-exit targets
        pnl = _number(trip["res"].get("profitLoss")) / 10000.0
        realized_pnl += pnl
        # split by close status so the console/ledger can show how much of
        # the realized total came from clean grid round-trips vs
        # stop/close-all (panic_exited) exits — the two behave differently
        # for reliability accounting (audit-20260906)
        if trip["res"].get("status") == "panic_exited":
            trips_panic += 1
            realized_panic += pnl
        else:
            trips_completed += 1
            realized_completed += pnl
    realized_pnl = round(realized_pnl, 4)
    realized_pnl_completed = round(realized_completed, 4)
    realized_pnl_panic = round(realized_panic, 4)

    policy = bot.get("stagnation_policy") or {}
    expected = _number(policy.get("expected_fills_per_24h"), 0.0)
    realized_ratio = round(fills_24h / expected, 4) if expected > 0 else 0.0

    ladder_full = _ladder_full(bot, res, open_positions)

    dd_vs_atr_band = _dd_vs_atr_band(bot, price)

    # per-line loss state for the optimizer's never-close-at-a-loss gate:
    # open_losing = count of open lines that would realize a NET loss if
    # closed at the current mark. Both go None when any line can't be
    # computed honestly (the aggregate unrealized_pnl is the backstop).
    open_lines, open_losing = 0, 0
    for r in open_positions:
        pnl = _line_pnl(r)
        if pnl is None:
            open_lines, open_losing = None, None
            break
        open_lines += 1
        if pnl < 0:
            open_losing += 1

    obs = {
        "status": status,
        "price": price,
        "fills_24h": fills_24h,
        "realized_ratio": realized_ratio,
        "unrealized_pnl": unrealized,
        "ladder_full": ladder_full,
        "dd_vs_atr_band": dd_vs_atr_band,
        "open_lines": open_lines,
        "open_losing": open_losing,
        "realized_pnl": realized_pnl,
        "trips_completed": trips_completed,
        "trips_panic": trips_panic,
        "realized_pnl_completed": realized_pnl_completed,
        "realized_pnl_panic": realized_pnl_panic,
    }
    if res:
        # current exit profile (additive) — consumed by the position
        # optimizer's exit awareness and projected onto the bot record
        # by the daemon's health cycle
        obs["exits"] = _exit_fields(res)
        # current grid geometry (additive) — same health-cycle merge;
        # populates bot["channel"] / bot["upsert"] for ADOPTED bots
        # (the gap-report: adopted bots had channel=None, upsert=None
        # forever, so revalue_grid ran blind). Only attach when the
        # resource actually carries geometry — a transient observation
        # glitch (status list loaded but no fields) must NOT clobber a
        # non-adopted bot's existing geometry.
        raw = _channel_fields(res)
        if _has_geometry(raw):
            obs["channel"] = _channel_field_map(raw)
            obs["upsert"] = _upsert_field_map(raw)
    else:
        obs["error"] = ("grid status list unavailable (browser/session down)"
                        if not list_ok
                        else "grid resource not found in status list")
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
