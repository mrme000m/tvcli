#!/usr/bin/env python3
"""Dynamic slot-opening tests — venue slots open for profitable tokens when
deployable capital is spare (the "HL slots open" requirement).

Reuses the hermetic ManageHarness (no network, no wt_browser) so the
rescreen path is exercised end-to-end with mocked merge/deploy workers.
"""
import os
import sys
import unittest
from unittest import mock

import daemon  # noqa: E402  (path set up by test_daemon_manage import below)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "policy"))

try:
    from test_daemon_manage import (ManageHarness, PROFILES,  # noqa: F401
                                     make_payloads)
except ImportError:  # pytest rootdir=agents/grid-autonomy
    from tests.test_daemon_manage import (ManageHarness, PROFILES,  # noqa: F401
                                          make_payloads)

from stagnation import slot_plan


def _active_bot(symbol, venue, bot_code):
    return {"symbol": symbol, "venue": venue, "bot_code": bot_code}


def _cand(venue, symbol, score):
    return {"venue": venue, "symbol": symbol,
            "tv_symbol": f"BINANCE:{symbol}USDT", "regime": "neutral",
            "score_final": score, "step": 0.5,
            "archetype": "Neutral Grid (mean-reversion)"}


class TestOpenSlot(ManageHarness):
    def test_opens_when_spare_capital(self):
        d = self.make_daemon()
        slot, err = d.open_slot("hyperliquid")
        self.assertIsNone(err)
        self.assertEqual(slot["slot"], 5)
        self.assertEqual(slot["venue"], "hyperliquid")
        self.assertEqual(slot["balance"], 100.0)   # 300 / 3 HL slots
        self.assertEqual(slot["max_commitment"], 50.0)
        self.assertEqual(len(d.state["slots"]), 5)
        # fixed mode: existing HL slot budgets re-normalized to the 3-way split
        hl = [s for s in d.state["slots"] if s["venue"] == "hyperliquid"]
        self.assertEqual([s["balance"] for s in hl], [100.0, 100.0, 100.0])
        kinds = [e["kind"] for e in d.state["journal"]]
        self.assertIn("slot-open", kinds)

    def test_refuses_at_slots_max(self):
        d = self.make_daemon()
        d.state["slots"] = slot_plan(500.0, n_slots=5)["slots"]
        slot, err = d.open_slot("hyperliquid")
        self.assertIsNone(slot)
        self.assertIn("slots_max", err)
        self.assertEqual(len(d.state["slots"]), 5)

    def test_refuses_when_capital_tight(self):
        d = self.make_daemon()
        # committed 425 = the whole deployable ceiling (500 × 0.85) → no
        # spare for the new slot's $50 worst-case
        d.state["committed"] = {"1": 225.0, "2": 200.0}
        slot, err = d.open_slot("hyperliquid")
        self.assertIsNone(slot)
        self.assertIn("spare", err)
        self.assertEqual(len(d.state["slots"]), 4)

    def test_refuses_unfunded_venue(self):
        d = self.make_daemon()
        slot, err = d.open_slot("bybit")
        self.assertIsNone(slot)
        self.assertIn("not funded", err)


