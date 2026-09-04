"""Unit tests for execution/resolve.py (offline; network patched out)."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="grid-resolve-test-")
os.environ["GRID_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "execution"))

import resolve  # noqa: E402

# `daemon.py` (imported by earlier test modules in a full-suite run) imports
# `resolve` before GRID_STATE_DIR is set above, so `resolve.STATE_DIR` is bound
# to the repo's live `state/` dir. Re-point it explicitly so cache reads/writes
# in these tests never touch (or pollute) the live cache, and so the
# "no cache" test is deterministic regardless of import order.
resolve.STATE_DIR = _TMP

NESTED = {
    "HYPERLIQUID_SWAP": {
        "canUseCollateral": True,
        "market": "derivative",
        "markets": {
            "HYPERLIQUID_SWAP:159": {
                "unifiedCode": "HYPE-USDC",
                "inverse": False,
                "pair": {"code": "159", "viewSymbol": "HYPE-USDC",
                         "ref": "HYPE", "base": "USDC"},
            },
            "HYPERLIQUID_SWAP:150": {
                "unifiedCode": "ETH-USDC",
                "inverse": False,
                "pair": {"code": "150", "viewSymbol": "ETH-USDC",
                         "ref": "ETH", "base": "USDC"},
            },
        },
    },
    "BINANCE": {
        "canUseCollateral": False,
        "market": "spot",
        "markets": {
            "BINANCE:BTCUSDT": {
                "unifiedCode": "BTC/USDT",
                "inverse": False,
                "pair": {"code": "BTCUSDT", "viewSymbol": "BTCUSDT",
                         "ref": "BTC", "base": "USDT"},
            },
        },
    },
}

FLAT = resolve._normalize(NESTED, "mixed")


class TestNormalize(unittest.TestCase):
    def test_flatten_hyperliquid(self):
        self.assertEqual(FLAT["HYPERLIQUID_SWAP:159"]["pair"], "HYPE-USDC")
        self.assertEqual(FLAT["HYPERLIQUID_SWAP:159"]["exchange"],
                         "HYPERLIQUID_SWAP")
        self.assertEqual(FLAT["HYPERLIQUID_SWAP:159"]["code"], "159")

    def test_flatten_binance(self):
        self.assertEqual(FLAT["BINANCE:BTCUSDT"]["pair"], "BTCUSDT")
        self.assertEqual(FLAT["BINANCE:BTCUSDT"]["unified"], "BTC/USDT")

    def test_non_dict_survives(self):
        self.assertEqual(resolve._normalize(None, "spot"), {})
        self.assertEqual(resolve._normalize([1, 2], "spot"), {})


class TestCandidates(unittest.TestCase):
    def test_hyperliquid_base(self):
        self.assertIn("HYPE-USDC", resolve._candidates("hyperliquid", "HYPE"))
        self.assertIn("HYPE", resolve._candidates("hyperliquid", "HYPE"))

    def test_binance_base_gets_usdt(self):
        self.assertIn("BTCUSDT", resolve._candidates("binance", "BTC"))

    def test_binance_full_passthrough(self):
        c = resolve._candidates("binance", "BTCUSDT")
        self.assertEqual(c, ["BTCUSDT"])


class TestMarketMapCache(unittest.TestCase):
    def test_fresh_cache_no_network(self):
        cache = {"fetched_at": __import__("time").time(),
                 "market": "derivative", "items": FLAT}
        with open(resolve._cache_path("derivative"), "w") as fh:
            json.dump(cache, fh)
        with mock.patch.object(resolve, "_fetch_map", side_effect=AssertionError):
            out = resolve.market_map("derivative", ttl_h=24)
        self.assertEqual(out["HYPERLIQUID_SWAP:159"]["pair"], "HYPE-USDC")

    def test_stale_cache_offline_fallback(self):
        cache = {"fetched_at": 0.0, "market": "derivative", "items": FLAT}
        with open(resolve._cache_path("derivative"), "w") as fh:
            json.dump(cache, fh)
        with mock.patch.object(resolve, "_fetch_map", return_value=({}, "down")):
            out = resolve.market_map("derivative", ttl_h=24)
        self.assertTrue(out)

    def test_empty_when_no_cache_and_fetch_fails(self):
        with mock.patch.object(resolve, "_fetch_map", return_value=({}, "down")):
            self.assertEqual(resolve.market_map("spot", ttl_h=0), {})


class TestResolvePair(unittest.TestCase):
    def test_hyperliquid_hype_numeric(self):
        with mock.patch.object(resolve, "market_map", return_value=FLAT):
            r = resolve.resolve_pair("hyperliquid", "HYPE")
        self.assertEqual(r["pairCode"], "159")
        self.assertEqual(r["unified"], "HYPE-USDC")
        self.assertEqual(r["pair"], "HYPE-USDC")

    def test_binance_btc(self):
        with mock.patch.object(resolve, "market_map", return_value=FLAT):
            r = resolve.resolve_pair("binance", "BTC")
        self.assertEqual(r["pairCode"], "BTCUSDT")

    def test_unresolved(self):
        with mock.patch.object(resolve, "market_map", return_value=FLAT):
            r = resolve.resolve_pair("hyperliquid", "DOESNOTEXIST")
        self.assertIsNone(r["pairCode"])

    def test_unknown_venue(self):
        r = resolve.resolve_pair("nope", "HYPE")
        self.assertIsNone(r["pairCode"])


if __name__ == "__main__":
    unittest.main()
