#!/usr/bin/env python3
"""Slot optimizer — the fast (2–5 min) capital-reallocation engine.

The rescreen cycle (60 min) is thorough but slow: a slot that goes quiet
waits up to an hour for attention, and the rotation gate (24 h min-hold +
~24 h stagnation windows) is tuned for "is this archetype still viable",
not "is this slot earning right now". The optimizer runs between rescreens
on a 2–5 min cadence and answers one question:

    Is a slot idle while a measurably better token exists?
    → swap the slot to the better token NOW.

Per cycle (fail-soft at every step, never raises):

  1. TRACK     update per-slot fill trackers from the health poll's
               observed.fills_24h (no extra WT calls — health_cycle
               refreshes observations every 60 s).
  2. IDLE      token-relative inactivity: a slot is idle when no fill has
               landed for max(idle_minutes floor [5], idle_k × the token's
               own expected fill interval). A 200-fill/day token is flagged
               after ~7 min; a 20-fill/day token needs ~72 min — "no
               activity" is measured against what the token promised, and
               needs_reanalysis (out-of-channel/stopped) counts as idle
               immediately.
  3. HUNT      fast challenger board: refresh the rescreen's cached
               candidates (screen_cache) with live 1h candles + preset
               scoring + grid-fill EV, enrich incumbents AND challengers
               with tvcli /hunt structure on 15m (squeeze/choppiness —
               the fast tape the hourly 1H pass never sees), re-rank by
               score_final.
  4. ARBITER   one LLM call per idle slot with a live challenger: the
               Mistral-pinned arbiter compares structure + EV + idle
               context and returns a strict-JSON verdict. It may approve a
               swap inside the relaxed margin band (Δscore ≥ hysteresis)
               with confidence ≥ 0.7; it can NEVER approve below the hard
               floor. Numeric gate → rule fallback when the LLM is down.
  5. SWAP      execute_rotation with force_rotate (reuses the entire
               stop → verify → delete → archive → guard → deliberate →
               deploy machinery). Churn-bounded: fast min-hold (20 min),
               per-slot swap interval (30 min), max swaps/hour (3),
               challenger cooldowns, upgrade margin (Δscore ≥ 8, or ≥ 5
               with arbiter backing).
  6. CAPITAL   free slots + a deployable challenger (score ≥
               open_slot_min_score) nudge a rescreen (the deploy/open-slot
               capital logic stays there); the report quantifies idle
               committed capital so the fleet's fund utilization is
               visible per cycle.

State: state["optimizer"] = {enabled, interval_min, cycles, swaps_total,
last_at, last_report, trackers, swap_log}. The daemon persists
state["screen_cache"] from every rescreen (the optimizer's candidate
pool). HTTP ctl: GET /optimizer, POST /optimize.

Usage:
  optimizer.py --once [--json]        # one cycle against live state
  optimizer.py --policy               # print the merged optimizer config
  from optimizer import SlotOptimizer # wired by daemon.py (defensive)
"""
import argparse
import copy
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
for sub in ("llm", "screen", "policy", "agents", "execution"):
    sys.path.insert(0, os.path.join(HERE, sub))
WUN_SCRIPTS = os.path.normpath(os.path.join(
    HERE, "..", "..", ".agents", "skills", "wundertrading", "scripts"))
sys.path.insert(0, WUN_SCRIPTS)

# ── defensive imports (module must import standalone, tests included) ──
try:
    from market_regime import fetch_candles, compute_metrics, classify
    HAS_MARKET = True
except Exception:
    HAS_MARKET = False

try:
    from universe_screen import load_presets, score as preset_score, \
        derived_step
    HAS_PRESETS = True
except Exception:
    HAS_PRESETS = False

try:
    from stagnation import simulate_grid_fills
    HAS_STAG = True
except Exception:
    HAS_STAG = False

try:
    from merge import tv_hunt, tvcli_fitness, fetch_symbol
    HAS_MERGE = True
except Exception:
    HAS_MERGE = False

    def fetch_symbol(venue, symbol):
        return symbol

try:
    from provider import chat_json, named_chain
    HAS_LLM = True
except Exception:
    HAS_LLM = False

try:
    from guardrails import ROUND_TRIP_FEE_PCT
except Exception:
    ROUND_TRIP_FEE_PCT = {"hyperliquid": 0.10, "binance": 0.20}

# ── config defaults (config.yaml `optimizer:` section overrides) ───────
OPTIMIZER_DEFAULTS = {
    "enabled": True,
    "interval_min": 3,          # fast cadence, clamped to 2–5 by the daemon
    "idle_minutes": 5.0,        # absolute no-activity floor
    "idle_k": 1.0,              # × the token's expected fill interval
    "min_hold_min": 20,         # fast churn guard (NOT the 24h rescreen floor)
    "upgrade_margin": 8.0,      # challenger must beat incumbent by this much
    "arbiter_margin": 5.0,      # …or by this much with arbiter backing
    "arbiter_min_confidence": 0.7,
    "min_swap_interval_min": 30,   # per-slot rate limit on SUCCESSFUL swaps
    "max_swaps_per_hour": 3,       # successful swaps
    "max_attempts_per_hour": 6,    # attempts (guard-vetoed tries included)
    "max_attempts_per_slot": 2,    # challengers tried per idle slot per cycle
    "fail_cooldown_min": 60,       # failed swap → cool down THAT challenger
    "hunt_top": 8,              # challengers refreshed per cycle
    "hunt_skills": ["squeeze", "choppiness"],
    "hunt_timeframe": "15m",    # fast tape the hourly 1H pass never sees
    "hunt_bars": 96,
    "refresh_limit": 180,       # 1h candles fetched per refresh (light)
    "screen_cache_fresh_min": 120,  # older than this → wait for rescreen
    "refill_nudge_min": 10,     # empty-slot rescreen nudge rate limit
    "llm_provider": "mistral",  # arbiter pinned here when creds exist
}

