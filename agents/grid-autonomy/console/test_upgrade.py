#!/usr/bin/env python3
"""Offline tests for the console upgrade: fail-soft /api/status, /api/pnl
(PB → state.json fallback), recommendation apply-gate verdicts, and the
reliability ledger's real/synthetic split + staleness.

Run:  python3 -m unittest discover -s console -p "test_upgrade.py"
"""
from __future__ import annotations

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
sys.path.insert(0, os.path.dirname(HERE))          # grid-autonomy root
sys.path.insert(0, os.path.dirname(HERE))           # config_lite
sys.path.insert(0, HERE)                            # yaml_edit

import console.server as server                     # noqa: E402

CONFIG_TMPL = """\
portfolio:
  total_usd: 600.0
  slots_default: 4
optimizer:
  enabled: true
position_optimizer:
  enabled: true
  apply: false
  max_apply_per_day: 4
"""


class UpgradeTestCase(unittest.TestCase):
    """Isolated server globals: temp state dir, dead ctl/PB ports."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="console-upgrade-")
        self._saved = {
            "STATE_DIR": server.STATE_DIR, "CONFIG_PATH": server.CONFIG_PATH,
            "PB_URL": server.PB_URL, "PB_ENV_PATH": server.PB_ENV_PATH,
        }
        server.STATE_DIR = os.path.join(self.tmp, "state")
        server.CONFIG_PATH = os.path.join(self.tmp, "config.yaml")
        server.PB_URL = "http://127.0.0.1:59999"          # dead port
        server.PB_ENV_PATH = os.path.join(self.tmp, "pb.env")
        os.makedirs(server.STATE_DIR, exist_ok=True)
        with open(server.CONFIG_PATH, "w") as f:
            f.write(CONFIG_TMPL)
        server._ctl_port = lambda: 59999
        server._launchd_managed = lambda: False
        server._pid = lambda: None
        server._CTL_CACHE.clear()

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(server, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_state(self, state):
        with open(os.path.join(server.STATE_DIR, "state.json"), "w") as f:
            json.dump(state, f)

    # ── shaping ──────────────────────────────────────────────────────

    def test_pnl_payload_state_fallback_and_tolerant_shape(self):
        self.write_state({"journal": [
            {"kind": "screen", "msg": "x", "at": "2026-09-06T00:00:00+00:00"},
            {"kind": "pnl-snapshot", "at": "2026-09-06T01:00:00+00:00",
             "fleet": {"net": 1.5, "realized": 1.0, "unrealized": 0.5,
                       "committed_usd": 250.0, "idle_usd": 350.0,
                       "fills_24h": 14}},
            {"kind": "pnl-snapshot", "at": "2026-09-06T01:05:00+00:00",
             "extra": {"fleet": {"net": 2.0}}},   # payload inside `extra`
            {"kind": "pnl-snapshot", "at": "2026-09-06T01:10:00+00:00"},  # no payload
        ]})
        out = server.pnl_payload()
        self.assertEqual(out["source"], "state")   # PB is a dead port here
        self.assertEqual(len(out["points"]), 3)
        self.assertEqual(out["points"][0]["at"], "2026-09-06T01:10:00+00:00")  # newest first
        self.assertIsNone(out["points"][0]["fleet"])
        self.assertEqual(out["points"][1]["fleet"]["net"], 2.0)
        self.assertEqual(out["points"][2]["fleet"]["idle_usd"], 350.0)

    def test_pnl_payload_empty_is_valid(self):
        self.write_state({"journal": [{"kind": "screen", "msg": "x",
                                       "at": "2026-09-06T00:00:00+00:00"}]})
        out = server.pnl_payload()
        self.assertEqual(out, {"points": [], "source": "state", "total": 0})

    def test_recommendations_blocked_by_verdicts(self):
        recs = [
            {"at": "2026-09-06T01:00:00+00:00", "applied": True,
             "applied_at": "2026-09-06T01:01:00+00:00"},
            {"at": "2026-09-06T02:00:00+00:00"},   # no applied key at all
            {"at": "2026-09-06T03:00:00+00:00"},
        ]
        real_http = server._http_json
        server._http_json = (lambda url, timeout=2.0, method="GET", body=None:
                             (True, {"items": [dict(r) for r in recs]}))
        try:
            payload = server.recommendations_payload(100)
        finally:
            server._http_json = real_http
        self.assertEqual(payload["apply"], False)          # advisory template
        self.assertEqual(payload["persisted_today"], 3)
        self.assertEqual(payload["recommendations"][0]["blocked_by"], "applied")
        self.assertEqual(payload["recommendations"][0]["applied_at"],
                         "2026-09-06T01:01:00+00:00")
        for r in payload["recommendations"][1:]:
            self.assertEqual(r["blocked_by"], "apply disabled")
            self.assertFalse(r["applied"])

    def test_reliability_real_samples_and_age(self):
        with open(os.path.join(server.STATE_DIR, "reliability.json"), "w") as f:
            json.dump({"chop": {"samples": 11, "synthetic_samples": 8,
                                "profit_factor": 1.4},
                       "clean": {"samples": 3}}, f)
        out = server.reliability_payload()
        self.assertEqual(out["archetypes"]["chop"]["real_samples"], 3)
        self.assertEqual(out["archetypes"]["clean"]["real_samples"], 3)
        self.assertIsNotNone(out["ledger_age_h"])
        self.assertFalse(out["stale"])

    # ── HTTP (fail-soft against dead ctl/PB) ─────────────────────────

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

    def call(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"{}")

    def test_status_and_observe_fail_soft_200(self):
        for path in ("/api/status", "/api/observe"):
            code, body = self.call(path)
            self.assertEqual(code, 200)          # never a 500/502
            self.assertIn("error", body)

    def test_pnl_endpoint_valid_json_empty(self):
        code, body = self.call("/api/pnl")
        self.assertEqual(code, 200)
        self.assertEqual(body["points"], [])


if __name__ == "__main__":
    unittest.main()
