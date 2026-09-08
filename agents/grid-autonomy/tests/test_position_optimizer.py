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
    POSITION_OPTIMIZER_DEFAULTS, PositionOptimizer, current_exits,
    evaluate_exits, exit_edit_kwargs, expected_delta_for_channel,
    expected_profit_delta, make_recommendation, revalue_grid,
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

    def test_failed_persist_does_not_consume_daily_cap(self):
        # a persist that returns no record id (PB down / collection missing)
        # must NOT burn one of the max_apply_per_day slots — it used to, so
        # four phantom "successes" silently filled the cap while the PB
        # collection stayed empty and the console showed no recommendations
        bot = flat_bot()
        bot["channel"]["mid"] = 90.0
        bot["observed"]["fills_24h"] = 5
        opt, journal, _ = make_optimizer(persist=lambda rec: None)
        rec = opt.analyze_bot(bot, "7", dry_run=False, now=NOW)
        self.assertIsNotNone(rec)
        self.assertFalse(rec.get("persisted"))
        kinds = [e["kind"] for e in journal.events]
        self.assertIn("position-optimizer-error", kinds)
        # the cap is untouched: the same rec persists on a healthy backend
        ok_persist = FakePersist()
        opt.persist_fn = ok_persist
        rid = opt._persist(rec)
        self.assertEqual(rid, "rec-1")
        self.assertEqual(len(ok_persist.recs), 1)

    def test_keep_does_not_consume_daily_persist_cap(self):
        # entry-keeps are baseline records, not applies: with the cap at
        # 1, a persisted keep must not block the next actionable rec from
        # reaching PB (live 2026-09-08: four post-deploy entry keeps
        # filled the 4/day cap before any actionable rec could persist)
        opt, journal, _ = make_optimizer(
            cfg={"max_apply_per_day": 1})
        fp = FakePersist()
        opt.persist_fn = fp
        keep = {"recommendation": "keep", "slot": "1", "symbol": "CHIP"}
        self.assertEqual(opt._persist(keep), "rec-1")
        self.assertEqual(opt._persisted_today[1], 0)     # not counted
        action = {"recommendation": "recenter", "slot": "2",
                  "symbol": "ARB"}
        self.assertEqual(opt._persist(action), "rec-2")  # cap not reached
        self.assertEqual(opt._persisted_today[1], 1)
        overflow = {"recommendation": "widen", "slot": "3", "symbol": "JUP"}
        self.assertIsNone(opt._persist(overflow))        # NOW the cap bites
        self.assertEqual(len(fp.recs), 2)
        cap_msgs = [e for e in journal.events
                    if e["kind"] == "position-optimizer"
                    and "cap reached" in e.get("msg", "")]
        self.assertEqual(len(cap_msgs), 1)

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


