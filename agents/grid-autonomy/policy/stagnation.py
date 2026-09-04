#!/usr/bin/env python3
"""Grid-autonomy stagnation policy — per-token thresholds derived from history.

Instead of fixed "48h / 24h" constants, every candidate gets its own
stagnation definition computed from its recent candles:

  expected_fills_per_24h — naive grid-cross simulation over 30d of history
  avg_holding_h          — mean bars between mid-line recrossings (the token's
                           natural oscillation period)
  cooldown_h             — k × avg_holding_h (k per regime), clamped 12–72h
  stagnant_if            — fills_24h < 0.3×expected AND realized < 0.4×expected,
                           or regime-switched-against-us, or full-ladder DD

Pure functions (no network) so the daemon and the unit tests share one
implementation. History fetching lives in screen/merge.py — pass closes in.

Slot allocator for the 500 USD fund (300 HL / 200 Binance → 3–5 slots) is
here too: slot_plan() is pure math the daemon calls before creating a bot.
"""
import math

INTERVAL_HOURS = {"15m": 0.25, "1h": 1.0, "4h": 4.0, "1d": 24.0}

# cooldown multiplier per regime (choppy tokens rotate faster)
COOLDOWN_K = {
    "chop_high_volatility": 1.5,
    "squeeze": 2.5,
    "neutral": 2.0,
    "trend_up": 3.0,
    "trend_down": 3.0,
}

COOLDOWN_MIN_H = 12.0
COOLDOWN_MAX_H = 72.0

FILL_RATIO_STAGNANT = 0.3   # fills_24h < 0.3 × expected
REALIZED_RATIO_STAGNANT = 0.4
SCORE_DROP_ROTATE = 12.0    # regime switch + score drop > 12 → rotate
HYSTERESIS_SCORE = 5.0      # new token must beat incumbent by ≥ 5


def simulate_grid_fills(closes, step_pct):
    """Naive fill simulation: grid lines at mid×(1±k×step), mid = last close.

    A 'fill' is counted each time consecutive closes cross any grid line.
    Returns (fills_total, mid_crossings) over the window.
    """
    if not closes or step_pct <= 0:
        return 0, 0
    mid = closes[-1]
    step = step_pct / 100.0
    # lines out to ±10 steps (covers the ATR-band channel)
    lines = [mid * (1 + k * step) for k in range(-10, 11) if k != 0]
    fills, mids = 0, 0
    for prev, cur in zip(closes, closes[1:]):
        for ln in lines:
            if (prev - ln) * (cur - ln) < 0:
                fills += 1
                break
        if (prev - mid) * (cur - mid) < 0:
            mids += 1
    return fills, mids


def avg_holding_h(closes, interval, step_pct):
    """Mean hours between mid recrossings ≈ the token's oscillation period."""
    _, mids = simulate_grid_fills(closes, step_pct)
    bars = max(len(closes) - 1, 1)
    if mids < 2:
        # no oscillation visible → fall back to a full-window hold estimate
        return bars * INTERVAL_HOURS.get(interval, 1.0)
    return bars / mids * INTERVAL_HOURS.get(interval, 1.0)


def derive_policy(closes, interval, step_pct, regime):
    """Per-token stagnation policy from history. All values JSON-serializable."""
    bars = len(closes)
    window_h = bars * INTERVAL_HOURS.get(interval, 1.0)
    fills, _ = simulate_grid_fills(closes, step_pct)
    fills_per_24h = fills / window_h * 24 if window_h else 0.0
    holding = avg_holding_h(closes, interval, step_pct)
    k = COOLDOWN_K.get(regime, 2.0)
    cooldown = min(COOLDOWN_MAX_H, max(COOLDOWN_MIN_H, k * holding))
    return {
        "regime": regime,
        "window_bars": bars,
        "window_h": round(window_h, 1),
        "step_pct": step_pct,
        "expected_fills_per_24h": round(fills_per_24h, 2),
        "avg_holding_h": round(holding, 1),
        "cooldown_h": round(cooldown, 1),
        "cooldown_k": k,
        "stagnant_if": {
            "min_fills_24h": round(fills_per_24h * FILL_RATIO_STAGNANT, 2),
            "min_realized_ratio": REALIZED_RATIO_STAGNANT,
            "window_h": 48,
        },
        "score_drop_rotate": SCORE_DROP_ROTATE,
        "hysteresis_score": HYSTERESIS_SCORE,
    }