ARB_SYS = ("You are a fast crypto grid-trading slot arbiter. You realloc "
           "capital between grid slots on a 2–5 minute cadence. Reply with "
           "STRICT JSON only, no markdown fences, no commentary. Strings at "
           "most 25 words. Judge from STRUCTURE (squeeze/chop state), "
           "expected-value (fills × net step), and idle time — not price "
           "direction guesses.")


def merge_cfg(config):
    """OPTIMIZER_DEFAULTS ← config.yaml `optimizer:` section."""
    cfg = dict(OPTIMIZER_DEFAULTS)
    section = (config or {}).get("optimizer") or {}
    for k, v in section.items():
        if v is not None:
            cfg[k] = v
    return cfg


# ── pure decision core (no network, no daemon) ─────────────────────────

def update_tracker(tracker, fills_24h, now):
    """Fold one fills_24h observation into a slot's activity tracker.

    Pure: returns a NEW dict. `last_increase_at` only ever moves forward —
    the epoch of the most recent observation where the 24h fill count went
    UP. First sight of a non-zero count sets it to now (we cannot know the
    true last-fill time; optimistic = fail-closed against churn). A
    non-numeric observation (observe glitch) leaves the tracker untouched.
    """
    tracker = tracker if isinstance(tracker, dict) else {}
    try:
        fills = float(fills_24h)
    except (TypeError, ValueError):
        return dict(tracker)
    last = tracker.get("last_fills")
    bumped = last is None or fills > float(last)
    return {
        "last_fills": fills,
        "last_increase_at": now if bumped or tracker.get(
            "last_increase_at") is None else tracker["last_increase_at"],
    }


def expected_fill_interval_min(expected_fills_per_24h):
    """Minutes between fills the token's own history promised (None=unknown)."""
    try:
        exp = float(expected_fills_per_24h or 0)
    except (TypeError, ValueError):
        return None
    if exp <= 0:
        return None
    return 24 * 60.0 / exp


def idle_threshold_min(cfg, expected_fills_per_24h):
    """Idle cutoff = max(floor, k × expected interval) — never below the
    configured floor (default 5 min), scaled up for slow tokens so a
    20-fill/day grid is not called dead after five quiet minutes."""
    floor = float(cfg.get("idle_minutes", 5.0))
    interval = expected_fill_interval_min(expected_fills_per_24h)
    if interval is None:
        return floor
    return max(floor, float(cfg.get("idle_k", 1.0)) * interval)


def _bot_age_min(bot, now):
    try:
        since = datetime.fromisoformat(bot["since"]) \
            if bot.get("since") else None
        if since is None:
            return None
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        return (now - since.timestamp()) / 60.0
    except Exception:
        return None


def is_idle(bot, tracker, now, cfg):
    """(idle, [reasons]) for one active bot. Fail-closed on blindness.

    A stopped/out-of-channel bot (needs_reanalysis, set by health_cycle) is
    idle immediately — it earns nothing while it waits for the hourly
    rescreen. An observe error is NEVER idle (missing fills would fake
    inactivity and churn the fleet during an outage).
    """
    obs = bot.get("observed") or {}
    if obs.get("error"):
        return False, ["observe error — fail closed"]
    # NEVER swap out a bot with open positions under water: the swap's
    # stop is stop_and_close_all, which would realize the mark loss. The
    # incumbent keeps running — its grid keeps working the position back
    # toward break-even (and health_cycle may re-center the channel).
    # This check precedes needs_reanalysis: even an out-of-channel bot
    # is held while its positions are under water. Per-LINE first (a
    # net-positive aggregate can still hide one losing line that the
    # close would realize), aggregate as the backstop when per-line data
    # is unavailable (open_losing is None).
    losing = obs.get("open_losing")
    if losing is not None and losing:
        return False, [f"{losing} open line(s) at a loss "
                       f"(unrealized ${obs.get('unrealized_pnl')}) — "
                       f"held for recovery, never closed at a loss"]
    pnl = obs.get("unrealized_pnl")
    if losing is None and pnl is not None and float(pnl) < 0:
        return False, [f"open position at loss (${float(pnl):.2f} "
                       f"unrealized) — held for recovery, never closed "
                       f"at a loss"]
    if bot.get("needs_reanalysis"):
        return True, ["needs_reanalysis (out-of-channel/stopped)"]
    if (obs.get("status") or "active").lower() in ("stopped", "stopped_all",
                                                   "stopping"):
        return True, [f"status {obs.get('status')}"]
    tracker = tracker if isinstance(tracker, dict) else {}
    last_increase = tracker.get("last_increase_at")
    if last_increase is None:
        return False, ["no activity history yet"]
    idle_min = (now - float(last_increase)) / 60.0
    policy = bot.get("stagnation_policy") or {}
    exp = policy.get("expected_fills_per_24h")
    threshold = idle_threshold_min(cfg, exp)
    age_min = _bot_age_min(bot, now)
    if age_min is not None and age_min < float(cfg.get("min_hold_min", 20)):
        return False, [f"age {age_min:.0f}m < min_hold {cfg.get('min_hold_min')}m"]
    if idle_min >= threshold:
        why = (f"no fills for {idle_min:.0f}m "
               f"(threshold {threshold:.0f}m"
               + (f", expected {exp}/24h" if exp else ", fill rate unknown")
               + ")")
        return True, [why]
    return False, [f"active ({idle_min:.0f}m since last fill, "
                   f"threshold {threshold:.0f}m)"]


