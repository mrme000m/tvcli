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
        # carry_after_h is the parallel `k_carry=1.5 × avg_holding_h`,
        # also clamped to [0.5, 168] hours so fast tokens don't get
        # abandoned at 10 min and slow tokens don't tie up the slot
        # for a week
        self.assertGreaterEqual(p["carry_after_h"], 0.5)
        self.assertLessEqual(p["carry_after_h"], 168.0)
        self.assertEqual(p["carry_after_k"], 1.5)
        # carries uses the SAME holding math as cooldown → both share
        # the regime multiplier, so a trend token's carry is
        # proportionally longer than a chop token's. Round to 1dp to
        # tolerate derive_policy's internal rounding.
        self.assertAlmostEqual(
            p["carry_after_h"],
            min(168.0, max(0.5, 1.5 * p["avg_holding_h"])),
            delta=0.01)

    def test_derive_carry_clamped_low_holding(self):
        """Fast-oscillation tokens (avg_holding_h close to 0) hit the
        CARRY_MIN_H=0.5 floor — never carry a fresh bot within 30 min."""
        fast = sine_closes(n=720, periods=240)  # ~3h holding
        p = derive_policy(fast, "1h", 0.5, "chop_high_volatility")
        self.assertGreaterEqual(p["carry_after_h"], 0.5)

    def test_derive_carry_clamped_high_holding(self):
        """Trend tokens with very long holding times hit the CARRY_MAX_H
        cap so the slot isn't tied up for a week while a bot drowns."""
        flat = flat_closes(n=720)
        p = derive_policy(flat, "1h", 0.5, "trend_up")
        # flat input → fallback holding blows past the cap
        self.assertLessEqual(p["carry_after_h"], 168.0)

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
