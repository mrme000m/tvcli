#!/usr/bin/env python3
"""Grid-bot execution adapter — ticket → WunderTrading payloads → deploy.

Two paths (grid_bot preferred, mcp_strategy fallback):
  build_upsert(ticket, brief, cfg) — pure: grid_bot math (via grid_config)
      translated into the verified /en/trader/grid_bots/upsert payload
      (browser-debug/docs/wt/grid-bot-api.md §9 + grid line geometry).
  deploy_paper / deploy_live — subprocess drivers over wt_browser.py
      (grid create/stop/delete) with --dry-run default; MCP DCA fallback
      via grid_config --send when the session web is unreachable.

Nothing here executes on import. Live functions require explicit
live=True AND guardrails.deploy() having passed (checked by the daemon,
re-asserted here via the ctx argument).
"""
import json
import os
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
WUN_SCRIPTS = os.path.join(ROOT, ".agents", "skills", "wundertrading", "scripts")
sys.path.insert(0, WUN_SCRIPTS)
sys.path.insert(0, os.path.join(HERE, "..", "policy"))

import grid_config  # noqa: E402

GRID_TYPE_MAP = {"long": "long", "short": "short", "neutral": "neutral"}
EXCHANGE_CODE = {"hyperliquid": "HYPERLIQUID_SWAP", "binance": "BINANCE"}
GRID_MARKET = {"hyperliquid": "derivative", "binance": "spot"}


def _fetch_symbol(venue, symbol):
    """Full ticker for public candle APIs (binance needs BTCUSDT, not BTC)."""
    s = (symbol or "").upper().replace("/", "")
    if venue == "binance" and not s.endswith(("USDT", "USDC", "BUSD")):
        return f"{s}USDT"
    return s


def market_for_exchange(exchange_code):
    """gridMarket query param for a profile's exchange (spot vs derivative)."""
    e = (exchange_code or "").upper()
    if e == "BINANCE":
        return "spot"
    if e in ("HYPERLIQUID_SWAP",) or e.endswith("_FUTURES") or \
            e.endswith("_SWAP") or e in ("BITMEX",):
        return "derivative"
    return "spot"


def grid_args(symbol, venue, step_pct, slot_balance, max_alloc,
              band_atr=3.0, step_factor=0.5, min_grids=5, max_grids=30):
    """argparse-compatible namespace mirroring grid_config.py defaults.

    `min_grids`/`max_grids` are the grid-count bounds the daemon threads
    from config `grid_defaults` so adapter and daemon share one source."""
    return types.SimpleNamespace(
        symbol=symbol, exchange=venue, interval="1h", limit=300,
        market="futures" if venue == "hyperliquid" else "spot",
        band_atr=band_atr, step_factor=step_factor,
        step_min=0.1, step_max=2.0, min_grids=min_grids, max_grids=max_grids,
        balance=slot_balance, max_alloc=max_alloc,
        dca_factor=1.5, dca_orders=6, dca_vol_mult=1.4, dca_dev_mult=1.5,
        dca_tp=1.5, ttl=1440, client_id=None, pair_code=None, profiles=None,
        json=False, send=False, yes=False)


