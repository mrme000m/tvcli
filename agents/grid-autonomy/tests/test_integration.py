#!/usr/bin/env python3
"""Integration tests — orchestrator wiring added after WP-A/B/C merged.

Covers the cross-worker seams: venue-strict profile selection, the
Binance-futures paper stand-in (market/exchange coherence), the
fetch-symbol fix, the rotation pass in rescreen_cycle, and the
manual /rotate force flag.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "execution"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "policy"))

import daemon
from daemon import (select_profile, market_for_profile, fetch_symbol,
                    _allowed_profile_names)
import grid_adapter

PROFILES = [
    {"code": "real-hl", "name": "WunderTrading-1769861648579",
     "exchange": "HYPERLIQUID_SWAP", "paperTrading": False, "balance": 4.56},
    {"code": "paper-hl", "name": "demo-hype",
     "exchange": "HYPERLIQUID_SWAP", "paperTrading": True, "balance": 10000.0},
    {"code": "paper-bn", "name": "demo-bn",
     "exchange": "BINANCE_FUTURES", "paperTrading": True, "balance": 10000.0},
    {"code": "real-bn-spot", "name": "real-binance-spot",
     "exchange": "BINANCE", "paperTrading": False, "balance": 200.0},
]

CFG = {
    "autonomy": {"paper_profiles": {"hyperliquid": ["demo-hype"],
                                     "binance": ["demo-bn"]},
                 "live_profiles": []},
}


class TestSelectProfile(unittest.TestCase):
    def test_venue_strict_no_cross_venue_fallback(self):
        # binance venue must never receive the HL paper profile
        cfg = {"autonomy": {"paper_profiles": {"hyperliquid": ["demo-hype"],
                                               "binance": []},
                           "live_profiles": []}}
        profile, violation = select_profile("binance", PROFILES, cfg, paper=True)
        self.assertIsNone(profile)
        self.assertIn("no binance profile allowlisted", violation)

    def test_paper_binance_uses_futures_stand_in(self):
        profile, violation = select_profile("binance", PROFILES, CFG, paper=True)
        self.assertIsNone(violation)
        self.assertEqual(profile["name"], "demo-bn")
        self.assertEqual(profile["exchange"], "BINANCE_FUTURES")

    def test_hyperliquid_paper(self):
        profile, violation = select_profile("hyperliquid", PROFILES, CFG, paper=True)
        self.assertIsNone(violation)
        self.assertEqual(profile["name"], "demo-hype")

    def test_denylisted_profile_refused_even_if_listed(self):
        denied = dict(PROFILES[0],
                      code="c629f5ba3a643a82137e7864",  # real denylisted code
                      paperTrading=True)
        cfg = {"autonomy": {"paper_profiles": {
                   "hyperliquid": ["WunderTrading-1769861648579"],
                   "binance": ["demo-bn"]},
               "live_profiles": []}}
        profile, violation = select_profile(
            "hyperliquid", PROFILES + [denied], cfg, paper=True)
        self.assertIn("denylisted", violation)

    def test_real_profiles_refused_in_paper_mode(self):
        profile, violation = select_profile(
            "binance", [PROFILES[3]], CFG, paper=True)  # spot, not paper
        self.assertIsNone(profile)
        self.assertIn("allowlisted", violation)

    def test_flat_legacy_paper_profiles_still_work(self):
        cfg = {"autonomy": {"paper_profiles": ["demo-hype", "demo-bn"],
                           "live_profiles": []}}
        profile, violation = select_profile("binance", PROFILES, cfg, paper=True)
        self.assertIsNone(violation)
        self.assertEqual(profile["name"], "demo-bn")
        self.assertEqual(_allowed_profile_names(cfg),
                         {"demo-hype", "demo-bn"})


class TestMarketCoherence(unittest.TestCase):
    def test_market_for_profile(self):
        self.assertEqual(market_for_profile({"exchange": "BINANCE"}), "spot")
        self.assertEqual(
            market_for_profile({"exchange": "BINANCE_FUTURES"}), "derivative")
        self.assertEqual(
            market_for_profile({"exchange": "HYPERLIQUID_SWAP"}), "derivative")

    def test_fetch_symbol_binance_needs_usdt(self):
        self.assertEqual(fetch_symbol("binance", "BTC"), "BTCUSDT")
        self.assertEqual(fetch_symbol("binance", "BTCUSDT"), "BTCUSDT")
        self.assertEqual(fetch_symbol("hyperliquid", "HYPE"), "HYPE")

    def test_compute_upsert_exchange_override(self):
        up = grid_adapter.compute_upsert(
            "BTC", "binance", 50000.0, 2.0, 1.0, 10, 0.01, "long",
            "profile-1", "BTCUSDT", exchange_code="BINANCE_FUTURES")
        self.assertEqual(up["exchangeCode"], "BINANCE_FUTURES")
        up2 = grid_adapter.compute_upsert(
            "BTC", "binance", 50000.0, 2.0, 1.0, 10, 0.01, "long",
            "profile-1", "BTCUSDT")
        self.assertEqual(up2["exchangeCode"], "BINANCE")

    def test_grid_create_market_derived_from_exchange(self):
        # keep the temp payload file so we can inspect it (normally unlinked)
        body_holder = {}
        real_unlink = os.unlink

        def keep_unlink(path, *a, **k):
            if path.endswith(".json") and os.path.exists(path):
                try:
                    body_holder[path] = json.load(open(path))
                except Exception:
                    pass
            return real_unlink(path, *a, **k)

        with mock.patch("os.unlink", side_effect=keep_unlink):
            res = grid_adapter.grid_create(
                {"exchangeCode": "BINANCE_FUTURES", "pairCode": "BTCUSDT"},
                "binance", dry_run=True)
        self.assertTrue(res["ok"])
        bodies = [b for b in body_holder.values()
                  if b.get("exchangeCode") == "BINANCE_FUTURES"]
        self.assertTrue(bodies, "payload not written or exchange missing")
        self.assertEqual(bodies[0]["gridMarketHint"], "derivative")


class TestResolveMarketHint(unittest.TestCase):
    """resolve_pair market hint via a fixture cache (offline)."""

    def setUp(self):
        import resolve
        self.resolve = resolve
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._saved = resolve.STATE_DIR
        resolve.STATE_DIR = self.tmp.name
        self.addCleanup(setattr, resolve, "STATE_DIR", self._saved)

    def _cache(self, market, items):
        with open(os.path.join(self.tmp.name, f"market_map-{market}.json"),
                  "w") as fh:
            json.dump({"fetched_at": time.time(), "market": market,
                       "items": items}, fh)

    def test_binance_spot_default_vs_derivative_hint(self):
        self._cache("spot", {"BINANCE:BTCUSDT": {
            "exchange": "BINANCE", "pair": "BTCUSDT", "code": "BTCUSDT",
            "unified": "BTC/USDT"}})
        self._cache("derivative", {"BINANCE_FUTURES:BTCUSDT": {
            "exchange": "BINANCE_FUTURES", "pair": "BTCUSDT",
            "code": "BTCUSDT", "unified": "BTC/USDT"}})
        spot = self.resolve.resolve_pair("binance", "BTC")
        self.assertEqual(spot["pairCode"], "BTCUSDT")
        self.assertEqual(spot["unified"], "BTC/USDT")
        deriv = self.resolve.resolve_pair("binance", "BTC", market="derivative")
        self.assertEqual(deriv["pairCode"], "BTCUSDT")
        self.assertEqual(deriv["unified"], "BTC/USDT")
        # the exchange key the pairCode belongs to differs:
        self.assertEqual(
            self.resolve.market_map("spot")["BINANCE:BTCUSDT"]["exchange"],
            "BINANCE")
        self.assertEqual(
            self.resolve.market_map("derivative")[
                "BINANCE_FUTURES:BTCUSDT"]["exchange"],
            "BINANCE_FUTURES")

    def test_hyperliquid_default_market(self):
        self._cache("derivative", {"HYPERLIQUID_SWAP:159": {
            "exchange": "HYPERLIQUID_SWAP", "pair": "HYPE-USDC",
            "code": "159", "unified": "HYPE-USDC"}})
        r = self.resolve.resolve_pair("hyperliquid", "HYPE")
        self.assertEqual(r["pairCode"], "159")


class TestRotationPass(unittest.TestCase):
    """rescreen_cycle rotation wiring: stagnant incumbent + challenger."""

    def setUp(self):
        import importlib
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state_path = os.path.join(self.tmp.name, "state.json")
        self.patches = [
            mock.patch("daemon.STATE_PATH", state_path),
            mock.patch("daemon.SPECS_DIR",
                       os.path.join(self.tmp.name, "watch", "specs")),
            mock.patch("daemon.deliberate",
                       side_effect=lambda brief: {
                           "decision": "GO", "grid_type": "neutral",
                           "symbol": brief.get("symbol"),
                           "venue": brief.get("venue")}),
            mock.patch("daemon.memories_for_safe",
                       side_effect=lambda brief, k=3: []),
            mock.patch("daemon.record_decision_safe",
                       side_effect=lambda *a, **k: "d1"),
            mock.patch("daemon.record_outcome_safe",
                       side_effect=lambda *a, **k: None),
            mock.patch("daemon.write_run_card_safe",
                       side_effect=lambda *a, **k: None),
            mock.patch("daemon.resolve_pair_safe",
                       side_effect=lambda v, s, market=None: (s, "")),
            mock.patch("daemon.pair_meta",
                       side_effect=lambda v, s, market=None: {"min_cost": 1, "amount_precision": 2}),
            mock.patch("daemon.guard_deploy",
                       side_effect=lambda t, ctx: (True, [])),
            mock.patch("daemon.grid_profiles_safe", side_effect=lambda: PROFILES),
            mock.patch("daemon.reliability_load_safe", side_effect=lambda: {}),
            mock.patch("daemon.grid_status_safe", side_effect=lambda: []),
            mock.patch("daemon.grid_adapter.build_ticket_payloads",
                       side_effect=lambda t, b, bal, m, pc, pcode,
                       exchange_code=None, amount_precision=None,
                       max_affordable_grids=None, min_cost=None,
                       min_grids=None: {"upsert": {"pairCode": pcode},
                                 "grid_bot": {"profit_per_grid_pct": 0.5,
                                              "grids": 10},
                                 "guard_ctx": {"total_commitment": 50.0},
                                 "stagnation_policy": {}}),
            mock.patch("daemon.grid_adapter.grid_create",
                       side_effect=lambda u, v, dry_run=True: {
                           "ok": True, "gridBotCode": "NEWBOT"}),
            mock.patch("daemon.grid_adapter.grid_stop",
                       side_effect=lambda bc, cond="stop_and_close_all",
                       dry_run=True: {"ok": True}),
            mock.patch("daemon.grid_adapter.grid_delete",
                       side_effect=lambda bc, dry_run=True: {"ok": True}),
            mock.patch("daemon.market_regime_fetch",
                       create=True),  # unused marker
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        # fetch_candles is imported inside functions from market_regime
        sys.path.insert(0, os.path.join(
            os.path.dirname(__file__), "..", "..",
            ".agents", "skills", "wundertrading", "scripts"))

    def _make_daemon(self):
        d = daemon.Daemon.__new__(daemon.Daemon)
        d.config = json.loads(json.dumps(daemon.DEFAULT_CONFIG))
        d.config["autonomy"]["paper_profiles"] = {
            "hyperliquid": ["demo-hype"], "binance": ["demo-bn"]}
        d.port = 8799
        d.state = json.loads(json.dumps(daemon.DEFAULT_STATE))
        d.state["slots"] = daemon.slot_plan()["slots"]
        d.profiles = PROFILES
        d.reliability = {}
        d._lock = __import__("threading").Lock()
        d._rescreen_flag = False
        d.top = 5
        return d

    def test_stagnant_incumbent_rotates_to_challenger(self):
        d = self._make_daemon()
        d.state["active_bots"]["1"] = {
            "symbol": "HYPE", "venue": "hyperliquid", "since": "2026-09-01T00:00:00+00:00",
            "bot_code": "OLDBOT", "score_final": 100.0,
            "stagnation_policy": {
                "regime": "chop_high_volatility",
                "stagnant_if": {"min_fills_24h": 5.0, "min_realized_ratio": 0.4}},
            "observed": {"fills_24h": 0.0, "realized_ratio": 0.0,
                         "status": "active", "price": 86.0},
            "channel": {"low": 80.0, "mid": 86.0, "high": 92.0, "step_pct": 0.5},
            "decision_id": "d-old",
        }
        cands = [
            {"venue": "hyperliquid", "symbol": "SOL",
             "tv_symbol": "BINANCE:SOLUSDT", "regime": "chop_high_volatility",
             "score_final": 120.0, "step": 0.5},
        ]
        with mock.patch("daemon.run_merge",
                        return_value={"results": cands}), \
             mock.patch("daemon.observe_all_safe",
                        side_effect=lambda bots: {
                            "1": {"fills_24h": 0.0, "realized_ratio": 0.0}}):
            d.rescreen_cycle(dry_run=True, max_new=2, top=5)
        self.assertNotIn("1", d.state["active_bots"] or {},
                         "stagnant incumbent slot should be rotated out")
        # cooldown set for the rotated symbol
        self.assertIn("hyperliquid:HYPE", d.state["cooldowns_until"])
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertIn("rotate", kinds)

    def test_healthy_incumbent_not_rotated(self):
        d = self._make_daemon()
        d.state["active_bots"]["1"] = {
            "symbol": "HYPE", "venue": "hyperliquid", "since": "2026-09-01T00:00:00+00:00",
            "bot_code": "OLDBOT", "score_final": 130.0,
            "stagnation_policy": {
                "regime": "chop_high_volatility",
                "stagnant_if": {"min_fills_24h": 5.0, "min_realized_ratio": 0.4}},
            "observed": {"fills_24h": 20.0, "realized_ratio": 0.9,
                         "status": "active", "price": 86.0},
            "channel": {"low": 80.0, "mid": 86.0, "high": 92.0, "step_pct": 0.5},
        }
        cands = [
            {"venue": "hyperliquid", "symbol": "SOL",
             "tv_symbol": "BINANCE:SOLUSDT", "regime": "chop_high_volatility",
             "score_final": 120.0, "step": 0.5},
        ]
        with mock.patch("daemon.run_merge",
                        return_value={"results": cands}), \
             mock.patch("daemon.observe_all_safe",
                        side_effect=lambda bots: {
                            "1": {"fills_24h": 20.0, "realized_ratio": 0.9}}):
            d.rescreen_cycle(dry_run=True, max_new=2, top=5)
        self.assertIn("1", d.state["active_bots"])
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertNotIn("rotate", kinds)

    def test_force_rotate_flag_triggers_rotation(self):
        d = self._make_daemon()
        d.state["active_bots"]["1"] = {
            "symbol": "HYPE", "venue": "hyperliquid", "since": "2026-09-01T00:00:00+00:00",
            "bot_code": "OLDBOT", "score_final": 130.0,
            "force_rotate": True,
            "stagnation_policy": {"regime": "chop_high_volatility"},
            "observed": {"fills_24h": 20.0, "realized_ratio": 0.9},
            "channel": {"low": 80.0, "mid": 86.0, "high": 92.0, "step_pct": 0.5},
        }
        cands = [
            {"venue": "hyperliquid", "symbol": "SOL",
             "tv_symbol": "BINANCE:SOLUSDT", "regime": "chop_high_volatility",
             "score_final": 100.0, "step": 0.5},
        ]
        with mock.patch("daemon.run_merge",
                        return_value={"results": cands}), \
             mock.patch("daemon.observe_all_safe", side_effect=lambda bots: {}):
            d.rescreen_cycle(dry_run=True, max_new=2, top=5)
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertIn("rotate", kinds)


if __name__ == "__main__":
    unittest.main()


class TestAffordabilityFit(unittest.TestCase):
    """max_affordable_grids widens the step so the channel fits the budget."""

    def test_widened_step_fits_budget(self):
        ticket = {"symbol": "SOL", "venue": "hyperliquid", "grid_type": "neutral",
                  "regime": "chop_high_volatility", "step_mult": 1.0,
                  "max_alloc_mult": 1.0}
        brief = {"metrics": {"price": 100.0, "atr_pct": 2.0, "adx14": 20.0,
                             "rsi14": 50.0, "bb_width_pctile": 50.0},
                 "evidence": {}, "spread_pct": 0.01}
        # baseline: ATR band ±6% at step ~1% ≈ 12 lines, alloc $3 → tiny/grid
        base = grid_adapter.build_ticket_payloads(
            ticket, brief, 150.0, 0.02, "prof", "5")
        base_grids = base["grid_bot"]["grids"]
        self.assertGreaterEqual(base_grids, 8)
        self.assertLess(base["grid_bot"]["sizing"]["usd_per_grid"], 10.0)
        # afford at most 7 lines at $10 each within the 50% cap
        fit = grid_adapter.build_ticket_payloads(
            ticket, brief, 150.0, 0.50, "prof", "5",
            max_affordable_grids=7)
        g = fit["grid_bot"]
        self.assertLessEqual(g["grids"], 7)
        self.assertGreaterEqual(g["sizing"]["usd_per_grid"], 10.0)
        # commitment stays within the allocation
        self.assertLessEqual(g["sizing"]["total_commitment_estimate"], 75.01)
        # the upsert carries the same geometry
        self.assertEqual(fit["upsert"]["amountPerTrade"],
                         g["sizing"]["amount_per_trade"])

    def test_no_fit_when_min_grids_impossible(self):
        ticket = {"symbol": "SOL", "venue": "hyperliquid", "grid_type": "neutral",
                  "regime": "chop_high_volatility", "step_mult": 1.0,
                  "max_alloc_mult": 1.0}
        brief = {"metrics": {"price": 100.0, "atr_pct": 2.0, "adx14": 20.0,
                             "rsi14": 50.0, "bb_width_pctile": 50.0},
                 "evidence": {}, "spread_pct": 0.01}
        # fit_grids below min_grids: keep original geometry (daemon vetoes)
        fit = grid_adapter.build_ticket_payloads(
            ticket, brief, 150.0, 0.02, "prof", "5",
            max_affordable_grids=2)
        self.assertGreaterEqual(fit["grid_bot"]["grids"], 5)


class TestDensityFirstSizing(unittest.TestCase):
    """User directive: grid density is the profit driver — funds follow the
    strategy. A tier allocation below the exchange minimum must RAISE the
    per-line funding (more funds) instead of dropping grid lines, and the
    risk cap applies to the one-sided worst case, not all-lines notional."""

    def test_min_cost_bumps_funds_not_drops_lines(self):
        ticket = {"symbol": "SOL", "venue": "hyperliquid", "grid_type": "neutral",
                  "regime": "chop_high_volatility", "step_mult": 1.0,
                  "max_alloc_mult": 1.0}
        brief = {"metrics": {"price": 100.0, "atr_pct": 2.0, "adx14": 20.0,
                             "rsi14": 50.0, "bb_width_pctile": 50.0},
                 "evidence": {}, "spread_pct": 0.01}
        # tier 25% of $150 = $37.5 across ~12 lines = $3.1/line < $10 min
        p = grid_adapter.build_ticket_payloads(
            ticket, brief, 150.0, 0.25, "prof", "5", min_cost=10.0)
        g = p["grid_bot"]
        self.assertGreaterEqual(g["grids"], 8)          # density preserved
        self.assertGreaterEqual(g["sizing"]["usd_per_grid"], 10.0)  # funded to min
        # one-sided worst case ≈ half the channel, within the 50% cap
        self.assertLessEqual(g["sizing"]["total_commitment_estimate"], 75.01)
        # guard bound covers the honest worst case (risk mult cannot shrink
        # it below what min_cost funding actually commits)
        self.assertGreaterEqual(p["guard_ctx"]["max_alloc"],
                                g["sizing"]["total_commitment_estimate"] / 150.0 - 1e-9)

    def test_worst_case_is_one_sided(self):
        ticket = {"symbol": "SOL", "venue": "hyperliquid", "grid_type": "long",
                  "regime": "trend_up", "step_mult": 1.0, "max_alloc_mult": 1.0}
        brief = {"metrics": {"price": 100.0, "atr_pct": 2.0, "adx14": 20.0,
                             "rsi14": 50.0, "bb_width_pctile": 50.0},
                 "evidence": {}, "spread_pct": 0.01}
        p = grid_adapter.build_ticket_payloads(
            ticket, brief, 150.0, 0.50, "prof", "5", min_cost=10.0)
        s = p["grid_bot"]["sizing"]
        self.assertEqual(s["side_lines"], (p["grid_bot"]["grids"] + 1) // 2)
        self.assertAlmostEqual(
            s["total_commitment_estimate"],
            round(s["usd_per_grid"] * s["side_lines"], 2), places=1)
        self.assertLess(s["total_commitment_estimate"], s["distributed_notional"])


class TestMinHoldFloor(unittest.TestCase):
    """Bots younger than policy.min_hold_h are never rotated (churn guard)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # hermetic: no profile snapshot, no candles, no spec/journal writes —
        # the deploy pass must veto on the empty profile allowlist so only
        # the rotation pass is exercised
        self.patches = [
            mock.patch("daemon.STATE_PATH",
                       os.path.join(self.tmp.name, "state.json")),
            mock.patch("daemon.SPECS_DIR",
                       os.path.join(self.tmp.name, "watch", "specs")),
            mock.patch("daemon.grid_profiles_safe", side_effect=lambda: []),
            mock.patch("daemon.deliberate",
                       side_effect=lambda brief: {
                           "decision": "GO", "grid_type": "neutral",
                           "symbol": brief.get("symbol"),
                           "venue": brief.get("venue")}),
            mock.patch("daemon.memories_for_safe",
                       side_effect=lambda brief, k=3: []),
            mock.patch("daemon.record_decision_safe",
                       side_effect=lambda *a, **k: "d1"),
            mock.patch("daemon.record_outcome_safe",
                       side_effect=lambda *a, **k: None),
            mock.patch("daemon.write_run_card_safe",
                       side_effect=lambda *a, **k: None),
            mock.patch("market_regime.fetch_candles",
                       side_effect=lambda *a, **k: []),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def _daemon_with_bot(self, since_iso):
        import threading
        d = daemon.Daemon.__new__(daemon.Daemon)
        d.config = json.loads(json.dumps(daemon.DEFAULT_CONFIG))
        d.port = 8799
        d.state = json.loads(json.dumps(daemon.DEFAULT_STATE))
        d.state["slots"] = daemon.slot_plan()["slots"]
        d.state["active_bots"]["1"] = {
            "symbol": "HYPE", "venue": "hyperliquid", "since": since_iso,
            "bot_code": "OLDBOT", "score_final": 100.0,
            "stagnation_policy": {
                "regime": "chop_high_volatility",
                "stagnant_if": {"min_fills_24h": 5.0, "min_realized_ratio": 0.4}},
            "observed": {"fills_24h": 0.0, "realized_ratio": 0.0},
            "channel": {"low": 80.0, "mid": 86.0, "high": 92.0, "step_pct": 0.5},
        }
        d.profiles = []
        d.reliability = {}
        d._lock = threading.Lock()
        d._rescreen_flag = False
        d.top = 5
        return d

    def test_young_bot_not_rotated_even_if_stagnant(self):
        from datetime import datetime, timezone, timedelta
        fresh = (datetime.now(timezone.utc) - timedelta(hours=1)) \
            .isoformat(timespec="seconds")
        d = self._daemon_with_bot(fresh)
        cands = [{"venue": "hyperliquid", "symbol": "SOL",
                  "tv_symbol": "BINANCE:SOLUSDT", "regime": "chop_high_volatility",
                  "score_final": 130.0, "step": 0.5}]
        with mock.patch("daemon.run_merge", return_value={"results": cands}), \
             mock.patch("daemon.observe_all_safe", side_effect=lambda b: {}), \
             mock.patch.object(d, "execute_rotation",
                        side_effect=lambda *a, **k: self.fail("rotated a 1h-old bot")):
            d.rescreen_cycle(dry_run=True, max_new=1, top=5)

    def test_old_stagnant_bot_can_rotate(self):
        d = self._daemon_with_bot("2026-09-01T00:00:00+00:00")
        cands = [{"venue": "hyperliquid", "symbol": "SOL",
                  "tv_symbol": "BINANCE:SOLUSDT", "regime": "chop_high_volatility",
                  "score_final": 130.0, "step": 0.5}]
        rotated = []
        with mock.patch("daemon.run_merge", return_value={"results": cands}), \
             mock.patch("daemon.observe_all_safe", side_effect=lambda b: {}), \
             mock.patch.object(d, "execute_rotation",
                        side_effect=lambda sk, ch, dry_run=True:
                        rotated.append(sk) or True):
            d.rescreen_cycle(dry_run=True, max_new=1, top=5)
        self.assertEqual(rotated, ["1"])
