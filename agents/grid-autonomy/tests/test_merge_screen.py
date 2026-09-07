#!/usr/bin/env python3
"""screen/merge.py tvcli-fitness + universe tests — hermetic, no network.

Covers the numeric fitness read (moves large & fast, CHOP harvestability,
direction agreement, caps/penalties), the config.yaml screen-key reader, and
the Binance universe hygiene filter (stables/leveraged excluded, volume
floor + cap). The tvcli hunt calls themselves are exercised live by the
standalone merge run; here the pure functions are the contract.
"""
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "screen"))

import merge  # noqa: E402


def _res(structure):
    return {"ok": True, "result": {"structure": structure}}


def _cand(regime="chop_high_volatility", venue="hyperliquid",
          atr=2.0, rsi=50.0, price=None):
    m = {"atr_pct": atr, "rsi14": rsi}
    if price is not None:
        m["price"] = price
    return {"regime": regime, "venue": venue, "metrics": m}


class TestTvcliFitness(unittest.TestCase):
    def test_no_results_fails_soft(self):
        # tvcli down: no hunts → only the metric-based "moves large" reads
        bonus, notes, fit = merge.tvcli_fitness(_cand())
        self.assertEqual(bonus, 1.0)          # atr 2.0 ≥ 1.5 → moves-large
        self.assertIn("moves-large", notes)
        self.assertEqual(fit["atr_pct"], 2.0)

    def test_no_results_and_quiet_tape(self):
        bonus, notes, _ = merge.tvcli_fitness(_cand(atr=0.5))
        self.assertEqual(bonus, 0.0)
        self.assertEqual(notes, [])

    def test_squeeze_coiled_breakout_pending(self):
        bonus, notes, fit = merge.tvcli_fitness(
            _cand(atr=0.5), sq=_res({"squeezeOn": True, "squeezeBars": 7}))
        self.assertEqual(bonus, 1.5)
        self.assertIn("squeeze-coiled(breakout pending)", notes)
        self.assertTrue(fit["squeeze_on"])

    def test_squeeze_momentum_release(self):
        # momentum is PRICE-SCALED: 25.0 raw on a $100 symbol = 25% of
        # price — a genuine release (market.lastPrice from the hunt result)
        bonus, notes, fit = merge.tvcli_fitness(
            _cand(atr=0.5),
            sq={"result": {"structure": {"squeezeOn": False,
                                         "momentum": 25.0},
                           "market": {"lastPrice": 100.0}}})
        self.assertEqual(bonus, 1.0)
        self.assertIn("momentum-release", notes)
        self.assertEqual(fit["squeeze_momentum"], 25.0)
        self.assertEqual(fit["squeeze_momentum_pct"], 25.0)

    def test_momentum_release_price_normalized(self):
        # regression (live 2026-09-07): the old raw gate |mom| >= 20 meant
        # "high-priced symbol" — BTC (price 79831, momentum -175.1) always
        # fired, ETH/PUMP never could. Same momentum value, two prices:
        # 5.0 on a $100 symbol = 5% of price → fires; on a $100000 symbol
        # = 0.005% → does not.
        low_priced = merge.tvcli_fitness(
            _cand(atr=0.5),
            sq={"result": {"structure": {"squeezeOn": False,
                                         "momentum": 5.0},
                           "market": {"lastPrice": 100.0}}})
        high_priced = merge.tvcli_fitness(
            _cand(atr=0.5),
            sq={"result": {"structure": {"squeezeOn": False,
                                         "momentum": 5.0},
                           "market": {"lastPrice": 100000.0}}})
        self.assertEqual(low_priced[0], 1.0)     # 5.0/100 = 5% >= 0.10%
        self.assertIn("momentum-release", low_priced[1])
        self.assertEqual(high_priced[0], 0.0)     # 5.0/100000 = 0.005%
        self.assertEqual(high_priced[1], [])
        self.assertEqual(low_priced[2]["squeeze_momentum_pct"], 5.0)
        self.assertEqual(high_priced[2]["squeeze_momentum_pct"], 0.005)

    def test_momentum_release_falls_back_to_metrics_price(self):
        # no market.lastPrice in the hunt result → the candidate's own
        # metrics price normalizes instead
        bonus, notes, fit = merge.tvcli_fitness(
            _cand(atr=0.5, price=50.0),
            sq=_res({"squeezeOn": False, "momentum": 1.0}))
        self.assertEqual(bonus, 1.0)   # 1.0/50 = 2% >= 0.10%
        self.assertIn("momentum-release", notes)
        self.assertEqual(fit["squeeze_momentum_pct"], 2.0)

    def test_momentum_release_without_any_price_fails_soft(self):
        # neither market.lastPrice nor a metrics price → cannot normalize
        # → no bonus (never fire on an unnormalized raw value)
        bonus, notes, fit = merge.tvcli_fitness(
            _cand(atr=0.5),
            sq=_res({"squeezeOn": False, "momentum": 999.0}))
        self.assertEqual(bonus, 0.0)
        self.assertEqual(notes, [])
        self.assertNotIn("squeeze_momentum_pct", fit)

    def test_momentum_release_edge_at_threshold(self):
        # exactly 0.10% of price fires; just under does not
        at_edge = merge.tvcli_fitness(
            _cand(atr=0.5),
            sq={"result": {"structure": {"squeezeOn": False,
                                         "momentum": 0.1},
                           "market": {"lastPrice": 100.0}}})
        self.assertEqual(at_edge[0], 1.0)
        below = merge.tvcli_fitness(
            _cand(atr=0.5),
            sq={"result": {"structure": {"squeezeOn": False,
                                         "momentum": 0.0999},
                           "market": {"lastPrice": 100.0}}})
        self.assertEqual(below[0], 0.0)

    def test_squeeze_active_range_small_bonus(self):
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5),
            sq=_res({"squeezeOn": True, "squeezeBars": 2}))
        self.assertEqual(bonus, 0.5)
        self.assertIn("squeeze-active-range", notes)

    def test_high_chop_harvest(self):
        bonus, notes, fit = merge.tvcli_fitness(
            _cand(atr=0.5, regime="neutral"),
            ch=_res({"chop": 70.0}))
        self.assertEqual(bonus, 1.5)
        self.assertIn("high-chop-harvest", notes)
        self.assertEqual(fit["chop"], 70.0)

    def test_clean_trend_chop(self):
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_up"),
            ch=_res({"chop": 30.0}))
        self.assertEqual(bonus, 1.0)
        self.assertIn("clean-trend", notes)

    def test_mtf_aligned_long(self):
        bonus, notes, fit = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_up"),
            mtf=_res({"mtfComposite": 120.0, "mtfAligned": 2,
                      "volRatio": 1.0}))
        self.assertEqual(bonus, 2.0)
        self.assertIn("mtf-aligned-long", notes)
        self.assertEqual(fit["mtf_composite"], 120.0)

    def test_mtf_aligned_short(self):
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_down"),
            mtf=_res({"mtfComposite": -120.0}))
        self.assertEqual(bonus, 2.0)
        self.assertIn("mtf-aligned-short", notes)

    def test_mtf_range_agree(self):
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="squeeze"),
            mtf=_res({"mtfComposite": 10.0}))
        self.assertEqual(bonus, 1.0)
        self.assertIn("mtf-range-agree", notes)

    def test_mtf_vol_expanding(self):
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="neutral"),
            mtf=_res({"mtfComposite": 0.0, "volRatio": 1.6}))
        self.assertEqual(bonus, 2.0)  # range-agree + vol-expanding
        self.assertIn("vol-expanding", notes)

    def test_dvi_trend_agreement(self):
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_up"),
            dvi=_res({"trend": 1, "momentum": 1.2}))
        self.assertEqual(bonus, 1.0)
        self.assertIn("dvi-trend-agree-long", notes)

    def test_dvi_disagree_no_bonus(self):
        bonus, _, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_up"),
            dvi=_res({"trend": -1}))
        self.assertEqual(bonus, 0.0)

    def test_positive_bonus_capped(self):
        bonus, _, _ = merge.tvcli_fitness(
            _cand(atr=2.5, regime="trend_up"),
            sq=_res({"squeezeOn": True, "squeezeBars": 8}),
            ch=_res({"chop": 20.0}),
            mtf=_res({"mtfComposite": 150.0, "volRatio": 2.0}),
            dvi=_res({"trend": 1}))
        # raw sum would be 1+1.5+1+2+1+1=7.5 → capped at TVCLI_BONUS_CAP
        self.assertEqual(bonus, merge.TVCLI_BONUS_CAP)

    # --- vp-pro (volume profile value area) --------------------------------

    def test_vp_value_area_harvest(self):
        bonus, notes, fit = merge.tvcli_fitness(
            _cand(atr=0.5, regime="neutral"),
            vp=_res({"poc": 100.0, "vah": 105.0, "val": 95.0,
                     "bias": "range", "valueAreaWidth": 10.0,
                     "price": 100.0, "pricePosition": "inside_value_area",
                     "distToPOCPct": 0.1, "distToVAHPct": 4.5,
                     "distToVALPct": -5.0}))
        self.assertEqual(bonus, 1.0)
        self.assertIn("value-area-harvest", notes)
        self.assertEqual(fit["vp_poc"], 100.0)
        self.assertEqual(fit["vp_vah"], 105.0)
        self.assertEqual(fit["vp_val"], 95.0)

    def test_vp_breakout_up_with_trend(self):
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_up"),
            vp=_res({"poc": 100.0, "vah": 105.0, "val": 95.0,
                     "bias": "bullish", "price": 106.0,
                     "distToPOCPct": 6.0, "distToVAHPct": 1.0,
                     "distToVALPct": 11.0, "pricePosition": "above_value_area",
                     "valueAreaWidth": 10.0}))
        self.assertEqual(bonus, 1.5)
        self.assertIn("va-breakout-up", notes)

    def test_vp_breakout_up_inferred_from_price(self):
        # pricePosition absent (older parser) — price > VAH still proves it
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_up"),
            vp=_res({"poc": 100.0, "vah": 105.0, "val": 95.0,
                     "bias": "bullish", "price": 105.5}))
        self.assertEqual(bonus, 1.5)
        self.assertIn("va-breakout-up", notes)

    def test_vp_breakout_down_with_trend(self):
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_down"),
            vp=_res({"poc": 100.0, "vah": 105.0, "val": 95.0,
                     "bias": "bearish", "price": 94.0,
                     "pricePosition": "below_value_area"}))
        self.assertEqual(bonus, 1.5)
        self.assertIn("va-breakout-down", notes)

    def test_vp_inside_value_area_trend_no_breakout_bonus(self):
        # trend regime but price INSIDE the value area → no breakout bonus
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_up"),
            vp=_res({"poc": 100.0, "vah": 105.0, "val": 95.0,
                     "bias": "neutral", "price": 101.0,
                     "pricePosition": "inside_value_area"}))
        self.assertEqual(bonus, 0.0)
        self.assertEqual(notes, [])

    # --- public `vp` skill shape (aboveVAHBuffer/belowVALBuffer + market
    # price — vp-pro is a private script most accounts cannot hunt) --------

    @staticmethod
    def _vp_public(buffers):
        return {"ok": True, "result": {
            "structure": {"poc": 100.0, "vah": 105.0, "val": 95.0,
                          **buffers},
            "market": {"lastPrice": 100.0, "bias": "range"}}}

    def test_vp_public_value_area_harvest(self):
        bonus, notes, fit = merge.tvcli_fitness(
            _cand(atr=0.5, regime="neutral"),
            vp=self._vp_public({"aboveVAHBuffer": False,
                                "belowVALBuffer": False}))
        self.assertEqual(bonus, 1.0)
        self.assertIn("value-area-harvest", notes)
        self.assertEqual(fit["vp_poc"], 100.0)

    def test_vp_public_breakout_up_from_buffer(self):
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_up"),
            vp=self._vp_public({"aboveVAHBuffer": True,
                                "belowVALBuffer": False}))
        self.assertEqual(bonus, 1.5)
        self.assertIn("va-breakout-up", notes)

    def test_vp_public_breakout_down_from_buffer(self):
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_down"),
            vp=self._vp_public({"aboveVAHBuffer": False,
                                "belowVALBuffer": True}))
        self.assertEqual(bonus, 1.5)
        self.assertIn("va-breakout-down", notes)

    def test_vp_public_breakout_from_market_price(self):
        # buffers absent — market.lastPrice > VAH still proves the breakout
        res = {"ok": True, "result": {
            "structure": {"poc": 100.0, "vah": 105.0, "val": 95.0},
            "market": {"lastPrice": 106.0}}}
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_up"), vp=res)
        self.assertEqual(bonus, 1.5)
        self.assertIn("va-breakout-up", notes)

    def test_vp_absent_no_bonus(self):
        # parser returns no structure when POC/VAH/VAL are missing
        bonus, notes, fit = merge.tvcli_fitness(
            _cand(atr=0.5), vp={"result": {}})
        self.assertEqual(bonus, 0.0)
        self.assertEqual(notes, [])
        self.assertNotIn("vp_poc", fit)

    # --- sr-breaks (support/resistance breaks) ----------------------------

    def test_sr_fresh_breakout_up(self):
        bonus, notes, fit = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_up"),
            sr=_res({"support": 100.0, "resistance": 110.0, "price": 111.0,
                     "bias": "bullish", "lastBreak": "bullish",
                     "breakBarsAgo": 3}))
        self.assertEqual(bonus, 1.5)
        self.assertIn("fresh-breakout-up", notes)
        self.assertEqual(fit["sr_last_break"], "bullish")
        self.assertEqual(fit["sr_break_bars_ago"], 3)

    def test_sr_recent_breakout_up(self):
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_up"),
            sr=_res({"support": 100.0, "resistance": 110.0, "price": 109.0,
                     "bias": "bullish", "lastBreak": "bullish",
                     "breakBarsAgo": 12}))
        self.assertEqual(bonus, 0.75)
        self.assertIn("recent-breakout-up", notes)

    def test_sr_fresh_breakout_down(self):
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_down"),
            sr=_res({"support": 100.0, "resistance": 110.0, "price": 99.0,
                     "bias": "bearish", "lastBreak": "bearish",
                     "breakBarsAgo": 2}))
        self.assertEqual(bonus, 1.5)
        self.assertIn("fresh-breakout-down", notes)

    def test_sr_recent_breakout_down(self):
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_down"),
            sr=_res({"support": 100.0, "resistance": 110.0, "price": 101.0,
                     "bias": "bearish", "lastBreak": "bearish",
                     "breakBarsAgo": 20}))
        self.assertEqual(bonus, 0.75)
        self.assertIn("recent-breakout-down", notes)

    def test_sr_regime_disagree_zero(self):
        # bullish break against a down regime → 0, never negative
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_down"),
            sr=_res({"lastBreak": "bullish", "breakBarsAgo": 1}))
        self.assertEqual(bonus, 0.0)
        self.assertEqual(notes, [])

    def test_sr_stale_break_no_bonus(self):
        # aligned but >20 bars ago → too old to ride
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_up"),
            sr=_res({"lastBreak": "bullish", "breakBarsAgo": 45}))
        self.assertEqual(bonus, 0.0)

    def test_sr_absent_no_bonus(self):
        bonus, notes, fit = merge.tvcli_fitness(_cand(atr=0.5))
        self.assertEqual(bonus, 0.0)
        self.assertEqual(notes, [])
        self.assertNotIn("sr_last_break", fit)

    def test_positive_bonus_capped_with_new_skills(self):
        # raw sum 1.5+1+2+1+1+1.5+1.5 = 9.5 → capped at TVCLI_BONUS_CAP
        bonus, _, _ = merge.tvcli_fitness(
            _cand(atr=0.5, regime="trend_up"),
            sq=_res({"squeezeOn": True, "squeezeBars": 8}),
            ch=_res({"chop": 20.0}),
            mtf=_res({"mtfComposite": 150.0, "volRatio": 2.0}),
            dvi=_res({"trend": 1}),
            vp=_res({"poc": 100.0, "vah": 105.0, "val": 95.0,
                     "bias": "bullish-breakout"}),
            sr=_res({"lastBreak": "bullish", "breakBarsAgo": 2}))
        self.assertEqual(bonus, merge.TVCLI_BONUS_CAP)

    def test_rsi_overheated_penalty(self):
        bonus, notes, _ = merge.tvcli_fitness(_cand(atr=0.5, rsi=80.0))
        self.assertEqual(bonus, -3.0)
        self.assertIn("rsi-overheated", notes)

    def test_binance_short_flat_penalty(self):
        bonus, notes, _ = merge.tvcli_fitness(
            _cand(atr=0.5, venue="binance", regime="trend_down"))
        self.assertEqual(bonus, -25.0)
        self.assertIn("spot-no-short", notes)

    def test_nan_guarded(self):
        self.assertIsNone(merge._rnum(_res({"x": float("nan")}), "structure", "x"))
        self.assertIsNone(merge._rnum({}, "structure", "x"))
        self.assertIsNone(merge._rnum(_res({"x": "nope"}), "structure", "x"))


