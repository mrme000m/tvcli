#!/usr/bin/env python3
"""Daemon/config side of the 2026-09-06 profit-audit fixes.

Covers (work items 1–7):
  1. config consolidation — derivation invariants (sleeves sum, worst-case
     total ≤ 85% ceiling, no guardrail weakened) + reconcile_slots safety
     for a fixture mirroring the LIVE state (5 bots in slots 1,2,3,5,7)
  2. watch.adjust_cooldown_h tunable (was hardcoded 6 h)
  3. position-optimizer apply path (geometry-only, gates, daily cap,
     shared rate limit, PB update)
  4. take_profit_pct 0.04 defaults
  5. demo-cap nudge gating (auto nudges skipped at 5/5, transition-journaled)
  6. pnl-snapshot observability + /status pnl & demo_cap blocks
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "policy"))

import daemon  # noqa: E402
import pbclient  # noqa: E402
from config_lite import load_yaml  # noqa: E402

try:
    from test_daemon_manage import ManageHarness, make_payloads  # noqa: F401
except ImportError:  # pytest rootdir=agents/grid-autonomy
    from tests.test_daemon_manage import (ManageHarness,  # noqa: F401
                                          make_payloads)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")

# mirror of the live capacity snapshot (binance free tier at its 1-bot cap)
CAPACITY_LIVE = {
    "max_active": {"other": 1, "premium": 200},
    "active": {"other": 1, "premium": {"HYPERLIQUID_SWAP": 4}},
    "used_pairs": {},
}


# ── work item 1: config derivation invariants ──────────────────────────

class TestConfigDerivation(unittest.TestCase):
    """The shipped config.yaml must satisfy the audit math exactly."""

    @classmethod
    def setUpClass(cls):
        with open(CONFIG_PATH) as f:
            cls.cfg = load_yaml(f.read()) or {}

    def test_sleeves_sum_to_total(self):
        p = self.cfg["portfolio"]
        sleeves = sum(v["balance_usd"] for v in p["venues"].values())
        self.assertAlmostEqual(sleeves, p["total_usd"], places=2)

    def test_no_guardrail_weakened(self):
        p = self.cfg["portfolio"]
        self.assertEqual(p["max_alloc_per_slot"], 0.5)
        self.assertEqual(p["cash_buffer_pct"], 0.15)
        au = self.cfg["autonomy"]
        self.assertEqual(au["full_pct"], 0.5)
        # the declared fail-closed checks are unchanged (7 names; the
        # KILL-file gate is the 8th, enforced in guardrails.CHECKS)
        self.assertEqual(
            self.cfg["guardrails"]["checks"],
            ["pairCode_from_get_exchange_markets",
             "profilesCodes_active_with_balance",
             "worst_case_commitment_within_max_alloc",
             "profit_per_grid_ge_2x_spread",
             "venue_side_allowed",
             "reliability_gate",
             "cooldown_and_hysteresis"])
        import execution.guardrails as _g
        self.assertEqual(len(_g.CHECKS), 8)

    def test_hl_slot_unlocks_cashcat_band(self):
        """min_slot_usd 180 → 0.5 × 180 = $90 worst-case cap admits the
        $80–90 worst-case full-density grids the old $50 cap vetoed."""
        p = self.cfg["portfolio"]
        cap_usd = min(p["max_alloc_per_slot"], self.cfg["autonomy"]["full_pct"]) \
            * p["min_slot_usd"]
        self.assertGreaterEqual(cap_usd, 90.0)
        # 18 lines × $10/line exchange floor fit: fit_grids = 2×cap/10
        self.assertGreaterEqual(int(2 * cap_usd / 10.0), 18)

    def test_total_worst_case_within_ceiling(self):
        """4 HL slots × $90 + 1 binance slot × $60 = $420 ≤ 85% of 600."""
        p = self.cfg["portfolio"]
        alloc = min(p["max_alloc_per_slot"], self.cfg["autonomy"]["full_pct"])
        hl = 4 * alloc * p["min_slot_usd"]
        bn = alloc * p["venues"]["binance"]["balance_usd"]  # 1 binance slot
        total = hl + bn
        ceiling = p["total_usd"] * (1 - p["cash_buffer_pct"])
        self.assertAlmostEqual(total, 420.0, places=1)
        self.assertLessEqual(total, ceiling)

    def test_take_profit_and_tunables(self):
        self.assertAlmostEqual(
            self.cfg["grid_defaults"]["take_profit_pct"], 0.04)
        po = self.cfg["position_optimizer"]
        self.assertAlmostEqual(po["take_profit_pct"], 0.04)
        self.assertTrue(po["apply"])
        self.assertEqual(po["max_apply_per_day"], 4)
        self.assertAlmostEqual(self.cfg["watch"]["adjust_cooldown_h"], 2.0)
        self.assertEqual(self.cfg["watch"]["pnl_snapshot_interval_s"], 300)

    def test_default_config_carries_new_tunables(self):
        # code defaults (used when config.yaml omits a knob) match the doc
        d = daemon.Daemon.__new__(daemon.Daemon)
        d.config = daemon.DEFAULT_CONFIG
        self.assertEqual(d._adjust_cooldown_s(), 2 * 3600)
        self.assertEqual(d._pnl_snapshot_interval_s(), 300)


# ── work item 1: reconcile safety on the LIVE-state fixture ────────────

class ReconcileHarness(ManageHarness):
    """ManageHarness + the shipped consolidation config + a slot/bot
    fixture mirroring the live 2026-09-06 state (bots in 1,2,3,5,7)."""

    def make_live_daemon(self):
        d = self.make_daemon()
        p = d.config["portfolio"]
        p["total_usd"] = 600.0
        p["venues"]["hyperliquid"]["balance_usd"] = 480.0
        p["venues"]["binance"]["balance_usd"] = 120.0
        p["slots_max"] = 5
        p["slots_hard_max"] = 9
        p["dynamic_slot_venues"] = ["hyperliquid"]
        p["min_slot_usd"] = 180.0
        # 9 persisted slots exactly like state.json on 2026-09-06:
        # HL 1,2,5,6,7,8,9 + binance 3,4 (all seeded at $100)
        d.state["slots"] = [
            {"slot": i, "venue": "hyperliquid", "balance": 100.0,
             "max_commitment": 50.0, "venue_sleeve": 400.0,
             "venue_slots": 7, **({"dynamic": True} if i >= 6 else {})}
            for i in (1, 2, 5, 6, 7, 8, 9)] + [
            {"slot": i, "venue": "binance", "balance": 100.0,
             "max_commitment": 50.0, "venue_sleeve": 200.0,
             "venue_slots": 2}
            for i in (3, 4)]
        d.state["slots"].sort(key=lambda s: s["slot"])
        for slot, sym, venue in (("1", "DOGE", "hyperliquid"),
                                 ("2", "CHIP", "hyperliquid"),
                                 ("3", "GIGGLE", "binance"),
                                 ("5", "LTC", "hyperliquid"),
                                 ("7", "GRAM", "hyperliquid")):
            d.state["active_bots"][slot] = {
                "symbol": sym, "venue": venue, "bot_code": f"B{slot}",
                "since": "2026-09-05T18:00:00+00:00"}
            d.state["committed"][slot] = 50.0
        d.state["demo_bot_cap"] = 5
        d.state["capacity"] = CAPACITY_LIVE
        return d


class TestReconcileLiveState(ReconcileHarness):
    def test_live_fleet_survives_consolidation(self):
        d = self.make_live_daemon()
        d.reconcile_slots()
        # all five bots keep their slots — no bot orphaned, slot 7 included
        self.assertEqual(sorted(d.state["active_bots"]),
                         ["1", "2", "3", "5", "7"])
        slot_ids = sorted(s["slot"] for s in d.state["slots"])
        self.assertEqual(slot_ids, [1, 2, 3, 5, 7])
        # HL slots raised to the $180 floor → $90 worst-case cap
        hl = [s for s in d.state["slots"] if s["venue"] == "hyperliquid"]
        self.assertEqual([s["balance"] for s in hl], [180.0] * 4)
        self.assertEqual([s["max_commitment"] for s in hl], [90.0] * 4)
        # the single binance slot: 120 sleeve / 1 slot → $60 worst-case
        bn = [s for s in d.state["slots"] if s["venue"] == "binance"]
        self.assertEqual([s["balance"] for s in bn], [120.0])
        self.assertEqual([s["max_commitment"] for s in bn], [60.0])
        # total worst-case at full re-deploy ≤ 85% ceiling
        worst = sum(s["max_commitment"] for s in d.state["slots"])
        self.assertLessEqual(worst, 600.0 * 0.85)
        # committed claims: honest (never raised on growth), never above cap
        for s in d.state["slots"]:
            sk = str(s["slot"])
            self.assertIn(sk, d.state["committed"])
            self.assertLessEqual(d.state["committed"][sk],
                                 s["max_commitment"])
        # the prune is journaled
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertIn("slots-reconciled", kinds)
        prune = [e for e in d.state["journal"]
                 if e.get("kind") == "slots-reconciled"
                 and "pruned unfillable" in e.get("msg", "")]
        self.assertTrue(prune)
        for gone in ("4", "6", "8", "9"):
            self.assertIn(f"slot {gone}", prune[0]["msg"])

    def test_reconcile_idempotent_after_prune(self):
        d = self.make_live_daemon()
        d.reconcile_slots()
        n_journal = len(d.state["journal"])
        d.reconcile_slots()
        # second pass: nothing left to prune or re-normalize
        self.assertEqual(len(d.state["slots"]), 5)
        self.assertEqual(len(d.state["journal"]), n_journal)


class TestReconcilePruneRules(ReconcileHarness):
    def test_headroom_keeps_empty_slots(self):
        d = self.make_live_daemon()
        d.state["demo_bot_cap"] = None       # cap unknown → no demo prune
        d.state["capacity"] = {}             # no tier data → fail-closed
        d.reconcile_slots()
        self.assertEqual(len(d.state["slots"]), 9)   # nothing pruned

    def test_tier_cap_prunes_only_that_venue(self):
        d = self.make_live_daemon()
        d.state["demo_bot_cap"] = None       # headroom on the demo cap…
        # …but binance is at its free-tier plan cap → slot 4 is dead
        d.reconcile_slots()
        slot_ids = sorted(s["slot"] for s in d.state["slots"])
        self.assertEqual(slot_ids, [1, 2, 3, 5, 6, 7, 8, 9])

    def test_occupied_slot_never_pruned_even_at_caps(self):
        d = self.make_live_daemon()
        # binance at tier cap AND its only bot is in slot 3 → 3 stays, 4 goes
        d.state["demo_bot_cap"] = None
        d.reconcile_slots()
        self.assertIn(3, [s["slot"] for s in d.state["slots"]])

    def test_committed_clamped_on_config_shrink(self):
        d = self.make_live_daemon()
        d.state["demo_bot_cap"] = None
        d.state["capacity"] = {}
        # shrink the binance sleeve (HL 540 + BN 60 = 600, so no rescale)
        # → 2 binance slots × $30 → slot 3's $15 worst-case cap < its claim
        d.config["portfolio"]["venues"]["hyperliquid"]["balance_usd"] = 540.0
        d.config["portfolio"]["venues"]["binance"]["balance_usd"] = 60.0
        d.state["committed"]["3"] = 50.0
        d.reconcile_slots()
        s3 = next(s for s in d.state["slots"] if s["slot"] == 3)
        self.assertEqual(s3["max_commitment"], 15.0)
        self.assertEqual(d.state["committed"]["3"], 15.0)
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertIn("slots-reconciled", kinds)
        clamped = [e for e in d.state["journal"]
                   if e.get("kind") == "slots-reconciled"
                   and "clamped" in e.get("msg", "")]
        self.assertTrue(clamped)

    def test_committed_never_raised_on_growth(self):
        d = self.make_live_daemon()
        d.reconcile_slots()
        # the deployed grids still run at their old $50 worst-case — the
        # raised cap only materializes on the next deploy into the slot
        self.assertEqual(d.state["committed"]["1"], 50.0)


# ── work item 2: adjust cooldown tunable ────────────────────────────────

class TestAdjustCooldown(ManageHarness):
    def _seed(self, d):
        d.state["active_bots"]["1"] = {
            "venue": "hyperliquid", "symbol": "SOL", "bot_code": "B1",
            "ticket": {"grid_type": "neutral", "decision": "GO"},
            "stagnation_policy": {"regime": "neutral"},
            "channel": {"mid": 100.0, "step_pct": 0.5, "grids": 10},
            "upsert": {"gridPercentStep": 0.005, "gridLevels": 10,
                       "amountPerTrade": 10.0, "pairCode": "PAIR1"},
            "profile_code": "profile-1", "pair_code": "PAIR1",
        }

    def test_cooldown_from_config(self):
        d = self.make_daemon()
        self.assertEqual(d._adjust_cooldown_s(), 2 * 3600)
        d.config["watch"]["adjust_cooldown_h"] = 0.5
        self.assertEqual(d._adjust_cooldown_s(), 1800)
        d.config["watch"]["adjust_cooldown_h"] = "garbage"
        self.assertEqual(d._adjust_cooldown_s(), 2 * 3600)  # fail-safe

    def test_zero_cooldown_allows_repeated_edits(self):
        d = self.make_daemon()
        self._seed(d)
        d.config["watch"]["adjust_cooldown_h"] = 0
        d.adjust_bot("1", dry_run=False)
        d.adjust_bot("1", dry_run=False)   # no cooldown → not rate limited
        edits = [op for op in self.ops if op[0] == "edit"]
        self.assertEqual(edits, [("edit", "B1", False), ("edit", "B1", False)])

    def test_recenter_precheck_uses_tunable(self):
        d = self.make_daemon()
        self._seed(d)
        d.config["watch"]["adjust_cooldown_h"] = 2.0
        bot = d.state["active_bots"]["1"]
        bot["channel"] = {"low": 90.0, "mid": 100.0, "high": 110.0,
                         "step_pct": 0.5, "grids": 10}
        obs = {"status": "active", "price": 121.0, "error": None,
               "realized_pnl": 0.0, "unrealized_pnl": -0.5,
               "open_lines": 3, "open_losing": 2, "fills_24h": 1,
               "ladder_full": False, "dd_vs_atr_band": 1.4}
        bot["observed"] = obs
        with mock.patch("daemon.observe_all_safe", return_value={"1": obs}), \
                mock.patch.object(daemon.Daemon, "browser_watchdog",
                                 lambda self: True):
            # 3 h since the last edit > 2 h cooldown → recenter fires
            d.state["last_adjust"]["1"] = time.time() - 3 * 3600
            d.health_cycle(dry_run=False)
            kinds = [e.get("kind") for e in d.state["journal"]]
            self.assertIn("recenter", kinds)
            self.assertTrue(any(op[0] == "edit" for op in self.ops))
            # 30 min since the last edit < 2 h → suppressed entirely
            d2_journal_len = len(d.state["journal"])
            d.state["last_adjust"]["1"] = time.time() - 0.5 * 3600
            d.health_cycle(dry_run=False)
            kinds = [e.get("kind") for e in d.state["journal"]]
            self.assertNotIn("recenter", kinds[d2_journal_len:])


# ── work item 3: position-optimizer apply path ─────────────────────────

def _geometry_payload(**over):
    p = {"pairCode": "PAIR1", "lowPrice": 0.09, "midPrice": 0.10,
         "highPrice": 0.11, "gridPercentStep": 0.005, "gridLevels": 10,
         "amountPerTrade": 10.0}
    p.update(over)
    return p


def _rec(name="recenter", delta=205.75, slot="1", bot_code="B1",
         payload=None, **over):
    r = {"slot": slot, "venue": "hyperliquid", "symbol": "SOL",
         "bot_code": bot_code, "recommendation": name,
         "expected_delta_pct": delta, "applied": False, "applied_at": None,
         "action": {"type": "edit", "payload": payload
                    if payload is not None else _geometry_payload(),
                    "apply": True}}
    r.update(over)
    return r


class TestPositionOptimizerApply(ManageHarness):
    def _daemon(self, apply=True, **cfg_over):
        d = self.make_daemon()
        po = {"apply": apply, "min_improvement_pct": 2.0,
              "max_apply_per_day": 4, "enabled": True}
        po.update(cfg_over)
        d.config["position_optimizer"] = po
        d.state["active_bots"]["1"] = {
            "venue": "hyperliquid", "symbol": "SOL", "bot_code": "B1",
            "channel": {"low": 0.09, "mid": 0.10, "high": 0.11,
                        "step_pct": 0.5, "grids": 10},
            "upsert": _geometry_payload(),
            "observed": {"status": "active", "price": 0.16,
                         "realized_pnl": 1.0, "unrealized_pnl": 0.5,
                         "fills_24h": 2},
        }
        return d

    def test_geometry_rec_applied(self):
        d = self._daemon()
        rec = _rec()
        out = d.apply_position_optimizer_recs([rec], dry_run=False)
        self.assertEqual(out, [rec])
        self.assertTrue(any(op == ("edit", "B1", False) for op in self.ops))
        ev = [e for e in d.state["journal"]
              if e.get("kind") == "position-optimizer-applied"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["slot"], "1")
        self.assertEqual(ev[0]["symbol"], "SOL")
        self.assertEqual(ev[0]["recommendation"], "recenter")
        self.assertAlmostEqual(ev[0]["expected_delta_pct"], 205.75)
        self.assertTrue(rec["applied"])
        self.assertTrue(rec["applied_at"])
        # shared rate-limit clock + bookkeeping updated
        self.assertIn("1", d.state["last_adjust"])
        self.assertEqual(d.state["active_bots"]["1"]["channel"]["mid"], 0.10)
        self.assertEqual(d.state["active_bots"]["1"]["channel"]["grids"], 10)
        self.assertEqual(d.state["position_optimizer_applies"]["count"], 1)

    def test_exit_recs_never_applied(self):
        d = self._daemon()
        for name in ("add-take-profit", "add-trailing", "add-stop-loss"):
            rec = _rec(name=name, delta=50.0)
            out = d.apply_position_optimizer_recs([rec], dry_run=False)
        self.assertEqual(out, [])
        self.assertEqual([op for op in self.ops if op[0] == "edit"], [])

    def test_below_improvement_gate_skipped(self):
        d = self._daemon()
        out = d.apply_position_optimizer_recs(
            [_rec(delta=1.99)], dry_run=False)
        self.assertEqual(out, [])
        self.assertEqual([op for op in self.ops if op[0] == "edit"], [])

    def test_apply_disabled_is_noop(self):
        d = self._daemon(apply=False)
        out = d.apply_position_optimizer_recs([_rec()], dry_run=False)
        self.assertEqual(out, [])
        self.assertEqual([op for op in self.ops if op[0] == "edit"], [])

    def test_rate_limit_shared_with_manual_adjusts(self):
        d = self._daemon()
        d.state["last_adjust"]["1"] = time.time() - 600  # 10 min ago
        out = d.apply_position_optimizer_recs([_rec()], dry_run=False)
        self.assertEqual(out, [])
        self.assertEqual([op for op in self.ops if op[0] == "edit"], [])
        skips = [e for e in d.state["journal"]
                 if e.get("kind") == "position-optimizer-skip"]
        self.assertEqual(len(skips), 1)
        self.assertIn("rate limit", skips[0]["msg"])
        # throttled: a second skip in the same hour journals nothing more
        d.apply_position_optimizer_recs([_rec()], dry_run=False)
        skips = [e for e in d.state["journal"]
                 if e.get("kind") == "position-optimizer-skip"]
        self.assertEqual(len(skips), 1)

    def test_daily_cap_vetoes_and_journals_once(self):
        d = self._daemon()
        d.state["position_optimizer_applies"] = {
            "day": daemon.utcnow()[:10], "count": 4}
        d.apply_position_optimizer_recs([_rec()], dry_run=False)
        d.apply_position_optimizer_recs([_rec()], dry_run=False)
        self.assertEqual([op for op in self.ops if op[0] == "edit"], [])
        skips = [e for e in d.state["journal"]
                 if e.get("kind") == "position-optimizer-skip"
                 and "daily" in e.get("msg", "")]
        self.assertEqual(len(skips), 1)

    def test_never_applies_on_stopped_or_errored_bots(self):
        for status in ("stopped", "stopped_and_close_all", "error"):
            d = self._daemon()
            d.state["active_bots"]["1"]["observed"]["status"] = status
            out = d.apply_position_optimizer_recs([_rec()], dry_run=False)
            self.assertEqual(out, [])
        self.assertEqual([op for op in self.ops if op[0] == "edit"], [])

    def test_stale_rec_for_rotated_bot_skipped(self):
        d = self._daemon()
        rec = _rec(bot_code="OLDBOT")   # bot in slot 1 is now B1
        out = d.apply_position_optimizer_recs([rec], dry_run=False)
        self.assertEqual(out, [])
        self.assertEqual([op for op in self.ops if op[0] == "edit"], [])

    def test_exit_keys_stripped_from_geometry_payload(self):
        d = self._daemon()
        payload = _geometry_payload(takeProfitUsd=7.2, stopLossUsd=50.0,
                                    trailingActivationPct=5.0,
                                    trailingExecutePct=2.0,
                                    positionsTrailing=True)
        edits = []

        def capture(bot_code, upsert, dry_run=True):
            edits.append(upsert)
            return {"ok": True}

        with mock.patch("daemon.grid_edit_safe", capture):
            out = d.apply_position_optimizer_recs(
                [_rec(payload=payload)], dry_run=False)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(edits), 1)
        for k in ("takeProfitUsd", "stopLossUsd", "trailingActivationPct",
                  "trailingExecutePct", "positionsTrailing"):
            self.assertNotIn(k, edits[0])

    def test_pb_record_updated_on_apply(self):
        d = self._daemon()
        rec = _rec(id="uuid-1", persisted=True)
        fake = mock.Mock()
        with mock.patch("daemon._pb", return_value=fake):
            d.apply_position_optimizer_recs([rec], dry_run=False)
        fake.recommendation_update.assert_called_once_with(
            "uuid-1", {"applied": True, "applied_at": rec["applied_at"]})

    def test_pb_update_fail_soft(self):
        d = self._daemon()
        with mock.patch("daemon._pb", return_value=None):
            self.assertIsNone(d._pb_recommendation_update(_rec()))
        boom = mock.Mock()
        boom.recommendation_update.side_effect = RuntimeError("pb down")
        with mock.patch("daemon._pb", return_value=boom):
            self.assertIsNone(
                d._pb_recommendation_update(_rec(id="x")))

    def test_apply_pass_never_raises(self):
        d = self._daemon()
        with mock.patch("daemon.grid_edit_safe",
                        side_effect=RuntimeError("boom")):
            out = d.apply_position_optimizer_recs([_rec()], dry_run=False)
        self.assertEqual(out, [])
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertIn("position-optimizer-error", kinds)

    def test_engine_cycle_end_to_end_applies_recenter(self):
        """Real engine cycle (stubbed candles) → daemon applier: the rec
        shapes line up and a Δ+498% recenter is auto-applied through the
        grid-edit path, updating bot bookkeeping."""
        import position_optimizer

        def oscillating(venue, symbol, interval, limit, market="spot"):
            return [(0.101, 0.101, 0.099, 0.099 if i % 2 == 0 else 0.101)
                    for i in range(limit or 300)]

        d = self._daemon()
        bot = d.state["active_bots"]["1"]
        bot["observed"]["fills_24h"] = 4     # far below the ~24 expected
        po = position_optimizer.PositionOptimizer(
            cfg=d.config["position_optimizer"],
            fetch_candles_fn=oscillating,
            journal_fn=lambda e: None, persist_fn=None)
        recs = po.cycle(d.state["active_bots"], dry_run=True, now=1000.0)
        recs = [r for r in recs if r["slot"] == "1"]
        self.assertEqual(recs[0]["recommendation"], "recenter")
        self.assertGreaterEqual(recs[0]["expected_delta_pct"], 2.0)
        out = d.apply_position_optimizer_recs(recs, dry_run=False)
        self.assertEqual(len(out), 1)
        self.assertTrue(any(op == ("edit", "B1", False) for op in self.ops))
        # channel bookkeeping rebuilt from the applied geometry payload
        self.assertEqual(bot["channel"]["step_pct"], 0.5)
        self.assertIn("1", d.state["last_adjust"])

    def test_dry_run_not_counted_against_daily_cap(self):
        d = self._daemon()
        d.apply_position_optimizer_recs([_rec()], dry_run=True)
        self.assertEqual(
            d.state["position_optimizer_applies"].get("count"), 0)
        ev = [e for e in d.state["journal"]
              if e.get("kind") == "position-optimizer-applied"]
        self.assertTrue(ev[0]["dry_run"])


# ── grid-edit failure handling (live incident 2026-09-06 03:56Z) ───────

class TestGridEditFailureHandling(ManageHarness):
    """A FAILED WT grid edit must never be journaled as applied, burn the
    2 h adjust cooldown, or mutate bot bookkeeping — it is retried after
    a 10-min per-slot backoff instead."""

    FAIL = {"ok": False,
            "stdout": '{"code": 500, "message": "Internal Server Error"}'}

    def _daemon(self):
        return TestPositionOptimizerApply._daemon(self)

    def _seed_adjust_bot(self, d):
        d.state["active_bots"]["1"] = {
            "venue": "hyperliquid", "symbol": "SOL", "bot_code": "B1",
            "ticket": {"grid_type": "neutral"},
            "channel": {"mid": 100.0, "step_pct": 0.5, "grids": 10},
            "upsert": {"gridPercentStep": 0.005, "gridLevels": 10,
                       "amountPerTrade": 10.0, "pairCode": "PAIR1"},
            "profile_code": "profile-1", "pair_code": "PAIR1",
        }

    # ── apply pass ───────────────────────────────────────────────────────
    def test_apply_failure_not_treated_as_applied(self):
        d = self._daemon()
        rec = _rec()
        pb = mock.Mock()
        with mock.patch("daemon.grid_edit_safe",
                        return_value=dict(self.FAIL)), \
                mock.patch("daemon._pb", return_value=pb):
            out = d.apply_position_optimizer_recs([rec], dry_run=False)
        self.assertEqual(out, [])
        errs = [e for e in d.state["journal"]
                if e.get("kind") == "position-optimizer-error"]
        applied = [e for e in d.state["journal"]
                   if e.get("kind") == "position-optimizer-applied"]
        self.assertEqual(len(errs), 1)
        self.assertEqual(applied, [])
        self.assertIn("FAILED on WT", errs[0]["msg"])
        self.assertIn("500", errs[0]["msg"])
        self.assertEqual(errs[0]["slot"], "1")
        self.assertEqual(errs[0]["recommendation"], "recenter")
        self.assertNotIn("1", d.state["last_adjust"])
        self.assertFalse(rec.get("applied"))
        self.assertFalse(rec.get("applied_at"))
        self.assertEqual(d.state["position_optimizer_applies"]["count"], 0)
        pb.recommendation_update.assert_not_called()
        # bot bookkeeping untouched for geometry WT never accepted
        bot = d.state["active_bots"]["1"]
        self.assertEqual(bot["channel"]["mid"], 0.10)
        self.assertEqual(bot["upsert"], _geometry_payload())
        self.assertNotIn("last_adjust", bot)
        self.assertIn("1", d.state["last_adjust_failed"])

    def test_apply_failure_backoff_blocks_immediate_retry(self):
        d = self._daemon()
        calls = []

        def capture(bot_code, upsert, dry_run=True):
            calls.append(bot_code)
            return dict(self.FAIL)

        with mock.patch("daemon.grid_edit_safe", capture):
            d.apply_position_optimizer_recs([_rec()], dry_run=False)
            d.apply_position_optimizer_recs([_rec()], dry_run=False)
        self.assertEqual(calls, ["B1"])  # second pass: backoff, no edit

    def test_apply_backoff_expiry_retries_and_still_fails(self):
        d = self._daemon()
        calls = []

        def capture(bot_code, upsert, dry_run=True):
            calls.append(bot_code)
            return dict(self.FAIL)

        with mock.patch("daemon.grid_edit_safe", capture):
            d.apply_position_optimizer_recs([_rec()], dry_run=False)
            # >600 s since the failed edit (equivalent to advancing time)
            d.state["last_adjust_failed"]["1"] -= \
                daemon.ADJUST_FAILED_RETRY_S + 1
            d.apply_position_optimizer_recs([_rec()], dry_run=False)
        self.assertEqual(calls, ["B1", "B1"])
        errs = [e for e in d.state["journal"]
                if e.get("kind") == "position-optimizer-error"]
        self.assertEqual(len(errs), 2)
        self.assertNotIn("1", d.state["last_adjust"])
        self.assertFalse(d.state["active_bots"]["1"].get("last_adjust"))

    def test_apply_failure_then_success_clears_backoff(self):
        d = self._daemon()
        results = [dict(self.FAIL), {"ok": True}]

        def capture(bot_code, upsert, dry_run=True):
            return results.pop(0)

        rec = _rec()
        with mock.patch("daemon.grid_edit_safe", capture):
            self.assertEqual(
                d.apply_position_optimizer_recs([_rec()], dry_run=False),
                [])
            d.state["last_adjust_failed"]["1"] -= \
                daemon.ADJUST_FAILED_RETRY_S + 1
            out = d.apply_position_optimizer_recs([rec], dry_run=False)
        self.assertEqual(out, [rec])
        self.assertTrue(rec["applied"])
        self.assertTrue(rec["applied_at"])
        self.assertIn("1", d.state["last_adjust"])
        self.assertNotIn("1", d.state.get("last_adjust_failed", {}))

    # ── watch lane adjust_bot ────────────────────────────────────────────
    def test_adjust_bot_failure_not_treated_as_applied(self):
        d = self._daemon()
        self._seed_adjust_bot(d)
        before = {"channel": dict(d.state["active_bots"]["1"]["channel"]),
                  "upsert": dict(d.state["active_bots"]["1"]["upsert"])}
        calls = []

        def capture(bot_code, upsert, dry_run=True):
            calls.append(bot_code)
            return dict(self.FAIL)

        with mock.patch("daemon.grid_edit_safe", capture):
            d.adjust_bot("1", dry_run=False)
        self.assertEqual(calls, ["B1"])
        errs = [e for e in d.state["journal"]
                if e.get("kind") == "adjust-error"]
        self.assertEqual(len(errs), 1)
        self.assertIn("grid edit FAILED", errs[0]["msg"])
        self.assertIn("500", errs[0]["msg"])
        self.assertIn("1", d.state["last_adjust_failed"])
        self.assertNotIn("1", d.state["last_adjust"])
        bot = d.state["active_bots"]["1"]
        self.assertNotIn("last_adjust", bot)
        self.assertEqual(bot["channel"], before["channel"])
        self.assertEqual(bot["upsert"], before["upsert"])
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertNotIn("adjust", kinds)

    def test_adjust_bot_backoff_suppresses_refetch(self):
        d = self._daemon()
        self._seed_adjust_bot(d)
        fetches = []

        def counting(venue, symbol, interval, limit, market="spot"):
            fetches.append(symbol)
            return [(100.0, 101.0, 99.0, 100.0 + i * 0.001)
                    for i in range(limit or 100)]

        with mock.patch("daemon.grid_edit_safe",
                        return_value=dict(self.FAIL)), \
                mock.patch("market_regime.fetch_candles", counting):
            d.adjust_bot("1", dry_run=False)
            d.adjust_bot("1", dry_run=False)  # inside the 10-min backoff
        self.assertEqual(fetches, ["SOL"])  # silent: not even a re-fetch


class TestPBRecommendationUpdate(unittest.TestCase):
    def test_patches_by_recommendation_id(self):
        pb = pbclient.PB.__new__(pbclient.PB)
        pb.disabled = False
        with mock.patch.object(pbclient.PB, "list",
                               return_value=[{"id": "pb-9"}]) as ml, \
                mock.patch.object(pbclient.PB, "update",
                                  return_value={"id": "pb-9"}) as mu:
            out = pb.recommendation_update(
                "uuid-1", {"applied": True, "applied_at": "now"})
        ml.assert_called_once_with(
            "recommendations",
            filter='recommendation_id = "uuid-1"', per_page=1)
        mu.assert_called_once_with(
            "recommendations", "pb-9",
            {"applied": True, "applied_at": "now"})
        self.assertEqual(out, {"id": "pb-9"})

    def test_missing_record_and_disabled_guard(self):
        pb = pbclient.PB.__new__(pbclient.PB)
        pb.disabled = False
        with mock.patch.object(pbclient.PB, "list", return_value=[]):
            self.assertIsNone(pb.recommendation_update("x", {}))
        pb.disabled = True
        self.assertIsNone(pb.recommendation_update("x", {}))


# ── work items 4 + 6: take-profit default, pnl snapshot, /status ───────

class TestPnlSnapshot(ManageHarness):
    def _seed(self, d):
        for slot, (sym, r, u, f) in {
                "1": ("DOGE", 0.0, -0.26, 0),
                "2": ("CHIP", 0.576, -1.00, 3),
                "7": ("GRAM", 0.1408, -0.21, 4)}.items():
            d.state["active_bots"][slot] = {
                "symbol": sym, "venue": "hyperliquid", "bot_code": f"B{slot}",
                "observed": {"status": "active", "realized_pnl": r,
                             "unrealized_pnl": u, "fills_24h": f}}
        d.state["committed"] = {"1": 50.0, "2": 50.0, "7": 50.0}

    def test_snapshot_math(self):
        d = self.make_daemon()
        self._seed(d)
        snap = d.pnl_snapshot()
        f = snap["fleet"]
        self.assertAlmostEqual(f["realized"], 0.7168, places=3)
        self.assertAlmostEqual(f["unrealized"], -1.47, places=3)
        self.assertAlmostEqual(f["net"], round(0.7168 - 1.47, 4), places=3)
        self.assertEqual(f["committed_usd"], 150.0)
        self.assertEqual(f["fills_24h"], 7)
        self.assertEqual(snap["bots"]["2"]["symbol"], "CHIP")
        self.assertEqual(snap["bots"]["2"]["fills_24h"], 3)

    def test_idle_usd_from_portfolio_total(self):
        d = self.make_daemon()
        self._seed(d)
        d.config["portfolio"]["total_usd"] = 600.0
        self.assertEqual(d.pnl_snapshot()["fleet"]["idle_usd"], 450.0)

    def test_journal_event_emitted(self):
        d = self.make_daemon()
        self._seed(d)
        d._journal_pnl_snapshot()
        ev = [e for e in d.state["journal"]
              if e.get("kind") == "pnl-snapshot"]
        self.assertEqual(len(ev), 1)
        self.assertIn("fleet", ev[0])
        self.assertIn("bots", ev[0])
        self.assertIn("committed $150.00", ev[0]["msg"])

    def test_fail_soft_on_garbage_observe(self):
        d = self.make_daemon()
        d.state["active_bots"]["1"] = {
            "symbol": "X", "observed": {"status": "active",
                                        "realized_pnl": "n/a",
                                        "unrealized_pnl": None}}
        snap = d.pnl_snapshot()  # must not raise
        self.assertEqual(snap["fleet"]["realized"], 0.0)

    def test_interval_config(self):
        d = self.make_daemon()
        self.assertEqual(d._pnl_snapshot_interval_s(), 300)
        d.config["watch"]["pnl_snapshot_interval_s"] = 0
        self.assertEqual(d._pnl_snapshot_interval_s(), 0)
        d.config["watch"]["pnl_snapshot_interval_s"] = "junk"
        self.assertEqual(d._pnl_snapshot_interval_s(), 300)


class TestCtlStatusPayload(ManageHarness):
    def test_status_has_pnl_and_demo_cap(self):
        import ctl_http
        d = self.make_daemon()
        d.state["active_bots"]["1"] = {
            "symbol": "DOGE", "observed": {"status": "active",
                                            "realized_pnl": 0.5,
                                            "unrealized_pnl": -0.25,
                                            "fills_24h": 2}}
        d.state["demo_bot_cap"] = 5
        payload = ctl_http.status_payload(d)
        self.assertEqual(payload["pnl"]["realized"], 0.5)
        self.assertEqual(payload["pnl"]["unrealized"], -0.25)
        self.assertAlmostEqual(payload["pnl"]["net"], 0.25)
        self.assertEqual(payload["demo_cap"],
                         {"cap": 5, "active": 1, "headroom": 4})
        # the pre-existing blocks are all still there
        for key in ("slots", "active_bots", "committed", "live_allow",
                    "profiles", "capacity", "account_limits",
                    "capabilities", "env", "last_cycle", "journal_tail"):
            self.assertIn(key, payload)

    def test_status_serves_per_bot_pnl_block(self):
        import ctl_http
        d = self.make_daemon()
        d.state["active_bots"]["1"] = {
            "symbol": "DOGE", "observed": {"status": "active",
                                            "realized_pnl": 0.5,
                                            "unrealized_pnl": -0.25,
                                            "fills_24h": 2}}
        payload = ctl_http.status_payload(d)
        # the fleet totals stay flat alongside the per-bot map
        self.assertEqual(payload["pnl"]["realized"], 0.5)
        bot = payload["pnl"]["bots"]["1"]
        self.assertEqual(bot["symbol"], "DOGE")
        self.assertEqual(bot["realized"], 0.5)
        self.assertEqual(bot["unrealized"], -0.25)
        self.assertEqual(bot["fills_24h"], 2.0)
        # projected /24h present even when no stagnation policy applies
        self.assertIn("projected_24h_usd", bot)

    def test_demo_cap_unknown_when_not_learned(self):
        import ctl_http
        d = self.make_daemon()
        payload = ctl_http.status_payload(d)
        self.assertIsNone(payload["demo_cap"]["cap"])
        self.assertIsNone(payload["demo_cap"]["headroom"])

    def test_pnl_block_fail_soft(self):
        import ctl_http

        class _D:
            state = {"slots": [], "active_bots": {}, "committed": {},
                     "live_allow": False, "journal": []}

            def pnl_snapshot(self):
                raise RuntimeError("boom")

        payload = ctl_http.status_payload(_D())
        self.assertEqual(payload["pnl"], {})
        self.assertEqual(payload["demo_cap"]["active"], 0)


# ── work item 5: demo-cap nudge gating ─────────────────────────────────

class TestDemoCapNudgeGating(ManageHarness):
    def _fleet(self, d, n=5, cap=5):
        d.state["demo_bot_cap"] = cap
        for i in range(1, n + 1):
            d.state["active_bots"][str(i)] = {
                "symbol": f"S{i}", "venue": "hyperliquid",
                "bot_code": f"B{i}"}

    def test_auto_nudge_skipped_at_cap_transition_journaled(self):
        d = self.make_daemon()
        self._fleet(d)
        self.assertFalse(d.queue_rescreen())          # gated
        self.assertFalse(d.consume_rescreen())        # nothing queued
        skips = [e for e in d.state["journal"]
                 if e.get("kind") == "demo-cap-nudge-skip"]
        self.assertEqual(len(skips), 1)
        d.queue_rescreen()                            # still capped
        d.queue_rescreen()
        skips = [e for e in d.state["journal"]
                 if e.get("kind") == "demo-cap-nudge-skip"]
        self.assertEqual(len(skips), 1)              # transition only

    def test_headroom_resume_journals_and_queues(self):
        d = self.make_daemon()
        self._fleet(d)
        d.queue_rescreen()
        del d.state["active_bots"]["5"]               # headroom back
        self.assertTrue(d.queue_rescreen())
        self.assertTrue(d.consume_rescreen())
        skips = [e for e in d.state["journal"]
                 if e.get("kind") == "demo-cap-nudge-skip"]
        self.assertEqual(len(skips), 2)               # skip + resume
        self.assertIn("headroom", skips[-1]["msg"])

    def test_manual_force_always_queues(self):
        d = self.make_daemon()
        self._fleet(d)
        self.assertTrue(d.queue_rescreen(force=True))
        self.assertTrue(d.consume_rescreen())

    def test_rescreen_cap_veto_journal_transition_only(self):
        d = self.make_daemon()
        self._fleet(d)
        cands = [{"venue": "hyperliquid", "symbol": "PEPE",
                  "tv_symbol": "BINANCE:PEPEUSDT", "regime": "neutral",
                  "score_final": 90.0, "step": 0.5, "archetype":
                  "Neutral Grid (mean-reversion)"}]
        with mock.patch("daemon.run_merge", return_value={"results": cands}):
            d.rescreen_cycle(dry_run=False, max_new=2)
            d.rescreen_cycle(dry_run=False, max_new=2)
        vetoes = [e for e in d.state["journal"]
                  if e.get("kind") == "demo-cap-veto"]
        self.assertEqual(len(vetoes), 1)


# ── work item 4: take-profit defaults ──────────────────────────────────

class TestTakeProfitDefaults(ManageHarness):
    def test_default_target_from_config(self):
        d = self.make_daemon()
        d.config["grid_defaults"]["take_profit_pct"] = 0.04
        s1 = next(s for s in d.state["slots"] if str(s["slot"]) == "1")
        self.assertEqual(d._default_take_profit("1"),
                         round(s1["balance"] * 0.04, 2))

    def test_zero_disables(self):
        d = self.make_daemon()
        d.config["grid_defaults"]["take_profit_pct"] = 0.0
        self.assertIsNone(d._default_take_profit("1"))


if __name__ == "__main__":
    unittest.main()