def compute_upsert(symbol, venue, price, atr_pct, step_pct, grids,
                   amount_per_trade, grid_type, profile_code, pair_code,
                   band_atr=3.0, leverage=1, exchange_code=None):
    """Pure translator: ATR-band channel + geometric grid lines → upsert JSON.

    `exchange_code` overrides the static venue default (e.g. "BINANCE_FUTURES"
    when the Binance sleeve runs on a futures paper profile)."""
    band = band_atr * (atr_pct or 0.0) / 100.0
    high = price * (1 + band)
    low = price * (1 - band)
    step = (step_pct or 0.1) / 100.0
    # Grid line geometry (client-side, replicate exactly — grid-bot-api.md):
    lines, e = [], low
    guard = 0
    while e <= high and (high - e) / e * 100 >= step_pct and guard < 500:
        lines.append(e)
        e *= (1 + step)
        guard += 1
    lines.append(high)
    below = [ln for ln in lines if ln <= price]
    above = [ln for ln in lines if ln > price]
    closest_low = max(below) if below else lines[0]
    closest_high = min(above) if above else lines[-1]
    return {
        "exchangeCode": exchange_code or EXCHANGE_CODE[venue],
        "pairCode": str(pair_code),
        "profilesCodes": [profile_code],
        "gridType": "interval",
        "gridMethod": "classic",
        "gridTradingType": GRID_TYPE_MAP[grid_type],
        "gridPercentStep": round(step, 6),
        "gridTickStep": None,
        "gridLevels": len(lines),
        "midPrice": round(price, 6),
        "initPrice": round(price, 6),
        "closestHighLevelPrice": round(closest_high, 6),
        "closestLowLevelPrice": round(closest_low, 6),
        "amountPerTrade": amount_per_trade,
        "amountPerTradeType": "base",
        "stopOnOutOfGrid": True,
        "startCondition": "immediate",
        "signalCode": None,
        "maxRequiredAmount": None,
        "leverage": leverage,
        "highPrice": round(high, 6),
        "lowPrice": round(low, 6),
        "stopCondition": "stop_and_close_all",
        "profitCurrencyType": "base",
        "pumpProtection": True,
        "pumpProtectionOrderType": "market",
    }