class TestLastBotFields(unittest.TestCase):
    """Per-bot position_optimizer bookkeeping: what was recommended, how
    confident, why it ran, and WHICH data hop served the candles."""

    def setUp(self):
        # _last_fetch_hop reads market_regime.FETCH_EVENTS (lazy import in
        # the engine); seed it hermetically and reset between tests
        sys.path.insert(0, po.WUN_SCRIPTS)
        import market_regime
        self.mr = market_regime
        market_regime.FETCH_EVENTS.clear()

    def tearDown(self):
        self.mr.FETCH_EVENTS.clear()

    def test_last_fields_written_on_keep(self):
        bot = flat_bot()  # aligned channel → keep
        opt, _, _ = make_optimizer()
        rec = opt.analyze_bot(bot, "7", trigger="periodic", now=NOW)
        self.assertEqual(rec["recommendation"], "keep")
        bk = bot["position_optimizer"]
        self.assertEqual(bk["last_analyzed_at"], NOW)
        self.assertEqual(bk["last_recommendation"], "keep")
        self.assertEqual(bk["last_trigger"], "periodic")
        self.assertEqual(bk["last_delta_pct"], 0.0)
        self.assertEqual(bk["last_confidence"], rec["confidence"])
        # injected fetcher records no events → hop unknown, fail-soft
        self.assertIsNone(bk["last_fetch_hop"])

    def test_last_delta_pct_rounded_to_two(self):
        bot = flat_bot()
        bot["channel"]["mid"] = 90.0     # drift → recenter
        bot["observed"]["fills_24h"] = 5
        opt, _, _ = make_optimizer()
        rec = opt.analyze_bot(bot, "7", trigger="periodic", now=NOW)
        self.assertEqual(rec["recommendation"], "recenter")
        bk = bot["position_optimizer"]
        self.assertEqual(bk["last_delta_pct"], round(rec["expected_delta_pct"], 2))
        self.assertEqual(bk["last_trigger"], "periodic")
        self.assertIsNotNone(bk["last_confidence"])

    def test_last_fetch_hop_read_from_fetch_events(self):
        # a matching event (symbol + interval) surfaces its hop
        bot = flat_bot()  # hyperliquid HYPE
        self.mr.FETCH_EVENTS.append(
            {"ts": NOW - 60, "venue": "hyperliquid", "symbol": "HYPE",
             "interval": "1h", "hop": "vision", "rows": 300, "ms": 42})
        opt, _, _ = make_optimizer()
        opt.analyze_bot(bot, "7", now=NOW)
        self.assertEqual(bot["position_optimizer"]["last_fetch_hop"],
                         "vision")

    def test_last_fetch_hop_newest_matching_event_wins(self):
        bot = flat_bot()
        self.mr.FETCH_EVENTS.append(
            {"ts": NOW - 120, "venue": "hyperliquid", "symbol": "HYPE",
             "interval": "1h", "hop": "direct", "rows": 300, "ms": 42})
        self.mr.FETCH_EVENTS.append(
            {"ts": NOW - 60, "venue": "hyperliquid", "symbol": "HYPE",
             "interval": "1h", "hop": "tvcli", "rows": 300, "ms": 42})
        opt, _, _ = make_optimizer()
        opt.analyze_bot(bot, "7", now=NOW)
        self.assertEqual(bot["position_optimizer"]["last_fetch_hop"],
                         "tvcli")

    def test_last_fetch_hop_binance_symbol_and_interval_match(self):
        # the fetch uses the FULL pair (ROBO → ROBOUSDT) on interval 1h;
        # a 15m event or a base-symbol event must not match
        bot = flat_bot(venue="binance", symbol="ROBO")
        self.mr.FETCH_EVENTS.append(
            {"ts": NOW - 90, "venue": "binance", "symbol": "ROBO",
             "interval": "1h", "hop": "direct", "rows": 300, "ms": 42})
        self.mr.FETCH_EVENTS.append(
            {"ts": NOW - 60, "venue": "binance", "symbol": "ROBOUSDT",
             "interval": "15m", "hop": "tvcli", "rows": 96, "ms": 42})
        self.mr.FETCH_EVENTS.append(
            {"ts": NOW - 30, "venue": "binance", "symbol": "ROBOUSDT",
             "interval": "1h", "hop": "vision", "rows": 300, "ms": 42})
        opt, _, _ = make_optimizer()
        opt.analyze_bot(bot, "3", now=NOW)
        self.assertEqual(bot["position_optimizer"]["last_fetch_hop"],
                         "vision")


# ── current_exits + exit-aware recommendations ─────────────────────────

# the real enriched grid_list shape (GridClient.list, verified 2026-09-07)
ENRICHED_EXITS = {"takeProfit": 5, "stopLoss": 3,
                  "stopLossPnlCompareType": "total",
                  "trailingStopActivation": 5, "trailingStopExecute": 2,
                  "trailingStopPnlCompareType": "total",
                  "strategyProfitCondition": "trailing_stop",
                  "strategyStopLossFixedPercentRatio": 0.05,
                  "pumpProtectionOrderType": "market"}


def no_exits():
    return {"take_profit_usd": None, "stop_loss_usd": None,
            "trailing_activation_pct": None, "trailing_execute_pct": None,
            "positions_trailing": False, "reasons": []}


class TestCurrentExits(unittest.TestCase):
    def test_extracts_from_bot_exits_projection(self):
        bot = flat_bot(exits=dict(ENRICHED_EXITS))
        cur = current_exits(bot)
        self.assertEqual(cur["takeProfit"], 5)
        self.assertEqual(cur["stopLoss"], 3)
        self.assertEqual(cur["trailingStopActivation"], 5)
        self.assertEqual(cur["trailingStopExecute"], 2)
        self.assertEqual(cur["strategyProfitCondition"], "trailing_stop")
        self.assertEqual(cur["strategyStopLossFixedPercentRatio"], 0.05)
        self.assertEqual(cur["pumpProtectionOrderType"], "market")

    def test_extracts_from_observed_exits(self):
        bot = flat_bot()
        bot["observed"]["exits"] = dict(ENRICHED_EXITS)
        self.assertEqual(current_exits(bot)["takeProfit"], 5)

    def test_extracts_from_top_level_grid_list_fields(self):
        bot = flat_bot()
        bot.update({"takeProfit": 7, "stopLoss": None})
        cur = current_exits(bot)
        self.assertEqual(cur["takeProfit"], 7)
        self.assertIsNone(cur["stopLoss"])

    def test_unknown_when_no_exit_fields(self):
        self.assertEqual(current_exits(flat_bot()), {})
        self.assertEqual(current_exits(None), {})
        self.assertEqual(current_exits({}), {})


