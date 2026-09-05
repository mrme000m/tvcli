import os
import sys
import unittest
from unittest import mock

try:
    from test_daemon_manage import ManageHarness, make_payloads  # noqa: F401
except ImportError:
    from tests.test_daemon_manage import ManageHarness, make_payloads  # noqa: F401

import daemon  # noqa: E402  (path set up by test_daemon_manage import)

from stagnation import slot_plan  # noqa: E402


def _bot(symbol, venue, bot_code, **over):
    b = {"symbol": symbol, "venue": venue, "bot_code": bot_code,
         "since": "2026-09-05T00:00:00+00:00",
         "stagnation_policy": {"regime": "neutral"},
         "channel": {"low": 90.0, "mid": 100.0, "high": 110.0,
                     "step_pct": 0.5, "atr_pct": 3.0, "grids": 10},
         "upsert": make_payloads()["upsert"],
         "profile_code": "demo-hype", "pair_code": "PAIR1",
         "decision_id": "d1"}
    b.update(over)
    return b


class TestProfitExit(ManageHarness):
    """Daemon-side profit exit: WT's native takeProfit is accepted but NOT
    enforced server-side for grid bots, so health_cycle owns the exit —
    cumulative total PnL (realized + mark) ≥ take_profit_usd AND every
    open line ≥ 0 → stop at profit, slot recycled. Never a losing close."""

    def _daemon_with_bot(self, obs):
        d = self.make_daemon()
        d.config.setdefault("grid_defaults", {})["take_profit_pct"] = 0.10
        d.state["active_bots"]["1"] = _bot("HYPE", "hyperliquid", "B1",
                                           take_profit_usd=10.0)
        d.state["active_bots"]["1"]["observed"] = obs
        return d

    def _run(self, d):
        with mock.patch("daemon.observe_all_safe",
                        return_value={"1": d.state["active_bots"]["1"]
                                      ["observed"]}):
            d.health_cycle(dry_run=False)

    def test_exits_at_profit_flat_book(self):
        d = self._daemon_with_bot(
            {"status": "active", "price": 100.0, "error": None,
             "realized_pnl": 10.5, "unrealized_pnl": 0.0,
             "open_lines": 0, "open_losing": 0, "fills_24h": 5,
             "realized_ratio": 1.0, "ladder_full": False,
             "dd_vs_atr_band": 0.0})
        self._run(d)
        stops = [op for op in self.ops if op[0] == "stop"]
        self.assertEqual(stops, [("stop", "B1", False)])
        kinds = [e["kind"] for e in d.state["journal"]]
        self.assertIn("profit-exit", kinds)
        self.assertTrue(d.state["active_bots"]["1"].get("needs_reanalysis"))

    def test_exits_at_profit_all_lines_positive(self):
        d = self._daemon_with_bot(
            {"status": "active", "price": 100.0, "error": None,
             "realized_pnl": 4.0, "unrealized_pnl": 7.0,
             "open_lines": 3, "open_losing": 0, "fills_24h": 2,
             "realized_ratio": 0.5, "ladder_full": False,
             "dd_vs_atr_band": 0.0})
        self._run(d)
        self.assertIn("profit-exit",
                      [e["kind"] for e in d.state["journal"]])

    def test_no_exit_when_one_line_losing(self):
        # the strict per-line rule: a net-positive total with ONE under-
        # water line must NOT exit — closing realizes that line's loss
        d = self._daemon_with_bot(
            {"status": "active", "price": 100.0, "error": None,
             "realized_pnl": 4.0, "unrealized_pnl": 7.0,
             "open_lines": 3, "open_losing": 1, "fills_24h": 2,
             "realized_ratio": 0.5, "ladder_full": False,
             "dd_vs_atr_band": 0.0})
        self._run(d)
        self.assertEqual([op for op in self.ops if op[0] == "stop"], [])
        self.assertNotIn("profit-exit",
                         [e["kind"] for e in d.state["journal"]])

    def test_no_exit_below_target(self):
        d = self._daemon_with_bot(
            {"status": "active", "price": 100.0, "error": None,
             "realized_pnl": 3.0, "unrealized_pnl": 2.0,
             "open_lines": 0, "open_losing": 0, "fills_24h": 1,
             "realized_ratio": 0.2, "ladder_full": False,
             "dd_vs_atr_band": 0.0})
        self._run(d)
        self.assertEqual([op for op in self.ops if op[0] == "stop"], [])

    def test_no_exit_when_per_line_blind_with_positions(self):
        # open_lines None (per-line data unavailable) with an open book →
        # fail closed: only the aggregate is known and it can hide a
        # losing line
        d = self._daemon_with_bot(
            {"status": "active", "price": 100.0, "error": None,
             "realized_pnl": 9.0, "unrealized_pnl": 3.0,
             "open_lines": None, "open_losing": None, "fills_24h": 1,
             "realized_ratio": 0.2, "ladder_full": False,
             "dd_vs_atr_band": 0.0})
        self._run(d)
        self.assertEqual([op for op in self.ops if op[0] == "stop"], [])

    def test_no_exit_without_target(self):
        d = self._daemon_with_bot(
            {"status": "active", "price": 100.0, "error": None,
             "realized_pnl": 50.0, "unrealized_pnl": 0.0,
             "open_lines": 0, "open_losing": 0, "fills_24h": 9,
             "realized_ratio": 2.0, "ladder_full": False,
             "dd_vs_atr_band": 0.0})
        d.state["active_bots"]["1"].pop("take_profit_usd")
        d.config["grid_defaults"]["take_profit_pct"] = 0.0
        self._run(d)
        self.assertEqual([op for op in self.ops if op[0] == "stop"], [])

    def test_default_target_backfilled_from_slot(self):
        d = self.make_daemon()
        d.config.setdefault("grid_defaults", {})["take_profit_pct"] = 0.10
        d.state["active_bots"]["1"] = _bot("HYPE", "hyperliquid", "B1")
        tp = d._default_take_profit("1")
        slot = next(s for s in d.state["slots"] if s["slot"] == 1)
        self.assertAlmostEqual(tp, round(slot["balance"] * 0.10, 2))


