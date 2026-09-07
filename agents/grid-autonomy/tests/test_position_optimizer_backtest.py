#!/usr/bin/env python3
"""Unit tests for the position-optimizer BACKTEST-VALIDATION stage.

Covers the opt-in (default OFF) validation of exit-add recommendations
through the wtclient grid-backtest engine: the pure config builders and
the exit overlay, the engine-level veto / pass-with-margin / skip /
fail-open paths with an injected FAKE backtest_fn (no network, no WT,
no daemon import for the engine parts), the disabled-by-default no-op,
and the daemon wiring (backtest_fn seam + the REAL pure engine on
synthetic candles — GridClient.backtest's :2087 network fetch is never
touched).
"""
import math
import os
import sys
import time
import unittest
from unittest import mock

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, HERE)

import position_optimizer as po  # noqa: E402
from position_optimizer import (  # noqa: E402
    POSITION_OPTIMIZER_DEFAULTS, backtest_configs, backtest_grid_cfg,
    candidate_exit_fields, current_exit_fields, engine_candles,
    exit_overlay_pnl)

NOW = time.time()
CFG = dict(POSITION_OPTIMIZER_DEFAULTS)

try:
    from test_position_optimizer import (  # noqa: E402
        FakeJournal, flat_bot, make_optimizer, sine_rows)
except ImportError:  # pytest rootdir=agents/grid-autonomy
    from tests.test_position_optimizer import (  # noqa: E402
        FakeJournal, flat_bot, make_optimizer, sine_rows)

# the real wtclient engine is PURE (stdlib, zero network) — use it for
# the parity/daemon tests when importable, skip otherwise
WUN_SCRIPTS = os.path.normpath(os.path.join(
    HERE, "..", "..", ".agents", "skills", "wundertrading", "scripts"))
try:
    if WUN_SCRIPTS not in sys.path:
        sys.path.insert(0, WUN_SCRIPTS)
    from wtclient import backtest as wt_backtest  # noqa: E402
    HAS_ENGINE = True
except Exception:
    wt_backtest = None
    HAS_ENGINE = False


# ── fixtures ───────────────────────────────────────────────────────────

def trade(side, strategy, ts, price):
    """One engine trade record (side != strategy = a CLOSE)."""
    return {"side": side, "strategy": strategy, "timestamp": ts,
            "price": price}


OPEN_LONG = ("long", "long")
OPEN_SHORT = ("short", "short")
CLOSE_LONG = ("short", "long")    # closing a long at `price`
CLOSE_SHORT = ("long", "short")   # closing a short at `price`


def tp_bot():
    """Aligned bot with realized $7 >= 60% of the $10 target → the
    engine recommends add-take-profit (priority head of exit adds)."""
    bot = flat_bot()
    bot["observed"]["realized_pnl"] = 7.0
    return bot


def engine_result(pnl_fiat=1.0, trades=None, **extra):
    """Minimal engine-result shape the overlay/validation consume."""
    res = {"pnlFiat": pnl_fiat, "pnl": pnl_fiat,
           "unrealizedPnlFiat": 0.0, "tradesCount": len(trades or []),
           "trades": trades or []}
    res.update(extra)
    return res


# ── pure: candle conversion ────────────────────────────────────────────

class TestEngineCandles(unittest.TestCase):
    def test_shape_and_synthetic_clock(self):
        rows = [(1.0, 2.0, 0.5, 1.5), (1.5, 2.5, 1.0, 2.0)]
        out = engine_candles(rows, now=1_000_000.0)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], {"time": (1_000_000 - 3600) * 1000.0,
                                  "high": 2.0, "low": 0.5, "close": 1.5})
        self.assertEqual(out[1], {"time": 1_000_000 * 1000.0,
                                  "high": 2.5, "low": 1.0, "close": 2.0})
        # strictly increasing 1h clocks, oldest first
        self.assertLess(out[0]["time"], out[1]["time"])

    def test_skips_malformed_rows(self):
        out = engine_candles([None, (1.0, 2.0, 0.5), (1.0, 2.0, 0.5, 1.5)],
                              now=100.0)
        self.assertEqual(len(out), 1)

    def test_empty(self):
        self.assertEqual(engine_candles([]), [])


