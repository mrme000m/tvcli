#!/usr/bin/env python3
"""Tests for the geo-block-resilient candle fallback (market_regime.py).

Reproduces the az00 failure class — Binance answers HTTP 451 ("Unavailable
For Legal Reasons") from US-datacenter IPs, which used to starve the whole
regime pipeline (screen 4h-confirm, stagnation, position optimizer) of
binance candles. The fix chains: venue REST → data-api.binance.vision mirror
→ tvcli /fetch (TradingView, geo-agnostic). These tests inject the internal
sources (no network) and assert the chain falls through correctly.

Also covers daemon._confluence_ok, which surfaces how many tvcli confluence
skills actually returned a result for a screened candidate.
"""
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
GRID = os.path.normpath(os.path.join(HERE, ".."))
WUN_SCRIPTS = os.path.normpath(os.path.join(
    GRID, "..", "..", ".agents", "skills", "wundertrading", "scripts"))
sys.path.insert(0, GRID)
sys.path.insert(0, WUN_SCRIPTS)

import market_regime  # noqa: E402


def _rows(n=3, start=100.0):
    """Oldest-first (o,h,l,c) rows, distinct per bar."""
    return [(start + i, start + i + 1.0, start + i - 0.5, start + i + 0.5)
            for i in range(n)]


class TvSymbolTest(unittest.TestCase):
    def test_binance_base_symbol_gets_usdt(self):
        self.assertEqual(market_regime._tv_symbol("binance", "GIGGLE"),
                         "BINANCE:GIGGLEUSDT")

    def test_binance_full_symbol_untouched(self):
        self.assertEqual(market_regime._tv_symbol("binance", "BTCUSDT"),
                         "BINANCE:BTCUSDT")

    def test_binance_usdc_untouched(self):
        self.assertEqual(market_regime._tv_symbol("binance", "XRPUSDC"),
                         "BINANCE:XRPUSDC")

    def test_hyperliquid_rides_binance_pair(self):
        self.assertEqual(market_regime._tv_symbol("hyperliquid", "PUMP"),
                         "BINANCE:PUMPUSDT")

    def test_symbol_normalised_upper_and_slash(self):
        self.assertEqual(market_regime._tv_symbol("binance", "btc/usdt"),
                         "BINANCE:BTCUSDT")


