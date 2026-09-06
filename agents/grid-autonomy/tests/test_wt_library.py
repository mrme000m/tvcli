"""Tests for the in-process wtclient adapter used by grid-autonomy.

These tests use mocked transports so they are hermetic — no network, no
browser. They verify the dry_run semantics, the redaction of secrets, and
the recorder/catalog wiring.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make the adapter importable
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from execution import wt_library  # noqa: E402


class TestDryRunSemantics(unittest.TestCase):
    def test_grid_create_dry_run(self):
        result = wt_library.grid_create({"exchangeCode": "HYPERLIQUID_SWAP", "pairCode": "191"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["transport"], "wtclient.GridClient.create")
        # secret redaction in payload preview
        body = {"exchangeCode": "X", "profilesCodes": ["secret-1"]}
        result = wt_library.grid_create(body)
        self.assertEqual(result["payload_preview"]["profilesCodes"], "<redacted>")
        self.assertEqual(result["payload_preview"]["exchangeCode"], "X")

    def test_grid_stop_dry_run(self):
        result = wt_library.grid_stop("bot123", "stop_only")
        self.assertTrue(result["ok"])
        self.assertEqual(result["condition"], "stop_only")
        self.assertEqual(result["code"], "bot123")

    def test_grid_delete_dry_run(self):
        result = wt_library.grid_delete("bot123")
        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "bot123")

    def test_grid_edit_dry_run_drops_market_hint(self):
        body = {"exchangeCode": "BINANCE", "gridMarketHint": "derivative", "pairCode": "1"}
        result = wt_library.grid_edit("bot1", body)
        self.assertTrue(result["ok"])
        self.assertEqual(result["market"], "spot")  # BINANCE → spot
        self.assertNotIn("gridMarketHint", result["payload_preview"])


class TestLiveCallsUseWtclient(unittest.TestCase):
    def setUp(self):
        wt_library.reset_wun()

    def tearDown(self):
        wt_library.reset_wun()

    def test_grid_list_calls_wtclient(self):
        with patch("execution.wt_library.get_wun") as gw:
            fake_wun = MagicMock()
            fake_wun.grid.list.return_value = [{"code": "abc"}]
            gw.return_value = fake_wun
            result = wt_library.grid_list()
        self.assertEqual(result, [{"code": "abc"}])
        fake_wun.grid.list.assert_called_once_with(active_only=True, limit=50)

    def test_grid_create_live(self):
        with patch("execution.wt_library.get_wun") as gw:
            fake_wun = MagicMock()
            fake_wun.grid.create.return_value = {"result": "created"}
            gw.return_value = fake_wun
            result = wt_library.grid_create({"exchangeCode": "HYPERLIQUID_SWAP"}, dry_run=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"], {"result": "created"})
        fake_wun.grid.create.assert_called_once()

    def test_grid_stop_live_handles_wun_error(self):
        from wtclient.errors import WunApiError

        with patch("execution.wt_library.get_wun") as gw:
            fake_wun = MagicMock()
            fake_wun.grid.stop.side_effect = WunApiError("nope", status_code=400)
            gw.return_value = fake_wun
            result = wt_library.grid_stop("bot1", dry_run=False)
        self.assertFalse(result["ok"])
        self.assertIn("nope", result["error"])


class TestRecorderAndCatalog(unittest.TestCase):
    def setUp(self):
        wt_library.reset_wun()

    def tearDown(self):
        wt_library.reset_wun()

    def test_catalog_empty_when_debug_disabled(self):
        cat = wt_library.catalog()
        self.assertEqual(len(cat), 0)

    def test_recorder_none_when_debug_disabled(self):
        self.assertIsNone(wt_library.recorder())


if __name__ == "__main__":
    unittest.main()