def build_ticket_payloads(ticket, brief, slot_balance, max_alloc,
                          profile_code, pair_code, exchange_code=None,
                          amount_precision=None, max_affordable_grids=None,
                          min_cost=None, min_grids=None):
    """grid_config math (ATR channel, step, sizing) + upsert translation.

    `exchange_code` (from the selected profile's exchange) overrides the
    static venue default in the upsert payload.
    `amount_precision` (from :2087 market metadata) rounds amountPerTrade
    to the decimals the exchange accepts.
    `min_cost` (from :2087 market metadata `limits.cost.min`) is the minimum
    notional per grid LINE — per-line sizing is raised to it so grid density
    is never degraded for budget reasons.
    `max_affordable_grids`: last-resort fallback (only when even min_cost
    per line breaks the worst-case cap) — widen the step so the channel
    fits at most this many lines."""
    from stagnation import derive_policy  # local import: keeps module light
    symbol = ticket["symbol"]
    venue = ticket["venue"]
    m = brief.get("metrics", {})
    price = m.get("price")
    atr = m.get("atr_pct") or 0.0
    step_mult = ticket.get("step_mult", 1.0) or 1.0
    alloc_mult = ticket.get("max_alloc_mult", 1.0) or 1.0
    args = grid_args(symbol, venue, 0, slot_balance, max_alloc * alloc_mult,
                     min_grids=5 if min_grids is None else min_grids)
    # regime evidence path: reuse build_grid/build_mcp shapes with live metrics
    spread = brief.get("spread_pct")
    grid = grid_config.build_grid(symbol, args, m, ticket.get("regime", "neutral"),
                                  brief.get("evidence", {}), spread)
    # risk step adjustment (geometric mean from the risk team)
    grid["profit_per_grid_pct"] = round(
        min(args.step_max, max(args.step_min, grid["profit_per_grid_pct"] * step_mult)), 3)
    # affordability: widen the step until the grid count fits the budget
    if max_affordable_grids and grid["grids"] > max_affordable_grids >= args.min_grids:
        width = grid["channel"]["width_pct"]
        # the geometry appends the channel high as its last line — fit one fewer
        step_fit = round(width / max(max_affordable_grids - 1, 1), 3)
        step_new = min(args.step_max, max(grid["profit_per_grid_pct"], step_fit))
        if step_new > grid["profit_per_grid_pct"]:
            grid["profit_per_grid_pct"] = step_new
            grid["step_widened_for_affordability"] = True
    # ── SIZING ───────────────────────────────────────────────────────
    # WunderTrading's amountPerTrade (amountPerTradeType "base") is denominated
    # in the market's WT-base currency — ALWAYS a USD-stable on our venues
    # (USDC on Hyperliquid, USDT on Binance; verified via :2087/market
    # currencies.base + the server's own min-trade violations).
    #
    # WORST-CASE IS ONE-SIDED: only the buy-side lines of a long/neutral
    # grid (or sell-side of a short) can fill against us; the other side is
    # take-profit flow closing what the entry side opened. WunderTrading
    # itself sizes accounts this way (verified: a 13-line × 20 HYPE bot ≈
    # $22.6k distributed runs on a $10k paper account). So:
    #   per-line  ≥ exchange min_cost (order-level constraint)
    #   worst_case = per_line × side_lines  ≤ slot × max_alloc (risk cap)
    # Grid DENSITY (line count) is the profit driver (fills/day); we size
    # funds to the strategy, never degrade the geometry to fit a budget.
    alloc_usd = (slot_balance or 0.0) * (max_alloc * alloc_mult)
    if alloc_usd > 0 and price:
        band = args.band_atr * atr / 100.0
        low, high = price * (1 - band), price * (1 + band)
        step_pct = grid["profit_per_grid_pct"]
        lines, e = [], low
        guard = 0
        while e <= high and (high - e) / e * 100 >= step_pct and guard < 500:
            lines.append(e)
            e *= (1 + step_pct / 100.0)
            guard += 1
        lines.append(high)
        grids_n = max(len(lines), 1)
        # buy-side lines for long/neutral (entered near channel mid),
        # sell-side for short — the lines that can fill against us
        side_lines = max(1, (grids_n + 1) // 2)
        per_line = alloc_usd / grids_n
        if min_cost and per_line < min_cost:
            per_line = float(min_cost)  # fund density up to the exchange minimum
        precision = int(amount_precision or 2)
        amount_usd = round(per_line, precision) or round(10 ** -precision, precision)
        grid["grids"] = grids_n
        grid["sizing"]["amount_per_trade"] = amount_usd
        grid["sizing"]["units"] = "usd"
        grid["sizing"]["usd_per_grid"] = amount_usd
        grid["sizing"]["side_lines"] = side_lines
        grid["sizing"]["distributed_notional"] = round(amount_usd * grids_n, 2)
        # worst-case adverse commitment = one side of the channel
        grid["sizing"]["total_commitment_estimate"] = round(
            amount_usd * side_lines, 2)
    mcp = grid_config.build_mcp(symbol, args, m, ticket.get("regime", "neutral"))
    upsert = compute_upsert(
        symbol, venue, price, atr, grid["profit_per_grid_pct"], grid["grids"],
        grid["sizing"]["amount_per_trade"], ticket["grid_type"],
        profile_code, pair_code, exchange_code=exchange_code)
    policy = None
    try:
        from market_regime import fetch_candles
        sym = _fetch_symbol(venue, symbol)
        cl = fetch_candles(venue, sym, "1h", 300,
                           "futures" if venue == "hyperliquid" else "spot")
        policy = derive_policy([c[3] for c in cl], "1h",
                               grid["profit_per_grid_pct"], ticket.get("regime"))
    except Exception as exc:
        policy = {"error": f"history fetch failed: {exc}"}
    # The guardrail bound must cover the ACTUAL worst-case commitment. When
    # the exchange minimum (min_cost) dominates over the risk-multiplied tier,
    # the honest worst-case fraction is what the guard checks — never the
    # fictitiously small tier×risk number.
    worst_frac = 0.0
    if slot_balance:
        worst_frac = grid["sizing"]["total_commitment_estimate"] / slot_balance
    guard_max_alloc = max(max_alloc * alloc_mult, worst_frac)
    return {"grid_bot": grid, "mcp_strategy": mcp, "upsert": upsert,
            "stagnation_policy": policy,
            "guard_ctx": {
                "pair_code": pair_code, "profile_code": profile_code,
                "slot_balance": slot_balance, "max_alloc": guard_max_alloc,
                "total_commitment": grid["sizing"]["total_commitment_estimate"],
                "step_pct": grid["profit_per_grid_pct"], "spread_pct": spread,
                "venue": venue, "grid_type": ticket["grid_type"],
            }}


def _run(cmd, dry_run=True):
    if dry_run:
        return {"ok": True, "dry_run": True, "cmd": cmd}
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return {"ok": p.returncode == 0, "stdout": p.stdout[-2000:],
                "stderr": p.stderr[-1000:], "cmd": cmd}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "cmd": cmd}


