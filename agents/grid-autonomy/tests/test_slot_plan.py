#!/usr/bin/env python3
"""slot_plan() venue math — the 3/4/5-slot proportional sleeve examples.

The 500 USD fund is 300 HL perps + 200 Binance spot; slot counts follow the
sleeve share (largest-remainder, min 1 per funded venue), then each venue's
sleeve balance is split equally across its slots.
"""
import unittest

from policy.stagnation import slot_plan


def _venues(plan):
    return [s["venue"] for s in plan["slots"]]


def _balances(plan):
    return [s["balance"] for s in plan["slots"]]


def _venue_totals(plan):
    out = {}
    for s in plan["slots"]:
        out[s["venue"]] = out.get(s["venue"], 0.0) + s["balance"]
    return out


class TestSlotPlanVenueMath(unittest.TestCase):
    def test_three_slots(self):
        plan = slot_plan(500.0, n_slots=3)
        self.assertEqual(_venues(plan),
                         ["hyperliquid", "hyperliquid", "binance"])
        self.assertEqual(_balances(plan), [150.0, 150.0, 200.0])
        self.assertEqual(_venue_totals(plan),
                         {"hyperliquid": 300.0, "binance": 200.0})

    def test_four_slots(self):
        plan = slot_plan(500.0, n_slots=4)
        self.assertEqual(_venues(plan),
                         ["hyperliquid", "hyperliquid", "binance", "binance"])
        self.assertEqual(_balances(plan), [150.0, 150.0, 100.0, 100.0])
        self.assertEqual(_venue_totals(plan),
                         {"hyperliquid": 300.0, "binance": 200.0})

    def test_five_slots(self):
        plan = slot_plan(500.0, n_slots=5)
        self.assertEqual(_venues(plan),
                         ["hyperliquid", "hyperliquid", "hyperliquid",
                          "binance", "binance"])
        self.assertEqual(_balances(plan), [100.0, 100.0, 100.0, 100.0, 100.0])
        self.assertEqual(_venue_totals(plan),
                         {"hyperliquid": 300.0, "binance": 200.0})

    def test_every_funded_venue_gets_a_slot(self):
        for n in (3, 4, 5):
            plan = slot_plan(500.0, n_slots=n)
            venues = _venues(plan)
            self.assertIn("hyperliquid", venues)
            self.assertIn("binance", venues)

    def test_slot_meta(self):
        plan = slot_plan(500.0, n_slots=4)
        for s in plan["slots"]:
            self.assertEqual(s["max_commitment"], round(s["balance"] * 0.5, 2))
            self.assertEqual(s["venue_sleeve"],
                             300.0 if s["venue"] == "hyperliquid" else 200.0)
            self.assertGreaterEqual(s["venue_slots"], 1)

    def test_deployable_ceiling(self):
        plan = slot_plan(500.0, n_slots=4)
        self.assertEqual(plan["deployable_ceiling"], 425.0)

    def test_custom_venue_balances_scale(self):
        # 600 total split 2:1 should keep the proportional sleeve share.
        plan = slot_plan(600.0,
                         venue_balances={"hyperliquid": 400.0, "binance": 200.0},
                         n_slots=3)
        self.assertEqual(_venue_totals(plan),
                         {"hyperliquid": 400.0, "binance": 200.0})


if __name__ == "__main__":
    unittest.main()
