#!/usr/bin/env python3
"""Grid-fleet daemon — autonomous scan → deliberate → guard → deploy → watch → rotate.

Schedules (from ../config.yaml, merged over built-in defaults):
  health/positions poll   60s        (KILL check, observe_all, stagnation eval,
                                      out-of-channel re-analysis, in-place adjust)
  rescreen                60m        (merge.py → swarm → guardrails → deploy)
  reliability cron        24h        (bot_trades → archetype_stats → save
                                    → reload → sizing/kill gates)

Autonomy with guardrails: deployments are paper (demo-hype) until the
reliability gate passes (>=30 samples, PF>=1.3); live needs live_allow=true in
state.json (set only by an explicit operator action) AND guardrails.deploy().
--dry-run (default) plans everything without creating anything.
--once runs a single rescreen cycle plus one health pass then exits.

State: state/state.json {slots, active_bots, cooldowns_until, reliability,
live_allow, committed, journal}. HTTP ctl (thread): GET /health /status
/reliability /observe, POST /rotate {"slot": n} /rescreen /kill.

Stdlib only (http.server, subprocess, json). Subprocess drivers call the
sibling modules in-process where possible (import) and via CLI where the
upstream scripts demand it (merge.py). Worker A/C modules (resolve, observe,
reliability_grid, reflect, spreads) are imported DEFENSIVELY so the daemon
runs today and picks the real modules up when they land.
"""
import argparse
import copy
import json
import math
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "llm"))
sys.path.insert(0, os.path.join(HERE, "screen"))
sys.path.insert(0, os.path.join(HERE, "policy"))
sys.path.insert(0, os.path.join(HERE, "agents"))
sys.path.insert(0, os.path.join(HERE, "execution"))
sys.path.insert(0, os.path.join(HERE, "watch"))
WUN_SCRIPTS = os.path.normpath(os.path.join(
    HERE, "..", "..", ".agents", "skills", "wundertrading", "scripts"))
sys.path.insert(0, WUN_SCRIPTS)

from stagnation import is_stagnant, slot_plan, derive_policy  # noqa: E402
from swarm import deliberate  # noqa: E402
from guardrails import deploy as guard_deploy  # noqa: E402
import grid_adapter  # noqa: E402
from spec import build_spec  # noqa: E402
from config_lite import deep_merge, load_yaml  # noqa: E402
from ctl_http import serve_ctl  # noqa: E402

# ── Worker A defensive imports ────────────────────────────────────────
try:
    from resolve import resolve_pair, market_map, pair_meta  # noqa: F401
    HAS_RESOLVE = True
except Exception:
    HAS_RESOLVE = False

    def resolve_pair(venue, symbol, market=None):
        return None

    def pair_meta(venue, symbol, market=None):
        return {}
    market_map = {}

try:
    from observe import (account_limits, observe_all,  # noqa: F401
                         grid_capacity, grid_profiles, grid_status)
    HAS_OBSERVE = True
except Exception:
    HAS_OBSERVE = False

    def observe_all(active_bots):
        return {}

    def account_limits():
        return {}

    def grid_capacity():
        return {}

    def grid_profiles():
        return []

    def grid_status():
        return []

try:
    from reliability_grid import (archetype_stats, bot_trades,  # noqa: F401
                                  load as load_reliability,
                                  save as save_reliability)
    HAS_RELIABILITY = True
except Exception:
    HAS_RELIABILITY = False

    def archetype_stats(bots_by_archetype):
        return {}

    def bot_trades(bot_code):
        return []

    def load_reliability():
        return {}

    def save_reliability(data):
        return False

# ── Worker C defensive import ─────────────────────────────────────────
try:
    from reflect import (record_decision, record_outcome,  # noqa: F401
                         memories_for, write_run_card)
    HAS_REFLECT = True
except Exception:
    HAS_REFLECT = False

    def record_decision(ticket, brief, action, payloads):
        return None

    def record_outcome(decision_id, final):
        return None

    def memories_for(brief, k=3):
        return []

    def write_run_card(cycle_report):
        return None

# Real-money profile hard denylist (refused even if allowlisted by mistake).
PROFILE_DENYLIST = {"c629f5ba3a643a82137e7864"}

# Minimum viable notional per grid line when :2087 market metadata is
# unavailable (live values are per-pair `limits.cost.min`: 10 USDC on
# Hyperliquid, 5-50 USDT on Binance markets).
MIN_USD_PER_GRID = 10.0

# Bot statuses accepted as "verified stopped" before a rotation may delete
# the incumbent. Anything else (active, stopping, …) keeps it alive.
STOPPED_STATES = {"stopped", "stopped_and_close_all", "closed"}

STATE_PATH = os.path.join(HERE, "state", "state.json")
SPECS_DIR = os.path.join(HERE, "watch", "specs")  # patchable in tests
DEFAULT_STATE = {
    "live_allow": False,
    "slots": [],               # from slot_plan()
    "active_bots": {},         # slot -> {symbol, venue, bot_code, ticket, payloads, since, ...}
    "cooldowns_until": {},     # "venue:SYM" -> epoch
    "reliability": None,
    "committed": {},           # slot -> worst-case commitment
    "journal": [],             # recent decisions (capped)
    "last_cycle": None,        # utcnow() of last manage-loop pass
    "last_observe": {},        # latest observe_all() result (GET /observe)
    "last_adjust": {},         # slot -> epoch of last in-place grid_edit
    "profiles": [],            # last grid_profiles() snapshot
}

DEFAULT_CONFIG = {
    "portfolio": {
        "total_usd": 500.0,
        "venues": {"hyperliquid": {"balance_usd": 300.0},
                   "binance": {"balance_usd": 200.0}},
        "slots_min": 3, "slots_max": 5, "slots_default": 4,
        "max_alloc_per_slot": 0.5, "cash_buffer_pct": 0.15,
    },
    "screen": {"rescreen_minutes": 60, "confirm_interval": "4h"},
    "watch": {"interval_s": 60, "adjust_steps_threshold": 2.0},
    "autonomy": {"mode": "auto", "base_pct": 0.25, "probe_pct": 0.40, "full_pct": 0.50,
                 "live_profiles": [], "paper_profiles": ["demo-hype"]},
    "memory": {"k": 3},
    "adopt_existing": True,
    "policy": {"hysteresis_score": 5.0, "cooldown_min_h": 12.0,
               "cooldown_max_h": 72.0, "min_hold_h": 24},
    "reliability": {"min_samples": 30, "profit_factor_pass": 1.3},
    "server": {"daemon_port": 8799},
}

# ── config loading (YAML subset parser lives in config_lite.py) ───────


def load_config(path=None):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    p = path or os.path.join(HERE, "config.yaml")
    if os.path.isfile(p):
        try:
            with open(p) as f:
                cfg = deep_merge(cfg, load_yaml(f.read()) or {})
        except Exception as exc:
            print(f"config load failed ({exc}) — using defaults", flush=True)
    return cfg


# ── state + journal ────────────────────────────────────────────────────

def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(state, event):
    event = dict(event)
    event["at"] = utcnow()
    state.setdefault("journal", []).append(event)
    state["journal"] = state["journal"][-200:]
    print(f"[{event['at']}] {event.get('kind')}: {event.get('msg')}", flush=True)


def load_state():
    st = copy.deepcopy(DEFAULT_STATE)
    if os.path.isfile(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                st.update(json.load(f))
        except Exception as exc:
            print(f"state load failed ({exc}) — starting fresh", flush=True)
    for key in DEFAULT_STATE:
        st.setdefault(key, DEFAULT_STATE[key])
    return st


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)


