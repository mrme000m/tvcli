"""Unit tests for compute_upsert / build_ticket_payloads server-side
risk-exit fields (takeProfit / stopLoss / trailing / positions-*).

Grounded in browser-debug/docs/wt/grid-bot-api.md §9 (the "optional,
only when the UI toggles are on" payload block). No network: compute_upsert
is pure; build_ticket_payloads is monkeypatched for network-free exercise
of the kwargs threading.
"""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "execution"))

import grid_adapter
from grid_adapter import compute_upsert, build_ticket_payloads

EXIT_KEYS = (
    "takeProfit", "stopLoss", "stopLossPnlCompareType",
    "trailingStopActivation", "trailingStopExecute",
    "trailingStopPnlCompareType",
    "strategyProfitCondition", "strategyStopLossFixedPercentRatio",
)


def upsert(**kw):
    base = dict(symbol="PUMP", venue="hyperliquid", price=100.0,
                atr_pct=2.0, step_pct=1.0, grids=10,
                amount_per_trade=20.0, grid_type="neutral",
                profile_code="prof1", pair_code="123")
    base.update(kw)
    return compute_upsert(**base)


class TestNoExitsByDefault(unittest.TestCase):
    def test_default_payload_has_no_exit_keys(self):
        u = upsert()
        for k in EXIT_KEYS:
            self.assertNotIn(k, u, f"unexpected key {k} in default payload")

    def test_default_payload_unchanged(self):
        # the pre-exit contract (23 structural keys, values spot-checked)
        u = upsert()
        self.assertEqual(u["exchangeCode"], "HYPERLIQUID_SWAP")
        self.assertEqual(u["gridTradingType"], "neutral")
        self.assertEqual(u["gridPercentStep"], 0.01)
        self.assertEqual(u["stopCondition"], "stop_and_close_all")
        self.assertFalse(u["stopOnOutOfGrid"])
        self.assertTrue(u["pumpProtection"])
        self.assertEqual(len(u), 26)  # exact key count = backward compat


class TestTakeProfit(unittest.TestCase):
    def test_take_profit_usd_adds_takeprofit_and_compare_type(self):
        u = upsert(take_profit_usd=100)
        self.assertEqual(u["takeProfit"], 100.0)
        self.assertEqual(u["stopLossPnlCompareType"], "total")
        self.assertNotIn("stopLoss", u)

    def test_take_profit_rounds_to_cents(self):
        u = upsert(take_profit_usd=123.456)
        self.assertEqual(u["takeProfit"], 123.46)


class TestStopLoss(unittest.TestCase):
    def test_stop_loss_only_when_provided(self):
        u = upsert()
        self.assertNotIn("stopLoss", u)
        u = upsert(stop_loss_usd=50)
        self.assertEqual(u["stopLoss"], 50.0)
        self.assertEqual(u["stopLossPnlCompareType"], "total")
        self.assertNotIn("takeProfit", u)  # SL alone adds no TP

    def test_tp_and_sl_together(self):
        u = upsert(take_profit_usd=100, stop_loss_usd=50)
        self.assertEqual(u["takeProfit"], 100.0)
        self.assertEqual(u["stopLoss"], 50.0)
        self.assertEqual(u["stopLossPnlCompareType"], "total")


class TestCumulativeTrailing(unittest.TestCase):
    def test_both_args_add_trailing_keys(self):
        u = upsert(trailing_activation_pct=5.0, trailing_execute_pct=2.0)
        self.assertEqual(u["trailingStopActivation"], 5.0)
        self.assertEqual(u["trailingStopExecute"], 2.0)
        self.assertEqual(u["trailingStopPnlCompareType"], "total")
        for k in ("takeProfit", "stopLoss", "stopLossPnlCompareType"):
            self.assertNotIn(k, u)

    def test_partial_args_add_nothing(self):
        u = upsert(trailing_activation_pct=5.0)
        self.assertNotIn("trailingStopActivation", u)
        self.assertNotIn("trailingStopExecute", u)
        self.assertNotIn("trailingStopPnlCompareType", u)
        u = upsert(trailing_execute_pct=2.0)
        self.assertNotIn("trailingStopExecute", u)


class TestPositionsExits(unittest.TestCase):
    def test_positions_trailing_flag(self):
        u = upsert(positions_trailing=True)
        self.assertEqual(u["strategyProfitCondition"], "trailing_stop")
        u = upsert()  # default False → absent
        self.assertNotIn("strategyProfitCondition", u)

    def test_positions_stop_loss_ratio(self):
        u = upsert(positions_stop_loss_ratio=0.05)
        self.assertEqual(u["strategyStopLossFixedPercentRatio"], 0.05)
        u = upsert()
        self.assertNotIn("strategyStopLossFixedPercentRatio", u)