def is_stagnant(observed, policy, regime_now=None, score_drop=0.0,
                ladder_full=False, dd_vs_atr_band=0.0):
    """observed: {fills_24h, realized_ratio}. Returns (stagnant, [reasons])."""
    reasons = []
    th = policy["stagnant_if"]
    if observed.get("fills_24h", 0) < th["min_fills_24h"] and \
            observed.get("realized_ratio", 0) < th["min_realized_ratio"]:
        reasons.append(
            f"fills {observed.get('fills_24h')} < {th['min_fills_24h']} and "
            f"realized {observed.get('realized_ratio')} < {th['min_realized_ratio']}")
    if regime_now and regime_now != policy["regime"] and score_drop > policy["score_drop_rotate"]:
        reasons.append(f"regime {policy['regime']}→{regime_now} + score -{score_drop}")
    if ladder_full and dd_vs_atr_band > 1.5:
        reasons.append(f"full ladder, DD {dd_vs_atr_band}× ATR band")
    return (len(reasons) > 0), reasons


# ── Slot allocator (500 USD fund: 300 HL perps / 200 Binance spot → 3–5 slots)

def slot_plan(total_usd=500.0, venue_balances=None, n_slots=4, max_alloc=0.5,
              cash_buffer_pct=0.15):
    """Split the fund into n_slots (3–5), proportional to venue sleeve share.

    Each funded venue gets floor/round of its sleeve share of n_slots (round
    HL up on any remainder so perps keep priority), at least 1 slot each; the
    sleeve balance is then divided equally among that venue's slots:

      n=4 → HL 2×$150 + BN 2×$100   (300/2 and 200/2)
      n=3 → HL 2×$150 + BN 1×$200
      n=5 → HL 3×$100 + BN 2×$100

    Returns {slots, per_slot, deployable}. deployable = total × (1 − cash
    buffer) is the hard ceiling the daemon enforces across all bots; per-slot
    worst-case commitment ≤ max_alloc.
    """
    if venue_balances is None:
        venue_balances = {"hyperliquid": 300.0, "binance": 200.0}
    if not 3 <= n_slots <= 5:
        raise ValueError("n_slots must be 3–5")
    venue_sum = sum(venue_balances.values())
    scale = total_usd / venue_sum if venue_sum else 0
    scaled = {k: round(v * scale, 2) for k, v in venue_balances.items()}
    funded = {k: v for k, v in scaled.items() if v > 0}
    sleeve_total = sum(funded.values())
    if sleeve_total <= 0 or not funded:
        raise ValueError("at least one venue must be funded")
    # Proportional slot counts (largest-remainder on the sleeve share; min 1
    # per funded venue, then top-balance venue absorbs any remainder).
    slots_per = {}
    for k, v in funded.items():
        slots_per[k] = max(1, round(v / sleeve_total * n_slots))
    diff = n_slots - sum(slots_per.values())
    by_balance = sorted(funded, key=lambda k: funded[k], reverse=True)
    while diff > 0:
        slots_per[by_balance[0]] += 1
        diff -= 1
    while diff < 0:
        for k in by_balance:
            if slots_per[k] > 1:
                slots_per[k] -= 1
                diff += 1
                break
    slots = []
    slot_no = 0
    for k, v in funded.items():
        per_slot = round(v / slots_per[k], 2)
        for _ in range(slots_per[k]):
            slot_no += 1
            slots.append({
                "slot": slot_no, "venue": k,
                "balance": per_slot,
                "max_commitment": round(per_slot * max_alloc, 2),
                "venue_sleeve": v,
                "venue_slots": slots_per[k],
            })
    return {
        "total_usd": total_usd,
        "venue_balances": scaled,
        "n_slots": n_slots,
        "max_alloc": max_alloc,
        "deployable_ceiling": round(total_usd * (1 - cash_buffer_pct), 2),
        "slots": slots,
    }


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slots", action="store_true", help="print slot plan")
    ap.add_argument("--n-slots", type=int, default=4)
    ap.add_argument("--total", type=float, default=500.0)
    args = ap.parse_args()
    if args.slots:
        print(json.dumps(slot_plan(args.total, n_slots=args.n_slots), indent=2))
