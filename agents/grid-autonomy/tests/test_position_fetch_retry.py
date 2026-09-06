#!/usr/bin/env python3
"""Watchdog beat-2 change: _default_fetch retries once on transient errors.

Reproduces the 2026-09-05T22:24:36Z failure class (SSL handshake timeout
on a binance candle fetch) with an injected market_regime and asserts the
single retry recovers it; a second consecutive flake must still raise so
analyze_bot stays fail-soft.
"""
import sys
import types
import unittest
import urllib.error

sys.path.insert(0, ".")


class _FakeMarketRegime(types.ModuleType):
    """market_regime stand-in: fails N times with a transient error."""

    def __init__(self, failures=1, rows=None):
        super().__init__("market_regime")
        self.failures = failures
        self.calls = 0
        self.rows = rows or [(1.0, 2.0, 0.5, 1.5)]

    def fetch_candles(self, venue, symbol, interval, limit, market):
        self.calls += 1
        if self.calls <= self.failures:
            raise urllib.error.URLError(
                "<urlopen error _ssl.c:1063: The handshake operation "
                "timed out>")
        return self.rows


class DefaultFetchRetryTest(unittest.TestCase):
    def _po(self):
        from position_optimizer import PositionOptimizer
        return PositionOptimizer({"enabled": True})

    def test_transient_flake_recovers_on_one_retry(self):
        fake = _FakeMarketRegime(failures=1)
        sys.modules["market_regime"] = fake
        try:
            rows = self._po()._default_fetch(
                "binance", "ROBOUSDT", "1h", 300, "spot")
        finally:
            sys.modules.pop("market_regime", None)
        self.assertEqual(rows, fake.rows)
        self.assertEqual(fake.calls, 2)  # 1 flake + 1 success

    def test_persistent_flake_still_raises(self):
        fake = _FakeMarketRegime(failures=5)
        sys.modules["market_regime"] = fake
        try:
            with self.assertRaises(urllib.error.URLError):
                self._po()._default_fetch(
                    "binance", "ROBOUSDT", "1h", 300, "spot")
        finally:
            sys.modules.pop("market_regime", None)
        self.assertEqual(fake.calls, 2)  # 1 try + 1 retry, then raises

    def test_non_transient_error_not_retried(self):
        fake = _FakeMarketRegime(failures=5)

        class _Hard(urllib.error.URLError):
            def __str__(self):
                return "HTTP Error 400: Bad Request"

        def _hard(*_a):
            fake.calls += 1
            raise _Hard("400")

        fake.fetch_candles = _hard
        sys.modules["market_regime"] = fake
        try:
            with self.assertRaises(_Hard):
                self._po()._default_fetch(
                    "binance", "ROBO", "1h", 300, "spot")
        finally:
            sys.modules.pop("market_regime", None)
        self.assertEqual(fake.calls, 1)  # 400 is not transient: no retry


if __name__ == "__main__":
    unittest.main()
