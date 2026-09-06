"""Unit tests for position_optimizer.py — the per-bot grid revaluation
+ exit-profile engine. Pure functions get exact fixtures; the engine runs
against injected fakes (canned candles, fake journal, fake persistence)
so no network, no WT, no PB, no daemon import ever happens.
"""
import math
import os
import sys
import time
import unittest

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, HERE)

import position_optimizer as po  # noqa: E402
from position_optimizer import (  # noqa: E402
    POSITION_OPTIMIZER_DEFAULTS, PositionOptimizer, evaluate_exits,
    expected_delta_for_channel, expected_profit_delta, make_recommendation,
    revalue_grid,
)

NOW = time.time()

CFG = dict(POSITION_OPTIMIZER_DEFAULTS)


def sine_rows(n=180, base=100.0, amp=2.0, periods=10):
    """1h candle rows (open, high, low, close) that oscillate — the exact
    4-tuple shape market_regime.fetch_candles returns (close at index 3)."""
    rows = []
    for i in range(n):
        c = base + amp * math.sin(2 * math.pi * i * periods / n)
        rows.append((c, c * 1.005, c * 0.995, c))
    return rows


def flat_bot(**over):
    """A fresh, healthy deployed bot fixture (all keys per the daemon's
    per-bot state contract)."""
    bot = {
        "symbol": "HYPE",
        "venue": "hyperliquid",
        "bot_code": "wt-42",
        "channel": {"low": 97.0, "mid": 100.0, "high": 103.0,
                     "step_pct": 0.5, "grids": 12},
        "upsert": {"pairCode": "HYPEUSD", "gridPercentStep": 0.005,
                   "gridLevels": 12, "amountPerTrade": 5.0},
        "ticket": {"grid_type": "neutral", "regime": "neutral"},
        "take_profit_usd": 10.0,
        "slot_balance": 100.0,
        "observed": {"status": "active", "price": 100.0, "fills_24h": 40,
                     "realized_ratio": 0.5, "realized_pnl": 1.0,
                     "unrealized_pnl": 0.0, "ladder_full": False,
                     "dd_vs_atr_band": 0.2, "open_lines": 4,
                     "open_losing": 1},
        "stagnation_policy": {"expected_fills_per_24h": 40.0},
    }
    bot.update(over)
    return bot


def fresh_revalue(**over):
    """A revalue dict with zero drift and matching channel width."""
    rv = revalue_grid(100.0, 1.0, 0.5, 12, 5.0, band_atr=3.0,
                      deployed_mid=100.0, deployed_step_pct=0.5,
                      deployed_grids=12, deployed_low=97.0, deployed_high=103.0)
    rv.update(over)
    return rv


class FakeJournal:
    def __init__(self):
        self.events = []

    def __call__(self, event):
        self.events.append(event)


class FakePersist:
    def __init__(self):
        self.recs = []

    def __call__(self, rec):
        self.recs.append(rec)
        return "rec-%d" % len(self.recs)


def make_optimizer(**over):
    """Engine with canned candles + fakes wired (fetch never hits network)."""
    rows = over.pop("rows", sine_rows())
    cfg = dict(CFG)
    cfg.update(over.pop("cfg", {}))
    journal = over.pop("journal", FakeJournal())
    persist = over.pop("persist", FakePersist())
    opt = PositionOptimizer(
        cfg=cfg, journal_fn=journal, persist_fn=persist,
        fetch_candles_fn=lambda *a, **k: rows,
        now_fn=lambda: NOW)
    return opt, journal, persist


# ── revalue_grid geometry ──────────────────────────────────────────────

