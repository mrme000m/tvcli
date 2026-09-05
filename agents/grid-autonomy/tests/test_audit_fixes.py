#!/usr/bin/env python3
"""Regression tests for the 2026-09-05 audit fixes.

Covers:
  HIGH-1  archetype kill-flag sample floor — one losing round-trip on a
          fresh archetype must not permanently ban the regime
  HIGH-2  canonical reliability-ledger keys + startup migration + archive
          re-keying (adopted bots used to write regime names while fresh
          deploys wrote archetype labels)
  MEDIUM-3 tier-cap snapshot freshness within one rescreen cycle (the
          pre-check must count bots this daemon created earlier in the
          same cycle)
  MEDIUM-4 transition-guarded health-cycle journaling — `stagnant` and
          `re-analysis` log once per state change, not once per 60 s sweep
  MEDIUM-5 active_keys refresh after in-cycle rotations (no duplicate
          challenger when two slots rotate in one cycle)
  LOW     deployed-step decision lineage, deploy-failed outcome closing +
          memory exclusion
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="grid-audit-fixes-")
os.environ["GRID_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "execution"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "policy"))

import daemon  # noqa: E402
import reflect  # noqa: E402
import reliability_grid as rg  # noqa: E402
from tests.test_daemon_manage import (ManageHarness,  # noqa: E402
                                      make_payloads)


def trade(pnl, close, sid="x"):
    return {"pnl_usd": pnl, "close_ts": close, "entered_at": None,
            "strategy_id": sid}


# ── HIGH-1: kill-flag sample floor ─────────────────────────────────────
class TestKillFlagSampleFloor(unittest.TestCase):
    ARCH = "Neutral Grid (mean-reversion)"

    def test_single_losing_trip_does_not_kill(self):
        rel = {self.ARCH: {"samples": 1, "recent_pf": 0.0}}
        self.assertFalse(daemon.refuse_new_archetype(rel, self.ARCH))

    def test_kill_binds_with_enough_samples(self):
        rel = {self.ARCH: {"samples": 12, "recent_pf": 0.4}}
        self.assertTrue(daemon.refuse_new_archetype(rel, self.ARCH))

    def test_no_samples_key_is_no_signal(self):
        # stats dicts written before the floor existed may lack "samples"
        rel = {self.ARCH: {"recent_pf": 0.9}}
        self.assertFalse(daemon.refuse_new_archetype(rel, self.ARCH))

    def test_min_samples_override(self):
        rel = {"X": {"samples": 5, "recent_pf": 0.0}}
        self.assertTrue(daemon.refuse_new_archetype(rel, "X", min_samples=5))
        self.assertFalse(daemon.refuse_new_archetype(rel, "X", min_samples=6))

    def test_healthy_recent_pf_never_kills(self):
        rel = {self.ARCH: {"samples": 40, "recent_pf": 2.0}}
        self.assertFalse(daemon.refuse_new_archetype(rel, self.ARCH))


# ── HIGH-2: canonical ledger keys + migration ───────────────────────────
class TestLedgerKeyCanonical(ManageHarness):
    def test_ledger_key_maps_regimes_to_labels(self):
        self.assertEqual(rg.ledger_key("chop_high_volatility"),
                         "Neutral Grid (mean-reversion)")
        self.assertEqual(rg.ledger_key("trend_up"),
                         "Long Grid / classic LONG")
        self.assertEqual(rg.ledger_key("Neutral Grid (mean-reversion)"),
                         "Neutral Grid (mean-reversion)")  # passthrough
        self.assertEqual(rg.ledger_key(None), "unknown")

    def test_archive_trades_canonicalizes_key(self):
        # the real archive_trades (daemon's is stubbed by ManageTestCase)
        self.assertTrue(rg.archive_trades([trade(1.0, 1)], "trend_up"))
        arch = rg.archived_by_archetype()
        self.assertIn("Long Grid / classic LONG", arch)
        self.assertNotIn("trend_up", arch)

    def test_normalize_archive_rekeys_and_merges(self):
        with open(rg.ARCHIVE_PATH, "w", encoding="utf-8") as fh:
            json.dump({"chop_high_volatility": [trade(1.0, 1), trade(2.0, 2)],
                       "Neutral Grid (mean-reversion)": [trade(3.0, 3)]}, fh)
        self.assertTrue(rg.normalize_archive())
        with open(rg.ARCHIVE_PATH, encoding="utf-8") as fh:
            out = json.load(fh)
        self.assertEqual(sorted(out.keys()),
                         ["Neutral Grid (mean-reversion)"])
        self.assertEqual(len(out["Neutral Grid (mean-reversion)"]), 3)
        self.assertFalse(rg.normalize_archive())  # idempotent

    def test_startup_migration_rekeys_state_and_ledger(self):
        self.reliability = {
            "chop_high_volatility": {"samples": 7, "profit_factor": 1.1,
                                     "recent_pf": 1.2},
            "unknown": {"samples": 2},
        }
        d = self.make_daemon()
        d.reliability = dict(self.reliability)
        d.state["reliability"] = d.reliability
        d.state["active_bots"] = {
            "1": {"symbol": "ARB", "venue": "hyperliquid",
                  "archetype": "chop_high_volatility"}}
        d._migrate_archetype_keys()
        self.assertEqual(
            d.reliability.get("Neutral Grid (mean-reversion)",
                              {}).get("samples"), 7)
        self.assertNotIn("chop_high_volatility", d.reliability)
        self.assertEqual(d.state["active_bots"]["1"]["archetype"],
                         "Neutral Grid (mean-reversion)")
        self.assertTrue(any(e.get("kind") == "reliability-migrate"
                            for e in d.state["journal"]))

    def test_duplicate_alias_drops_in_favor_of_canonical(self):
        self.reliability = {
            "chop_high_volatility": {"samples": 2},
            "Neutral Grid (mean-reversion)": {"samples": 9},
        }
        d = self.make_daemon()
        d.reliability = dict(self.reliability)
        d.state["reliability"] = d.reliability
        d._migrate_archetype_keys()
        self.assertEqual(
            d.reliability["Neutral Grid (mean-reversion)"]["samples"], 9)
        self.assertNotIn("chop_high_volatility", d.reliability)


# ── MEDIUM-3: capacity snapshot freshness ──────────────────────────────
class TestCapacitySnapshotFreshness(ManageHarness):
    def _cap(self):
        return {"max_active": {"other": 1, "premium": 200},
                "active": {"other": 0, "premium": {"HYPERLIQUID_SWAP": 0}},
                "used_pairs": {}}

    def test_note_deploy_blocks_second_candidate_same_cycle(self):
        d = self.make_daemon()
        cap = self._cap()
        d._capacity_note_deploy(
            cap, {"venue": "binance", "symbol": "ARB"},
            {"upsert": {"pairCode": "ARBUSDT"}})
        blocked = d.venue_capacity_block(
            {"venue": "binance", "symbol": "NEAR"}, cap)
        self.assertTrue(blocked)
        self.assertIn("plan cap", blocked)

    def test_note_deploy_records_used_pair(self):
        d = self.make_daemon()
        cap = self._cap()
        d._capacity_note_deploy(
            cap, {"venue": "binance", "symbol": "ARB"},
            {"upsert": {"pairCode": "ARBUSDT"}})
        with mock.patch("daemon.resolve_pair_safe",
                       lambda venue, symbol, market=None: ("ARBUSDT", "")):
            blocked = d.venue_capacity_block(
                {"venue": "binance", "symbol": "ARB"}, cap)
        self.assertTrue(blocked)
        self.assertIn("already", blocked)

    def test_rescreen_prechecks_after_own_deploy(self):
        # live regression shape: slot 3 ARB deployed, slot 4 NEAR create
        # then 400'd "Maximum number of Grid Bots reached" — the snapshot
        # must count the bot this daemon created earlier in the cycle
        d = self.make_daemon()
        cap = self._cap()
        cands = [
            {"venue": "binance", "symbol": "ARB",
             "tv_symbol": "BINANCE:ARBUSDT", "regime": "neutral",
             "score_final": 90.0, "step": 0.5,
             "archetype": "Neutral Grid (mean-reversion)"},
            {"venue": "binance", "symbol": "NEAR",
             "tv_symbol": "BINANCE:NEARUSDT", "regime": "neutral",
             "score_final": 80.0, "step": 0.5,
             "archetype": "Neutral Grid (mean-reversion)"},
        ]
        with mock.patch("daemon.run_merge",
                        return_value={"results": cands}),                 mock.patch("daemon.grid_capacity_safe", return_value=cap),                 mock.patch("daemon.resolve_pair_safe",
                           side_effect=lambda v, s, market=None: (s, "")):
            d.rescreen_cycle(dry_run=False, max_new=2, top=5)
        creates = [op for op in self.ops if op[0] == "create"]
        self.assertEqual(len(creates), 1)  # only ARB
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertIn("capacity-veto", kinds)
        self.assertNotIn("deploy-failed", kinds)
        self.assertEqual(sorted(d.state["active_bots"]), ["3"])


# ── MEDIUM-4: transition-guarded health-cycle journaling ───────────────
class TestHealthCycleJournaling(ManageHarness):
    def _bot(self):
        return {
            "symbol": "ARB", "venue": "hyperliquid", "bot_code": "B1",
            "since": "2026-09-01T00:00:00+00:00",
            "ticket": {"grid_type": "neutral", "decision": "GO",
                       "regime": "neutral"},
            "archetype": "Neutral Grid (mean-reversion)",
            "stagnation_policy": {
                "regime": "neutral",
                "stagnant_if": {"min_fills_24h": 5.0,
                                "min_realized_ratio": 0.4}},
            "channel": {"low": 0.9, "mid": 1.0, "high": 1.1,
                        "step_pct": 0.5, "grids": 10},
        }

    def _obs(self, **over):
        base = {"status": "active", "price": 1.0, "fills_24h": 0,
                "realized_ratio": 0.0, "unrealized_pnl": 0.0,
                "ladder_full": False, "dd_vs_atr_band": 0.0,
                "regime_now": "neutral", "score_drop": 0.0}
        base.update(over)
        return {"1": base}

    def _health(self, d, obs, n=1):
        with mock.patch.object(daemon, "cdp_alive", lambda *a: True),                 mock.patch.object(daemon, "observe_all_safe",
                                  return_value=obs):
            for _ in range(n):
                d.health_cycle(dry_run=True)

    def test_stagnant_logged_once_per_transition(self):
        d = self.make_daemon()
        d.state["active_bots"]["1"] = self._bot()
        self._health(d, self._obs(), n=3)
        stag = [e for e in d.state["journal"]
                if e.get("kind") == "stagnant"]
        self.assertEqual(len(stag), 1)
        # recovers → marker cleared → goes stagnant again → one more line
        self._health(d, self._obs(fills_24h=50, realized_ratio=0.9), n=2)
        self._health(d, self._obs(), n=2)
        stag = [e for e in d.state["journal"]
                if e.get("kind") == "stagnant"]
        self.assertEqual(len(stag), 2)

    def test_reanalysis_logged_once_per_transition(self):
        d = self.make_daemon()
        d.state["active_bots"]["1"] = self._bot()
        self._health(d, self._obs(status="stopped"), n=3)
        rean = [e for e in d.state["journal"]
                if e.get("kind") == "re-analysis"]
        self.assertEqual(len(rean), 1)
        self.assertTrue(d.state["active_bots"]["1"]["needs_reanalysis"])
        # back running → cleared; stopped again → one more line
        self._health(d, self._obs(), n=1)
        self.assertNotIn("needs_reanalysis",
                         d.state["active_bots"]["1"])
        self._health(d, self._obs(status="stopped"), n=2)
        rean = [e for e in d.state["journal"]
                if e.get("kind") == "re-analysis"]
        self.assertEqual(len(rean), 2)

    def test_reliability_flag_logged_once(self):
        d = self.make_daemon()
        d.state["active_bots"]["1"] = self._bot()
        d.reliability = {"Neutral Grid (mean-reversion)":
                         {"samples": 20, "recent_pf": 0.3}}
        self._health(d, self._obs(fills_24h=50, realized_ratio=0.9), n=3)
        flags = [e for e in d.state["journal"]
                 if e.get("kind") == "reliability-flag"]
        self.assertEqual(len(flags), 1)


# ── MEDIUM-5: no duplicate challenger within one cycle ─────────────────
class TestRotationChallengerFreshness(ManageHarness):
    def _stale_bot(self, sym, code):
        return {
            "symbol": sym, "venue": "hyperliquid", "bot_code": code,
            "since": "2026-09-01T00:00:00+00:00",
            "score_final": 40.0,
            "ticket": {"grid_type": "neutral", "decision": "GO",
                       "regime": "neutral"},
            "archetype": "Neutral Grid (mean-reversion)",
            "stagnation_policy": {
                "regime": "neutral",
                "stagnant_if": {"min_fills_24h": 5.0,
                                "min_realized_ratio": 0.4},
                "cooldown_h": 24.0, "hysteresis_score": 5.0},
            "observed": {"status": "active", "price": 1.0, "fills_24h": 0,
                         "realized_ratio": 0.0, "ladder_full": False,
                         "dd_vs_atr_band": 0.0, "regime_now": "neutral",
                         "score_drop": 0.0},
        }

    def test_two_rotations_never_pick_the_same_challenger(self):
        d = self.make_daemon()
        d.state["active_bots"]["1"] = self._stale_bot("PUMP", "OLD1")
        d.state["active_bots"]["2"] = self._stale_bot("HYPE", "OLD2")
        cands = [{"venue": "hyperliquid", "symbol": "SOL",
                  "tv_symbol": "BINANCE:SOLUSDT", "regime": "neutral",
                  "score_final": 90.0, "step": 0.5,
                  "archetype": "Neutral Grid (mean-reversion)"}]
        with mock.patch("daemon.run_merge",
                        return_value={"results": cands}),                 mock.patch("daemon.resolve_pair_safe",
                           side_effect=lambda v, s, market=None: (s, "")),                 mock.patch("daemon.grid_capacity_safe", return_value={}),                 mock.patch("daemon.time.sleep", lambda s: None):
            d.rescreen_cycle(dry_run=False, max_new=0, top=5)
        creates = [op for op in self.ops if op[0] == "create"]
        self.assertEqual(len(creates), 1)  # SOL created exactly once
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertIn("rotation-skip", kinds)
        self.assertNotIn("deploy-failed", kinds)
        # exactly one slot holds SOL; the other stale bot survived
        syms = [b["symbol"] for b in d.state["active_bots"].values()]
        self.assertEqual(sorted(syms), ["HYPE", "SOL"])


# ── LOW: decision lineage + deploy-failed outcomes ─────────────────────
class TestDecisionLineage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old = os.environ.get("GRID_STATE_DIR")
        os.environ["GRID_STATE_DIR"] = self.tmp.name
        # reflect resolves the state dir at CALL time

    def tearDown(self):
        if self._old is not None:
            os.environ["GRID_STATE_DIR"] = self._old

    def _read(self):
        with open(reflect._decisions_path()) as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def test_deployed_step_wins_over_screen_step(self):
        ticket = {"symbol": "ARB", "venue": "binance", "decision": "GO",
                  "grid_type": "neutral", "regime": "neutral"}
        brief = {"symbol": "ARB", "venue": "binance", "slot": {"slot": 3},
                 "regime": "neutral", "step": 0.5,
                 "metrics": {"atr_pct": 2.0, "price": 1.0}}
        payloads = make_payloads(step_pct=1.2)
        did = reflect.record_decision(ticket, brief, "deploy", payloads)
        line = [l for l in self._read() if l["id"] == did][0]
        self.assertEqual(line["step_pct"], 1.2)      # deployed step
        self.assertEqual(line["step_pct_screen"], 0.5)  # screen lineage

    def test_screen_step_fallback_without_payloads(self):
        ticket = {"symbol": "ARB", "venue": "binance", "decision": "GO",
                  "grid_type": "neutral", "regime": "neutral"}
        did = reflect.record_decision(
            ticket, {"symbol": "ARB", "venue": "binance", "step": 0.7},
            "adopted")
        line = [l for l in self._read() if l["id"] == did][0]
        self.assertEqual(line["step_pct"], 0.7)

    def test_deploy_failed_outcome_closes_line_and_stays_out_of_memory(self):
        d = daemon.Daemon.__new__(daemon.Daemon)  # no init side effects
        ticket = {"symbol": "ZZARB", "venue": "binance", "decision": "GO",
                  "grid_type": "neutral", "regime": "neutral"}
        brief = {"symbol": "ZZARB", "venue": "binance", "slot": {"slot": 3},
                 "regime": "neutral", "step": 0.5,
                 "metrics": {"atr_pct": 2.0, "price": 1.0}}
        payloads = make_payloads()
        did = reflect.record_decision(ticket, brief, "deploy", payloads)
        action = {"kind": "DEPLOY-PAPER", "slot": 3, "venue": "binance",
                  "symbol": "ZZARB", "decision_id": did,
                  "profile": "profile-2"}
        cand = {"venue": "binance", "symbol": "ZZARB", "regime": "neutral",
                "archetype": "Neutral Grid (mean-reversion)"}
        d.state = {"active_bots": {}, "committed": {}, "journal": []}
        slot = {"slot": 3, "venue": "binance", "balance": 200.0}
        with mock.patch("daemon.grid_adapter.grid_create",
                        return_value={"ok": False,
                                      "stderr": "Maximum number of Grid "
                                                "Bots reached"}), \
                mock.patch("daemon.time.sleep", lambda s: None):
            d.commit_deploy(action, ticket, payloads, brief, cand, slot,
                            dry_run=False)
        self.assertEqual(action["kind"], "deploy-failed")
        line = [l for l in self._read() if l["id"] == did][0]
        self.assertEqual(line["outcome"]["reason"], "deploy-failed")
        self.assertIsNone(line["outcome"]["realized_pnl"])
        # closed but never injected as a swarm memory (no market content)
        self.assertEqual(
            reflect.memories_for({"symbol": "ZZARB", "venue": "binance"}),
            [])


if __name__ == "__main__":
    unittest.main()