class TestExitAwareRecommendations(unittest.TestCase):
    """A bot whose CURRENT exit config already has the field must NOT get
    a redundant add-* rec — the rec falls through to keep (priority order
    untouched); a materially different value still escalates."""

    def _exits_ready(self, tp=True, trail=False):
        """exits dict with the requested targets computed (TP $10,
        trail 5/2). Only what a real evaluate_exits would set for that
        trigger — so 'covered' tests are unambiguous."""
        return {"take_profit_usd": 10.0 if tp else None,
                "stop_loss_usd": None,
                "trailing_activation_pct": 5.0 if trail else None,
                "trailing_execute_pct": 2.0 if trail else None,
                "positions_trailing": False, "reasons": []}

    def _rec(self, current, **kw):
        bot = flat_bot()
        return make_recommendation(bot, fresh_revalue(),
                                   {"price": 100.0, "atr_pct": 1.0},
                                   bot["observed"],
                                   self._exits_ready(**kw), CFG,
                                   current=current)

    def test_take_profit_already_set_stays_keep(self):
        rec = self._rec(current={"takeProfit": 10.0})
        self.assertEqual(rec["recommendation"], "keep")
        self.assertEqual(rec["expected_delta_pct"], 0.0)
        self.assertIn("already configured", rec["rationale"])

    def test_take_profit_materially_different_still_recommends(self):
        rec = self._rec(current={"takeProfit": 2.0})   # target is 10
        self.assertEqual(rec["recommendation"], "add-take-profit")

    def test_trailing_already_set_falls_through_to_keep(self):
        cur = {"trailingStopActivation": 5.0, "trailingStopExecute": 2.0}
        rec = self._rec(current=cur, tp=False, trail=True)
        self.assertEqual(rec["recommendation"], "keep")

    def test_positions_sl_ratio_covers_add_stop_loss(self):
        cfg = dict(CFG, stop_loss_enabled=True)
        exits = {"take_profit_usd": None, "stop_loss_usd": -15.0,
                 "trailing_activation_pct": None,
                 "trailing_execute_pct": None, "positions_trailing": False,
                 "reasons": []}
        bot = flat_bot()
        rec = make_recommendation(bot, fresh_revalue(),
                                  {"price": 100.0, "atr_pct": 1.0},
                                  bot["observed"], exits, cfg,
                                  current={"strategyStopLossFixedPercentRatio":
                                           0.05})
        self.assertEqual(rec["recommendation"], "keep")

    def test_cumulative_stop_loss_match_covers_add_stop_loss(self):
        cfg = dict(CFG, stop_loss_enabled=True)
        exits = {"take_profit_usd": None, "stop_loss_usd": -15.0,
                 "trailing_activation_pct": None,
                 "trailing_execute_pct": None, "positions_trailing": False,
                 "reasons": []}
        bot = flat_bot()
        rec = make_recommendation(bot, fresh_revalue(),
                                  {"price": 100.0, "atr_pct": 1.0},
                                  bot["observed"], exits, cfg,
                                  current={"stopLoss": 15.0})
        self.assertEqual(rec["recommendation"], "keep")

    def test_no_current_recommends_as_before(self):
        rec = self._rec(current={})
        self.assertEqual(rec["recommendation"], "add-take-profit")

    def test_auto_extraction_from_bot_record(self):
        # make_recommendation without `current` still sees the bot's
        # observed.exits (daemon projection) — no redundant add rec
        bot = flat_bot()
        bot["observed"]["exits"] = {"takeProfit": 10.0}
        rec = make_recommendation(bot, fresh_revalue(),
                                  {"price": 100.0, "atr_pct": 1.0},
                                  bot["observed"],
                                  self._exits_ready(tp=True, trail=False),
                                  CFG)
        self.assertEqual(rec["recommendation"], "keep")
        self.assertEqual(rec["current_exits"]["takeProfit"], 10.0)

    def test_geometry_priority_unchanged_with_exits_present(self):
        # drift dominates any exit add, covered or not
        bot = flat_bot()
        bot["observed"]["exits"] = {"takeProfit": 10.0}
        rv = fresh_revalue(delta_drift_pct=3.0)
        rec = make_recommendation(bot, rv, {"price": 103.0, "atr_pct": 1.0},
                                  bot["observed"], self._exits_ready(), CFG)
        self.assertEqual(rec["recommendation"], "recenter")

    def test_evaluate_exits_reports_covered(self):
        bot = flat_bot()
        obs = dict(bot["observed"], realized_pnl=6.0)   # TP target computed
        out = evaluate_exits(bot, {}, obs, CFG,
                             current={"takeProfit": 10.0})
        self.assertEqual(out["take_profit_usd"], 10.0)  # still computed
        self.assertTrue(out["covered"]["take_profit"])
        self.assertTrue(any("already configured" in r for r in out["reasons"]))
        # and without current: not covered
        out = evaluate_exits(bot, {}, obs, CFG)
        self.assertFalse(out["covered"]["take_profit"])