class TestRevalueGrid(unittest.TestCase):
    def test_geometry_matches_known_line_count(self):
        # price 100, atr 0.72%, band 3 ATR → [97.84, 102.16]; step 0.5%
        # lines from 97.84 rising ×1.005 while >= 0.5% below high, plus high
        rv = revalue_grid(100.0, 0.72, 0.5, 12, 5.0, band_atr=3.0)
        self.assertEqual(rv["grids"], 9)
        self.assertAlmostEqual(rv["low"], 97.84, places=6)
        self.assertAlmostEqual(rv["high"], 102.16, places=6)
        self.assertAlmostEqual(rv["mid"], 100.0, places=6)
        # every geometric gap is >= one step; the final line is the high
        # itself, appended after the loop, so its gap can exceed one step
        # but stays under two (loop stops within one step of high)
        gaps = [(b / a - 1) * 100 for a, b in zip(rv["grid_lines"],
                                                  rv["grid_lines"][1:])]
        for g in gaps:
            self.assertGreaterEqual(g, 0.5 - 1e-6)
        self.assertLess(gaps[-1], 2 * 0.5)
        self.assertAlmostEqual(rv["grid_lines"][-1], 102.16, places=6)

    def test_wider_atr_gives_more_lines(self):
        narrow = revalue_grid(100.0, 0.72, 0.5, 12, 5.0)
        wide = revalue_grid(100.0, 2.0, 0.8, 12, 5.0)
        self.assertEqual(wide["grids"], 16)
        self.assertGreater(wide["channel_width_pct"],
                           narrow["channel_width_pct"])
        # width = (high/low - 1)*100 = 2b/(1-b) for band b
        self.assertAlmostEqual(narrow["channel_width_pct"],
                               (1.0216 / 0.9784 - 1) * 100, places=3)

    def test_deployed_deltas(self):
        rv = revalue_grid(101.0, 1.0, 0.6, 12, 5.0, band_atr=3.0,
                          deployed_mid=100.0, deployed_step_pct=0.5,
                          deployed_grids=9, deployed_low=97.0,
                          deployed_high=103.0)
        self.assertAlmostEqual(rv["delta_drift_pct"], 1.0, places=4)
        self.assertAlmostEqual(rv["delta_step_pct"], 0.1, places=6)
        self.assertEqual(rv["delta_grids"], rv["grids"] - 9)
        self.assertAlmostEqual(rv["deployed_width_pct"],
                               (103.0 - 97.0) / 97.0 * 100, places=4)

    def test_no_deployed_context_is_neutral(self):
        rv = revalue_grid(100.0, 1.0, 0.5, 12, 5.0)
        self.assertEqual(rv["delta_drift_pct"], 0.0)
        self.assertEqual(rv["delta_step_pct"], 0.0)
        self.assertEqual(rv["delta_grids"], 0)
        self.assertIsNone(rv["deployed_width_pct"])


# ── expected-profit math ───────────────────────────────────────────────

class TestExpectedDeltas(unittest.TestCase):
    def test_profit_delta_math(self):
        # (10 → 15) fills at $1/fill → +50% EV
        self.assertAlmostEqual(
            expected_profit_delta(10.0, 15.0, 1.0), 50.0, places=4)
        self.assertAlmostEqual(
            expected_profit_delta(10.0, 8.0, 2.0), -20.0, places=4)

    def test_profit_delta_div_zero_guarded(self):
        self.assertEqual(expected_profit_delta(0.0, 15.0, 1.0), 0.0)
        self.assertEqual(expected_profit_delta(10.0, 15.0, 0.0), 0.0)
        self.assertEqual(expected_profit_delta(None, 15.0, 1.0), 0.0)

    def test_channel_delta_inverse_width(self):
        # doubling the channel width halves the fill count → −50% EV
        self.assertAlmostEqual(
            expected_delta_for_channel(6.0, 12.0, 40.0, 0.025), -50.0,
            places=4)
        # narrowing 6% → 3% doubles fills → +100%
        self.assertAlmostEqual(
            expected_delta_for_channel(6.0, 3.0, 40.0, 0.025), 100.0,
            places=4)

    def test_channel_delta_guarded(self):
        self.assertEqual(
            expected_delta_for_channel(0.0, 6.0, 40.0, 0.025), 0.0)
        self.assertEqual(
            expected_delta_for_channel(6.0, 6.0, 0.0, 0.025), 0.0)


# ── evaluate_exits ─────────────────────────────────────────────────────

