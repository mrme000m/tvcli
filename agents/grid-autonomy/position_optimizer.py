#!/usr/bin/env python3
"""Position optimizer — per-bot grid revaluation + exit-profile engine.

The rescreen (10 min) and the slot optimizer (2–5 min) manage WHICH token
a slot trades and how idle capital moves. This module manages the deployed
position itself: for a bot that is already live on WunderTrading it
re-analyzes the market, REVALUES the grid geometry (ATR-band channel +
step + line count), evaluates the exit profile (take-profit /
stop-loss-risk-cap / trailing / per-position trailing) and produces a
RECOMMENDATION record the daemon (or a human via the console) can act on.

Design contract (mirrors optimizer.py):

  * Pure decision functions — revalue_grid / expected_profit_delta /
    expected_delta_for_channel / evaluate_exits / make_recommendation —
    take plain dicts + numbers, do zero I/O, and are unit-testable.
  * The PositionOptimizer class takes INJECTABLE dependencies
    (journal_fn / persist_fn / fetch_candles_fn / now_fn) so tests never
    touch network, WunderTrading or PocketBase. market_regime /
    stagnation are imported lazily inside methods, never at import time —
    this module imports standalone with zero side effects.
  * Never raises out of analyze_bot / cycle / post_deploy: on any fetch
    or metrics failure the bot is skipped (None / omitted) — fail-soft.

Recommendations: keep | recenter | widen | narrow | resize |
revalue-grid | add-take-profit | add-trailing | add-stop-loss.

Grids are mean-reversion: closing at a loss is normally FORBIDDEN, so
stop_loss_usd is only ever set when the config explicitly enables it and
even then only as a wide risk cap (>= 15% of slot balance).

Usage:
  from position_optimizer import PositionOptimizer   # wired by daemon.py
  po = PositionOptimizer(cfg, journal_fn=log, persist_fn=pb_write)
  recs = po.cycle(state["active_bots"], dry_run=True)
"""
import os
import sys
import time
import uuid
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
WUN_SCRIPTS = os.path.normpath(os.path.join(
    HERE, "..", "..", ".agents", "skills", "wundertrading", "scripts"))

# ── config defaults (config.yaml `position_optimizer:` section overrides) ──
POSITION_OPTIMIZER_DEFAULTS = {
    "enabled": True,
    "interval_min": 15,          # cadence the daemon calls cycle() at
    "cooldown_min": 60,          # per-bot re-analysis cooldown
    "min_improvement_pct": 2.0,  # journal/persist gate on expected_delta
    "apply": False,              # advisory by default — never auto-edit WT
    "max_apply_per_day": 4,      # persisted recommendations per calendar day
    "stop_loss_enabled": False,  # grids are mean-reversion: off by default
    "take_profit_pct": 0.10,     # × slot balance = USD profit-exit target
    "trailing_activation_pct": 5.0,   # cumulative PnL % of slot to arm trail
    "trailing_execute_pct": 2.0,      # give-back % that executes the trail
    "positions_trailing": True,      # per-position trailing preference
    "band_atr": 3.0,             # ATR multiples each side of price
    "drift_steps": 2.0,         # recenter after N × step_pct drift
    "atr_change_pct": 15.0,     # widen/narrow after N% channel-width change
}

# regimes where per-position trailing recycles capital fastest
TRAILING_REGIMES = ("neutral", "chop", "choppy", "range")

STOP_LOSS_PCT_FLOOR = 0.15   # a SL must be >= 15% of slot (wide risk cap)
TP_TRIGGER_RATIO = 0.6      # TP when realized >= 60% of target
TRAIL_REALIZED_RATIO = 0.3  # trail needs realized_ratio >= 0.3
FILLS_HEALTHY_RATIO = 0.5   # healthy = fills_24h >= 0.5 × expected
DD_BAND_TRIGGER = 1.5       # drawdown vs ATR band → full revalue
WIDEN_MAX_GRIDS = 30        # never widen beyond this many lines
NARROW_MIN_GRIDS = 8        # never narrow below this many lines


# ── pure helpers ──────────────────────────────────────────────────────
def _f(v, default=0.0):
    """float() that never raises; None/absent → default."""
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _slot_balance(bot):
    """Slot balance in USD from a bot record (0.0 when unknown)."""
    if not isinstance(bot, dict):
        return 0.0
    for key in ("slot_balance", "balance", "slot_usd"):
        v = bot.get(key)
        if v:
            return _f(v)
    slot = bot.get("slot")
    if isinstance(slot, dict) and slot.get("balance"):
        return _f(slot["balance"])
    return 0.0


def _expected_fills(bot):
    """Expected fills/24h from the bot's stagnation policy (0.0 unknown)."""
    pol = (bot.get("stagnation_policy") or {}) \
        if isinstance(bot, dict) else {}
    return _f(pol.get("expected_fills_per_24h"))