class TestScreenBinanceCache(unittest.TestCase):
    def test_cache_shares_candles_across_presets(self):
        # regression: main() used to seed candle_cache as a LIST — tuple
        # keys (symbol, interval, limit) then failed on every lookup and
        # screen_binance silently skipped the whole Binance venue
        from universe_screen import load_presets
        preset = load_presets(None)["grid-directional"]
        candles = [[i, 100.0 + i, 101.0 + i, 100.5 + i, 99.5 + i, 5.0]
                   for i in range(300)]
        cache = {}
        fetches = {"n": 0}

        def fake_fetch(venue, symbol, interval, limit, market):
            fetches["n"] += 1
            return list(candles)

        with mock.patch("merge.fetch_candles", side_effect=fake_fetch), \
                mock.patch("merge.binance_spot_universe",
                           return_value=[("BTCUSDT", 9e8)]), \
                mock.patch("merge.binance_spreads", return_value={}):
            out1 = merge.screen_binance("p1", preset, "1h", 300,
                                        2_000_000, 100, cache=cache)
            out2 = merge.screen_binance("p2", preset, "1h", 300,
                                       2_000_000, 100, cache=cache)
        self.assertEqual(fetches["n"], 1)   # second preset hit the cache
        self.assertTrue(out1 or out2)