class TestDemoBotCap(ManageHarness):
    """WunderTrading's demo (paper) grid-bot cap (5 on this plan) is not in
    any capacity API — it is learned from the create-400 and must gate new
    deploys + slot opens (rotations unaffected)."""

    def test_parse_demo_cap_from_error(self):
        err = ("You\u2019ve reached the maximum number of Demo Trading "
               "Grid Bots! (Limit: 5)")
        self.assertEqual(daemon.demo_cap_from_error(err), 5)
        self.assertIsNone(daemon.demo_cap_from_error("some other 400"))
        self.assertIsNone(daemon.demo_cap_from_error(None))

    def test_commit_deploy_learns_the_cap(self):
        import json as _json
        d = self.make_daemon()
        slot = d.state["slots"][0]
        action = {"kind": "DEPLOY-PAPER", "slot": 1, "venue": "hyperliquid",
                  "symbol": "PEPE", "grid_type": "neutral",
                  "decision_id": "d1"}
        payloads = make_payloads()
        bad = {"ok": False, "stdout": _json.dumps({
            "status": 400, "ok": False, "gridBotCode": None,
            "message": "You\u2019ve reached the maximum number of Demo "
                       "Trading Grid Bots! (Limit: 5)"})}
        with mock.patch("daemon.retry_grid_call", return_value=bad):
            d.commit_deploy(action, {"symbol": "PEPE"}, payloads,
                            {"symbol": "PEPE", "venue": "hyperliquid"},
                            {"venue": "hyperliquid", "symbol": "PEPE",
                             "score_final": 90.0}, slot, dry_run=False)
        self.assertEqual(d.state["demo_bot_cap"], 5)
        kinds = [e["kind"] for e in d.state["journal"]]
        self.assertIn("demo-cap", kinds)
        self.assertIn("deploy-failed", kinds)

    def _fill_all_slots(self, d):
        for k, (symbol, venue) in {
                "1": ("HYPE", "hyperliquid"), "2": ("ARB", "hyperliquid"),
                "3": ("SOL", "binance"), "4": ("NEAR", "binance")}.items():
            d.state["active_bots"][k] = _active_bot(symbol, venue, f"B{k}")

    def test_rescreen_gated_at_demo_cap(self):
        d = self.make_daemon()
        self._fill_all_slots(d)
        d.state["demo_bot_cap"] = 5   # already learned
        self.assertEqual(len(d.state["active_bots"]), 4)
        d.state["active_bots"]["9"] = _active_bot("EXTRA", "hyperliquid",
                                                  "B9")  # now 5 = cap
        cands = [_cand("hyperliquid", "PEPE", 90.0)]
        with mock.patch("daemon.run_merge", return_value={"results": cands}):
            d.rescreen_cycle(dry_run=False, max_new=2)
        # at the cap: no slot opened, no bot created, one clear veto line
        self.assertEqual(len(d.state["slots"]), 4)
        creates = [op for op in self.ops if op[0] == "create"]
        self.assertEqual(creates, [])
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertIn("demo-cap-veto", kinds)
        self.assertNotIn("slot-open", kinds)

    def test_rotation_still_allowed_at_demo_cap(self):
        # the gate sits in the DEPLOY loop only — execute_rotation must
        # still be able to stop+delete+deploy (net count unchanged)
        d = self.make_daemon()
        d.state["demo_bot_cap"] = 5
        d.state["active_bots"]["1"] = {
            "symbol": "PUMP", "venue": "hyperliquid", "bot_code": "OLDBOT",
            "score_final": 40.0,
            "stagnation_policy": {
                "regime": "neutral",
                "stagnant_if": {"min_fills_24h": 1.0,
                                "min_realized_ratio": 0.4},
                "score_drop_rotate": 12.0, "hysteresis_score": 5.0,
                "cooldown_h": 12.0},
            "observed": {"fills_24h": 0.0, "realized_ratio": 0.0},
            "decision_id": "OLD1"}
        challenger = _cand("hyperliquid", "SOL", 90.0)
        self.grid_status_ret = [{"code": "OLDBOT", "status": "stopped"}]
        ok = d.execute_rotation("1", challenger, dry_run=False)
        self.assertTrue(ok)
        self.assertIn("create", [op[0] for op in self.ops])