def eligible_challengers(cands, venue, active_keys, cooldowns_until, now):
    """Challengers for a slot: same venue, not already running anywhere,
    cooldown-clean. Sorted by score_final desc."""
    out = []
    for c in cands or []:
        if c.get("venue") != venue or not c.get("symbol"):
            continue
        key = f"{c.get('venue')}:{c.get('symbol')}"
        if key in (active_keys or set()):
            continue
        until = (cooldowns_until or {}).get(key, 0)
        if now < until:
            continue
        out.append(c)
    out.sort(key=lambda c: c.get("score_final") or 0, reverse=True)
    return out


def swap_gate(incumbent, challenger, arbiter, cfg, swap_log, now, slot_key,
              slot_rate_log=None):
    """(approve, [reasons]) — the full fast-swap decision. Pure.

    Layers (all must pass):
      rate limits   per-slot min interval between SUCCESSFUL swaps (over
                    `slot_rate_log`, which defaults to swap_log — callers
                    pass the pre-cycle log so a same-cycle retry after a
                    failed ATTEMPT is not blocked by its own attempt) +
                    global swaps/hour cap. Failed attempts do NOT rate-
                    limit the slot: nothing was rotated, and the failing
                    challenger is cooled down by the caller instead —
                    otherwise one undeployable token would lock an idle
                    slot out of every other challenger for 30 min.
      margin        Δscore ≥ upgrade_margin (numeric alone), or ≥
                    arbiter_margin with an arbiter approve at confidence ≥
                    arbiter_min_confidence. Below arbiter_margin nothing
                    approves — the arbiter can relax the bar, never remove it.
    """
    reasons = []
    rate_log = slot_rate_log if slot_rate_log is not None else swap_log
    for entry in (rate_log or []):
        if entry.get("slot") == str(slot_key) and entry.get("ok") and \
                now - float(entry.get("at", 0)) < \
                float(cfg.get("min_swap_interval_min", 30)) * 60:
            age = (now - float(entry.get("at", 0))) / 60
            return False, [f"swap rate limit: last swap {age:.0f}m ago "
                           f"(min {cfg.get('min_swap_interval_min')}m)"]
    recent = [e for e in (swap_log or [])
              if now - float(e.get("at", 0)) < 3600]
    swaps_ok = [e for e in recent if e.get("ok")]
    if len(swaps_ok) >= int(cfg.get("max_swaps_per_hour", 3)):
        return False, [f"global rate limit: {len(swaps_ok)} swaps in the "
                       f"last hour (max {cfg.get('max_swaps_per_hour')})"]
    if len(recent) >= int(cfg.get("max_attempts_per_hour", 6)):
        return False, [f"attempt rate limit: {len(recent)} attempts in the "
                       f"last hour (max {cfg.get('max_attempts_per_hour')})"]

    inc_score = incumbent.get("score_final") or 0
    ch_score = challenger.get("score_final") or 0
    margin = ch_score - inc_score
    hard = float(cfg.get("arbiter_margin", 5.0))
    upg = float(cfg.get("upgrade_margin", 8.0))
    if margin >= upg:
        return True, [f"Δscore {margin:.1f} ≥ upgrade margin {upg:.0f}"]
    if margin >= hard:
        a = arbiter if isinstance(arbiter, dict) else {}
        conf = a.get("confidence") or 0
        if a.get("approve") and conf >= float(
                cfg.get("arbiter_min_confidence", 0.7)):
            return True, [
                f"arbiter approved: Δscore {margin:.1f} in relaxed band "
                f"[{hard:.0f}, {upg:.0f}), confidence {conf}",
                f"arbiter: {str(a.get('rationale'))[:120]}"]
        return False, [
            f"Δscore {margin:.1f} in arbiter band but no backing "
            f"(approve={a.get('approve')}, conf={conf})"]
    return False, [f"Δscore {margin:.1f} < hard floor {hard:.0f}"]


# ── LLM arbiter (Mistral-pinned tactical layer) ────────────────────────

def arbiter_view(bot, tracker, now, cfg):
    """Compact incumbent view for the arbiter prompt."""
    obs = bot.get("observed") or {}
    policy = bot.get("stagnation_policy") or {}
    tr = tracker if isinstance(tracker, dict) else {}
    idle_min = (now - float(tr.get("last_increase_at") or now)) / 60.0
    return {
        "slot": bot.get("slot"), "symbol": bot.get("symbol"),
        "venue": bot.get("venue"), "regime":
            (bot.get("ticket") or {}).get("regime") or policy.get("regime"),
        "score": bot.get("score_final"),
        "idle_min": round(idle_min, 1),
        "expected_fills_24h": policy.get("expected_fills_per_24h"),
        "fills_24h": obs.get("fills_24h"),
        "realized_ratio": obs.get("realized_ratio"),
        "unrealized_pnl": obs.get("unrealized_pnl"),
        "needs_reanalysis": bool(bot.get("needs_reanalysis")),
    }


def normalize_pick(name, chals):
    """Match the arbiter's challenger name to a candidate.

    Arbiters sometimes decorate the symbol ("SOL_hyperliquid",
    "hyperliquid:SOL"). Exact match first, then ignore venue decorations.
    Returns the matched candidate or None."""
    if not name:
        return None
    for c in chals:
        if c.get("symbol") == name:
            return c
    bare = str(name).split("_")[0].split(":")[-1].strip().upper()
    for c in chals:
        if str(c.get("symbol", "")).upper() == bare:
            return c
    return None


def challenger_view(c):
    """Compact challenger view for the arbiter prompt."""
    return {
        "symbol": c.get("symbol"), "venue": c.get("venue"),
        "regime": c.get("regime"), "score_final": c.get("score_final"),
        "step_pct": c.get("step"),
        "spread_pct": c.get("spread_pct"),
        "expected_fills_per_24h": c.get("expected_fills_per_24h"),
        "harvest_net_pct_24h": c.get("harvest_net_pct_24h"),
        "structure": c.get("structure_notes") or c.get("confluence_notes"),
        "tvcli_fit": c.get("tvcli_fit"),
    }


