import json
#!/usr/bin/env python3
"""Daemon manage-loop tests — hermetic, no network, no wt_browser.

Worker A/C modules are stubbed at the daemon boundary (resolve/observe/
reliability_grid/reflect + grid_adapter subprocess drivers), so these tests
verify the manage loop itself: rotation order, escalation ladder, adjust
gating, and first-run adoption.
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "policy"))

import daemon
from stagnation import slot_plan

PROFILES = [{"code": "profile-1", "name": "demo-hype", "balance": 300.0,
             "exchange": "HYPERLIQUID_SWAP", "paperTrading": True},
            {"code": "profile-2", "name": "demo-bn", "balance": 200.0,
             "exchange": "BINANCE_FUTURES", "paperTrading": True}]


def make_payloads(pair="PAIR1", price=100.0, step_pct=0.5, grids=10):
    return {
        "upsert": {
            "pairCode": pair,
            "lowPrice": price * 0.9,
            "midPrice": price,
            "highPrice": price * 1.1,
            "gridPercentStep": step_pct / 100.0,
            "gridLevels": grids,
            "amountPerTrade": 10.0,
            "profileCode": "profile-1",
            "paperTrading": True,
        },
        "grid_bot": {"profit_per_grid_pct": step_pct, "grids": grids},
        "guard_ctx": {"total_commitment": 50.0},
        "stagnation_policy": {"regime": "neutral", "step": 0.005},
    }


class ManageTestCase(unittest.TestCase):
    def setUp(self):
        self.ops = []
        self.grid_status_ret = []
        self.reliability = {}
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state_path = os.path.join(self.tmp.name, "state", "state.json")

        def fake_candles(venue, symbol, interval, limit, market="spot"):
            return [(100.0, 101.0, 99.0, 100.0 + i * 0.001)
                    for i in range(limit or 100)]

        def fake_deliberate(brief):
            return {"decision": "GO", "grid_type": "neutral",
                    "symbol": brief.get("symbol"),
                    "venue": brief.get("venue"),
                    "regime": brief.get("regime")}

        def fake_build(ticket, brief, balance, mult, profile_code, pair_code,
                       exchange_code=None, amount_precision=None,
                       max_affordable_grids=None, min_cost=None,
                       min_grids=None):
            self.ops.append(("build", pair_code, round(mult, 4)))
            return make_payloads(pair=pair_code)

        def fake_create(upsert, venue, dry_run=False):
            self.ops.append(("create", venue, dry_run))
            return {"ok": True, "gridBotCode": "NEWBOT"}

        def fake_stop(bot_code, mode, dry_run=True):
            self.ops.append(("stop", bot_code, dry_run))
            return {"ok": True}

        def fake_delete(bot_code, dry_run=True):
            self.ops.append(("delete", bot_code, dry_run))
            return {"ok": True}

        def fake_grid_status():
            self.ops.append(("verify",))
            return list(self.grid_status_ret)

        def fake_grid_edit(bot_code, upsert, dry_run=True):
            self.ops.append(("edit", bot_code, dry_run))
            return {"ok": True}

        def fake_compute(symbol, venue, price, atr_pct, step_pct, grids,
                         amount, grid_type, profile_code, pair_code,
                         exchange_code=None):
            return make_payloads(pair=pair_code)["upsert"]

        patches = {
            "daemon.STATE_PATH": state_path,
            "daemon.SPECS_DIR": os.path.join(self.tmp.name, "watch", "specs"),
            "daemon.deliberate": fake_deliberate,
            "daemon.memories_for_safe": lambda brief, k=3: [],
            "daemon.record_decision_safe": lambda *a, **k: "d1",
            "daemon.record_outcome_safe": lambda *a, **k: None,
            "daemon.write_run_card_safe": lambda *a, **k: None,
            "daemon.resolve_pair_safe": lambda venue, symbol, market=None: ("PAIR1", ""),
            "daemon.pair_meta": lambda venue, symbol, market=None: {"min_cost": 1, "amount_precision": 2},
            "daemon.guard_deploy": lambda ticket, ctx: (True, []),
            "daemon.grid_profiles_safe": lambda: list(PROFILES),
            "daemon.grid_capacity_safe": lambda: {},
            "daemon.account_limits_safe": lambda: {},
            "daemon.reliability_load_safe": lambda: dict(self.reliability),
            "daemon.grid_status_safe": fake_grid_status,
            "daemon.grid_edit_safe": fake_grid_edit,
            "daemon.grid_adapter.build_ticket_payloads": fake_build,
            "daemon.grid_adapter.grid_create": fake_create,
            "daemon.grid_adapter.grid_stop": fake_stop,
            "daemon.grid_adapter.grid_delete": fake_delete,
            "daemon.grid_adapter.compute_upsert": fake_compute,
            "market_regime.fetch_candles": fake_candles,
        }
        self.patches = []
        for target, new in patches.items():
            patcher = mock.patch(target, new)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_daemon(self):
        d = daemon.Daemon()
        d.state["slots"] = slot_plan(500.0, n_slots=4)["slots"]
        return d

    # ── rotation ────────────────────────────────────────────────────────
    def test_rotation_order_stop_verify_delete_then_deploy(self):
        d = self.make_daemon()
        d.state["active_bots"]["1"] = {
            "symbol": "PUMP", "venue": "hyperliquid", "bot_code": "OLDBOT",
            "score_final": 40.0,
            "stagnation_policy": {
                "regime": "neutral",
                "stagnant_if": {"min_fills_24h": 1.0, "min_realized_ratio": 0.4},
                "score_drop_rotate": 12.0, "hysteresis_score": 5.0,
                "cooldown_h": 12.0},
            "observed": {"fills_24h": 0.0, "realized_ratio": 0.0},
            "decision_id": "OLD1",
        }
        challenger = {"venue": "hyperliquid", "symbol": "SOL",
                      "tv_symbol": "HYPE:SOL", "regime": "neutral",
                      "score_final": 60.0,
                      "archetype": "Neutral Grid (mean-reversion)"}
        self.grid_status_ret = [{"code": "OLDBOT", "status": "stopped"}]
        ok = d.execute_rotation("1", challenger, dry_run=False)
        self.assertTrue(ok)
        # already-stopped incumbent: the stop call is skipped (WT would
        # answer 400 "Grid Bot already stopped")
        self.assertEqual(self.ops, [
            ("build", "PAIR1", 0.25),      # challenger plan (guard passes)
            ("verify",),                   # pre-check: already stopped
            ("verify",),                   # post-stop verification
            ("delete", "OLDBOT", False),   # delete incumbent
            ("create", "hyperliquid", False),  # deploy challenger
        ])

    def test_rotation_stops_active_incumbent(self):
        d = self.make_daemon()
        d.state["active_bots"]["1"] = {
            "symbol": "PUMP", "venue": "hyperliquid", "bot_code": "OLDBOT",
            "score_final": 40.0,
            "stagnation_policy": {
                "regime": "neutral",
                "stagnant_if": {"min_fills_24h": 1.0, "min_realized_ratio": 0.4},
                "score_drop_rotate": 12.0, "hysteresis_score": 5.0,
                "cooldown_h": 12.0},
            "observed": {"fills_24h": 0.0, "realized_ratio": 0.0},
            "decision_id": "OLD1",
        }
        challenger = {"venue": "hyperliquid", "symbol": "SOL",
                      "tv_symbol": "HYPE:SOL", "regime": "neutral",
                      "score_final": 60.0,
                      "archetype": "Neutral Grid (mean-reversion)"}
        self.grid_status_ret = [{"code": "OLDBOT", "status": "active"}]

        def fake_stop_active(bot_code, mode, dry_run=True):
            self.ops.append(("stop", bot_code, dry_run))
            self.grid_status_ret = [{"code": "OLDBOT", "status": "stopped"}]
            return {"ok": True}

        with mock.patch("daemon.grid_adapter.grid_stop", fake_stop_active):
            ok = d.execute_rotation("1", challenger, dry_run=False)
        self.assertTrue(ok)
        self.assertEqual(self.ops, [
            ("build", "PAIR1", 0.25),
            ("verify",),                   # pre-check: incumbent still active
            ("stop", "OLDBOT", False),     # active incumbent: stop first
            ("verify",),                   # post-stop verification
            ("delete", "OLDBOT", False),
            ("create", "hyperliquid", False),
        ])
        self.assertGreater(d.state["cooldowns_until"]["hyperliquid:PUMP"],
                           time.time())
        self.assertEqual(d.state["active_bots"]["1"]["symbol"], "SOL")
        self.assertEqual(d.state["active_bots"]["1"]["bot_code"], "NEWBOT")
        self.assertEqual(d.state["committed"]["1"], 50.0)

    def test_rotation_veto_when_challenger_not_better_enough(self):
        d = self.make_daemon()
        d.state["active_bots"]["1"] = {
            "symbol": "PUMP", "venue": "hyperliquid", "bot_code": "OLDBOT",
            "score_final": 100.0,
            "stagnation_policy": {
                "regime": "neutral",
                "stagnant_if": {"min_fills_24h": 1.0, "min_realized_ratio": 0.4},
                "score_drop_rotate": 12.0, "hysteresis_score": 5.0,
                "cooldown_h": 12.0},
            "observed": {"fills_24h": 0.0, "realized_ratio": 0.0},
        }
        challenger = {"venue": "hyperliquid", "symbol": "SOL",
                      "tv_symbol": "HYPE:SOL", "regime": "neutral",
                      "score_final": 102.0,
                      "archetype": "Neutral Grid (mean-reversion)"}
        ok = d.execute_rotation("1", challenger, dry_run=False)
        self.assertFalse(ok)
        self.assertEqual(self.ops, [])  # no build/stop/delete ever ran
        self.assertEqual(d.state["active_bots"]["1"]["symbol"], "PUMP")

    def _seed_rotating_bot(self, d):
        d.state["active_bots"]["1"] = {
            "symbol": "PUMP", "venue": "hyperliquid", "bot_code": "OLDBOT",
            "score_final": 40.0,
            "stagnation_policy": {
                "regime": "neutral",
                "stagnant_if": {"min_fills_24h": 1.0, "min_realized_ratio": 0.4},
                "score_drop_rotate": 12.0, "hysteresis_score": 5.0,
                "cooldown_h": 12.0},
            "observed": {"fills_24h": 0.0, "realized_ratio": 0.0},
            "decision_id": "OLD1",
        }

    def _challenger(self):
        return {"venue": "hyperliquid", "symbol": "SOL",
                "tv_symbol": "HYPE:SOL", "regime": "neutral",
                "score_final": 60.0,
                "archetype": "Neutral Grid (mean-reversion)"}

    def test_rotation_aborts_when_stop_fails(self):
        d = self.make_daemon()
        self._seed_rotating_bot(d)
        with mock.patch("daemon.grid_adapter.grid_stop",
                        side_effect=lambda bc, mode="stop_and_close_all",
                        dry_run=True: {"ok": False, "error": "cf 403"}), \
             mock.patch("daemon.time.sleep"):
            ok = d.execute_rotation("1", self._challenger(), dry_run=False)
        self.assertFalse(ok)
        self.assertNotIn(("delete", "OLDBOT", False), self.ops)
        self.assertNotIn(("create", "hyperliquid", False), self.ops)
        self.assertEqual(d.state["active_bots"]["1"]["symbol"], "PUMP")

    def test_rotation_aborts_when_stop_not_verified(self):
        # stop reported ok but the bot is listed as still running → the
        # incumbent must never be deleted
        d = self.make_daemon()
        self._seed_rotating_bot(d)
        self.grid_status_ret = [{"code": "OLDBOT", "status": "active"}]
        ok = d.execute_rotation("1", self._challenger(), dry_run=False)
        self.assertFalse(ok)
        self.assertNotIn(("delete", "OLDBOT", False), self.ops)
        self.assertEqual(d.state["active_bots"]["1"]["symbol"], "PUMP")

    def test_rotation_proceeds_when_stopped_bot_unlisted(self):
        # stop ok and the bot no longer appears in the status list →
        # deleting an already-gone bot is harmless; rotation proceeds
        d = self.make_daemon()
        self._seed_rotating_bot(d)
        self.grid_status_ret = []
        ok = d.execute_rotation("1", self._challenger(), dry_run=False)
        self.assertTrue(ok)
        self.assertIn(("delete", "OLDBOT", False), self.ops)
        self.assertIn(("create", "hyperliquid", False), self.ops)

    def test_challenger_deploy_failure_after_delete_empties_slot(self):
        # incumbent already removed; challenger create fails → rotation
        # completes with a visible rotation-error and an empty slot that the
        # next rescreen refills
        d = self.make_daemon()
        self._seed_rotating_bot(d)
        self.grid_status_ret = [{"code": "OLDBOT", "status": "stopped"}]
        with mock.patch("daemon.grid_adapter.grid_create",
                        side_effect=lambda u, v, dry_run=True:
                        {"ok": False, "error": "create rejected"}), \
             mock.patch("daemon.time.sleep"):
            ok = d.execute_rotation("1", self._challenger(), dry_run=False)
        self.assertTrue(ok)  # incumbent WAS rotated out
        self.assertNotIn("1", d.state["active_bots"])  # slot left empty
        self.assertTrue(any(e.get("kind") == "rotation-error"
                            for e in d.state["journal"]))

    # ── escalation ladder ────────────────────────────────────────────────
    def test_escalation_ladder_transitions(self):
        cfg = daemon.DEFAULT_CONFIG
        arch = "Neutral Grid (mean-reversion)"
        self.assertEqual(daemon.size_multiplier({}, arch, cfg)[0], 0.25)
        self.assertEqual(daemon.size_multiplier(
            {arch: {"samples": 10, "profit_factor": 0.8}}, arch, cfg)[0], 0.40)
        self.assertEqual(daemon.size_multiplier(
            {arch: {"samples": 30, "profit_factor": 1.4}}, arch, cfg)[0], 0.50)
        self.assertFalse(daemon.refuse_new_archetype({arch: {"samples": 30}},
                                                     arch))
        self.assertTrue(daemon.refuse_new_archetype(
            {arch: {"recent_pf": 0.9}}, arch))

    # ── reliability ledger wiring ───────────────────────────────────────
    def _seed_reliability_bot(self, d,
                              archetype="Neutral Grid (mean-reversion)"):
        d.state["active_bots"]["1"] = {
            "symbol": "PUMP", "venue": "hyperliquid", "bot_code": "OLDBOT",
            "archetype": archetype,
            "stagnation_policy": {}, "observed": {},
        }

    def test_reliability_cycle_computes_and_saves(self):
        d = self.make_daemon()
        self._seed_reliability_bot(d)
        trades = [{"pnl_usd": 10.0, "close_ts": 1.0, "entered_at": None,
                   "strategy_id": None},
                  {"pnl_usd": -4.0, "close_ts": 2.0, "entered_at": None,
                   "strategy_id": None}]
        saved = {}
        with mock.patch("daemon.bot_trades", return_value=trades), \
             mock.patch("daemon.save_reliability",
                        side_effect=lambda data: saved.update(data) or True):
            d.reliability_cycle()
        stats = saved.get("Neutral Grid (mean-reversion)")
        self.assertEqual(stats["samples"], 2)
        self.assertEqual(stats["profit_factor"], 2.5)  # 10 / 4
        self.assertEqual(stats["win_rate"], 0.5)

    def test_reliability_cycle_zero_samples_keep_existing_ledger(self):
        d = self.make_daemon()
        self._seed_reliability_bot(d)
        with mock.patch("daemon.bot_trades", return_value=[]), \
             mock.patch("daemon.save_reliability") as fake_save:
            d.reliability_cycle()
        fake_save.assert_not_called()  # nothing fresh → ledger untouched

    # ── adjust gating ────────────────────────────────────────────────────
    def _seed_adjust_bot(self, d):
        d.state["active_bots"]["1"] = {
            "venue": "hyperliquid", "symbol": "SOL", "bot_code": "B1",
            "ticket": {"grid_type": "neutral", "decision": "GO"},
            "stagnation_policy": {"regime": "neutral"},
            "channel": {"mid": 100.0, "step_pct": 0.5, "grids": 10},
            "upsert": {"gridPercentStep": 0.005, "gridLevels": 10,
                       "amountPerTrade": 10.0, "pairCode": "PAIR1"},
            "profile_code": "profile-1", "pair_code": "PAIR1",
        }

    def test_adjust_applies_once_then_rate_limits(self):
        d = self.make_daemon()
        self._seed_adjust_bot(d)
        d.adjust_bot("1", dry_run=False)
        d.adjust_bot("1", dry_run=False)  # immediately again → rate limited
        edits = [op for op in self.ops if op[0] == "edit"]
        self.assertEqual(edits, [("edit", "B1", False)])
        self.assertIn("1", d.state["last_adjust"])

    def test_adjust_skip_without_bot_code(self):
        d = self.make_daemon()
        self._seed_adjust_bot(d)
        d.state["active_bots"]["1"]["bot_code"] = None
        d.adjust_bot("1", dry_run=False)
        self.assertEqual([op for op in self.ops if op[0] == "edit"], [])

    # ── first-run adoption ───────────────────────────────────────────────
    def test_adopt_existing_paper_bot(self):
        d = self.make_daemon()
        self.grid_status_ret = [{"code": "ADOPT1", "paperTrading": True,
                                 "status": "active", "exchange": "BINANCE",
                                 "pair": "BTCUSDT"}]
        d.adopt_existing(dry_run=True)
        self.assertIn("3", d.state["active_bots"])  # first binance slot
        b = d.state["active_bots"]["3"]
        self.assertTrue(b["adopted"])
        self.assertEqual(b["venue"], "binance")
        self.assertEqual(b["symbol"], "BTC")

    def test_adopt_skips_non_paper(self):
        d = self.make_daemon()
        self.grid_status_ret = [{"code": "LIVE1", "paperTrading": False,
                                 "status": "active", "exchange": "BINANCE",
                                 "pair": "BTCUSDT"}]
        d.adopt_existing(dry_run=True)
        self.assertEqual(d.state["active_bots"], {})

    # ── rescreen duplicate-deploy guard ─────────────────────────────────
    def test_rescreen_skips_already_active_symbol(self):
        d = self.make_daemon()
        d.state["active_bots"]["1"] = {
            "symbol": "HYPE", "venue": "hyperliquid", "bot_code": "ADOPT1",
            "ticket": {"grid_type": "neutral", "decision": "GO"},
            "stagnation_policy": {"regime": "neutral"}}
        cands = [
            {"venue": "hyperliquid", "symbol": "HYPE",
             "tv_symbol": "BINANCE:HYPEUSDT", "regime": "neutral",
             "score_final": 90.0, "step": 0.5,
             "archetype": "Neutral Grid (mean-reversion)"},
            {"venue": "hyperliquid", "symbol": "SOL",
             "tv_symbol": "BINANCE:SOLUSDT", "regime": "neutral",
             "score_final": 80.0, "step": 0.5,
             "archetype": "Neutral Grid (mean-reversion)"},
            {"venue": "binance", "symbol": "BTC",
             "tv_symbol": "BINANCE:BTCUSDT", "regime": "neutral",
             "score_final": 70.0, "step": 0.5,
             "archetype": "Neutral Grid (mean-reversion)"},
        ]
        with mock.patch("daemon.run_merge", return_value={"results": cands}), \
                mock.patch("daemon.resolve_pair_safe",
                           side_effect=lambda v, s, market=None: (s, "")):
            d.rescreen_cycle(dry_run=True, max_new=2, top=5)
        built = [op for op in self.ops if op[0] == "build"]
        self.assertEqual([op[1] for op in built], ["SOL", "BTC"])
        self.assertEqual(d.state["active_bots"]["1"]["symbol"], "HYPE")

    def test_rescreen_builds_reflect_report_shape(self):
        d = self.make_daemon()
        cands = [
            {"venue": "hyperliquid", "symbol": "SOL",
             "tv_symbol": "BINANCE:SOLUSDT", "regime": "neutral",
             "score_final": 80.0, "step": 0.5,
             "archetype": "Neutral Grid (mean-reversion)"},
            {"venue": "binance", "symbol": "BTC",
             "tv_symbol": "BINANCE:BTCUSDT", "regime": "neutral",
             "score_final": 70.0, "step": 0.5,
             "archetype": "Neutral Grid (mean-reversion)"},
        ]
        captured = {}
        with mock.patch("daemon.run_merge", return_value={"results": cands}), \
                mock.patch("daemon.resolve_pair_safe",
                           side_effect=lambda v, s, market=None: (s, "")), \
                mock.patch("daemon.write_run_card_safe",
                           side_effect=lambda rep: captured.update(rep)):
            d.rescreen_cycle(dry_run=True, max_new=2, top=5)
        self.assertEqual(captured["cycle_kind"], "rescreen")
        self.assertEqual(captured["screen"]["n_candidates"], 2)
        self.assertEqual(len(captured["deliberations"]), 2)
        self.assertEqual(len(captured["deployments"]), 2)
        self.assertEqual(captured["deployments"][0]["slot"], 1)
        self.assertEqual(captured["deployments"][0]["symbol"], "SOL")
        self.assertEqual(captured["deployments"][1]["slot"], 3)
        self.assertEqual(captured["deployments"][1]["symbol"], "BTC")
        self.assertTrue(captured["paper"])
        self.assertEqual(captured["guard"][0]["ok"], True)


class DefensiveImportTest(unittest.TestCase):
    """Real (unpatched) defensive stubs when Worker A/C modules are absent."""

    def test_resolve_stub(self):
        if not daemon.HAS_RESOLVE:
            self.assertEqual(daemon.resolve_pair_safe("hyperliquid", "SOL"),
                             (None, "resolve module missing"))

    def test_observe_stubs(self):
        if not daemon.HAS_OBSERVE:
            self.assertEqual(daemon.observe_all_safe({}), {})
            self.assertEqual(daemon.grid_profiles_safe(), [])
            self.assertEqual(daemon.grid_status_safe(), [])

    def test_reliability_stub(self):
        if not daemon.HAS_RELIABILITY:
            self.assertEqual(daemon.reliability_load_safe(), {})


if __name__ == "__main__":
    unittest.main()


CAPACITY_LIVE = {
    "max_active": {"other": 1, "premium": 200},
    "active": {"other": 1, "premium": {"HYPERLIQUID_SWAP": 2}},
    "used_pairs": {"BINANCE_FUTURES": {"demo-bn-prof": ["SOLUSDT"]},
                   "HYPERLIQUID_SWAP": {"demo-hype-prof": ["1", "200"]}},
}

PROFILES_LIVE = [
    {"code": "demo-hype-prof", "name": "demo-hype",
     "exchange": "HYPERLIQUID_SWAP", "paperTrading": True, "balance": 300.0},
    {"code": "demo-bn-prof", "name": "demo-bn",
     "exchange": "BINANCE_FUTURES", "paperTrading": True, "balance": 200.0},
]


class CapacityBlockTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch("daemon.STATE_PATH",
                             os.path.join(self.tmp.name, "state.json"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.d = daemon.Daemon()
        self.d.state["slots"] = slot_plan(500.0, n_slots=4)["slots"]
        self.d.profiles = list(PROFILES_LIVE)

    def test_binance_blocked_at_tier_cap(self):
        # free plan: 1 active bot on non-premium exchanges, SOL occupies it
        reason = self.d.venue_capacity_block(
            {"venue": "binance", "symbol": "PUMP"}, CAPACITY_LIVE)
        self.assertTrue(reason)
        self.assertIn("plan cap", reason)

    def test_hyperliquid_premium_allowed(self):
        reason = self.d.venue_capacity_block(
            {"venue": "hyperliquid", "symbol": "ZEC"}, CAPACITY_LIVE)
        self.assertIsNone(reason)

    def test_hyperliquid_blocked_at_premium_cap(self):
        cap = json.loads(json.dumps(CAPACITY_LIVE))
        cap["max_active"]["premium"] = 2
        cap["active"]["premium"]["HYPERLIQUID_SWAP"] = 2
        reason = self.d.venue_capacity_block(
            {"venue": "hyperliquid", "symbol": "ZEC"}, cap)
        self.assertTrue(reason)

    def test_used_pair_blocked(self):
        cap = json.loads(json.dumps(CAPACITY_LIVE))
        cap["active"]["other"] = 0  # tier has room; pair is the blocker
        with mock.patch("daemon.resolve_pair_safe",
                        lambda venue, symbol, market=None: ("SOLUSDT", "")):
            reason = self.d.venue_capacity_block(
                {"venue": "binance", "symbol": "SOL"}, cap)
        self.assertTrue(reason)
        self.assertIn("already", reason)

    def test_no_capacity_data_passes(self):
        self.assertIsNone(
            self.d.venue_capacity_block({"venue": "binance", "symbol": "X"}, {}))


class SubscriptionObservationTestCase(unittest.TestCase):
    """The daemon must observe the plan (account-limits + tier caps) each
    rescreen cycle and journal a `subscription` entry when it changes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch("daemon.STATE_PATH",
                             os.path.join(self.tmp.name, "state.json"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.d = daemon.Daemon()
        self.d.state["slots"] = slot_plan(500.0, n_slots=4)["slots"]
        patches = {
            "daemon.grid_profiles_safe": lambda: list(PROFILES),
            "daemon.grid_capacity_safe": lambda: dict(CAPACITY_LIVE),
            "daemon.account_limits_safe": lambda: {
                "gridBots": {"active": 3, "max": 200, "allowOnCurrentPlan": True},
                "dcaBots": {"active": 0, "max": 300},
            },
            "daemon.run_merge": lambda **kw: {"results": []},
            "daemon.write_run_card_safe": lambda rep: None,
            "daemon.record_decision_safe": lambda *a, **k: "d1",
            "daemon.resolve_pair_safe": lambda v, s, market=None: ("PAIR1", ""),
        }
        self.patches = {}
        for target, new in patches.items():
            p = mock.patch(target, new)
            p.start()
            self.addCleanup(p.stop)

    def test_subscription_journaled_once_then_only_on_change(self):
        self.d.rescreen_cycle(dry_run=True)
        self.assertEqual(self.d.state["account_limits"]["gridBots"]["max"], 200)
        self.assertEqual(self.d.state["capacity"]["max_active"]["other"], 1)
        subs = [j for j in self.d.state["journal"] if j.get("kind") == "subscription"]
        self.assertEqual(len(subs), 1)
        self.assertIn("gridBots 3/200", subs[0]["msg"])
        # second cycle with identical limits: no duplicate journal entry
        self.d.rescreen_cycle(dry_run=True)
        subs = [j for j in self.d.state["journal"] if j.get("kind") == "subscription"]
        self.assertEqual(len(subs), 1)
        # a plan change (free->paid tier caps) journals again
        mock.patch("daemon.grid_capacity_safe",
                   side_effect=lambda: {
                       "max_active": {"other": 20, "premium": 200},
                       "active": {"other": 1, "premium": {"HYPERLIQUID_SWAP": 2}},
                   }).start()
        self.d.rescreen_cycle(dry_run=True)
        subs = [j for j in self.d.state["journal"] if j.get("kind") == "subscription"]
        self.assertEqual(len(subs), 2)

    def test_limits_signature_shape(self):
        sig = json.loads(daemon._limits_signature(
            {"gridBots": {"active": 3, "max": 200}}, CAPACITY_LIVE))
        self.assertEqual(sig["gridBots"], {"active": 3, "max": 200})
        self.assertEqual(sig["tier_caps"], {"other": 1, "premium": 200})
        self.assertEqual(sig["premium_exchanges"], ["HYPERLIQUID_SWAP"])
