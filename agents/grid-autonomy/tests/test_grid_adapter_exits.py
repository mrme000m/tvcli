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


class TestTierWorstCaseSizing(unittest.TestCase):
    """Regression (live bug 2026-09-08): the tier budget is a TARGET
    WORST-CASE fraction of the slot and must be divided over the SIDE
    lines only (the lines that can fill adversely). The old alloc/grids_n
    divisor spread the one-sided budget over ALL lines, so every HL bot
    committed only $60-70 worst-case at full tier ($180 slot) instead of
    the designed $90 (50% cap) — the reliability ladder was symbolically
    thin and idle capital never went into fatter positions.
    """

    # a 12-line channel: ±6% band (3 ATR × atr 2.0) at a 1.05% step →
    # 12 lines, 6 side lines — the live neutral-grid shape
    TICKET = {"symbol": "CHIP", "venue": "hyperliquid",
              "grid_type": "neutral", "regime": "chop_high_volatility",
              "step_mult": 1.05, "max_alloc_mult": 1.0}
    BRIEF = {"metrics": {"price": 100.0, "atr_pct": 2.0, "adx14": 20.0,
                         "rsi14": 50.0, "bb_width_pctile": 50.0},
             "evidence": {}, "spread_pct": 0.01}

    def _build(self, slot=180.0, max_alloc=0.5, alloc_mult=1.0,
               min_cost=10.0, step_mult=1.05, **kw):
        return build_ticket_payloads(
            dict(self.TICKET, max_alloc_mult=alloc_mult,
                 step_mult=step_mult), self.BRIEF,
            slot, max_alloc, "prof", "5", min_cost=min_cost, **kw)

    def test_full_tier_hits_designed_50pct_worst_case(self):
        # full tier (0.5) × neutral risk (mult 1.0) on a $180 slot:
        # tier budget $90 over 6 side lines → $15/line, worst = $90
        p = self._build()
        g, s = p["grid_bot"], p["grid_bot"]["sizing"]
        self.assertEqual(g["grids"], 12)            # 11-12-line channel
        self.assertEqual(s["side_lines"], 6)
        self.assertAlmostEqual(s["usd_per_grid"], 15.0, places=2)
        self.assertAlmostEqual(s["total_commitment_estimate"], 90.0,
                               delta=0.05)          # the designed 50% cap
        self.assertGreater(s["total_commitment_estimate"], 80.0)  # not
        # the old min_cost-floored ~$70 half-target
        # distributed notional covers BOTH sides (all lines)
        self.assertAlmostEqual(s["distributed_notional"], 15.0 * 12,
                               delta=0.5)
        # guard bound is the honest worst fraction, never below the tier
        self.assertGreaterEqual(p["guard_ctx"]["max_alloc"], 0.5 - 1e-9)
        self.assertLessEqual(p["guard_ctx"]["max_alloc"], 0.5 + 1e-3)

    def test_full_tier_with_risk_mult_07(self):
        # risk-team mult 0.7: target worst-case 0.5 × 0.7 × 180 = $63
        p = self._build(alloc_mult=0.7)
        s = p["grid_bot"]["sizing"]
        self.assertAlmostEqual(s["usd_per_grid"], 63.0 / 6, places=2)
        self.assertAlmostEqual(s["total_commitment_estimate"], 63.0,
                               delta=0.05)

    def test_base_tier_floor_bound(self):
        # base tier 0.25 × mult 0.7 → $31.5 over 6 side lines = $5.25/line
        # < the $10 exchange floor → per-line floored, worst = floor-bound
        p = self._build(max_alloc=0.25, alloc_mult=0.7)
        s = p["grid_bot"]["sizing"]
        self.assertEqual(s["usd_per_grid"], 10.0)  # min_cost floor
        self.assertAlmostEqual(s["total_commitment_estimate"],
                               10.0 * s["side_lines"], delta=0.05)
        # guard bound covers the honest floor-driven worst fraction
        self.assertGreaterEqual(p["guard_ctx"]["max_alloc"],
                                s["total_commitment_estimate"] / 180.0
                                - 1e-9)

    def test_dense_channel_floor_breaks_cap_honestly(self):
        # dense channel (step_mult 0.7 → 18 lines, side_lines 9 × $10
        # = $90 worst-case > the 50% cap on a $100 slot) — the daemon's
        # size-fit/guard-veto path must see the honest worst case and the
        # guard bound at the real fraction (0.9), never the fictitious
        # tier×risk number
        p = self._build(slot=100.0, step_mult=0.7)
        s = p["grid_bot"]["sizing"]
        self.assertGreaterEqual(p["grid_bot"]["grids"], 16)
        self.assertEqual(s["usd_per_grid"], 10.0)   # floor-bound
        self.assertGreater(s["total_commitment_estimate"], 50.0)  # > cap
        self.assertAlmostEqual(p["guard_ctx"]["max_alloc"],
                               s["total_commitment_estimate"] / 100.0,
                               places=3)


if __name__ == "__main__":
    unittest.main()
