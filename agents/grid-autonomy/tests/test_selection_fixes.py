#!/usr/bin/env python3
"""Regression tests for the 2026-09-05 token-selection review fixes.

Covers: adopt_existing's record_decision_safe arity crash (live-observed),
the fee-aware step floor + guardrail, the binance 4h-confirm bare-symbol
400, the stablecoin universe leak (RLUSD/USD1), the global dead-tape ATR
floor, and the harvest-EV ranking adjustment.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
GRID = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, GRID)
sys.path.insert(0, os.path.join(GRID, "screen"))
sys.path.insert(0, os.path.join(GRID, "execution"))

import merge                     # screen/merge.py
import guardrails                # execution/guardrails.py
import market_regime             # wundertrading skill (path set by imports above)


def _cand(symbol="CHIP", venue="hyperliquid", atr=2.0, step=1.0, score=100.0):
    return {"symbol": symbol, "venue": venue, "score": score,
            "score_final": score, "step": step, "spread_pct": None,
            "regime": "chop_high_volatility",
            "metrics": {"atr_pct": atr, "price": 1.0}}


class TestDeadTapeFloor(unittest.TestCase):
    def test_below_floor_dropped(self):
        alive, dropped = merge.drop_dead_tape([_cand(atr=0.15), _cand(atr=0.9)])
        self.assertEqual([c["symbol"] for c in alive], ["CHIP"])
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["atr_pct"], 0.15)

    def test_stable_peg_wobble_dropped(self):
        # RLUSD/USD1 live case: trend_down on peg noise, ATR ~0.01%
        cands = [_cand(symbol="RLUSD", venue="binance", atr=0.016),
                 _cand(symbol="USD1", venue="binance", atr=0.009)]
        alive, dropped = merge.drop_dead_tape(cands)
        self.assertEqual(alive, [])
        self.assertEqual(len(dropped), 2)


class TestStableUniverseFilter(unittest.TestCase):
    def _tickers(self, *symbols):
        return [{"symbol": s + "USDT", "quoteVolume": 50_000_000}
                for s in symbols]

    def test_new_stables_excluded(self):
        with mock.patch.object(merge.urllib.request, "urlopen") as up:
            up.return_value.__enter__.return_value.read.return_value = \
                json.dumps(self._tickers("BTC", "RLUSD", "USD1", "USDE",
                                         "PYUSD", "ETH")).encode()
            rows = merge.binance_spot_universe()
        syms = [b for b, _ in rows]
        self.assertIn("BTCUSDT", syms)
        self.assertIn("ETHUSDT", syms)
        for bad in ("RLUSDUSDT", "USD1USDT", "USDEUSDT", "PYUSDUSDT"):
            self.assertNotIn(bad, syms)


class TestConfirmSymbol(unittest.TestCase):
    def test_binance_candidates_use_full_ticker(self):
        seen = []

        def fake_fetch(venue, symbol, *a, **k):
            seen.append((venue, symbol))
            return [(1.0, 1.1, 0.9, 1.0 + 0.01 * i) for i in range(200)]

        cands = [{"venue": "binance", "symbol": "ZKP", "regime": "trend_up",
                  "metrics": {}}]
        with mock.patch.object(merge, "fetch_candles", fake_fetch), \
                mock.patch.object(merge, "compute_metrics",
                                  lambda cl: {"atr_pct": 1.0}), \
                mock.patch.object(merge, "classify",
                                  lambda m: ("trend_up", {})):
            out = merge.confirm_directional(cands, "4h")
        self.assertEqual(seen[0], ("binance", "ZKPUSDT"))
        self.assertEqual(len(out), 1)  # 4h agrees → kept


class TestHarvestEV(unittest.TestCase):
    def test_flat_tape_penalized_oscillating_rewarded(self):
        flat = _cand(symbol="FLAT", step=1.0, score=100.0)
        osc = _cand(symbol="OSC", step=1.0, score=90.0)

        def fake_fetch(venue, symbol, *a, **k):
            # 300 hourly closes: FLAT is a straight line, OSC swings ±2%
            if symbol.startswith("FLAT"):
                return [(1.0, 1.0, 1.0, 1.0) for i in range(300)]
            return [(1.0, 1.02, 0.98, 1.0 + (0.02 if i % 2 else -0.02))
                    for i in range(300)]

        with mock.patch.object(merge, "fetch_candles", fake_fetch):
            out = merge.apply_harvest_ev([flat, osc], "1h", 300, top_n=2)
        by = {c["symbol"]: c for c in out}
        self.assertTrue(by["FLAT"]["harvest_net_pct_24h"] < 0.1)
        self.assertLess(by["FLAT"]["score"], 100.0)      # −10 dead-tape penalty
        self.assertGreater(by["OSC"]["score"], 90.0)     # harvest bonus
        # re-ranked: the oscillator now leads despite the lower heuristic
        self.assertEqual(out[0]["symbol"], "OSC")

    def test_fail_open_on_fetch_error(self):
        def boom(*a, **k):
            raise RuntimeError("network down")

        with mock.patch.object(merge, "fetch_candles", boom):
            out = merge.apply_harvest_ev([_cand(score=100.0)], "1h", 300)
        self.assertEqual(out[0]["score"], 100.0)  # heuristic untouched


class TestFeeAwareStep(unittest.TestCase):
    def test_step_floored_above_round_trip_cost(self):
        from grid_adapter import build_ticket_payloads
        ticket = {"symbol": "CHIP", "venue": "hyperliquid",
                  "grid_type": "neutral", "decision": "GO", "step_mult": 1.0,
                  "max_alloc_mult": 1.0, "regime": "chop_high_volatility"}
        brief = {"metrics": {"price": 1.0, "atr_pct": 0.2},
                 "spread_pct": 0.4, "evidence": {}}
        with mock.patch("grid_adapter.grid_config.build_grid",
                        lambda *a, **k: {
                            "grid_type": "neutral",
                            "channel": {"width_pct": 2.0},
                            "profit_per_grid_pct": 0.05,  # far below cost
                            "grids": 20,
                            "sizing": {}}), \
                mock.patch("grid_adapter.grid_config.build_mcp",
                           lambda *a, **k: {}), \
                mock.patch("market_regime.fetch_candles",
                            lambda *a, **k: [(1, 1, 1, 1.0 + 0.01 * i)
                                             for i in range(300)]):
            payloads = build_ticket_payloads(
                ticket, brief, 150.0, 0.5, "profile-1", "PAIR1")
        step = payloads["grid_bot"]["profit_per_grid_pct"]
        # 2×spread (0.8) + HL round-trip fees (0.10)
        self.assertGreaterEqual(step, 0.9)

    def test_guard_blocks_fee_underwater_step(self):
        v = guardrails.check_spread({"step_pct": 0.15, "spread_pct": None,
                                     "venue": "binance"})
        self.assertEqual(len(v), 1)  # step 0.15% < binance 0.20% fees
        v = guardrails.check_spread({"step_pct": 0.15, "spread_pct": 0.02,
                                      "venue": "binance"})
        self.assertEqual(len(v), 1)  # also below 2×spread + fees
        v = guardrails.check_spread({"step_pct": 0.5, "spread_pct": 0.02,
                                     "venue": "binance"})
        self.assertEqual(v, [])      # clears 0.04 + 0.20

    def test_guard_ctx_carries_fee(self):
        from grid_adapter import build_ticket_payloads
        ticket = {"symbol": "CHIP", "venue": "hyperliquid",
                  "grid_type": "neutral", "decision": "GO",
                  "max_alloc_mult": 1.0, "regime": "chop_high_volatility"}
        brief = {"metrics": {"price": 1.0, "atr_pct": 2.0},
                 "spread_pct": 0.05, "evidence": {}}
        with mock.patch("grid_adapter.grid_config.build_grid",
                        lambda *a, **k: {"grid_type": "neutral",
                                         "channel": {"width_pct": 12.0},
                                         "profit_per_grid_pct": 1.0,
                                         "grids": 12, "sizing": {}}), \
                mock.patch("grid_adapter.grid_config.build_mcp",
                           lambda *a, **k: {}), \
                mock.patch("market_regime.fetch_candles",
                           lambda *a, **k: [(1, 1, 1, 1.0 + 0.01 * i)
                                            for i in range(300)]):
            payloads = build_ticket_payloads(
                ticket, brief, 150.0, 0.5, "profile-1", "PAIR1")
        self.assertEqual(payloads["guard_ctx"]["round_trip_fee_pct"], 0.10)


class TestAdoptionRecordsDecision(unittest.TestCase):
    """Live regression: adopt_existing crashed on record_decision_safe's
    missing 4th arg, aborting the whole adoption pass."""

    def test_record_decision_safe_defaults_payloads(self):
        import daemon
        with mock.patch.object(daemon, "HAS_REFLECT", True), \
                mock.patch.object(daemon, "record_decision",
                                  lambda *a, **k: "d42"):
            self.assertEqual(
                daemon.record_decision_safe({"symbol": "X"}, {}, {"kind": "adopted"}),
                "d42")

    def test_adopt_existing_records_decision(self):
        import daemon
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["GRID_STATE_DIR"] = tmp.name
        try:
            with mock.patch.object(daemon, "STATE_PATH",
                                   os.path.join(tmp.name, "state.json")), \
                    mock.patch.object(daemon, "self_heal_env",
                                      lambda *a, **k: []), \
                    mock.patch.object(daemon, "grid_profiles_safe",
                                       lambda: []), \
                    mock.patch.object(daemon, "reliability_load_safe",
                                      lambda: {}), \
                    mock.patch.object(daemon, "grid_status_safe",
                                      lambda: [{"code": "ADOPT9",
                                                "paperTrading": True,
                                                "status": "active",
                                                "exchange": "HYPERLIQUID_SWAP",
                                                "pair": "HYPE-USDC"}]), \
                    mock.patch.object(daemon, "reclassify_regime",
                                      lambda *a, **k: "chop_high_volatility"), \
                    mock.patch("market_regime.fetch_candles",
                               lambda *a, **k: [(1, 1, 1, 1.0)
                                                for i in range(300)]), \
                    mock.patch.object(daemon, "_pb_journal", lambda *a, **k: None):
                d = daemon.Daemon.__new__(daemon.Daemon)
                d.config = {"adopt_existing": True, "portfolio": {
                    "total_usd": 500.0,
                    "venues": {"hyperliquid": {"balance_usd": 300.0},
                               "binance": {"balance_usd": 200.0}},
                    "slots_default": 4, "max_alloc_per_slot": 0.5,
                    "cash_buffer_pct": 0.15}}
                d.state = {"journal": [], "active_bots": {}}
                d.adopt_existing(dry_run=True)
            self.assertIn("1", d.state["active_bots"])
            dec_path = os.path.join(tmp.name, "decisions.jsonl")
            self.assertTrue(os.path.isfile(dec_path))
            line = json.loads(open(dec_path).readlines()[-1])
            self.assertEqual(line["symbol"], "HYPE")
            self.assertEqual(line["venue"], "hyperliquid")
        finally:
            del os.environ["GRID_STATE_DIR"]


if __name__ == "__main__":
    unittest.main()