def _regime(bot):
    ticket = bot.get("ticket") if isinstance(bot, dict) else None
    if isinstance(ticket, dict):
        return ticket.get("regime")
    return None


# ── pure: grid revaluation ────────────────────────────────────────────
def revalue_grid(price, atr_pct, step_pct, grids, amount_per_trade,
                 band_atr=3.0, deployed_mid=None, deployed_step_pct=None,
                 deployed_grids=None, deployed_low=None, deployed_high=None):
    """Re-derive the ATR-band channel + geometric grid for a live bot.

    Same geometry as execution/grid_adapter.compute_upsert: band =
    band_atr × atr_pct (percent) each side of price; lines start at low
    and multiply by (1 + step_pct/100) while they stay >= step_pct below
    the high; the high itself is always the last line. "grids" in the
    result is the fresh LINE COUNT (the `grids` argument is the deployed
    count, kept explicit so the deployed-vs-revalued deltas are pure).

    Deployed comparison inputs are all optional/None-safe.
    """
    price = _f(price)
    atr_pct = _f(atr_pct)
    step_pct = _f(step_pct)
    amount_per_trade = _f(amount_per_trade)
    band = _f(band_atr, 3.0) * atr_pct / 100.0

    low = price * (1 - band)
    high = price * (1 + band)
    lines = []
    if low > 0 and step_pct > 0:
        e = low
        while e <= high and (high - e) / e * 100 >= step_pct:
            lines.append(e)
            e *= (1 + step_pct / 100)
        lines.append(high)
    grids_new = len(lines)

    channel_width_pct = (high - low) / low * 100 if low > 0 else 0.0
    deployed_width_pct = None
    if deployed_low and deployed_high and deployed_low > 0:
        deployed_width_pct = (deployed_high - deployed_low) \
            / deployed_low * 100

    delta_drift_pct = 0.0
    if deployed_mid:
        delta_drift_pct = (price - _f(deployed_mid)) / _f(deployed_mid) * 100

    return {
        "low": round(low, 8),
        "mid": round(price, 8),
        "high": round(high, 8),
        "step_pct": step_pct,
        "grids": grids_new,
        "amount_per_trade": amount_per_trade,
        "band_pct": round(band * 100, 4),
        "channel_width_pct": round(channel_width_pct, 4),
        "deployed_width_pct": (round(deployed_width_pct, 4)
                               if deployed_width_pct is not None else None),
        "delta_drift_pct": round(delta_drift_pct, 4),
        "delta_step_pct": (step_pct - _f(deployed_step_pct)
                           if deployed_step_pct is not None else 0.0),
        "delta_grids": (grids_new - int(_f(deployed_grids, grids_new))
                        if deployed_grids is not None else 0),
        "grid_lines": [round(x, 8) for x in lines],
    }


# ── pure: expected-profit deltas ──────────────────────────────────────
def expected_profit_delta(old_fills_per_24h, new_fills_per_24h,
                          profit_per_fill_usd):
    """Expected-profit change (pct) from a fill-rate change.

    EV = fills_per_24h × profit_per_fill; returns (new−old)/old as a
    percentage. Guards div-by-zero (no history / no per-fill profit →
    0.0 — never a spurious recommendation driver).
    """
    old_ev = _f(old_fills_per_24h) * _f(profit_per_fill_usd)
    new_ev = _f(new_fills_per_24h) * _f(profit_per_fill_usd)
    if old_ev <= 0:
        return 0.0
    return round((new_ev - old_ev) / old_ev * 100, 4)


def expected_delta_for_channel(deployed_width_pct, new_width_pct,
                               expected_fills_per_24h, profit_per_fill_usd):
    """Simpler channel-only EV delta (pct).

    At constant volatility the fill rate scales ~inversely with channel
    width (the same swing crosses fewer lines in a wider band), so a
    widen trades per-fill profit for fill count and vice versa.
    Div-by-zero / unknown inputs → 0.0.
    """
    dw = _f(deployed_width_pct)
    nw = _f(new_width_pct)
    old_ev = _f(expected_fills_per_24h) * _f(profit_per_fill_usd)
    if dw <= 0 or nw <= 0 or old_ev <= 0:
        return 0.0
    new_fills = _f(expected_fills_per_24h) * dw / nw
    new_ev = new_fills * _f(profit_per_fill_usd)
    return round((new_ev - old_ev) / old_ev * 100, 4)


