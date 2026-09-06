#!/usr/bin/env python3
"""Daemon-side position-optimizer wiring tests — hermetic, no network.

Covers the daemon integration of position_optimizer.py (which is tested
separately and NOT touched here): engine wiring in Daemon.__init__, the
manage-loop cadence method, the commit_deploy post-deploy hook, the
demo-cap upward relearn in health_cycle, and the PocketBase
recommendation persistence (client method + daemon persist closure).
"""
import os
import sys
import unittest
from unittest import mock

import daemon  # noqa: E402  (path set up by test_daemon_manage import below)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "policy"))

try:
    from test_daemon_manage import ManageHarness, PROFILES  # noqa: F401
except ImportError:  # pytest rootdir=agents/grid-autonomy
    from tests.test_daemon_manage import ManageHarness, PROFILES  # noqa: F401

import pbclient
import position_optimizer


class PositionOptimizerHarness(ManageHarness):
    """ManageHarness + health-cycle isolation (no browser, no observe)."""

    def setUp(self):
        super().setUp()
        # health_cycle paths that would otherwise reach a real browser:
        # the watchdog probes CDP and can try to LAUNCH CloakBrowser.
        patcher = mock.patch.object(
            daemon.Daemon, "browser_watchdog", lambda self: True)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestWiring(PositionOptimizerHarness):
    # (a) engine wiring
    def test_init_wires_position_optimizer(self):
        d = self.make_daemon()
        self.assertIsInstance(d.position_optimizer,
                              position_optimizer.PositionOptimizer)
        self.assertTrue(d.capabilities.get("position_optimizer"))

    # (b) manage-loop cadence
    def test_interval_s_config_driven(self):
        d = self.make_daemon()
        self.assertEqual(d.position_optimizer_interval_s(), 15 * 60)
        d.config["position_optimizer"]["interval_min"] = 7
        self.assertEqual(d.position_optimizer_interval_s(), 7 * 60)

    def test_interval_s_disabled_or_missing(self):
        d = self.make_daemon()
        d.config["position_optimizer"]["enabled"] = False
        self.assertEqual(d.position_optimizer_interval_s(), 0)
        d.config["position_optimizer"]["enabled"] = True
        d.position_optimizer = None
        self.assertEqual(d.position_optimizer_interval_s(), 0)


class _StubPO:
    def __init__(self, rec):
        self.rec = rec
        self.calls = []

    def post_deploy(self, bot, slot_key, dry_run=True):
        self.calls.append((bot.get("bot_code"), slot_key, dry_run))
        return self.rec


class TestPostDeployHook(PositionOptimizerHarness):
    # (c) commit_deploy invokes post_deploy + stores last_recommendation
    def _commit(self, d):
        action = {"kind": "DEPLOY-PAPER", "profile": "profile-1"}
        ticket = {"regime": "neutral", "archetype":
                  "Neutral Grid (mean-reversion)"}
        brief = {"metrics": {"atr_pct": 2.0}}
        cand = {"symbol": "DOGE", "venue": "hyperliquid",
                "tv_symbol": "BINANCE:DOGEUSDT", "regime": "neutral",
                "score_final": 80.0, "archetype":
                "Neutral Grid (mean-reversion)"}
        payloads = {
            "upsert": {"pairCode": "PAIR1", "lowPrice": 90.0,
                       "midPrice": 100.0, "highPrice": 110.0,
                       "gridPercentStep": 0.005, "gridLevels": 10,
                       "amountPerTrade": 10.0, "profileCode": "profile-1",
                       "paperTrading": True},
            "grid_bot": {"profit_per_grid_pct": 0.5, "grids": 10},
            "guard_ctx": {"total_commitment": 50.0},
            "stagnation_policy": {"regime": "neutral", "step": 0.005},
        }
        slot = next(s for s in d.state["slots"] if str(s["slot"]) == "1")
        return d.commit_deploy(action, ticket, payloads, brief, cand, slot,
                               dry_run=False)

    def test_post_deploy_called_and_recommendation_stored(self):
        d = self.make_daemon()
        stub = _StubPO({"recommendation": "widen", "slot": "1"})
        d.position_optimizer = stub
        self._commit(d)
        bot = d.state["active_bots"]["1"]
        self.assertEqual(bot["bot_code"], "NEWBOT")
        self.assertEqual(stub.calls, [("NEWBOT", "1", False)])
        self.assertEqual(
            bot["position_optimizer"]["last_recommendation"], "widen")

    def test_post_deploy_keep_leaves_no_marker(self):
        d = self.make_daemon()
        stub = _StubPO({"recommendation": "keep", "slot": "1"})
        d.position_optimizer = stub
        self._commit(d)
        bot = d.state["active_bots"]["1"]
        self.assertEqual(stub.calls, [("NEWBOT", "1", False)])
        self.assertNotIn("position_optimizer", bot)

    def test_post_deploy_failure_is_fail_soft(self):
        d = self.make_daemon()

        class _Boom:
            def post_deploy(self, *a, **k):
                raise RuntimeError("boom")

        d.position_optimizer = _Boom()
        self._commit(d)  # must not raise
        self.assertIn("NEWBOT",
                      [b.get("bot_code")
                       for b in d.state["active_bots"].values()])
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertIn("position-optimizer-error", kinds)

    def test_real_engine_post_deploy_offline(self):
        """End-to-end through the real engine with stubbed candles (advisory:
        dry_run so nothing is persisted or edited on WunderTrading)."""
        d = self.make_daemon()
        rec = d.position_optimizer.post_deploy(
            {"symbol": "DOGE", "venue": "hyperliquid", "bot_code": "NEWBOT",
             "channel": {"low": 0.09, "mid": 0.10, "high": 0.11,
                         "step_pct": 0.5, "grids": 10},
             "upsert": {"amountPerTrade": 10.0, "lowPrice": 0.09,
                        "midPrice": 0.10, "highPrice": 0.11,
                        "gridLevels": 10, "gridPercentStep": 0.005},
             "ticket": {"regime": "neutral"},
             "stagnation_policy": {"regime": "neutral", "step": 0.005}},
            "1", dry_run=True)
        self.assertTrue(rec is None or isinstance(rec, dict))
        if rec:
            self.assertEqual(rec["trigger"], "post-deploy")