def should_rotate(candidate, incumbent, policy, observed, now_epoch,
                  needs_reanalysis=False):
    """Pure rotation decision → (rotate: bool, reasons: [str]).

    `needs_reanalysis` (set by health_cycle when the incumbent went
    out-of-channel or was stopped) is a hard trigger: a stopped bot earns
    nothing, so the challenger hysteresis does not apply. The min-hold floor
    and per-symbol cooldowns are still enforced by the caller.
    """
    reasons = []
    # `or 0`: adopted bots carry score_final=None — must not break arithmetic
    inc_score = incumbent.get("score_final") or 0
    cand_score = candidate.get("score_final") or 0
    stag, s_reasons = is_stagnant(
        observed, policy,
        regime_now=candidate.get("regime"),
        score_drop=inc_score - cand_score,
        ladder_full=observed.get("ladder_full", False),
        dd_vs_atr_band=observed.get("dd_vs_atr_band", 0.0))
    if not stag and needs_reanalysis:
        stag = True
        s_reasons = list(s_reasons) + [
            "needs_reanalysis (out-of-channel/stopped)"]
    if not stag:
        return False, ["incumbent healthy"]
    reasons.extend(s_reasons)
    dscore = cand_score - inc_score
    if not needs_reanalysis and dscore < policy.get("hysteresis_score", 5.0):
        return False, reasons + [f"Δscore {dscore:.1f} < hysteresis"]
    return True, reasons + [f"Δscore {dscore:.1f}"]


def run_merge(top=30, confluence_top=10, no_confluence=False):
    cmd = [sys.executable, os.path.join(HERE, "screen", "merge.py"),
           "--top", str(top), "--confluence-top", str(confluence_top), "--json"]
    if no_confluence:
        cmd.append("--no-confluence")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if p.returncode != 0:
        raise RuntimeError(f"merge failed: {p.stderr[-500:]}")
    return json.loads(p.stdout)


# ── Worker A/C safe wrappers ───────────────────────────────────────────

def resolve_pair_safe(venue, symbol, market=None):
    """(pair_code, error). error non-empty means the resolve module is absent.

    `market` overrides the venue default ("derivative" when the Binance
    sleeve runs on its BINANCE_FUTURES paper stand-in profile)."""
    if not HAS_RESOLVE:
        return None, "resolve module missing"
    try:
        info = resolve_pair(venue, symbol, market=market) \
            if market else resolve_pair(venue, symbol)
    except TypeError:
        try:
            info = resolve_pair(venue, symbol)
        except Exception as exc:
            return None, f"resolve failed: {exc}"
    except Exception as exc:
        return None, f"resolve failed: {exc}"
    if not info:
        return None, ""
    code = info.get("pairCode") if isinstance(info, dict) else None
    return code, ""


def observe_all_safe(active_bots):
    try:
        return observe_all(active_bots) or {}
    except Exception as exc:
        return {"_error": str(exc)[:160]}


def grid_profiles_safe():
    try:
        return grid_profiles() or []
    except Exception:
        return []


def grid_capacity_safe():
    """grid_capacity with error guard: {} on failure (never raises)."""
    try:
        return grid_capacity() or {}
    except Exception:
        return {}


def account_limits_safe():
    """account_limits with error guard: {} on failure (never raises)."""
    try:
        return account_limits() or {}
    except Exception:
        return {}


def _limits_signature(limits, capacity):
    """Compact signature of the observed subscription state (change detection)."""
    gb = (limits or {}).get("gridBots") or {}
    caps = (capacity or {}).get("max_active") or {}
    active = (capacity or {}).get("active") or {}
    premium = active.get("premium")
    premium_exchanges = sorted(premium.keys()) if isinstance(premium, dict) else []
    return json.dumps({
        "gridBots": {"active": gb.get("active"), "max": gb.get("max")},
        "tier_caps": caps,
        "premium_exchanges": premium_exchanges,
    }, sort_keys=True)


def grid_status_safe():
    """List of active bots from observe.grid_status (fallback grid_adapter)."""
    try:
        bots = grid_status() or []
        if bots:
            return bots
    except Exception:
        pass
    fn = getattr(grid_adapter, "grid_status", None)
    if fn is not None:
        try:
            return fn() or []
        except Exception:
            return []
    return []


def grid_edit_safe(bot_code, upsert, dry_run=True):
    fn = getattr(grid_adapter, "grid_edit", None)
    if fn is None:
        return {"ok": False, "error": "grid_edit not implemented yet"}
    try:
        return fn(bot_code, upsert, dry_run=dry_run)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:160]}


def reliability_load_safe():
    if not HAS_RELIABILITY:
        return {}
    try:
        return load_reliability() or {}
    except Exception:
        return {}


def record_decision_safe(ticket, brief, action, payloads):
    if not HAS_REFLECT:
        return None
    try:
        return record_decision(ticket, brief, action, payloads)
    except Exception:
        return None


def record_outcome_safe(decision_id, final):
    if not HAS_REFLECT or decision_id is None:
        return None
    try:
        return record_outcome(decision_id, final)
    except Exception:
        return None


def memories_for_safe(brief, k=3):
    if not HAS_REFLECT:
        return []
    try:
        return memories_for(brief, k=k) or []
    except Exception:
        return []


def write_run_card_safe(cycle_report):
    if not HAS_REFLECT:
        return None
    try:
        return write_run_card(cycle_report)
    except Exception:
        return None


def retry_grid_call(fn, dry, *args, attempts=3, backoff=2.0, **kwargs):
    """Retry wt_browser subprocess failures twice with backoff (call-site retry).

    `dry` controls retries only; the wrapped fn receives *args/**kwargs
    untouched (e.g. its own dry_run=... flag).
    """
    last = None
    for i in range(attempts):
        try:
            res = fn(*args, **kwargs) or {"ok": False, "error": "empty result"}
        except Exception as exc:
            res = {"ok": False, "error": str(exc)[:160]}
        last = res
        if dry or res.get("ok"):
            return res
        if i < attempts - 1:
            time.sleep(backoff * (i + 1))
    return last


def extract_bot_code(res):
    if not isinstance(res, dict):
        return None
    code = res.get("gridBotCode") or res.get("bot_code") or res.get("code")
    if not code and res.get("stdout"):
        try:
            j = json.loads(res["stdout"])
            code = j.get("gridBotCode") or j.get("code")
        except Exception:
            pass
    return code


# ── profiles / venue helpers ───────────────────────────────────────────

def build_profiles_active(profiles):
    return {p.get("code"): p.get("balance", 0) or 0
            for p in profiles if p.get("code")}


# venue -> exchanges that may serve it. Binance SPOT is the live target;
# BINANCE_FUTURES is the paper stand-in (WunderTrading has no Binance spot
# paper mode) and keeps the sleeve's spot-like no-short rule.
VENUE_EXCHANGES = {
    "hyperliquid": {"HYPERLIQUID_SWAP"},
    "binance": {"BINANCE", "BINANCE_FUTURES"},
}


def _allowed_profile_names(cfg):
    """Flatten the venue-keyed paper_profiles map (or a legacy flat list)."""
    pp = cfg["autonomy"]["paper_profiles"]
    if isinstance(pp, dict):
        names = set()
        for v in pp.values():
            names.update(v if isinstance(v, (list, tuple)) else [v])
        return names
    return set(pp)


def select_profile(venue, profiles, cfg, paper=True):
    """(profile, violation). Venue-strict; paper deploys use paper only.

    A binance-venue profile must be on BINANCE (spot) or BINANCE_FUTURES
    (paper stand-in); a hyperliquid-venue profile on HYPERLIQUID_SWAP. No
    cross-venue fallback — a mismatched profile is a violation, never a
    silent misroute."""
    allowed = _allowed_profile_names(cfg)
    if not paper:
        allowed |= set(cfg["autonomy"]["live_profiles"])
    exchanges = VENUE_EXCHANGES.get(venue, set())
    candidates = [p for p in profiles
                  if p.get("code") and p.get("name") in allowed
                  and (p.get("exchange") or "").upper() in exchanges]
    if not candidates:
        have = [p.get("name") for p in profiles
                if (p.get("exchange") or "").upper() in exchanges]
        return None, (f"no {venue} profile allowlisted (allowlist={sorted(allowed)}, "
                      f"{venue} profiles present={have})")
    # prefer the venue's native market (BINANCE spot) over stand-ins
    candidates.sort(key=lambda p: 0 if (p.get("exchange") or "").upper()
                    == ("BINANCE" if venue == "binance" else "HYPERLIQUID_SWAP")
                    else 1)
    for p in candidates:
        if p.get("code") in PROFILE_DENYLIST:
            return None, f"profile {p.get('name')} denylisted"
        if paper and not p.get("paperTrading"):
            continue
        return p, None
    return None, f"no paper profile on {venue} (candidates exist but not paperTrading)"