class TestRetryUrlopenJson(unittest.TestCase):
    def test_succeeds_after_transient_failure(self):
        calls = {"n": 0}

        def flaky(req, timeout, context):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("ssl handshake timed out")
            return _FakeResp([{"symbol": "BTCUSDT", "quoteVolume": "9"}])
        with mock.patch("merge.urllib.request.urlopen", side_effect=flaky), \
                mock.patch("time.sleep") as sl:
            data = merge.retry_urlopen_json(mock.Mock(), tries=3, timeout=5)
        self.assertEqual(data, [{"symbol": "BTCUSDT", "quoteVolume": "9"}])
        self.assertEqual(calls["n"], 2)
        sl.assert_called_once()  # backoff between attempts

    def test_raises_after_all_tries(self):
        with mock.patch("merge.urllib.request.urlopen",
                        side_effect=OSError("down")), \
                mock.patch("time.sleep"):
            with self.assertRaises(OSError):
                merge.retry_urlopen_json(mock.Mock(), tries=3, timeout=5)


class TestConfigScreenReader(unittest.TestCase):
    def test_confirm_interval_from_config(self):
        self.assertEqual(merge.config_confirm_interval(), "4h")

    def test_confluence_skills_include_dvi(self):
        skills = merge.config_confluence_skills()
        self.assertIn("dvi", skills)
        self.assertIn("squeeze", skills)
        self.assertIn("mtf-confluence", skills)

    def test_screen_scalars(self):
        self.assertEqual(merge.config_screen_value("min_volume_usd", 0),
                         2_000_000)
        self.assertEqual(merge.config_screen_value("universe_max_symbols", 0),
                         100)

    def test_missing_key_returns_default(self):
        self.assertIsNone(merge._config_screen("no_such_key_xyz"))
        self.assertEqual(merge.config_screen_value("no_such_key_xyz", 7), 7)