class TestDemoCapRelearn(PositionOptimizerHarness):
    # (d) the create-400 cap relearns UPWARD in health_cycle
    def test_cap_raises_to_live_count(self):
        d = self.make_daemon()
        d.state["demo_bot_cap"] = 3
        d.state["active_bots"]["1"] = {"symbol": "DOGE",
                                       "venue": "hyperliquid",
                                       "bot_code": "B1"}
        # live WT shows 5 paper grid bots — above the learned cap of 3
        self.grid_status_ret = [{"code": f"B{i}", "status": "active"}
                                for i in range(5)]
        with mock.patch("daemon.observe_all_safe",
                        lambda bots: {"1": {"error": "offline test"}}):
            d.health_cycle(dry_run=True)
        self.assertEqual(d.state["demo_bot_cap"], 5)
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertIn("demo-cap-relearn", kinds)

    def test_cap_not_lowered_and_no_relearn_at_or_below(self):
        d = self.make_daemon()
        d.state["demo_bot_cap"] = 5
        d.state["active_bots"]["1"] = {"symbol": "DOGE",
                                       "venue": "hyperliquid",
                                       "bot_code": "B1"}
        self.grid_status_ret = [{"code": f"B{i}", "status": "active"}
                                for i in range(3)]
        with mock.patch("daemon.observe_all_safe",
                        lambda bots: {"1": {"error": "offline test"}}):
            d.health_cycle(dry_run=True)
        self.assertEqual(d.state["demo_bot_cap"], 5)
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertNotIn("demo-cap-relearn", kinds)

    def test_stopped_bots_do_not_count(self):
        d = self.make_daemon()
        d.state["demo_bot_cap"] = 3
        d.state["active_bots"]["1"] = {"symbol": "DOGE",
                                       "venue": "hyperliquid",
                                       "bot_code": "B1"}
        self.grid_status_ret = [
            {"code": "B0", "status": "active"},
            {"code": "B1", "status": "stopped"},
            {"code": "B2", "status": "stopped_and_close_all"},
        ]
        with mock.patch("daemon.observe_all_safe",
                        lambda bots: {"1": {"error": "offline test"}}):
            d.health_cycle(dry_run=True)
        self.assertEqual(d.state["demo_bot_cap"], 3)  # 1 live <= 3 → unchanged
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertNotIn("demo-cap-relearn", kinds)