class FetchCandlesTvcliTest(unittest.TestCase):
    def _resp(self, periods):
        return {"account": "a", "bars": len(periods), "periods": periods,
                "symbol": "BINANCE:TESTUSDT", "timeframe": "1h"}

    def test_parses_and_orders_oldest_first(self):
        # tvcli returns newest-first (descending time)
        newest_first = [
            {"open": 103.0, "high": 104.0, "low": 102.5, "close": 103.5,
             "time": 3000, "volume": 1.0},
            {"open": 102.0, "high": 103.0, "low": 101.5, "close": 102.5,
             "time": 2000, "volume": 1.0},
            {"open": 101.0, "high": 102.0, "low": 100.5, "close": 101.5,
             "time": 1000, "volume": 1.0},
        ]
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = \
                json.dumps(self._resp(newest_first)).encode()
            rows = market_regime.fetch_candles_tvcli(
                "binance", "TESTUSDT", "1h", 3, "spot")
        # oldest-first (o,h,l,c)
        self.assertEqual(rows[0], (101.0, 102.0, 100.5, 101.5))
        self.assertEqual(rows[-1], (103.0, 104.0, 102.5, 103.5))

    def test_empty_periods_raises(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = \
                json.dumps(self._resp([])).encode()
            with self.assertRaises(RuntimeError):
                market_regime.fetch_candles_tvcli(
                    "binance", "TESTUSDT", "1h", 3, "spot")


class FallbackChainTest(unittest.TestCase):
    """fetch_candles falls binance vision→api→tvcli (hyperliquid direct→tvcli),
    aggregating errors only when every source fails."""

    def _patch(self, direct=None, mirror=None, tvcli=None):
        m = mock.patch.object
        ps = [m(market_regime, "_fetch_direct",
                side_effect=direct if direct is not None
                else lambda e, s, i, l, mk: _rows())]
        if mirror is not None:
            ps.append(m(market_regime, "_binance_mirror",
                        side_effect=mirror))
        if tvcli is not None:
            ps.append(m(market_regime, "fetch_candles_tvcli",
                        side_effect=tvcli))
        return ps

    def _start(self, ps):
        for p in ps:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in ps])

    def test_hyperliquid_direct_success_short_circuits(self):
        rows = _rows(4)
        ps = [mock.patch.object(market_regime, "_fetch_direct",
                                return_value=rows)]
        self._start(ps)
        self.assertEqual(market_regime.fetch_candles(
            "hyperliquid", "BTC", "1h", 4), rows)

    def test_binance_vision_primary_success(self):
        # vision (mirror) is tried FIRST for binance; direct is never called
        vision_rows = _rows(3, 200.0)
        direct = mock.Mock(side_effect=AssertionError("direct must not run"))
        ps = self._patch(direct=direct,
                         mirror=lambda s, i, l: vision_rows)
        self._start(ps)
        rows = market_regime.fetch_candles("binance", "GIGGLEUSDT", "1h", 3,
                                           "spot")
        self.assertEqual(rows, vision_rows)
        direct.assert_not_called()

    def test_vision_451_falls_to_direct(self):
        err = lambda: urllib.error.HTTPError(
            "https://example/x", 451, "Unavailable For Legal Reasons",
            None, None)
        direct_rows = _rows(3, 250.0)
        ps = self._patch(direct=lambda e, s, i, l, mk: direct_rows,
                         mirror=mock.Mock(side_effect=err()))
        self._start(ps)
        rows = market_regime.fetch_candles("binance", "GIGGLEUSDT", "1h", 3,
                                           "spot")
        self.assertEqual(rows, direct_rows)

    def test_vision_then_direct_451_falls_to_tvcli(self):
        err = lambda: urllib.error.HTTPError(
            "https://example/x", 451, "Unavailable For Legal Reasons",
            None, None)
        tvcli_rows = _rows(2, 300.0)
        ps = self._patch(direct=mock.Mock(side_effect=err()),
                         mirror=mock.Mock(side_effect=err()),
                         tvcli=lambda e, s, i, l, mk: tvcli_rows)
        self._start(ps)
        rows = market_regime.fetch_candles("binance", "GIGGLEUSDT", "1h", 2,
                                           "spot")
        self.assertEqual(rows, tvcli_rows)

    def test_all_sources_dead_aggregates_errors(self):
        err = lambda: urllib.error.HTTPError(
            "https://example/x", 451, "Unavailable For Legal Reasons",
            None, None)
        ps = self._patch(direct=mock.Mock(side_effect=err()),
                         mirror=mock.Mock(side_effect=err()),
                         tvcli=mock.Mock(side_effect=RuntimeError("tvcli down")))
        self._start(ps)
        with self.assertRaises(RuntimeError) as ctx:
            market_regime.fetch_candles("binance", "GIGGLEUSDT", "1h", 2,
                                        "spot")
        msg = str(ctx.exception)
        self.assertIn("vision:", msg)
        self.assertIn("binance:", msg)
        self.assertIn("tvcli:", msg)

    def test_hyperliquid_has_no_mirror_leg(self):
        # hyperliquid chain: direct → tvcli (no binance mirror step)
        direct = mock.Mock(side_effect=RuntimeError("hl down"))
        tvcli_rows = _rows(2, 400.0)
        ps = self._patch(direct=direct,
                         tvcli=lambda e, s, i, l, mk: tvcli_rows)
        self._start(ps)
        rows = market_regime.fetch_candles("hyperliquid", "BTC", "1h", 2,
                                           "futures")
        self.assertEqual(rows, tvcli_rows)