class TestExitEditKwargs(unittest.TestCase):
    def test_take_profit_rec(self):
        kw = exit_edit_kwargs({"take_profit_usd": 10.0},
                              recommendation="add-take-profit")
        self.assertEqual(kw, {"take_profit": 10.0})

    def test_trailing_rec_includes_positions_trailing(self):
        kw = exit_edit_kwargs({"trailing_activation_pct": 5.0,
                               "trailing_execute_pct": 2.0,
                               "positions_trailing": True},
                              recommendation="add-trailing")
        self.assertEqual(kw, {"trailing_activation": 5.0,
                              "trailing_execute": 2.0,
                              "positions_trailing_stop": True})

    def test_trailing_rec_skips_positions_when_already_on(self):
        kw = exit_edit_kwargs({"trailing_activation_pct": 5.0,
                               "trailing_execute_pct": 2.0,
                               "positions_trailing": True},
                              recommendation="add-trailing",
                              current={"strategyProfitCondition":
                                       "trailing_stop"})
        self.assertEqual(kw, {"trailing_activation": 5.0,
                              "trailing_execute": 2.0})

    def test_stop_loss_rec_sends_positive_magnitude_and_total(self):
        kw = exit_edit_kwargs({"stop_loss_usd": -15.0},
                              recommendation="add-stop-loss")
        self.assertEqual(kw, {"stop_loss": 15.0,
                             "pnl_compare_type": "total"})


# ── opt-in exit apply path (set_exits seam) ────────────────────────────