# ── pure: exit profile ────────────────────────────────────────────────
def evaluate_exits(bot, metrics, obs, cfg):
    """Evaluate the exit profile for one deployed bot.

    Mean-reversion grids normally forbid closing at a loss:
    stop_loss_usd is ONLY set when cfg["stop_loss_enabled"] is True, and
    even then it is a wide risk cap at STOP_LOSS_PCT_FLOOR × slot balance.
    """
    obs = obs or {}
    reasons = []
    out = {
        "take_profit_usd": None,
        "stop_loss_usd": None,
        "trailing_activation_pct": None,
        "trailing_execute_pct": None,
        "positions_trailing": False,
        "reasons": reasons,
    }

    slot_balance = _slot_balance(bot)
    realized = _f(obs.get("realized_pnl"))
    unrealized = _f(obs.get("unrealized_pnl"))
    realized_ratio = _f(obs.get("realized_ratio"))
    fills_24h = obs.get("fills_24h")
    expected = _expected_fills(bot)

    # take-profit: realized profit >= 60% of the slot target
    tp_pct = _f(cfg.get("take_profit_pct"))
    if slot_balance > 0 and tp_pct > 0:
        target = tp_pct * slot_balance
        if realized >= TP_TRIGGER_RATIO * target:
            out["take_profit_usd"] = round(target, 2)
            reasons.append(
                f"take-profit: realized ${realized:.2f} >= "
                f"{TP_TRIGGER_RATIO:.0%} of ${target:.2f} target")

    # stop-loss: wide risk cap only, and only when explicitly enabled
    if cfg.get("stop_loss_enabled") and slot_balance > 0:
        level = -STOP_LOSS_PCT_FLOOR * slot_balance
        out["stop_loss_usd"] = round(level, 2)
        reasons.append(
            f"stop-loss: risk cap at {STOP_LOSS_PCT_FLOOR:.0%} of "
            f"${slot_balance:.2f} slot (cumulative PnL <= "
            f"${level:.2f})")

    # portfolio trailing: cumulative PnL over activation AND realized share
    act_pct = _f(cfg.get("trailing_activation_pct"))
    cum = realized + unrealized
    if slot_balance > 0 and act_pct > 0 \
            and cum >= act_pct / 100.0 * slot_balance \
            and realized_ratio >= TRAIL_REALIZED_RATIO:
        out["trailing_activation_pct"] = act_pct
        out["trailing_execute_pct"] = _f(cfg.get("trailing_execute_pct"))
        reasons.append(
            f"trailing: cumulative ${cum:.2f} >= {act_pct}% of slot "
            f"(realized_ratio {realized_ratio:.2f})")

    # per-position trailing: healthy fills + mean-reversion regime
    regime = _regime(bot)
    healthy = (fills_24h is not None
               and _f(fills_24h) >= FILLS_HEALTHY_RATIO * expected)
    if healthy and regime in TRAILING_REGIMES:
        out["positions_trailing"] = True
        reasons.append(
            f"positions-trailing: fills_24h "
            f"{_f(fills_24h):.0f} >= {FILLS_HEALTHY_RATIO}× expected "
            f"{expected:.0f} in {regime} regime")

    return out


