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

    def test_skips_non_closed(self):
        items = [{"status": "entered", "profitLoss": 100, "updatedAt": "2026-09-04T00:00:00Z"},
                 {"status": "completed", "profitLoss": 100, "updatedAt": "2026-09-04T00:00:00Z"}]
        self.assertEqual(len(rg.parse_trades(items)), 1)

    def test_counts_panic_exited_as_closed(self):
        # WT closes stop_and_close_all leftovers as "panic_exited" and
        # their profitLoss is REAL PnL — it must reach the ledger
        items = [{"status": "completed", "profitLoss": 10000, "exitedAt": "2026-09-04T00:00:00Z"},
                 {"status": "panic_exited", "profitLoss": -7229, "exitedAt": "2026-09-04T01:00:00Z"},
                 {"status": "entered", "profitLoss": 100, "updatedAt": "2026-09-04T00:00:00Z"}]
        trades = rg.parse_trades(items)
        self.assertEqual(len(trades), 2)
        self.assertAlmostEqual(trades[1]["pnl_usd"], -7229 / 10000.0, places=6)


class TestSyntheticFlagging(unittest.TestCase):
    """backfill-N seed rows are synthetic: flagged on read/parse, excluded
    from every archetype stat, counted separately as synthetic_samples."""

    def test_parse_trades_marks_backfill_ids(self):
        items = [
            {"status": "completed", "profitLoss": 1092, "updatedAt":
             "2026-09-04T08:00:00Z", "strategyId": "backfill-0"},
            {"status": "completed", "profitLoss": 19461, "updatedAt":
             "2026-09-05T09:00:00Z", "strategyId": "6a9c0ac6f891fd79a98926b3"},
        ]
        trades = rg.parse_trades(items)
        self.assertTrue(trades[0]["synthetic"])
        self.assertFalse(trades[1]["synthetic"])

    def test_stats_excludes_synthetic(self):
        real = [trade(1.0, 1), trade(2.0, 2)]
        seeded = [dict(trade(5.0, 3), strategy_id="backfill-0"),
                  dict(trade(-4.0, 4), strategy_id="backfill-1")]
        stats = rg.archetype_stats({"x": real + seeded})["x"]
        # only the 2 real rows count; the 5.0/-4.0 seeds must not skew
        self.assertEqual(stats["samples"], 2)
        self.assertEqual(stats["synthetic_samples"], 2)
        self.assertAlmostEqual(stats["expectancy_usd"], 1.5, places=4)
        self.assertAlmostEqual(stats["gross_profit_usd"], 3.0, places=4)
        self.assertAlmostEqual(stats["gross_loss_usd"], 0.0, places=4)
        self.assertEqual(stats["profit_factor"], rg.PROFIT_FACTOR_CAP)

    def test_stats_all_synthetic_zero_samples(self):
        seeded = [dict(trade(5.0, 1), strategy_id="backfill-0")]
        stats = rg.archetype_stats({"x": seeded})["x"]
        self.assertEqual(stats["samples"], 0)
        self.assertEqual(stats["synthetic_samples"], 1)
        self.assertEqual(stats["win_rate"], 0.0)
        self.assertEqual(stats["expectancy_usd"], 0.0)

    def test_stats_explcit_synthetic_flag_honored(self):
        # explicit flag works even with a non-backfill strategy_id
        seeded = [dict(trade(9.0, 1), synthetic=True)]
        stats = rg.archetype_stats({"x": seeded})["x"]
        self.assertEqual(stats["samples"], 0)
        self.assertEqual(stats["synthetic_samples"], 1)

    def test_archived_rows_flagged_on_read_without_rewrite(self):
        # archive rows carry no "synthetic" key on disk; archived_by_archetype
        # must flag them at read time and never rewrite the file
        d = tempfile.mkdtemp(prefix="grid-syn-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        path = os.path.join(d, "reliability_archive.json")
        rg.ARCHIVE_PATH = path
        rows = [{"pnl_usd": 1.9461, "close_ts": 1788507611.0,
                 "entered_at": None, "strategy_id": "backfill-0"},
                {"pnl_usd": 0.0791, "close_ts": 1788611561.0,
                 "entered_at": "2026-09-05T15:27:50+03:00",
                 "strategy_id": "6a9c0ac6f891fd79a98926b3"}]
        with open(path, "w") as fh:
            json.dump({"unknown": rows}, fh)
        got = rg.archived_by_archetype()["unknown"]
        self.assertTrue(got[0]["synthetic"])
        self.assertNotIn("synthetic", got[1])
        # on-disk file untouched (no synthetic key persisted by the read)
        with open(path) as fh:
            self.assertNotIn("synthetic", fh.read())
        # and the real/seed split flows through the stats
        stats = rg.archetype_stats({"unknown": got})["unknown"]
        self.assertEqual(stats["samples"], 1)
        self.assertEqual(stats["synthetic_samples"], 1)
        self.assertAlmostEqual(stats["gross_profit_usd"], 0.0791, places=4)
        self.addCleanup(setattr, rg, "ARCHIVE_PATH", rg.ARCHIVE_PATH)

    def test_archive_write_preserves_synthetic_rows(self):
        # archiving must not drop or double-count seeded rows
        d = tempfile.mkdtemp(prefix="grid-syn-w-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        rg.ARCHIVE_PATH = os.path.join(d, "reliability_archive.json")
        seeded = dict(trade(1.0, 1), strategy_id="backfill-0")
        self.assertTrue(rg.archive_trades([seeded], "trend"))
        self.assertTrue(rg.archive_trades([trade(2.0, 2)], "trend"))
        rows = rg.archived_by_archetype()["trend"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["strategy_id"], "backfill-0")
        stats = rg.archetype_stats({"trend": rows})["trend"]
        self.assertEqual(stats["samples"], 1)
        self.assertEqual(stats["synthetic_samples"], 1)


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


class TestRotationArchive(unittest.TestCase):
    """archive_trades/archived_by_archetype — rotated-out bots must keep
    feeding the reliability ledger after WunderTrading deletes them."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="grid-archive-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, True))
        rg.ARCHIVE_PATH = os.path.join(self.dir, "reliability_archive.json")

    def test_roundtrip_and_grouping(self):
        t1, t2 = trade(1.0, 1), trade(-0.5, 2)
        self.assertTrue(rg.archive_trades([t1], "trend"))
        self.assertTrue(rg.archive_trades([t2], "trend"))
        self.assertTrue(rg.archive_trades([trade(3.0, 3)], "chop"))
        self.assertEqual(rg.archived_by_archetype(),
                         {"trend": [t1, t2], "chop": [trade(3.0, 3)]})

    def test_empty_trades_no_write(self):
        self.assertFalse(rg.archive_trades([], "trend"))
        self.assertEqual(rg.archived_by_archetype(), {})

    def test_missing_file_empty(self):
        self.assertEqual(rg.archived_by_archetype(), {})

    def test_bounded_per_archetype(self):
        rows = [trade(float(i), i) for i in range(rg.ARCHIVE_MAX_PER_ARCHETYPE + 10)]
        rg.archive_trades(rows, "trend")
        got = rg.archived_by_archetype()["trend"]
        self.assertEqual(len(got), rg.ARCHIVE_MAX_PER_ARCHETYPE)
        self.assertEqual(got[-1]["pnl_usd"],
                         float(rg.ARCHIVE_MAX_PER_ARCHETYPE + 9))

    def test_unknown_archetype_bucket(self):
        rg.archive_trades([trade(1.0, 1)], None)
        self.assertIn("unknown", rg.archived_by_archetype())


if __name__ == "__main__":
    unittest.main()