# ── pure: config builders ──────────────────────────────────────────────

class TestBacktestConfigs(unittest.TestCase):
    def test_grid_cfg_from_deployed_geometry(self):
        bot = flat_bot()
        cfg = backtest_grid_cfg(bot)
        self.assertEqual(cfg["percents"], 0.5)        # step in PERCENT
        self.assertEqual(cfg["lowPrice"], 97.0)
        self.assertEqual(cfg["highPrice"], 103.0)
        self.assertEqual(cfg["midPrice"], 100.0)
        self.assertEqual(cfg["amountPerTrade"], 5.0)
        self.assertEqual(cfg["gridTradingType"], "neutral")
        self.assertEqual(cfg["gridType"], "interval")

    def test_grid_cfg_mid_falls_back_to_channel_midpoint(self):
        bot = flat_bot(channel={"low": 98.0, "high": 102.0})
        cfg = backtest_grid_cfg(bot)
        self.assertEqual(cfg["midPrice"], 100.0)

    def test_baseline_carries_current_exits(self):
        current = {"takeProfit": 12.0, "stopLoss": -20.0,
                   "trailingStopActivation": 4.0}
        fields = current_exit_fields(current)
        self.assertEqual(fields, {"takeProfitUsd": 12.0,
                                  "stopLossUsd": 20.0,
                                  "trailingActivationPct": 4.0})

    def test_candidate_overlays_only_the_recommended_exit(self):
        exits = {"take_profit_usd": 10.0, "trailing_activation_pct": 5.0,
                 "trailing_execute_pct": 2.0}
        current = {"stopLoss": 20.0}
        fields = candidate_exit_fields(exits, "add-take-profit", current)
        self.assertEqual(fields, {"stopLossUsd": 20.0,
                                  "takeProfitUsd": 10.0})

    def test_candidate_stop_loss_is_positive_magnitude(self):
        exits = {"stop_loss_usd": -15.0}
        fields = candidate_exit_fields(exits, "add-stop-loss", None)
        self.assertEqual(fields, {"stopLossUsd": 15.0})

    def test_configs_share_geometry_differ_only_in_exits(self):
        bot = flat_bot()
        revalue = po.revalue_grid(100.0, 1.0, 0.5, 12, 5.0)
        exits = {"take_profit_usd": 10.0}
        current = {"takeProfit": 25.0}    # materially different → rec fires
        base, cand = backtest_configs(bot, revalue, exits, current,
                                      "add-take-profit")
        for key in ("gridType", "gridTradingType", "percents",
                    "lowPrice", "midPrice", "highPrice", "amountPerTrade"):
            self.assertEqual(base[key], cand[key], key)
        self.assertEqual(base["takeProfitUsd"], 25.0)
        self.assertEqual(cand["takeProfitUsd"], 10.0)


# ── pure: exit overlay on an engine result ─────────────────────────────

