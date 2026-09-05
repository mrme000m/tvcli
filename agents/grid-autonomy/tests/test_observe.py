"""Unit tests for execution/observe.py (offline over captured fixtures)."""
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "execution"))

import observe  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
GRID_RESOURCE = json.load(open(os.path.join(FIX, "grid_resource.json")))
POSITIONS_LIVE = json.load(open(os.path.join(FIX, "positions_live.json")))
POSITIONS_HISTORY = json.load(open(os.path.join(FIX, "positions_history.json")))

GRID_RAW = {"_embedded": {"items": [{"resource": GRID_RESOURCE}]}}
NOW = datetime(2026, 9, 4, 14, 0, 0, tzinfo=timezone.utc).timestamp()

BOT = {
    "bot_code": "c629f5ba3a643a82fc53dd4e",
    "venue": "hyperliquid",
    "symbol": "HYPE",
    "channel": {"low": 83.85, "mid": 86.935, "high": 90.02,
                "step_pct": 0.59, "atr_pct": 2.0, "grids": 13},
    "stagnation_policy": {"expected_fills_per_24h": 6.0,
                          "regime": "neutral"},
}


def _router(path):
    if path.startswith("/en/trader/grid_bots/grid?"):
        return GRID_RAW
    if "/positions/grid?" in path:
        return POSITIONS_LIVE
    if "/positions-history/grid?" in path:
        return POSITIONS_HISTORY
    return None


class TestStatus(unittest.TestCase):
    def test_grid_status_shape(self):
        with mock.patch.object(observe, "_api_json", side_effect=_router):
            st = observe.grid_status()
        self.assertEqual(len(st), 1)
        b = st[0]
        self.assertEqual(b["code"], "c629f5ba3a643a82fc53dd4e")
        self.assertEqual(b["status"], "active")
        self.assertTrue(b["paperTrading"])
        self.assertEqual(b["exchange"], "HYPERLIQUID_SWAP")
        self.assertEqual(b["pair"], "HYPE-USDC")
        self.assertEqual(b["pairCode"], "159")

    def test_grid_status_failure_returns_empty(self):
        with mock.patch.object(observe, "_api_json", return_value=None):
            self.assertEqual(observe.grid_status(), [])


class TestProfiles(unittest.TestCase):
    def test_profiles_balance_and_no_secret_leak(self):
        boot = {
            "data": {
                "exchangesProfiles": {
                    "HYPERLIQUID_SWAP": {
                        "demo": {
                            "name_of_account": "demo-hype",
                            "paperTrading": True,
                            "balance": {
                                "notionalBalances": {"total": {"USD": 10000}},
                                "ccxt": {"apiKey": "TOP-SECRET-KEY"},
                            },
                        }
                    }
                }
            }
        }
        with mock.patch.object(observe, "_api_json", return_value=boot):
            profiles = observe.grid_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["code"], "demo")
        self.assertEqual(profiles[0]["exchange"], "HYPERLIQUID_SWAP")
        self.assertTrue(profiles[0]["paperTrading"])
        self.assertEqual(profiles[0]["balance"], 10000.0)
        dumped = json.dumps(profiles)
        self.assertNotIn("TOP-SECRET-KEY", dumped)
        self.assertNotIn("ccxt", dumped)


class TestObserveAll(unittest.TestCase):
    def test_observe_all_offline(self):
        with mock.patch.object(observe, "_api_json", side_effect=_router), \
             mock.patch.object(observe.time, "time", return_value=NOW):
            out = observe.observe_all({"0": BOT})
        obs = out["0"]
        self.assertEqual(obs["status"], "active")
        self.assertAlmostEqual(obs["price"], 86.8475, places=4)
        self.assertEqual(obs["fills_24h"], 3)
        self.assertAlmostEqual(obs["realized_ratio"], 0.5, places=4)
        # authoritative mark PnL from the grid resource (fixture pnlFiat -6.12);
        # the open-position totalProfitLoss sum mis-scales ~10x (-0.5837)
        self.assertAlmostEqual(obs["unrealized_pnl"], -6.12, places=4)
        self.assertFalse(obs["ladder_full"])
        expected_dd = round((86.935 - 86.8475) / 86.935 * 100.0 / 2.0, 4)
        self.assertAlmostEqual(obs["dd_vs_atr_band"], expected_dd, places=4)
        self.assertNotIn("error", obs)

    def test_no_bot_code(self):
        out = observe.observe_all({"1": {"venue": "binance"}})
        self.assertEqual(out["1"]["status"], "unknown")
        self.assertEqual(out["1"]["error"], "no bot_code")
        self.assertEqual(out["1"]["fills_24h"], 0)

    def test_ladder_full_threshold(self):
        full = dict(BOT, channel=dict(BOT["channel"], grids=2))
        with mock.patch.object(observe, "_api_json", side_effect=_router), \
             mock.patch.object(observe.time, "time", return_value=NOW):
            out = observe.observe_all({"0": full})
        self.assertTrue(out["0"]["ladder_full"])


class TestTimestamps(unittest.TestCase):
    def test_iso_z(self):
        ts = observe._ts_epoch("2026-09-04T08:56:39.764Z")
        self.assertIsNotNone(ts)

    def test_epoch_ms(self):
        self.assertEqual(observe._ts_epoch(1788512584213), 1788512584.213)


if __name__ == "__main__":
    unittest.main()