class TestExitApplyPath(unittest.TestCase):
    """apply=False (default) or apply_fn None → advisory, byte-for-byte
    today's behavior; apply=True + apply_fn → the seam fires with the
    right kwargs and the outcome lands on the rec. Never raises."""

    def _tp_bot(self):
        # aligned channel + realized $7 >= 60% of the $10 target →
        # add-take-profit (trailing + positions trailing also armed, but
        # TP wins the priority chain)
        bot = flat_bot()
        bot["observed"]["realized_pnl"] = 7.0
        return bot

    def test_default_advisory_unchanged_without_apply_fn(self):
        opt, journal, persist = make_optimizer()
        rec = opt.analyze_bot(self._tp_bot(), "7", now=NOW)
        self.assertEqual(rec["recommendation"], "add-take-profit")
        self.assertFalse(rec["applied"])
        self.assertIsNone(rec["applied_at"])
        self.assertNotIn("apply_error", rec)
        self.assertEqual(opt.apply_fn, None)
        kinds = [e["kind"] for e in journal.events]
        self.assertNotIn("position-optimizer-applied", kinds)
        self.assertNotIn("position-optimizer-error", kinds)

    def test_apply_true_but_no_fn_still_advisory(self):
        opt, journal, _ = make_optimizer(cfg={"apply": True})
        rec = opt.analyze_bot(self._tp_bot(), "7", now=NOW)
        self.assertEqual(rec["recommendation"], "add-take-profit")
        self.assertFalse(rec["applied"])

    def test_apply_fn_called_with_set_exits_kwargs(self):
        calls = []

        def fake_apply(code, kwargs):
            calls.append((code, dict(kwargs)))
            return {"ok": True}

        opt, journal, _ = make_optimizer(cfg={"apply": True})
        opt.apply_fn = fake_apply
        bot = self._tp_bot()
        rec = opt.analyze_bot(bot, "7", now=NOW)
        self.assertEqual(calls, [("wt-42", {"take_profit": 10.0})])
        self.assertTrue(rec["applied"])
        self.assertTrue(rec["applied_at"])
        self.assertIsNone(rec.get("apply_error"))
        # the advisory action payload carries the ready-to-run kwargs
        self.assertEqual(rec["action"]["exit_kwargs"], {"take_profit": 10.0})
        ev = [e for e in journal.events
              if e["kind"] == "position-optimizer-applied"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["bot_code"], "wt-42")
        self.assertEqual(ev[0]["recommendation"], "add-take-profit")
        self.assertEqual(ev[0]["outcome"], "applied")
        self.assertEqual(ev[0]["exit_kwargs"], {"take_profit": 10.0})

    def test_apply_fn_raising_is_caught_and_recorded(self):
        def boom(code, kwargs):
            raise RuntimeError("set_exits exploded")

        opt, journal, _ = make_optimizer(cfg={"apply": True})
        opt.apply_fn = boom
        rec = opt.analyze_bot(self._tp_bot(), "7", now=NOW)
        self.assertFalse(rec["applied"])
        self.assertIsNone(rec["applied_at"])
        self.assertIn("set_exits exploded", rec["apply_error"])
        ev = [e for e in journal.events
              if e["kind"] == "position-optimizer-error"]
        self.assertEqual(len(ev), 1)
        self.assertIn("FAILED", ev[0]["msg"])

    def test_apply_fn_bad_envelope_recorded(self):
        opt, journal, _ = make_optimizer(cfg={"apply": True})
        opt.apply_fn = lambda code, kw: {"ok": False, "error": "WT said no"}
        rec = opt.analyze_bot(self._tp_bot(), "7", now=NOW)
        self.assertFalse(rec["applied"])
        self.assertEqual(rec["apply_error"], "WT said no")

    def test_dry_run_envelope_does_not_burn_daily_cap(self):
        opt, journal, _ = make_optimizer(
            cfg={"apply": True, "max_apply_per_day": 1})
        opt.apply_fn = lambda code, kw: {"ok": True, "dry_run": True}
        b1 = self._tp_bot()
        rec1 = opt.analyze_bot(b1, "7", now=NOW)
        self.assertTrue(rec1["applied"])           # journaled as planned
        self.assertTrue(rec1["applied_at"])
        self.assertEqual(opt._applied_today[1], 0)  # rehearsal: not counted
        b2 = self._tp_bot()
        rec2 = opt.analyze_bot(b2, "8", now=NOW)
        self.assertTrue(rec2["applied"])           # cap untouched

    def test_max_apply_per_day_respected(self):
        opt, journal, _ = make_optimizer(
            cfg={"apply": True, "max_apply_per_day": 1})
        opt.apply_fn = lambda code, kw: {"ok": True}
        rec1 = opt.analyze_bot(self._tp_bot(), "7", now=NOW)
        self.assertTrue(rec1["applied"])
        self.assertEqual(opt._applied_today[1], 1)
        rec2 = opt.analyze_bot(self._tp_bot(), "8", now=NOW)
        self.assertFalse(rec2["applied"])          # capped
        self.assertNotIn("apply_error", rec2)
        kinds = [e["kind"] for e in journal.events]
        cap_msgs = [e for e in journal.events
                    if e["kind"] == "position-optimizer"
                    and "cap reached" in e.get("msg", "")]
        self.assertEqual(len(cap_msgs), 1)

    def test_cap_resets_next_day(self):
        opt, _, _ = make_optimizer(
            cfg={"apply": True, "max_apply_per_day": 1})
        opt.apply_fn = lambda code, kw: {"ok": True}
        opt.analyze_bot(self._tp_bot(), "7", now=NOW)
        day2 = NOW + 26 * 3600
        opt.now_fn = lambda: day2
        rec = opt.analyze_bot(self._tp_bot(), "8", now=day2)
        self.assertTrue(rec["applied"])

    def test_geometry_recs_never_apply(self):
        # a recenter rec (drift) with apply=True + apply_fn present must
        # NOT ride the exit seam — the daemon's grid_edit path owns it
        calls = []
        opt, journal, _ = make_optimizer(cfg={"apply": True})
        opt.apply_fn = lambda code, kw: calls.append((code, kw)) or {"ok": True}
        bot = flat_bot()
        bot["channel"]["mid"] = 90.0
        bot["observed"]["fills_24h"] = 5
        rec = opt.analyze_bot(bot, "7", now=NOW)
        self.assertEqual(rec["recommendation"], "recenter")
        self.assertEqual(calls, [])
        self.assertFalse(rec["applied"])

    def test_apply_only_for_exit_recs_on_covered_bot(self):
        # exit-aware + apply: a bot whose CURRENT exits already cover the
        # computed targets stays "keep" and nothing is applied even with
        # apply=True (the engine computes TP $10 + trail 5/2 + positions
        # trailing for this bot — cover all of them)
        calls = []
        opt, journal, _ = make_optimizer(cfg={"apply": True})
        opt.apply_fn = lambda code, kw: calls.append((code, kw)) or {"ok": True}
        bot = self._tp_bot()
        bot["observed"]["exits"] = {"takeProfit": 10.0,
                                    "trailingStopActivation": 5.0,
                                    "trailingStopExecute": 2.0,
                                    "strategyProfitCondition":
                                        "trailing_stop"}
        rec = opt.analyze_bot(bot, "7", now=NOW)
        self.assertEqual(rec["recommendation"], "keep")
        self.assertEqual(calls, [])
        self.assertFalse(rec["applied"])