def llm_arbiter(inc, chals, _chain=None, provider=None):
    """One arbiter call: (verdict dict, degraded). Fail-soft.

    verdict = {"approve": bool, "challenger": str, "rationale": str,
               "confidence": 0-1, "provider": str}. Degraded=True means the
    LLM was unreachable/unparseable and the rule fallback answered (approve
    = numeric margin ≥ upgrade_margin — the conservative reading).
    """
    fallback = {
        "approve": False, "challenger": None,
        "rationale": "rule-fallback: LLM unavailable — numeric margin only",
        "confidence": 0.0, "provider": "rule-fallback",
    }
    if not chals:
        return fallback, True
    best = chals[0]
    margin = (best.get("score_final") or 0) - (inc.get("score") or 0)
    fb = dict(fallback)
    fb["approve"] = margin >= 0 and inc.get("needs_reanalysis", False)
    fb["challenger"] = best.get("symbol")
    fb["rationale"] = (f"rule-fallback: Δscore {margin:.1f}, "
                       + ("stopped incumbent" if inc.get("needs_reanalysis")
                          else "LLM unavailable"))
    if not HAS_LLM:
        return fb, True
    prompt = (
        f"Slot {inc.get('slot')} runs a grid on {inc.get('venue')}:"
        f"{inc.get('symbol')} and has been idle {inc.get('idle_min')}m "
        f"(expected {inc.get('expected_fills_24h')} fills/24h, realized "
        f"{inc.get('realized_ratio')}). "
        f"Challengers ranked by refreshed score: "
        f"{json.dumps([challenger_view(c) for c in chals[:3]])}. "
        f"Should we rotate the slot to the best challenger NOW (grid "
        f"capital is idle; the swap costs one stop+recreate and a "
        f"{min_swap_interval_default()}m cooldown)? Schema: "
        f'{{"approve":bool,"challenger":str,'
        f'"rationale":str,"confidence":0-1}}.')
    try:
        chain = _chain
        if chain is None and provider:
            chain = named_chain(provider) or None
        name, obj = chat_json(
            [{"role": "system", "content": ARB_SYS},
             {"role": "user", "content": prompt}], _chain=chain)
        verdict = {
            "approve": bool(obj.get("approve")),
            "challenger": obj.get("challenger"),
            "rationale": str(obj.get("rationale", ""))[:200],
            "confidence": max(0.0, min(1.0, float(obj.get("confidence")
                                                   or 0))),
            "provider": name,
        }
        return verdict, False
    except Exception:
        return fb, True


def min_swap_interval_default():
    return OPTIMIZER_DEFAULTS["min_swap_interval_min"]


# ── fast hunter (light data refresh, DI for tests) ─────────────────────

class FastHunter:
    """Refreshes the challenger board between rescreens.

    Data sources are injected callables so unit tests run without network:
      fetch_candles(venue, symbol, interval, limit, market) -> [[o,h,l,c,…]]
      hunt(skill, tv_symbols, timeframe, bars) -> {tv_symbol: result|error}
    Defaults bind the real market_regime / merge helpers at call time.
    """

    def __init__(self, fetch_candles_fn=None, hunt_fn=None,
                 interval="1h", limit=180, presets=None):
        self._fetch_candles_fn = fetch_candles_fn
        self._hunt_fn = hunt_fn
        self.interval = interval
        self.limit = limit
        self._presets = presets

    def _fetch(self, venue, symbol, market):
        fn = self._fetch_candles_fn
        if fn is None:
            if not HAS_MARKET:
                raise RuntimeError("market_regime unavailable")
            fn = fetch_candles
        return fn(venue, fetch_symbol(venue, symbol), self.interval,
                  self.limit, market)

    def _preset_for(self, name):
        if self._presets is not None:
            return self._presets.get(name)
        if not HAS_PRESETS:
            return None
        return (load_presets(None) or {}).get(name)

    def refresh_one(self, cand):
        """Re-score one cached candidate on live candles (fail-soft).

        Returns an updated COPY: fresh metrics/regime/score/step + the
        grid-fill EV fields, or the original with refresh_error set.
        """
        out = dict(cand)
        try:
            venue = cand["venue"]
            market = "futures" if venue == "hyperliquid" else "spot"
            cl = self._fetch(venue, cand["symbol"], market)
            if not cl or len(cl) < 60:
                raise RuntimeError(f"short candle history ({len(cl or [])})")
            m = compute_metrics(cl)
            regime, ev = classify(m)
            preset = self._preset_for(cand.get("preset") or "grid-neutral")
            sc = preset_score(regime, m, preset, cand.get("spread_pct")) \
                if preset is not None else (cand.get("score") or 0)
            out.update({
                "metrics": m, "regime": regime, "evidence": ev,
                "score": round(sc, 2) if sc is not None else cand.get("score"),
                "step": derived_step(m, preset) if preset is not None
                else cand.get("step"),
                "refreshed_at": time.time(),
            })
            if HAS_STAG:
                step = out.get("step") or 0.5
                closes = [row[3] for row in cl]
                fills, _ = simulate_grid_fills(closes, step)
                window_h = len(closes)  # 1h bars → hours == bars
                per24 = fills / window_h * 24 if window_h else 0.0
                rt = ROUND_TRIP_FEE_PCT.get(venue, 0.15)
                out["expected_fills_per_24h"] = round(per24, 2)
                out["harvest_gross_pct_24h"] = round(per24 * step, 3)
                out["harvest_net_pct_24h"] = round(
                    per24 * max(step - rt, 0.0), 3)
                if out["harvest_net_pct_24h"] < 0.1:
                    out["score"] = round((out["score"] or 0) - 10.0, 2)
                else:
                    out["score"] = round(
                        (out["score"] or 0)
                        + min(out["harvest_net_pct_24h"], 3.0), 2)
            out.pop("refresh_error", None)
        except Exception as exc:
            out["refresh_error"] = str(exc)[:120]
            # keep the cached score as score_final — stale beats blind
        out.setdefault("score_final", out.get("score") or 0)
        if "refresh_error" not in out:
            out["score_final"] = out.get("score") or 0
        return out

    def apply_structure(self, cands, skills, timeframe, bars):
        """tvcli /hunt enrichment (structure → fitness bonus → re-rank).

        Adds `structure_notes` + folds the tvcli fitness bonus into
        score_final (same pure tvcli_fitness the hourly screen uses), so
        the fast lane sees the same structure language as the slow lane.
        Fail-soft: a failed hunt leaves scores untouched.
        """
        if not cands:
            return cands, {}
        syms = list({c.get("tv_symbol") for c in cands if c.get("tv_symbol")})
        if not syms:
            return cands, {}
        hunts = {}
        for skill in skills or []:
            try:
                fn = self._hunt_fn or (tv_hunt if HAS_MERGE else None)
                if fn is None:
                    raise RuntimeError("tvcli hunt unavailable")
                hunts[skill] = fn(skill, syms, timeframe, bars)
            except Exception as exc:
                hunts[skill] = {"_error": str(exc)[:120]}
        if not hunts:
            return cands, hunts
        for c in cands:
            tv = c.get("tv_symbol")
            per_skill = {s: (h.get(tv) or {}) for s, h in hunts.items()}
            # refresh the per-skill boolean truth the screen recorded, so a
            # fast-lane deploy's evidence block (confluence.ok) reflects the
            # LIVE hunt, not only the last hourly screen
            if isinstance(c.get("confluence"), dict):
                for s, h in per_skill.items():
                    c["confluence"][s] = bool(h.get("result") is not None)
            if HAS_MERGE:
                bonus, notes, _fit = tvcli_fitness(
                    c,
                    sq=per_skill.get("squeeze"),
                    ch=per_skill.get("choppiness"),
                    mtf=per_skill.get("mtf-confluence"),
                    dvi=per_skill.get("dvi"))
                base = c.get("score") or c.get("score_final") or 0
                c["score_final"] = round(
                    base + (bonus or 0), 2)
                if notes:
                    c["structure_notes"] = notes
        cands.sort(key=lambda c: c.get("score_final") or 0, reverse=True)
        return cands, hunts