class TestEvaluateExits(unittest.TestCase):
    def test_no_stop_loss_when_disabled(self):
        bot = flat_bot()
        bot["observed"].update(realized_pnl=-20.0, unrealized_pnl=-10.0,
                               dd_vs_atr_band=2.5)
        out = evaluate_exits(bot, {"price": 100, "atr_pct": 1.0},
                            bot["observed"], CFG)
        self.assertIsNone(out["stop_loss_usd"])  # mean-reversion: forbidden
        self.assertNotIn("stop", " ".join(out["reasons"]).lower()
                         .replace("stop-loss:", ""))

    def test_stop_loss_when_enabled_is_wide_risk_cap(self):
        cfg = dict(CFG, stop_loss_enabled=True)
        bot = flat_bot()
        out = evaluate_exits(bot, {"price": 100, "atr_pct": 1.0},
                             bot["observed"], cfg)
        self.assertIsNotNone(out["stop_loss_usd"])
        # slot 100 → level is a >= 15% loss cap, expressed as negative USD
        self.assertLessEqual(out["stop_loss_usd"], -0.15 * 100.0 + 1e-9)
        self.assertTrue(any("risk cap" in r for r in out["reasons"]))

    def test_take_profit_at_60pct_of_target(self):
        bot = flat_bot()  # slot 100, tp_pct 0.10 → target $10
        obs = bot["observed"]
        # below the 60% ($6) trigger → nothing
        out = evaluate_exits(bot, {}, dict(obs, realized_pnl=5.99), CFG)
        self.assertIsNone(out["take_profit_usd"])
        # at/above → target set
        out = evaluate_exits(bot, {}, dict(obs, realized_pnl=6.0), CFG)
        self.assertEqual(out["take_profit_usd"], 10.0)

    def test_trailing_when_cumulative_pnl_over_activation(self):
        bot = flat_bot()
        obs = bot["observed"]
        # 5% of 100 = $5 cumulative, realized_ratio 0.5 >= 0.3
        out = evaluate_exits(bot, {}, dict(obs, realized_pnl=3.0,
                                           unrealized_pnl=2.0), CFG)
        self.assertEqual(out["trailing_activation_pct"], 5.0)
        self.assertEqual(out["trailing_execute_pct"], 2.0)
        # below activation → no trailing
        out = evaluate_exits(bot, {}, dict(obs, realized_pnl=2.0,
                                           unrealized_pnl=2.0), CFG)
        self.assertIsNone(out["trailing_activation_pct"])

    def test_positions_trailing_needs_healthy_fills_and_regime(self):
        bot = flat_bot()  # expected 40, neutral regime
        out = evaluate_exits(bot, {}, dict(bot["observed"],
                                           fills_24h=20), CFG)
        self.assertTrue(out["positions_trailing"])
        # unhealthy fills kill it
        out = evaluate_exits(bot, {}, dict(bot["observed"],
                                           fills_24h=10), CFG)
        self.assertFalse(out["positions_trailing"])
        # trend regime kills it even when healthy
        trend = flat_bot()
        trend["ticket"]["regime"] = "trend_up"
        out = evaluate_exits(trend, {}, dict(trend["observed"],
                                             fills_24h=30), CFG)
        self.assertFalse(out["positions_trailing"])


# ── make_recommendation ────────────────────────────────────────────────

class TestMakeRecommendation(unittest.TestCase):
    def test_keep_when_channel_aligned(self):
        bot = flat_bot()
        rec = make_recommendation(
            bot, fresh_revalue(), {"price": 100.0, "atr_pct": 1.0},
            bot["observed"],
            {"take_profit_usd": None, "stop_loss_usd": None,
             "trailing_activation_pct": None, "trailing_execute_pct": None,
             "positions_trailing": False, "reasons": []}, CFG)
        self.assertEqual(rec["recommendation"], "keep")
        self.assertEqual(rec["expected_delta_pct"], 0.0)
        self.assertEqual(rec["action"]["type"], "edit")
        self.assertFalse(rec["action"]["apply"])

    def test_recenter_on_drift(self):
        bot = flat_bot()
        # drift 3% vs threshold 2 × 0.5% = 1% → recenter
        rv = fresh_revalue(delta_drift_pct=3.0)
        rec = make_recommendation(
            bot, rv, {"price": 103.0, "atr_pct": 1.0}, bot["observed"],
            {"take_profit_usd": None, "stop_loss_usd": None,
             "trailing_activation_pct": None, "trailing_execute_pct": None,
             "positions_trailing": False, "reasons": []}, CFG)
        self.assertEqual(rec["recommendation"], "recenter")
        self.assertGreaterEqual(rec["expected_delta_pct"], 0.0)
        self.assertGreaterEqual(rec["confidence"], 0.5)

    def test_revalue_grid_on_deep_drawdown_vs_band(self):
        bot = flat_bot()
        obs = dict(bot["observed"], dd_vs_atr_band=1.7)
        rec = make_recommendation(
            bot, fresh_revalue(), {"price": 100.0, "atr_pct": 1.0}, obs,
            {"take_profit_usd": None, "stop_loss_usd": None,
             "trailing_activation_pct": None, "trailing_execute_pct": None,
             "positions_trailing": False, "reasons": []}, CFG)
        self.assertEqual(rec["recommendation"], "revalue-grid")

    def test_widen_on_atr_growth(self):
        bot = flat_bot()
        # deployed width 6.174% → new 9% = +45% > 15%, 16 grids <= 30
        rv = revalue_grid(100.0, 1.5, 0.5, 12, 5.0, band_atr=3.0,
                          deployed_mid=100.0, deployed_step_pct=0.5,
                          deployed_grids=9, deployed_low=97.0,
                          deployed_high=103.0)
        rec = make_recommendation(
            bot, rv, {"price": 100.0, "atr_pct": 1.5}, bot["observed"],
            {"take_profit_usd": None, "stop_loss_usd": None,
             "trailing_activation_pct": None, "trailing_execute_pct": None,
             "positions_trailing": False, "reasons": []}, CFG)
        self.assertEqual(rec["recommendation"], "widen")
        self.assertEqual(rec["action"]["payload"]["gridLevels"],
                         rv["grids"])

    def test_add_take_profit_when_exit_ready(self):
        bot = flat_bot()
        obs = dict(bot["observed"], realized_pnl=7.0)
        rec = make_recommendation(
            bot, fresh_revalue(), {"price": 100.0, "atr_pct": 1.0}, obs,
            {"take_profit_usd": 10.0, "stop_loss_usd": None,
             "trailing_activation_pct": None, "trailing_execute_pct": None,
             "positions_trailing": False, "reasons": ["tp"]}, CFG)
        self.assertEqual(rec["recommendation"], "add-take-profit")
        self.assertEqual(rec["exit_profile"]["take_profit_usd"], 10.0)
        self.assertEqual(rec["action"]["payload"]["takeProfitUsd"], 10.0)

    def test_resize_below_per_line_floor(self):
        bot = flat_bot()
        rec = make_recommendation(
            bot, fresh_revalue(amount_per_trade=0.5),
            {"price": 100.0, "atr_pct": 1.0}, bot["observed"],
            {"take_profit_usd": None, "stop_loss_usd": None,
             "trailing_activation_pct": None, "trailing_execute_pct": None,
             "positions_trailing": False, "reasons": []},
            CFG, min_cost=1.0)
        self.assertEqual(rec["recommendation"], "resize")
        self.assertIn("floor", rec["rationale"])