class TestExitOverlayPnl(unittest.TestCase):
    def test_no_trades_reports_engine_pnl(self):
        v = exit_overlay_pnl(engine_result(pnl_fiat=1.25), {}, 10.0)
        self.assertEqual(v["realized_fiat"], 1.25)
        self.assertIsNone(v["stopped_at"])
        self.assertIsNone(v["reason"])
        self.assertEqual(v["closes"], 0)

    def test_no_exit_fields_winds_down_at_final_mark(self):
        # realized + marked unrealized when nothing fires
        res = engine_result(pnl_fiat=1.0, unrealizedPnlFiat=-0.4,
                            trades=[trade(*OPEN_LONG, 1, 100.0)])
        v = exit_overlay_pnl(res, {}, 10.0)
        self.assertAlmostEqual(v["realized_fiat"], 0.6)
        self.assertIsNone(v["reason"])

    def test_take_profit_locks_at_trigger(self):
        # open long @100, closed @101: banks (1/100 - 0.002) x $10 = $0.08
        trades = [trade(*OPEN_LONG, 1, 100.0),
                  trade(*CLOSE_LONG, 2, 101.0)]
        v = exit_overlay_pnl(engine_result(trades=trades),
                             {"takeProfitUsd": 0.05}, 10.0)
        self.assertEqual(v["reason"], "take-profit")
        self.assertEqual(v["stopped_at"], 2)
        self.assertAlmostEqual(v["realized_fiat"], 0.08, places=6)
        self.assertEqual(v["closes"], 1)

    def test_take_profit_counts_unrealized(self):
        # one open long @100 marked at 110 (an open short event): total
        # PnL = +10% x $10 = $1.00 → TP $0.50 locks it with ZERO closes
        trades = [trade(*OPEN_LONG, 1, 100.0),
                  trade(*OPEN_SHORT, 2, 110.0)]
        v = exit_overlay_pnl(engine_result(trades=trades),
                             {"takeProfitUsd": 0.50}, 10.0)
        self.assertEqual(v["reason"], "take-profit")
        self.assertAlmostEqual(v["realized_fiat"], 1.0, places=6)
        self.assertEqual(v["closes"], 0)

    def test_take_profit_not_reached_winds_down(self):
        trades = [trade(*OPEN_LONG, 1, 100.0),
                  trade(*CLOSE_LONG, 2, 101.0)]
        v = exit_overlay_pnl(
            engine_result(pnl_fiat=0.08, unrealizedPnlFiat=0.0,
                          trades=trades),
            {"takeProfitUsd": 5.0}, 10.0)
        self.assertIsNone(v["reason"])
        self.assertAlmostEqual(v["realized_fiat"], 0.08)

    def test_stop_loss_fires_on_drawdown(self):
        # two open longs (100, 98) marked at 90 → total ≈ -$1.82
        trades = [trade(*OPEN_LONG, 1, 100.0),
                  trade(*OPEN_LONG, 2, 98.0),
                  trade(*OPEN_SHORT, 3, 90.0)]
        v = exit_overlay_pnl(engine_result(trades=trades),
                             {"stopLossUsd": 1.0}, 10.0)
        self.assertEqual(v["reason"], "stop-loss")
        expected = ((90 - 100) / 100 + (90 - 98) / 98) * 10.0
        self.assertAlmostEqual(v["realized_fiat"], expected, places=6)

    def test_trailing_arms_then_executes_on_giveback(self):
        # $1 activation / $0.50 execute on a $100 slot. Ledger: two open
        # longs (100, 110) mark 110 → total $1.00 → armed; close @111 →
        # total $1.1709 (peak); mark 105 → total $0.5709 → giveback
        # $0.60 >= $0.50 → trailing exit locks $0.5709
        trades = [trade(*OPEN_LONG, 1, 100.0),
                  trade(*OPEN_LONG, 2, 110.0),   # total +$1.00 → armed
                  trade(*CLOSE_LONG, 3, 111.0),  # total $1.1709 (peak)
                  trade(*OPEN_SHORT, 4, 105.0)]   # total $0.5709 → exit
        v = exit_overlay_pnl(engine_result(trades=trades),
                             {"trailingActivationPct": 1.0,
                              "trailingExecutePct": 0.5},
                             10.0, slot_balance=100.0)
        self.assertEqual(v["reason"], "trailing")
        self.assertEqual(v["stopped_at"], 4)
        self.assertAlmostEqual(v["realized_fiat"], 0.570909, places=6)

    def test_trailing_never_arms_winds_down(self):
        trades = [trade(*OPEN_LONG, 1, 100.0),
                  trade(*CLOSE_LONG, 2, 101.0),
                  trade(*OPEN_SHORT, 3, 120.0)]   # total $0.08 < $1 → never
        v = exit_overlay_pnl(
            engine_result(pnl_fiat=0.08, unrealizedPnlFiat=2.0,
                          trades=trades),
            {"trailingActivationPct": 1.0, "trailingExecutePct": 5.0},
            10.0, slot_balance=100.0)
        self.assertIsNone(v["reason"])
        self.assertAlmostEqual(v["realized_fiat"], 2.08)

    def test_close_entry_resolution(self):
        # closing a long at 101 resolves the open long JUST BELOW it
        # (the adjacent grid level), not any other open long
        trades = [trade(*OPEN_LONG, 1, 90.0),
                  trade(*OPEN_LONG, 2, 100.0),
                  trade(*CLOSE_LONG, 3, 101.0)]
        v = exit_overlay_pnl(engine_result(trades=trades),
                             {"takeProfitUsd": 100.0}, 10.0)
        self.assertIsNone(v["reason"])
        # realized = (101-100)/100 - fee (the 90 entry stays open);
        # wind-down falls back to the engine aggregates
        self.assertEqual(v["closes"], 1)