# ── pure: recommendation ──────────────────────────────────────────────
def make_recommendation(bot, revalue, metrics, obs, exits, cfg,
                        spread_pct=None, min_cost=None):
    """Classify one revaluation into a recommendation record.

    Precedence (documented, deterministic): out-of-channel geometry
    (revalue-grid / recenter) > widen > narrow > resize > exit adds
    (take-profit > trailing > stop-loss) > keep.
    """
    obs = obs or {}
    metrics = metrics or {}
    step_pct = _f(revalue.get("step_pct"))
    drift = _f(revalue.get("delta_drift_pct"))
    dd_band = obs.get("dd_vs_atr_band")
    dd_band = None if dd_band is None else _f(dd_band)
    grids = int(_f(revalue.get("grids")))
    deployed_grids = grids - int(_f(revalue.get("delta_grids")))
    amount_per_trade = _f(revalue.get("amount_per_trade"))
    expected = _expected_fills(bot)
    obs_fills = obs.get("fills_24h")
    slot_balance = _slot_balance(bot)
    realized = _f(obs.get("realized_pnl"))
    regime = _regime(bot)

    drift_thr = _f(cfg.get("drift_steps"), 2.0) * step_pct
    atr_chg = _f(cfg.get("atr_change_pct"), 15.0)
    dw = revalue.get("deployed_width_pct")
    nw = _f(revalue.get("channel_width_pct"))
    width_chg_pct = ((nw - _f(dw)) / _f(dw) * 100) if dw else 0.0

    profit_per_fill = amount_per_trade * step_pct / 100.0

    recommendation = "keep"
    reason = None
    if dd_band is not None and dd_band > DD_BAND_TRIGGER:
        recommendation = "revalue-grid"
        reason = (f"drawdown {dd_band:.2f}× the ATR band — full "
                  f"revaluation of channel + step needed")
    elif drift_thr > 0 and abs(drift) >= drift_thr:
        recommendation = "recenter"
        reason = (f"price drifted {drift:+.2f}% vs deployed mid "
                  f"(threshold ±{drift_thr:.2f}% = "
                  f"{_f(cfg.get('drift_steps'), 2.0):g}× step {step_pct:g}%)")
    elif dw and width_chg_pct > atr_chg and grids <= WIDEN_MAX_GRIDS:
        recommendation = "widen"
        reason = (f"channel {width_chg_pct:+.1f}% wider than deployed "
                  f"(ATR growth > {atr_chg:g}%) at {grids} lines")
    elif dw and width_chg_pct < -atr_chg and grids >= NARROW_MIN_GRIDS:
        recommendation = "narrow"
        reason = (f"channel {width_chg_pct:+.1f}% narrower than deployed "
                  f"(ATR shrank > {atr_chg:g}%) at {grids} lines")
    elif (min_cost and amount_per_trade > 0
            and amount_per_trade < _f(min_cost)) or obs.get("ladder_full"):
        recommendation = "resize"
        detail = ("full ladder" if obs.get("ladder_full")
                  else f"amount_per_trade ${amount_per_trade:.2f} below "
                       f"per-line floor ${_f(min_cost):.2f}")
        reason = f"resize grid sizing: {detail}"
    elif exits.get("take_profit_usd") is not None:
        recommendation = "add-take-profit"
        reason = (f"profit exit ready at ${exits['take_profit_usd']:.2f} "
                  f"(realized ${realized:.2f})")
    elif exits.get("trailing_activation_pct") is not None:
        recommendation = "add-trailing"
        reason = (f"trail armed at "
                  f"{exits['trailing_activation_pct']:g}% activation")
    elif exits.get("stop_loss_usd") is not None:
        recommendation = "add-stop-loss"
        reason = (f"risk cap ${exits['stop_loss_usd']:.2f} suggested")

    # expected_delta_pct — positive only when a change is recommended
    delta = 0.0
    if recommendation == "keep":
        delta = 0.0
    elif recommendation in ("recenter", "revalue-grid"):
        # out-of-channel stalls fills: expect recovery to the token's
        # own expected rate from what is actually being observed
        delta = expected_profit_delta(obs_fills, expected, profit_per_fill)
    elif recommendation in ("widen", "narrow"):
        delta = expected_delta_for_channel(dw, nw, expected, profit_per_fill)
    elif recommendation == "resize":
        if deployed_grids and deployed_grids > 0 and expected > 0:
            delta = expected_profit_delta(
                expected, expected * grids / deployed_grids, profit_per_fill)
    elif recommendation == "add-take-profit":
        if slot_balance > 0:
            delta = (exits["take_profit_usd"] - realized) \
                / slot_balance * 100
        delta = max(delta, 0.0)
    elif recommendation == "add-trailing":
        delta = _f(cfg.get("trailing_execute_pct"), 2.0)
    elif recommendation == "add-stop-loss":
        delta = 0.0  # risk containment, not EV
    delta = round(max(delta, 0.0), 4)

    # confidence heuristic — more evidence, higher score
    conf = 0.0
    if metrics.get("price") is not None and metrics.get("atr_pct") is not None:
        conf += 0.30
    if obs_fills is not None:
        conf += 0.20
        if expected > 0 and _f(obs_fills) >= FILLS_HEALTHY_RATIO * expected:
            conf += 0.15
    if (obs.get("status") or "active") != "error":
        conf += 0.15
    if (drift_thr > 0 and abs(drift) >= drift_thr) \
            or (dd_band is not None and dd_band > DD_BAND_TRIGGER) \
            or (dw and abs(width_chg_pct) >= atr_chg):
        conf += 0.20
    conf = round(min(max(conf, 0.0), 1.0), 2)

    if reason is None:
        reason = (f"channel aligned: drift {drift:+.2f}% within "
                  f"±{drift_thr:.2f}%, width change {width_chg_pct:+.1f}%")
    rationale = (f"{reason}. Expected 24h fills {expected:.0f} at "
                 f"~${profit_per_fill:.4f}/fill; current fills_24h "
                 f"{_f(obs_fills):.0f}.")

    return {
        "slot": None,  # filled by analyze_bot
        "venue": (bot or {}).get("venue"),
        "symbol": (bot or {}).get("symbol"),
        "bot_code": (bot or {}).get("bot_code"),
        "trigger": None,  # filled by analyze_bot
        "price": metrics.get("price"),
        "atr_pct": metrics.get("atr_pct"),
        "regime": regime,
        "spread_pct": spread_pct,
        "revalue": revalue,
        "exit_profile": {
            "take_profit_usd": exits.get("take_profit_usd"),
            "stop_loss_usd": exits.get("stop_loss_usd"),
            "trailing_activation_pct": exits.get("trailing_activation_pct"),
            "trailing_execute_pct": exits.get("trailing_execute_pct"),
            "positions_trailing": bool(exits.get("positions_trailing")),
        },
        "recommendation": recommendation,
        "action": {
            "type": "edit",
            "payload": _edit_payload(bot, revalue, exits),
            "apply": bool(cfg.get("apply", False)),
        },
        "expected_delta_pct": delta,
        "confidence": conf,
        "rationale": rationale,
    }