class TestPBRecommendation(unittest.TestCase):
    # (e) client method: id → recommendation_id rename, into "recommendations"
    def test_recommendation_renames_id(self):
        pb = pbclient.PB.__new__(pbclient.PB)  # skip env-dependent __init__
        with mock.patch.object(pbclient.PB, "create",
                               return_value={"id": "pb-1"}) as m:
            out = pb.recommendation({"id": "uuid-9", "recommendation": "widen",
                                     "expected_delta_pct": 4.2})
        m.assert_called_once_with(
            "recommendations", {"recommendation_id": "uuid-9",
                               "recommendation": "widen",
                               "expected_delta_pct": 4.2})
        self.assertEqual(out, {"id": "pb-1"})

    def test_recommendation_without_id_untouched(self):
        pb = pbclient.PB.__new__(pbclient.PB)
        with mock.patch.object(pbclient.PB, "create",
                               return_value={"id": "pb-2"}) as m:
            pb.recommendation({"recommendation": "keep"})
        m.assert_called_once_with("recommendations",
                                  {"recommendation": "keep"})

    def test_daemon_persist_closure(self):
        d = daemon.Daemon.__new__(daemon.Daemon)  # no state/config needed
        with mock.patch("daemon._pb", return_value=None):
            self.assertIsNone(d._pb_recommendation_persist({"id": "x"}))
        fake = mock.Mock()
        fake.recommendation.return_value = {"id": "pb-3"}
        with mock.patch("daemon._pb", return_value=fake):
            self.assertEqual(
                d._pb_recommendation_persist({"id": "uuid"}), "pb-3")
        fake.recommendation.assert_called_once_with({"id": "uuid"})
        # a failing PB write must be swallowed (returns None, no raise)
        fake2 = mock.Mock()
        fake2.recommendation.side_effect = RuntimeError("pb down")
        with mock.patch("daemon._pb", return_value=fake2):
            self.assertIsNone(d._pb_recommendation_persist({"id": "y"}))


class TestEngineCycle(unittest.TestCase):
    # (f) cycle() end-to-end on the real engine with a stub fetch
    @staticmethod
    def _stub_fetch(venue, symbol, interval, limit, market="spot"):
        """OHLC drifting UP out of the 0.09–0.11 channel → revalue."""
        return [(0.10, 0.10 + 0.002 * i, 0.099, 0.10 + 0.001 * i)
                for i in range(limit or 100)]

    def _bot(self, code="B1", error=False):
        return {
            "symbol": "DOGE", "venue": "hyperliquid", "bot_code": code,
            "channel": {"low": 0.09, "mid": 0.10, "high": 0.11,
                        "step_pct": 0.5, "grids": 10},
            "upsert": {"amountPerTrade": 10.0, "lowPrice": 0.09,
                       "midPrice": 0.10, "highPrice": 0.11,
                       "gridLevels": 10, "gridPercentStep": 0.005},
            "ticket": {"regime": "neutral", "amount": 50.0},
            "stagnation_policy": {"regime": "neutral", "step": 0.005},
            "observed": {"status": "error", "price": 0.10} if error
            else {"status": "active", "price": 0.16,
                  "realized_pnl": 1.0, "unrealized_pnl": 0.5,
                  "fills_24h": 2},
        }

    def test_cycle_skips_error_bots_and_honors_cooldown(self):
        events, persisted = [], []
        po = position_optimizer.PositionOptimizer(
            fetch_candles_fn=self._stub_fetch,
            journal_fn=events.append,
            persist_fn=lambda rec: persisted.append(rec) or "pid")
        bots = {"1": self._bot(), "2": self._bot("B2", error=True)}
        recs = po.cycle(bots, dry_run=True, now=1000.0)
        self.assertTrue(isinstance(recs, list))
        self.assertEqual([r["slot"] for r in recs], ["1"])
        for r in recs:
            self.assertEqual(r["trigger"], "periodic")
            self.assertEqual(r["dry_run"], True)
            self.assertIn("recommendation", r)
        # immediate second cycle at the same timestamp: per-bot cooldown
        recs2 = po.cycle(bots, dry_run=True, now=1000.0)
        self.assertEqual(recs2, [])
        # after the cooldown window passes the bot is analyzed again
        recs3 = po.cycle(bots, dry_run=True,
                         now=1000.0 + 61 * 60)  # > cooldown_min 60
        self.assertEqual([r["slot"] for r in recs3], ["1"])

    def test_cycle_disabled_returns_empty(self):
        po = position_optimizer.PositionOptimizer(
            cfg={"enabled": False}, fetch_candles_fn=self._stub_fetch)
        self.assertEqual(po.cycle({"1": self._bot()}, dry_run=True), [])

    def test_cycle_never_raises(self):
        def boom(*a, **k):
            raise RuntimeError("fetch failed")
        po = position_optimizer.PositionOptimizer(fetch_candles_fn=boom)
        bots = {"1": self._bot(), "2": {"not": "a dict"}}
        self.assertEqual(po.cycle(bots, dry_run=True), [])


if __name__ == "__main__":
    unittest.main()