class TestSweepJournal(unittest.TestCase):
    """cycle() journals ONE compact position-optimizer-sweep entry per
    noteworthy cycle (rec / fetch failure / first / ≥2h) so all-keep
    periodic passes do not flood the 200-entry state journal ring."""

    def setUp(self):
        sys.path.insert(0, po.WUN_SCRIPTS)
        import market_regime
        self.mr = market_regime
        market_regime.FETCH_EVENTS.clear()

    def tearDown(self):
        self.mr.FETCH_EVENTS.clear()

    def _sweeps(self, journal):
        return [e for e in journal.events
                if e.get("kind") == "position-optimizer-sweep"]

    def test_first_cycle_journals_full_sweep(self):
        bots = {str(i): flat_bot() for i in range(3)}
        opt, journal, _ = make_optimizer()
        recs = opt.cycle(bots, dry_run=True, now=NOW)
        self.assertEqual(len(recs), 3)
        sweeps = self._sweeps(journal)
        self.assertEqual(len(sweeps), 1)
        ev = sweeps[0]
        for key in ("msg", "analyzed", "skipped_cooldown", "keeps",
                    "recs", "fetch_failures", "fetch_hops", "at"):
            self.assertIn(key, ev)
        self.assertEqual(ev["analyzed"], 3)
        self.assertEqual(ev["keeps"], 3)
        self.assertEqual(ev["recs"], [])
        self.assertEqual(ev["fetch_failures"], [])
        self.assertEqual(ev["fetch_hops"], {})
        self.assertEqual(ev["skipped_cooldown"], 0)
        self.assertIn("3 bots: 3 keep, 0 recs", ev["msg"])
        self.assertIn("0 fetch failures", ev["msg"])

    def test_all_keep_within_2h_is_silent_but_stats_computed(self):
        opt, journal, _ = make_optimizer()
        opt.cycle({"7": flat_bot()}, dry_run=True, now=NOW)
        # 65 min later: cooldown passed, all keep, nothing notable → the
        # state journal stays quiet …
        opt.cycle({"7": flat_bot()}, dry_run=True, now=NOW + 65 * 60)
        self.assertEqual(len(self._sweeps(journal)), 1)
        # … but the stats were still computed and are readable
        st = opt.last_sweep_stats
        self.assertIsNotNone(st)
        self.assertEqual(st["analyzed"], 1)
        self.assertEqual(st["keeps"], 1)
        self.assertEqual(st["skipped_cooldown"], 0)
        self.assertEqual(st["fetch_failures"], [])
        self.assertIn("1 bots: 1 keep, 0 recs", st["msg"])

    def test_non_keep_rec_journals_within_2h(self):
        bot = flat_bot()
        bot["channel"]["mid"] = 90.0     # drift → recenter
        bot["observed"]["fills_24h"] = 5
        opt, journal, _ = make_optimizer()
        opt.cycle({"7": flat_bot()}, dry_run=True, now=NOW)  # first
        opt.cycle({"8": bot}, dry_run=True, now=NOW + 65 * 60)
        sweeps = self._sweeps(journal)
        self.assertEqual(len(sweeps), 2)
        ev = sweeps[-1]
        self.assertEqual(ev["analyzed"], 1)
        self.assertEqual(ev["keeps"], 0)
        self.assertEqual(len(ev["recs"]), 1)
        self.assertEqual(ev["recs"][0]["slot"], "8")
        self.assertEqual(ev["recs"][0]["symbol"], "HYPE")
        self.assertEqual(ev["recs"][0]["rec"], "recenter")
        self.assertIn("delta_pct", ev["recs"][0])

    def test_empty_rows_fetch_failure_surfaces_in_sweep(self):
        # silent-failure visibility: an empty candle fetch used to return
        # None with NOTHING journaled (only exceptions were journaled)
        opt, journal, _ = make_optimizer()
        opt.cycle({"7": flat_bot()}, dry_run=True, now=NOW)  # first, keep
        opt2, journal2, _ = make_optimizer(rows=[])
        bot = flat_bot()
        recs = opt2.cycle({"8": bot}, dry_run=True, now=NOW + 65 * 60)
        self.assertEqual(recs, [])
        sweeps = self._sweeps(journal2)
        self.assertEqual(len(sweeps), 1)     # failure forces the journal
        ev = sweeps[0]
        self.assertEqual(ev["analyzed"], 0)
        self.assertEqual(ev["keeps"], 0)
        self.assertEqual(len(ev["fetch_failures"]), 1)
        self.assertIn("hyperliquid:HYPE", ev["fetch_failures"][0])
        self.assertIn("0 candle rows", ev["fetch_failures"][0])
        # and the per-bot marker is readable for the next cycle too
        self.assertIn("last_fetch_failure",
                      bot["position_optimizer"])
        # a later successful analysis clears the stale marker
        opt3, _, _ = make_optimizer()
        opt3.analyze_bot(bot, "8", now=NOW + 70 * 60)
        self.assertNotIn("last_fetch_failure", bot["position_optimizer"])

    def test_two_hours_since_last_sweep_journals_again(self):
        opt, journal, _ = make_optimizer()
        opt.cycle({"7": flat_bot()}, dry_run=True, now=NOW)
        opt.cycle({"7": flat_bot()}, dry_run=True,
                  now=NOW + 2 * 3600)      # exactly 2h → due
        self.assertEqual(len(self._sweeps(journal)), 2)

    def test_skipped_cooldown_counted(self):
        bot = flat_bot()
        bot["position_optimizer"] = {"last_analyzed_at": NOW - 600}
        opt, journal, _ = make_optimizer()
        recs = opt.cycle({"7": bot}, dry_run=True, now=NOW)  # 10min < 60
        self.assertEqual(recs, [])
        sweeps = self._sweeps(journal)
        self.assertEqual(len(sweeps), 1)
        self.assertEqual(sweeps[0]["skipped_cooldown"], 1)
        self.assertIn("0 bots: 0 keep, 0 recs", sweeps[0]["msg"])

    def test_fetch_hops_counted_per_hop(self):
        # two analyzed bots whose fetches were served by different hops
        bots = {"7": flat_bot(symbol="HYPE"), "8": flat_bot(symbol="PUMP")}
        self.mr.FETCH_EVENTS.append(
            {"ts": NOW - 60, "venue": "hyperliquid", "symbol": "HYPE",
             "interval": "1h", "hop": "vision", "rows": 300, "ms": 42})
        self.mr.FETCH_EVENTS.append(
            {"ts": NOW - 30, "venue": "hyperliquid", "symbol": "PUMP",
             "interval": "1h", "hop": "tvcli", "rows": 300, "ms": 42})
        opt, journal, _ = make_optimizer()
        recs = opt.cycle(bots, dry_run=True, now=NOW)
        self.assertEqual(len(recs), 2)
        sweeps = self._sweeps(journal)
        self.assertEqual(sweeps[0]["fetch_hops"], {"vision": 1, "tvcli": 1})
        self.assertIn("candles tvcli 1, vision 1", sweeps[0]["msg"])

    def test_no_candidates_no_sweep(self):
        # empty fleet (or disabled engine) → cycle returns early, no entry
        opt, journal, _ = make_optimizer()
        self.assertEqual(opt.cycle({}, dry_run=True, now=NOW), [])
        self.assertEqual(self._sweeps(journal), [])


