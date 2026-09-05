"""Unit tests for optimizer.py — the fast (2–5 min) slot-reallocation loop.

Pure decision functions get exact fixtures; the full cycle runs against a
FakeDaemon + StubHunter so no network, no WT browser, and no LLM provider
is ever touched.
"""
import math
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "llm"))
sys.path.insert(0, os.path.join(HERE, "screen"))
sys.path.insert(0, os.path.join(HERE, "policy"))

import optimizer  # noqa: E402
from optimizer import (  # noqa: E402
    FastHunter, SlotOptimizer, OPTIMIZER_DEFAULTS, eligible_challengers,
    idle_threshold_min, is_idle, llm_arbiter, swap_gate, update_tracker,
)

NOW = time.time()  # all fixture timestamps are relative to real now

CFG = dict(OPTIMIZER_DEFAULTS)


def iso_min_ago(minutes):
    return (datetime.now(timezone.utc)
            - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def sine_rows(n=180, base=100.0, amp=2.0, periods=10):
    """1h candle rows (open, high, low, close) that oscillate — the exact
    4-tuple shape market_regime.fetch_candles returns (close at index 3)."""
    rows = []
    for i in range(n):
        c = base + amp * math.sin(2 * math.pi * i * periods / n)
        rows.append((c, c * 1.005, c * 0.995, c))
    return rows


# ── tracker math ───────────────────────────────────────────────────────

class TestTracker(unittest.TestCase):
    def test_first_observation_starts_clock(self):
        tr = update_tracker(None, 0, NOW)
        self.assertEqual(tr, {"last_fills": 0, "last_increase_at": NOW})

    def test_increase_moves_timestamp(self):
        tr = update_tracker(None, 5, NOW)
        tr = update_tracker(tr, 7, NOW + 600)
        self.assertEqual(tr["last_fills"], 7)
        self.assertEqual(tr["last_increase_at"], NOW + 600)

    def test_no_increase_keeps_timestamp(self):
        tr = update_tracker(None, 7, NOW)
        tr = update_tracker(tr, 7, NOW + 600)
        tr = update_tracker(tr, 6, NOW + 900)  # 24h window slid — still no bump
        self.assertEqual(tr["last_increase_at"], NOW)
        self.assertEqual(tr["last_fills"], 6)

    def test_none_fills_is_noop(self):
        tr = {"last_fills": 3, "last_increase_at": NOW}
        self.assertEqual(update_tracker(tr, None, NOW + 60), tr)


# ── idle detection ─────────────────────────────────────────────────────

class TestIdleThreshold(unittest.TestCase):
    def test_floor_for_unknown_rate(self):
        self.assertEqual(idle_threshold_min(CFG, None), 5.0)
        self.assertEqual(idle_threshold_min(CFG, 0), 5.0)

    def test_fast_token_uses_floor(self):
        # 600 fills/day → 2.4-min interval → floor 5 min wins
        self.assertEqual(idle_threshold_min(CFG, 600), 5.0)

    def test_slow_token_scales_up(self):
        # 20 fills/day → 72-min interval → the token sets its own bar
        self.assertAlmostEqual(idle_threshold_min(CFG, 20), 72.0)

    def test_k_multiplier(self):
        cfg = dict(CFG, idle_k=2.0)
        self.assertAlmostEqual(idle_threshold_min(cfg, 20), 144.0)
        self.assertEqual(idle_threshold_min(cfg, 600), 5.0)


def make_bot(expected=300.0, fills=3, since_min=120, **extra):
    bot = {
        "symbol": "PUMP", "venue": "hyperliquid",
        "since": iso_min_ago(since_min),
        "score_final": 50.0,
        "stagnation_policy": {"regime": "chop_high_volatility",
                              "expected_fills_per_24h": expected},
        "observed": {"fills_24h": fills, "realized_ratio": 0.1,
                     "status": "active"},
        "ticket": {"grid_type": "neutral"},
    }
    bot.update(extra)
    return bot


class TestIsIdle(unittest.TestCase):
    def test_idle_fast_token(self):
        # 300/day → 4.8-min interval → threshold 5; quiet for 10 min → idle
        bot = make_bot(expected=300)
        tr = {"last_fills": 3, "last_increase_at": NOW - 600}
        idle, reasons = is_idle(bot, tr, NOW, CFG)
        self.assertTrue(idle)
        self.assertIn("no fills", reasons[0])

    def test_active_not_idle(self):
        bot = make_bot(expected=300)
        tr = {"last_fills": 3, "last_increase_at": NOW - 60}
        idle, _ = is_idle(bot, tr, NOW, CFG)
        self.assertFalse(idle)

    def test_slow_token_needs_longer(self):
        # 20/day → 72-min threshold; 10 quiet minutes is NOT idle
        bot = make_bot(expected=20)
        tr = {"last_fills": 1, "last_increase_at": NOW - 600}
        idle, _ = is_idle(bot, tr, NOW, CFG)
        self.assertFalse(idle)

    def test_needs_reanalysis_idle_immediately(self):
        bot = make_bot(expected=20, needs_reanalysis=True)
        tr = {"last_fills": 1, "last_increase_at": NOW}
        idle, reasons = is_idle(bot, tr, NOW, CFG)
        self.assertTrue(idle)
        self.assertIn("needs_reanalysis", reasons[0])

    def test_stopped_status_idle(self):
        bot = make_bot(observed={"fills_24h": 5, "status": "stopped"})
        idle, reasons = is_idle(bot, {}, NOW, CFG)
        self.assertTrue(idle)

    def test_observe_error_fail_closed(self):
        bot = make_bot(observed={"error": "grid status list unavailable"})
        idle, _ = is_idle(bot, {"last_fills": 0,
                                "last_increase_at": NOW - 99999}, NOW, CFG)
        self.assertFalse(idle)

    def test_fresh_bot_below_fast_min_hold(self):
        bot = make_bot(expected=300, since_min=5)
        tr = {"last_fills": 0, "last_increase_at": NOW - 600}
        idle, reasons = is_idle(bot, tr, NOW, CFG)
        self.assertFalse(idle)
        self.assertIn("min_hold", reasons[0])

    def test_no_history_not_idle(self):
        bot = make_bot()
        idle, reasons = is_idle(bot, None, NOW, CFG)
        self.assertFalse(idle)


# ── challenger eligibility ─────────────────────────────────────────────

class TestEligible(unittest.TestCase):
    CANDS = [
        {"venue": "hyperliquid", "symbol": "SOL", "score_final": 90},
        {"venue": "hyperliquid", "symbol": "HYPE", "score_final": 80},
        {"venue": "binance", "symbol": "WIF", "score_final": 95},
        {"venue": "hyperliquid", "symbol": "RUNNING", "score_final": 99},
    ]

    def test_filters_and_sorts(self):
        out = eligible_challengers(
            self.CANDS, "hyperliquid",
            {"hyperliquid:RUNNING"}, {}, NOW)
        self.assertEqual([c["symbol"] for c in out], ["SOL", "HYPE"])

    def test_cooldown_excluded(self):
        out = eligible_challengers(
            self.CANDS, "hyperliquid", set(),
            {"hyperliquid:SOL": NOW + 3600}, NOW)
        self.assertEqual([c["symbol"] for c in out], ["RUNNING", "HYPE"])

    def test_expired_cooldown_allowed(self):
        out = eligible_challengers(
            self.CANDS, "hyperliquid", set(),
            {"hyperliquid:SOL": NOW - 1}, NOW)
        self.assertEqual(out[0]["symbol"], "RUNNING")


# ── swap gate ──────────────────────────────────────────────────────────

class TestSwapGate(unittest.TestCase):
    INC = {"score_final": 50.0}

    def chal(self, score):
        return {"venue": "hyperliquid", "symbol": "SOL",
                "score_final": score}

    def test_numeric_margin_approves(self):
        ok, reasons = swap_gate(self.INC, self.chal(60), None, CFG, [],
                                NOW, "1")
        self.assertTrue(ok)

    def test_below_hard_floor_refuses(self):
        ok, reasons = swap_gate(self.INC, self.chal(54), None, CFG, [],
                                NOW, "1")
        self.assertFalse(ok)
        self.assertIn("hard floor", reasons[0])

    def test_arbiter_band_without_backing_refuses(self):
        ok, _ = swap_gate(self.INC, self.chal(56),
                          {"approve": False, "confidence": 0.9},
                          CFG, [], NOW, "1")
        self.assertFalse(ok)

    def test_arbiter_band_with_backing_approves(self):
        arb = {"approve": True, "confidence": 0.8, "rationale": "structure"}
        ok, reasons = swap_gate(self.INC, self.chal(56), arb, CFG, [],
                                NOW, "1")
        self.assertTrue(ok)
        self.assertIn("arbiter approved", reasons[0])

    def test_arbiter_cannot_approve_below_floor(self):
        # Δscore 2 < arbiter_margin 5 — even a confident arbiter refuses
        arb = {"approve": True, "confidence": 0.99}
        ok, _ = swap_gate(self.INC, self.chal(52), arb, CFG, [], NOW, "1")
        self.assertFalse(ok)

    def test_same_cycle_attempt_not_rate_limited_via_pre_cycle_log(self):
        # a swap logged THIS cycle must not block the next challenger
        # when the caller passes the pre-cycle log for per-slot limiting
        this_cycle = [{"slot": "1", "at": NOW - 30, "ok": True}]
        pre_cycle = []
        ok, _ = swap_gate(self.INC, self.chal(90), None, CFG,
                          this_cycle, NOW, "1", slot_rate_log=pre_cycle)
        self.assertTrue(ok)
        # default (no override): the same-cycle success blocks, as before
        ok, reasons = swap_gate(self.INC, self.chal(90), None, CFG,
                                this_cycle, NOW, "1")
        self.assertFalse(ok)
        self.assertIn("rate limit", reasons[0])

    def test_low_confidence_arbiter_refuses(self):
        arb = {"approve": True, "confidence": 0.5}
        ok, _ = swap_gate(self.INC, self.chal(56), arb, CFG, [], NOW, "1")
        self.assertFalse(ok)

    def test_per_slot_rate_limit(self):
        log = [{"slot": "1", "at": NOW - 600, "ok": True}]  # swapped 10 min ago
        ok, reasons = swap_gate(self.INC, self.chal(90), None, CFG, log,
                                NOW, "1")
        self.assertFalse(ok)
        self.assertIn("rate limit", reasons[0])

    def test_failed_attempt_does_not_rate_limit_slot(self):
        # a vetoed attempt 10 min ago rotated NOTHING — the slot stays free
        # for the next-best challenger (the failing challenger itself is
        # cooled down by the caller, not the slot)
        log = [{"slot": "1", "at": NOW - 600, "ok": False}]
        ok, _ = swap_gate(self.INC, self.chal(90), None, CFG, log,
                          NOW, "1")
        self.assertTrue(ok)

    def test_other_slot_swap_does_not_block(self):
        log = [{"slot": "2", "at": NOW - 600}]
        ok, _ = swap_gate(self.INC, self.chal(90), None, CFG, log, NOW, "1")
        self.assertTrue(ok)

    def test_global_hourly_cap_counts_successes_only(self):
        # 3 failed attempts + 3 ok swaps in the last hour: the swap cap
        # (max 3 successes/hour) refuses the next attempt, while a fleet
        # with ONLY failures burns attempts, not the swap cap
        log = [{"slot": "s9", "at": NOW - 300, "ok": False},
               {"slot": "s8", "at": NOW - 400, "ok": False},
               {"slot": "s7", "at": NOW - 500, "ok": False},
               {"slot": "s6", "at": NOW - 600, "ok": True},
               {"slot": "s5", "at": NOW - 700, "ok": True},
               {"slot": "s4", "at": NOW - 800, "ok": True}]
        ok, reasons = swap_gate(self.INC, self.chal(90), None, CFG, log,
                                NOW, "1")
        self.assertFalse(ok)
        self.assertIn("global rate limit", reasons[0])

    def test_attempt_hourly_cap(self):
        # failures alone hit max_attempts_per_hour (6) and stop the loop
        log = [{"slot": f"s{i}", "at": NOW - 300, "ok": False}
               for i in range(6)]
        ok, reasons = swap_gate(self.INC, self.chal(90), None, CFG, log,
                                NOW, "1")
        self.assertFalse(ok)
        self.assertIn("attempt rate limit", reasons[0])

    def test_failed_attempts_do_not_exhaust_swap_cap(self):
        # 3 failures, 0 successes → swap cap untouched → approve
        log = [{"slot": f"s{i}", "at": NOW - 300, "ok": False}
               for i in range(3)]
        ok, _ = swap_gate(self.INC, self.chal(90), None, CFG, log, NOW, "1")
        self.assertTrue(ok)


# ── LLM arbiter ────────────────────────────────────────────────────────

class TestArbiter(unittest.TestCase):
    INC = {"slot": "1", "symbol": "PUMP", "venue": "hyperliquid",
           "score": 50.0, "idle_min": 12.0,
           "expected_fills_24h": 300, "realized_ratio": 0.1,
           "needs_reanalysis": False}
    CHALS = [{"venue": "hyperliquid", "symbol": "SOL",
              "score_final": 58.0, "regime": "chop_high_volatility",
              "step": 1.0, "harvest_net_pct_24h": 0.9}]

    def test_no_challengers_degrades(self):
        v, degraded = llm_arbiter(self.INC, [])
        self.assertTrue(degraded)
        self.assertFalse(v["approve"])

    def test_llm_failure_rule_fallback(self):
        def boom(*a, **k):
            raise RuntimeError("all providers failed")
        with mock.patch.object(optimizer, "chat_json", boom), \
                mock.patch.object(optimizer, "HAS_LLM", True):
            v, degraded = llm_arbiter(self.INC, self.CHALS)
        self.assertTrue(degraded)
        self.assertEqual(v["provider"], "rule-fallback")

    def test_llm_verdict_parsed(self):
        reply = {"approve": True, "challenger": "SOL",
                 "rationale": "coiled squeeze beats dead tape",
                 "confidence": 0.8}
        chain = [("stub", lambda msgs, mt: "unused")]
        with mock.patch.object(optimizer, "chat_json",
                               return_value=("stub", reply)), \
                mock.patch.object(optimizer, "HAS_LLM", True):
            v, degraded = llm_arbiter(self.INC, self.CHALS, _chain=chain)
        self.assertFalse(degraded)
        self.assertTrue(v["approve"])
        self.assertEqual(v["challenger"], "SOL")
        self.assertEqual(v["provider"], "stub")

    def test_confidence_clamped(self):
        reply = {"approve": True, "confidence": 42}
        chain = [("stub", lambda msgs, mt: "x")]
        with mock.patch.object(optimizer, "chat_json",
                               return_value=("stub", reply)), \
                mock.patch.object(optimizer, "HAS_LLM", True):
            v, _ = llm_arbiter(self.INC, self.CHALS, _chain=chain)
        self.assertEqual(v["confidence"], 1.0)

    def test_provider_pin_passes_chain(self):
        seen = {}

        def fake_chat_json(msgs, max_tokens=4096, _chain=None, **k):
            seen["chain"] = _chain
            return "mistral", {"approve": False, "confidence": 0.1}

        pinned = [("mistral", lambda m, t: "x")]
        with mock.patch.object(optimizer, "chat_json", fake_chat_json), \
                mock.patch.object(optimizer, "HAS_LLM", True):
            llm_arbiter(self.INC, self.CHALS, _chain=pinned)
        self.assertEqual(seen["chain"], pinned)


# ── arbiter pick normalization ─────────────────────────────────────────

class TestNormalizePick(unittest.TestCase):
    CHALS = [{"venue": "hyperliquid", "symbol": "CASHCAT"},
             {"venue": "hyperliquid", "symbol": "ROBO"}]

    def test_exact(self):
        self.assertEqual(optimizer.normalize_pick("ROBO", self.CHALS)
                         ["symbol"], "ROBO")

    def test_venue_decorations(self):
        # live arbiter behavior 2026-09-05: "CASHCAT_hyperliquid"
        self.assertEqual(
            optimizer.normalize_pick("CASHCAT_hyperliquid",
                                     self.CHALS)["symbol"], "CASHCAT")
        self.assertEqual(
            optimizer.normalize_pick("hyperliquid:CASHCAT",
                                     self.CHALS)["symbol"], "CASHCAT")

    def test_no_match(self):
        self.assertIsNone(optimizer.normalize_pick("NOPE", self.CHALS))
        self.assertIsNone(optimizer.normalize_pick(None, self.CHALS))


# ── fast hunter ────────────────────────────────────────────────────────

PRESET = {"regime_weights": {"chop_high_volatility": 50, "neutral": 40,
                             "trend_up": 30},
          "step_atr_factor": 0.5, "step_min": 0.1, "step_max": 3.0}


class StubFetcher:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __call__(self, venue, symbol, interval, limit, market="spot"):
        self.calls.append((venue, symbol, interval, limit, market))
        return self.rows


class TestFastHunter(unittest.TestCase):
    def test_refresh_one_rescores(self):
        h = FastHunter(fetch_candles_fn=StubFetcher(sine_rows()),
                       presets={"grid-neutral": PRESET},
                       interval="1h", limit=180)
        cand = {"venue": "hyperliquid", "symbol": "SOL",
                "tv_symbol": "BINANCE:SOLUSDT", "preset": "grid-neutral",
                "score": 30.0, "score_final": 30.0, "spread_pct": 0.02,
                "step": 0.5}
        out = h.refresh_one(cand)
        self.assertNotIn("refresh_error", out)
        self.assertIn("metrics", out)
        self.assertIn("regime", out)
        # EV fields added by the fill simulation
        self.assertIn("expected_fills_per_24h", out)
        self.assertIn("harvest_net_pct_24h", out)
        # oscillating tape → the EV bonus lifts the heuristic score
        self.assertGreaterEqual(out["score"], cand["score"])
        self.assertGreaterEqual(out["score_final"], cand["score"])

    def test_refresh_one_fetch_failure_failsoft(self):
        def boom(*a, **k):
            raise RuntimeError("venue down")

        h = FastHunter(fetch_candles_fn=boom, presets={"p": PRESET})
        cand = {"venue": "hyperliquid", "symbol": "SOL", "preset": "p",
                "score": 30.0, "score_final": 30.0}
        out = h.refresh_one(cand)
        self.assertIn("refresh_error", out)
        self.assertEqual(out["score_final"], 30.0)  # cached score stands

    def test_short_history_failsoft(self):
        h = FastHunter(fetch_candles_fn=StubFetcher(sine_rows(30)),
                       presets={"p": PRESET})
        cand = {"venue": "binance", "symbol": "WIF", "preset": "p",
                "score": 30.0}
        out = h.refresh_one(cand)
        self.assertIn("refresh_error", out)

    def test_binance_fetch_uses_full_pair(self):
        fetcher = StubFetcher(sine_rows())
        h = FastHunter(fetch_candles_fn=fetcher, presets={"p": PRESET})
        h.refresh_one({"venue": "binance", "symbol": "WIF", "preset": "p",
                       "score": 30.0})
        self.assertEqual(fetcher.calls[0][1], "WIFUSDT")

    def test_apply_structure_reranks(self):
        def hunt(skill, syms, tf, bars):
            # SOL: coiled squeeze on the fast tape; DOGE: nothing
            sol = {"result": {"structure": {
                "squeezeOn": True, "squeezeBars": 8, "momentum": 0}}}
            return {"BINANCE:SOLUSDT": sol}

        h = FastHunter(hunt_fn=hunt, presets={"p": PRESET})
        cands = [
            {"venue": "hyperliquid", "symbol": "DOGE",
             "tv_symbol": "BINANCE:DOGEUSDT", "regime": "neutral",
             "score": 55.0, "score_final": 55.0},
            {"venue": "hyperliquid", "symbol": "SOL",
             "tv_symbol": "BINANCE:SOLUSDT",
             "regime": "chop_high_volatility", "score": 54.0,
             "score_final": 54.0},
        ]
        out, hunts = h.apply_structure(cands, ["squeeze"], "15m", 96)
        self.assertEqual(out[0]["symbol"], "SOL")  # squeeze bonus re-ranks
        self.assertIn("structure_notes", out[0])
        self.assertNotIn("structure_notes", out[1])
        self.assertIn("squeeze", hunts)

    def test_apply_structure_hunt_failure_failsoft(self):
        def boom(*a, **k):
            raise RuntimeError("tvcli down")

        h = FastHunter(hunt_fn=boom, presets={"p": PRESET})
        cands = [{"venue": "hyperliquid", "symbol": "SOL",
                  "tv_symbol": "BINANCE:SOLUSDT", "regime": "neutral",
                  "score": 60.0, "score_final": 60.0}]
        out, hunts = h.apply_structure(cands, ["squeeze"], "15m", 96)
        self.assertEqual(out[0]["score_final"], 60.0)  # untouched
        self.assertIn("_error", hunts["squeeze"])


# ── full cycle on a fake daemon ────────────────────────────────────────

class StubHunter:
    """Duck-typed FastHunter: refresh adds a fixed bonus, structure adds
    SOL a squeeze-coil note (mirrors the real re-rank)."""

    def __init__(self, bonus=0.0, sol_bonus=0.0):
        self.bonus = bonus
        self.sol_bonus = sol_bonus

    def refresh_one(self, cand):
        out = dict(cand)
        out["score_final"] = (out.get("score_final")
                              or out.get("score") or 0) + self.bonus
        return out

    def apply_structure(self, cands, skills, tf, bars):
        for c in cands:
            if c.get("symbol") == "SOL":
                c["score_final"] = (c.get("score_final") or 0) + self.sol_bonus
                c["structure_notes"] = ["squeeze-coiled"]
        cands.sort(key=lambda c: c.get("score_final") or 0, reverse=True)
        # real contract: {skill: {tv_symbol: result|error}}
        hunts = {s: {c.get("tv_symbol"): {"result": {"structure": {}}}
                     for c in cands if c.get("tv_symbol")}
                 for s in (skills or [])}
        return cands, hunts


class FakeDaemon:
    def __init__(self, state, config=None):
        self.config = config or {"optimizer": {},
                                 "screen": {"open_slot_min_score": 40.0},
                                 "portfolio": {"total_usd": 500.0}}
        self.state = state
        self.optimizer = object()  # truthy — the fast loop is wired
        self.swaps = []
        self.rescreens = 0
        self.cards = []

    def execute_rotation(self, slot_key, challenger, dry_run=True):
        self.swaps.append((str(slot_key),
                           f"{challenger.get('venue')}:"
                           f"{challenger.get('symbol')}", dry_run))
        old = self.state["active_bots"].pop(str(slot_key), None)
        self.state["active_bots"][str(slot_key)] = {
            "symbol": challenger["symbol"], "venue": challenger["venue"],
            "since": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "observed": {"fills_24h": 0, "status": "active"},
            "stagnation_policy": {"expected_fills_per_24h": 300},
            "score_final": challenger.get("score_final"),
            "ticket": {"grid_type": "neutral"},
            "_replaced": (old or {}).get("symbol"),
        }
        return True

    def queue_rescreen(self):
        self.rescreens += 1

    def plan_slots(self):
        return {"deployable_ceiling": 425.0}

    def save_state(self, st):
        pass

    def write_run_card_safe(self, rep):
        self.cards.append(rep)


def cycle_state():
    """One idle HL slot (PUMP) + a fresh screen cache with a better SOL."""
    return {
        "active_bots": {
            "1": make_bot(expected=300, fills=3),
            "2": {**make_bot(expected=1.0, fills=9, since_min=200),
                  "symbol": "DOGE", "score_final": 70.0},
        },
        "slots": [{"slot": 1, "venue": "hyperliquid", "balance": 150.0,
                   "max_commitment": 75.0},
                  {"slot": 2, "venue": "hyperliquid", "balance": 150.0,
                   "max_commitment": 75.0}],
        "committed": {"1": 60.0, "2": 70.0},
        "cooldowns_until": {},
        "optimizer": {"trackers": {
            # PUMP quiet for 10 min (threshold 5 → idle)
            "1": {"last_fills": 3, "last_increase_at": time.time() - 600},
            # DOGE filled 1 min ago (threshold 5 → active)
            "2": {"last_fills": 9, "last_increase_at": time.time() - 60},
        }},
        "screen_cache": {"at": time.time() - 60, "candidates": [
            {"venue": "hyperliquid", "symbol": "SOL",
             "tv_symbol": "BINANCE:SOLUSDT",
             "regime": "chop_high_volatility", "preset": "grid-neutral",
             "score": 80.0, "score_final": 80.0, "spread_pct": 0.02,
             "step": 1.0, "metrics": {"price": 100.0, "atr_pct": 2.0},
             "evidence": {}},
        ]},
    }


class TestCycle(unittest.TestCase):
    def setUp(self):
        # no cycle test may reach a live LLM provider, whatever keys the
        # ambient env carries — degrade the arbiter to its rule fallback
        p = mock.patch.object(optimizer, "HAS_LLM", False)
        p.start()
        self.addCleanup(p.stop)

    def run_cycle(self, state, hunter=None, **cfg_over):
        d = FakeDaemon(state, config={
            "optimizer": cfg_over, "screen": {"open_slot_min_score": 40.0}})
        opt = SlotOptimizer(d, journal_fn=lambda st, ev: None,
                            hunter=hunter or StubHunter())
        rep = opt.run_cycle(dry_run=False)
        return d, rep

    def test_idle_slot_swapped_to_better_token(self):
        st = cycle_state()
        d, rep = self.run_cycle(st)
        self.assertEqual(len(d.swaps), 1)
        slot, to, dry = d.swaps[0]
        self.assertEqual(slot, "1")
        self.assertEqual(to, "hyperliquid:SOL")
        self.assertFalse(dry)
        # the new incumbent is the challenger; the healthy slot is untouched
        self.assertEqual(st["active_bots"]["1"]["symbol"], "SOL")
        self.assertEqual(st["active_bots"]["2"]["symbol"], "DOGE")
        self.assertEqual(len(rep["swaps"]), 1)
        self.assertEqual(rep["swaps"][0]["from"], "hyperliquid:PUMP")
        self.assertEqual(rep["swaps"][0]["to"], "hyperliquid:SOL")
        # tracker + counters persisted
        self.assertEqual(st["optimizer"]["swaps_total"], 1)
        self.assertEqual(st["optimizer"]["cycles"], 1)
        self.assertIn("1", st["optimizer"]["trackers"])

    def test_healthy_fleet_no_swaps(self):
        st = cycle_state()
        # both bots filled a minute ago
        t = time.time() - 60
        st["optimizer"]["trackers"]["1"]["last_increase_at"] = t
        d, rep = self.run_cycle(st)
        self.assertEqual(d.swaps, [])
        self.assertEqual(rep["swaps"], [])

    def test_weak_challenger_vetoed(self):
        st = cycle_state()
        st["screen_cache"]["candidates"][0]["score_final"] = 52.0
        st["screen_cache"]["candidates"][0]["score"] = 52.0
        d, rep = self.run_cycle(st)
        self.assertEqual(d.swaps, [])
        self.assertTrue(any("floor" in v["reason"] or "band" in v["reason"]
                            for v in rep["vetoes"]))

    def test_failed_rotation_cools_challenger_and_clears_marks(self):
        st = cycle_state()
        # a second challenger so both same-cycle attempts are exercised
        st["screen_cache"]["candidates"].append(
            {"venue": "hyperliquid", "symbol": "HYPE",
             "tv_symbol": "BINANCE:HYPEUSDT", "regime": "neutral",
             "preset": "grid-neutral", "score": 75.0, "score_final": 75.0,
             "spread_pct": 0.02, "step": 1.0, "metrics": {},
             "evidence": {}})

        class FailDaemon(FakeDaemon):
            def execute_rotation(self, slot_key, challenger, dry_run=True):
                self.swaps.append((str(slot_key), "FAIL",
                                   challenger["symbol"]))
                return False  # rotation machinery vetoed the challenger

        d = FailDaemon(st)
        opt = SlotOptimizer(d, journal_fn=lambda st_, ev: None,
                            hunter=StubHunter())
        rep = opt.run_cycle(dry_run=False)
        # both challengers were attempted this cycle (max_attempts 2)…
        self.assertEqual(len(d.swaps), 2)
        self.assertEqual(rep["swaps"], [])     # …but none counted as swaps
        # force_rotate cleared so the hourly rescreen won't re-trigger it
        self.assertNotIn("force_rotate", st["active_bots"]["1"])
        self.assertNotIn("optimizer_swap", st["active_bots"]["1"])
        # both failed challengers are cooled down (not the slot)
        self.assertGreater(st["cooldowns_until"]["hyperliquid:SOL"],
                           time.time())
        self.assertGreater(st["cooldowns_until"]["hyperliquid:HYPE"],
                           time.time())
        # a second immediate cycle: cooled challengers are ineligible, so
        # nothing is retried and no arbiter call is spent
        rep2 = opt.run_cycle(dry_run=False)
        self.assertEqual(len(d.swaps), 2)
        self.assertTrue(any("no eligible" in v["reason"]
                            for v in rep2["vetoes"]))
        # …but the SLOT is not rate-limited: a fresh challenger is tried
        st["screen_cache"]["candidates"].append(
            {"venue": "hyperliquid", "symbol": "WIF",
             "tv_symbol": "BINANCE:WIFUSDT", "regime": "neutral",
             "preset": "grid-neutral", "score": 78.0, "score_final": 78.0,
             "spread_pct": 0.02, "step": 1.0, "metrics": {},
             "evidence": {}})
        rep3 = opt.run_cycle(dry_run=False)
        self.assertEqual(len(d.swaps), 3)
        self.assertEqual(d.swaps[-1][-1], "WIF")

    def test_machinery_veto_falls_through_to_next_challenger(self):
        st = cycle_state()
        # two challengers: SOL (top, will fail in the machinery) + HYPE
        st["screen_cache"]["candidates"].append(
            {"venue": "hyperliquid", "symbol": "HYPE",
             "tv_symbol": "BINANCE:HYPEUSDT", "regime": "neutral",
             "preset": "grid-neutral", "score": 75.0, "score_final": 75.0,
             "spread_pct": 0.02, "step": 1.0, "metrics": {},
             "evidence": {}})
        fails = {"SOL"}

        class PickyDaemon(FakeDaemon):
            def execute_rotation(self, slot_key, challenger, dry_run=True):
                if challenger.get("symbol") in fails:
                    self.swaps.append((str(slot_key), "VETOED",
                                       challenger["symbol"]))
                    return False
                return FakeDaemon.execute_rotation(self, slot_key,
                                                   challenger, dry_run)

        d = PickyDaemon(st)
        opt = SlotOptimizer(d, journal_fn=lambda st_, ev: None,
                            hunter=StubHunter())
        rep = opt.run_cycle(dry_run=False)
        # SOL vetoed by the machinery → HYPE deployed in the SAME cycle
        self.assertEqual(d.swaps[0], ("1", "VETOED", "SOL"))
        self.assertEqual(st["active_bots"]["1"]["symbol"], "HYPE")
        self.assertEqual(len(rep["swaps"]), 1)
        self.assertEqual(rep["swaps"][0]["to"], "hyperliquid:HYPE")

    def test_no_challengers_vetoed_with_reason(self):
        st = cycle_state()
        st["screen_cache"]["candidates"] = []
        d, rep = self.run_cycle(st)
        self.assertEqual(d.swaps, [])
        self.assertTrue(any("no eligible" in v["reason"]
                            for v in rep["vetoes"]))
        # stale/empty cache nudges a rescreen
        self.assertGreaterEqual(d.rescreens, 1)

    def test_swap_marker_carries_fresh_incumbent_score(self):
        # the daemon's rotation guard compares challenger vs the incumbent's
        # STORED score (potentially an hour old); the optimizer decided on
        # fresh-vs-fresh scores, so the marker must carry the fresh one —
        # otherwise a stale-high stored score falsely vetoes the swap
        st = cycle_state()
        seen = {}

        class WatchDaemon(FakeDaemon):
            def execute_rotation(self, slot_key, challenger, dry_run=True):
                seen["marker"] = dict(
                    (self.state["active_bots"][str(slot_key)]
                     .get("optimizer_swap") or {}))
                return FakeDaemon.execute_rotation(self, slot_key,
                                                   challenger, dry_run)

        d = WatchDaemon(st)
        opt = SlotOptimizer(d, journal_fn=lambda st_, ev: None,
                            hunter=StubHunter())
        rep = opt.run_cycle(dry_run=False)
        self.assertEqual(len(rep["swaps"]), 1)
        self.assertEqual(seen["marker"].get("inc_score_fresh"), 50.0)
        self.assertTrue(seen["marker"].get("challenger", "")
                        .endswith(":SOL"))

    def test_arbiter_skipped_when_no_challenger_in_band(self):
        # incumbent 50, challenger 52 → Δscore 2 < arbiter band 5: the gate
        # can never approve, so the Mistral call must not even happen
        st = cycle_state()
        st["screen_cache"]["candidates"][0]["score_final"] = 52.0
        st["screen_cache"]["candidates"][0]["score"] = 52.0
        calls = []

        def spy(inc, chals, _chain=None, provider=None):
            calls.append(1)
            return {"approve": True, "confidence": 0.99}, False

        with mock.patch.object(optimizer, "llm_arbiter", spy):
            d, rep = self.run_cycle(st)
        self.assertEqual(calls, [])
        self.assertEqual(d.swaps, [])
        self.assertTrue(any("arbiter band" in v["reason"]
                            and "skipped" in v["reason"]
                            for v in rep["vetoes"]))

    def test_stale_cache_flagged(self):
        st = cycle_state()
        st["screen_cache"]["at"] = time.time() - 3 * 3600
        d, rep = self.run_cycle(st)
        self.assertTrue(any("stale" in c or "empty" in c
                            for c in rep["caveats"]))

    def test_arbiter_backed_swap_in_relaxed_band(self):
        st = cycle_state()
        # Δscore 6: below upgrade_margin (8) but inside the arbiter band (5)
        st["screen_cache"]["candidates"][0]["score_final"] = 56.0
        st["screen_cache"]["candidates"][0]["score"] = 56.0

        def arb(inc, chals, _chain=None, provider=None):
            return {"approve": True, "challenger": "SOL",
                    "rationale": "coiled squeeze beats dead tape",
                    "confidence": 0.85, "provider": "stub"}, False

        d = FakeDaemon(st, config={"optimizer": {},
                                   "screen": {"open_slot_min_score": 40.0}})
        opt = SlotOptimizer(d, journal_fn=lambda st_, ev: None,
                            hunter=StubHunter())
        with mock.patch.object(optimizer, "llm_arbiter", arb):
            rep = opt.run_cycle(dry_run=False)
        self.assertEqual(len(d.swaps), 1)
        self.assertTrue(any("arbiter approved" in r
                            for r in rep["swaps"][0]["reasons"]))
        self.assertEqual(rep["arbiter"]["provider"], "stub")

    def test_disabled_optimizer_noop(self):
        st = cycle_state()
        d, rep = self.run_cycle(st, enabled=False)
        self.assertEqual(d.swaps, [])
        self.assertEqual(rep.get("skipped"), "disabled")

    def test_cycle_never_raises(self):
        st = cycle_state()
        st["active_bots"]["1"]["observed"] = {"fills_24h": "not-a-number"}
        d = FakeDaemon(st)
        opt = SlotOptimizer(d, journal_fn=lambda st_, ev: None,
                            hunter=StubHunter())
        rep = opt.run_cycle(dry_run=True)
        self.assertEqual(rep["cycle_kind"], "optimizer")

    def test_capital_report_and_refill_nudge(self):
        st = cycle_state()
        st["active_bots"] = {"1": make_bot(expected=300)}  # slot 2 free
        t = time.time() - 60
        st["optimizer"]["trackers"] = {"1": {"last_fills": 3,
                                             "last_increase_at": t}}
        d, rep = self.run_cycle(st)
        self.assertIn("capital", rep)
        self.assertEqual(rep["capital"]["free_slots"], [2])
        # SOL (80) ≥ open_slot floor 40 with a free slot → rescreen nudged
        self.assertGreaterEqual(d.rescreens, 1)
        self.assertTrue(rep["refill"]["nudged"])

    def test_refill_nudge_rate_limited(self):
        st = cycle_state()
        st["active_bots"] = {"1": make_bot(expected=300)}
        t = time.time() - 60
        st["optimizer"]["trackers"] = {"1": {"last_fills": 3,
                                             "last_increase_at": t}}
        # nudge happened 2 min ago (< refill_nudge_min 10)
        st["optimizer"]["last_refill_nudge"] = time.time() - 120
        d, rep = self.run_cycle(st)
        self.assertEqual(d.rescreens, 0)
        self.assertFalse(rep["refill"]["nudged"])


class TestStatus(unittest.TestCase):
    def test_status_snapshot(self):
        st = cycle_state()
        d = FakeDaemon(st)
        opt = SlotOptimizer(d, journal_fn=lambda st_, ev: None,
                            hunter=StubHunter())
        opt.run_cycle(dry_run=True)
        s = opt.status()
        self.assertTrue(s["enabled"])
        self.assertEqual(s["cycles"], 1)
        self.assertIn("trackers", s)
        self.assertIn("last_report", s)


# ── daemon wiring (hermetic, mirrors the ManageHarness pattern) ────────

try:
    from test_daemon_manage import ManageHarness  # noqa: E402
except ImportError:
    from tests.test_daemon_manage import ManageHarness  # noqa: E402


def _cand(venue, symbol, score, preset="grid-neutral", step=1.0):
    return {"venue": venue, "symbol": symbol,
            "tv_symbol": f"BINANCE:{symbol}USDT",
            "regime": "chop_high_volatility", "preset": preset,
            "metrics": {"price": 1.0, "atr_pct": 2.0},
            "evidence": {}, "score": score, "score_final": score,
            "spread_pct": 0.02, "step": step,
            "archetype": "Neutral Grid (mean-reversion)",
            "vol_usd": 9_000_000, "flags": [], "confluence_notes": [],
            "confluence_bonus": 0.0,
            "expected_fills_per_24h": 40.0, "harvest_net_pct_24h": 0.3}


class TestDaemonWiring(ManageHarness):
    """rescreen persists screen_cache; the daemon schedules + ctl-exposes
    the optimizer. Subclasses the heretic ManageHarness (no network/WT) so
    its patch cleanups actually run."""

    def make_daemon(self):
        return ManageHarness.make_daemon(self)

    def test_rescreen_persists_screen_cache(self):
        import daemon as daemon_mod
        d = self.make_daemon()
        cands = [_cand("hyperliquid", "SOL", 90),
                 _cand("hyperliquid", "HYPE", 70),
                 _cand("binance", "WIF", 60)]
        with mock.patch.object(daemon_mod, "run_merge",
                               return_value={"results": cands}):
            d.rescreen_cycle(dry_run=True, max_new=0)
        cache = d.state.get("screen_cache") or {}
        self.assertTrue(cache.get("at"))
        syms = [c["symbol"] for c in cache["candidates"]]
        self.assertEqual(syms, ["SOL", "HYPE", "WIF"])
        # whitelisted fields only — no preset-internal bloat in state.json
        import json as _json
        dumped = _json.dumps(cache)
        self.assertLess(len(dumped), 4000)
        for f in ("venue", "symbol", "tv_symbol", "regime", "metrics",
                  "score_final", "step", "preset"):
            self.assertIn(f, cache["candidates"][0])

    def test_daemon_has_optimizer_engine(self):
        d = self.make_daemon()
        self.assertIsNotNone(d.optimizer)
        self.assertTrue(d.capabilities.get("optimizer"))
        s = d.optimizer_status()
        self.assertTrue(s.get("enabled"))
        # cadence clamped to the 2–5 min design band
        d.config["optimizer"] = {"interval_min": 1, "enabled": True}
        self.assertEqual(d.optimizer_interval_s(), 2 * 60)
        d.config["optimizer"] = {"interval_min": 9}
        self.assertEqual(d.optimizer_interval_s(), 5 * 60)
        d.config["optimizer"] = {"enabled": False}
        self.assertIsNone(d.optimizer_interval_s())

    def test_queue_consume_optimize(self):
        d = self.make_daemon()
        self.assertFalse(d.consume_optimize())
        d.queue_optimize()
        self.assertTrue(d.consume_optimize())
        self.assertFalse(d.consume_optimize())

    def test_optimizer_cycle_through_daemon(self):
        """The daemon-owned engine end-to-end on a hermetic state: idle
        slot + better challenger → execute_rotation is invoked."""
        import daemon as daemon_mod
        d = self.make_daemon()
        now = time.time()
        d.state["active_bots"]["1"] = {
            "symbol": "PUMP", "venue": "hyperliquid",
            "bot_code": "OLDBOT", "score_final": 50.0,
            "since": iso_min_ago(120),
            "stagnation_policy": {
                "regime": "neutral",
                "expected_fills_per_24h": 300,
                "stagnant_if": {"min_fills_24h": 1.0,
                                "min_realized_ratio": 0.4},
                "hysteresis_score": 5.0, "cooldown_h": 12.0},
            "observed": {"fills_24h": 3, "realized_ratio": 0.1,
                         "status": "active"},
            "decision_id": "OLD1", "ticket": {"grid_type": "neutral"},
        }
        d.state.setdefault("optimizer", {}).setdefault("trackers", {})["1"] = {
            "last_fills": 3, "last_increase_at": now - 600}
        d.state["screen_cache"] = {"at": now - 60,
                                   "candidates": [_cand(
                                       "hyperliquid", "SOL", 90)]}
        self.grid_status_ret = [{"code": "OLDBOT", "status": "stopped"}]
        with mock.patch.object(optimizer, "HAS_LLM", False):
            rep = d.optimizer.run_cycle(dry_run=False)
        # execute_rotation → plan_candidate → build → stop/verify → delete
        # → create: the challenger bot was actually created through the
        # stubbed grid_adapter
        self.assertTrue(any(op[0] == "create" for op in self.ops),
                        f"no create op in {self.ops}")
        self.assertEqual(len(rep["swaps"]), 1)
        self.assertEqual(rep["swaps"][0]["to"], "hyperliquid:SOL")
        self.assertEqual(d.state["active_bots"]["1"]["symbol"], "SOL")


# ── config plumbing ────────────────────────────────────────────────────

class TestConfig(unittest.TestCase):
    def test_defaults_merge(self):
        cfg = optimizer.merge_cfg({"optimizer": {"interval_min": 5}})
        self.assertEqual(cfg["interval_min"], 5)
        self.assertEqual(cfg["idle_minutes"],
                         OPTIMIZER_DEFAULTS["idle_minutes"])

    def test_daemon_interval_clamp(self):
        import daemon as daemon_mod
        for want, expect in ((1, 2.0), (3, 3.0), (9, 5.0), (None, 3.0)):
            cfg = {"optimizer": {}} if want is None else \
                {"optimizer": {"interval_min": want}}
            d = FakeDaemon({}, config=cfg)
            self.assertEqual(daemon_mod.Daemon.optimizer_interval_s(d),
                             expect * 60)

    def test_disabled_returns_none(self):
        import daemon as daemon_mod
        d = FakeDaemon({}, config={"optimizer": {"enabled": False}})
        self.assertIsNone(daemon_mod.Daemon.optimizer_interval_s(d))


if __name__ == "__main__":
    unittest.main()
