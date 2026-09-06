#!/usr/bin/env python3
"""Watchdog beat-17 change: observe._closed_round_trips counts panic_exited.

WT closes stop_and_close_all leftovers as "panic_exited" with real
profitLoss; the old `!= "completed"` filter dropped them from
realized_pnl (fleet realized overstated ~$0.72, orchestrator audit
2026-09-06). Verified live vocabulary: {completed, panic_exited}.

Ledger-truth extension (audit-20260906): _observe_one additionally reports
the split — trips_completed / trips_panic and realized_pnl_completed /
realized_pnl_panic — while realized_pnl stays the TOTAL.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from execution import observe  # noqa: E402


class ClosedRoundTripsTest(unittest.TestCase):
    def test_counts_completed_and_panic_exited(self):
        hist = [
            {"status": "completed", "profitLoss": 1927,
             "exitedAt": "2026-09-05T18:00:00Z"},
            {"status": "panic_exited", "profitLoss": -2385,
             "exitedAt": "2026-09-05T17:00:00Z"},
            {"status": "entered", "profitLoss": 5,
             "updatedAt": "2026-09-05T16:00:00Z"},
            {"status": "canceled", "profitLoss": 9,
             "updatedAt": "2026-09-05T15:00:00Z"},
        ]
        trips = observe._closed_round_trips(hist)
        self.assertEqual({t["res"]["status"] for t in trips},
                         {"completed", "panic_exited"})
        # newest first
        self.assertEqual(trips[0]["res"]["status"], "completed")

    def test_empty_and_malformed(self):
        self.assertEqual(observe._closed_round_trips([]), [])
        self.assertEqual(observe._closed_round_trips([None, 5, {}]), [])


def _observe_with_history(bot, history):
    """_observe_one over a fixed closed-trip history (no network)."""
    with mock.patch.object(observe, "_positions_open", return_value=[]), \
         mock.patch.object(observe, "_positions_history",
                           return_value=history):
        return observe._observe_one(bot, {})


class ObserveSplitFieldsTest(unittest.TestCase):
    """trips_completed / trips_panic / realized_pnl_completed /
    realized_pnl_panic split the closed trips by close status; realized_pnl
    stays the total (audit-20260906 WT ground truth per bot)."""

    BOT = {"bot_code": "c629f5ba3a643a8264b4a3e6", "venue": "hyperliquid",
           "symbol": "FARTCOIN"}

    def test_mixed_completed_and_panic(self):
        # FARTCOIN's real final history: 1 completed (+0.0941) and 4
        # panic_exited (−0.1213/−0.2236/−0.3387/−0.4333) = −1.0228 total
        hist = [
            {"status": "completed", "profitLoss": 941,
             "exitedAt": "2026-09-05T17:28:16+03:00"},
            {"status": "panic_exited", "profitLoss": -1213,
             "exitedAt": "2026-09-05T19:58:28+03:00"},
            {"status": "panic_exited", "profitLoss": -2236,
             "exitedAt": "2026-09-05T19:58:27+03:00"},
            {"status": "panic_exited", "profitLoss": -3387,
             "exitedAt": "2026-09-05T19:58:28+03:00"},
            {"status": "panic_exited", "profitLoss": -4333,
             "exitedAt": "2026-09-05T19:58:28+03:00"},
        ]
        obs = _observe_with_history(self.BOT, hist)
        self.assertEqual(obs["trips_completed"], 1)
        self.assertEqual(obs["trips_panic"], 4)
        self.assertAlmostEqual(obs["realized_pnl_completed"], 0.0941, places=6)
        self.assertAlmostEqual(obs["realized_pnl_panic"], -1.1169, places=6)
        self.assertAlmostEqual(obs["realized_pnl"], -1.0228, places=6)

    def test_positive_panic_still_counts(self):
        # ARB-HL: 4 completed (+0.4822) + 1 panic (+0.2385) = +0.7207
        hist = [
            {"status": "completed", "profitLoss": 1247,
             "exitedAt": "2026-09-05T14:39:33+03:00"},
            {"status": "completed", "profitLoss": 868,
             "exitedAt": "2026-09-05T16:32:02+03:00"},
            {"status": "completed", "profitLoss": 1435,
             "exitedAt": "2026-09-05T17:49:29+03:00"},
            {"status": "completed", "profitLoss": 1272,
             "exitedAt": "2026-09-05T17:55:06+03:00"},
            {"status": "panic_exited", "profitLoss": 2385,
             "exitedAt": "2026-09-05T18:21:45+03:00"},
        ]
        obs = _observe_with_history(self.BOT, hist)
        self.assertEqual((obs["trips_completed"], obs["trips_panic"]), (4, 1))
        self.assertAlmostEqual(obs["realized_pnl_completed"], 0.4822,
                              places=6)
        self.assertAlmostEqual(obs["realized_pnl_panic"], 0.2385, places=6)
        self.assertAlmostEqual(obs["realized_pnl"], 0.7207, places=6)

    def test_all_completed(self):
        hist = [{"status": "completed", "profitLoss": 3546,
                 "exitedAt": "2026-09-05T16:54:02+03:00"}]
        obs = _observe_with_history(self.BOT, hist)
        self.assertEqual(obs["trips_completed"], 1)
        self.assertEqual(obs["trips_panic"], 0)
        self.assertAlmostEqual(obs["realized_pnl_completed"], 0.3546,
                              places=6)
        self.assertAlmostEqual(obs["realized_pnl_panic"], 0.0, places=6)
        self.assertAlmostEqual(obs["realized_pnl"], 0.3546, places=6)

    def test_empty_history_zero_defaults(self):
        obs = _observe_with_history(self.BOT, [])
        self.assertEqual(obs["trips_completed"], 0)
        self.assertEqual(obs["trips_panic"], 0)
        self.assertEqual(obs["realized_pnl_completed"], 0.0)
        self.assertEqual(obs["realized_pnl_panic"], 0.0)
        self.assertEqual(obs["realized_pnl"], 0.0)

    def test_absent_profitloss_fails_soft_to_zero(self):
        hist = [{"status": "completed", "exitedAt": "2026-09-05T17:00:00Z"},
                {"status": "panic_exited", "exitedAt":
                 "2026-09-05T18:00:00Z"}]
        obs = _observe_with_history(self.BOT, hist)
        self.assertEqual(obs["trips_completed"], 1)
        self.assertEqual(obs["trips_panic"], 1)
        self.assertEqual(obs["realized_pnl_completed"], 0.0)
        self.assertEqual(obs["realized_pnl_panic"], 0.0)
        self.assertEqual(obs["realized_pnl"], 0.0)

    def test_no_bot_code_defaults(self):
        obs = observe.observe_all({"1": {"venue": "binance"}})
        self.assertEqual(obs["1"]["trips_completed"], 0)
        self.assertEqual(obs["1"]["trips_panic"], 0)
        self.assertEqual(obs["1"]["realized_pnl_completed"], 0.0)
        self.assertEqual(obs["1"]["realized_pnl_panic"], 0.0)
        self.assertEqual(obs["1"]["realized_pnl"], 0.0)


if __name__ == "__main__":
    unittest.main()