# ── edit payload: full WT contract (WT-500 regression) ────────────────

# a full deploy upsert exactly as execution/grid_adapter.compute_upsert
# emits it (live-verified shape) — including a deploy-time exit block
FULL_UPSERT = {
    "exchangeCode": "HYPERLIQUID_SWAP", "pairCode": "HYPEUSD",
    "profilesCodes": ["demo-hype"], "gridType": "interval",
    "gridMethod": "classic", "gridTradingType": "neutral",
    "gridPercentStep": 0.005, "gridTickStep": None, "gridLevels": 13,
    "midPrice": 100.0, "initPrice": 100.0,
    "closestHighLevelPrice": 100.442, "closestLowLevelPrice": 99.943,
    "amountPerTrade": 5.0, "amountPerTradeType": "base",
    "stopOnOutOfGrid": False, "startCondition": "immediate",
    "signalCode": None, "maxRequiredAmount": None, "leverage": 1,
    "highPrice": 103.0, "lowPrice": 97.0,
    "stopCondition": "stop_and_close_all", "profitCurrencyType": "base",
    "pumpProtection": True, "pumpProtectionOrderType": "market",
    # deploy-time exit block — must NOT ride into a geometry edit
    "takeProfit": 50.0, "stopLossPnlCompareType": "total",
    "strategyStopLossFixedPercentRatio": 0.05,
}

NO_EXITS = {"take_profit_usd": None, "stop_loss_usd": None,
            "trailing_activation_pct": None, "trailing_execute_pct": None,
            "positions_trailing": False, "reasons": []}


