"""Unit tests for execution/reliability_grid.py (offline math + ledger io)."""
import json
import os
import sys
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="grid-reliability-test-")
os.environ["GRID_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "execution"))

import reliability_grid as rg  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def trade(pnl, close):
    return {"pnl_usd": pnl, "close_ts": close, "entered_at": None,
            "strategy_id": "x"}


class TestParseTrades(unittest.TestCase):
    def test_parse_fixture(self):
        raw = json.load(open(os.path.join(FIX, "positions_history.json")))
        items = [it["resource"] for it in raw["_embedded"]["items"]]
        trades = rg.parse_trades(items)
        self.assertEqual(len(trades), 3)
        # profitLoss is USD x 1e4
        self.assertAlmostEqual(trades[0]["pnl_usd"], 19461 / 10000.0, places=6)
        self.assertAlmostEqual(trades[1]["pnl_usd"], 17321 / 10000.0, places=6)
        self.assertAlmostEqual(trades[2]["pnl_usd"], 18389 / 10000.0, places=6)
        # ascending by close time
        closes = [t["close_ts"] for t in trades]
        self.assertEqual(closes, sorted(closes))

    def test_skips_non_completed(self):
        items = [{"status": "entered", "profitLoss": 100, "updatedAt": "2026-09-04T00:00:00Z"},
                 {"status": "completed", "profitLoss": 100, "updatedAt": "2026-09-04T00:00:00Z"}]
        self.assertEqual(len(rg.parse_trades(items)), 1)


class TestArchetypeStats(unittest.TestCase):
    def test_math(self):
        trades = [trade(1.0, 1), trade(-0.5, 2), trade(2.0, 3), trade(1.5, 4)]
        stats = rg.archetype_stats({"trend": trades})["trend"]
        self.assertEqual(stats["samples"], 4)
        self.assertAlmostEqual(stats["profit_factor"], 4.5 / 0.5, places=4)
        self.assertAlmostEqual(stats["recent_pf"], 4.5 / 0.5, places=4)
        self.assertAlmostEqual(stats["win_rate"], 0.75, places=4)
        self.assertAlmostEqual(stats["expectancy_usd"], 1.0, places=4)
        self.assertAlmostEqual(stats["max_dd_usd"], 0.5, places=4)

    def test_no_losses_capped(self):
        stats = rg.archetype_stats({"x": [trade(1.0, 1), trade(2.0, 2)]})["x"]
        self.assertEqual(stats["profit_factor"], rg.PROFIT_FACTOR_CAP)
        self.assertEqual(stats["max_dd_usd"], 0.0)

    def test_no_trades(self):
        stats = rg.archetype_stats({"x": []})["x"]
        self.assertEqual(stats["samples"], 0)
        self.assertEqual(stats["profit_factor"], 0.0)
        self.assertEqual(stats["recent_pf"], 0.0)

    def test_flatten_bot_dict(self):
        grouped = {"trend": {"bot1": [trade(1.0, 1)], "bot2": [trade(2.0, 2)]}}
        stats = rg.archetype_stats(grouped)["trend"]
        self.assertEqual(stats["samples"], 2)


class TestLedgerIo(unittest.TestCase):
    def test_save_load_roundtrip(self):
        data = {"trend": {"samples": 4, "profit_factor": 9.0, "recent_pf": 9.0,
                          "win_rate": 0.75, "expectancy_usd": 1.0,
                          "max_dd_usd": 0.5}}
        self.assertTrue(rg.save(data))
        self.assertEqual(rg.load(), data)

    def test_load_missing(self):
        os.environ["GRID_STATE_DIR"] = os.path.join(_TMP, "missing-subdir")
        rg.STATE_DIR = os.environ["GRID_STATE_DIR"]
        rg.RELIABILITY_PATH = os.path.join(rg.STATE_DIR, "reliability.json")
        self.assertEqual(rg.load(), {})


if __name__ == "__main__":
    unittest.main()