def market_for_profile(profile):
    """'derivative' | 'spot' for the market/pairCode resolution."""
    e = (profile.get("exchange") or "").upper() if profile else ""
    if e == "BINANCE":
        return "spot"
    if e == "HYPERLIQUID" or e == "HYPERLIQUID_SWAP" or e.endswith("_FUTURES") \
            or e.endswith("_SWAP"):
        return "derivative"
    return "spot"


def fetch_symbol(venue, symbol):
    """Full ticker for public candle APIs (binance needs BTCUSDT, not BTC)."""
    s = (symbol or "").upper().replace("/", "")
    if venue == "binance" and not s.endswith(("USDT", "USDC", "BUSD")):
        return f"{s}USDT"
    return s


def venue_from_exchange(exchange):
    e = (exchange or "").upper()
    if "HYPERLIQUID" in e:
        return "hyperliquid"
    if "BINANCE" in e:
        return "binance"
    return None


def symbol_from_pair(pair, venue):
    p = (pair or "").upper()
    if not p:
        return None
    if venue == "hyperliquid":
        return p.split("-")[0] if "-" in p else p
    for suffix in ("USDT", "USDC"):
        if p.endswith(suffix) and len(p) > len(suffix):
            return p[:-len(suffix)]
    return p


def reclassify_regime(venue, symbol, interval="1h", limit=300):
    from market_regime import fetch_candles, compute_metrics, classify
    market = "futures" if venue == "hyperliquid" else "spot"
    cl = fetch_candles(venue, fetch_symbol(venue, symbol), interval, limit, market)
    m = compute_metrics(cl)
    regime, _ = classify(m)
    return regime


# ── reliability escalation ─────────────────────────────────────────────

def _reliability_stats_for(reliability, archetype):
    if not isinstance(reliability, dict):
        return {}
    stats = reliability.get(archetype)
    if isinstance(stats, dict):
        return stats
    # flat fallback: single-bot/archetype reliability dict
    if "samples" in reliability or "profit_factor" in reliability:
        return reliability
    return {}


def size_multiplier(reliability, archetype, cfg):
    """(multiplier, tier, stats) for the escalation ladder.

    Tiers are TARGET worst-case fractions of the slot — the exchange
    minimum floor may still raise them toward the cap (density-first
    sizing). Unproven archetypes start at base (25% target), scale to
    probe/full only with reliability samples."""
    full = float(cfg["autonomy"]["full_pct"])
    probe = float(cfg["autonomy"]["probe_pct"])
    base = float(cfg["autonomy"].get("base_pct", 0.25))
    stats = _reliability_stats_for(reliability, archetype)
    samples = stats.get("samples", 0) or 0
    pf = stats.get("profit_factor", 0) or 0
    if samples >= 30 and pf >= 1.3:
        return full, "full", stats
    if samples >= 10:
        return probe, "probe", stats
    return base, "base", stats


def refuse_new_archetype(reliability, archetype):
    stats = _reliability_stats_for(reliability, archetype)
    recent_pf = stats.get("recent_pf")
    return recent_pf is not None and recent_pf < 1.0


# ── Daemon ─────────────────────────────────────────────────────────────