class TestHeldRecenter(ManageHarness):
    """Out-of-channel bot with losing open lines: re-center the grid on the
    current price (verified live: the edit leaves open positions untouched)
    so it keeps trading — never stop, never close at a loss."""

    def test_recenters_held_out_of_channel_bot(self):
        d = self.make_daemon()
        d.state["active_bots"]["1"] = _bot("HYPE", "hyperliquid", "B1")
        obs = {"status": "active", "price": 120.0, "error": None,  # above high
               "realized_pnl": 0.0, "unrealized_pnl": -2.0,
               "open_lines": 2, "open_losing": 2, "fills_24h": 0,
               "realized_ratio": 0.0, "ladder_full": False,
               "dd_vs_atr_band": 1.5}
        d.state["active_bots"]["1"]["observed"] = obs
        with mock.patch("daemon.observe_all_safe", return_value={"1": obs}):
            d.health_cycle(dry_run=False)
        self.assertEqual([op for op in self.ops if op[0] == "edit"],
                         [("edit", "B1", False)])
        self.assertIn("recenter", [e["kind"] for e in d.state["journal"]])

    def test_no_recenter_when_profitable_out_of_channel(self):
        # out-of-channel at profit → normal needs_reanalysis flow (the
        # optimizer may swap it); no edit forced
        d = self.make_daemon()
        d.state["active_bots"]["1"] = _bot("HYPE", "hyperliquid", "B1")
        obs = {"status": "active", "price": 120.0, "error": None,
               "realized_pnl": 0.0, "unrealized_pnl": 1.5,
               "open_lines": 1, "open_losing": 0, "fills_24h": 0,
               "realized_ratio": 0.0, "ladder_full": False,
               "dd_vs_atr_band": 0.0}
        d.state["active_bots"]["1"]["observed"] = obs
        with mock.patch("daemon.observe_all_safe", return_value={"1": obs}):
            d.health_cycle(dry_run=False)
        self.assertEqual([op for op in self.ops if op[0] == "edit"], [])


if __name__ == "__main__":
    unittest.main()
