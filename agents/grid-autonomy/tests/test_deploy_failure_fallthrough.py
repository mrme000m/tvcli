#!/usr/bin/env python3
"""Regression: a FAILED live deploy must not consume its slot (2026-09-08).

az00 journal evidence, both chains reproduced here hermetically:
  1. binance:RAY create → WT HTTP 400 → slot 4 was removed from `free`
     anyway → the next binance candidate tripped open_slot → the fixed
     $120 sleeve re-split to 2×$60 → every later binance candidate hit the
     "cannot fund ≥5 lines within 50% worst-case cap" guard-vetoes.
  2. hyperliquid:MON create → 400 at the demo-bot cap on freshly opened
     slot 7 → slot 7 was consumed from `free` → the next HL candidate (MET)
     was refused with "slot-open-veto: already at slots_hard_max" instead
     of falling through into slot 7.

Fixed semantics under test (one rescreen/deploy cycle, dry_run=False):
  the second, lower-scored same-venue candidate deploys into the FIRST
  candidate's slot id — no slot appended, no "slot-open" journal line,
  deployments/deployed count only the success — while the deploy-failed
  action still lands in the journal and the failed decision's outcome is
  recorded.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "policy"))

import daemon  # noqa: E402

try:
    from test_daemon_manage import ManageHarness  # noqa: E401
except ImportError:  # pytest rootdir=agents/grid-autonomy
    from tests.test_daemon_manage import ManageHarness  # noqa: E401


def _cand(venue, symbol, score):
    return {"venue": venue, "symbol": symbol,
            "tv_symbol": f"BINANCE:{symbol}USDT", "regime": "neutral",
            "score_final": score, "step": 0.5,
            "archetype": "Neutral Grid (mean-reversion)"}


def _active_bot(symbol, venue, bot_code):
    return {"symbol": symbol, "venue": venue, "bot_code": bot_code}


class _FailFirstCreate:
    """grid_create fake: the FIRST candidate's create 400s on every retry
    attempt (retry_grid_call retries 3x with backoff — by the time
    commit_deploy journals deploy-failed the 400 is deterministic), every
    later create succeeds. Generic stderr so no demo-cap is learned and the
    second candidate is not demo-cap-vetoed."""

    def __init__(self):
        self.calls = 0
        # mirror the real retry budget so the fake fails exactly as many
        # times as retry_grid_call will call it for the doomed candidate
        self.fail_budget = daemon.retry_grid_call.__kwdefaults__["attempts"]

    def __call__(self, upsert, venue, dry_run=False):
        self.calls += 1
        if self.fail_budget > 0:
            self.fail_budget -= 1
            return {"ok": False, "stderr": "WT HTTP 400: Bad Request"}
        return {"ok": True, "gridBotCode": "NEWBOT"}


class TestDeployFailureFallthrough(ManageHarness):

    def _run_live_cycle(self, d, cands):
        fake = _FailFirstCreate()
        outcomes, run_cards = [], []

        def rec_outcome(decision_id, payload):
            outcomes.append((decision_id, payload))

        def write_card(report):
            run_cards.append(report)

        with mock.patch("daemon.run_merge",
                        return_value={"results": cands}), \
                mock.patch("daemon.grid_adapter.grid_create", new=fake), \
                mock.patch("daemon.time.sleep", new=lambda s: None), \
                mock.patch("daemon.record_outcome_safe", new=rec_outcome), \
                mock.patch("daemon.write_run_card_safe", new=write_card):
            d.rescreen_cycle(dry_run=False, max_new=2)
        return fake, outcomes, run_cards

    def test_failed_live_deploy_leaves_slot_free_for_next_candidate(self):
        d = self.make_daemon()
        binance_before = [s["balance"] for s in d.state["slots"]
                          if s["venue"] == "binance"]
        self.assertEqual(len(binance_before), 2)

        fake, outcomes, run_cards = self._run_live_cycle(d, [
            _cand("binance", "RAY", 90.0),    # create 400s (all retries)
            _cand("binance", "CHIP", 80.0),   # lower score, same venue
        ])
        # the failed create journaled deploy-failed with its slot id
        fails = [e for e in d.state["journal"]
                 if e.get("kind") == "deploy-failed"]
        self.assertEqual(len(fails), 1)
        self.assertEqual(fails[0]["symbol"], "RAY")
        failed_slot = fails[0]["slot"]

        # the SECOND candidate deployed into the FIRST candidate's slot:
        # no new slot, no slot-open, the sleeve was never re-split
        self.assertEqual(list(d.state["active_bots"]), [str(failed_slot)])
        self.assertEqual(d.state["active_bots"][str(failed_slot)]["symbol"],
                         "CHIP")
        self.assertEqual(len(d.state["slots"]), 4)
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertNotIn("slot-open", kinds)
        binance_after = [s["balance"] for s in d.state["slots"]
                         if s["venue"] == "binance"]
        self.assertEqual(binance_after, binance_before)

        # deployed/deployments count only the success (dry-run never ran):
        # one run card, one deployment row — the CHIP one, live, first slot
        self.assertEqual(len(run_cards), 1)
        deps = run_cards[0]["deployments"]
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]["symbol"], "CHIP")
        self.assertEqual(deps[0]["slot"], failed_slot)
        self.assertFalse(deps[0]["paper"])

        # create traffic: 3 doomed retries for RAY + 1 success for CHIP
        # (retry_grid_call retries the 400 with backoff — deterministic
        # failure by the time commit_deploy journals deploy-failed)
        self.assertEqual(fake.calls,
                         daemon.retry_grid_call.__kwdefaults__["attempts"] + 1)

        # the failed decision's outcome was recorded (ledger closure)
        dep_failed = [p for _did, p in outcomes
                      if p.get("reason") == "deploy-failed"]
        self.assertEqual(len(dep_failed), 1)

    def test_failed_create_on_freshly_opened_slot_reuses_it(self):
        # chain 2: all 4 slots occupied → the first HL candidate opens
        # slot 5, its create 400s → the next HL candidate must fall through
        # into slot 5 instead of tripping open_slot at slots_max
        d = self.make_daemon()
        for k, (symbol, venue) in {
                "1": ("HYPE", "hyperliquid"), "2": ("ARB", "hyperliquid"),
                "3": ("SOL", "binance"), "4": ("NEAR", "binance")}.items():
            d.state["active_bots"][k] = _active_bot(symbol, venue,
                                                    f"B{int(k)}")
        self._run_live_cycle(d, [
            _cand("hyperliquid", "MON", 90.0),   # opens slot 5, create 400s
            _cand("hyperliquid", "MET", 85.0),   # must reuse slot 5
        ])
        self.assertEqual(len(d.state["slots"]), 5)
        self.assertIn("5", d.state["active_bots"])
        self.assertEqual(d.state["active_bots"]["5"]["symbol"], "MET")
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertEqual(kinds.count("slot-open"), 1)   # MON's opener only
        self.assertNotIn("slot-open-veto", kinds)

    def test_dry_run_planning_unchanged(self):
        # criterion 5: dry-run never creates, never journals deploy-failed,
        # and both candidates are planned/counted (paper mirror)
        d = self.make_daemon()
        outcomes, run_cards = [], []

        def rec_outcome(decision_id, payload):
            outcomes.append((decision_id, payload))

        def write_card(report):
            run_cards.append(report)

        cands = [_cand("binance", "RAY", 90.0),
                 _cand("binance", "CHIP", 80.0)]
        with mock.patch("daemon.run_merge",
                        return_value={"results": cands}), \
                mock.patch("daemon.record_outcome_safe", new=rec_outcome), \
                mock.patch("daemon.write_run_card_safe", new=write_card):
            d.rescreen_cycle(dry_run=True, max_new=2)
        creates = [op for op in self.ops if op[0] == "create"]
        self.assertEqual(creates, [])
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertNotIn("deploy-failed", kinds)
        self.assertNotIn("slot-open", kinds)
        self.assertEqual(len(run_cards), 1)
        deps = run_cards[0]["deployments"]
        self.assertEqual([e["symbol"] for e in deps], ["RAY", "CHIP"])
        self.assertTrue(all(e["paper"] for e in deps))
        # both planned into two DISTINCT free binance slots (dry run never
        # mutates active_bots, so nothing is occupied for the mirror)
        binance_slots = {s["slot"] for s in d.state["slots"]
                         if s["venue"] == "binance"}
        self.assertEqual({e["slot"] for e in deps}, binance_slots)


if __name__ == "__main__":
    unittest.main()