class Daemon:
    def __init__(self, port=None):
        self.config = load_config()
        self.port = port or int(self.config["server"].get("daemon_port", 8799))
        self.state = load_state()
        self.profiles = grid_profiles_safe()
        self.reliability = reliability_load_safe()
        self.state["profiles"] = self.profiles
        self.state["reliability"] = self.reliability
        self.capabilities = {
            "resolve": HAS_RESOLVE, "observe": HAS_OBSERVE,
            "reliability": HAS_RELIABILITY, "reflect": HAS_REFLECT,
        }
        self._lock = threading.Lock()
        self._rescreen_flag = False

    # ctl hook
    def queue_rescreen(self):
        with self._lock:
            self._rescreen_flag = True

    def consume_rescreen(self):
        with self._lock:
            v = self._rescreen_flag
            self._rescreen_flag = False
            return v

    def plan_slots(self):
        p = self.config["portfolio"]
        venues = {k: v.get("balance_usd", 0) for k, v in p["venues"].items()}
        return slot_plan(p["total_usd"], venues, p["slots_default"],
                         p["max_alloc_per_slot"], p["cash_buffer_pct"])

    # ── plan + commit one candidate (shared by rescreen and rotation) ──
    def plan_candidate(self, cand, slot, dry_run, is_rotation=False,
                       incumbent=None, cooldown_ok=True):
        """resolve → deliberate → profile → reliability → payloads → guard.

        Returns (action, ticket, payloads, brief); action is None on any veto.
        Does NOT create a bot — callers commit separately so rotation can
        stop/verify/delete the incumbent before deploying the challenger.
        """
        key = f"{cand['venue']}:{cand['symbol']}"
        brief = dict(cand, slot=slot)
        brief["memories"] = memories_for_safe(
            brief, k=int(self.config["memory"].get("k", 3)))
        # stagnation policy for the swarm brief (best-effort, same as deploy)
        try:
            from market_regime import fetch_candles
            cl = fetch_candles(cand["venue"],
                               fetch_symbol(cand["venue"], cand["symbol"]),
                               "1h", 300,
                               "futures" if cand["venue"] == "hyperliquid" else "spot")
            brief["stagnation_policy"] = derive_policy(
                [c[3] for c in cl], "1h", cand.get("step") or 0.5, cand["regime"])
        except Exception as exc:
            brief["stagnation_policy"] = {"error": str(exc)[:120]}

        ticket = deliberate(brief)
        if ticket.get("decision") != "GO":
            log(self.state, {"kind": "veto",
                             "msg": f"{key}: {ticket.get('veto', '?')[:160]}"})
            return None, ticket, None, brief

        # live profile selection FIRST (venue-strict, allowlist + denylist):
        # the profile's exchange decides the market for pairCode resolution.
        profile, violation = select_profile(cand["venue"], self.profiles,
                                            self.config, paper=True)
        if violation:
            log(self.state, {"kind": "guard-veto", "msg": f"{key}: {violation}"})
            return None, ticket, None, brief
        profiles_active = build_profiles_active(self.profiles)
        exch = (profile.get("exchange") or "").upper()
        market = market_for_profile(profile)

        # pairCode resolved BEFORE guard, from the profile-coherent market
        pair_code, resolve_err = resolve_pair_safe(cand["venue"], cand["symbol"],
                                                   market=market)
        if resolve_err:
            log(self.state, {"kind": "guard-veto",
                             "msg": f"{key}: {resolve_err} — cannot resolve pairCode"})
            return None, ticket, None, brief
        if not pair_code:
            log(self.state, {"kind": "guard-veto",
                             "msg": f"{key}: pairCode unresolved for "
                                    f"{cand['venue']}:{cand['symbol']}"})
            return None, ticket, None, brief
        # per-pair trading constraints (min notional per trade, precision)
        try:
            meta = pair_meta(cand["venue"], cand["symbol"], market=market) or {}
        except Exception:
            meta = {}
        # WT grid engines enforce a per-line floor ($MIN_USD_PER_GRID) that is
        # HIGHER than the exchange's order min (limits.cost.min) on Binance
        # futures (5 USDT) — the exchange value must only raise it, never lower
        # it below the grid floor.
        min_cost = max(meta.get("min_cost") or 0, MIN_USD_PER_GRID)
        amount_precision = meta.get("amount_precision")

        # escalation ladder
        archetype = cand.get("archetype") or cand.get("regime", "neutral")
        if refuse_new_archetype(self.reliability, archetype):
            log(self.state, {"kind": "reliability-veto",
                             "msg": f"{key}: recent_pf < 1.0 for archetype "
                                    f"{archetype} — refuse new deployment"})
            return None, ticket, None, brief
        mult, tier, stats = size_multiplier(self.reliability, archetype, self.config)
        max_alloc_eff = min(mult, float(self.config["autonomy"]["full_pct"]),
                            float(self.config["portfolio"]["max_alloc_per_slot"]))

        payloads = grid_adapter.build_ticket_payloads(
            ticket, brief, slot["balance"], max_alloc_eff, profile["code"],
            pair_code, exchange_code=exch, amount_precision=amount_precision,
            min_cost=min_cost)

        # ── density-first sizing (user directive) ──────────────────────
        # Grid DENSITY (line count) drives profit (fills/day × profit/fill).
        # Funds are the risk budget, not a reason to degrade geometry:
        #   1. build_ticket_payloads already raises per-line USD to the
        #      exchange minimum (min_cost) when the tier allocation is
        #      smaller — using MORE funds, keeping all the lines.
        #   2. The risk cap applies to the WORST-CASE (one side of the
        #      channel ≈ grids/2 lines), not the distributed notional —
        #      if the min-funded worst case fits the cap, bump the tier to
        #      cover it and keep the full-density grid.
        #   3. Only when even half a channel at min_cost breaks the cap:
        #      widen the step (fewer lines) as the last resort, else veto.
        sizing = payloads["grid_bot"].get("sizing") or {}
        worst = sizing.get("total_commitment_estimate") or 0.0
        side_lines = int(sizing.get("side_lines")
                         or ((int(payloads["grid_bot"].get("grids") or 0) + 1) // 2)
                         or 1)
        cap = min(float(self.config["autonomy"]["full_pct"]),
                 float(self.config["portfolio"]["max_alloc_per_slot"]))
        cap_usd = cap * max(slot["balance"], 1e-9)
        if worst > cap_usd + 1e-9:
            min_grids = int(self.config.get("grid_defaults", {})
                            .get("min_grids", 5))
            # last resort: fit fewer lines at the exchange minimum
            fit_grids = int(2 * cap_usd / max(min_cost, 1e-9))
            if fit_grids >= min_grids:
                payloads = grid_adapter.build_ticket_payloads(
                    ticket, brief, slot["balance"], cap, profile["code"],
                    pair_code, exchange_code=exch,
                    amount_precision=amount_precision,
                    max_affordable_grids=fit_grids, min_cost=min_cost,
                    min_grids=min_grids)
                sizing = payloads["grid_bot"].get("sizing") or {}
                new_worst = sizing.get("total_commitment_estimate") or 0.0
                log(self.state, {"kind": "size-fit",
                                 "msg": f"{key}: worst-case ${worst:.0f} > "
                                        f"${cap_usd:.0f} cap — widened to "
                                        f"{payloads['grid_bot'].get('grids')} "
                                        f"grids (worst ${new_worst:.0f})"})
                if new_worst > cap_usd + 1e-9 or \
                        (sizing.get("usd_per_grid") or 0) < min_cost - 1e-9:
                    log(self.state, {"kind": "guard-veto",
                                     "msg": f"{key}: cannot fund ≥{min_grids} "
                                            f"lines at ${min_cost} within "
                                            f"{int(cap * 100)}% worst-case cap"})
                    return None, ticket, None, brief
                max_alloc_eff = cap
            else:
                log(self.state, {"kind": "guard-veto",
                                 "msg": f"{key}: slot ${slot['balance']:.0f} cannot "
                                        f"fund ≥{min_grids} lines at "
                                        f"${min_cost} within "
                                        f"{int(cap * 100)}% worst-case cap"})
                return None, ticket, None, brief
        elif worst > 1e-9:
            # min_cost funding needs a bigger tier than the ladder set —
            # raise the effective allocation to the honest worst case
            floor_alloc = worst / max(slot["balance"], 1e-9)
            if floor_alloc > max_alloc_eff + 1e-9:
                max_alloc_eff = floor_alloc
                # rebuild so guard_ctx.max_alloc matches the raised tier
                # (per-line is unchanged: min_cost dominates)
                payloads = grid_adapter.build_ticket_payloads(
                    ticket, brief, slot["balance"], max_alloc_eff,
                    profile["code"], pair_code, exchange_code=exch,
                    amount_precision=amount_precision, min_cost=min_cost)
                log(self.state, {"kind": "size-floor",
                                 "msg": f"{key}: funded "
                                        f"{payloads['grid_bot'].get('grids')} "
                                        f"grids at "
                                        f"${(sizing.get('usd_per_grid') or 0):.0f}"
                                        f"/line — worst-case "
                                        f"{max_alloc_eff:.0%} of slot"})

        ctx = dict(payloads["guard_ctx"], kill_file=os.path.join(HERE, "KILL"),
                   profiles_active=profiles_active,
                   profile_code=profile["code"],
                   deployable_ceiling=self.plan_slots()["deployable_ceiling"],
                   committed_now=sum(self.state["committed"].values()),
                   paper=True, is_rotation=is_rotation)
        if is_rotation:
            ctx.update({
                "cooldown_ok": cooldown_ok,
                "incumbent_score": (incumbent or {}).get("score_final") or 0,
                "candidate_score": cand.get("score_final") or 0,
                # manual rotate (ctl /rotate) overrides the score hysteresis;
                # every other guard (sizing, spread, venue, reliability) stays
                "hysteresis": 0.0 if (incumbent or {}).get("force_rotate")
                else float(self.config["policy"].get("hysteresis_score", 5.0)),
            })
        ok, violations = guard_deploy(ticket, ctx)
        if not ok:
            log(self.state, {"kind": "guard-veto",
                             "msg": f"{key}: {'; '.join(violations)[:200]}"})
            return None, ticket, None, brief

        action = {"kind": "deploy-paper" if dry_run else "DEPLOY-PAPER",
                  "slot": slot["slot"], "venue": cand["venue"],
                  "symbol": cand["symbol"], "grid_type": ticket["grid_type"],
                  "msg": f"slot {slot['slot']} {key} {ticket['grid_type']} "
                         f"step {payloads['grid_bot']['profit_per_grid_pct']}% "
                         f"x{payloads['grid_bot']['grids']} (dry_run={dry_run})",
                  "size_multiplier": max_alloc_eff, "escalation_tier": tier,
                  "profile": profile["code"]}
        decision_id = record_decision_safe(ticket, brief, action, payloads)
        action["decision_id"] = decision_id
        return action, ticket, payloads, brief

    def commit_deploy(self, action, ticket, payloads, brief, cand, slot, dry_run):
        """Create the bot (or journal the plan) and record active state."""
        archetype = cand.get("archetype") or cand.get("regime", "neutral")
        if dry_run:
            action["upsert"] = payloads["upsert"]
            log(self.state, action)
            return action
        res = retry_grid_call(grid_adapter.grid_create, False,
                              payloads["upsert"], cand["venue"], dry_run=False)
        action["result"] = res
        if not res.get("ok"):
            # surface the real WT reason (e.g. "Maximum number of Grid Bots
            # reached") instead of a bare DEPLOY-PAPER journal line
            msg = None
            try:
                msg = json.loads(res.get("stdout") or "").get("message")
            except Exception:
                pass
            action["kind"] = "deploy-failed"
            action["error"] = (msg or (res.get("stderr") or "")[:200]
                                or "grid_create ok=false")
        log(self.state, action)
        bot_code = extract_bot_code(res)
        if res.get("ok") and bot_code:
            channel = {
                "low": payloads["upsert"]["lowPrice"],
                "mid": payloads["upsert"]["midPrice"],
                "high": payloads["upsert"]["highPrice"],
                "step_pct": payloads["grid_bot"]["profit_per_grid_pct"],
                "atr_pct": brief.get("metrics", {}).get("atr_pct"),
                "grids": payloads["upsert"]["gridLevels"],
            }
            self.state["active_bots"][str(slot["slot"])] = {
                "symbol": cand["symbol"], "venue": cand["venue"],
                "since": utcnow(), "ticket": ticket,
                "score_final": cand["score_final"],
                "archetype": archetype,
                "stagnation_policy": payloads["stagnation_policy"],
                "bot_code": bot_code, "channel": channel,
                "profile_code": action.get("profile"), "pair_code": payloads["upsert"]["pairCode"],
                "upsert": payloads["upsert"],
                "decision_id": action.get("decision_id"),
                "size_multiplier": action.get("size_multiplier"),
            }
            self.state["committed"][str(slot["slot"])] =                 payloads["guard_ctx"]["total_commitment"]
            spec = build_spec(cand["symbol"], cand["tv_symbol"],
                              payloads["upsert"]["midPrice"],
                              payloads["upsert"]["gridPercentStep"] * 100,
                              payloads["upsert"]["gridLevels"], slot["slot"],
                              regime=cand["regime"])
            spec_path = os.path.join(
                SPECS_DIR, f"{cand['symbol'].lower()}-s{slot['slot']}.json")
            try:
                os.makedirs(os.path.dirname(spec_path), exist_ok=True)
                with open(spec_path, "w") as f:
                    json.dump(spec, f, indent=2)
            except Exception as exc:
                log(self.state, {"kind": "warn", "msg": f"spec write failed: {exc}"})
        return action

    def venue_capacity_block(self, cand, capacity):
        """(blocked_reason | None) — plan-level grid-bot capacity for a venue.

        From the upsert init data (maxActiveGridBots/activeGridBots/
        exchangesUsedPairs). Two rules:
          1. active-grid-bot cap per exchange tier — non-premium exchanges
             (free plan: everything except HYPERLIQUID_SWAP) share a single
             active-bot cap; premium exchanges have their own large cap.
             A blocked venue is rotation-only: stop+delete first frees it.
          2. one bot per pair per profile (server-validated pair exclusivity).
        Returns None when capacity data is unavailable — the guard-profile
        gate and the server-side 400 stay the backstops.
        """
        if not capacity:
            return None
        profiles = self.profiles or self.state.get("profiles") or []
        prof, _violation = select_profile(cand["venue"], profiles,
                                          self.config, paper=True)
        if not prof:
            return None  # profile gate handles it
        exch = (prof.get("exchange") or "").upper()
        active = capacity.get("active") or {}
        max_active = capacity.get("max_active") or {}
        premium_active = active.get("premium")
        if isinstance(premium_active, dict) and exch in premium_active:
            act, cap = premium_active.get(exch, 0), max_active.get("premium")
        else:
            act, cap = active.get("other"), max_active.get("other")
        if cap is not None and act is not None and act >= cap:
            return (f"plan cap: {act} active grid bot(s) on {exch} already "
                    f"at the max {cap} for its tier — venue is "
                    f"rotation-only until capacity frees")
        used = (capacity.get("used_pairs") or {}).get(exch) or {}
        if isinstance(used, dict):
            try:
                pair, _m = resolve_pair_safe(cand["venue"], cand["symbol"])
            except Exception:
                pair = None
            if pair and pair in (used.get(prof.get("code")) or []):
                return (f"pair {cand['venue']}:{cand['symbol']} already "
                        f"has a bot on profile {prof.get('name')}")
        return None

    # ── rescreen cycle ─────────────────────────────────────────────────
    def rescreen_cycle(self, dry_run=True, no_confluence=False, max_new=2,
                       top=None):
        actions = []
        top = top or getattr(self, "top", 30)
        # self-heal: refresh the profile snapshot every cycle (survives
        # browser restarts / Cloudflare blips that emptied the cache)
        try:
            fresh_profiles = grid_profiles_safe()
            if fresh_profiles:
                self.profiles = fresh_profiles
                self.state["profiles"] = fresh_profiles
            elif not self.profiles:
                log(self.state, {"kind": "health-warn",
                                  "msg": "grid_profiles() empty — browser/CF "
                                         "may be challenged; deployments will "
                                         "veto until it recovers"})
        except Exception as exc:
            log(self.state, {"kind": "health-warn",
                              "msg": f"profile refresh failed: {str(exc)[:120]}"})
        # subscription observation: enforced tier caps (upsert init) + the
        # dashboard plan view (account-limits). Journaled on any change so
        # plan upgrades/downgrades and the Hyperliquid premium tier are
        # noticed automatically.
        capacity = grid_capacity_safe()
        if capacity:
            self.state["capacity"] = capacity
        limits = account_limits_safe()
        if limits:
            self.state["account_limits"] = limits
        sig = _limits_signature(limits, capacity)
        if limits and sig != self.state.get("limits_signature"):
            self.state["limits_signature"] = sig
            gb = (limits.get("gridBots") or {})
            caps = (capacity or {}).get("max_active") or {}
            active = (capacity or {}).get("active") or {}
            premium = active.get("premium")
            premium_ex = sorted(premium.keys()) if isinstance(premium, dict) else []
            log(self.state, {"kind": "subscription",
                             "msg": f"gridBots {gb.get('active')}/"
                                    f"{gb.get('max')} (dashboard); tier caps "
                                    f"other={caps.get('other')} "
                                    f"premium={caps.get('premium')} "
                                    f"[premium: {','.join(premium_ex) or 'none'}]"})
        capacity_noted = set()  # one capacity-veto log per venue per cycle
        plan = self.plan_slots()
        if not self.state["slots"]:
            self.state["slots"] = plan["slots"]
        try:
            report = run_merge(top=top, no_confluence=no_confluence)
        except Exception as exc:
            log(self.state, {"kind": "screen-error", "msg": str(exc)[:200]})
            save_state(self.state)
            return actions
        cands = report.get("results", [])
        log(self.state, {"kind": "screen",
                         "msg": f"{len(cands)} candidates, top=" +
                                (f"{cands[0]['venue']}:{cands[0]['symbol']} "
                                 f"{cands[0]['score_final']}" if cands else "none")})
        used_slots = {int(s) for s in self.state["active_bots"]}
        active_keys = {f"{b.get('venue')}:{b.get('symbol')}"
                       for b in self.state["active_bots"].values()}
        free = [s for s in plan["slots"] if s["slot"] not in used_slots]
        deployed = 0
        deliberations, guards, deployments = [], [], []
        for cand in cands:
            if deployed >= max_new or not free:
                break
            key = f"{cand['venue']}:{cand['symbol']}"
            if key in active_keys:
                continue  # already running in another slot — no duplicate deploy
            if key in self.state["cooldowns_until"] and \
                    time.time() < self.state["cooldowns_until"][key]:
                continue
            slot = next((s for s in free if s["venue"] == cand["venue"]), None)
            if slot is None:
                continue  # no free slot on this venue
            blocked = self.venue_capacity_block(cand, capacity)
            if blocked:
                if cand["venue"] not in capacity_noted:
                    capacity_noted.add(cand["venue"])
                    log(self.state, {"kind": "capacity-veto",
                                     "msg": f"{key}: {blocked}"})
                continue
            action, ticket, payloads, brief = self.plan_candidate(cand, slot, dry_run)
            deliberations.append({
                "symbol": cand["symbol"], "venue": cand["venue"],
                "decision": ticket.get("decision", "NO_GO"),
                "confidence": ticket.get("confidence"),
                "llm_degraded": ticket.get("llm_degraded", False),
                "veto": ticket.get("veto"),
            })
            if action is None:
                continue
            guards.append({
                "symbol": cand["symbol"], "venue": cand["venue"],
                "ok": True, "violations": [],
            })
            self.commit_deploy(action, ticket, payloads, brief, cand, slot, dry_run)
            actions.append(action)
            deployments.append({
                "slot": slot["slot"], "symbol": cand["symbol"],
                "venue": cand["venue"], "grid_type": ticket.get("grid_type"),
                "step_pct": payloads["grid_bot"]["profit_per_grid_pct"],
                "amount": payloads["upsert"].get("amountPerTrade"),
                "multiplier": action.get("size_multiplier"),
                "paper": dry_run,
            })
            free.remove(slot)
            deployed += 1

        # ── rotation pass: stagnant incumbents vs better challengers ──
        rotations = []
        active_keys = {f"{b.get('venue')}:{b.get('symbol')}"
                       for b in self.state["active_bots"].values()}
        for slot_key, bot in list(self.state["active_bots"].items()):
            obs = bot.get("observed") or {}
            policy = bot.get("stagnation_policy") or {}
            fresh = next(
                (c for c in cands if c.get("venue") == bot.get("venue")
                 and c.get("symbol") == bot.get("symbol")), None)
            manual = bool(bot.get("force_rotate"))
            reasons = []
            if manual:
                reasons = ["manual rotate (ctl /rotate)"]
                stag = True
            else:
                # min-hold floor: never rotate a bot younger than min_hold_h
                # (fresh bots always look fill-less until the first expected
                # oscillation — rotating them is churn, not adaptation)
                try:
                    since = datetime.fromisoformat(bot["since"]) \
                        if bot.get("since") else None
                    age_h = (datetime.now(timezone.utc) - since
                             ).total_seconds() / 3600 if since else 0.0
                except Exception:
                    age_h = 0.0
                min_hold_h = float(self.config.get("policy", {})
                                   .get("min_hold_h", 24))
                if age_h < min_hold_h:
                    continue
            needs_re = bool(bot.get("needs_reanalysis"))
            if not manual and not policy.get("stagnant_if") and not needs_re:
                # adopted bots / policy-derivation failures: no thresholds yet
                continue
            if not manual:
                regime_now = fresh.get("regime") if fresh else None
                inc_score = bot.get("score_final") or 0
                fresh_score = (fresh.get("score_final") or inc_score) if fresh \
                    else inc_score
                score_drop = inc_score - fresh_score
                stag, reasons = is_stagnant(
                    obs, policy, regime_now=regime_now, score_drop=score_drop,
                    ladder_full=obs.get("ladder_full", False),
                    dd_vs_atr_band=obs.get("dd_vs_atr_band", 0.0))
                if not stag and needs_re:
                    # out-of-channel/stopped incumbent flagged by
                    # health_cycle: rotate even without fill stagnation
                    stag = True
                    reasons = list(reasons) + [
                        "needs_reanalysis (out-of-channel/stopped)"]
            if not stag:
                continue
            # challenger: best fresh candidate on the SAME venue as the slot,
            # not already active, cooldown-clean, different symbol
            slot_venue = bot.get("venue")
            challenger = next(
                (c for c in cands if c.get("venue") == slot_venue
                 and f"{c.get('venue')}:{c.get('symbol')}" not in active_keys
                 and c.get("symbol") != bot.get("symbol")
                 and not (f"{c.get('venue')}:{c.get('symbol')}"
                          in self.state["cooldowns_until"]
                          and time.time() < self.state["cooldowns_until"][
                              f"{c.get('venue')}:{c.get('symbol')}"])), None)
            if challenger is None:
                log(self.state, {"kind": "rotation-skip", "slot": slot_key,
                                 "msg": f"{bot.get('venue')}:{bot.get('symbol')} "
                                        f"stagnant ({'; '.join(reasons)[:100]}) "
                                        f"but no eligible challenger"})
                continue
            if self.execute_rotation(slot_key, challenger, dry_run):
                rotations.append({
                    "slot": int(slot_key), "from": f"{bot.get('venue')}:{bot.get('symbol')}",
                    "to": f"{challenger.get('venue')}:{challenger.get('symbol')}",
                    "reasons": reasons,
                })
        save_state(self.state)
        cycle_report = {
            "at": utcnow(), "cycle_kind": "rescreen",
            "dry_run": dry_run, "paper": dry_run,
            "screen": {"n_candidates": len(cands),
                       "top3": [{"venue": c.get("venue"), "symbol": c.get("symbol"),
                                 "regime": c.get("regime"),
                                 "score_final": c.get("score_final"),
                                 "step": c.get("step", c.get("step_pct"))}
                                for c in cands[:3]]},
            "deliberations": deliberations,
            "guard": guards,
            "deployments": deployments,
            "rotations": rotations,
            "observed": self.state.get("last_observe", {}),
            "reliability": self.reliability,
            "caveats": ["dry-run: zero WunderTrading mutations"] if dry_run else [],
            "actions": actions,
            "active_slots": sorted(self.state["active_bots"]),
        }
        write_run_card_safe(cycle_report)
        return actions

    # ── health poll ────────────────────────────────────────────────────
    def health_cycle(self, dry_run=True):
        if not self.state["active_bots"]:
            return
        observed_all = observe_all_safe(self.state["active_bots"])
        self.state["last_observe"] = observed_all
        for slot_key, bot in list(self.state["active_bots"].items()):
            obs = observed_all.get(slot_key, observed_all.get(int(slot_key), {}))
            bot["observed"] = obs
            bot["last_observed"] = utcnow()
            if obs.get("error"):
                log(self.state, {"kind": "health-warn", "slot": slot_key,
                                 "msg": f"observe error: {obs['error'][:160]}"})
                continue
            policy = bot.get("stagnation_policy") or {}
            status = (obs.get("status") or "active").lower()
            price = obs.get("price")
            channel = bot.get("channel") or {}

            # reliability kill-flag review for existing bots
            archetype = bot.get("archetype") or bot.get("ticket", {}).get("regime")
            if refuse_new_archetype(self.reliability, archetype):
                log(self.state, {"kind": "reliability-flag", "slot": slot_key,
                                 "msg": f"{bot.get('venue')}:{bot.get('symbol')} "
                                        f"archetype {archetype} recent_pf<1.0 — "
                                        f"flagged for rotation review"})

            # stagnation evaluation (drives rotation candidates)
            stag, reasons = is_stagnant(
                obs, policy, regime_now=obs.get("regime_now"),
                score_drop=obs.get("score_drop", 0.0),
                ladder_full=obs.get("ladder_full", False),
                dd_vs_atr_band=obs.get("dd_vs_atr_band", 0.0))
            if stag:
                log(self.state, {"kind": "stagnant", "slot": slot_key,
                                 "msg": f"{bot.get('venue')}:{bot.get('symbol')}: "
                                        f"{'; '.join(reasons)[:160]}"})

            out_of_channel = False
            if price is not None and channel.get("high") and channel.get("low"):
                out_of_channel = price > channel["high"] or price < channel["low"]
            if out_of_channel or status in ("stopped", "closed",
                                            "stopped_and_close_all"):
                bot["needs_reanalysis"] = True
                log(self.state, {"kind": "re-analysis", "slot": slot_key,
                                 "msg": f"{bot.get('venue')}:{bot.get('symbol')} "
                                        f"out-of-channel/stopped — mark for re-analysis"})
                continue
            # back in channel and running: an earlier re-analysis mark is stale
            bot.pop("needs_reanalysis", None)

            # inside channel: drift → regime re-check → in-place adjust
            if price is not None and channel.get("mid") and channel.get("step_pct"):
                step = channel["step_pct"] / 100.0
                drift = abs(price - channel["mid"]) / (channel["mid"] * step) if step else 0
                if drift > float(self.config["watch"]["adjust_steps_threshold"]):
                    try:
                        regime_now = reclassify_regime(bot["venue"], bot["symbol"])
                    except Exception as exc:
                        log(self.state, {"kind": "health-warn", "slot": slot_key,
                                         "msg": f"regime recheck failed: {exc}"})
                        continue
                    if regime_now == policy.get("regime"):
                        self.adjust_bot(slot_key, dry_run)
                    else:
                        log(self.state, {"kind": "regime-changed", "slot": slot_key,
                                         "msg": f"{bot.get('venue')}:{bot.get('symbol')} "
                                                f"{policy.get('regime')}→{regime_now} "
                                                f"(no in-place adjust)"})
        save_state(self.state)

    def adjust_bot(self, slot_key, dry_run=True):
        bot = self.state["active_bots"].get(slot_key)
        if not bot or not bot.get("bot_code"):
            return
        last = self.state.get("last_adjust", {}).get(slot_key)
        now = time.time()
        if last and now - last < 6 * 3600:
            log(self.state, {"kind": "adjust-skip", "slot": slot_key,
                             "msg": f"rate limit (1 edit/6h) for {bot.get('symbol')}"})
            return
        venue, symbol = bot["venue"], bot["symbol"]
        try:
            from market_regime import fetch_candles, compute_metrics
            cl = fetch_candles(venue, fetch_symbol(venue, symbol), "1h", 300,
                               "futures" if venue == "hyperliquid" else "spot")
            m = compute_metrics(cl)
        except Exception as exc:
            log(self.state, {"kind": "adjust-error", "slot": slot_key,
                             "msg": f"metrics fetch failed: {exc}"})
            return
        price, atr_pct = m["price"], m["atr_pct"]
        channel = bot.get("channel") or {}
        upsert_old = bot.get("upsert") or {}
        step_pct = channel.get("step_pct") or (upsert_old.get("gridPercentStep", 0) * 100)
        grids = channel.get("grids") or upsert_old.get("gridLevels", 0)
        amount = upsert_old.get("amountPerTrade", 0)
        grid_type = (bot.get("ticket") or {}).get("grid_type", "neutral")
        profile_code = bot.get("profile_code")
        pair_code = bot.get("pair_code") or upsert_old.get("pairCode")
        try:
            new_upsert = grid_adapter.compute_upsert(
                symbol, venue, price, atr_pct, step_pct, grids, amount,
                grid_type, profile_code, pair_code)
        except Exception as exc:
            log(self.state, {"kind": "adjust-error", "slot": slot_key,
                             "msg": f"compute_upsert failed: {exc}"})
            return
        res = grid_edit_safe(bot["bot_code"], new_upsert, dry_run=dry_run)
        self.state.setdefault("last_adjust", {})[slot_key] = now
        bot["last_adjust"] = now
        bot["upsert"] = new_upsert
        bot["channel"] = {
            "low": new_upsert["lowPrice"], "mid": new_upsert["midPrice"],
            "high": new_upsert["highPrice"], "step_pct": step_pct,
            "atr_pct": atr_pct, "grids": grids,
        }
        log(self.state, {"kind": "adjust", "slot": slot_key, "dry_run": dry_run,
                         "msg": f"{venue}:{symbol} re-centered at {price} "
                                f"(step {step_pct}%, grids {grids})",
                         "result": res})

    # ── rotation ───────────────────────────────────────────────────────
    def execute_rotation(self, slot_key, challenger, dry_run=True):
        incumbent = self.state["active_bots"].get(slot_key)
        if not incumbent:
            return False
        policy = incumbent.get("stagnation_policy") or {}
        observed = incumbent.get("observed") or {}
        manual = bool(incumbent.get("force_rotate"))
        if manual:
            ok_rot, reasons = True, ["manual rotate (ctl /rotate)"]
        else:
            try:
                ok_rot, reasons = should_rotate(
                    challenger, incumbent, policy, observed, time.time(),
                    needs_reanalysis=bool(incumbent.get("needs_reanalysis")))
            except Exception as exc:
                ok_rot, reasons = False, [f"rotation eval failed: {exc}"]
        if not ok_rot:
            log(self.state, {"kind": "rotation-veto", "slot": slot_key,
                             "msg": f"{'; '.join(reasons)[:160]}"})
            return False
        key = f"{incumbent.get('venue')}:{incumbent.get('symbol')}"
        cooldown_ok = time.time() >= self.state["cooldowns_until"].get(key, 0)
        plan = self.plan_slots()
        slot = next((s for s in plan["slots"] if s["slot"] == int(slot_key)), None)
        if slot is None:
            log(self.state, {"kind": "rotation-veto", "slot": slot_key,
                             "msg": "slot missing from plan"})
            return False
        action, ticket, payloads, brief = self.plan_candidate(
            challenger, slot, dry_run, is_rotation=True,
            incumbent=incumbent, cooldown_ok=cooldown_ok)
        if action is None:
            return False
        # stop → verify → delete → cooldown → clear → deploy challenger
        bot_code = incumbent.get("bot_code")
        # an already-stopped incumbent (health_cycle flags stopped bots for
        # re-analysis) cannot be stopped again — WT answers 400 "already
        # stopped", which used to veto the rotation forever. Treat a
        # verified-stopped bot as successfully stopped.
        already_stopped = False
        if not dry_run:
            try:
                for b in grid_status_safe() or []:
                    if b.get("code") == bot_code and                             (b.get("status") or "").lower() in STOPPED_STATES:
                        already_stopped = True
                        break
            except Exception:
                pass
        if already_stopped:
            stop_res = {"ok": True, "status": "already stopped", "skipped": True}
        else:
            stop_res = retry_grid_call(grid_adapter.grid_stop, dry_run, bot_code,
                                       "stop_and_close_all", dry_run=dry_run)
        log(self.state, {"kind": "rotation-stop", "slot": slot_key,
                         "msg": f"stop {bot_code}", "result": stop_res})
        if dry_run:
            status_res = {"ok": True, "dry_run": True, "status": "stopped"}
        else:
            status_res = None
            try:
                gs = grid_status_safe()
                for b in gs:
                    if b.get("code") == bot_code:
                        st = (b.get("status") or "").lower()
                        status_res = {"ok": st in STOPPED_STATES,
                                      "status": st}
                        break
            except Exception as exc:
                status_res = {"ok": False, "error": str(exc)[:160]}
        stop_failed = not (stop_res or {}).get("ok") and "already stopped"             not in json.dumps(stop_res or {}).lower()
        if not dry_run and stop_failed:
            log(self.state, {"kind": "rotation-veto", "slot": slot_key,
                             "msg": f"stop failed for {bot_code} — incumbent "
                                    f"kept, challenger not deployed"})
            return False
        if not dry_run and status_res is not None and \
                not status_res.get("ok"):
            # bot listed but still running — never delete a live bot
            log(self.state, {"kind": "rotation-veto", "slot": slot_key,
                             "msg": f"stop not verified for {bot_code} "
                                    f"(status={status_res.get('status')}) — "
                                    f"incumbent kept"})
            return False
        if status_res is None and not dry_run:
            # stop reported ok but the bot is not listed — deletion of an
            # already-gone bot is harmless, so proceed with a warning
            log(self.state, {"kind": "rotation-warn", "slot": slot_key,
                             "msg": f"could not verify stop for {bot_code} "
                                    f"(not listed) — proceeding"})
        del_res = retry_grid_call(grid_adapter.grid_delete, dry_run, bot_code,
                                  dry_run=dry_run)
        log(self.state, {"kind": "rotation-delete", "slot": slot_key,
                         "msg": f"delete {bot_code}", "result": del_res})
        self.state["cooldowns_until"][key] = time.time() + policy.get(
            "cooldown_h", 24.0) * 3600
        self.state["active_bots"].pop(slot_key, None)
        self.state["committed"].pop(slot_key, None)
        # reflect contract: {reason, realized_pnl, fills, holding_h, observed}
        obs_out = incumbent.get("observed") or {}
        try:
            since = datetime.fromisoformat(incumbent.get("since") or "") \
                if incumbent.get("since") else None
            holding_h = round((datetime.now(timezone.utc) - since).total_seconds()
                              / 3600, 1) if since else None
        except Exception:
            holding_h = None
        record_outcome_safe(incumbent.get("decision_id"), {
            "reason": "stagnant" if reasons and "fills" in reasons[0]
            else (reasons[0][:60] if reasons else "rotated"),
            "realized_pnl": obs_out.get("realized_pnl_24h",
                                        obs_out.get("unrealized_pnl")),
            "fills": obs_out.get("fills_24h"),
            "holding_h": holding_h,
            "observed": obs_out,
            "challenger": f"{challenger.get('venue')}:{challenger.get('symbol')}",
        })
        incumbent.pop("force_rotate", None)
        self.commit_deploy(action, ticket, payloads, brief, challenger, slot,
                           dry_run)
        if not dry_run and str(slot["slot"]) not in self.state["active_bots"]:
            # challenger create failed after the incumbent was removed:
            # the slot stays empty and the next rescreen refills it — make
            # the gap visible instead of logging a plain success
            log(self.state, {"kind": "rotation-error", "slot": slot_key,
                             "msg": f"challenger "
                                    f"{challenger.get('venue')}:"
                                    f"{challenger.get('symbol')} deploy "
                                    f"failed after incumbent removal — slot "
                                    f"left empty, next rescreen will refill"})
        log(self.state, {"kind": "rotate", "slot": slot_key,
                         "msg": f"{key} → {challenger.get('venue')}:"
                                f"{challenger.get('symbol')} "
                                f"({'; '.join(reasons)[:120]})"})
        save_state(self.state)
        return True

    # ── first-run adoption ─────────────────────────────────────────────
    def adopt_existing(self, dry_run=True):
        if not self.config.get("adopt_existing"):
            return
        bots = grid_status_safe()
        if not bots:
            return
        plan = self.plan_slots()
        occupied = set(self.state["active_bots"])
        free = [s for s in plan["slots"]
                if str(s["slot"]) not in occupied]
        # never double-track: skip bots already known to state (by code) and
        # symbols already active in another slot (duplicate deploy)
        tracked_codes = {bb.get("bot_code") for bb in
                         self.state["active_bots"].values()}
        tracked_keys = {f"{bb.get('venue')}:{bb.get('symbol')}" for bb in
                        self.state["active_bots"].values()}
        for b in bots:
            if not b.get("paperTrading"):
                continue
            if (b.get("status") or "active").lower() != "active":
                continue
            if b.get("code") in tracked_codes:
                continue  # already tracked in a slot
            venue = venue_from_exchange(b.get("exchange"))
            symbol = symbol_from_pair(b.get("pair") or b.get("pairCode"), venue)
            funded = self.config["portfolio"]["venues"]
            if venue not in funded or not symbol or not free:
                log(self.state, {"kind": "adopt-warn",
                                 "msg": f"paper bot {b.get('code')} "
                                        f"({b.get('pair')}) left running — "
                                        f"not adopted (no venue/slot match)"})
                continue
            if f"{venue}:{symbol}" in tracked_keys:
                log(self.state, {"kind": "adopt-warn",
                                 "msg": f"paper bot {b.get('code')} "
                                        f"({b.get('pair')}) left running — "
                                        f"{venue}:{symbol} already active in "
                                        f"another slot"})
                continue
            slot = next((s for s in free if s["venue"] == venue), None)
            if slot is None:
                log(self.state, {"kind": "adopt-warn",
                                 "msg": f"paper bot {b.get('code')} "
                                        f"({b.get('pair')}) left running — "
                                        f"no free {venue} slot"})
                continue
            free.remove(slot)
            try:
                from market_regime import fetch_candles
                cl = fetch_candles(venue, fetch_symbol(venue, symbol), "1h", 300,
                                   "futures" if venue == "hyperliquid" else "spot")
                policy = derive_policy([c[3] for c in cl], "1h", 0.5, "neutral")
            except Exception as exc:
                policy = {"error": str(exc)[:120]}
            self.state["active_bots"][str(slot["slot"])] = {
                "symbol": symbol, "venue": venue,
                "since": utcnow(), "adopted": True,
                "bot_code": b.get("code"),
                "ticket": {"symbol": symbol, "venue": venue,
                           "decision": "GO", "grid_type": "neutral"},
                "score_final": None,
                "archetype": None,
                "stagnation_policy": policy,
                "channel": None,
                "profile_code": None, "pair_code": b.get("pairCode"),
                "upsert": None, "decision_id": None,
            }
            log(self.state, {"kind": "adopted", "slot": slot["slot"],
                             "msg": f"{venue}:{symbol} bot {b.get('code')} "
                                    f"adopted into slot {slot['slot']}"})
        save_state(self.state)

    # ── reliability cron (24h) ────────────────────────────────────────
    def reliability_cycle(self):
        """Refresh the reliability ledger, then reload it into the daemon.

        Measurement: export each active bot's closed round-trips
        (`bot_trades`), aggregate per archetype (`archetype_stats`), and
        merge into `state/reliability.json` — zero-sample archetypes never
        erase existing entries, so history from already-deleted bots
        survives. The reloaded ledger gates sizing escalation
        (base → probe → full) and archetype kill-flags. Never raises.
        """
        try:
            by_archetype = {}
            for bot in (self.state.get("active_bots") or {}).values():
                code = bot.get("bot_code")
                if not code:
                    continue
                by_archetype.setdefault(bot.get("archetype") or "unknown",
                                        []).extend(bot_trades(code))
            stats = archetype_stats(by_archetype) if by_archetype else {}
            merged = dict(self.reliability or {})
            fresh = 0
            for arch, st in (stats or {}).items():
                if st.get("samples"):
                    merged[arch] = st
                    fresh += 1
            if fresh:
                save_reliability(merged)
                log(self.state, {"kind": "reliability",
                                 "msg": f"computed {fresh} archetype(s) from "
                                        f"closed round-trips"})
        except Exception as exc:
            log(self.state, {"kind": "reliability-error",
                             "msg": f"compute failed: {exc}"})
        self.reliability = reliability_load_safe()
        self.state["reliability"] = self.reliability
        log(self.state, {"kind": "reliability",
                         "msg": f"reloaded ({len(self.reliability)} keys)"})
        save_state(self.state)

    # ── manage loop ────────────────────────────────────────────────────
    def run(self, once=False, dry_run=True, no_confluence=False, top=None):
        self.top = top
        t = threading.Thread(target=serve_ctl, args=(self, self.port), daemon=True)
        t.start()
        print(f"ctl on 127.0.0.1:{self.port} (dry_run={dry_run})", flush=True)
        if os.path.exists(os.path.join(HERE, "KILL")):
            print("KILL present — refusing to run", flush=True)
            return []
        try:
            self.adopt_existing(dry_run)
        except Exception as exc:
            log(self.state, {"kind": "adopt-error", "msg": str(exc)[:160]})
        actions = []
        try:
            actions = self.rescreen_cycle(dry_run=dry_run,
                                          no_confluence=no_confluence,
                                          top=self.top)
        except Exception as exc:
            log(self.state, {"kind": "rescreen-error", "msg": str(exc)[:200],
                             "tb": traceback.format_exc(limit=6)[-1200:]})
        if once:
            try:
                self.health_cycle(dry_run)
            except Exception as exc:
                log(self.state, {"kind": "health-error", "msg": str(exc)[:200],
                             "tb": traceback.format_exc(limit=6)[-1200:]})
            self.state["last_cycle"] = utcnow()
            save_state(self.state)
            return actions

        interval_s = float(self.config["watch"]["interval_s"])
        rescreen_s = float(self.config["screen"]["rescreen_minutes"]) * 60
        reliability_s = 24 * 3600
        next_health = time.time() + interval_s
        next_rescreen = time.time() + rescreen_s
        next_reliability = time.time() + reliability_s
        while True:
            if os.path.exists(os.path.join(HERE, "KILL")):
                log(self.state, {"kind": "kill", "msg": "KILL file — halting"})
                save_state(self.state)
                break
            now = time.time()
            if now >= next_reliability:
                try:
                    self.reliability_cycle()
                except Exception as exc:
                    log(self.state, {"kind": "reliability-error", "msg": str(exc)[:160]})
                next_reliability = now + reliability_s
            if self.consume_rescreen():
                try:
                    self.rescreen_cycle(dry_run=dry_run,
                                        no_confluence=no_confluence,
                                        top=self.top)
                except Exception as exc:
                    log(self.state, {"kind": "rescreen-error", "msg": str(exc)[:200],
                             "tb": traceback.format_exc(limit=6)[-1200:]})
                next_rescreen = time.time() + rescreen_s
            if now >= next_health:
                try:
                    self.health_cycle(dry_run)
                except Exception as exc:
                    log(self.state, {"kind": "health-error", "msg": str(exc)[:200],
                             "tb": traceback.format_exc(limit=6)[-1200:]})
                next_health = time.time() + interval_s
            if now >= next_rescreen:
                try:
                    self.rescreen_cycle(dry_run=dry_run,
                                        no_confluence=no_confluence,
                                        top=self.top)
                except Exception as exc:
                    log(self.state, {"kind": "rescreen-error", "msg": str(exc)[:200],
                             "tb": traceback.format_exc(limit=6)[-1200:]})
                next_rescreen = time.time() + rescreen_s
            self.state["last_cycle"] = utcnow()
            save_state(self.state)
            nxt = min(next_health, next_rescreen, next_reliability)
            time.sleep(max(1.0, min(10.0, nxt - time.time())))



def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="plan only (default; --live-paper turns this off)")
    ap.add_argument("--live-paper", action="store_true",
                    help="actually create paper bots (default: plan only)")
    ap.add_argument("--no-confluence", action="store_true")
    ap.add_argument("--top", type=int, default=30, help="merge --top passthrough")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    d = Daemon(port=args.port)
    d.run(once=args.once, dry_run=not args.live_paper,
          no_confluence=args.no_confluence, top=args.top)


if __name__ == "__main__":
    main()
