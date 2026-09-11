#!/usr/bin/env python3
"""Regression: the demo (paper) grid-bot cap gate must count LIVE bots
(incident 2026-09-10/11 — 130+ futile deploy attempts in ~1.5 h).

WT's `demo-hype` paper profile ran 5 demo grid bots while the daemon
tracked only 3 in `active_bots`: the other 2 were PHANTOMS — parked in
`state["carry_pray"]` by carry-pray-enter, which frees the daemon slot
but leaves the WT-side bot RUNNING with a server-side takeProfit. Every
demo-cap gate counted only `active_bots` (3) vs the learned cap (5), so
the daemon believed headroom=2 and kept deploying into free slots —
every create 400'd ("You've reached the maximum number of Demo Trading
Grid Bots! (Limit: 5)") after a full LLM deliberation.

Fixed semantics under test (hermetic, ManageHarness — no network/WT):
  * `_count_paper_bots` counts WT-side used_pairs when available, else
    the tracked fallback (active_bots + live carry-pray parks).
  * All four legacy scalar gates (deploy loop, transition journal,
    refill-nudge skip, slot prune) use the live total.
  * A carry-pray park still counts toward the cap; when
    `_check_carry_pray_completion` drops the stopped bot, headroom
    returns and deploys resume.
  * used_pairs phantoms (untracked WT bots) veto the per-profile gate at
    the true count (5/5) AND journal a transition-only `phantom-bot`
    line with the profile code + extra pair codes.
  * ctl_http /status demo_cap reports the live count (active=5,
    headroom=0 with phantoms, not active=3/headroom=2).
  * No phantoms → unchanged behavior: the helper returns the tracked
    count and no `phantom-bot` journal fires.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "policy"))

import daemon  # noqa: E402
import ctl_http  # noqa: E402

try:
    from test_daemon_manage import ManageHarness  # noqa: E401
except ImportError:  # pytest rootdir=agents/grid-autonomy
    from tests.test_daemon_manage import ManageHarness  # noqa: E401


P1, P2 = "profile-1", "profile-2"      # harness PROFILES codes
HL_EX, BN_EX = "HYPERLIQUID_SWAP", "BINANCE_FUTURES"


def _cand(venue, symbol, score):
    return {"venue": venue, "symbol": symbol,
            "tv_symbol": f"BINANCE:{symbol}USDT", "regime": "neutral",
            "score_final": score, "step": 0.5,
            "archetype": "Neutral Grid (mean-reversion)"}


def _bot(symbol, venue, bot_code, profile_code, pair_code, **kw):
    bot = {"symbol": symbol, "venue": venue, "bot_code": bot_code,
           "profile_code": profile_code, "pair_code": pair_code}
    bot.update(kw)
    return bot


def _carry(bot_code, bot):
    return {"bot_code": bot_code, "bot": bot, "source_slot": "1",
            "carry_since": "2026-09-10T00:00:00+00:00",
            "carry_target_tp_usd": 1.0, "decision_id": "d_carry",
            "take_profit_applied": True, "take_profit_envelope": None}


def _seed_fleet(d, active, carries=(), used_pairs=None):
    """Seed active bots (slot->bot), carry-pray parks and the capacity
    snapshot in one call. `d` slots: 1,2 hyperliquid; 3,4 binance."""
    for slot, bot in active.items():
        d.state["active_bots"][str(slot)] = bot
    for bot_code, bot in carries:
        d.state["carry_pray"][bot_code] = _carry(bot_code, bot)
    if used_pairs is not None:
        d.state["capacity"] = {"used_pairs": used_pairs}


def _run_cycle(d, cands, dry_run=False, max_new=2):
    with mock.patch("daemon.run_merge",
                    return_value={"results": cands}), \
            mock.patch("daemon.time.sleep", new=lambda s: None):
        d.rescreen_cycle(dry_run=dry_run, max_new=max_new)


def _journal(d, kind):
    return [e for e in d.state["journal"] if e.get("kind") == kind]


class TestLiveDemoCapCount(ManageHarness):
    """The demo-cap gates count LIVE bots (WT used_pairs + carry-pray
    parks), not just tracked active_bots."""

    # ── criterion 1: carry-pray parks count toward the cap ──────────────
    def test_carry_pray_counts_toward_legacy_cap_deploy_vetoed(self):
        d = self.make_daemon()
        d.state["demo_bot_cap"] = 5
        # 3 tracked active (2 HL + 1 BN) + 2 carry-pray parks still
        # running on WT = 5 live demo bots = the cap
        _seed_fleet(
            d,
            {"1": _bot("HYPE", "hyperliquid", "B1", P1, "HYPEUSDT"),
             "2": _bot("ARB", "hyperliquid", "B2", P1, "ARBUSDT"),
             "3": _bot("SOL", "binance", "B3", P2, "SOLUSDT")},
            carries=[("C1", _bot("BTC", "hyperliquid", "C1", P1,
                                 "BTCUSDT")),
                     ("C2", _bot("NEAR", "hyperliquid", "C2", P1,
                                 "NEARUSDT"))])
        self.assertEqual(daemon._count_paper_bots_total(d.state), 5)
        self.assertEqual(daemon._count_paper_bots(d.state, P1), 4)
        _run_cycle(d, [_cand("hyperliquid", "PEPE", 90.0)])
        # at the cap: no create attempted, one transition veto line
        self.assertEqual([op for op in self.ops if op[0] == "create"], [])
        vetoes = _journal(d, "demo-cap-veto")
        self.assertEqual(len(vetoes), 1)
        self.assertIn("5/5", vetoes[0]["msg"])
        # still at the cap next cycle → transition-only, no new line
        _run_cycle(d, [_cand("hyperliquid", "PEPE", 90.0)])
        self.assertEqual(len(_journal(d, "demo-cap-veto")), 1)

    def test_carry_pray_counts_toward_per_profile_cap(self):
        # per-profile gate (no legacy scalar learned): profile-1 sits at
        # 1 active + 1 carry = its cap of 2 → deploy into free HL slot 2
        # is vetoed instead of attempting a create
        d = self.make_daemon()
        d.state["demo_bot_cap"] = None
        d.state["demo_bot_caps"] = {P1: 2}
        _seed_fleet(
            d,
            {"1": _bot("HYPE", "hyperliquid", "B1", P1, "HYPEUSDT")},
            carries=[("C1", _bot("BTC", "hyperliquid", "C1", P1,
                                 "BTCUSDT"))])
        self.assertEqual(daemon._count_paper_bots(d.state, P1), 2)
        _run_cycle(d, [_cand("hyperliquid", "PEPE", 90.0)])
        self.assertEqual([op for op in self.ops if op[0] == "create"], [])
        vetoes = [e for e in _journal(d, "demo-cap-veto")
                  if e.get("profile") == P1]
        self.assertEqual(len(vetoes), 1)
        self.assertIn("2/2", vetoes[0]["msg"])

    # ── criterion 2: used_pairs phantoms → per-profile veto 5/5 ────────
    #            + transition-only phantom-bot journal ──────────────────
    def _phantom_state(self):
        d = self.make_daemon()
        d.state["demo_bot_cap"] = None
        d.state["demo_bot_caps"] = {P1: 5, P2: 5}
        # daemon tracks 2 on profile-1, WT runs 5 (3 phantoms)
        _seed_fleet(
            d,
            {"1": _bot("HYPE", "hyperliquid", "B1", P1, "HYPEUSDT"),
             "2": _bot("ARB", "hyperliquid", "B2", P1, "ARBUSDT"),
             "3": _bot("SOL", "binance", "B3", P2, "SOLUSDT")},
            used_pairs={HL_EX: {P1: ["HYPEUSDT", "ARBUSDT", "BTCUSDT",
                                     "NEARUSDT", "DOGEUSDT"]},
                        BN_EX: {P2: ["SOLUSDT"]}})
        return d

    def test_used_pairs_phantoms_veto_at_true_count_and_journal(self):
        d = self._phantom_state()
        self.assertEqual(daemon._count_paper_bots(d.state, P1), 5)
        _run_cycle(d, [_cand("hyperliquid", "PEPE", 90.0)])
        # gate vetoes at the WT-side truth 5/5 — no create attempted
        self.assertEqual([op for op in self.ops if op[0] == "create"], [])
        vetoes = [e for e in _journal(d, "demo-cap-veto")
                  if e.get("profile") == P1]
        self.assertEqual(len(vetoes), 1)
        self.assertIn("5/5", vetoes[0]["msg"])
        # loud transition-only phantom-bot journal with the profile code
        phantoms = _journal(d, "phantom-bot")
        self.assertEqual(len(phantoms), 1)
        self.assertEqual(phantoms[0]["profile"], P1)
        self.assertIn("5", phantoms[0]["msg"])
        self.assertIn("2", phantoms[0]["msg"])
        self.assertIn("BTCUSDT", phantoms[0]["msg"])
        # transition-only: a second cycle journals nothing new
        _run_cycle(d, [_cand("hyperliquid", "PEPE", 90.0)])
        self.assertEqual(len(_journal(d, "phantom-bot")), 1)

    def test_phantom_journal_rearms_after_resolution(self):
        d = self._phantom_state()
        d._check_phantom_bots()
        self.assertEqual(len(_journal(d, "phantom-bot")), 1)
        # phantom resolved (WT-side bots stopped) → state clears silently
        d.state["capacity"] = {"used_pairs": {
            HL_EX: {P1: ["HYPEUSDT", "ARBUSDT"]},
            BN_EX: {P2: ["SOLUSDT"]}}}
        d._check_phantom_bots()
        self.assertEqual(len(_journal(d, "phantom-bot")), 1)
        self.assertEqual(getattr(d, "_phantom_profiles", set()), set())
        # mismatch returns → journals again (re-armed transition)
        d.state["capacity"] = {"used_pairs": {
            HL_EX: {P1: ["HYPEUSDT", "ARBUSDT", "BTCUSDT"]}}}
        d._check_phantom_bots()
        self.assertEqual(len(_journal(d, "phantom-bot")), 2)

    # ── criterion 3: carry-pray exit restores headroom ──────────────────
    def test_carry_pray_completion_restores_headroom(self):
        d = self.make_daemon()
        d.state["demo_bot_cap"] = 5
        _seed_fleet(
            d,
            {"1": _bot("HYPE", "hyperliquid", "B1", P1, "HYPEUSDT"),
             "2": _bot("ARB", "hyperliquid", "B2", P1, "ARBUSDT"),
             "3": _bot("SOL", "binance", "B3", P2, "SOLUSDT")},
            carries=[("C1", _bot("BTC", "hyperliquid", "C1", P1,
                                 "BTCUSDT")),
                     ("C2", _bot("NEAR", "hyperliquid", "C2", P1,
                                 "NEARUSDT"))])
        # at the cap (3 active + 2 carry): no create
        _run_cycle(d, [_cand("binance", "PEPE", 90.0)])
        self.assertEqual([op for op in self.ops if op[0] == "create"], [])
        # both carried bots stopped on WT → entries dropped
        stopped = {"status": "stopped", "unrealized_pnl": 0.5,
                   "realized_pnl": 0.25, "fills_24h": 0}
        with mock.patch.object(
                daemon, "observe_all_safe",
                return_value={"cp_C1": dict(stopped),
                              "cp_C2": dict(stopped)}):
            exits = d._check_carry_pray_completion(1000.0)
        self.assertEqual(len(exits), 2)
        self.assertEqual(d.state["carry_pray"], {})
        # count dropped → headroom back → the deploy now proceeds
        self.assertEqual(daemon._count_paper_bots_total(d.state), 3)
        _run_cycle(d, [_cand("binance", "PEPE", 90.0)])
        creates = [op for op in self.ops if op[0] == "create"]
        self.assertEqual(len(creates), 1)
        self.assertIn("4", d.state["active_bots"])

    # ── criterion 4: refill nudge skipped at the LIVE cap ──────────────
    def test_refill_nudge_skipped_at_live_cap_with_phantoms(self):
        d = self._phantom_state()
        d.state["demo_bot_cap"] = 5
        # only 3 tracked active bots but 6 live (3 phantoms on P1)
        self.assertEqual(daemon._count_paper_bots_total(d.state), 6)
        self.assertFalse(d.queue_rescreen())          # gated
        self.assertFalse(d.consume_rescreen())        # nothing queued
        skips = _journal(d, "demo-cap-nudge-skip")
        self.assertEqual(len(skips), 1)
        self.assertIn("6/5", skips[0]["msg"])
        # transition-only: repeated nudges journal nothing new
        d.queue_rescreen()
        self.assertEqual(len(_journal(d, "demo-cap-nudge-skip")), 1)
        # headroom back (phantoms stopped on WT) → nudges resume
        d.state["capacity"] = {"used_pairs": {
            HL_EX: {P1: ["HYPEUSDT", "ARBUSDT"]},
            BN_EX: {P2: ["SOLUSDT"]}}}
        self.assertTrue(d.queue_rescreen())
        self.assertTrue(d.consume_rescreen())
        skips = _journal(d, "demo-cap-nudge-skip")
        self.assertEqual(len(skips), 2)
        self.assertIn("headroom", skips[-1]["msg"])

    # ── criterion 5: /status demo_cap reports the live count ────────────
    def test_status_demo_cap_reports_live_count_with_phantoms(self):
        d = self._phantom_state()
        payload = ctl_http.status_payload(d)
        pp = payload["demo_cap"]["per_profile"]
        self.assertEqual(pp[P1], {"cap": 5, "active": 5, "headroom": 0})
        self.assertEqual(pp[P2], {"cap": 5, "active": 1, "headroom": 4})
        self.assertEqual(payload["demo_cap"]["total"]["active"], 6)

    def test_status_demo_cap_counts_carry_pray_without_capacity(self):
        # capacity snapshot unavailable → the tracked fallback still
        # counts carry-pray parks (never active=3 when 5 run on WT)
        d = self.make_daemon()
        d.state["demo_bot_caps"] = {P1: 5}
        _seed_fleet(
            d,
            {"1": _bot("HYPE", "hyperliquid", "B1", P1, "HYPEUSDT"),
             "2": _bot("ARB", "hyperliquid", "B2", P1, "ARBUSDT"),
             "3": _bot("SOL", "binance", "B3", P2, "SOLUSDT")},
            carries=[("C1", _bot("BTC", "hyperliquid", "C1", P1,
                                 "BTCUSDT")),
                     ("C2", _bot("NEAR", "hyperliquid", "C2", P1,
                                 "NEARUSDT"))])
        payload = ctl_http.status_payload(d)
        pp = payload["demo_cap"]["per_profile"]
        self.assertEqual(pp[P1]["active"], 4)
        self.assertEqual(pp[P2]["active"], 1)

    # ── criterion 6: no phantoms → unchanged behavior ──────────────────
    def test_no_phantoms_returns_tracked_count_no_journal(self):
        state = {
            "demo_bot_caps": {"PHL": 5},
            "active_bots": {
                "1": {"profile_code": "PHL", "venue": "hyperliquid",
                      "pair_code": "HYPEUSDT"},
                "2": {"profile_code": "PHL", "venue": "hyperliquid",
                      "pair_code": "ARBUSDT"},
            },
            "carry_pray": {},
            "profiles": [
                {"code": "PHL", "name": "demo-hype",
                 "exchange": "HYPERLIQUID_SWAP", "paperTrading": True},
            ],
        }
        # no capacity snapshot → the tracked count, exactly as before
        self.assertEqual(daemon._count_paper_bots(state, "PHL"), 2)
        # WT-side view consistent with the tracked set → still 2
        state["capacity"] = {"used_pairs": {
            "HYPERLIQUID_SWAP": {"PHL": ["HYPEUSDT", "ARBUSDT"]}}}
        self.assertEqual(daemon._count_paper_bots(state, "PHL"), 2)
        self.assertEqual(daemon._count_paper_bots_total(state), 2)
        # fail-closed max: a mid-cycle deploy is tracked but not yet in
        # the once-per-cycle snapshot → count never drops below tracked
        state["active_bots"]["3"] = {"profile_code": "PHL",
                                     "venue": "hyperliquid",
                                     "pair_code": "BTCUSDT"}
        self.assertEqual(daemon._count_paper_bots(state, "PHL"), 3)
        # and no phantom-bot journal fires while the views agree
        d = self.make_daemon()
        d.state["profiles"] = state["profiles"]
        d.state["active_bots"] = {k: dict(v) for k, v
                                  in state["active_bots"].items()}
        d.state["carry_pray"] = {}
        d.state["capacity"] = state["capacity"]
        d._check_phantom_bots()
        self.assertEqual(_journal(d, "phantom-bot"), [])

    def test_helper_guards(self):
        # unknown profile / non-paper profile → 0, never raises
        state = {"profiles": [], "active_bots": {"1": {"venue": "x"}},
                 "carry_pray": {"c": {"bot": {}}}}
        self.assertEqual(daemon._count_paper_bots(state, "nope"), 0)
        self.assertEqual(daemon._count_paper_bots_total(state), 0)
        self.assertEqual(daemon._count_paper_bots(state, None), 0)


if __name__ == "__main__":
    unittest.main()
