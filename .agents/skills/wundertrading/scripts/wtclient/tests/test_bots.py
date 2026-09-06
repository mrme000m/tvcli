"""Tests for :class:`wtclient.clients.bots.BotsClient`."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from wtclient.clients.bots import BotsClient


def _mock_transport(payload):
    t = MagicMock()
    t.request.return_value.json.return_value = payload
    return t


class TestBotsClient(unittest.TestCase):
    def test_kinds_returns_supported(self):
        bots = BotsClient(MagicMock())
        self.assertIn("signal", bots.kinds)
        self.assertIn("dca", bots.kinds)
        self.assertIn("mn", bots.kinds)
        self.assertIn("mp", bots.kinds)

    def test_supports_kind(self):
        bots = BotsClient(MagicMock())
        self.assertTrue(bots.supports("dca"))
        self.assertFalse(bots.supports("nope"))

    def test_unknown_kind_raises(self):
        bots = BotsClient(MagicMock())
        with self.assertRaises(ValueError):
            bots.list("nope")

    def test_list_active_dca(self):
        payload = {
            "_embedded": {
                "items": [
                    {
                        "resource": {
                            "code": "abc",
                            "id": 1,
                            "status": "active",
                            "pair": {"unifiedCode": "BTC-USDC"},
                            "dcaTradingType": "long",
                            "exchange": {"code": "HYPERLIQUID_SWAP"},
                        }
                    }
                ]
            }
        }
        transport = _mock_transport(payload)
        bots = BotsClient(transport)
        out = bots.list_active("dca")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["code"], "abc")
        self.assertEqual(out[0]["pair"], "BTC-USDC")
        self.assertEqual(out[0]["type"], "long")
        self.assertEqual(out[0]["exchange"], "HYPERLIQUID_SWAP")

    def test_raw_returns_full_response(self):
        payload = {"_embedded": {"items": [{"resource": {"code": "x"}}]}}
        transport = _mock_transport(payload)
        bots = BotsClient(transport)
        out = bots.raw("signal")
        self.assertEqual(out, payload)


if __name__ == "__main__":
    unittest.main()