class TestAllExitsTogether(unittest.TestCase):
    def test_full_exit_stack(self):
        u = upsert(take_profit_usd=100, stop_loss_usd=50,
                   trailing_activation_pct=5.0, trailing_execute_pct=2.0,
                   positions_trailing=True, positions_stop_loss_ratio=0.05)
        self.assertEqual(u["takeProfit"], 100.0)
        self.assertEqual(u["stopLoss"], 50.0)
        self.assertEqual(u["stopLossPnlCompareType"], "total")
        self.assertEqual(u["trailingStopActivation"], 5.0)
        self.assertEqual(u["trailingStopExecute"], 2.0)
        self.assertEqual(u["trailingStopPnlCompareType"], "total")
        self.assertEqual(u["strategyProfitCondition"], "trailing_stop")
        self.assertEqual(u["strategyStopLossFixedPercentRatio"], 0.05)


class TestBuildTicketPayloadsThreading(unittest.TestCase):
    """build_ticket_payloads must thread the exit kwargs into compute_upsert."""

    def test_kwargs_thread_through(self):
        ticket = {"symbol": "PUMP", "venue": "hyperliquid",
                  "grid_type": "neutral", "regime": "neutral",
                  "step_mult": 1.0, "max_alloc_mult": 1.0}
        brief = {"metrics": {"price": 100.0, "atr_pct": 2.0},
                 "spread_pct": 0.02}
        captured = {}

        def fake_compute_upsert(*a, **kw):
            captured.update(kw)
            return compute_upsert("PUMP", "hyperliquid", 100.0, 2.0,
                                  1.0, 10, 20.0, "neutral", "prof1",
                                  "123", **kw)

        # stub the heavy paths: grid math + candle history
        with mock.patch("grid_adapter.grid_config") as gc, \
                mock.patch("stagnation.derive_policy", return_value={}):
            gc.build_grid.return_value = {
                "profit_per_grid_pct": 1.0, "grids": 10,
                "channel": {"width_pct": 6.0}, "sizing": {
                    "amount_per_trade": 20.0, "units": "usd",
                    "usd_per_grid": 20.0, "side_lines": 5,
                    "distributed_notional": 200.0,
                    "total_commitment_estimate": 100.0},
                "fee_floor_pct": 0.19}
            gc.build_mcp.return_value = {}
            with mock.patch.object(grid_adapter, "compute_upsert",
                                   side_effect=fake_compute_upsert):
                p = build_ticket_payloads(
                    ticket, brief, 125.0, 0.5, "prof1", "123",
                    take_profit_usd=100, trailing_activation_pct=5.0,
                    trailing_execute_pct=2.0, positions_trailing=True,
                    positions_stop_loss_ratio=0.05)
        # the kwargs reached compute_upsert intact
        self.assertEqual(captured["take_profit_usd"], 100)
        self.assertIsNone(captured["stop_loss_usd"])
        self.assertEqual(captured["trailing_activation_pct"], 5.0)
        self.assertEqual(captured["trailing_execute_pct"], 2.0)
        self.assertTrue(captured["positions_trailing"])
        self.assertEqual(captured["positions_stop_loss_ratio"], 0.05)
        # ...and compute_upsert translated them into the payload that
        # build_ticket_payloads stored under "upsert"
        u = p["upsert"]
        self.assertEqual(u["takeProfit"], 100.0)
        self.assertEqual(u["trailingStopActivation"], 5.0)
        self.assertEqual(u["trailingStopExecute"], 2.0)
        self.assertEqual(u["trailingStopPnlCompareType"], "total")
        self.assertEqual(u["strategyProfitCondition"], "trailing_stop")
        self.assertEqual(u["strategyStopLossFixedPercentRatio"], 0.05)
        self.assertNotIn("stopLoss", u)

    def test_default_call_adds_no_exit_keys(self):
        ticket = {"symbol": "PUMP", "venue": "hyperliquid",
                  "grid_type": "neutral", "regime": "neutral",
                  "step_mult": 1.0, "max_alloc_mult": 1.0}
        brief = {"metrics": {"price": 100.0, "atr_pct": 2.0},
                 "spread_pct": 0.02}
        captured = {}

        def fake_compute_upsert(*a, **kw):
            captured.update(kw)
            return compute_upsert("PUMP", "hyperliquid", 100.0, 2.0,
                                  1.0, 10, 20.0, "neutral", "prof1",
                                  "123", **kw)

        with mock.patch("grid_adapter.grid_config") as gc, \
                mock.patch("stagnation.derive_policy", return_value={}):
            gc.build_grid.return_value = {
                "profit_per_grid_pct": 1.0, "grids": 10,
                "channel": {"width_pct": 6.0}, "sizing": {
                    "amount_per_trade": 20.0, "units": "usd",
                    "usd_per_grid": 20.0, "side_lines": 5,
                    "distributed_notional": 200.0,
                    "total_commitment_estimate": 100.0},
                "fee_floor_pct": 0.19}
            gc.build_mcp.return_value = {}
            with mock.patch.object(grid_adapter, "compute_upsert",
                                   side_effect=fake_compute_upsert):
                p = build_ticket_payloads(
                    ticket, brief, 125.0, 0.5, "prof1", "123")
        # defaults thread through as None/False → no exit keys anywhere
        self.assertIsNone(captured.get("take_profit_usd"))
        self.assertIsNone(captured.get("stop_loss_usd"))
        self.assertFalse(captured.get("positions_trailing"))
        for k in EXIT_KEYS:
            self.assertNotIn(k, p["upsert"])


if __name__ == "__main__":
    unittest.main()