# ── engine: analyze_bot / cycle / post_deploy ─────────────────────────

class TestAnalyzeBot(unittest.TestCase):
    def test_happy_path_returns_schema_complete_rec_and_journals(self):
        # deployed mid 100, candles centered on ~100 → drift ~0 → keep,
        # but force a recommendation by drifting the channel mid to 90
        bot = flat_bot()
        bot["channel"]["mid"] = 90.0   # price ~100 → drift ~+11% >> 1%
        bot["observed"]["fills_24h"] = 5  # drifted out of channel → stalled
        opt, journal, persist = make_optimizer()
        rec = opt.analyze_bot(bot, "7", trigger="periodic", dry_run=True,
                              now=NOW)
        self.assertIsNotNone(rec)
        for key in ("id", "at", "slot", "venue", "symbol", "bot_code",
                    "status", "trigger", "price", "atr_pct", "regime",
                    "spread_pct", "revalue", "exit_profile",
                    "recommendation", "action", "expected_delta_pct",
                    "confidence", "rationale", "applied", "applied_at"):
            self.assertIn(key, rec)
        self.assertEqual(rec["slot"], "7")
        self.assertEqual(rec["venue"], "hyperliquid")
        self.assertEqual(rec["symbol"], "HYPE")
        self.assertEqual(rec["trigger"], "periodic")
        self.assertEqual(rec["recommendation"], "recenter")
        for key in ("low", "mid", "high", "step_pct", "grids",
                    "amount_per_trade", "delta_drift_pct", "delta_step_pct",
                    "delta_grids"):
            self.assertIn(key, rec["revalue"])
        for key in ("take_profit_usd", "stop_loss_usd",
                    "trailing_activation_pct", "trailing_execute_pct",
                    "positions_trailing"):
            self.assertIn(key, rec["exit_profile"])
        self.assertEqual(rec["action"]["type"], "edit")
        self.assertFalse(rec["action"]["apply"])
        # journaled: recenter + expected_delta >= min_improvement
        kinds = [e["kind"] for e in journal.events]
        self.assertIn("position-optimizer", kinds)
        ev = [e for e in journal.events if e["kind"]
              == "position-optimizer"][-1]
        self.assertEqual(ev["slot"], "7")
        self.assertEqual(ev["recommendation"], "recenter")
        # dry_run respected: nothing persisted, cooldown recorded
        self.assertEqual(persist.recs, [])
        self.assertEqual(bot["position_optimizer"]["last_analyzed_at"], NOW)

    def test_returns_none_on_fetch_failure(self):
        opt, journal, _ = make_optimizer(rows=[])
        rec = opt.analyze_bot(flat_bot(), "7", now=NOW)
        self.assertIsNone(rec)
        opt2, _, _ = make_optimizer()
        opt2.fetch_candles_fn = lambda *a, **k: (_ for _ in ()).throw(
            OSError("network down"))
        self.assertIsNone(opt2.analyze_bot(flat_bot(), "7", now=NOW))

    def test_persists_when_not_dry_run(self):
        bot = flat_bot()
        bot["channel"]["mid"] = 90.0
        bot["observed"]["fills_24h"] = 5
        opt, journal, persist = make_optimizer()
        rec = opt.analyze_bot(bot, "7", dry_run=False, now=NOW)
        self.assertEqual(len(persist.recs), 1)
        self.assertEqual(rec["id"], "rec-1")

    def test_disabled_returns_none(self):
        opt, _, _ = make_optimizer(cfg={"enabled": False})
        self.assertIsNone(opt.analyze_bot(flat_bot(), "7", now=NOW))

    def test_binance_symbol_gets_usdt_suffix_on_fetch(self):
        # candle endpoints need the full pair (ROBO -> ROBOUSDT) — same
        # convention as screen/merge.fetch_symbol / the optimizer refresh;
        # a bare base symbol 400s on binance and the bot silently skips
        # analysis (verified live 2026-09-05).
        seen = {}

        def spy(venue, symbol, interval, limit, market):
            seen.update(venue=venue, symbol=symbol, interval=interval,
                        limit=limit, market=market)
            return sine_rows()

        opt, _, _ = make_optimizer()
        opt.fetch_candles_fn = spy
        bot = flat_bot(venue="binance", symbol="ROBO")
        rec = opt.analyze_bot(bot, "3", now=NOW)
        self.assertIsNotNone(rec)
        self.assertEqual(seen["symbol"], "ROBOUSDT")
        self.assertEqual(seen["market"], "spot")
        # already-suffixed and non-binance symbols pass through unchanged
        self.assertEqual(po._fetch_symbol("binance", "BTCUSDT"), "BTCUSDT")
        self.assertEqual(po._fetch_symbol("hyperliquid", "HYPE"), "HYPE")
        self.assertEqual(po._fetch_symbol("binance", "ETH/USDC"), "ETHUSDC")


