"""Pure Python port of WunderTrading's client-side grid backtest engine.

Verbatim port of the configurator SPA engine (module 534152), matching the
already parity-verified Node reference ``browser-debug/wt-backtest.mjs``
(digit-for-digit against the live UI on 2026-09-04). Engine rules:

- ``FEE`` = 0.002 (0.2%) per closed grid position.
- Grid lines start at ``lowPrice`` and multiply by ``1 + step/100`` while the
  remaining distance to ``highPrice`` covers a full step; ``highPrice`` is
  appended as the last line. The first/last (edge) lines never open
  positions.
- Intra-candle path is a zigzag: a down candle (prev close > close) walks
  ``[high, low, close]``, an up candle walks ``[low, high, close]``.
- Open rules: ``long`` grids buy down-crossings; ``neutral`` longs at or
  below ``midPrice`` and shorts above it; ``two_way`` both sides;
  ``pumpProtection`` flips the side filter (longs open only on up-crossings).
- ``stopOnOutOfGrid`` (interval only) trims leading candles that are not
  fully inside the channel and halts the simulation when a candle breaks
  the channel high/low.
- When the first candle is outside the channel, the start bracket begins
  at the near edge.
- ``infinite`` grids use the candle-window high/low ±1% as bounds.

Pointer mechanics (ported exactly, including the platform's quirks): the
simulation tracks two level indices — the down-cross watch (``C``) and the
up-cross watch (``_``). Crossings are only detected at those two watched
levels, so a price that wanders several levels away within one candle can
skip intermediate crossings; a dip below the bottom level leaves the up-watch
index negative and stops up-cross detection (platform behavior, verified in
the SPA bundle).

The engine is pure: no I/O, no network, stdlib only.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

LONG = "long"
SHORT = "short"
NEUTRAL = "neutral"
TWO_WAY = "two_way"
INTERVAL = "interval"
INFINITE = "infinite"

FEE = 0.002  # per closed grid position (the "-.002" constant in the bundle)


def _js_round(value: float, digits: int) -> float:
    """JavaScript ``Math.round`` semantics (half away toward +infinity)."""
    factor = 10 ** digits
    return math.floor(value * factor + 0.5) / factor


def _num(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _can_open_long(
    level: dict[str, Any],
    prev_price: float,
    mid_price: float | None,
    method: str,
    pump: bool,
) -> bool:
    """Port of ``canOpenLong`` (JS comparisons against undefined are False)."""
    ok = not level["long"]
    if method != TWO_WAY:
        if pump:
            ok = ok and (level["price"] > prev_price if prev_price is not None else False)
        else:
            ok = ok and (level["price"] < prev_price if prev_price is not None else False)
        if method == NEUTRAL:
            ok = ok and (level["price"] <= mid_price if mid_price is not None else False)
        else:
            ok = ok and method == LONG
    return ok


def _can_open_short(
    level: dict[str, Any],
    prev_price: float,
    mid_price: float | None,
    method: str,
    pump: bool,
) -> bool:
    """Port of ``canOpenShort``."""
    ok = not level["short"]
    if method != TWO_WAY:
        if pump:
            ok = ok and (level["price"] < prev_price if prev_price is not None else False)
        else:
            ok = ok and (level["price"] > prev_price if prev_price is not None else False)
        if method == NEUTRAL:
            ok = ok and (level["price"] > mid_price if mid_price is not None else False)
        else:
            ok = ok and method == SHORT
    return ok


def _normalize_candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Accept ``:2087`` items (``timestamp``) or engine bars (``time``)."""
    out: list[dict[str, Any]] = []
    for candle in candles:
        time_ms = candle.get("time", candle.get("timestamp"))
        if time_ms is None or candle.get("high") is None or candle.get("low") is None or candle.get("close") is None:
            raise ValueError("candles need time/timestamp, high, low and close")
        out.append(
            {
                "time": float(time_ms),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
            }
        )
    return out


def _empty_result(date_started: str, message: str) -> dict[str, Any]:
    return {
        "dateStarted": date_started,
        "error": message,
        "pnl": 0,
        "pnlFiat": 0,
        "unrealizedPnl": 0,
        "unrealizedPnlFiat": 0,
        "totalResult": 0,
        "totalResultFiat": 0,
        "positionsLong": 0,
        "positionsShort": 0,
        "unrealizedPositionsLong": 0,
        "unrealizedPositionsShort": 0,
    }


