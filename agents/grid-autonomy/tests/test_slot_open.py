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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "policy"))

try:
    from test_daemon_manage import ManageHarness, PROFILES  # unittest discover
except ImportError:  # pytest rootdir=agents/grid-autonomy
    from tests.test_daemon_manage import ManageHarness, PROFILES

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
        self.assertEqual(slot["balance"], round(400.0 / 3, 2))   # 400 / 3 HL slots
        self.assertEqual(slot["max_commitment"],
                         round(round(400.0 / 3, 2) * 0.5, 2))
        self.assertEqual(len(d.state["slots"]), 5)
        # existing HL slot budgets re-normalized to the 3-way split
        hl = [s for s in d.state["slots"] if s["venue"] == "hyperliquid"]
        self.assertEqual([s["balance"] for s in hl],
                         [round(400.0 / 3, 2)] * 3)
        kinds = [e["kind"] for e in d.state["journal"]]
        self.assertIn("slot-open", kinds)

    def test_refuses_at_slots_max(self):
        d = self.make_daemon()
        base = slot_plan(500.0, n_slots=5)["slots"]
        d.state["slots"] = base + [{
            "slot": 6, "venue": "hyperliquid", "balance": 100.0,
            "max_commitment": 50.0, "venue_sleeve": 400.0, "venue_slots": 4}]
        slot, err = d.open_slot("hyperliquid")
        self.assertIsNone(slot)
        self.assertIn("slots_max", err)
        self.assertEqual(len(d.state["slots"]), 6)

    def test_refuses_when_capital_tight(self):
        d = self.make_daemon()
        # committed 510 = the whole deployable ceiling → no spare for the
        # new slot's worst-case commitment
        d.state["committed"] = {"1": 260.0, "2": 250.0}
        slot, err = d.open_slot("hyperliquid")
        self.assertIsNone(slot)
        self.assertIn("spare", err)
        self.assertEqual(len(d.state["slots"]), 4)

    def test_refuses_unfunded_venue(self):
        d = self.make_daemon()
        slot, err = d.open_slot("bybit")
        self.assertIsNone(slot)
        self.assertIn("not funded", err)


class TestReconcileSlots(ManageHarness):
    """Config edits must reach the persisted slot plan on restart.

    Slots persist in state.json — without reconciliation, raising
    portfolio.venues + restarting left the fleet sizing from the old
    sleeves while the console config editor showed the new ones
    (the console-vs-backend drift)."""
    # pin the portfolio to the harness slot plan (500 = 300 HL + 200 BN)
    # so the tests do not depend on the live config.yaml
    def _pin(self, d):
        d.config["portfolio"]["total_usd"] = 500.0
        d.config["portfolio"]["venues"]["hyperliquid"]["balance_usd"] = 300.0
        d.config["portfolio"]["venues"]["binance"]["balance_usd"] = 200.0
        return d

    def _slots(self, d):
        return {s["slot"]: s for s in d.state["slots"]}

    def test_venue_sleeve_edit_applies_on_restart(self):
        d = self._pin(self.make_daemon())
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
        d = self._pin(self.make_daemon())
        d.reconcile_slots()
        self.assertNotIn("slots-reconciled",
                         [e["kind"] for e in d.state["journal"]])

    def test_opened_slot_count_survives_reconcile(self):
        d = self._pin(self.make_daemon())
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

    def test_bad_config_never_raises(self):
        d = self._pin(self.make_daemon())
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