class _FakeResp:
    def __init__(self, obj):
        self._bytes = json.dumps(obj).encode()

    def read(self):
        return self._bytes

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestBinanceUniverse(unittest.TestCase):
    def test_filter_and_cap(self):
        tickers = [
            {"symbol": "BTCUSDT", "quoteVolume": "900000000"},
            {"symbol": "ETHUSDT", "quoteVolume": "500000000"},
            {"symbol": "SHIBUSDT", "quoteVolume": "60000000"},
            {"symbol": "TINYUSDT", "quoteVolume": "1000"},      # below floor
            {"symbol": "USDCUSDT", "quoteVolume": "800000000"},  # stable
            {"symbol": "BTCUPUSDT", "quoteVolume": "300000000"},  # leveraged
            {"symbol": "ETHBTC", "quoteVolume": "999999999"},    # not USDT
            {"symbol": "\u5e01\u5b89\u4eba\u751fUSDT", "quoteVolume": "70000000"},  # non-ASCII
        ]
        with mock.patch("merge.urllib.request.urlopen",
                        return_value=_FakeResp(tickers)):
            rows = merge.binance_spot_universe(
                min_quote_vol_usd=2_000_000, max_symbols=100)
        syms = [s for s, _ in rows]
        self.assertIn("BTCUSDT", syms)
        self.assertIn("SHIBUSDT", syms)
        self.assertNotIn("TINYUSDT", syms)
        self.assertNotIn("USDCUSDT", syms)
        self.assertNotIn("BTCUPUSDT", syms)
        self.assertNotIn("ETHBTC", syms)
        self.assertNotIn("\u5e01\u5b89\u4eba\u751fUSDT", syms)  # ascii-only
        # sorted by volume, desc
        self.assertEqual(syms[0], "BTCUSDT")

    def test_cap_respected(self):
        tickers = [{"symbol": f"T{i:03d}USDT", "quoteVolume": "5000000"}
                   for i in range(50)]
        with mock.patch("merge.urllib.request.urlopen",
                        return_value=_FakeResp(tickers)):
            rows = merge.binance_spot_universe(
                min_quote_vol_usd=2_000_000, max_symbols=10)
        self.assertEqual(len(rows), 10)

    def test_vision_first_with_api_fallback(self):
        # data-api.binance.vision is tried first; on HTTP 451 (az00 geo-block)
        # it falls through to api.binance.com. retry_urlopen_json inspects the
        # request's full_url to decide which host is being called.
        tickers = [{"symbol": "BTCUSDT", "quoteVolume": "900000000"}]
        calls = []

        def fake_retry(req, tries=3, timeout=30, backoff_s=2.0):
            url = getattr(req, "full_url", "")
            calls.append(url)
            if "data-api.binance.vision" in url:
                raise urllib.error.HTTPError(
                    url, 451, "Unavailable For Legal Reasons", None, None)
            return tickers

        with mock.patch("merge.retry_urlopen_json", side_effect=fake_retry):
            rows = merge.binance_spot_universe(
                min_quote_vol_usd=2_000_000, max_symbols=100)
        self.assertEqual([s for s, _ in rows], ["BTCUSDT"])
        self.assertEqual(len(calls), 2)
        self.assertIn("data-api.binance.vision", calls[0])
        self.assertIn("api.binance.com", calls[1])