# ── the optimizer itself ───────────────────────────────────────────────

class SlotOptimizer:
    """Fast-loop engine wired into the daemon between rescreens.

    The daemon instance is INJECTED (no daemon import → no cycle): the
    optimizer reads/writes daemon.state, calls daemon.execute_rotation /
    daemon.queue_rescreen, and journals through the injected journal_fn
    (daemon.log shape: fn(state, event_dict)).
    """

    def __init__(self, daemon, journal_fn=None, hunter=None):
        self.daemon = daemon
        self.journal_fn = journal_fn
        self._hunter = hunter

    # ── small helpers ──────────────────────────────────────────────────
    def cfg(self):
        return merge_cfg(self.daemon.config)

    def _journal(self, event):
        event = dict(event)
        event.setdefault("at", datetime.now(timezone.utc)
                         .isoformat(timespec="seconds"))
        if self.journal_fn is not None:
            try:
                self.journal_fn(self.daemon.state, event)
                return
            except Exception:
                pass
        try:
            self.daemon.state.setdefault("journal", []).append(event)
            self.daemon.state["journal"] = self.daemon.state["journal"][-200:]
        except Exception:
            pass

    def _opt_state(self):
        st = self.daemon.state.setdefault("optimizer", {})
        st.setdefault("trackers", {})
        st.setdefault("swap_log", [])
        st.setdefault("cycles", 0)
        st.setdefault("swaps_total", 0)
        return st

    def status(self):
        """Snapshot for GET /optimizer and the console."""
        st = self.daemon.state.get("optimizer") or {}
        return {
            "enabled": bool(self.cfg().get("enabled", True)),
            "interval_min": self.cfg().get("interval_min"),
            "cycles": st.get("cycles", 0),
            "swaps_total": st.get("swaps_total", 0),
            "last_at": st.get("last_at"),
            "trackers": st.get("trackers", {}),
            "last_report": st.get("last_report"),
        }

    # ── the cycle ──────────────────────────────────────────────────────
    def run_cycle(self, dry_run=True, hunter=None):
        """One optimize pass. Returns the cycle report; never raises."""
        cfg = self.cfg()
        report = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cycle_kind": "optimizer", "dry_run": dry_run,
            "screen": {"n_candidates": 0}, "idle": [], "hunt": {},
            "arbiter": None, "swaps": [], "vetoes": [], "refill": None,
            "capital": {}, "caveats": [],
        }
        if not cfg.get("enabled", True):
            report["skipped"] = "disabled"
            return report
        try:
            self._cycle(report, cfg, dry_run, hunter)
        except Exception as exc:
            report["caveats"].append(f"cycle error: {str(exc)[:160]}")
            self._journal({"kind": "optimizer-error",
                           "msg": str(exc)[:200]})
        st = self._opt_state()
        st["cycles"] = int(st.get("cycles", 0)) + 1
        st["last_at"] = report["at"]
        st["last_report"] = report
        # run-card throttle: the fast loop cycles every 2–5 min — an
        # unconditioned card per cycle floods state/reports with ~300–700
        # "nothing happened" files a day and buries the rescreen cards.
        # Write a card only when the cycle DID something (idle detected,
        # arbiter consulted, swap attempted/vetoed, refill nudged, or an
        # error caveat), plus one periodic heartbeat card every N quiet
        # cycles so the Run-cards view still proves the loop is alive.
        interesting = bool(report.get("idle") or report.get("swaps")
                           or report.get("vetoes") or report.get("arbiter")
                           or report.get("refill")
                           or any("error" in str(c) for c in report.get("caveats") or []))
        every_n = int(self.cfg().get("run_card_every_n_cycles", 40))
        st["cycles_since_card"] = int(st.get("cycles_since_card", 0)) + 1
        if interesting or st["cycles_since_card"] >= max(1, every_n):
            st["cycles_since_card"] = 0
            try:
                self.daemon.write_run_card_safe(report)
            except Exception:
                pass
        try:
            self.daemon.save_state(self.daemon.state)
        except Exception:
            pass
        return report

    def _cycle(self, report, cfg, dry_run, hunter):
        state = self.daemon.state
        now = time.time()
        cycle_started = now
        active = state.get("active_bots") or {}
        ost = self._opt_state()

        # 1. TRACK — fold fresh fills_24h into per-slot trackers
        for slot_key, bot in active.items():
            fills = (bot.get("observed") or {}).get("fills_24h")
            ost["trackers"][slot_key] = update_tracker(
                ost["trackers"].get(slot_key), fills, now)
            # tracker lives on the bot view too (arbiter + console)
            bot["optimizer"] = {
                "last_increase_at":
                    ost["trackers"][slot_key].get("last_increase_at"),
                "last_fills": ost["trackers"][slot_key].get("last_fills"),
            }

        # 2. IDLE — token-relative inactivity per slot
        idle = {}
        for slot_key, bot in active.items():
            flag, reasons = is_idle(bot, ost["trackers"].get(slot_key),
                                    now, cfg)
            if flag:
                idle[slot_key] = (bot, reasons)
                report["idle"].append({
                    "slot": slot_key, "symbol": bot.get("symbol"),
                    "venue": bot.get("venue"), "reasons": reasons})
        if idle:
            self._journal({"kind": "optimizer-idle",
                           "msg": "; ".join(
                               f"slot {k} ({b.get('symbol')}): "
                               f"{'; '.join(r)[:80]}"
                               for k, (b, r) in idle.items())[:300]})

        # 3. HUNT — refresh the challenger board from the rescreen cache
        cache = state.get("screen_cache") or {}
        cands = list(cache.get("candidates") or [])
        cache_age_min = None
        if cache.get("at"):
            try:
                cache_age_min = (now - float(cache["at"])) / 60
            except (TypeError, ValueError):
                cache_age_min = None
        stale = cache_age_min is None or cache_age_min > float(
            cfg.get("screen_cache_fresh_min", 120))
        if not cands or (cands and stale):
            age_txt = (f"{cache_age_min:.0f}m old"
                       if cache_age_min is not None else "no timestamp")
            report["caveats"].append(
                f"screen cache empty/stale ({age_txt}) — challengers "
                f"unavailable, waiting for rescreen")
            self._maybe_nudge_rescreen(cfg, now, reason="stale-cache")
        h = hunter or self._hunter or FastHunter(
            interval="1h", limit=int(cfg.get("refresh_limit", 180)))
        refreshed = [h.refresh_one(dict(c))
                     for c in cands[:int(cfg.get("hunt_top", 8))]]
        report["screen"]["n_candidates"] = len(refreshed)

        # incumbents get the same fresh eyes (their cached score can be an
        # hour old; the swap margin must compare like with like)
        inc_fresh = {}
        for slot_key, bot in active.items():
            cached = next((c for c in cands
                           if c.get("venue") == bot.get("venue")
                           and c.get("symbol") == bot.get("symbol")), None)
            view = dict(cached) if cached else {
                "venue": bot.get("venue"), "symbol": bot.get("symbol"),
                "tv_symbol": f"BINANCE:{bot.get('symbol')}USDT",
                "preset": "grid-neutral"
                if (bot.get("ticket") or {}).get("grid_type", "neutral")
                == "neutral" else "grid-directional",
                "score_final": bot.get("score_final") or 0,
            }
            fresh = h.refresh_one(view)
            inc_fresh[slot_key] = fresh

        # structure pass over incumbents + challengers (tvcli 15m)
        if refreshed or inc_fresh:
            pool = refreshed + list(inc_fresh.values())
            enriched, hunts = h.apply_structure(
                pool, cfg.get("hunt_skills"),
                cfg.get("hunt_timeframe", "15m"),
                int(cfg.get("hunt_bars", 96)))
            # split back (apply_structure sorts in place by score_final)
            by_key = {f"{c.get('venue')}:{c.get('symbol')}": c
                      for c in enriched}
            refreshed = [by_key.get(
                f"{c.get('venue')}:{c.get('symbol')}", c) for c in refreshed]
            refreshed.sort(key=lambda c: c.get("score_final") or 0,
                           reverse=True)
            for slot_key, fresh in list(inc_fresh.items()):
                inc_fresh[slot_key] = by_key.get(
                    f"{fresh.get('venue')}:{fresh.get('symbol')}", fresh)
            ok_hunts = {s: sum(1 for r in (h or {}).values()
                               if isinstance(r, dict)
                               and r.get("result") is not None)
                        for s, h in hunts.items()}
            # hunt observability: which skills errored (the {"_error":
            # ...} fail-soft markers) and per-skill hunted/ok counts —
            # a down tvcli used to show up only as zeros in "tvcli"
            hunt_errors = [f"{s}: {h.get('_error')}"
                           for s, h in (hunts or {}).items()
                           if isinstance(h, dict) and h.get("_error")]
            hunt_skills = {}
            for s, h in (hunts or {}).items():
                if not isinstance(h, dict):
                    continue
                entries = [r for r in h.values() if isinstance(r, dict)]
                hunt_skills[s] = {
                    "hunted": len(entries),
                    "ok": sum(1 for r in entries
                              if r.get("result") is not None)}
            report["hunt"] = {
                "refreshed": len(refreshed),
                "tvcli": ok_hunts,
                "errors": hunt_errors,
                "skills": hunt_skills,
                "top3": [{"venue": c.get("venue"), "symbol": c.get("symbol"),
                          "regime": c.get("regime"),
                          "score_final": c.get("score_final"),
                          "harvest_net_pct_24h":
                              c.get("harvest_net_pct_24h")}
                         for c in refreshed[:3]],
            }

        # 4+5. ARBITER + SWAP per idle slot
        active_keys = {f"{b.get('venue')}:{b.get('symbol')}"
                       for b in active.values()}
        # per-slot rate limiting is evaluated against the PRE-cycle log: a
        # challenger whose rotation is vetoed by the machinery (sizing,
        # reliability, …) must not block the next-best challenger in the
        # SAME cycle — that is adaptation, not churn
        pre_cycle_log = [e for e in ost.get("swap_log", [])
                         if float(e.get("at", 0)) < cycle_started]
        max_attempts = max(1, int(cfg.get("max_attempts_per_slot", 2)))
        for slot_key, (bot, idle_reasons) in idle.items():
            venue = bot.get("venue")
            chals = eligible_challengers(refreshed, venue, active_keys,
                                         state.get("cooldowns_until") or {},
                                         now)
            if not chals:
                report["vetoes"].append({
                    "slot": slot_key, "reason":
                        f"idle ({'; '.join(idle_reasons)[:80]}) but no "
                        f"eligible {venue} challenger"})
                continue
            # incumbent view uses the FRESH score when we have one
            inc_view = dict(bot)
            inc_fresh_score = (inc_fresh.get(slot_key) or {}).get(
                "score_final")
            if inc_fresh_score is not None:
                inc_view["score_final"] = inc_fresh_score
            # band pre-filter: below arbiter_margin the gate can NEVER
            # approve (the arbiter relaxes the bar, it cannot remove it) —
            # don't spend a Mistral call when no swap is numerically
            # possible, and don't let sub-band challengers consume the
            # per-slot attempt budget
            inc_score = inc_view.get("score_final") or 0
            band = float(cfg.get("arbiter_margin", 5.0))
            in_band = [c for c in chals
                       if (c.get("score_final") or 0) - inc_score >= band]
            if not in_band:
                top = chals[0]
                report["vetoes"].append({
                    "slot": slot_key,
                    "reason": f"best challenger {top.get('symbol')} Δscore "
                              f"{(top.get('score_final') or 0) - inc_score:.1f}"
                              f" < arbiter band {band:.0f} — no swap "
                              f"numerically possible (arbiter skipped)"})
                continue
            arbiter, degraded = llm_arbiter(
                arbiter_view({**inc_view, "slot": slot_key},
                             ost["trackers"].get(slot_key), now, cfg),
                in_band, provider=cfg.get("llm_provider"))
            if degraded:
                report["caveats"].append("arbiter llm_degraded")
            report["arbiter"] = {**arbiter, "slot": slot_key,
                                 "llm_degraded": degraded}
            # try order: the arbiter's pick first (when approved), then the
            # numeric ranking — bounded attempts per cycle
            ordered = list(in_band)
            pick_cand = normalize_pick(arbiter.get("challenger"), chals)
            pick = pick_cand.get("symbol") if pick_cand else None
            if pick_cand is not None:
                ordered = [pick_cand] + [c for c in chals if c is not pick_cand]
            swapped = False
            for best in ordered[:max_attempts]:
                # arbiter backing applies only to its named challenger
                arb_for_gate = arbiter if (arbiter.get("approve")
                                           and best.get("symbol") == pick) \
                    else None
                approve, reasons = swap_gate(
                    inc_view, best, arb_for_gate, cfg, ost.get("swap_log"),
                    now, slot_key, slot_rate_log=pre_cycle_log)
                if not approve:
                    report["vetoes"].append({"slot": slot_key,
                                             "reason": "; ".join(reasons)})
                    continue
                self._execute_swap(slot_key, bot, best,
                                   idle_reasons + reasons, dry_run, report,
                                   ost, now,
                                   inc_score_fresh=inc_view.get(
                                       "score_final"))
                swapped = report["swaps"] and \
                    report["swaps"][-1].get("slot") == slot_key
                if swapped:
                    break
            if swapped:
                active_keys = {
                    f"{b.get('venue')}:{b.get('symbol')}"
                    for b in (state.get("active_bots") or {}).values()}

        # 6. CAPITAL — free slots + deployable challenger → rescreen nudge
        self._capital(report, cfg, refreshed, now, dry_run)

    # ── swap execution ─────────────────────────────────────────────────
    def _execute_swap(self, slot_key, bot, challenger, reasons, dry_run,
                      report, ost, now, inc_score_fresh=None):
        state = self.daemon.state
        # record the ATTEMPT now: failed attempts count toward the global
        # attempts/hour cap (the spam guard), while the per-slot interval
        # counts successes only and the failing challenger cools down below
        ost["swap_log"] = ([e for e in ost.get("swap_log", [])
                            if now - float(e.get("at", 0)) < 7200]
                           + [{"slot": str(slot_key), "at": now,
                               "ok": False}])
        bot["force_rotate"] = True
        bot["optimizer_swap"] = {
            "reasons": reasons, "challenger":
                f"{challenger.get('venue')}:{challenger.get('symbol')}",
            "at": report["at"],
            # the guard's rotation hysteresis compares against THIS, not the
            # hour-old stored score — the swap was decided fresh-vs-fresh
            "inc_score_fresh": inc_score_fresh}
        ok = False
        try:
            ok = bool(self.daemon.execute_rotation(
                slot_key, challenger, dry_run))
        except Exception as exc:
            self._journal({"kind": "optimizer-error",
                           "slot": slot_key,
                           "msg": f"swap failed: {str(exc)[:160]}"})
        if ok:
            ost["swap_log"][-1]["ok"] = True
            ost["swaps_total"] = int(ost.get("swaps_total", 0)) + 1
            report["swaps"].append({
                "slot": slot_key,
                "from": f"{bot.get('venue')}:{bot.get('symbol')}",
                "to": f"{challenger.get('venue')}:"
                      f"{challenger.get('symbol')}",
                "reasons": reasons})
            self._journal({
                "kind": "optimizer-swap", "slot": slot_key, "dry_run":
                    dry_run,
                "msg": f"{bot.get('venue')}:{bot.get('symbol')} → "
                       f"{challenger.get('venue')}:"
                       f"{challenger.get('symbol')} "
                       f"({'; '.join(reasons)[:140]})"})
        else:
            # cool down THIS challenger (not the slot): the attempt failed
            # inside the rotation machinery (sizing/reliability/capacity
            # veto or transport error) — retrying the same token every 3
            # min would burn an arbiter call + rotation attempt each cycle
            # (CASHCAT at $10 min-notional never fits a $50 slot), while
            # the slot itself stays free to try the next-best challenger.
            # state["cooldowns_until"] is honored by the rescreen deploy
            # path too, so an undeployable token stops failing there as well.
            cool_min = float(self.cfg().get("fail_cooldown_min", 60))
            ckey = f"{challenger.get('venue')}:{challenger.get('symbol')}"
            self.daemon.state.setdefault("cooldowns_until", {})[ckey] = \
                now + cool_min * 60
            report["vetoes"].append({
                "slot": slot_key,
                "reason": f"swap attempt failed (rotation machinery vetoed "
                          f"or errored) — {ckey} cooled down {cool_min:.0f}m"})
            self._journal({
                "kind": "optimizer-cooldown", "slot": slot_key,
                "msg": f"{ckey} cooled down {cool_min:.0f}m — swap attempt "
                       f"failed (vetoed or errored)"})
            # clear the manual-rotate marks so the hourly rescreen does not
            # re-trigger this as a "manual" rotation an hour later
            bot.pop("force_rotate", None)
            bot.pop("optimizer_swap", None)

    # ── capital utilization ────────────────────────────────────────────
    def _maybe_nudge_rescreen(self, cfg, now, reason=""):
        state = self.daemon.state
        last = (state.get("optimizer") or {}).get("last_refill_nudge", 0)
        if now - float(last) < float(cfg.get("refill_nudge_min", 10)) * 60:
            return False
        state.setdefault("optimizer", {})["last_refill_nudge"] = now
        try:
            self.daemon.queue_rescreen()
        except Exception:
            return False
        self._journal({"kind": "optimizer-refill",
                       "msg": f"rescreen nudged ({reason}) — deployable "
                              f"capital / stale cache needs the full screen"})
        return True

    def _capital(self, report, cfg, refreshed, now, dry_run):
        state = self.daemon.state
        slots = state.get("slots") or []
        used = {str(s) for s in (state.get("active_bots") or {})}
        free = [s for s in slots if str(s.get("slot")) not in used]
        committed = sum(float(v or 0)
                        for v in (state.get("committed") or {}).values())
        idle_slots = {i.get("slot") for i in report.get("idle", [])}
        idle_committed = sum(
            float(s.get("max_commitment") or 0) for s in slots
            if str(s.get("slot")) in {str(k) for k in idle_slots})
        try:
            ceiling = self.daemon.plan_slots().get("deployable_ceiling")
        except Exception:
            ceiling = None
        report["capital"] = {
            "free_slots": [s.get("slot") for s in free],
            "committed_usd": round(committed, 2),
            "deployable_ceiling_usd": ceiling,
            "idle_committed_usd": round(idle_committed, 2),
        }
        if free and refreshed:
            floor = float((self.daemon.config.get("screen") or {})
                          .get("open_slot_min_score", 40.0))
            best = refreshed[0]
            if (best.get("score_final") or 0) >= floor:
                nudged = self._maybe_nudge_rescreen(
                    cfg, now,
                    reason=f"free slots {[s.get('slot') for s in free]}, "
                           f"{best.get('symbol')} at "
                           f"{best.get('score_final')} ≥ floor {floor}")
                report["refill"] = {
                    "nudged": nudged,
                    "best": f"{best.get('venue')}:{best.get('symbol')}",
                    "score_final": best.get("score_final"), "floor": floor,
                }


