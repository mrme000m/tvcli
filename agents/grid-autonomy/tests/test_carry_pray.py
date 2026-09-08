#!/usr/bin/env python3
"""Unit tests for the carry-and-pray transition (gap-report 2026-09-07).

The carry-and-pray policy moves a bot whose underwater book has been
stuck longer than the token's natural profitable-close time from
``active_bots`` into ``state["carry_pray"]`` and frees the slot. A
server-side takeProfit is placed at break-even + buffer through the
existing ``wt_library.grid_set_exits`` seam (the same path the position
optimizer's exit-add recs use), with the daemon's live-paper gate.

Covers:
  * ``_check_carry_pray_transition`` predicate — gates on open_losing,
    elapsed-vs-carry_after_h, the carrying case is the happy path.
  * Transition side-effects: bot moved to ``carry_pray``; slot freed;
    decision_id outcome recorded; cooldowns_until cleared.
  * ``auto_apply_tp=False`` branch (advisory-only).
  * Completion path: ``_check_carry_pray_completion`` drops entries
    whose bot is in ``STOPPED_STATES`` on WT, records the exit, and
    journals ``carry-pray-exit``.

No network, no WT, no PB. ``ManageHarness`` + injected fakes.
"""
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from unittest import mock

HERE = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "policy"))
sys.path.insert(0, os.path.join(HERE, "execution"))

import daemon  # noqa: E402
import policy.stagnation as stagnation  # noqa: E402
import importlib.util  # noqa: E402
_OBS_PATH = os.path.join(HERE, "execution", "observe.py")
_spec = importlib.util.spec_from_file_location("observe_local", _OBS_PATH)
observe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(observe)


try:
    from test_daemon_manage import ManageHarness  # noqa: E401
except ImportError:
    from tests.test_daemon_manage import ManageHarness  # noqa: E401


def _active_bot(symbol, venue, bot_code, **kwargs):
    bot = {"symbol": symbol, "venue": venue, "bot_code": bot_code}
    bot.update(kwargs)
    return bot


def _now():
    return datetime(2026, 9, 8, 0, 0, 0, tzinfo=timezone.utc).timestamp()


def _bot_with_underwater(venue="hyperliquid", symbol="DASH", open_losing=5,
                         open_lines=10, unrealized=-2.13, age_h=48.0,
                         bot_code="CBOT1", since=None, carry_after_h=24.0,
                         observed_status="active"):
    """One active bot with the carry-eligible shape."""
    if since is None:
        since = (datetime(2026, 9, 8, 0, 0, 0, tzinfo=timezone.utc) -
                 timedelta(hours=age_h)).isoformat()
    return {
        "slot": "1", "bot_code": bot_code, "venue": venue, "symbol": symbol,
        "since": since, "adopted": False, "score_final": 80.0,
        "stagnation_policy": {
            "regime": "neutral", "step": 0.5,
            "avg_holding_h": 30.0, "carry_after_h": carry_after_h,
            "stagnant_if": {"min_fills_24h": 0.66, "min_realized_ratio": 0.4},
            "cooldown_h": 36.0,
        },
        "channel": {"low": 60.0, "mid": 64.0, "high": 68.0,
                    "step_pct": 1.5, "grids": 12},
        "decision_id": "d_test_carry",
        "observed": {
            "status": observed_status, "price": 64.0,
            "fills_24h": 2, "realized_pnl": 0.29,
            "realized_ratio": 0.5, "unrealized_pnl": unrealized,
            "open_lines": open_lines, "open_losing": open_losing,
        },
    }


class TestCarryPrayPolicy(unittest.TestCase):
    """Pure derive_policy fields."""

    def test_carry_constants_present(self):
        self.assertEqual(stagnation.CARRY_AFTER_K, 1.5)
        self.assertEqual(stagnation.CARRY_MIN_H, 0.5)
        self.assertEqual(stagnation.CARRY_MAX_H, 168.0)
        self.assertEqual(stagnation.CARRY_BREAK_EVEN_BUFFER_USD, 0.5)

    def test_derive_policy_includes_carry_after_h(self):
        p = stagnation.derive_policy([1.0, 1.1, 1.0, 1.1] * 100,
                                     "1h", 0.5, "chop_high_volatility")
        self.assertIn("carry_after_h", p)
        self.assertIn("carry_after_k", p)
        self.assertGreaterEqual(p["carry_after_h"], 0.5)
        self.assertLessEqual(p["carry_after_h"], 168.0)