class TestHuntStats(unittest.TestCase):
    """last_hunt_stats / report["hunt_stats"]: what the tvcli /hunt pass
    actually found (per-skill hunted/ok, boosted candidates, errors) —
    a down tvcli degraded silently to score-only screening before."""

    def setUp(self):
        # module-level snapshot: each test starts from a known zero state
        merge._set_hunt_stats({"at": 0.0, "skills": {},
                               "candidates_boosted": 0, "errors": []})

    def _cands(self, n=2):
        return [{"venue": "hyperliquid", "symbol": f"S{i}",
                 "tv_symbol": f"BINANCE:S{i}USDT",
                 "regime": "neutral", "score": 50.0,
                 "metrics": {"atr_pct": 0.5, "price": 100.0}}
                for i in range(n)]

    def test_zero_shape_before_any_run(self):
        st = merge.last_hunt_stats()
        self.assertEqual(set(st), {"at", "skills", "candidates_boosted",
                                   "errors"})
        self.assertEqual(st["skills"], {})
        self.assertEqual(st["candidates_boosted"], 0)
        self.assertEqual(st["errors"], [])
        self.assertEqual(st["at"], 0.0)

    def test_skills_counts_and_boosted(self):
        # squeeze result carries a price for the momentum gate
        def hunt(skill, syms, tf, bars):
            if skill == "squeeze":
                return {
                    "BINANCE:S0USDT": {"result": {
                        "structure": {"squeezeOn": False, "momentum": 5.0},
                        "market": {"lastPrice": 100.0}}},
                    "BINANCE:S1USDT": {"error": "no data"}}
            return {s: {"result": {"structure": {}}} for s in syms}

        cands = self._cands(2)
        with mock.patch("merge.config_confluence_skills",
                        return_value=["squeeze", "choppiness"]), \
                mock.patch("merge.tv_hunt", side_effect=hunt):
            out = merge.apply_confluence(cands)
        st = merge.last_hunt_stats()
        self.assertEqual(st["skills"]["squeeze"], {"hunted": 2, "ok": 1})
        self.assertEqual(st["skills"]["choppiness"], {"hunted": 2, "ok": 2})
        self.assertEqual(st["errors"], [])
        # S0 earned the momentum-release bonus (+1.0); S1 got nothing
        self.assertEqual(st["candidates_boosted"], 1)
        self.assertEqual(out[0]["score_final"], 51.0)
        self.assertEqual(out[1]["score_final"], 50.0)
        self.assertIsInstance(st["at"], float)
        self.assertGreater(st["at"], 0.0)

    def test_hunt_exception_recorded_as_error(self):
        def boom(skill, syms, tf, bars):
            raise RuntimeError("tvcli down")

        cands = self._cands(1)
        with mock.patch("merge.config_confluence_skills",
                        return_value=["squeeze", "choppiness"]), \
                mock.patch("merge.tv_hunt", side_effect=boom), \
                mock.patch("sys.stderr"):
            merge.apply_confluence(cands)
        st = merge.last_hunt_stats()
        self.assertEqual(st["skills"]["squeeze"], {"hunted": 0, "ok": 0})
        self.assertEqual(st["skills"]["choppiness"], {"hunted": 0, "ok": 0})
        self.assertEqual(len(st["errors"]), 2)
        self.assertIn("squeeze", st["errors"][0])
        self.assertIn("tvcli down", st["errors"][0])
        self.assertEqual(st["candidates_boosted"], 0)

    def test_last_hunt_stats_is_a_copy(self):
        # callers mutating the snapshot must not corrupt module state
        st = merge.last_hunt_stats()
        st["skills"]["x"] = {"hunted": 99, "ok": 99}
        self.assertNotIn("x", merge.last_hunt_stats()["skills"])