class TestDynamicSlotOpen(ManageHarness):
    """Dynamic slot mode (default for hyperliquid): slots open while a
    profitable opportunity waits AND deployable capital is spare — capital
    is the ceiling, not a slot count."""

    def _occupy_all(self, d):
        for k, s in enumerate(d.state["slots"], 1):
            d.state["active_bots"][str(s["slot"])] = _active_bot(
                f"SYM{k}", s["venue"], f"B{k}")

    def _hl_slot(self, n, balance=100.0):
        return {"slot": n, "venue": "hyperliquid", "balance": balance,
                "max_commitment": round(balance * 0.5, 2),
                "venue_sleeve": 400.0, "venue_slots": 4}

    def test_opens_beyond_slots_max_while_capital_lasts(self):
        d = self.make_dynamic_daemon()
        # 6 slots (3 HL × 133.33 + 2 BN from the n=5 plan + 1 more HL) =
        # already AT slots_max 6 — dynamic mode ignores the fixed cap
        d.state["slots"] = slot_plan(
            600.0, {"hyperliquid": 400.0, "binance": 200.0}, 5)["slots"] \
            + [self._hl_slot(6)]
        self.assertEqual(len(d.state["slots"]), 6)
        self._occupy_all(d)
        slot, err = d.open_slot("hyperliquid")
        self.assertIsNone(err)
        self.assertEqual(slot["slot"], 7)
        # budget = max(sleeve/(n+1), min_slot_usd) = max(400/5, 100) = 100
        self.assertEqual(slot["balance"], 100.0)
        self.assertEqual(slot["max_commitment"], 50.0)
        self.assertTrue(slot["dynamic"])
        # existing slots KEEP their budgets (no shrink below the floor)
        hl = sorted(s["balance"] for s in d.state["slots"]
                    if s["venue"] == "hyperliquid")
        self.assertEqual(hl, [100.0, 100.0, 133.33, 133.33, 133.33])

    def test_capital_is_the_ceiling(self):
        d = self.make_dynamic_daemon()
        # seeded 4 slots (3 HL × 133.33, 1 BN × 200), ALL occupied
        self._occupy_all(d)
        # ceiling = 600 × 0.85 = 510; committed 200 → spare 310; every new
        # slot is $100/50 (floor) and reserves $50 once opened:
        # 310 → 260 → 210 → 160 → 110 → 60 → 10 → REFUSE (10 < 50)
        d.state["committed"] = {"1": 100.0, "2": 100.0}
        opened = 0
        while True:
            slot, err = d.open_slot("hyperliquid")
            if slot is None:
                self.assertIn("spare", err)
                self.assertIn("$10.00", err)
                break
            opened += 1
            self.assertLessEqual(opened, 10)  # runaway guard for the test
        self.assertEqual(opened, 6)
        self.assertLess(len(d.state["slots"]),
                        d.config["portfolio"]["slots_hard_max"])

    def test_empty_slots_reserve_their_worst_case(self):
        d = self.make_dynamic_daemon()
        # ceiling 510. Slot 1 occupied + committed 200; one EMPTY HL slot
        # already reserves $50 → effective spare 260, not the raw 310
        d.state["slots"] = [
            {"slot": 1, "venue": "hyperliquid", "balance": 100.0,
             "max_commitment": 50.0, "venue_sleeve": 400.0,
             "venue_slots": 2},
            {"slot": 2, "venue": "hyperliquid", "balance": 100.0,
             "max_commitment": 50.0, "venue_sleeve": 400.0,
             "venue_slots": 2},
        ]
        d.state["active_bots"]["1"] = _active_bot("A", "hyperliquid", "B1")
        d.state["committed"] = {"1": 200.0}
        # raw spare (without reservation) would be 510 − 200 = 310 — enough
        # for six $50 worst-cases. With the $50 reservation it is 260:
        # open1 budget max(400/3,100)=133.33 (worst 66.67) → 193.33, then
        # four × $50 → 143.33 → 93.33 → 43.33 → REFUSE
        opened = 0
        while True:
            slot, err = d.open_slot("hyperliquid")
            if slot is None:
                self.assertIn("spare", err)
                self.assertIn("$43.33", err)
                break
            opened += 1
            self.assertLessEqual(opened, 10)
        self.assertEqual(opened, 4)

    def test_respects_slots_hard_max(self):
        d = self.make_dynamic_daemon()
        d.config["portfolio"]["slots_hard_max"] = 5
        d.state["slots"] = slot_plan(
            600.0, {"hyperliquid": 400.0, "binance": 200.0}, 5)["slots"]
        slot, err = d.open_slot("hyperliquid")
        self.assertIsNone(slot)
        self.assertIn("slots_hard_max", err)

    def test_fixed_venue_still_caps_at_slots_max(self):
        d = self.make_dynamic_daemon()
        d.state["slots"] = slot_plan(
            600.0, {"hyperliquid": 400.0, "binance": 200.0}, 5)["slots"] \
            + [{"slot": 6, "venue": "binance", "balance": 100.0,
                "max_commitment": 50.0, "venue_sleeve": 200.0,
                "venue_slots": 3}]
        slot, err = d.open_slot("binance")  # not in dynamic_slot_venues
        self.assertIsNone(slot)
        self.assertIn("slots_max", err)


