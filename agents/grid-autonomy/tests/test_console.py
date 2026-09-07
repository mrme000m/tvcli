#!/usr/bin/env python3
"""Offline tests for the console backend (yaml_edit + server shaping + HTTP).

Network-touching helpers are pointed at dead ports / temp dirs so the tests
are hermetic; the real daemon is never contacted.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
GRID = os.path.dirname(HERE)
sys.path.insert(0, GRID)
sys.path.insert(0, os.path.join(GRID, "console"))

import yaml_edit  # noqa: E402
import server  # noqa: E402

REAL_CONFIG = os.path.join(GRID, "config.yaml")

CONFIG_TMPL = """# grid-autonomy daemon contract — comment that must survive
portfolio:
  total_usd: 500.0
  venues:
    hyperliquid: { balance_usd: 300.0, market: perps, grids: [Long, Short, Neutral] }
    binance:     { balance_usd: 200.0, market: spot,  grids: [Long, Neutral] }
  slots_default: 4          # four slots
  max_alloc_per_slot: 0.5
  cash_buffer_pct: 0.15

screen:
  rescreen_minutes: 60       # hourly

watch:
  interval_s: 60
  adjust_steps_threshold: 2.0

policy:
  hysteresis_score: 5.0
  min_hold_h: 24             # churn guard

memory:
  k: 3

autonomy:
  mode: auto                 # comment on container
  base_pct: 0.25
  live_profiles: []
"""


class TestYamlEdit(unittest.TestCase):
    def setUp(self):
        self.text = CONFIG_TMPL

    def test_get_block_leaves(self):
        self.assertEqual(yaml_edit.get_value(self.text, "portfolio.total_usd"),
                         (500.0, True))
        self.assertEqual(yaml_edit.get_value(self.text, "screen.rescreen_minutes"),
                         (60, True))
        self.assertEqual(yaml_edit.get_value(self.text, "policy.min_hold_h"),
                         (24, True))
        self.assertEqual(yaml_edit.get_value(self.text, "memory.k"), (3, True))

    def test_get_flow_leaves(self):
        self.assertEqual(
            yaml_edit.get_value(self.text,
                                "portfolio.venues.hyperliquid.balance_usd"),
            (300.0, True))
        self.assertEqual(
            yaml_edit.get_value(self.text,
                                "portfolio.venues.binance.balance_usd"),
            (200.0, True))

    def test_get_unknown(self):
        self.assertEqual(yaml_edit.get_value(self.text, "no.such.path"),
                         (None, False))
        self.assertEqual(yaml_edit.get_value(self.text, "portfolio.nope"),
                         (None, False))

    def test_set_block_leaf_preserves_comments(self):
        out = yaml_edit.set_value(self.text, "screen.rescreen_minutes", 30)
        self.assertIsNotNone(out)
        self.assertIn("rescreen_minutes: 30       # hourly", out)
        self.assertIn("# grid-autonomy daemon contract", out)
        self.assertNotIn("60       # hourly", out)

    def test_set_flow_leaf_preserves_siblings(self):
        out = yaml_edit.set_value(
            self.text, "portfolio.venues.hyperliquid.balance_usd", 420.5)
        line = [l for l in out.splitlines() if "hyperliquid:" in l][0]
        self.assertIn("balance_usd: 420.5", line)
        self.assertIn("market: perps", line)
        self.assertIn("grids: [Long, Short, Neutral]", line)

    def test_set_refuses_unknown(self):
        self.assertIsNone(yaml_edit.set_value(self.text, "no.such", 1))
        self.assertIsNone(yaml_edit.set_value(self.text, "portfolio", 1))

    def test_round_trip_through_config_lite(self):
        sys.path.insert(0, GRID)
        from config_lite import load_yaml
        out = yaml_edit.set_value(self.text, "watch.interval_s", 45)
        cfg = load_yaml(out)
        self.assertEqual(cfg["watch"]["interval_s"], 45)
        self.assertEqual(cfg["portfolio"]["venues"]["binance"]["balance_usd"], 200.0)

    def test_real_config_paths(self):
        with open(REAL_CONFIG) as f:
            text = f.read()
        for path in server.EDITABLE:
            _val, ok = yaml_edit.get_value(text, path)
            self.assertTrue(ok, f"{path} missing from real config.yaml")


class ConsoleTestCase(unittest.TestCase):
    """Isolated server globals: temp state dir, dead ctl/PB ports."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="console-test-")
        self._saved = {
            "STATE_DIR": server.STATE_DIR, "CONFIG_PATH": server.CONFIG_PATH,
            "KILL_FILE": server.KILL_FILE, "PB_URL": server.PB_URL,
        }
        server.STATE_DIR = os.path.join(self.tmp, "state")
        server.CONFIG_PATH = os.path.join(self.tmp, "config.yaml")
        server.KILL_FILE = os.path.join(self.tmp, "KILL")
        server.PB_URL = "http://127.0.0.1:59999"
        os.makedirs(server.STATE_DIR, exist_ok=True)
        with open(server.CONFIG_PATH, "w") as f:
            f.write(CONFIG_TMPL)
        # point the network-touching helpers at dead ports so tests are hermetic
        self._fn_patches = [("_ctl_port", server._ctl_port, lambda: 59999),
                            ("_launchd_managed", server._launchd_managed,
                             lambda: False),
                            ("_pid", server._pid, lambda: None)]
        for name, _old, new in self._fn_patches:
            setattr(server, name, new)
        # keep the PB read path hermetic too: no pbclient adapter (which
        # would otherwise seed the real .pocketbase/pb.env creds and hit
        # a live local PocketBase); tests that want it patch it again.
        self._real_pb_client = server._pb_client
        server._pb_client = lambda: None

    def tearDown(self):
        server._pb_client = self._real_pb_client
        for name, old, _new in self._fn_patches:
            setattr(server, name, old)
        for key, value in self._saved.items():
            setattr(server, key, value)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_state(self, state):
        with open(os.path.join(server.STATE_DIR, "state.json"), "w") as f:
            json.dump(state, f)