def _edit_payload(bot, revalue, exits):
    """Advisory WT grid edit payload (apply=False — the daemon decides)."""
    upsert = (bot.get("upsert") or {}) if isinstance(bot, dict) else {}
    payload = {
        "pairCode": upsert.get("pairCode"),
        "lowPrice": revalue.get("low"),
        "midPrice": revalue.get("mid"),
        "highPrice": revalue.get("high"),
        "gridPercentStep": _f(revalue.get("step_pct")) / 100.0,
        "gridLevels": revalue.get("grids"),
        "amountPerTrade": revalue.get("amount_per_trade"),
    }
    if exits.get("take_profit_usd") is not None:
        payload["takeProfitUsd"] = exits["take_profit_usd"]
    if exits.get("stop_loss_usd") is not None:
        payload["stopLossUsd"] = exits["stop_loss_usd"]
    if exits.get("trailing_activation_pct") is not None:
        payload["trailingActivationPct"] = exits["trailing_activation_pct"]
        payload["trailingExecutePct"] = exits.get("trailing_execute_pct")
    if exits.get("positions_trailing"):
        payload["positionsTrailing"] = True
    return payload


# ── symbol normalization (pure) ────────────────────────────────────────
def _fetch_symbol(venue, symbol):
    """Full ticker for the public candle APIs — binance needs BTCUSDT,
    not BTC. Mirrors screen/merge.fetch_symbol (same convention as the
    optimizer's incumbent refresh): candidates carry the BASE symbol,
    the candle endpoints need the full pair. Without this every binance
    fetch 400s (bad symbol) and the bot silently skips analysis."""
    s = (symbol or "").upper().replace("/", "")
    if "binance" in str(venue or "").lower() \
            and not s.endswith(("USDT", "USDC", "BUSD")):
        return f"{s}USDT"
    return s


