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
        self.assertEqual(slot["balance"], 100.0)   # 300 / 3 HL slots
        self.assertEqual(slot["max_commitment"], 50.0)
        self.assertEqual(len(d.state["slots"]), 5)
        # existing HL slot budgets re-normalized to the 3-way split
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
        # committed 425 = the whole deployable ceiling → no spare for the
        # new slot's $50 worst-case
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
            "max_commitment": 50.0, "venue_sleeve": 300.0,
            "venue_slots": 3}]
        cands = [_cand("hyperliquid", "WIF", 88.0)]
        with mock.patch("daemon.run_merge", return_value={"results": cands}):
            d.rescreen_cycle(dry_run=False, max_new=2)
        self.assertIn("5", d.state["active_bots"])
        self.assertEqual(d.state["active_bots"]["5"]["symbol"], "WIF")


if __name__ == "__main__":
    unittest.main()