class TestShaping(ConsoleTestCase):
    def test_tier_ladder(self):
        self.assertEqual(server._tier({"samples": 2, "profit_factor": 9.0,
                                       "recent_pf": 9.0}), "base")
        self.assertEqual(server._tier({"samples": 12, "profit_factor": 1.1,
                                       "recent_pf": 1.2}), "probe")
        self.assertEqual(server._tier({"samples": 40, "profit_factor": 1.4,
                                       "recent_pf": 1.2}), "full")
        self.assertEqual(server._tier({"samples": 40, "profit_factor": 1.4,
                                       "recent_pf": 0.8}), "killed")

    def test_enriched_bots_stagnation(self):
        self.write_state({
            "active_bots": {"1": {
                "symbol": "PUMP", "venue": "hyperliquid",
                "ticket": {"grid_type": "long"},
                "stagnation_policy": {"stagnant_if": {
                    "min_fills_24h": 3.8, "min_realized_ratio": 0.4}},
                "observed": {"fills_24h": 0, "realized_ratio": 0.0,
                             "price": 0.0043, "status": "active"},
                "channel": {"low": 0.004, "mid": 0.0043, "high": 0.0046,
                            "grids": 10},
            }},
            "committed": {"1": 37.5},
        })
        bots = server._enriched_bots(server._load_state())
        self.assertEqual(len(bots), 1)
        self.assertTrue(bots[0]["stagnant"])
        self.assertEqual(bots[0]["committed"], 37.5)
        self.assertEqual(bots[0]["grid_type"], "long")

    def test_enriched_bots_carries_current_exits(self):
        # the exit-profile fields the daemon health cycle projects
        # (bot.exits / observed.exits) must reach the console-facing
        # enriched bot record — the fleet card exit badge renders from it
        self.write_state({
            "active_bots": {
                "1": {"symbol": "PUMP", "venue": "hyperliquid",
                      "ticket": {"grid_type": "long"},
                      "observed": {"price": 0.004, "status": "active",
                                   "exits": {"takeProfit": 5,
                                             "stopLoss": None}},
                      "exits": {"takeProfit": 5, "stopLoss": 3,
                                "trailingStopActivation": 5,
                                "trailingStopExecute": 2,
                                "strategyStopLossFixedPercentRatio": 0.05}},
                "2": {"symbol": "DOGE", "venue": "binance",
                      "ticket": {"grid_type": "neutral"},
                      "observed": {"price": 0.10, "status": "active"}},
            },
        })
        bots = server._enriched_bots(server._load_state())
        by_slot = {str(b["slot"]): b for b in bots}
        self.assertEqual(by_slot["1"]["exits"]["takeProfit"], 5)
        self.assertEqual(by_slot["1"]["exits"]["strategyStopLossFixedPercentRatio"], 0.05)
        # bot.exits wins over observed.exits; bots without exits get None
        self.assertEqual(by_slot["1"]["exits"]["stopLoss"], 3)
        self.assertIsNone(by_slot["2"]["exits"])

    def test_enriched_bots_attaches_tvcli_fit_from_cache(self):
        # When the bot's symbol/venue appears in state.screen_cache, the
        # enriched record should carry the tvcli_fit block + notes — the
        # Fleet rail renders these directly on the slot card.
        self.write_state({
            "active_bots": {"1": {"symbol": "PUMP", "venue": "hyperliquid",
                                   "ticket": {"grid_type": "long"},
                                   "observed": {"price": 0.004, "status": "active"},
                                   "decision_id": "d-test"}},
            "screen_cache": {"at": time.time(),
                             "candidates": [{"venue": "hyperliquid", "symbol": "PUMP",
                                             "score_final": 120.5,
                                             "confluence_bonus": 5.0,
                                             "confluence_ok": 6,
                                             "confluence_notes": ["moves-large",
                                                                  "high-chop-harvest"],
                                             "tvcli_fit": {"chop": 62.3,
                                                           "mtf_composite": 12.4,
                                                           "atr_pct": 2.1}}]},
        })
        bots = server._enriched_bots(server._load_state())
        self.assertEqual(len(bots), 1)
        b = bots[0]
        self.assertEqual(b["tvcli_bonus"], 5.0)
        self.assertEqual(b["tvcli_ok"], 6)
        self.assertEqual(b["tvcli_notes"][0], "moves-large")
        self.assertEqual(b["tvcli_fit"]["chop"], 62.3)
        # screen_age_min is rounded to 1 decimal; just-now cache → 0
        self.assertIn("screen_age_min", b)
        self.assertLessEqual(b["screen_age_min"], 1)
        # bot missing from the latest cache → no fit block (None keys
        # render as "—" in the slot card, never as a crash)
        self.write_state({
            "active_bots": {"1": {"symbol": "X", "venue": "binance",
                                   "observed": {}}},
            "screen_cache": {"at": time.time(),
                             "candidates": [{"venue": "hyperliquid", "symbol": "PUMP"}]},
        })
        bots = server._enriched_bots(server._load_state())
        self.assertIsNone(bots[0]["tvcli_fit"])
        self.assertIsNone(bots[0]["tvcli_bonus"])

    def test_enriched_bots_attaches_decision_evidence(self):
        # When the bot carries decision_id and the id exists in
        # decisions.jsonl, the enriched record should carry the full
        # decision object so the Fleet rail can deep-link to it.
        with open(os.path.join(server.STATE_DIR, "decisions.jsonl"), "w") as f:
            f.write(json.dumps({"id": "d-test", "symbol": "PUMP",
                                "venue": "hyperliquid", "regime": "chop",
                                "evidence": {"confidence": 0.9,
                                             "llm": {"bull": "mistral"}}}) + "\n")
            f.write(json.dumps({"id": "d-other", "symbol": "X",
                                "venue": "binance"}) + "\n")
        self.write_state({
            "active_bots": {"1": {"symbol": "PUMP", "venue": "hyperliquid",
                                   "observed": {}, "decision_id": "d-test"}},
        })
        bots = server._enriched_bots(server._load_state())
        self.assertIsNotNone(bots[0]["decision"])
        self.assertEqual(bots[0]["decision"]["evidence"]["llm"]["bull"], "mistral")

    def test_decision_payload_by_id_with_cohort(self):
        # /api/decisions/<id> returns the row + cohort_size/cohort_realized
        # rolled up from same-symbol+venue+regime siblings.
        rows = [
            {"id": "d-a", "at": "2026-09-01T00:00:00", "symbol": "PUMP",
             "venue": "hyperliquid", "regime": "chop",
             "outcome": {"realized_pnl": 1.5}},
            {"id": "d-b", "at": "2026-09-02T00:00:00", "symbol": "PUMP",
             "venue": "hyperliquid", "regime": "chop",
             "outcome": {"realized_pnl": -0.3}},
            {"id": "d-c", "at": "2026-09-03T00:00:00", "symbol": "PUMP",
             "venue": "binance", "regime": "chop"},   # different venue → excluded
            {"id": "d-d", "at": "2026-09-04T00:00:00", "symbol": "X",
             "venue": "hyperliquid", "regime": "chop"},  # different sym → excluded
        ]
        with open(os.path.join(server.STATE_DIR, "decisions.jsonl"), "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        p = server.decision_payload_by_id("d-b")
        self.assertIsNotNone(p)
        self.assertEqual(p["decision"]["id"], "d-b")
        self.assertEqual(p["cohort_size"], 1)            # only d-a matches
        self.assertAlmostEqual(p["cohort_realized"], 1.5)
        self.assertIsNone(server.decision_payload_by_id("d-missing"))
        self.assertIsNone(server.decision_payload_by_id(""))

    def test_optimizer_swap_log_shape(self):
        # /api/optimizer/swap-log rolls up state.optimizer into a
        # console-friendly shape with idle-min per slot + at_iso on every swap.
        self.write_state({"optimizer": {
            "trackers": {"1": {"last_fills": 2.0,
                               "last_increase_at": time.time() - 600},
                          "2": {"last_fills": 0.0,
                                "last_increase_at": time.time() - 60}},
            "swap_log": [{"slot": "1", "at": time.time() - 60, "ok": True}],
            "cycles": 5, "swaps_total": 1,
            "last_report": {"arbiter": {"slot": "1", "approve": True,
                                       "challenger": "ETH",
                                       "confidence": 0.8,
                                       "llm": "mistral"}}}})
        sl = server.optimizer_swap_log()
        self.assertEqual(sl["cycles"], 5)
        self.assertEqual(sl["swaps_total"], 1)
        self.assertEqual(len(sl["trackers"]), 2)
        # trackers sorted so the most-idle slot surfaces first
        self.assertGreater(sl["trackers"][0]["idle_min"],
                           sl["trackers"][1]["idle_min"])
        self.assertEqual(len(sl["swaps"]), 1)
        self.assertIn("at_iso", sl["swaps"][0])
        self.assertEqual(sl["last_arbiter"]["challenger"], "ETH")

    def test_optimizer_swap_log_fail_soft_no_state(self):
        # No state.json on disk → empty shape (never a 500)
        sl = server.optimizer_swap_log()
        self.assertEqual(sl["cycles"], 0)
        self.assertEqual(sl["swaps_total"], 0)
        self.assertEqual(sl["trackers"], [])
        self.assertEqual(sl["swaps"], [])
        self.assertIsNone(sl["last_arbiter"])

    def test_reliability_payload_includes_ladder_progression(self):
        # The new kill_thresholds + per-archetype ladder_next fields
        # power the "next rung" column in the Reliability view.
        with open(os.path.join(server.STATE_DIR, "reliability.json"), "w") as f:
            json.dump({
                "Chop harvest": {"samples": 5, "profit_factor": 0.9,
                                 "recent_pf": 1.5, "synthetic_samples": 0},
                "Trend long": {"samples": 15, "profit_factor": 1.2,
                               "recent_pf": 1.1, "synthetic_samples": 0},
                "Killed one": {"samples": 30, "profit_factor": 5.0,
                               "recent_pf": 0.7, "synthetic_samples": 0},
            }, f)
        p = server.reliability_payload()
        self.assertIn("kill_thresholds", p)
        self.assertEqual(p["kill_thresholds"]["kill_min_samples"], 10)
        # base → probe
        a = p["archetypes"]["Chop harvest"]
        self.assertEqual(a["tier"], "base")
        self.assertEqual(a["ladder_next"], "probe")
        self.assertEqual(a["ladder_next_at"], p["ladder"]["probe_samples"])
        # probe → full
        b = p["archetypes"]["Trend long"]
        self.assertEqual(b["tier"], "probe")
        self.assertEqual(b["ladder_next"], "full")
        # killed: no next rung
        k = p["archetypes"]["Killed one"]
        self.assertEqual(k["tier"], "killed")
        self.assertIsNone(k["ladder_next"])
        # real_samples subtracts synthetic from samples
        self.assertEqual(a["real_samples"], 5)

    def test_decisions_payload_newest_first(self):
        with open(os.path.join(server.STATE_DIR, "decisions.jsonl"), "w") as f:
            for i, sym in enumerate(["AAA", "BBB", "CCC"]):
                f.write(json.dumps({"id": f"d1-{i}", "at": f"2026-09-04T1{i}:00:00",
                                    "symbol": sym}) + "\n")
        rows = server.decisions_payload(10)
        self.assertEqual([r["symbol"] for r in rows], ["CCC", "BBB", "AAA"])

    def test_reports_index_and_detail_shape(self):
        rdir = os.path.join(server.STATE_DIR, "reports")
        os.makedirs(rdir)
        with open(os.path.join(rdir, "20260904T204520Z-rescreen.json"), "w") as f:
            json.dump({"at": "2026-09-04T20:45:20+00:00",
                       "cycle_kind": "rescreen",
                       "screen": {"n_candidates": 54, "top3": [
                           {"venue": "hyperliquid", "symbol": "PUMP",
                            "regime": "chop", "score_final": 113.8}]}}, f)
        idx = server.reports_index()
        self.assertEqual(idx[0]["kind"], "rescreen")
        self.assertTrue(idx[0]["json"])
        scr = server.screen_payload()
        self.assertEqual(scr["n_candidates"], 54)
        self.assertEqual(scr["top"][0]["symbol"], "PUMP")

    def test_position_sweeps_payload(self):
        self.write_state({"journal": [
            {"kind": "screen", "msg": "unrelated", "at": "2026-09-07T00:00:00"},
            {"kind": "position-optimizer", "msg": "keep hyperliquid:DOGE",
             "at": "2026-09-07T01:00:00"},
            {"kind": "position-optimizer-applied", "msg": "recenter slot 2",
             "at": "2026-09-07T02:00:00"},
            {"kind": "position-optimizer-sweep", "msg": "3 bots analyzed",
             "at": "2026-09-07T03:00:00"},
            {"kind": "position-optimizer-skip", "msg": "cooldown",
             "at": "2026-09-07T04:00:00"},   # near-miss kind: excluded
        ]})
        sweeps = server.position_sweeps_payload()
        self.assertEqual([e["kind"] for e in sweeps],
                         ["position-optimizer-sweep",
                          "position-optimizer-applied",
                          "position-optimizer"])   # newest first, filtered
        # limit keeps the TAIL (most recent), not the head
        self.assertEqual(len(server.position_sweeps_payload(2)), 2)
        self.assertEqual(server.position_sweeps_payload(2)[0]["kind"],
                         "position-optimizer-sweep")

    def test_position_sweeps_fail_soft_no_state(self):
        # no state.json written yet → [] , never an exception
        self.assertEqual(server.position_sweeps_payload(), [])

    def test_recommendations_journal_fallback_for_dry_run(self):
        # a dry-run mirror never persists recs to PB; the Optimizer view
        # must fall back to the state journal so real rec activity shows
        self.write_state({
            "journal": [
                {"kind": "position-optimizer",
                 "msg": "recenter hyperliquid:PUMP (Δ+32.36%, conf 1.00)",
                 "slot": "1", "symbol": "PUMP",
                 "recommendation": "recenter",
                 "expected_delta_pct": 32.3636, "trigger": "periodic",
                 "dry_run": True, "at": "2026-09-07T18:47:00+00:00"},
                {"kind": "pnl-snapshot", "msg": "fleet net $+1",
                 "at": "2026-09-07T18:46:00+00:00"},
            ],
        })
        payload = server.recommendations_payload(limit=200)
        self.assertEqual(payload["source"], "journal")
        recs = payload["recommendations"]
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["symbol"], "PUMP")
        self.assertEqual(r["venue"], "hyperliquid")
        self.assertEqual(r["recommendation"], "recenter")
        self.assertEqual(r["blocked_by"], "journal-only")
        self.assertTrue(r["journal_only"])
        # journal-derived rows never count toward the PB persist cap
        self.assertEqual(payload["persisted_today"], 0)

    def test_logs_grep(self):
        with open(os.path.join(server.STATE_DIR, "daemon.log"), "w") as f:
            f.write("a stale line\nb stagnant line\nc veto line\n")
        out = server.logs_payload(10, "stagnant|veto")
        self.assertEqual(out["lines"], ["b stagnant line", "c veto line"])

    def test_config_payload_whitelist(self):
        payload = server.config_payload()
        self.assertIn("portfolio.total_usd", payload["editable"])
        self.assertEqual(payload["editable"]["portfolio.total_usd"]["value"], 500.0)
        self.assertNotIn("autonomy.live_profiles", payload["editable"])

    def test_apply_config_edits_ok(self):
        code, resp = server.apply_config_edits({"watch.interval_s": 45,
                                                "memory.k": 5})
        self.assertEqual(code, 200)
        self.assertEqual(len(resp["applied"]), 2)
        self.assertTrue(resp["restart_required"])
        self.assertTrue(os.path.exists(server.CONFIG_PATH + ".bak"))
        from config_lite import load_yaml
        cfg = load_yaml(open(server.CONFIG_PATH).read())
        self.assertEqual(cfg["watch"]["interval_s"], 45)
        self.assertEqual(cfg["memory"]["k"], 5)

    def test_apply_config_edits_rejects(self):
        code, resp = server.apply_config_edits({
            "autonomy.live_profiles": ["x"],       # not whitelisted
            "watch.interval_s": 1,                 # out of range
            "no.such.path": 5,                     # unknown
        })
        self.assertEqual(code, 400)
        self.assertEqual(len(resp["rejected"]), 3)