def grid_create(upsert_payload, venue, dry_run=True):
    """POST upsert via wt_browser.py (fetch-in-page, CSRF handled)."""
    import tempfile
    # gridMarket must match the PROFILE's exchange (BINANCE_FUTURES paper
    # sleeve → derivative), not the static venue default.
    market = market_for_exchange(upsert_payload.get("exchangeCode")) \
        or GRID_MARKET[venue]
    body = dict(upsert_payload)
    body["gridMarketHint"] = market  # consumed by wt_browser grid create
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(body, f)
        path = f.name
    cmd = [sys.executable, os.path.join(WUN_SCRIPTS, "wt_browser.py"),
           "grid", "create", path]
    res = _run(cmd, dry_run)
    try:
        os.unlink(path)
    except OSError:
        pass
    return res


def grid_stop(bot_code, condition="stop_and_close_all", dry_run=True):
    cmd = [sys.executable, os.path.join(WUN_SCRIPTS, "wt_browser.py"),
           "grid", "stop", bot_code, condition]
    return _run(cmd, dry_run)


def grid_delete(bot_code, dry_run=True):
    cmd = [sys.executable, os.path.join(WUN_SCRIPTS, "wt_browser.py"),
           "grid", "delete", bot_code]
    return _run(cmd, dry_run)


def mcp_send(mcp_payload, dry_run=True):
    """Fallback path: place_strategy_trade DCA/classic via grid_config --send."""
    if dry_run:
        return {"ok": True, "dry_run": True, "payload": mcp_payload}
    return grid_config.send_strategy(mcp_payload)


EXCHANGE_MARKET = {"HYPERLIQUID_SWAP": "derivative", "BINANCE": "spot"}


def grid_edit(bot_code, upsert_payload, dry_run=True):
    """Edit an existing grid bot: POST /en/trader/grid_bots/upsert?code=...

    Geometry (channel/step/levels) applies live on an active bot; sizing
    changes require stop -> edit -> restart per the verified grid-bot API.
    """
    import tempfile
    body = dict(upsert_payload or {})
    body.pop("gridMarketHint", None)
    exchange = body.get("exchangeCode", "")
    market = EXCHANGE_MARKET.get(exchange, "derivative")
    url = f"/en/trader/grid_bots/upsert?code={bot_code}&gridMarket={market}"
    if dry_run:
        cmd = [sys.executable, os.path.join(WUN_SCRIPTS, "wt_browser.py"),
               "api", "POST", url, "--data", f"@{body}"]
        return {"ok": True, "dry_run": True, "cmd": cmd}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(body, f)
        path = f.name
    cmd = [sys.executable, os.path.join(WUN_SCRIPTS, "wt_browser.py"),
           "api", "POST", url, "--data", f"@{path}"]
    res = _run(cmd, dry_run)
    try:
        os.unlink(path)
    except OSError:
        pass
    return res


def grid_status():
    """Thin wrapper over Worker A's observe.grid_status() (best-effort)."""
    try:
        from observe import grid_status as _status
        return _status() or []
    except Exception:
        return []


def grid_profiles():
    """Thin wrapper over Worker A's observe.grid_profiles() (best-effort)."""
    try:
        from observe import grid_profiles as _profiles
        return _profiles() or []
    except Exception:
        return []