def run_backtest(cfg: dict[str, Any], candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the grid backtest engine.

    ``cfg`` is the upsert-style config (the same payload POSTed to
    ``/en/trader/grid_bots/upsert``); ``candles`` are ``:2087/ohlc`` items.
    Returns the summary dict the web UI renders (``pnl``/``unrealizedPnl``/
    ``totalResult`` in percent, ``*Fiat`` in quote amounts, position counts).
    """
    method = cfg.get("gridTradingType") or cfg.get("gridMethod") or NEUTRAL
    grid_type = cfg.get("gridType") or INTERVAL
    step_pct = _num(cfg.get("percents"))
    if step_pct is None:
        raw_step = _num(cfg.get("gridPercentStep"))
        step_pct = raw_step * 100.0 if raw_step is not None else None
    if step_pct is None or step_pct <= 0:
        raise ValueError("gridPercentStep (decimal) or percents (percent) is required and must be > 0")

    amount = _num(cfg.get("amountPerTrade"), 20.0) or 20.0
    mid_price = _num(cfg.get("midPrice"))
    decimals_qty = int(cfg.get("decimalsQty") or 2)
    notional_price = _num(cfg.get("notionalPrice"), 1.0) or 1.0
    pump = bool(cfg.get("pumpProtection"))
    stop_out = bool(cfg.get("stopOnOutOfGrid")) and grid_type == INTERVAL

    bars = _normalize_candles(candles)
    high = _num(cfg.get("highPrice"))
    low = _num(cfg.get("lowPrice"))
    explicit_levels = cfg.get("gridLevels")
    if explicit_levels:
        explicit_levels = [float(p) for p in explicit_levels]
        low = low if low is not None else explicit_levels[0]
        high = high if high is not None else explicit_levels[-1]
    if high is None or low is None:
        raise ValueError("highPrice and lowPrice are required (or provide gridLevels)")

    date_started = datetime.fromtimestamp(bars[0]["time"] / 1000.0, tz=timezone.utc).isoformat()

    if stop_out:
        # Trim leading candles up to the last one not fully inside the channel.
        for t in range(len(bars) - 1, -1, -1):
            candle = bars[t]
            if not (candle["low"] > low and candle["high"] < high):
                bars = bars[t + 1 :]
                break
    if len(bars) < 2:
        return _empty_result(date_started, "no candles inside the channel (stopOnOutOfGrid trimmed everything)")

    price = bars[0]["close"]  # D — walking price pointer
    open_pos: dict[str, dict[str, Any]] = {}  # N — open positions by key
    closed = {"long": 0, "short": 0}  # I
    realized = {"fiat": 0.0, "percents": 0.0}  # G
    first_outside = bars[0]["high"] > high or bars[0]["low"] < low  # M
    rest = bars[1:]

    # -- build levels ------------------------------------------------------
    levels: list[dict[str, Any]] = []
    upper_i: int | None = None  # _
    lower_i: int | None = None  # C
    if explicit_levels:
        last = len(explicit_levels) - 1
        for b_idx, level_price in enumerate(explicit_levels):
            if (
                not first_outside
                and b_idx != last
                and price > level_price
                and price <= explicit_levels[b_idx + 1]
            ):
                upper_i = b_idx + 1
                lower_i = b_idx
            levels.append({"price": level_price, "long": False, "short": False, "trades": {}})
    else:
        q = low
        u = 0
        while step_pct <= (high - q) / q * 100.0:
            if (
                not first_outside
                and u != 0
                and q >= price
                and levels
                and levels[u - 1]["price"] < price
            ):
                upper_i = u
                lower_i = u - 1
            levels.append({"price": q, "long": False, "short": False, "trades": {}})
            u += 1
            q *= 1 + step_pct / 100.0
        levels.append({"price": high, "long": False, "short": False, "trades": {}})

    h_idx = len(levels) - 1  # H — top level index
    w_idx = len(levels) - 2  # W
    if first_outside:
        if low > rest[0]["low"]:
            upper_i = 1
            lower_i = 0
        else:
            upper_i = h_idx
            lower_i = w_idx

    top_price = levels[h_idx]["price"]  # X
    bottom_price = levels[0]["price"]  # Z

    trades: list[dict[str, Any]] = []  # P
    equity: dict[float, float] = {}  # Y

    def unrealized_percents(close: float) -> float:
        total = 0.0
        for pos in open_pos.values():
            if pos.get("long"):
                total += (close - pos["price"]) / pos["price"]
            if pos.get("short"):
                total += -1 * (close - pos["price"]) / pos["price"]
        return total

    def level_at(idx: int | None) -> dict[str, Any] | None:
        if idx is None or idx < 0 or idx > h_idx:
            return None
        return levels[idx]

    for candle in rest:
        c_high, c_low, c_close, c_time = candle["high"], candle["low"], candle["close"], candle["time"]
        breaks_top = c_high > top_price
        breaks_bottom = c_low < bottom_price
        if (breaks_top or breaks_bottom) and stop_out:
            break
        # zigzag intra-candle path
        path = [c_high, c_low, c_close] if price > c_close else [c_low, c_high, c_close]
        for ae in path:
            while price != ae:
                crossed: int | None = None
                down_level = level_at(lower_i)
                up_level = level_at(upper_i)
                if down_level is not None and down_level["price"] >= ae and down_level["price"] < price:
                    # moving DOWN through the watched level
                    crossed = lower_i  # ie
                    lower_i = max(lower_i - 1, 0)
                    upper_i = (upper_i - 1) if upper_i is not None else None
                    above = level_at(crossed + 1)
                    if above is not None and above["short"]:
                        entry = above["price"]
                        profit = -1 * (down_level["price"] - entry) / entry - FEE
                        realized["percents"] += profit
                        realized["fiat"] += profit * amount
                        above["short"] = False
                        open_pos.pop(f"{entry}-short", None)
                        closed["short"] += 1
                        down_level["trades"].setdefault(c_time, {"short": [], "long": []})
                        trades.append(
                            {"side": LONG, "strategy": SHORT, "timestamp": c_time / 1e3, "price": down_level["price"]}
                        )
                        down_level["trades"][c_time]["short"].append(f"CS [{crossed - 1}]")
                elif up_level is not None and up_level["price"] <= ae and up_level["price"] > price:
                    # moving UP through the watched level
                    crossed = upper_i  # ie
                    upper_i = min(upper_i + 1, h_idx)
                    lower_i = (lower_i + 1) if lower_i is not None else None
                    below = level_at(crossed - 1)
                    if below is not None and below["long"]:
                        entry = below["price"]
                        profit = (up_level["price"] - entry) / entry - FEE
                        realized["percents"] += profit
                        realized["fiat"] += profit * amount
                        below["long"] = False
                        open_pos.pop(f"{entry}-long", None)
                        closed["long"] += 1
                        up_level["trades"].setdefault(c_time, {"short": [], "long": []})
                        trades.append(
                            {"side": SHORT, "strategy": LONG, "timestamp": c_time / 1e3, "price": up_level["price"]}
                        )
                        up_level["trades"][c_time]["long"].append(f"CL [{crossed}]")
                if crossed:
                    # a level was crossed: try to open (never at the edge lines)
                    fe = crossed
                    level = levels[fe]
                    if fe != h_idx and fe != 0 and _can_open_long(level, price, mid_price, method, pump):
                        level["long"] = True
                        open_pos[f"{level['price']}-long"] = {"price": level["price"], "long": True}
                        level["trades"].setdefault(c_time, {"short": [], "long": []})
                        trades.append(
                            {"side": LONG, "strategy": LONG, "timestamp": c_time / 1e3, "price": level["price"]}
                        )
                        level["trades"][c_time]["long"].append("OL")
                    if fe != h_idx and fe != 0 and _can_open_short(level, price, mid_price, method, pump):
                        level["short"] = True
                        open_pos[f"{level['price']}-short"] = {"price": level["price"], "short": True}
                        level["trades"].setdefault(c_time, {"short": [], "long": []})
                        trades.append(
                            {"side": SHORT, "strategy": SHORT, "timestamp": c_time / 1e3, "price": level["price"]}
                        )
                        level["trades"][c_time]["short"].append("OS")
                    price = level["price"]
                else:
                    price = ae
        equity[c_time] = realized["percents"] + unrealized_percents(c_close)

    last_close = rest[-1]["close"] if rest else bars[-1]["close"]
    unrealized = {"fiat": 0.0, "percents": 0.0}
    open_counts = {"long": 0, "short": 0}
    for level in levels:
        if level["long"]:
            pnl = (last_close - level["price"]) / level["price"]
            unrealized["percents"] += pnl
            unrealized["fiat"] += pnl * amount
            open_counts["long"] += 1
        if level["short"]:
            pnl = -1 * (last_close - level["price"]) / level["price"]
            unrealized["percents"] += pnl
            unrealized["fiat"] += pnl * amount
            open_counts["short"] += 1

    total = {"percents": realized["percents"] + unrealized["percents"], "fiat": realized["fiat"] + unrealized["fiat"]}

    result: dict[str, Any] = {
        "dateStarted": date_started,
        "pnl": _js_round(100 * realized["percents"], 2),
        "pnlFiat": _js_round(realized["fiat"], decimals_qty),
        "unrealizedPnl": _js_round(100 * unrealized["percents"], 2),
        "unrealizedPnlFiat": _js_round(unrealized["fiat"], decimals_qty),
        "totalResult": _js_round(100 * total["percents"], 2),
        "totalResultFiat": _js_round(total["fiat"], decimals_qty),
        "unrealizedPositionsLong": open_counts["long"],
        "unrealizedPositionsShort": open_counts["short"],
        "positionsShort": closed["short"],
        "positionsLong": closed["long"],
        "gridMethod": method,
        "gridType": grid_type,
        "midPrice": mid_price,
        "percents": step_pct,
        "gridLevels": [level["price"] for level in levels],
        "gridLevelsQty": len(levels),
        "tradesCount": len(trades),
        "trades": trades,
    }
    if notional_price != 1:
        result["pnlUsd"] = _js_round(realized["fiat"] * notional_price, 2)
        result["unrealizedPnlUsd"] = _js_round(unrealized["fiat"] * notional_price, 2)
        result["totalResultUsd"] = _js_round(total["fiat"] * notional_price, 2)
    return result


def summary(result: dict[str, Any]) -> dict[str, Any]:
    """Compact summary (port of the Node ``summary()`` helper)."""
    keys = [
        "percents",
        "gridType",
        "gridMethod",
        "totalResult",
        "pnl",
        "unrealizedPnl",
        "positionsLong",
        "positionsShort",
        "unrealizedPositionsLong",
        "unrealizedPositionsShort",
        "gridLevelsQty",
        "tradesCount",
    ]
    return {key: result.get(key) for key in keys}


def build_input(cfg: dict[str, Any], candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the engine input from an upsert config + fetched candles.

    Port of the Node ``buildInput`` data assembly (no fetch): derives the
    percent step, resolves ``infinite`` bounds from the candle window
    (hi/lo ±1%), and maps the upsert fields onto the engine's input shape.
    """
    grid_type = cfg.get("gridType") or INTERVAL
    method = cfg.get("gridTradingType") or cfg.get("gridMethod") or NEUTRAL
    step_pct = _num(cfg.get("percents"))
    if step_pct is None:
        raw_step = _num(cfg.get("gridPercentStep"))
        step_pct = raw_step * 100.0 if raw_step is not None else None
    if step_pct is None or step_pct <= 0:
        raise ValueError("gridPercentStep (decimal) or percents (percent) is required and must be > 0")
    low = _num(cfg.get("lowPrice"))
    high = _num(cfg.get("highPrice"))
    mid = _num(cfg.get("midPrice"))
    bars = _normalize_candles(candles)
    if grid_type == INFINITE:
        hi = max(bar["high"] for bar in bars)
        lo = min(bar["low"] for bar in bars)
        high = hi * 1.01
        low = lo * 0.99
    return {
        "gridType": grid_type,
        "gridTradingType": method,
        "percents": step_pct,
        "amountPerTrade": _num(cfg.get("amountPerTrade"), 20.0),
        "midPrice": mid,
        "highPrice": high,
        "lowPrice": low,
        "decimalsQty": int(cfg.get("decimalsQty") or 4),
        "stopOnOutOfGrid": bool(cfg.get("stopOnOutOfGrid")),
        "pumpProtection": bool(cfg.get("pumpProtection")),
        "notionalPrice": _num(cfg.get("notionalPrice"), 1.0),
    }


__all__ = [
    "FEE",
    "build_input",
    "run_backtest",
    "summary",
]