class TestHTTP(ConsoleTestCase):
    """Full request/response cycle against a throwaway server instance."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def call(self, path, method="GET", body=None, headers=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_overview_graceful_when_daemon_down(self):
        self.write_state({"slots": [{"slot": 1, "venue": "binance"}],
                          "active_bots": {}, "journal": [{"kind": "cycle",
                                                          "msg": "x",
                                                          "at": "2026-09-04T00:00:00"}]})
        code, body = self.call("/api/overview")
        self.assertEqual(code, 200)
        self.assertFalse(body["daemon"]["running"])
        self.assertTrue(body["daemon"]["kill_file"] is False)
        self.assertEqual(len(body["journal_tail"]), 1)
        self.assertFalse(body["pocketbase"]["up"])

    def test_kill_requires_confirm_and_writes_file(self):
        code, body = self.call("/api/ctl/kill", "POST", {})
        self.assertEqual(code, 400)
        code, body = self.call("/api/ctl/kill", "POST", {"confirm": True})
        self.assertEqual(code, 200)
        self.assertTrue(os.path.exists(server.KILL_FILE))

    def test_unkill_removes_file(self):
        open(server.KILL_FILE, "w").write("now")
        code, _ = self.call("/api/ctl/unkill", "POST", {"confirm": True})
        self.assertEqual(code, 200)
        self.assertFalse(os.path.exists(server.KILL_FILE))

    def test_config_post_over_http(self):
        code, body = self.call("/api/config", "POST",
                               {"edits": {"portfolio.total_usd": 750.0}})
        self.assertEqual(code, 200)
        self.assertEqual(body["applied"][0]["value"], 750.0)

    def test_daemon_start_refused_when_running_none_and_kill(self):
        # pid patched to None and KILL armed → start must 409 without clear_kill
        open(server.KILL_FILE, "w").write("now")
        code, body = self.call("/api/daemon/start", "POST",
                               {"confirm": True, "live_paper": True})
        self.assertEqual(code, 409)
        self.assertTrue(body.get("kill_present"))

    def test_daemon_restart_passes_live_paper_flag(self):
        # The restart endpoint must forward live_paper to daemon_restart.
        called = {}
        real = server.daemon_restart
        def spy(clear_kill=False, live_paper=None):
            called["clear_kill"] = clear_kill
            called["live_paper"] = live_paper
            return 200, {"restarted": True, "mode": "live-paper" if live_paper else "dry-run"}
        server.daemon_restart = spy
        try:
            code, body = self.call("/api/daemon/restart", "POST",
                                   {"confirm": True, "live_paper": True})
            self.assertEqual(code, 200)
            self.assertFalse(called.get("clear_kill"))
            self.assertTrue(called.get("live_paper"))
            code, body = self.call("/api/daemon/restart", "POST",
                                   {"confirm": True, "live_paper": False})
            self.assertEqual(code, 200)
            self.assertFalse(called.get("live_paper"))
            code, body = self.call("/api/daemon/restart", "POST",
                                   {"confirm": True})
            self.assertEqual(code, 200)
            self.assertIsNone(called.get("live_paper"))
        finally:
            server.daemon_restart = real

    def test_daemon_restart_clears_its_own_stop_kill(self):
        # daemon_stop() arms the KILL file; the manual restart path must pass
        # clear_kill=True to daemon_start — otherwise a restart 409s on its
        # own stop marker and, in the VPS container, bricks the daemon into a
        # KILL boot-loop (console 502s).
        calls = {}
        real_lm, real_pid, real_stop, real_start = (
            server._launchd_managed, server._pid, server.daemon_stop,
            server.daemon_start)
        server._launchd_managed = lambda: False
        server._pid = lambda: None
        def spy_stop(force=False):
            calls["stop"] = True
            return 200, {"stopped": True}
        def spy_start(live_paper=False, clear_kill=False):
            calls["start_clear_kill"] = clear_kill
            calls["start_live_paper"] = live_paper
            return 200, {"started": True}
        server.daemon_stop, server.daemon_start = spy_stop, spy_start
        try:
            code, body = server.daemon_restart()
            self.assertEqual(code, 200)
            self.assertTrue(calls["stop"])
            self.assertTrue(calls["start_clear_kill"])
            self.assertFalse(calls["start_live_paper"])
        finally:
            server._launchd_managed, server._pid = real_lm, real_pid
            server.daemon_stop, server.daemon_start = real_stop, real_start

    def test_position_sweeps_endpoint(self):
        self.write_state({"journal": [
            {"kind": "position-optimizer", "msg": "keep binance:AAA",
             "at": "2026-09-07T05:00:00"},
        ]})
        code, body = self.call("/api/position-sweeps")
        self.assertEqual(code, 200)
        self.assertEqual(len(body["sweeps"]), 1)
        self.assertEqual(body["sweeps"][0]["kind"], "position-optimizer")
        # no state at all → 200 with the empty shape (fail-soft)
        self.write_state({})
        code, body = self.call("/api/position-sweeps")
        self.assertEqual(code, 200)
        self.assertEqual(body["sweeps"], [])

    def test_static_index_served(self):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/", timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("mission console", resp.read().decode())

    def test_meta_includes_wt_account(self):
        code, body = self.call("/api/meta")
        self.assertEqual(code, 200)
        self.assertIn("wt_account", body)
        self.assertIn(body["wt_account"], ("local (Mac account)", "vps (vault account)"))

    def test_path_traversal_refused(self):
        code, body = self.call("/api/../console/server.py")
        self.assertEqual(code, 404)

    def test_cross_origin_post_refused(self):
        code, body = self.call("/api/ctl/rescreen", "POST", {},
                               headers={"Origin": "https://evil.example"})
        self.assertEqual(code, 403)

    def test_ctl_post_errors_pass_daemon_message_through(self):
        # daemon answered but refused (e.g. optimizer import failed) — the
        # operator must see the daemon's own message, not "ctl unreachable"
        real_ctl = server._ctl
        server._ctl = lambda path, method="GET", body=None: (
            False, {"error": "optimizer unavailable (import failed — "
                             "see journal)"})
        try:
            code, body = self.call("/api/ctl/optimize", "POST", {})
        finally:
            server._ctl = real_ctl
        self.assertEqual(code, 502)
        self.assertEqual(body["error"], "optimizer unavailable "
                                        "(import failed — see journal)")
        self.assertEqual(body["detail"]["error"], "optimizer unavailable "
                                                  "(import failed — see journal)")

    def test_ctl_post_error_when_daemon_down_names_the_connection(self):
        # nothing answered (dead ctl port from setUp) — the raw transport
        # error is shown rather than a misleading bare "ctl unreachable"
        code, body = self.call("/api/ctl/rescreen", "POST", {})
        self.assertEqual(code, 502)
        self.assertIn("urlopen error", body["error"])

    # ── dev-script actions ────────────────────────────────────────────

    def test_dev_actions_require_confirm(self):
        for action in ("reset", "reset-wt", "clean"):
            code, body = self.call(f"/api/dev/{action}", "POST", {})
            self.assertEqual(code, 400)
            self.assertIn("confirm", body["error"])

    def test_dev_action_missing_script(self):
        real = server.DEV_SCRIPT
        server.DEV_SCRIPT = os.path.join(self.tmp, "no-such-dev")
        try:
            for action in ("reset", "reset-wt", "clean"):
                code, body = self.call(f"/api/dev/{action}", "POST",
                                        {"confirm": True})
                self.assertEqual(code, 500)
                self.assertIn("not found", body["error"])
        finally:
            server.DEV_SCRIPT = real

    def test_dev_action_spawns_detached(self):
        """confirm:true → 200 started + the dev script is spawned detached."""
        import subprocess as sp

        spawned = {}

        class FakePopen:
            def __init__(self, args, **kw):
                spawned["args"] = args
                spawned["kw"] = kw

        real_popen, real_script = sp.Popen, server.DEV_SCRIPT
        # point at this test file so isfile() passes
        server.DEV_SCRIPT = os.path.abspath(__file__)
        sp.Popen = FakePopen
        try:
            for action, extra in (("reset", {"keep_decisions": True,
                                            "start": True}),
                                  ("reset-wt", {}), ("clean", {})):
                code, body = self.call(f"/api/dev/{action}", "POST",
                                       {"confirm": True, **extra})
                self.assertEqual(code, 200)
                self.assertTrue(body["started"])
                self.assertEqual(body["action"], action)
                self.assertEqual(spawned["args"][0], server.DEV_SCRIPT)
                self.assertEqual(spawned["args"][1], action)
                self.assertIn("--yes", spawned["args"])
                self.assertEqual(spawned["kw"]["cwd"], server.GRID_HOME)
                self.assertTrue(spawned["kw"]["start_new_session"])
                if action == "reset":
                    self.assertIn("--keep-decisions", spawned["args"])
                    self.assertIn("--start", spawned["args"])
        finally:
            sp.Popen = real_popen
            server.DEV_SCRIPT = real_script


class TvcliStubHandler(BaseHTTPRequestHandler):
    """Local stand-in for the tvcli server's POST /fetch: records request
    bodies, answers with deterministic newest-first periods (like tvcli)."""

    state = {"hits": 0, "bodies": []}

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) if n else b"{}")
        except Exception:
            body = {}
        TvcliStubHandler.state["hits"] += 1
        TvcliStubHandler.state["bodies"].append(body)
        bars = int(body.get("bars", 0))
        periods = [{"time": 1700000000 + (bars - 1 - i) * 3600,
                    "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i,
                    "close": 100.5 + i, "volume": 10.0}
                   for i in range(bars)]   # newest-first, like real tvcli
        out = json.dumps({"symbol": body.get("symbol"),
                          "timeframe": body.get("timeframe"),
                          "bars": bars, "periods": periods}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class TestChart(ConsoleTestCase):
    """GET /api/chart against a local tvcli stub (hermetic)."""

    @classmethod
    def setUpClass(cls):
        cls.stub = ThreadingHTTPServer(("127.0.0.1", 0), TvcliStubHandler)
        threading.Thread(target=cls.stub.serve_forever, daemon=True).start()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls._saved_tvcli = server.TVCLI_BASE
        server.TVCLI_BASE = f"http://127.0.0.1:{cls.stub.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        server.TVCLI_BASE = cls._saved_tvcli
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.stub.shutdown()
        cls.stub.server_close()

    def setUp(self):
        super().setUp()
        TvcliStubHandler.state["hits"] = 0
        TvcliStubHandler.state["bodies"] = []
        server._CHART_CACHE.clear()

    def call(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_chart_happy_path_oldest_first(self):
        code, body = self.call("/api/chart?venue=binance&symbol=DASH/USDT")
        self.assertEqual(code, 200)
        self.assertEqual(body["count"], 96)
        bars = body["bars"]
        self.assertEqual(len(bars), 96)
        self.assertEqual(bars[0]["t"], 1700000000)           # oldest first
        self.assertEqual(bars[-1]["t"], 1700000000 + 95 * 3600)
        for b in bars:
            self.assertEqual(sorted(b), ["c", "h", "l", "o", "t"])
            self.assertIsInstance(b["t"], int)
            self.assertIsInstance(b["c"], float)
        self.assertEqual(body["venue"], "binance")
        self.assertEqual(body["symbol"], "DASH/USDT")
        self.assertEqual(body["interval"], "1h")
        self.assertIn("at", body)
        # the stub saw the mapped TradingView symbol
        self.assertEqual(TvcliStubHandler.state["bodies"][0]["symbol"],
                         "BINANCE:DASHUSDT")
        self.assertEqual(TvcliStubHandler.state["bodies"][0]["timeframe"], "1h")

    def test_chart_hyperliquid_rides_binance_pair(self):
        code, body = self.call("/api/chart?venue=hyperliquid&symbol=pump")
        self.assertEqual(code, 200)
        self.assertEqual(TvcliStubHandler.state["bodies"][0]["symbol"],
                         "BINANCE:PUMPUSDT")   # USDT quote appended

    def test_chart_unknown_venue_400(self):
        code, body = self.call("/api/chart?venue=kraken&symbol=ETHUSDT")
        self.assertEqual(code, 400)
        self.assertIn("error", body)

    def test_chart_bad_interval_400(self):
        code, body = self.call("/api/chart?venue=binance&symbol=ETHUSDT&interval=2h")
        self.assertEqual(code, 400)
        self.assertIn("error", body)

    def test_chart_bars_clamped(self):
        code, body = self.call("/api/chart?venue=binance&symbol=ETHUSDT&bars=9999")
        self.assertEqual(code, 200)
        self.assertEqual(body["count"], 500)               # clamped to 500
        self.assertEqual(TvcliStubHandler.state["bodies"][0]["bars"], 500)

    def test_chart_tvcli_outage_fail_soft(self):
        real = server.TVCLI_BASE
        server.TVCLI_BASE = "http://127.0.0.1:59998"       # dead port
        try:
            code, body = self.call("/api/chart?venue=binance&symbol=BTCUSDT")
        finally:
            server.TVCLI_BASE = real
        self.assertEqual(code, 200)                        # never a 500
        self.assertEqual(body["bars"], [])
        self.assertEqual(body["count"], 0)
        self.assertTrue(body.get("error"))

    def test_chart_cached_second_call_no_stub_hit(self):
        code, first = self.call("/api/chart?venue=binance&symbol=SOLUSDT")
        self.assertEqual(code, 200)
        self.assertEqual(TvcliStubHandler.state["hits"], 1)
        code, second = self.call("/api/chart?venue=binance&symbol=SOLUSDT")
        self.assertEqual(code, 200)
        self.assertEqual(TvcliStubHandler.state["hits"], 1)  # served from cache
        self.assertEqual(second["bars"], first["bars"])


class TestPnlPayload(ConsoleTestCase):
    """pnl_payload: pbclient adapter first, state.json fallback last."""

    def test_pb_client_primary_path(self):
        recs = [
            {"at": "2026-09-07T01:00:00+00:00", "kind": "pnl-snapshot",
             "extra": json.dumps({"fleet": {"net": 1.25, "realized": 1.0},
                                  "bots": {"2": {"symbol": "PUMP"}}})},
            {"at": "2026-09-07T02:00:00+00:00", "kind": "pnl-snapshot",
             "fleet": {"net": 2.0}},
        ]

        class StubPB:
            def __init__(self, rows):
                self.rows, self.calls = rows, []

            def list(self, coll, filter=None, sort=None, page=1, per_page=50):
                self.calls.append((coll, filter, sort, per_page))
                return self.rows

        stub = StubPB(recs)
        server._pb_client = lambda: stub
        out = server.pnl_payload()
        self.assertEqual(out["source"], "pocketbase")
        self.assertEqual(out["total"], 2)
        # the adapter was queried with the documented filter/sort/page size
        self.assertEqual(stub.calls[0], ("journal", "(kind='pnl-snapshot')",
                                         "-at", 200))
        # newest first, extra-as-JSON-string payload unpacked
        self.assertEqual(out["points"][0]["at"], "2026-09-07T02:00:00+00:00")
        self.assertEqual(out["points"][0]["fleet"], {"net": 2.0})
        self.assertEqual(out["points"][1]["fleet"], {"net": 1.25, "realized": 1.0})
        self.assertEqual(out["points"][1]["bots"]["2"]["symbol"], "PUMP")

    def test_pb_client_empty_falls_back_to_state(self):
        self.write_state({"journal": [
            {"kind": "pnl-snapshot", "at": "2026-09-07T03:00:00+00:00",
             "fleet": {"net": 5.0}},
        ]})

        class StubPB:
            def list(self, *a, **k):
                return []

        server._pb_client = lambda: StubPB()
        out = server.pnl_payload()
        self.assertEqual(out["source"], "state")   # PB dead/empty → fallback
        self.assertEqual(out["points"][0]["fleet"], {"net": 5.0})


if __name__ == "__main__":
    unittest.main()