class FetchAttributionTest(unittest.TestCase):
    """FETCH_EVENTS ring: whichever hop served the candles records a
    {ts, venue, symbol, interval, hop, rows, ms} event — nothing logged
    WHERE data came from before, so a silent geo-block degradation (all
    fetches now tvcli) was invisible."""

    def setUp(self):
        market_regime.FETCH_EVENTS.clear()

    def _patch(self, direct=None, mirror=None, tvcli=None):
        m = mock.patch.object
        ps = [m(market_regime, "_fetch_direct",
                side_effect=direct if direct is not None
                else lambda e, s, i, l, mk: _rows())]
        if mirror is not None:
            ps.append(m(market_regime, "_binance_mirror",
                        side_effect=mirror))
        if tvcli is not None:
            ps.append(m(market_regime, "fetch_candles_tvcli",
                        side_effect=tvcli))
        for p_ in ps:
            p_.start()
        self.addCleanup(lambda: [p_.stop() for p_ in ps])

    def _last(self):
        return market_regime.fetch_events_tail(1)[-1]

    def test_direct_hop_recorded(self):
        rows = _rows(5, 111.0)
        ps = [mock.patch.object(market_regime, "_fetch_direct",
                                return_value=rows)]
        for p_ in ps:
            p_.start()
        self.addCleanup(lambda: [p_.stop() for p_ in ps])
        out = market_regime.fetch_candles("hyperliquid", "BTC", "1h", 5)
        self.assertEqual(out, rows)
        ev = self._last()
        self.assertEqual(ev["hop"], "direct")
        self.assertEqual(ev["venue"], "hyperliquid")
        self.assertEqual(ev["symbol"], "BTC")
        self.assertEqual(ev["interval"], "1h")
        self.assertEqual(ev["rows"], 5)
        self.assertIsInstance(ev["ms"], int)
        self.assertIsInstance(ev["ts"], float)
        self.assertEqual(set(ev), {"ts", "venue", "symbol", "interval",
                                   "hop", "rows", "ms"})

    def test_vision_hop_recorded(self):
        # binance: the vision mirror serves → hop "vision"; direct unused
        rows = _rows(3, 222.0)
        direct = mock.Mock(side_effect=AssertionError("direct must not run"))
        self._patch(direct=direct, mirror=lambda s, i, l: rows)
        out = market_regime.fetch_candles("binance", "GIGGLEUSDT", "1h", 3)
        self.assertEqual(out, rows)
        self.assertEqual(self._last()["hop"], "vision")
        self.assertEqual(self._last()["symbol"], "GIGGLEUSDT")

    def test_tvcli_hop_recorded(self):
        # vision + direct both 451 → the tvcli leg serves → hop "tvcli"
        err = lambda *a, **k: urllib.error.HTTPError(
            "https://example/x", 451, "Unavailable For Legal Reasons",
            None, None)
        rows = _rows(2, 333.0)
        self._patch(direct=mock.Mock(side_effect=err()),
                    mirror=mock.Mock(side_effect=err()),
                    tvcli=lambda e, s, i, l, mk: rows)
        out = market_regime.fetch_candles("binance", "GIGGLEUSDT", "1h", 2)
        self.assertEqual(out, rows)
        self.assertEqual(self._last()["hop"], "tvcli")

    def test_failed_fetch_records_nothing(self):
        # every hop raising → no event (attribution only for served data)
        err = lambda *a, **k: urllib.error.HTTPError(
            "https://example/x", 451, "Unavailable For Legal Reasons",
            None, None)
        self._patch(direct=mock.Mock(side_effect=err()),
                    mirror=mock.Mock(side_effect=err()),
                    tvcli=mock.Mock(side_effect=RuntimeError("tvcli down")))
        with self.assertRaises(RuntimeError):
            market_regime.fetch_candles("binance", "GIGGLEUSDT", "1h", 2)
        self.assertEqual(market_regime.fetch_events_tail(50), [])

    def test_fetch_events_tail_newest_last_and_capped(self):
        def direct(e, s, i, l, mk):
            return _rows(1, 400.0 + int(s[1:]))
        ps = [mock.patch.object(market_regime, "_fetch_direct",
                                side_effect=direct)]
        for p_ in ps:
            p_.start()
        self.addCleanup(lambda: [p_.stop() for p_ in ps])
        for i in range(7):
            market_regime.fetch_candles("hyperliquid", f"S{i}", "15m", 1)
        tail = market_regime.fetch_events_tail(3)
        self.assertEqual([e["symbol"] for e in tail], ["S4", "S5", "S6"])
        self.assertEqual(market_regime.fetch_events_tail(50)[-1]["symbol"],
                         "S6")
        self.assertEqual(market_regime.fetch_events_tail(0), [])

    def test_ring_is_bounded(self):
        ps = [mock.patch.object(market_regime, "_fetch_direct",
                                return_value=_rows(1))]
        for p_ in ps:
            p_.start()
        self.addCleanup(lambda: [p_.stop() for p_ in ps])
        for _ in range(market_regime.FETCH_EVENTS.maxlen + 5):
            market_regime.fetch_candles("hyperliquid", "BTC", "1h", 1)
        self.assertEqual(len(market_regime.FETCH_EVENTS),
                         market_regime.FETCH_EVENTS.maxlen)

    def test_debug_env_prints_one_stderr_line(self):
        ps = [mock.patch.object(market_regime, "_fetch_direct",
                                return_value=_rows(2))]
        for p_ in ps:
            p_.start()
        self.addCleanup(lambda: [p_.stop() for p_ in ps])
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"MARKET_REGIME_DEBUG": "1"}), \
                mock.patch("sys.stderr", buf):
            market_regime.fetch_candles("hyperliquid", "BTC", "1h", 2)
        line = buf.getvalue().strip()
        self.assertIn("hyperliquid:BTC 1h", line)
        self.assertIn("direct", line)
        # without the env var: no stderr chatter
        buf2 = io.StringIO()
        with mock.patch.dict(os.environ, {"MARKET_REGIME_DEBUG": ""}), \
                mock.patch("sys.stderr", buf2):
            market_regime.fetch_candles("hyperliquid", "BTC", "1h", 2)
        self.assertEqual(buf2.getvalue(), "")