class TestReconcileSlots(ManageHarness):
    """Config edits must reach the persisted slot plan on restart.

    Slots persist in state.json — without reconciliation, raising
    portfolio.venues + restarting left the fleet sizing from the old
    sleeves while the console config editor showed the new ones
    (the console-vs-backend drift)."""
    def _slots(self, d):
        return {s["slot"]: s for s in d.state["slots"]}

    def test_venue_sleeve_edit_applies_on_restart(self):
        d = self.make_daemon()
        d.reconcile_slots()
        self.assertNotIn("slots-reconciled",
                         [e["kind"] for e in d.state["journal"]])
        # operator raised the HL sleeve 300→450 (total 650); persisted
        # slots still carry the old 2×$150 split
        d.config["portfolio"]["total_usd"] = 650.0
        d.config["portfolio"]["venues"]["hyperliquid"]["balance_usd"] = 450.0
        d.reconcile_slots()
        s = self._slots(d)
        # HL sleeve 450 / 2 slots = 225 each; binance 200/2 = 100
        self.assertEqual(s[1]["balance"], 225.0)
        self.assertEqual(s[2]["balance"], 225.0)
        self.assertEqual(s[1]["max_commitment"], 112.5)
        self.assertEqual(s[1]["venue_sleeve"], 450.0)
        self.assertEqual(s[3]["balance"], 100.0)
        kinds = [e["kind"] for e in d.state["journal"]]
        self.assertIn("slots-reconciled", kinds)

    def test_no_change_no_journal_noise(self):
        d = self.make_daemon()
        d.reconcile_slots()
        self.assertNotIn("slots-reconciled",
                         [e["kind"] for e in d.state["journal"]])

    def test_opened_slot_count_survives_reconcile(self):
        d = self.make_daemon()
        slot, _ = d.open_slot("hyperliquid")
        self.assertEqual(slot["slot"], 5)
        d.config["portfolio"]["total_usd"] = 800.0
        d.config["portfolio"]["venues"]["hyperliquid"]["balance_usd"] = 600.0
        d.config["portfolio"]["venues"]["binance"]["balance_usd"] = 200.0
        d.reconcile_slots()
        # 5 slots kept (runtime truth), HL budgets re-split 3-ways
        self.assertEqual(len(d.state["slots"]), 5)
        self.assertEqual([s["balance"] for s in d.state["slots"]
                          if s["venue"] == "hyperliquid"], [200.0] * 3)
        self.assertEqual([s["balance"] for s in d.state["slots"]
                          if s["venue"] == "binance"], [100.0] * 2)

    def test_dynamic_slot_not_shrunk_below_floor_on_restart(self):
        # a dynamic HL slot opened BEYOND the sleeve (budget $100 while
        # sleeve/n would be less) must not be re-shrunk by reconcile
        d = self.make_dynamic_daemon()
        # sleeve 400 → 3 seeded HL slots at 133.33; grow to 5 HL slots
        d.open_slot("hyperliquid")   # slot 5: max(400/4, 100) = 100
        d.open_slot("hyperliquid")   # slot 6: max(400/5, 100) = 100
        hl = sorted(s["balance"] for s in d.state["slots"]
                    if s["venue"] == "hyperliquid")
        self.assertEqual(hl, [100.0, 100.0, 133.33, 133.33, 133.33])
        d.reconcile_slots()  # sleeve/n = 80 → floor holds $100
        hl = sorted(s["balance"] for s in d.state["slots"]
                    if s["venue"] == "hyperliquid")
        self.assertEqual(hl, [100.0, 100.0, 100.0, 100.0, 100.0])

    def test_bad_config_never_raises(self):
        d = self.make_daemon()
        d.config["portfolio"]["venues"] = {}
        d.reconcile_slots()  # zero funded venues: no-op, no exception
        self.assertEqual(len(d.state["slots"]), 4)