# ── the engine ────────────────────────────────────────────────────────
class PositionOptimizer:
    """Per-bot revaluation engine wired into the daemon between watches.

    All I/O is injected: journal_fn(event) -> None, persist_fn(rec) ->
    record-id, fetch_candles_fn(venue, symbol, interval, limit, market) ->
    rows, now_fn() -> epoch seconds. Defaults lazily import
    market_regime.fetch_candles — the module itself stays import-clean.
    """

    # periodic all-keep sweeps journal at most this often (the state
    # journal is a 200-entry ring — flooding it hides real events)
    SWEEP_JOURNAL_INTERVAL_S = 2 * 3600

    def __init__(self, cfg=None, journal_fn=None, persist_fn=None,
                 fetch_candles_fn=None, hunt_fn=None, now_fn=None):
        merged = dict(POSITION_OPTIMIZER_DEFAULTS)
        if cfg:
            merged.update({k: v for k, v in cfg.items() if v is not None})
        self.cfg = merged
        self.journal_fn = journal_fn
        self.persist_fn = persist_fn
        self.fetch_candles_fn = fetch_candles_fn or self._default_fetch
        self.hunt_fn = hunt_fn
        self.now_fn = now_fn or (lambda: time.time())
        self._persisted_today = (self._day(), 0)
        # sweep-journal frequency control: None = no sweep journaled yet
        # (the first cycle after process start always journals once)
        self._last_sweep_journal_at = None
        # always-computed sweep stats from the last cycle() (even when the
        # journal gate stays silent — operators/debuggers read this)
        self.last_sweep_stats = None

    # ── small helpers ────────────────────────────────────────────────
    def _now(self):
        try:
            return float(self.now_fn())
        except Exception:
            return time.time()

    def _day(self, now=None):
        return datetime.fromtimestamp(
            now if now is not None else self._now(),
            tz=timezone.utc).strftime("%Y-%m-%d")

    def _journal(self, event):
        event = dict(event)
        event.setdefault("at", datetime.now(timezone.utc)
                         .isoformat(timespec="seconds"))
        if self.journal_fn is None:
            return
        try:
            self.journal_fn(event)
        except Exception:
            pass

    def _default_fetch(self, venue, symbol, interval, limit, market):
        """Live default: market_regime.fetch_candles (lazy import).

        Retries ONCE on a transient transport error (SSL handshake
        timeout, connection reset, refusal) — single candle-fetch flakes
        were skipping whole 15-min analyses. Non-transient errors and a
        second failure still raise (analyze_bot stays fail-soft)."""
        if WUN_SCRIPTS not in sys.path:
            sys.path.insert(0, WUN_SCRIPTS)
        from market_regime import fetch_candles  # noqa: deferred
        for attempt in (1, 2):
            try:
                return fetch_candles(venue, symbol, interval, limit, market)
            except Exception as exc:
                text = str(exc).lower()
                transient = any(k in text for k in (
                    "timed out", "timeout", "handshake", "reset",
                    "eof", "refused", "temporary failure", "try again"))
                if attempt == 1 and transient:
                    time.sleep(1.5)
                    continue
                raise

    def _compute_metrics(self, rows):
        """market_regime.compute_metrics when importable, else a local
        mean-range fallback (price + atr_pct only)."""
        try:
            if WUN_SCRIPTS not in sys.path:
                sys.path.insert(0, WUN_SCRIPTS)
            from market_regime import compute_metrics  # noqa: deferred
            return compute_metrics(rows)
        except Exception:
            return self._fallback_metrics(rows)

    @staticmethod
    def _fallback_metrics(rows):
        if not rows:
            return {}
        closes = [_f(r[3]) for r in rows if r and len(r) > 3]
        if not closes:
            return {}
        ranges = []
        for r in rows:
            if r and len(r) >= 4:
                c = _f(r[3])
                if c > 0:
                    ranges.append((_f(r[1]) - _f(r[2])) / c * 100)
        atr_pct = sum(ranges) / len(ranges) if ranges else 0.0
        return {"price": closes[-1], "atr_pct": round(atr_pct, 4)}

    def _derive_policy(self, closes, step_pct, regime):
        """stagnation.derive_policy when importable, else None."""
        try:
            sys.path.insert(0, os.path.join(HERE, "policy"))
            from stagnation import derive_policy  # noqa: deferred
            return derive_policy(closes, "1h", step_pct, regime)
        except Exception:
            return None

    def _market_for(self, venue):
        return "spot" if "binance" in str(venue or "").lower() else "futures"

    def _persist(self, rec):
        """persist_fn guarded by the per-day cap. Returns id or None."""
        if self.persist_fn is None:
            return None
        day = self._day()
        if day != self._persisted_today[0]:
            self._persisted_today = (day, 0)
        if self._persisted_today[1] >= int(self.cfg.get("max_apply_per_day",
                                                        4)):
            self._journal({"kind": "position-optimizer",
                           "msg": "per-day persist cap reached",
                           "slot": rec.get("slot"),
                           "symbol": rec.get("symbol")})
            return None
        try:
            rid = self.persist_fn(rec)
        except Exception as exc:
            self._journal({"kind": "position-optimizer-error",
                           "msg": f"persist failed (PB): {str(exc)[:160]}",
                           "slot": rec.get("slot"),
                           "symbol": rec.get("symbol")})
            return None
        if not rid:
            # a failed write must NOT consume the daily cap — it used to
            # (counter incremented on None), silently filling the cap with
            # phantom persists while the PB collection stayed empty
            self._journal({"kind": "position-optimizer-error",
                           "msg": "persist returned no record id (PB down "
                                  "or collection missing) — rec stays "
                                  "journal-only",
                           "slot": rec.get("slot"),
                           "symbol": rec.get("symbol")})
            return None
        self._persisted_today = (day, self._persisted_today[1] + 1)
        return rid

    def persist(self, rec):
        """Public persistence hook (daemon/console may call directly)."""
        return self._persist(rec)

    def _note_fetch_failure(self, bot, venue, symbol, detail):
        """Silent-failure visibility: an empty/invalid candle fetch used
        to vanish (analyze_bot → None, nothing journaled — only fetch
        EXCEPTIONS were journaled). Mark it on the bot so the cycle sweep
        can report it in fetch_failures. Fail-soft by construction."""
        try:
            po = bot.get("position_optimizer")
            po = po if isinstance(po, dict) else {}
            po["last_fetch_failure"] = f"{venue}:{symbol} 1h — {detail}"
            bot["position_optimizer"] = po
        except Exception:
            pass

    def _last_fetch_hop(self, venue, symbol, interval):
        """Newest market_regime.FETCH_EVENTS entry matching this bot's
        candle fetch → which hop served the data ("direct" | "vision" |
        "tvcli"). Lazy import, fail-soft None (an injected fetch_candles_fn
        records no events — tests/hermetic runs get None)."""
        try:
            if WUN_SCRIPTS not in sys.path:
                sys.path.insert(0, WUN_SCRIPTS)
            from market_regime import FETCH_EVENTS  # noqa: deferred
            target = _fetch_symbol(venue, symbol)
            for ev in reversed(list(FETCH_EVENTS)):
                if not isinstance(ev, dict):
                    continue
                if ev.get("symbol") == target \
                        and ev.get("interval") == interval:
                    return ev.get("hop")
        except Exception:
            pass
        return None

    def _sweep_report(self, recs, skipped_cooldown, fetch_failures,
                      fetch_hops, now, dry_run):
        """Compact per-cycle sweep entry (journal frequency controlled in
        cycle()). The stats are ALWAYS computed; the journal only fires on
        noteworthy cycles (any non-keep rec, any fetch failure, first
        cycle, or ≥ SWEEP_JOURNAL_INTERVAL_S since the last one) so the
        200-entry state journal ring is not flooded with all-keep periodic
        sweeps."""
        keeps = sum(1 for r in recs if r.get("recommendation") == "keep")
        non_keep = [r for r in recs if r.get("recommendation") != "keep"]
        compact_recs = [{"slot": r.get("slot"), "symbol": r.get("symbol"),
                         "rec": r.get("recommendation"),
                         "delta_pct": r.get("expected_delta_pct")}
                        for r in non_keep]
        hop_txt = ", ".join(f"{hop} {n}" for hop, n in
                            sorted((fetch_hops or {}).items())) \
            or "no attribution"
        msg = (f"{len(recs)} bots: {keeps} keep, {len(non_keep)} recs"
               f" · candles {hop_txt}"
               f" · {len(fetch_failures)} fetch failures")
        sweep = {
            "kind": "position-optimizer-sweep",
            "msg": msg,
            "analyzed": len(recs),
            "skipped_cooldown": skipped_cooldown,
            "keeps": keeps,
            "recs": compact_recs,
            "fetch_failures": list(fetch_failures or []),
            "fetch_hops": dict(fetch_hops or {}),
            "at": datetime.fromtimestamp(
                now, tz=timezone.utc).isoformat(timespec="seconds"),
            "dry_run": bool(dry_run),
        }
        self.last_sweep_stats = {k: v for k, v in sweep.items()
                                 if k != "kind"}
        noteworthy = bool(non_keep) or bool(fetch_failures)
        due = (self._last_sweep_journal_at is None
               or now - self._last_sweep_journal_at
               >= self.SWEEP_JOURNAL_INTERVAL_S)
        if noteworthy or due:
            self._journal(sweep)
            self._last_sweep_journal_at = now

    # ── the analysis pass ────────────────────────────────────────────
    def analyze_bot(self, bot, slot_key, trigger="periodic", dry_run=True,
                    now=None):
        """Full re-analysis of one deployed bot -> rec dict or None.

        Fail-soft: any fetch/metrics failure returns None (never raises).
        The rec is returned even on "keep" — the caller decides what to do.
        """
        if not self.cfg.get("enabled", True):
            return None
        if not isinstance(bot, dict):
            return None
        try:
            return self._analyze(bot, slot_key, trigger, dry_run, now)
        except Exception as exc:
            self._journal({"kind": "position-optimizer",
                          "msg": f"analysis failed: {str(exc)[:160]}",
                          "slot": str(slot_key),
                          "symbol": bot.get("symbol")})
            return None

    def _analyze(self, bot, slot_key, trigger, dry_run, now,
                 always_journal=False):
        now = float(now) if now is not None else self._now()
        cfg = self.cfg
        obs = bot.get("observed") or {}
        channel = bot.get("channel") or {}
        upsert = bot.get("upsert") or {}

        # 1. market data
        venue = bot.get("venue")
        symbol = bot.get("symbol")
        rows = self.fetch_candles_fn(venue, _fetch_symbol(venue, symbol),
                                     "1h", 300, self._market_for(venue))
        if not rows:
            self._note_fetch_failure(bot, venue, symbol,
                                     "fetch returned 0 candle rows")
            return None
        closes = [r[3] for r in rows if r and len(r) > 3]
        if not closes:
            self._note_fetch_failure(
                bot, venue, symbol,
                f"{len(rows)} rows but none with a valid close")
            return None
        metrics = self._compute_metrics(rows) or {}
        price = _f(metrics.get("price"))
        atr_pct = _f(metrics.get("atr_pct"))
        if price <= 0 or atr_pct <= 0:
            return None

        # 2. regime (deployed ticket) + expected fills (derive_policy)
        regime = _regime(bot)
        step_pct = _f(channel.get("step_pct"), _f(upsert.get(
            "gridPercentStep")) * 100)
        policy = self._derive_policy(closes, step_pct, regime) or {}
        expected = policy.get("expected_fills_per_24h")
        pol_view = dict(bot.get("stagnation_policy") or {})
        if expected is not None:
            pol_view["expected_fills_per_24h"] = expected
        bot_view = dict(bot)
        bot_view["stagnation_policy"] = pol_view

        # 3. revalue the grid against the deployed geometry
        revalue = revalue_grid(
            price, atr_pct, step_pct,
            _f(channel.get("grids"), _f(upsert.get("gridLevels"))),
            _f(upsert.get("amountPerTrade"), _f(bot.get("amount_per_trade"))),
            band_atr=_f(cfg.get("band_atr"), 3.0),
            deployed_mid=channel.get("mid"),
            deployed_step_pct=channel.get("step_pct"),
            deployed_grids=channel.get("grids"),
            deployed_low=channel.get("low"),
            deployed_high=channel.get("high"))
        revalue.pop("grid_lines", None)  # schema keeps the summary only

        # 4. exit profile + recommendation
        exits = evaluate_exits(bot_view, metrics, obs, cfg)
        rec = make_recommendation(
            bot_view, revalue, metrics, obs, exits, cfg,
            spread_pct=None, min_cost=bot.get("min_cost"))
        if self.hunt_fn is not None:
            try:
                rec["tvcli_structure"] = self.hunt_fn(bot)
            except Exception:
                pass

        # 5. complete the record schema
        rec["id"] = uuid.uuid4().hex
        rec["at"] = datetime.fromtimestamp(
            now, tz=timezone.utc).isoformat(timespec="seconds")
        rec["slot"] = str(slot_key)
        rec["trigger"] = trigger
        rec["venue"] = venue
        rec["symbol"] = symbol
        rec["bot_code"] = bot.get("bot_code")
        rec["status"] = obs.get("status") or "active"
        rec["applied"] = False
        rec["applied_at"] = None
        rec["dry_run"] = bool(dry_run)

        # 6. journal + persist gates
        noteworthy = (rec["recommendation"] != "keep"
                      and rec["expected_delta_pct"]
                      >= _f(cfg.get("min_improvement_pct"), 2.0))
        if noteworthy or always_journal:
            self._journal({
                "kind": "position-optimizer",
                "msg": (f"{rec['recommendation']} {venue}:{symbol} "
                        f"(Δ{rec['expected_delta_pct']:+.2f}%, "
                        f"conf {rec['confidence']:.2f})"),
                "slot": str(slot_key),
                "symbol": symbol,
                "recommendation": rec["recommendation"],
                "expected_delta_pct": rec["expected_delta_pct"],
                "trigger": trigger,
                "dry_run": bool(dry_run),
            })
            if not dry_run:
                rid = self._persist(rec)
                if rid:
                    rec["id"] = rid
                    rec["persisted"] = True

        # 7. per-bot cooldown bookkeeping (+ sweep visibility fields:
        # what was recommended, how confident, why, and WHICH data hop
        # served the candles — read by the cycle sweep journal)
        po = bot.setdefault("position_optimizer", {}) \
            if isinstance(bot.get("position_optimizer"), dict) \
            else {}
        bot["position_optimizer"] = po
        po["last_analyzed_at"] = now
        po["last_recommendation"] = rec["recommendation"]
        po.pop("last_fetch_failure", None)
        po["last_delta_pct"] = round(_f(rec.get("expected_delta_pct")), 2)
        po["last_confidence"] = rec.get("confidence")
        po["last_trigger"] = trigger
        po["last_fetch_hop"] = self._last_fetch_hop(venue, symbol, "1h")
        return rec

    # ── the cycle ────────────────────────────────────────────────────
    def cycle(self, active_bots, dry_run=True, now=None):
        """Analyze every eligible active bot. Returns the recs (keeps
        included). Bots in error state and bots inside their cooldown are
        skipped; nothing ever raises. After the loop ONE compact sweep
        entry is journaled (frequency-gated: noteworthy recs / fetch
        failures / first cycle / ≥2h) so operators can see what the
        periodic pass found — including the previously-silent empty-fetch
        failures."""
        recs = []
        if not self.cfg.get("enabled", True) or not active_bots:
            return recs
        now = float(now) if now is not None else self._now()
        cooldown_s = _f(self.cfg.get("cooldown_min"), 60) * 60
        skipped_cooldown = 0
        fetch_failures = []
        fetch_hops = {}
        for slot_key, bot in (active_bots or {}).items():
            try:
                if not isinstance(bot, dict):
                    continue
                obs = bot.get("observed") or {}
                if (obs.get("status") or "").lower() == "error":
                    continue
                po = bot.get("position_optimizer") or {}
                last = _f(po.get("last_analyzed_at"), None) \
                    if po.get("last_analyzed_at") is not None else None
                if last and now - last < cooldown_s:
                    skipped_cooldown += 1
                    continue
                rec = self.analyze_bot(bot, slot_key, trigger="periodic",
                                       dry_run=dry_run, now=now)
                if rec is not None:
                    recs.append(rec)
                    hop = ((bot.get("position_optimizer") or {})
                           .get("last_fetch_hop"))
                    if hop:
                        fetch_hops[hop] = fetch_hops.get(hop, 0) + 1
                else:
                    # silent-failure visibility: empty/invalid candle rows
                    # left NO trace before — surface them in the sweep
                    fail = ((bot.get("position_optimizer") or {})
                            .get("last_fetch_failure"))
                    if fail:
                        fetch_failures.append(fail)
            except Exception:
                continue
        try:
            self._sweep_report(recs, skipped_cooldown, fetch_failures,
                               fetch_hops, now, dry_run)
        except Exception:
            pass
        return recs

    # ── entry hook ───────────────────────────────────────────────────
    def post_deploy(self, bot, slot_key, dry_run=True):
        """'A grid position was entered' hook: same analysis, trigger
        post-deploy, cooldown-ignored, always journaled (even on keep)."""
        if not isinstance(bot, dict):
            return None
        try:
            return self._analyze(bot, slot_key, "post-deploy", dry_run,
                                 self._now(), always_journal=True)
        except Exception as exc:
            self._journal({"kind": "position-optimizer",
                          "msg": f"post-deploy failed: {str(exc)[:160]}",
                          "slot": str(slot_key),
                          "symbol": bot.get("symbol")})
            return None