class TestReportHuntStats(unittest.TestCase):
    """main() folds the hunt snapshot into report["hunt_stats"] — the
    daemon's run_merge is a subprocess, so the report is the only channel
    the module-level stats cross the process boundary through."""

    def setUp(self):
        merge._set_hunt_stats({"at": 0.0, "skills": {},
                               "candidates_boosted": 0, "errors": []})

    def _run_main(self, tmp_out):
        argv = ["merge.py", "--top", "5", "--confluence-top", "5",
                "--out", tmp_out]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch("merge.load_presets",
                           return_value={"grid-neutral": {},
                                         "grid-directional": {}}), \
                mock.patch("merge.screen_hyperliquid", return_value=[]), \
                mock.patch("merge.screen_binance",
                           return_value=[]), \
                mock.patch("merge.confirm_directional",
                           side_effect=lambda cands, iv: cands), \
                mock.patch("merge.apply_harvest_ev",
                           side_effect=lambda cands, *a, **k: cands), \
                mock.patch("sys.stdout"):
            merge.main()
        with open(tmp_out) as fh:
            return json.load(fh)

    def test_report_carries_hunt_stats_zero_shape(self):
        # no candidates → confluence skipped → the honest zero snapshot
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rep = self._run_main(os.path.join(td, "report.json"))
        self.assertIn("hunt_stats", rep)
        st = rep["hunt_stats"]
        self.assertEqual(set(st), {"at", "skills", "candidates_boosted",
                                   "errors"})
        self.assertEqual(st["skills"], {})
        self.assertEqual(st["candidates_boosted"], 0)
        self.assertEqual(st["errors"], [])
        self.assertGreater(st["at"], 0.0)

    def test_skipped_confluence_does_not_leak_stale_stats(self):
        # a prior apply_confluence in this process must NOT surface in a
        # run whose shortlist was empty (that run hunted nothing)
        def hunt(skill, syms, tf, bars):
            return {s: {"result": {"structure": {}}} for s in syms}

        cands = [{"venue": "hyperliquid", "symbol": "S0",
                  "tv_symbol": "BINANCE:S0USDT", "regime": "neutral",
                  "score": 50.0, "metrics": {"atr_pct": 0.5}}]
        with mock.patch("merge.config_confluence_skills",
                        return_value=["squeeze"]), \
                mock.patch("merge.tv_hunt", side_effect=hunt):
            merge.apply_confluence(cands)
        self.assertEqual(merge.last_hunt_stats()["skills"]["squeeze"]["ok"],
                         1)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rep = self._run_main(os.path.join(td, "report.json"))
        self.assertEqual(rep["hunt_stats"]["skills"], {})
        self.assertEqual(rep["hunt_stats"]["candidates_boosted"], 0)

    def test_report_carries_the_confluence_snapshot_when_hunted(self):
        # shortlist non-empty → apply_confluence runs inside main() and
        # its per-skill counts ride the report out of the subprocess
        def hunt(skill, syms, tf, bars):
            return {
                "BINANCE:S0USDT": {"result": {
                    "structure": {"squeezeOn": False, "momentum": 5.0},
                    "market": {"lastPrice": 100.0}}},
                "BINANCE:S1USDT": {"error": "no data"}}

        screened = [
            {"venue": "hyperliquid", "symbol": f"S{i}",
             "tv_symbol": f"BINANCE:S{i}USDT", "regime": "neutral",
             "score": 60.0 - i, "spread_pct": 0.02, "step": 0.5,
             "archetype": "x", "vol_usd": 9e8, "oi_usd": None,
             "mark_px": 100.0, "flags": [], "preset": "grid-neutral",
             "metrics": {"atr_pct": 0.5, "price": 100.0, "rsi14": 50},
             "evidence": {}}
            for i in range(2)]
        import copy
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            argv = ["merge.py", "--top", "5", "--confluence-top", "5",
                    "--out", os.path.join(td, "report.json")]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch("merge.load_presets",
                               return_value={"grid-neutral": {},
                                             "grid-directional": {}}), \
                    mock.patch("merge.screen_hyperliquid",
                               return_value=copy.deepcopy(screened)), \
                    mock.patch("merge.screen_binance",
                               return_value=[]), \
                    mock.patch("merge.confirm_directional",
                               side_effect=lambda cands, iv: cands), \
                    mock.patch("merge.apply_harvest_ev",
                               side_effect=lambda cands, *a, **k: cands), \
                    mock.patch("merge.config_confluence_skills",
                               return_value=["squeeze"]), \
                    mock.patch("merge.tv_hunt", side_effect=hunt), \
                    mock.patch("sys.stdout"):
                merge.main()
            with open(os.path.join(td, "report.json")) as fh:
                rep = json.load(fh)
        st = rep["hunt_stats"]
        self.assertEqual(st["skills"]["squeeze"], {"hunted": 2, "ok": 1})
        # S0 (momentum 5.0 on a $100 symbol = 5%) got the release bonus
        self.assertEqual(st["candidates_boosted"], 1)
        by_sym = {c["symbol"]: c for c in rep["results"]}
        self.assertEqual(by_sym["S0"]["score_final"], 61.0)
        self.assertEqual(by_sym["S1"]["score_final"], 59.0)


if __name__ == "__main__":
    unittest.main()
