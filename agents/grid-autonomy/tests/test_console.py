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
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

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

    def tearDown(self):
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

    def test_static_index_served(self):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/", timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("mission console", resp.read().decode())

    def test_path_traversal_refused(self):
        code, body = self.call("/api/../console/server.py")
        self.assertEqual(code, 404)

    def test_cross_origin_post_refused(self):
        code, body = self.call("/api/ctl/rescreen", "POST", {},
                               headers={"Origin": "https://evil.example"})
        self.assertEqual(code, 403)

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


if __name__ == "__main__":
    unittest.main()
