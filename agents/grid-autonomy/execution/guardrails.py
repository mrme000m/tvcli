#!/usr/bin/env python3
"""Code-enforced guardrails — every deployment gate as a pure function.

Fail-closed: deploy(ticket, ctx) returns (ok, [violations]); ANY violation
refuses execution. The daemon calls this before touching wt_browser or MCP.
All external state (profiles, history stats, cooldowns) is injected via `ctx`
so the gates are unit-testable without network.

ctx keys:
  kill_file: path whose presence halts everything
  pair_code: resolved market code or None
  profiles_active: {code: balance} for active profiles (or {} if unknown)
  profile_code / slot_balance / max_alloc / amount_per_trade / total_commitment
  deployable_ceiling / committed_now (portfolio-level cap)
  step_pct / spread_pct
  venue / grid_type
  reliability: {samples, profit_factor} or None if not yet measured
  paper: bool — paper mode skips the reliability gate (that's its purpose)
  cooldown_ok: bool + incumbent_score / candidate_score / hysteresis
"""
import os

HYSTERESIS_DEFAULT = 5.0

# Round-trip cost per venue, percent, both legs of a grid round trip:
#   hyperliquid: 2 × 0.015% maker + 2 × 0.035% WT builder fee ≈ 0.10%
#   binance:     2 × 0.1% spot taker/maker, no builder fee  ≈ 0.20%
# Paper fills simulate at real prices; live PnL pays these. The deploy gate
# and the screen's EV ranking both use this table (screen/merge.py imports
# it) so the whole pipeline charges one fee model.
ROUND_TRIP_FEE_PCT = {"hyperliquid": 0.10, "binance": 0.20}


def check_kill(ctx):
    kf = ctx.get("kill_file")
    if kf and os.path.exists(kf):
        return [f"KILL file present: {kf}"]
    return []


def check_paircode(ctx):
    pc = ctx.get("pair_code")
    if not pc or str(pc).startswith("<"):
        return ["pairCode unresolved — resolve via get_exchange_markets"]
    return []


def check_profile(ctx):
    act = ctx.get("profiles_active") or {}
    code = ctx.get("profile_code")
    if not act:
        return ["no active-profile data — fetch get_api_profiles first"]
    if code not in act:
        return [f"profile {code} not active"]
    if (act.get(code) or 0) <= 0:
        return [f"profile {code} has no balance"]
    return []


def check_sizing(ctx):
    v = []
    slot = ctx.get("slot_balance") or 0
    alloc = ctx.get("max_alloc", 0.5)
    total = ctx.get("total_commitment") or 0
    if slot <= 0:
        v.append("slot_balance missing")
    elif total > slot * alloc + 1e-9:
        v.append(f"worst-case {total} > slot {slot}×{alloc}")
    ceiling = ctx.get("deployable_ceiling")
    committed = ctx.get("committed_now", 0)
    if ceiling is not None and committed + total > ceiling + 1e-9:
        v.append(f"portfolio {committed}+{total} > ceiling {ceiling}")
    return v


def check_spread(ctx):
    step, spread = ctx.get("step_pct"), ctx.get("spread_pct")
    if step is None:
        return ["step_pct missing"]
    # fee floor: even with a zero spread the round trip pays exchange fees
    # (plus the WT builder fee on premium venues) — the step must clear them
    venue = ctx.get("venue")
    fee = ROUND_TRIP_FEE_PCT.get(venue, 0.15)
    if ctx.get("round_trip_fee_pct") is not None:
        fee = float(ctx.get("round_trip_fee_pct"))
    if step < fee:
        return [f"step {step}% < round-trip fees {fee}% — unharvestable"]
    if spread is None:
        return []  # unknown spread: warn upstream, don't block
    if step < 2 * spread + fee:
        return [f"step {step}% < 2×spread {2*spread}% + fees {fee}% — unharvestable"]
    return []


def check_venue_side(ctx):
    if ctx.get("venue") == "binance" and ctx.get("grid_type") == "short":
        return ["binance SPOT cannot short — flat only"]
    return []


def check_reliability(ctx):
    if ctx.get("paper"):
        return []  # paper exists to gather the samples
    r = ctx.get("reliability") or {}
    samples = r.get("samples", 0)
    pf = r.get("profit_factor", 0)
    if samples < 30:
        return [f"only {samples} closed samples (< 30) — stay paper/probe"]
    if pf < 1.3:
        return [f"profit factor {pf} < 1.3 — do not scale live"]
    if r.get("recent_pf") is not None and r["recent_pf"] < 1.0:
        return [f"recent PF {r['recent_pf']} < 1.0 — kill archetype"]
    return []


def check_rotation(ctx):
    if not ctx.get("is_rotation"):
        return []
    v = []
    if not ctx.get("cooldown_ok", False):
        v.append("rotation cooldown not expired")
    h = ctx.get("hysteresis", HYSTERESIS_DEFAULT)
    # .get(..., 0) default only fires on a MISSING key; adopted incumbents
    # carry score_final=None — coerce None to 0 so the gate never raises.
    cand_score = ctx.get("candidate_score") or 0
    inc_score = ctx.get("incumbent_score") or 0
    if (cand_score - inc_score) < h:
        v.append(f"Δscore < hysteresis {h}")
    return v


CHECKS = (check_kill, check_paircode, check_profile, check_sizing,
          check_spread, check_venue_side, check_reliability, check_rotation)


def deploy(ticket, ctx):
    """(ok, violations). ok=True only if every gate passes."""
    violations = []
    for fn in CHECKS:
        violations.extend(fn(ctx))
    if ticket.get("decision") != "GO":
        violations.append(f"ticket not GO: {ticket.get('veto', 'no reason')}")
    return (len(violations) == 0), violations