class TestCycle(unittest.TestCase):
    def test_respects_cooldown(self):
        bot = flat_bot()
        bot["position_optimizer"] = {"last_analyzed_at": NOW - 600}
        opt, _, _ = make_optimizer()
        recs = opt.cycle({"7": bot}, dry_run=True, now=NOW)  # 10 min < 60
        self.assertEqual(recs, [])
        # 2h old → analyzed
        bot2 = flat_bot()
        bot2["position_optimizer"] = {"last_analyzed_at": NOW - 7200}
        recs = opt.cycle({"8": bot2}, dry_run=True, now=NOW)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["slot"], "8")

    def test_skips_error_bots(self):
        bot = flat_bot()
        bot["observed"]["status"] = "error"
        opt, _, _ = make_optimizer()
        self.assertEqual(opt.cycle({"7": bot}, dry_run=True, now=NOW), [])

    def test_cycle_over_fleet(self):
        bots = {str(i): flat_bot() for i in range(3)}
        opt, _, _ = make_optimizer()
        recs = opt.cycle(bots, dry_run=True, now=NOW)
        self.assertEqual(len(recs), 3)
        self.assertEqual({r["slot"] for r in recs}, {"0", "1", "2"})


class TestPostDeploy(unittest.TestCase):
    def test_ignores_cooldown_and_journals_even_keep(self):
        bot = flat_bot()
        bot["position_optimizer"] = {"last_analyzed_at": NOW}  # fresh
        opt, journal, _ = make_optimizer()
        # cycle would skip it …
        self.assertEqual(opt.cycle({"7": bot}, dry_run=True, now=NOW), [])
        # … but post_deploy runs anyway and journals even on keep
        rec = opt.post_deploy(bot, "7", dry_run=True)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["trigger"], "post-deploy")
        kinds = [e["kind"] for e in journal.events]
        self.assertIn("position-optimizer", kinds)
        ev = [e for e in journal.events if e["kind"]
              == "position-optimizer"][-1]
        self.assertEqual(ev["trigger"], "post-deploy")
        self.assertEqual(bot["position_optimizer"]["last_analyzed_at"], NOW)


if __name__ == "__main__":
    unittest.main()