# ── engine level: injected fake backtest_fn ────────────────────────────

class TestBacktestValidation(unittest.TestCase):
    """backtest_validate (default False) → no-op; ON → candidate exit
    config must beat the current-exit baseline's locked-in PnL by the
    margin or the rec is downgraded to keep (veto journaled)."""

    def _opt(self, rows=None, **cfg_over):
        rows = sine_rows() if rows is None else rows
        cfg = dict(CFG)
        cfg.update(cfg_over)
        journal = FakeJournal()
        opt = po.PositionOptimizer(
            cfg=cfg, journal_fn=journal, persist_fn=None,
            fetch_candles_fn=lambda *a, **k: rows,
            now_fn=lambda: NOW)
        return opt, journal

    def _fake_bt(self, baseline_pnl=5.0, candidate_pnl=2.0, calls=None):
        def bt(grid_cfg, candles):
            if calls is not None:
                calls.append((dict(grid_cfg), list(candles)))
            # candidate = the cfg carrying the recommended exit overlay
            is_cand = any(k in grid_cfg for k in
                          ("takeProfitUsd", "stopLossUsd",
                           "trailingActivationPct"))
            return engine_result(pnl_fiat=(candidate_pnl if is_cand
                                           else baseline_pnl))
        return bt

    # disabled by default: no calls, byte-for-byte old behavior
    def test_disabled_by_default_is_a_no_op(self):
        calls = []
        opt, journal = self._opt()
        opt.backtest_fn = self._fake_bt(calls=calls)
        rec = opt.analyze_bot(tp_bot(), "7", now=NOW)
        self.assertEqual(rec["recommendation"], "add-take-profit")
        self.assertEqual(calls, [])
        self.assertNotIn("backtest", rec)
        kinds = [e["kind"] for e in journal.events]
        self.assertNotIn("position-optimizer-backtest-veto", kinds)
        self.assertNotIn("position-optimizer-backtest", kinds)

    def test_veto_downgrades_to_keep_and_journals_both_results(self):
        opt, journal = self._opt(backtest_validate=True,
                                 backtest_min_edge_pct=0.5)
        opt.backtest_fn = self._fake_bt(baseline_pnl=5.0,
                                        candidate_pnl=2.0)
        bot = tp_bot()
        rec = opt.analyze_bot(bot, "7", now=NOW)
        self.assertEqual(rec["recommendation"], "keep")
        self.assertEqual(rec["expected_delta_pct"], 0.0)
        self.assertNotIn("exit_kwargs", rec["action"])
        self.assertIn("backtest veto", rec["rationale"])
        bt = rec["backtest"]
        self.assertTrue(bt["validated"])
        self.assertFalse(bt["passed"])
        self.assertEqual(bt["edge_usd"], -3.0)
        self.assertEqual(bt["margin_usd"], 0.5)   # 0.5% of $100 slot
        self.assertEqual(bt["baseline"]["pnlFiat"], 5.0)
        self.assertEqual(bt["candidate"]["pnlFiat"], 2.0)
        vetoes = [e for e in journal.events
                  if e["kind"] == "position-optimizer-backtest-veto"]
        self.assertEqual(len(vetoes), 1)
        self.assertEqual(vetoes[0]["recommendation"], "add-take-profit")
        self.assertEqual(vetoes[0]["baseline"]["pnlFiat"], 5.0)
        self.assertEqual(vetoes[0]["candidate"]["pnlFiat"], 2.0)
        self.assertEqual(vetoes[0]["edge_usd"], -3.0)

    def test_veto_prevents_the_apply_path(self):
        applied = []

        def fake_apply(code, kwargs):
            applied.append((code, dict(kwargs)))
            return {"ok": True}

        opt, journal = self._opt(backtest_validate=True, apply=True)
        opt.backtest_fn = self._fake_bt(baseline_pnl=5.0,
                                        candidate_pnl=2.0)
        opt.apply_fn = fake_apply
        rec = opt.analyze_bot(tp_bot(), "7", now=NOW)
        self.assertEqual(rec["recommendation"], "keep")
        self.assertEqual(applied, [])
        self.assertFalse(rec["applied"])

    def test_pass_with_margin_keeps_the_rec(self):
        opt, journal = self._opt(backtest_validate=True,
                                 backtest_min_edge_pct=0.5)
        opt.backtest_fn = self._fake_bt(baseline_pnl=5.0,
                                        candidate_pnl=9.0)
        rec = opt.analyze_bot(tp_bot(), "7", now=NOW)
        self.assertEqual(rec["recommendation"], "add-take-profit")
        self.assertTrue(rec["backtest"]["passed"])
        self.assertEqual(rec["backtest"]["edge_usd"], 4.0)
        self.assertEqual(rec["action"]["exit_kwargs"],
                         {"take_profit": 10.0})
        passes = [e for e in journal.events
                  if e["kind"] == "position-optimizer-backtest"]
        self.assertEqual(len(passes), 1)
        self.assertTrue(passes[0]["passed"])
        self.assertNotIn("position-optimizer-backtest-veto",
                         [e["kind"] for e in journal.events])

    def test_edge_exactly_at_margin_passes(self):
        opt, _ = self._opt(backtest_validate=True,
                           backtest_min_edge_pct=0.5)
        opt.backtest_fn = self._fake_bt(baseline_pnl=5.0,
                                        candidate_pnl=5.5)
        rec = opt.analyze_bot(tp_bot(), "7", now=NOW)
        self.assertEqual(rec["recommendation"], "add-take-profit")
        self.assertTrue(rec["backtest"]["passed"])

    def test_edge_just_below_margin_vetoes(self):
        opt, _ = self._opt(backtest_validate=True,
                           backtest_min_edge_pct=0.5)
        opt.backtest_fn = self._fake_bt(baseline_pnl=5.0,
                                        candidate_pnl=5.49)
        rec = opt.analyze_bot(tp_bot(), "7", now=NOW)
        self.assertEqual(rec["recommendation"], "keep")
        self.assertFalse(rec["backtest"]["passed"])

    def test_missing_backtest_fn_fails_open(self):
        opt, journal = self._opt(backtest_validate=True)
        self.assertIsNone(opt.backtest_fn)
        rec = opt.analyze_bot(tp_bot(), "7", now=NOW)
        self.assertEqual(rec["recommendation"], "add-take-profit")  # unchanged
        self.assertNotIn("backtest", rec)
        skips = [e for e in journal.events
                 if e["kind"] == "position-optimizer-backtest-skip"]
        self.assertEqual(len(skips), 1)
        self.assertIn("no backtest_fn", skips[0]["msg"])

    def test_backtest_fn_raising_fails_open(self):
        def boom(grid_cfg, candles):
            raise RuntimeError("engine exploded")

        opt, journal = self._opt(backtest_validate=True)
        opt.backtest_fn = boom
        rec = opt.analyze_bot(tp_bot(), "7", now=NOW)
        self.assertEqual(rec["recommendation"], "add-take-profit")
        self.assertNotIn("backtest", rec)
        errors = [e for e in journal.events
                  if e["kind"] == "position-optimizer-error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("fail-open", errors[0]["msg"])

    def test_geometry_recs_are_never_validated(self):
        calls = []
        # drift 5% vs deployed mid 100 with step 0.5 → recenter rec
        bot = flat_bot()
        bot["observed"]["price"] = 105.0
        opt, _ = self._opt(backtest_validate=True)
        opt.backtest_fn = self._fake_bt(calls=calls)
        rows = sine_rows(base=105.0)
        opt.fetch_candles_fn = lambda *a, **k: rows
        rec = opt.analyze_bot(bot, "7", now=NOW)
        self.assertEqual(rec["recommendation"], "recenter")
        self.assertEqual(calls, [])
        self.assertNotIn("backtest", rec)

    def test_backtest_candles_slices_the_analysis_rows(self):
        calls = []
        rows = sine_rows(n=180)
        opt, _ = self._opt(rows=rows, backtest_validate=True,
                           backtest_candles=100)
        opt.backtest_fn = self._fake_bt(calls=calls)
        rec = opt.analyze_bot(tp_bot(), "7", now=NOW)
        self.assertEqual(len(calls), 2)     # baseline + candidate
        for _, candles in calls:
            self.assertEqual(len(candles), 100)
            self.assertEqual(candles, rows[-100:])

    def test_backtest_candles_refetches_a_longer_window(self):
        calls = []
        fetches = []

        def fetch(venue, symbol, interval, limit, market="spot"):
            fetches.append((interval, limit))
            return sine_rows(n=limit)

        cfg = dict(CFG)
        cfg.update({"backtest_validate": True, "backtest_candles": 400})
        opt = po.PositionOptimizer(
            cfg=cfg, journal_fn=FakeJournal(), persist_fn=None,
            fetch_candles_fn=fetch, now_fn=lambda: NOW)
        opt.backtest_fn = self._fake_bt(calls=calls)
        rec = opt.analyze_bot(tp_bot(), "7", now=NOW)
        # 1 analysis fetch (300) + 1 validation fetch (400)
        self.assertEqual(fetches, [("1h", 300), ("1h", 400)])
        for _, candles in calls:
            self.assertEqual(len(candles), 400)


# ── real-engine parity (pure wtclient module, zero network) ────────────

@unittest.skipUnless(HAS_ENGINE, "wtclient.backtest not importable")
class TestRealEngineParity(unittest.TestCase):
    """The overlay must reconcile with the real engine: with no exit
    fields the locked-in PnL is the engine's own wind-down total, and a
    TP overlay stops early on the trade ledger."""

    GRID = {"gridType": "interval", "gridTradingType": "neutral",
            "percents": 0.5, "lowPrice": 97.0, "midPrice": 100.0,
            "highPrice": 103.0, "amountPerTrade": 5.0}

    def _run(self, rows, exits):
        candles = engine_candles(rows, now=1_800_000_000)
        res = wt_backtest.run_backtest(
            wt_backtest.build_input(self.GRID, candles), candles)
        return res, exit_overlay_pnl(res, exits, amount=5.0)

    def sine(self, n=300, base=100.0, amp=2.0):
        rows = []
        for i in range(n):
            c = base + amp * math.sin(2 * math.pi * i * 24 / n)
            rows.append((c, c * 1.01, c * 0.99, c))
        return rows

    def test_overlay_reconciles_with_engine_totals(self):
        res, v = self._run(self.sine(), {})
        self.assertIsNone(v["reason"])
        # wind-down = realized + marked unrealized ≈ totalResultFiat
        self.assertAlmostEqual(v["realized_fiat"],
                               res["pnlFiat"] + res["unrealizedPnlFiat"],
                               places=4)
        self.assertEqual(v["closes"],
                         res["positionsLong"] + res["positionsShort"])

    def test_take_profit_overlays_stop_early(self):
        res, v = self._run(self.sine(), {"takeProfitUsd": 0.05})
        self.assertEqual(v["reason"], "take-profit")
        self.assertIsNotNone(v["stopped_at"])
        self.assertLess(v["realized_fiat"],
                        res["pnlFiat"] + res["unrealizedPnlFiat"])

    def test_pump_dump_take_profit_beats_riding(self):
        # rising window then a crash: riding holds underwater longs
        # (wind-down total goes NEGATIVE) while the TP candidate locks
        # profit — the exact regime asymmetry the gate exists to catch
        rows = []
        for i in range(150):
            c = 100.0 + 20.0 * i / 150
            rows.append((c, c * 1.01, c * 0.99, c))
        for i in range(150):
            c = 120.0 - 25.0 * i / 150
            rows.append((c, c * 1.01, c * 0.99, c))
        _, base = self._run(rows, {})
        _, cand = self._run(rows, {"takeProfitUsd": 0.10})
        self.assertEqual(cand["reason"], "take-profit")
        self.assertGreater(cand["realized_fiat"],
                           base["realized_fiat"])


# ── daemon wiring (hermetic — same harness as the other daemon tests) ──

import daemon  # noqa: E402  (path set up by the harness import below)

try:
    from test_daemon_manage import ManageHarness  # noqa: E402
except ImportError:  # pytest rootdir=agents/grid-autonomy
    from tests.test_daemon_manage import ManageHarness  # noqa: E402


class BacktestWiringHarness(ManageHarness):
    """ManageHarness + health-cycle isolation (no browser, no observe)."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(
            daemon.Daemon, "browser_watchdog", lambda self: True)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestDaemonWiring(BacktestWiringHarness):
    def test_init_wires_the_backtest_seam(self):
        d = self.make_daemon()
        if daemon.HAS_WT_BACKTEST:
            self.assertEqual(d.position_optimizer.backtest_fn,
                             d._po_backtest)
        else:
            self.assertIsNone(d.position_optimizer.backtest_fn)
        # the validation stage ships OFF (advisory-safe)
        self.assertFalse(d.position_optimizer.cfg["backtest_validate"])

    @unittest.skipUnless(getattr(daemon, "HAS_WT_BACKTEST", False),
                         "wtclient.backtest not importable")
    def test_po_backtest_runs_the_pure_engine(self):
        """_po_backtest → wtclient grid-backtest engine on GIVEN candles
        (pure computation — GridClient.backtest's :2087 fetch is never
        touched, so this runs hermetically)."""
        d = self.make_daemon()
        rows = []
        for i in range(200):
            c = 100.0 + 2.0 * math.sin(2 * math.pi * i * 24 / 200)
            rows.append((c, c * 1.01, c * 0.99, c))
        res = d._po_backtest(
            {"gridType": "interval", "gridTradingType": "neutral",
             "percents": 0.5, "lowPrice": 97.0, "midPrice": 100.0,
             "highPrice": 103.0, "amountPerTrade": 5.0}, rows)
        self.assertIn("pnlFiat", res)
        self.assertIn("trades", res)
        self.assertGreaterEqual(res["tradesCount"], 0)

    @unittest.skipUnless(getattr(daemon, "HAS_WT_BACKTEST", False),
                         "wtclient.backtest not importable")
    def test_po_backtest_rejects_empty_candles(self):
        d = self.make_daemon()
        with self.assertRaises(ValueError):
            d._po_backtest({"percents": 0.5, "lowPrice": 97.0,
                            "highPrice": 103.0}, [])


if __name__ == "__main__":
    unittest.main()
