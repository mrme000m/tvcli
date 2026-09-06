#!/usr/bin/env python3
"""Watchdog beat-5 change: commit_deploy clears the slot's fill tracker.

Reproduces the 2026-09-05 premature-idle class: a newly deployed bot
inherits the previous occupant's last_increase_at (XVG flagged idle at
age 21m with a 272m-old counter; the same staleness drove the ROBO
swap). After a successful create the slot's optimizer tracker must be
dropped so update_tracker re-seeds the idle clock at the deploy time.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, ".")


def make_payloads():
    upsert = {"lowPrice": 0.9, "midPrice": 1.0, "highPrice": 1.1,
              "gridLevels": 10, "pairCode": "PAIR1",
              "gridPercentStep": 0.005}
    return {"upsert": upsert,
            "grid_bot": {"profit_per_grid_pct": 0.5},
            "stagnation_policy": {"regime": "neutral"}}


class CommitDeployTrackerResetTest(unittest.TestCase):
    def _deploy(self, trackers):
        import daemon
        d = daemon.Daemon.__new__(daemon.Daemon)  # no init side effects
        slot = {"slot": 3, "venue": "binance", "balance": 200.0}
        d.state = {"active_bots": {}, "committed": {}, "journal": [],
                   "slots": [slot],
                   "optimizer": {"trackers": trackers}}
        d.config = {"grid_defaults": {"take_profit_pct": 0.1}}
        d.position_optimizer = None  # skip the post-deploy advisory pass
        action = {"kind": "DEPLOY-PAPER", "slot": 3, "venue": "binance",
                  "symbol": "ZZARB", "decision_id": None,
                  "profile": "profile-2"}
        ticket = {"symbol": "ZZARB", "venue": "binance", "decision": "GO",
                  "grid_type": "neutral", "regime": "neutral"}
        brief = {"metrics": {"atr_pct": 2.0}}
        cand = {"venue": "binance", "symbol": "ZZARB", "score_final": 10.0,
                "archetype": "Neutral Grid (mean-reversion)",
                "tv_symbol": "BINANCE:ZZARBUSDT", "regime": "neutral"}
        payloads = make_payloads()
        payloads["guard_ctx"] = {"total_commitment": 50.0}
        import tempfile
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, True))
        with mock.patch("daemon.grid_adapter.grid_create",
                        return_value={"ok": True, "stdout": "{\"gridBotCode\": \"NEW1\"}"}), \
                mock.patch("daemon.time.sleep", lambda s: None), \
                mock.patch("daemon.log"), \
                mock.patch("daemon.build_spec", return_value={}), \
                mock.patch("daemon.SPECS_DIR", tmp):
            d.commit_deploy(action, ticket, payloads, brief, cand, slot,
                            dry_run=False)
        return d

    def test_deploy_clears_stale_slot_tracker(self):
        d = self._deploy({"3": {"last_fills": 7, "last_increase_at": 1.0}})
        self.assertNotIn("3", d.state["optimizer"]["trackers"])
        # and the new bot landed in the slot
        self.assertIn("3", d.state["active_bots"])

    def test_deploy_without_optimizer_state_still_works(self):
        d = self._deploy({})  # no trackers at all — must not raise
        self.assertIn("3", d.state["active_bots"])
        self.assertEqual(d.state["optimizer"]["trackers"], {})

    def test_dry_run_leaves_tracker_alone(self):
        import daemon
        d = daemon.Daemon.__new__(daemon.Daemon)
        d.state = {"active_bots": {}, "committed": {}, "journal": [],
                   "optimizer": {"trackers": {"3": {"last_increase_at": 1}}}}
        action = {"kind": "DEPLOY-PAPER", "slot": 3, "venue": "binance",
                  "symbol": "ZZARB", "decision_id": None,
                  "profile": "profile-2"}
        ticket = {"symbol": "ZZARB", "venue": "binance", "decision": "GO",
                  "grid_type": "neutral", "regime": "neutral"}
        cand = {"venue": "binance", "symbol": "ZZARB", "score_final": 10.0}
        slot = {"slot": 3, "venue": "binance", "balance": 200.0}
        with mock.patch("daemon.log"):
            d.commit_deploy(action, ticket, make_payloads(),
                             {"metrics": {}}, cand, slot, dry_run=True)
        # dry run journals only; no state mutation, tracker untouched
        self.assertIn("3", d.state["optimizer"]["trackers"])
        self.assertNotIn("3", d.state["active_bots"])


if __name__ == "__main__":
    unittest.main()
