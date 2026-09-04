"""Unit tests for policy/stagnation.py — synthetic fixtures, no network."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "policy"))

from stagnation import (simulate_grid_fills, avg_holding_h, derive_policy,
                        is_stagnant, slot_plan)


def sine_closes(n=720, periods=12, base=100.0, amp=2.0):
    """Oscillating market: ~`periods` full cycles over n bars."""
    return [base + amp * math.sin(2 * math.pi * i * periods / n) for i in range(n)]


def flat_closes(n=720, base=100.0):
    return [base] * n


def trend_closes(n=720, base=100.0, drift=0.02):
    return [base + drift * i for i in range(n)]


class TestSimulate(unittest.TestCase):
    def test_oscillating_market_fills(self):
        fills, mids = simulate_grid_fills(sine_closes(), 0.5)
        self.assertGreater(fills, 20)
        self.assertGreaterEqual(mids, 8)  # mid sits off-center (endpoint phase)

    def test_flat_market_no_fills(self):
        fills, mids = simulate_grid_fills(flat_closes(), 0.5)
        self.assertEqual(fills, 0)
        self.assertEqual(mids, 0)

    def test_trend_few_fills(self):
        fills, _ = simulate_grid_fills(trend_closes(), 0.5)
        self.assertLess(fills, 30)

    def test_empty_and_bad_step(self):
        self.assertEqual(simulate_grid_fills([], 0.5), (0, 0))
        self.assertEqual(simulate_grid_fills(sine_closes(), 0), (0, 0))


class TestPolicy(unittest.TestCase):
    def test_derive_chop(self):
        p = derive_policy(sine_closes(), "1h", 0.5, "chop_high_volatility")
        self.assertGreater(p["expected_fills_per_24h"], 0)
        self.assertGreater(p["avg_holding_h"], 0)
        # k=1.5 × ~30h holding ≈ 45h, inside clamp
        self.assertGreaterEqual(p["cooldown_h"], 12.0)
        self.assertLessEqual(p["cooldown_h"], 72.0)
        self.assertEqual(p["cooldown_k"], 1.5)

    def test_derive_squeeze_cooldown_longer(self):
        # fast oscillation (holding ~8h) keeps both regimes off the 72h clamp
        fast = sine_closes(n=720, periods=48)
        pc = derive_policy(fast, "1h", 0.5, "chop_high_volatility")
        ps = derive_policy(fast, "1h", 0.5, "squeeze")
        self.assertLess(pc["cooldown_h"], 72.0)
        self.assertGreater(ps["cooldown_h"], pc["cooldown_h"])

    def test_derive_flat_clamped(self):
        p = derive_policy(flat_closes(), "1h", 0.5, "neutral")
        self.assertEqual(p["expected_fills_per_24h"], 0)
        self.assertEqual(p["cooldown_h"], 72.0)  # fallback holding=720h×2 → clamp

    def test_stagnant_low_activity(self):
        p = derive_policy(sine_closes(), "1h", 0.5, "chop_high_volatility")
        stag, reasons = is_stagnant({"fills_24h": 0, "realized_ratio": 0.0}, p)
        self.assertTrue(stag)
        self.assertTrue(any("fills" in r for r in reasons))

    def test_healthy_not_stagnant(self):
        p = derive_policy(sine_closes(), "1h", 0.5, "chop_high_volatility")
        exp = p["expected_fills_per_24h"]
        stag, _ = is_stagnant({"fills_24h": exp, "realized_ratio": 0.9}, p,
                              regime_now="chop_high_volatility")
        self.assertFalse(stag)

    def test_regime_switch_rotates(self):
        p = derive_policy(sine_closes(), "1h", 0.5, "chop_high_volatility")
        stag, reasons = is_stagnant(
            {"fills_24h": 999, "realized_ratio": 1.0}, p,
            regime_now="trend_up", score_drop=15.0)
        self.assertTrue(stag)
        self.assertTrue(any("regime" in r for r in reasons))

    def test_small_score_drop_no_rotate(self):
        p = derive_policy(sine_closes(), "1h", 0.5, "chop_high_volatility")
        stag, _ = is_stagnant(
            {"fills_24h": 999, "realized_ratio": 1.0}, p,
            regime_now="trend_up", score_drop=3.0)
        self.assertFalse(stag)

    def test_full_ladder_dd(self):
        p = derive_policy(sine_closes(), "1h", 0.5, "chop_high_volatility")
        stag, reasons = is_stagnant(
            {"fills_24h": 999, "realized_ratio": 1.0}, p,
            ladder_full=True, dd_vs_atr_band=2.0)
        self.assertTrue(stag)


class TestSlots(unittest.TestCase):
    def test_four_slots_proportional(self):
        plan = slot_plan(500.0, n_slots=4)
        self.assertEqual(len(plan["slots"]), 4)
        self.assertEqual(plan["deployable_ceiling"], 425.0)
        venues = [s["venue"] for s in plan["slots"]]
        self.assertEqual(venues, ["hyperliquid", "hyperliquid",
                                  "binance", "binance"])
        balances = [s["balance"] for s in plan["slots"]]
        self.assertEqual(balances, [150.0, 150.0, 100.0, 100.0])
        for s in plan["slots"]:
            self.assertEqual(s["max_commitment"], round(s["balance"] * 0.5, 2))
            self.assertIn("venue_sleeve", s)

    def test_sleeve_sum_conserved(self):
        plan = slot_plan(500.0, n_slots=4)
        by_venue = {}
        for s in plan["slots"]:
            by_venue[s["venue"]] = by_venue.get(s["venue"], 0.0) + s["balance"]
        self.assertAlmostEqual(by_venue["hyperliquid"], 300.0)
        self.assertAlmostEqual(by_venue["binance"], 200.0)

    def test_bounds(self):
        with self.assertRaises(ValueError):
            slot_plan(500.0, n_slots=2)
        with self.assertRaises(ValueError):
            slot_plan(500.0, n_slots=6)
        for n in (3, 5):
            self.assertEqual(len(slot_plan(500.0, n_slots=n)["slots"]), n)


if __name__ == "__main__":
    unittest.main()