class ConfluenceOkTest(unittest.TestCase):
    def _load(self):
        sys.path.insert(0, GRID)
        import daemon
        return daemon

    def test_counts_succeeded_skills(self):
        daemon = self._load()
        cand = {"confluence": {
            "mtf-confluence": True, "squeeze": True, "choppiness": False,
            "dvi": True, "vp-pro": False, "sr-breaks": True,
            "errors": {"mtf": None}}}
        self.assertEqual(daemon._confluence_ok(cand), 4)

    def test_missing_confluence_is_zero(self):
        daemon = self._load()
        self.assertEqual(daemon._confluence_ok({"score": 1.0}), 0)
        self.assertEqual(daemon._confluence_ok({"confluence": None}), 0)


class SpreadsFallbackTest(unittest.TestCase):
    """execution/spreads.py bookTicker: vision host first, api.binance.com
    fallback, for both the bulk and per-symbol paths (az00 451 geo-block)."""

    class _Resp:
        def __init__(self, obj):
            self._bytes = json.dumps(obj).encode()

        def read(self):
            return self._bytes

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _load(self):
        sys.path.insert(0, os.path.join(GRID, "execution"))
        import spreads
        return spreads

    def test_vision_first_bulk(self):
        spreads = self._load()
        tickers = [{"symbol": "BTCUSDT", "bidPrice": "100", "askPrice": "101"}]
        with mock.patch("spreads.urllib.request.urlopen",
                        return_value=self._Resp(tickers)) as urlopen:
            rows = spreads._fetch_all(["BTCUSDT"])
        self.assertEqual(len(rows), 1)
        # first call must hit data-api.binance.vision
        self.assertIn("data-api.binance.vision",
                      urlopen.call_args.args[0].full_url)

    def test_vision_451_falls_to_api_bulk(self):
        spreads = self._load()
        tickers = [{"symbol": "BTCUSDT", "bidPrice": "100", "askPrice": "101"}]

        def fake_urlopen(req, **kw):
            if "data-api.binance.vision" in req.full_url:
                raise urllib.error.HTTPError(
                    req.full_url, 451, "Unavailable For Legal Reasons",
                    None, None)
            return self._Resp(tickers)

        with mock.patch("spreads.urllib.request.urlopen",
                        side_effect=fake_urlopen):
            rows = spreads._fetch_all(["BTCUSDT"])
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()