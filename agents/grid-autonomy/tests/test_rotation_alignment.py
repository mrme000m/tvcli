#!/usr/bin/env python3
"""Rotation double-evaluation alignment tests.

The rescreen rotation pass pre-checks stagnation with the incumbent's OWN
fresh regime + score decay, then execute_rotation re-checks it via
should_rotate with CHALLENGER-relative inputs (candidate regime +
inc_score − cand_score). When the challenger's regime matches the policy
regime and fills/realized are healthy, the re-check vetoes with "incumbent
healthy" even though the incumbent genuinely decayed — live evidence: 45
`rotation-veto: incumbent healthy` journal entries in ~7h on healthy-fills
incumbents (ARB 6 fills, JUP 12, NEAR 19), one per rescreen cycle.

The fix threads the rescreen pre-check verdict through
execute_rotation → should_rotate (stag_ok / stag_reasons /
inc_score_fresh) while keeping the Δscore hysteresis gate and every
loss-veto rule intact. These tests pin that contract.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "policy"))

import daemon  # noqa: E402  (path set up before the import below)

try:
    from test_daemon_manage import ManageHarness  # noqa: F401
except ImportError:  # pytest rootdir=agents/grid-autonomy
    from tests.test_daemon_manage import ManageHarness  # noqa: F401

from daemon import should_rotate  # noqa: E402

POLICY = {"regime": "chop_high_volatility", "hysteresis_score": 5.0,
          "score_drop_rotate": 12.0,
          "stagnant_if": {"min_fills_24h": 3.0, "min_realized_ratio": 0.4,
                          "window_h": 48}}
INC = {"venue": "hyperliquid", "symbol": "PUMP", "score_final": 100.0}
# the rescreen pre-check's stagnation reasons: the incumbent's OWN fresh
# entry flipped regime + decayed score (score_drop 15 > score_drop_rotate 12)
STAG_REASONS = ["regime chop_high_volatility→trending + score -15.0"]


class TestShouldRotateStagOk(unittest.TestCase):
    """stag_ok=True: stagnation is already proven by the rescreen
    pre-check (incumbent's own fresh regime + score decay), so the internal
    is_stagnant re-check is skipped — the Δscore hysteresis gate still
    applies against the incumbent's FRESH score."""

    # healthy fills + challenger regime matching the policy regime — exactly
    # the inputs that made the OLD re-check veto "incumbent healthy"
    OBS_HEALTHY = {"fills_24h": 12.0, "realized_ratio": 0.9}
    CAND = {"regime": "chop_high_volatility", "score_final": 105.0}

    def test_approves_when_fresh_score_beaten_by_hysteresis(self):
        ok, reasons = should_rotate(
            self.CAND, INC, POLICY, self.OBS_HEALTHY, 0,
            stag_ok=True, stag_reasons=STAG_REASONS, inc_score_fresh=85.0)
        self.assertTrue(ok)
        joined = "; ".join(reasons)
        self.assertNotIn("incumbent healthy", joined)
        # the rescreen's stagnation reasons carry through verbatim
        self.assertIn("regime chop_high_volatility→trending", joined)
        self.assertIn("Δscore 20.0", joined)

    def test_vetoes_with_dscore_when_below_hysteresis(self):
        ok, reasons = should_rotate(
            {"regime": "chop_high_volatility", "score_final": 89.0},
            INC, POLICY, self.OBS_HEALTHY, 0,
            stag_ok=True, stag_reasons=STAG_REASONS, inc_score_fresh=85.0)
        self.assertFalse(ok)
        joined = "; ".join(reasons)
        self.assertIn("Δscore 4.0", joined)
        self.assertIn("< hysteresis", joined)
        self.assertNotIn("incumbent healthy", joined)

    def test_uses_inc_score_fresh_for_the_gate(self):
        # stale stored score (100) would approve (Δ+19); the FRESH score
        # (95) vetoes (Δ+4) — proves the fresh score drives the gate
        ok, reasons = should_rotate(
            {"regime": "chop_high_volatility", "score_final": 99.0},
            INC, POLICY, self.OBS_HEALTHY, 0,
            stag_ok=True, stag_reasons=STAG_REASONS, inc_score_fresh=95.0)
        self.assertFalse(ok)
        self.assertIn("< hysteresis", "; ".join(reasons))
        # and the reverse: stale 100 would veto (Δ−5), fresh 90 approves
        # (Δ+5 exactly at the gate) — fresh score beats stale in BOTH
        # directions
        ok, reasons = should_rotate(
            {"regime": "chop_high_volatility", "score_final": 95.0},
            INC, POLICY, self.OBS_HEALTHY, 0,
            stag_ok=True, stag_reasons=STAG_REASONS, inc_score_fresh=90.0)
        self.assertTrue(ok)
        self.assertIn("Δscore 5.0", "; ".join(reasons))

    def test_stag_ok_still_obeys_hysteresis_without_needs_reanalysis(self):
        # needs_reanalysis waives the gate; stag_ok alone does NOT — a
        # challenger Δ+2 below the gate is vetoed even though stagnation
        # was pre-proven
        ok, reasons = should_rotate(
            {"regime": "chop_high_volatility", "score_final": 87.0},
            INC, POLICY, self.OBS_HEALTHY, 0,
            stag_ok=True, stag_reasons=STAG_REASONS, inc_score_fresh=85.0)
        self.assertFalse(ok)
        self.assertIn("< hysteresis", "; ".join(reasons))

    def test_empty_stag_reasons_still_rotates_on_stag_ok(self):
        ok, reasons = should_rotate(
            self.CAND, INC, POLICY, self.OBS_HEALTHY, 0,
            stag_ok=True, stag_reasons=[], inc_score_fresh=85.0)
        self.assertTrue(ok)
        self.assertIn("Δscore 20.0", "; ".join(reasons))


class TestShouldRotateDefaultPath(unittest.TestCase):
    """stag_ok=None/False must behave EXACTLY as before — callers that do
    not pass the pre-check verdict keep the challenger-relative re-check
    (and its "incumbent healthy" veto for genuinely healthy incumbents)."""

    def test_healthy_incumbent_still_vetoed_incumbent_healthy(self):
        ok, reasons = should_rotate(
            {"regime": "chop_high_volatility", "score_final": 120.0},
            INC, POLICY,
            {"fills_24h": 12.0, "realized_ratio": 0.9}, 0)
        self.assertFalse(ok)
        self.assertEqual(reasons, ["incumbent healthy"])

    def test_stag_ok_false_identical_to_default(self):
        # stagnant fills + challenger Δ+10: both paths approve with the
        # same reasons — stag_ok=False ignores the pre-check inputs
        cand = {"regime": "chop_high_volatility", "score_final": 110.0}
        obs = {"fills_24h": 0.0, "realized_ratio": 0.0}
        ok_def, reasons_def = should_rotate(cand, INC, POLICY, obs, 0)
        ok_false, reasons_false = should_rotate(
            cand, INC, POLICY, obs, 0,
            stag_ok=False, stag_reasons=STAG_REASONS)
        self.assertTrue(ok_def)
        self.assertEqual(ok_false, ok_def)
        self.assertEqual(reasons_false, reasons_def)
        self.assertIn("fills 0.0", "; ".join(reasons_false))

    def test_default_path_uses_stored_score_when_fresh_absent(self):
        # inc_score_fresh defaults to None → the stored score drives the
        # gate (adopted bots carry score_final=None → 0, no crash)
        ok, reasons = should_rotate(
            {"regime": "chop_high_volatility", "score_final": 110.0},
            {"venue": "hyperliquid", "symbol": "PUMP", "score_final": None},
            POLICY, {"fills_24h": 0.0, "realized_ratio": 0.0}, 0)
        self.assertTrue(ok)
        self.assertIn("Δscore 110.0", "; ".join(reasons))


class TestRescreenRotationAlignment(ManageHarness):
    """End-to-end rescreen rotation pass: a HEALTHY-FILLS incumbent whose
    OWN regime flipped + score decayed (pre-check passes) is rotated to a
    challenger that beats the fresh score by ≥ hysteresis — execute_rotation
    is reached and journals no "incumbent healthy" veto."""

    def _bot(self, symbol, score=100.0, fills=12.0, realized=0.9):
        return {
            "symbol": symbol, "venue": "hyperliquid",
            "bot_code": f"BOT-{symbol}",
            "since": "2026-09-01T00:00:00+00:00",  # older than min_hold_h
            "score_final": score,
            "ticket": {"grid_type": "neutral", "decision": "GO"},
            "stagnation_policy": {
                "regime": "neutral",
                "stagnant_if": {"min_fills_24h": 1.0,
                                "min_realized_ratio": 0.4},
                "score_drop_rotate": 12.0, "hysteresis_score": 5.0,
                "cooldown_h": 12.0},
            "observed": {"fills_24h": fills, "realized_ratio": realized},
            "decision_id": f"DEC-{symbol}",
        }

    def test_rotates_decayed_incumbent_with_healthy_fills(self):
        d = self.make_daemon()
        # fill all 4 slots so the deploy loop cannot consume the challenger
        # in a free slot (open_slot stubbed fail-closed) — the rotation
        # pass is the ONLY path that can deploy SOL this cycle
        for i, sym in enumerate(["PUMP", "DOGE", "ADA", "ETH"], start=1):
            d.state["active_bots"][str(i)] = self._bot(sym)
        self.grid_status_ret = [{"code": "BOT-PUMP", "status": "stopped"}]
        cands = [
            # PUMP's OWN fresh entry: regime flipped + score decayed
            # (100 → 85 = −15 > score_drop_rotate 12) → pre-check passes
            {"venue": "hyperliquid", "symbol": "PUMP",
             "tv_symbol": "BINANCE:PUMPUSDT", "regime": "trending",
             "score_final": 85.0, "step": 0.5,
             "archetype": "Neutral Grid (mean-reversion)"},
            # challenger: regime MATCHES the policy regime and beats the
            # fresh score by ≥ hysteresis (85 + 7) — the OLD re-check vetoed
            # this exact input combo with "incumbent healthy" (healthy
            # fills 12/0.9 + matching challenger regime)
            {"venue": "hyperliquid", "symbol": "SOL",
             "tv_symbol": "BINANCE:SOLUSDT", "regime": "neutral",
             "score_final": 92.0, "step": 0.5,
             "archetype": "Neutral Grid (mean-reversion)"},
        ]
        with mock.patch("daemon.run_merge", return_value={"results": cands}), \
                mock.patch("daemon.resolve_pair_safe",
                           side_effect=lambda v, s, market=None: (s, "")), \
                mock.patch.object(
                    daemon.Daemon, "open_slot",
                    return_value=(None, "no spare capital (test)")):
            d.rescreen_cycle(dry_run=False, max_new=2, top=5)
        # the rotation executed end-to-end: incumbent deleted, challenger
        # created through the stubbed grid_adapter
        self.assertIn(("delete", "BOT-PUMP", False), self.ops)
        self.assertIn(("create", "hyperliquid", False), self.ops)
        self.assertEqual(d.state["active_bots"]["1"]["symbol"], "SOL")
        self.assertNotEqual(d.state["active_bots"]["1"]["symbol"], "PUMP")
        # ...and the journal carries NO "incumbent healthy" veto (the
        # rescreen's pre-check verdict was threaded through, not re-litigated)
        vetoes = [e for e in d.state["journal"]
                  if e.get("kind") == "rotation-veto"]
        self.assertFalse(
            any("incumbent healthy" in e.get("msg", "") for e in vetoes),
            f"unexpected incumbent-healthy vetoes: {vetoes}")

    def test_rescreen_dscore_gate_still_vetoes_weak_challenger(self):
        # the stag_ok path must NOT remove score-quality protection: a
        # challenger that only beats the fresh score by Δ+2 (< 5) is vetoed
        # with an accurate "Δscore X < hysteresis" message
        d = self.make_daemon()
        for i, sym in enumerate(["PUMP", "DOGE", "ADA", "ETH"], start=1):
            d.state["active_bots"][str(i)] = self._bot(sym)
        self.grid_status_ret = [{"code": "BOT-PUMP", "status": "stopped"}]
        cands = [
            {"venue": "hyperliquid", "symbol": "PUMP",
             "tv_symbol": "BINANCE:PUMPUSDT", "regime": "trending",
             "score_final": 85.0, "step": 0.5,
             "archetype": "Neutral Grid (mean-reversion)"},
            {"venue": "hyperliquid", "symbol": "SOL",
             "tv_symbol": "BINANCE:SOLUSDT", "regime": "neutral",
             "score_final": 87.0, "step": 0.5,
             "archetype": "Neutral Grid (mean-reversion)"},
        ]
        with mock.patch("daemon.run_merge", return_value={"results": cands}), \
                mock.patch("daemon.resolve_pair_safe",
                           side_effect=lambda v, s, market=None: (s, "")), \
                mock.patch.object(
                    daemon.Daemon, "open_slot",
                    return_value=(None, "no spare capital (test)")):
            d.rescreen_cycle(dry_run=False, max_new=2, top=5)
        self.assertNotIn(("delete", "BOT-PUMP", False), self.ops)
        self.assertEqual(d.state["active_bots"]["1"]["symbol"], "PUMP")
        vetoes = [e.get("msg", "") for e in d.state["journal"]
                  if e.get("kind") == "rotation-veto"]
        self.assertTrue(any("Δscore" in m and "< hysteresis" in m
                            for m in vetoes), f"vetoes: {vetoes}")
        self.assertFalse(any("incumbent healthy" in m for m in vetoes),
                         f"vetoes: {vetoes}")


if __name__ == "__main__":
    unittest.main()