class TestCarryPrayTransition(ManageHarness):
    """The Daemon._check_carry_pray_transition path."""

    def setUp(self):
        super().setUp()
        self.d = self.make_daemon()
        self.d.config["carry_pray"] = {
            "enabled": True, "carry_after_k": 1.5,
            "min_carry_h": 0.5, "max_carry_h": 168.0,
            "auto_apply_tp": True, "tp_break_even_buffer_usd": 0.5,
        }

    def test_no_transition_when_no_open_losing(self):
        bot = _bot_with_underwater(open_losing=0)
        self.d.state["active_bots"]["1"] = bot
        ok, reasons = self.d._check_carry_pray_transition(
            "1", bot, bot["observed"], _now())
        self.assertFalse(ok)
        self.assertIn("1", self.d.state["active_bots"])

    def test_no_transition_when_within_window(self):
        bot = _bot_with_underwater(open_losing=5, age_h=1.0, carry_after_h=24.0)
        self.d.state["active_bots"]["1"] = bot
        ok, _ = self.d._check_carry_pray_transition(
            "1", bot, bot["observed"], _now())
        self.assertFalse(ok)
        self.assertIn("1", self.d.state["active_bots"])

    def test_transition_parkes_bot_and_frees_slot(self):
        bot = _bot_with_underwater(open_losing=5, age_h=48.0,
                                   carry_after_h=24.0,
                                   unrealized=-2.13)
        self.d.state["active_bots"]["1"] = bot
        self.d.state["committed"] = {"1": 70.0}
        # seed a cooldown for the carried venue:symbol BEFORE the
        # transition — the carry flow must clear it so a future
        # challenger can pick the symbol up
        self.d.state.setdefault("cooldowns_until", {})[
            "hyperliquid:DASH"] = _now() + 3600
        # fake apply_fn that records the TP envelope
        applied = {}
        def fake_apply_fn(code, exit_kwargs):
            applied["code"] = code
            applied["kwargs"] = exit_kwargs
            return {"ok": True, "dry_run": True, "transport": "test"}
        self.d.position_optimizer.apply_fn = fake_apply_fn
        ok, reasons = self.d._check_carry_pray_transition(
            "1", bot, bot["observed"], _now())
        self.assertTrue(ok)
        # bot moved out of active_bots, into carry_pray
        self.assertNotIn("1", self.d.state["active_bots"])
        cp = self.d.state["carry_pray"]
        self.assertIn("CBOT1", cp)
        entry = cp["CBOT1"]
        self.assertEqual(entry["source_slot"], "1")
        self.assertGreater(entry["carry_target_tp_usd"], 0)
        # TP applied (auto_apply_tp=True)
        self.assertTrue(entry["take_profit_applied"])
        # the takeProfit target = -unrealized + buffer = 2.13 + 0.5 = 2.63
        self.assertAlmostEqual(applied["kwargs"]["take_profit"], 2.63)
        self.assertEqual(applied["kwargs"]["pnl_compare_type"], "total")
        # committed popped, slot free
        self.assertNotIn("1", self.d.state.get("committed", {}))
        # the cooldowns_until entry for the carried venue:symbol cleared
        self.assertNotIn("hyperliquid:DASH", self.d.state["cooldowns_until"])
        # journal kind is carry-pray-enter
        kinds = [e.get("kind") for e in self.d.state["journal"]]
        self.assertIn("carry-pray-enter", kinds)

    def test_transition_advisory_no_apply(self):
        bot = _bot_with_underwater(open_losing=5, age_h=48.0,
                                   carry_after_h=24.0,
                                   unrealized=-2.0)
        self.d.state["active_bots"]["1"] = bot
        self.d.config["carry_pray"]["auto_apply_tp"] = False
        called = {"n": 0}
        def fake_apply_fn(code, exit_kwargs):
            called["n"] += 1
            return {"ok": True, "dry_run": True}
        self.d.position_optimizer.apply_fn = fake_apply_fn
        self.d._check_carry_pray_transition(
            "1", bot, bot["observed"], _now())
        # apply_fn NOT called when auto_apply_tp=False
        self.assertEqual(called["n"], 0)
        entry = self.d.state["carry_pray"]["CBOT1"]
        self.assertFalse(entry["take_profit_applied"])

    def test_transition_target_is_break_even_plus_buffer(self):
        # 1.50 unrealized loss → target TP = 1.50 + 0.50 = 2.00
        bot = _bot_with_underwater(open_losing=5, age_h=48.0,
                                   carry_after_h=24.0,
                                   unrealized=-1.50)
        self.d.state["active_bots"]["1"] = bot
        captured = {}
        def fake_apply_fn(code, exit_kwargs):
            captured.update(exit_kwargs)
            return {"ok": True, "dry_run": True}
        self.d.position_optimizer.apply_fn = fake_apply_fn
        self.d._check_carry_pray_transition(
            "1", bot, bot["observed"], _now())
        self.assertAlmostEqual(captured["take_profit"], 2.0)
        # already-positive unrealized → target = buffer only (no
        # break-even distance to cover, just a fee margin)
        bot2 = _bot_with_underwater(open_losing=5, age_h=48.0,
                                    carry_after_h=24.0,
                                    unrealized=0.0, bot_code="CBOT2")
        self.d.state["active_bots"]["2"] = bot2
        captured.clear()
        self.d._check_carry_pray_transition(
            "2", bot2, bot2["observed"], _now())
        self.assertEqual(captured.get("take_profit"), 0.5)

    def test_disabled_in_config_no_transition(self):
        self.d.config["carry_pray"]["enabled"] = False
        bot = _bot_with_underwater(open_losing=5, age_h=48.0,
                                   carry_after_h=24.0)
        self.d.state["active_bots"]["1"] = bot
        ok, _ = self.d._check_carry_pray_transition(
            "1", bot, bot["observed"], _now())
        self.assertFalse(ok)
        self.assertIn("1", self.d.state["active_bots"])


