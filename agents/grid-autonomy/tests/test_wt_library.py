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


class TestGridSetExits(unittest.TestCase):
    """grid_set_exits — the exit-only live-edit wrapper over
    wtclient.GridClient.set_exits (verified 2026-09-07)."""

    def test_dry_run_envelope_shape(self):
        result = wt_library.grid_set_exits(
            "bot1", take_profit=10.0, stop_loss=15.0,
            pnl_compare_type="total", trailing_activation=5.0,
            trailing_execute=2.0, positions_trailing_stop=True,
            positions_stop_loss_pct=5, order_type="market")
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["transport"], "wtclient.GridClient.set_exits")
        self.assertEqual(result["code"], "bot1")
        self.assertEqual(result["payload"], {
            "take_profit": 10.0, "stop_loss": 15.0,
            "pnl_compare_type": "total", "trailing_activation": 5.0,
            "trailing_execute": 2.0, "positions_trailing_stop": True,
            "positions_stop_loss_pct": 5, "order_type": "market"})
        # dry-run must not touch wtclient at all
        with patch("execution.wt_library.get_wun") as gw:
            wt_library.grid_set_exits("bot1", take_profit=1.0)
            gw.assert_not_called()

    def test_dry_run_drops_unset_kwargs(self):
        result = wt_library.grid_set_exits("bot1")
        self.assertEqual(result["payload"], {})

    def test_live_success_wraps_result(self):
        with patch("execution.wt_library.get_wun") as gw:
            fake_wun = MagicMock()
            fake_wun.grid.set_exits.return_value = {"code": "bot1", "ok": 1}
            gw.return_value = fake_wun
            result = wt_library.grid_set_exits(
                "bot1", take_profit=10.0, dry_run=False)
        self.assertTrue(result["ok"])
        self.assertFalse(result.get("dry_run", False))
        self.assertEqual(result["transport"], "wtclient.GridClient.set_exits")
        self.assertEqual(result["result"], {"code": "bot1", "ok": 1})
        fake_wun.grid.set_exits.assert_called_once_with(
            "bot1", take_profit=10.0)

    def test_live_only_sends_provided_kwargs(self):
        with patch("execution.wt_library.get_wun") as gw:
            fake_wun = MagicMock()
            fake_wun.grid.set_exits.return_value = {}
            gw.return_value = fake_wun
            wt_library.grid_set_exits("bot1", stop_loss=3.0, dry_run=False)
        fake_wun.grid.set_exits.assert_called_once_with("bot1", stop_loss=3.0)

    def test_live_wun_error_returns_ok_false(self):
        from wtclient.errors import WunApiError

        with patch("execution.wt_library.get_wun") as gw:
            fake_wun = MagicMock()
            fake_wun.grid.set_exits.side_effect = WunApiError(
                "bad exit", status_code=400)
            gw.return_value = fake_wun
            result = wt_library.grid_set_exits("bot1", take_profit=1,
                                               dry_run=False)
        self.assertFalse(result["ok"])
        self.assertIn("bad exit", result["error"])

    def test_live_generic_exception_returns_ok_false(self):
        with patch("execution.wt_library.get_wun") as gw:
            fake_wun = MagicMock()
            fake_wun.grid.set_exits.side_effect = RuntimeError("transport died")
            gw.return_value = fake_wun
            result = wt_library.grid_set_exits("bot1", take_profit=1,
                                               dry_run=False)
        self.assertFalse(result["ok"])
        self.assertIn("transport died", result["error"])

    def test_exported_in_all(self):
        self.assertIn("grid_set_exits", wt_library.__all__)


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
