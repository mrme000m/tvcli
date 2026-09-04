"""Unit tests for daemon.should_rotate + watch/spec.build_spec."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "watch"))

from daemon import should_rotate
from spec import build_spec

POLICY = {"regime": "chop_high_volatility", "hysteresis_score": 5.0,
          "score_drop_rotate": 12.0,
          "stagnant_if": {"min_fills_24h": 3.0, "min_realized_ratio": 0.4,
                          "window_h": 48}}
INC = {"venue": "hyperliquid", "symbol": "PUMP", "score_final": 100.0}


class TestRotate(unittest.TestCase):
    def test_healthy_no_rotate(self):
        ok, reasons = should_rotate(
            {"regime": "chop_high_volatility", "score_final": 120.0},
            INC, POLICY,
            {"fills_24h": 12.0, "realized_ratio": 0.9}, 0)
        self.assertFalse(ok)

    def test_stagnant_plus_better_rotates(self):
        ok, reasons = should_rotate(
            {"regime": "chop_high_volatility", "score_final": 110.0},
            INC, POLICY,
            {"fills_24h": 0.0, "realized_ratio": 0.0}, 0)
        self.assertTrue(ok)

    def test_stagnant_but_weak_challenger(self):
        ok, reasons = should_rotate(
            {"regime": "chop_high_volatility", "score_final": 102.0},
            INC, POLICY,
            {"fills_24h": 0.0, "realized_ratio": 0.0}, 0)
        self.assertFalse(ok)
        self.assertTrue(any("hysteresis" in r for r in reasons))

    def test_needs_reanalysis_rotates_without_stagnation(self):
        # healthy fills + weak challenger (Δscore < hysteresis), but the
        # incumbent went out-of-channel / was stopped → hard rotation
        ok, reasons = should_rotate(
            {"regime": "chop_high_volatility", "score_final": 90.0},
            INC, POLICY,
            {"fills_24h": 12.0, "realized_ratio": 0.9}, 0,
            needs_reanalysis=True)
        self.assertTrue(ok)
        self.assertTrue(any("needs_reanalysis" in r for r in reasons))

    def test_no_flag_keeps_hysteresis_for_healthy_incumbent(self):
        ok, _ = should_rotate(
            {"regime": "chop_high_volatility", "score_final": 102.0},
            INC, POLICY,
            {"fills_24h": 12.0, "realized_ratio": 0.9}, 0,
            needs_reanalysis=False)
        self.assertFalse(ok)


class TestSpec(unittest.TestCase):
    def test_levels(self):
        s = build_spec("PUMP", "BINANCE:PUMPUSDT", 3.5, 1.087, 12, 1)
        self.assertEqual(s["id"], "pump-grid-s1")
        self.assertEqual(len(s["triggers"]), 5)
        lv = {t["id"]: t["level"] for t in s["triggers"] if "level" in t}
        self.assertGreater(lv["tp-hit"], 3.5)
        self.assertLess(lv["dca-full"], lv["dca3"])
        self.assertLess(lv["dca3"], lv["dca1"])


if __name__ == "__main__":
    unittest.main()
