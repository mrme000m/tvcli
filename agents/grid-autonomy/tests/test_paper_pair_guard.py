#!/usr/bin/env python3
"""Unit tests for the Binance paper (demo) pair guard in execution/resolve.py.

Background (az00, 2026-09-08): binance:RAY grid-bot create on the demo-bn
BINANCE_FUTURES paper profile returned HTTP 400 "Please check the highlighted
fields for errors and try again." deterministically, while binance:ZRO
succeeded 26 minutes later through the identical compute_upsert path. RAY
is live on Binance spot AND mainnet USDT-M futures (resolve_pair passes) but
is ABSENT from the Binance futures testnet — the venue WT's Binance demo
engine runs against. paper_pair_supported() is the screen-side guard: it
filters candidates on the public testnet exchangeInfo, fail-open.

All network patched out; state dir re-pointed like test_resolve.py.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "execution"))

import resolve  # noqa: E402

# resolve.STATE_DIR is a module-level global that OTHER test modules also
# re-point at IMPORT time (test_resolve.py:20) — in a full-suite run the
# last import wins, so an import-time assignment here is unreliable
# (integration failure 2026-09-08: the cache tests passed standalone but
# read a fresh cache another module's STATE_DIR had written). Every class
# below that touches the on-disk cache patches STATE_DIR to a fresh temp
# dir per test (auto-restored), keeping these tests hermetic in ANY
# execution order.


class _CacheIsolation(unittest.TestCase):
    """Fresh STATE_DIR (empty paper cache) per test; restored on cleanup."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="grid-paper-guard-test-")
        _p = mock.patch.object(resolve, "STATE_DIR", self.tmp)
        _p.start()
        self.addCleanup(_p.stop)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, True))

_TESTNET = {
    "symbols": [
        {"symbol": "ZROUSDT", "status": "TRADING"},
        {"symbol": "BTCUSDT", "status": "TRADING"},
        {"symbol": "SETTLINGUSDT", "status": "SETTLING"},  # not tradable
    ],
}


class TestFetchTestnetSymbols(_CacheIsolation):
    def test_trading_only_and_fail_soft(self):
        with mock.patch.object(resolve, "_fetch_testnet_symbols",
                               return_value={"ZROUSDT", "BTCUSDT"}):
            got = resolve.binance_paper_futures_symbols()
        self.assertEqual(got, {"ZROUSDT", "BTCUSDT"})

    def test_fetch_error_returns_empty_set(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            self.assertEqual(resolve._fetch_testnet_symbols(), set())

    def test_json_shapes(self):
        payload = json.dumps(_TESTNET).encode()
        with mock.patch("urllib.request.urlopen") as up:
            up.return_value.__enter__.return_value.read.return_value = payload
            got = resolve._fetch_testnet_symbols()
        self.assertEqual(got, {"ZROUSDT", "BTCUSDT"})  # SETTLING excluded


class TestPaperPairSupported(unittest.TestCase):
    # these tests mock binance_paper_futures_symbols itself — no on-disk
    # cache is touched, so no STATE_DIR isolation is needed

    def _symbols(self, syms):
        return mock.patch.object(
            resolve, "binance_paper_futures_symbols", return_value=set(syms))

    def test_ray_absent_on_testnet_is_rejected(self):
        with self._symbols({"ZROUSDT", "BTCUSDT"}):
            self.assertIs(
                resolve.paper_pair_supported("binance", "RAY", "derivative"),
                False)
            # native pair string form behaves the same
            self.assertIs(
                resolve.paper_pair_supported("binance", "RAYUSDT",
                                             "derivative"), False)

    def test_zro_present_on_testnet_passes(self):
        with self._symbols({"ZROUSDT", "BTCUSDT"}):
            self.assertIs(
                resolve.paper_pair_supported("binance", "ZRO", "derivative"),
                True)

    def test_unknown_testnet_fails_open(self):
        with self._symbols(set()):
            self.assertIsNone(
                resolve.paper_pair_supported("binance", "RAY", "derivative"))

    def test_non_binance_venue_has_no_constraint(self):
        with self._symbols(set()):  # even an empty testnet set
            self.assertIs(
                resolve.paper_pair_supported("hyperliquid", "HYPE"), True)

    def test_binance_spot_market_has_no_constraint(self):
        # Binance spot has no paper mode at all; the guard only binds on the
        # BINANCE_FUTURES paper sleeve (market=derivative)
        with self._symbols(set()):
            self.assertIs(
                resolve.paper_pair_supported("binance", "RAY", "spot"), True)
            self.assertIs(resolve.paper_pair_supported("binance", "RAY"),
                           True)  # venue default market is spot

    def test_symbol_normalization(self):
        with self._symbols({"RAYUSDT"}):
            self.assertIs(
                resolve.paper_pair_supported("binance", "ray", "derivative"),
                True)
            self.assertIs(
                resolve.paper_pair_supported("binance", "RAY/USDT",
                                             "derivative"), True)

    def test_empty_symbol_unknown(self):
        with self._symbols({"RAYUSDT"}):
            self.assertIsNone(resolve.paper_pair_supported("binance", ""))


class TestPaperSymbolsCache(_CacheIsolation):
    # setUp gives each test a fresh empty STATE_DIR — no unlink needed

    def test_cache_prevents_refetch(self):
        fresh = {"ZROUSDT", "BTCUSDT"}
        with mock.patch.object(resolve, "_fetch_testnet_symbols",
                               return_value=fresh) as fetch:
            first = resolve.binance_paper_futures_symbols(ttl_h=24)
            second = resolve.binance_paper_futures_symbols(ttl_h=24)
        self.assertEqual(first, fresh)
        self.assertEqual(second, fresh)
        self.assertEqual(fetch.call_count, 1)  # second call served by cache
        with open(os.path.join(self.tmp,
                               "paper_futures_symbols.json")) as fh:
            cache = json.load(fh)
        self.assertEqual(set(cache["symbols"]), fresh)

    def test_stale_cache_served_when_fetch_fails(self):
        stale = {"RAYUSDT", "ZROUSDT"}
        with open(os.path.join(self.tmp, "paper_futures_symbols.json"),
                  "w") as fh:
            json.dump({"fetched_at": 0.0, "symbols": sorted(stale)}, fh)
        with mock.patch.object(resolve, "_fetch_testnet_symbols",
                               return_value=set()):
            self.assertEqual(resolve.binance_paper_futures_symbols(), stale)

    def test_no_cache_and_fetch_failure_is_unknown(self):
        with mock.patch.object(resolve, "_fetch_testnet_symbols",
                               return_value=set()):
            self.assertEqual(resolve.binance_paper_futures_symbols(), set())


if __name__ == "__main__":
    unittest.main()