class TestEditPayloadFullContract(unittest.TestCase):
    """Live regression (2026-09-08): WT's grid-bot upsert/edit endpoint
    rejected the 7-field partial payload with HTTP 500 while the
    daemon's own full-compute_upsert adjust path succeeded on the same
    bots. _edit_payload must overlay the fresh geometry onto a copy of
    the stored deploy upsert so the FULL contract rides along.
    """

    def test_full_contract_overlay_from_stored_upsert(self):
        bot = flat_bot(upsert=dict(FULL_UPSERT))
        rv = fresh_revalue()
        payload = po._edit_payload(bot, rv, NO_EXITS)
        # full-contract keys present and inherited from the deploy
        self.assertEqual(payload["exchangeCode"], "HYPERLIQUID_SWAP")
        self.assertEqual(payload["profilesCodes"], ["demo-hype"])
        self.assertEqual(payload["gridType"], "interval")
        self.assertEqual(payload["gridMethod"], "classic")
        self.assertFalse(payload["stopOnOutOfGrid"])
        self.assertEqual(payload["startCondition"], "immediate")
        self.assertEqual(payload["stopCondition"], "stop_and_close_all")
        self.assertTrue(payload["pumpProtection"])
        # geometry updated from the revalue
        self.assertEqual(payload["lowPrice"], rv["low"])
        self.assertEqual(payload["midPrice"], rv["mid"])
        self.assertEqual(payload["highPrice"], rv["high"])
        self.assertEqual(payload["gridPercentStep"],
                         rv["step_pct"] / 100.0)
        self.assertEqual(payload["gridLevels"], rv["grids"])
        self.assertEqual(payload["amountPerTrade"],
                         rv["amount_per_trade"])
        # initPrice tracks the new mid; closest lines are the grid
        # lines nearest around it (max ≤ mid, min > mid)
        self.assertEqual(payload["initPrice"], rv["mid"])
        below = [x for x in rv["grid_lines"] if x <= rv["mid"]]
        above = [x for x in rv["grid_lines"] if x > rv["mid"]]
        self.assertEqual(payload["closestLowLevelPrice"],
                         round(max(below), 6))
        self.assertEqual(payload["closestHighLevelPrice"],
                         round(min(above), 6))
        # deploy-time exit keys are stripped (never silently inherited)
        for k in po.UPSERT_EXIT_KEYS:
            self.assertNotIn(k, payload)
        # the stored upsert itself is untouched (overlay is a copy)
        self.assertEqual(bot["upsert"], FULL_UPSERT)

    def test_closest_lines_fall_back_when_grid_lines_empty(self):
        bot = flat_bot(upsert=dict(FULL_UPSERT))
        rv = fresh_revalue(grid_lines=[])
        payload = po._edit_payload(bot, rv, NO_EXITS)
        self.assertEqual(payload["initPrice"], rv["mid"])
        self.assertEqual(payload["closestLowLevelPrice"],
                         FULL_UPSERT["closestLowLevelPrice"])
        self.assertEqual(payload["closestHighLevelPrice"],
                         FULL_UPSERT["closestHighLevelPrice"])

    def test_exit_keys_only_when_exits_set(self):
        bot = flat_bot(upsert=dict(FULL_UPSERT))
        rv = fresh_revalue()
        payload = po._edit_payload(
            bot, rv, dict(NO_EXITS, take_profit_usd=10.0))
        self.assertEqual(payload["takeProfitUsd"], 10.0)
        self.assertNotIn("stopLossUsd", payload)
        # stored deploy exit keys still never inherited
        self.assertNotIn("takeProfit", payload)

    def test_empty_stored_upsert_falls_back_to_minimal_partial(self):
        bot = flat_bot(upsert={})
        rv = fresh_revalue()
        payload = po._edit_payload(bot, rv, NO_EXITS)
        # the minimal geometry-only form, annotated as partial
        self.assertEqual(set(payload), {
            "pairCode", "lowPrice", "midPrice", "highPrice",
            "gridPercentStep", "gridLevels", "amountPerTrade",
            "payload_partial"})
        self.assertTrue(payload["payload_partial"])
        self.assertIsNone(payload["pairCode"])
        self.assertEqual(payload["gridLevels"], rv["grids"])

    def test_make_recommendation_lifts_partial_marker(self):
        # via make_recommendation: the marker rides on the rec's ACTION
        # (not inside the payload the daemon posts to WT)
        for upsert in ({}, dict(FULL_UPSERT)):
            bot = flat_bot(upsert=dict(upsert))
            rec = make_recommendation(
                bot, fresh_revalue(), {"price": 100.0, "atr_pct": 1.0},
                bot["observed"], NO_EXITS, CFG)
            payload = rec["action"]["payload"]
            # the marker never rides inside the posted payload —
            # make_recommendation lifts it onto the action
            self.assertNotIn("payload_partial", payload)
            if upsert:
                self.assertNotIn("payload_partial", rec["action"])
            else:
                self.assertTrue(rec["action"]["payload_partial"])
            if upsert:
                self.assertEqual(payload["exchangeCode"],
                                 "HYPERLIQUID_SWAP")


if __name__ == "__main__":
    unittest.main()
