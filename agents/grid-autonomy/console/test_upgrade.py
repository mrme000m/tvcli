#!/usr/bin/env python3
"""Offline tests for the console upgrade: fail-soft /api/status, /api/pnl
(PB → state.json fallback), recommendation apply-gate verdicts, the
reliability ledger's real/synthetic split + staleness, and the fast
slot-optimizer proxies (/api/optimizer fail-soft, /api/ctl/optimize 502).

Run:  python3 -m unittest discover -s console -p "test_upgrade.py"
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
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
            "_LLM_HEALTH_LAST": server._LLM_HEALTH_LAST,
            "_PB_CLIENT": server._PB_CLIENT,
        }
        server._LLM_HEALTH_LAST = None
        server._LLM_HEALTH_REFRESHING = False
        # a pbclient cached by an earlier test must not leak in here
        # (module-level cache → order dependence between tests)
        server._PB_CLIENT = None
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
        # `at` stamps must be TODAY (utc): persisted_today counts records
        # whose `at` date prefix matches the current UTC date
        today = server.utcnow()[:10]
        recs = [
            {"at": f"{today}T01:00:00+00:00", "applied": True,
             "applied_at": f"{today}T01:01:00+00:00"},
            {"at": f"{today}T02:00:00+00:00"},   # no applied key at all
            {"at": f"{today}T03:00:00+00:00"},
        ]
        # recommendations_payload's PB ladder never reaches _http_json:
        # _pb_client() builds a live pbclient.PB (its constructor does not
        # connect) and _pb_get() is its own raw-urllib path. Patch BOTH of
        # those seams directly: no PB client, and the raw GET answering the
        # canned records — an offline stand-in for a live PocketBase.
        real_client, real_get = server._pb_client, server._pb_get
        server._pb_client = lambda: None
        server._pb_get = (lambda url, timeout=2.5, token=None:
                          (True, {"items": [dict(r) for r in recs]}))
        try:
            payload = server.recommendations_payload(100)
        finally:
            server._pb_client, server._pb_get = real_client, real_get
        self.assertEqual(payload["apply"], False)          # advisory template
        self.assertEqual(payload["persisted_today"], 3)
        self.assertEqual(payload["recommendations"][0]["blocked_by"], "applied")
        self.assertEqual(payload["recommendations"][0]["applied_at"],
                         f"{today}T01:01:00+00:00")
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

    def test_optimizer_fail_soft_200_null_when_ctl_dead(self):
        # ctl port is a dead 59999 here — the panel route must degrade
        # with a 200 + {"optimizer": null, "error": ...}, never a 502
        code, body = self.call("/api/optimizer")
        self.assertEqual(code, 200)
        self.assertIsNone(body.get("optimizer"))
        self.assertIn("error", body)
        self.assertIn("detail", body)

    def test_position_sweeps_fail_soft_200(self):
        """The sweep-history endpoint reads state.json directly (no ctl
        round-trip) — it must answer 200 even with the daemon down."""
        self.write_state({"journal": [
            {"kind": "position-optimizer-sweep", "msg": "2 bots analyzed",
             "at": "2026-09-07T01:00:00+00:00"},
            {"kind": "heartbeat", "msg": "score 88/100",
             "at": "2026-09-07T01:05:00+00:00"},   # not a sweep: excluded
        ]})
        code, body = self.call("/api/position-sweeps")
        self.assertEqual(code, 200)
        self.assertEqual(len(body["sweeps"]), 1)
        self.assertEqual(body["sweeps"][0]["kind"],
                         "position-optimizer-sweep")

    def test_status_proxy_keys_added_only_when_daemon_up(self):
        """ctl is a dead port here → /api/status stays the documented
        fail-soft {"error": ...} + 200 shape (backward compat: no 502)."""
        code, body = self.call("/api/status")
        self.assertEqual(code, 200)
        self.assertIn("error", body)

    def test_ctl_optimize_502_when_ctl_dead(self):
        # POST /api/ctl/optimize mirrors /api/ctl/rescreen: ctl down → 502
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/ctl/optimize",
            data=b"{}", method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                code, body = resp.status, json.loads(resp.read() or b"{}")
            self.fail("expected HTTPError 502")
        except urllib.error.HTTPError as exc:
            code = exc.code
            body = json.loads(exc.read() or b"{}")
        self.assertEqual(code, 502)
        self.assertEqual(body.get("error"), "ctl unreachable")
        self.assertIn("detail", body)

    # ── /api/llm/health: async (non-blocking) refresh ─────────────────

    def _patch_probe(self, fake):
        """Patch the module-level blocking probe the refresh thread runs
        (so no real provider.py subprocess is ever launched offline)."""
        real = server._llm_health_probe
        server._llm_health_probe = fake
        self.addCleanup(setattr, server, "_llm_health_probe", real)

    def wait_llm_refresh(self, timeout=5.0):
        """Block (bounded) until the background refresh published its
        cache entry — tests may wait, the request handler never does."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            hit = server._CTL_CACHE.get("llm_health")
            if (hit and hit[0] > time.time()
                    and not server._LLM_HEALTH_REFRESHING):
                return hit[2]
            time.sleep(0.02)
        return None

    def test_llm_health_cold_cache_pending_fast(self):
        """Cold cache: the endpoint answers 200 in well under 2s with a
        pending marker and empty results; the refresh runs in a
        background thread."""
        started = threading.Event()

        def fake_probe():
            started.set()
            return (server._llm_health_assemble(
                [{"provider": "mistral", "ok": True, "latency_ms": 12,
                  "error": None}], ["cf", "mistral"]), None)
        self._patch_probe(fake_probe)
        t0 = time.perf_counter()
        code, body = self.call("/api/llm/health")
        elapsed = time.perf_counter() - t0
        self.assertEqual(code, 200)
        self.assertLess(elapsed, 2.0)            # never blocks on the ping
        self.assertTrue(body.get("pending"))
        self.assertEqual(body.get("results"), [])  # nothing known yet
        for key in ("at", "chain", "results", "roles", "role_keys",
                    "arbiter_provider", "note"):   # backward-compat schema
            self.assertIn(key, body)
        self.assertTrue(started.wait(2.0))        # refresh thread launched
        self.assertIsNotNone(self.wait_llm_refresh())

    def test_llm_health_followup_serves_refreshed_results(self):
        """After the background refresh lands, requests serve the canned
        results from the 60s cache (pending false, no stale marker)."""
        canned = [{"provider": "mistral", "ok": True, "latency_ms": 12,
                   "error": None}]

        def fake_probe():
            return (server._llm_health_assemble(canned, ["cf", "mistral"]),
                    None)
        self._patch_probe(fake_probe)
        code, first = self.call("/api/llm/health")
        self.assertEqual(code, 200)
        self.assertTrue(first["pending"])          # cold → immediate pending
        self.assertIsNotNone(self.wait_llm_refresh())
        code, second = self.call("/api/llm/health")
        self.assertEqual(code, 200)
        self.assertFalse(second["pending"])       # fresh from cache
        self.assertNotIn("stale", second)
        self.assertEqual(second["results"], canned)
        code, third = self.call("/api/llm/health")  # still inside the TTL
        self.assertEqual(third["results"], canned)

    def test_llm_health_concurrent_cold_requests_single_refresh(self):
        """5 concurrent cold requests spawn exactly ONE refresh thread and
        every request answers promptly with the pending shape."""
        calls = []
        release = threading.Event()

        def fake_probe():
            calls.append(1)
            release.wait(10.0)                     # hold the refresh open
            return (server._llm_health_assemble(
                [{"provider": "cf", "ok": True, "latency_ms": 3,
                  "error": None}], ["cf"]), None)
        self._patch_probe(fake_probe)
        out, errs = [], []

        def hit():
            try:
                out.append(self.call("/api/llm/health"))
            except Exception as exc:              # pragma: no cover
                errs.append(exc)
        threads = [threading.Thread(target=hit) for _ in range(5)]
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join(5.0)
        elapsed = time.perf_counter() - t0
        self.assertEqual(errs, [])
        self.assertLess(elapsed, 2.0)              # all 5 answered promptly
        self.assertEqual(len(calls), 1)            # exactly one refresh
        for code, body in out:
            self.assertEqual(code, 200)
            self.assertTrue(body.get("pending"))
        release.set()
        self.assertIsNotNone(self.wait_llm_refresh())
        code, body = self.call("/api/llm/health")
        self.assertEqual(code, 200)
        self.assertFalse(body["pending"])
        self.assertEqual(body["results"][0]["provider"], "cf")

    def test_llm_health_subprocess_failure_presence_only_no_5xx(self):
        """A failing ping subprocess (rc != 0, no last-known-good) falls
        back to presence-only results and never yields a 5xx."""
        from types import SimpleNamespace
        real_run = server.subprocess.run
        server.subprocess.run = (
            lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="",
                                               stderr="boom"))
        self.addCleanup(setattr, server.subprocess, "run", real_run)
        code, first = self.call("/api/llm/health")
        self.assertEqual(code, 200)
        self.assertTrue(first["pending"])
        self.assertIsNotNone(self.wait_llm_refresh())
        code, body = self.call("/api/llm/health")
        self.assertEqual(code, 200)               # never a 500/502
        self.assertFalse(body["pending"])
        names = {r["provider"] for r in body["results"]}
        self.assertEqual(names, {"cf", "nvidia", "openrouter", "mistral"})
        self.assertIn(body.get("error"), ("ping subprocess rc=1",))

    def test_llm_health_timeout_serves_stale_last_known(self):
        """A timed-out ping with a previous last-known-good payload serves
        the stale data (stale: true + error note), not the failure."""
        good = server._llm_health_assemble(
            [{"provider": "mistral", "ok": True, "latency_ms": 441,
              "error": None}], ["mistral"])
        server._LLM_HEALTH_LAST = good

        def boom(cmd, **kw):
            raise server.subprocess.TimeoutExpired(cmd, 180)
        real_run = server.subprocess.run
        server.subprocess.run = boom
        self.addCleanup(setattr, server.subprocess, "run", real_run)
        code, first = self.call("/api/llm/health")
        self.assertEqual(code, 200)
        self.assertTrue(first["pending"])          # cold → served from LAST
        self.assertEqual(first["results"], good["results"])
        self.assertIsNotNone(self.wait_llm_refresh())
        code, body = self.call("/api/llm/health")
        self.assertEqual(code, 200)
        self.assertFalse(body["pending"])
        self.assertTrue(body.get("stale"))         # stale, last-known data
        self.assertEqual(body["results"], good["results"])
        self.assertEqual(body["at"], good["at"])
        self.assertEqual(body.get("error"), "ping timeout (180s)")


if __name__ == "__main__":
    unittest.main()