# ── CLI ────────────────────────────────────────────────────────────────

def _live_daemon_optimize(port):
    """POST /optimize on the live daemon's ctl plane. Returns (ok, body)."""
    import urllib.request
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/optimize", data=b"{}",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return True, json.loads(r.read().decode())
    except Exception as exc:
        return False, {"error": str(exc)[:160]}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true",
                    help="run one cycle (queued on the live daemon's ctl "
                         "plane when one is running; otherwise a local "
                         "one-shot over the state file)")
    ap.add_argument("--policy", action="store_true",
                    help="print the merged optimizer config and exit")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.policy:
        print(json.dumps(merge_cfg({}), indent=2))
        return 0
    if not args.once:
        ap.print_help()
        return 2

    # a live daemon owns state.json — queue the cycle on its ctl plane
    # instead of racing it with a second writer
    try:
        sys.path.insert(0, HERE)
        import daemon as daemon_mod
        port = int((daemon_mod.load_config().get("server") or {})
                   .get("daemon_port", 8799))
        if not daemon_mod._pidguard_ok():
            ok, body = _live_daemon_optimize(port)
            print(json.dumps({"queued_on_live_daemon": ok, **body}, indent=2))
            return 0 if ok else 1
        d = daemon_mod.Daemon()
        opt = SlotOptimizer(
            d, journal_fn=daemon_mod.log,
            hunter=FastHunter(limit=int(
                merge_cfg(d.config).get("refresh_limit", 180))))
        rep = opt.run_cycle(dry_run=args.dry_run)
    except Exception as exc:
        rep = {"cycle_kind": "optimizer", "error": str(exc)[:200]}
    print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