class TestCarryPrayCompletion(ManageHarness):
    """When a carried bot stops on WT, the entry is dropped."""

    def setUp(self):
        super().setUp()
        self.d = self.make_daemon()
        self.d.config["carry_pray"] = {
            "enabled": True, "carry_after_k": 1.5,
            "min_carry_h": 0.5, "max_carry_h": 168.0,
            "auto_apply_tp": False, "tp_break_even_buffer_usd": 0.5,
        }

    def test_completion_drops_stopped_bot(self):
        # park a bot manually so the completion path has work to do
        bot = _bot_with_underwater(bot_code="CBOT_STOP")
        bot["observed"]["status"] = "stopped"
        bot["observed"]["unrealized_pnl"] = 0.85
        bot["observed"]["realized_pnl"] = 0.50
        self.d.state["active_bots"]["1"] = bot
        self.d.state["carry_pray"]["CBOT_STOP"] = {
            "bot_code": "CBOT_STOP",
            "bot": dict(bot),
            "source_slot": "1",
            "carry_since": "2026-09-07T22:00:00+00:00",
            "carry_target_tp_usd": 2.63,
            "decision_id": "d_test_complete",
            "take_profit_applied": True,
            "take_profit_envelope": {"ok": True, "dry_run": True},
        }
        # pop the active bot — completion only acts on the carry_pray side
        self.d.state["active_bots"].pop("1", None)
        # stub the daemon's observe_all_safe (the function the
        # completion path actually calls) — the synthetic slot key
        # matches _observe_carry_pray_bot's "cp_<code>" convention
        stopped_obs = {"status": "stopped", "unrealized_pnl": 0.85,
                       "realized_pnl": 0.50, "fills_24h": 0}
        with mock.patch.object(daemon, "observe_all_safe",
                               return_value={
                                   "cp_CBOT_STOP": stopped_obs}):
            self.d._check_carry_pray_completion(_now())
        # entry dropped
        self.assertNotIn("CBOT_STOP", self.d.state["carry_pray"])
        # journal kind is carry-pray-exit
        kinds = [e.get("kind") for e in self.d.state["journal"]]
        self.assertIn("carry-pray-exit", kinds)

    def test_completion_no_op_for_running_bot(self):
        bot = _bot_with_underwater(bot_code="CBOT_RUN", observed_status="active")
        self.d.state["active_bots"]["1"] = bot
        self.d.state["carry_pray"]["CBOT_RUN"] = {
            "bot_code": "CBOT_RUN", "bot": dict(bot),
            "source_slot": "1",
            "carry_since": "2026-09-07T22:00:00+00:00",
            "carry_target_tp_usd": 2.63,
            "decision_id": "d_test_run",
            "take_profit_applied": True, "take_profit_envelope": None,
        }
        running_obs = {"status": "active", "unrealized_pnl": -2.0,
                       "realized_pnl": 0.3, "fills_24h": 1}
        with mock.patch.object(daemon, "observe_all_safe",
                               return_value={"cp_CBOT_RUN": running_obs}):
            exits = self.d._check_carry_pray_completion(_now())
        # still running → not removed
        self.assertIn("CBOT_RUN", self.d.state["carry_pray"])
        self.assertEqual(exits, [])
        kinds = [e.get("kind") for e in self.d.state["journal"]]
        self.assertNotIn("carry-pray-exit", kinds)


if __name__ == "__main__":
    unittest.main()