class TestRescreenSlotOpening(ManageHarness):
    def _fill_all_slots(self, d):
        for k, (symbol, venue) in {
                "1": ("HYPE", "hyperliquid"), "2": ("ARB", "hyperliquid"),
                "3": ("SOL", "binance"), "4": ("NEAR", "binance")}.items():
            d.state["active_bots"][k] = _active_bot(symbol, venue, f"B{int(k)}")

    def test_profitable_hl_candidate_opens_slot_and_deploys(self):
        d = self.make_daemon()
        self._fill_all_slots(d)
        cands = [_cand("hyperliquid", "PEPE", 90.0)]
        run_merge_mock = mock.Mock(return_value={"results": cands})
        with mock.patch("daemon.run_merge", run_merge_mock):
            d.rescreen_cycle(dry_run=False, max_new=2)
        # run_merge received the widened-universe config passthrough
        kw = run_merge_mock.call_args.kwargs
        self.assertEqual(kw["min_volume"], 2_000_000)
        self.assertEqual(kw["max_symbols"], 100)
        self.assertEqual(kw["confluence_top"], 10)
        self.assertEqual(kw["top"], 30)
        # the 5th slot opened and the bot was deployed into it
        self.assertEqual(len(d.state["slots"]), 5)
        self.assertIn("5", d.state["active_bots"])
        self.assertEqual(d.state["active_bots"]["5"]["symbol"], "PEPE")
        creates = [op for op in self.ops if op[0] == "create"]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0][1], "hyperliquid")
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertIn("slot-open", kinds)
        self.assertIn("DEPLOY-PAPER", kinds)

    def test_below_score_floor_no_slot(self):
        d = self.make_daemon()
        self._fill_all_slots(d)
        cands = [_cand("hyperliquid", "PEPE", 30.0)]  # < open_slot_min_score
        with mock.patch("daemon.run_merge", return_value={"results": cands}):
            d.rescreen_cycle(dry_run=False, max_new=2)
        self.assertEqual(len(d.state["slots"]), 4)
        self.assertEqual(sorted(d.state["active_bots"]), ["1", "2", "3", "4"])
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertNotIn("slot-open", kinds)
        self.assertNotIn("DEPLOY-PAPER", kinds)

    def test_dry_run_never_grows_slots(self):
        d = self.make_daemon()
        self._fill_all_slots(d)
        cands = [_cand("hyperliquid", "PEPE", 90.0)]
        with mock.patch("daemon.run_merge", return_value={"results": cands}):
            d.rescreen_cycle(dry_run=True, max_new=2)
        self.assertEqual(len(d.state["slots"]), 4)
        self.assertEqual(sorted(d.state["active_bots"]), ["1", "2", "3", "4"])
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertNotIn("slot-open", kinds)

    def test_opened_slot_survives_next_rescreen(self):
        # slots are persisted: after opening, the next cycle plans against
        # the grown set instead of re-seeding the 4-slot default plan
        d = self.make_daemon()
        self._fill_all_slots(d)
        d.state["slots"] = slot_plan(500.0, n_slots=4)["slots"] + [{
            "slot": 5, "venue": "hyperliquid", "balance": 100.0,
            "max_commitment": 50.0, "venue_sleeve": 400.0,
            "venue_slots": 4}]
        cands = [_cand("hyperliquid", "WIF", 88.0)]
        with mock.patch("daemon.run_merge", return_value={"results": cands}):
            d.rescreen_cycle(dry_run=False, max_new=2)
        self.assertIn("5", d.state["active_bots"])
        self.assertEqual(d.state["active_bots"]["5"]["symbol"], "WIF")

    def test_vetoed_opener_does_not_orphan_fresh_slot(self):
        # the candidate that triggers open_slot can still guard-veto; the
        # freshly opened slot must stay visible to later candidates THIS
        # cycle (free is computed before the open) instead of pushing them
        # into another slot-open that then hits slots_max
        d = self.make_daemon()
        self._fill_all_slots(d)
        cands = [_cand("hyperliquid", "VETOED", 95.0),
                 _cand("hyperliquid", "PEPE", 90.0)]
        real = d.plan_candidate

        def fake_plan(cand, slot, dry_run, **kw):
            if cand["symbol"] == "VETOED":
                return None, {"decision": "NO_GO",
                              "veto": "guard-veto"}, None, {}
            return real(cand, slot, dry_run, **kw)

        with mock.patch("daemon.run_merge", return_value={"results": cands}), \
                mock.patch.object(d, "plan_candidate", side_effect=fake_plan):
            d.rescreen_cycle(dry_run=False, max_new=2)
        # one slot was opened and then REUSED by the second candidate
        self.assertEqual(len(d.state["slots"]), 5)
        self.assertIn("5", d.state["active_bots"])
        self.assertEqual(d.state["active_bots"]["5"]["symbol"], "PEPE")


if __name__ == "__main__":
    unittest.main()
