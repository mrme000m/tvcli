#!/usr/bin/env python3
"""Daemon heartbeat cycle — 8 fail-soft loop-health checks, the 0–100
score, the safe improving nudges (screen/optimizer staleness), the
state.heartbeat block, and the ctl /status surfacing (heartbeat +
data_sources tails). A heartbeat failure must never crash the manage
loop — every path here is fail-soft by contract.
"""
import json
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "policy"))

import daemon  # noqa: E402

try:
    from test_daemon_manage import ManageHarness  # noqa: E401
except ImportError:  # pytest rootdir=agents/grid-autonomy
    from tests.test_daemon_manage import ManageHarness  # noqa: E401


def _fresh_journal_event(kind, msg="x"):
    return {"kind": kind, "msg": msg,
            "at": daemon.utcnow().replace("+00:00", "+0000")}


class HeartbeatHarness(ManageHarness):
    """ManageHarness + all 8 checks stubbed deterministic (no network,
    no PB — daemon._pb is patched to None by the base harness)."""

    def setUp(self):
        super().setUp()
        self._hb_patches = [
            ("_hb_check_tvcli", lambda self: (True, "ok")),
            ("_hb_check_pocketbase", lambda self: (False, "pb unset")),
            ("_hb_check_wt_observe", lambda self: (True, "clean")),
            ("_hb_check_screen_fresh", lambda self, stale=2400: (True, "fresh")),
            ("_hb_check_optimizer_fresh", lambda self: (True, "fresh")),
            ("_hb_check_po_fresh", lambda self: (True, "fresh")),
            ("_hb_check_journal_errors", lambda self, rate=0.3: (True, "0/0")),
            ("_hb_check_pnl_feed", lambda self: (True, "fresh")),
        ]
        for name, fn in self._hb_patches:
            patcher = mock.patch.object(daemon.Daemon, name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_daemon_all_ok(self):
        """daemon whose only failing check is pocketbase (unset in tests)
        → 7/8 → score 88 (amber zone)."""
        return self.make_daemon()


class TestHeartbeatCycle(HeartbeatHarness):
    def test_score_and_state_block(self):
        d = self.make_daemon()
        block = d.heartbeat_cycle(dry_run=True)
        self.assertEqual(block["score"], 88)          # 7/8
        self.assertEqual(len(block["checks"]), 8)
        self.assertFalse(block["checks"]["pocketbase"]["ok"])
        self.assertTrue(block["checks"]["tvcli_health"]["ok"])
        self.assertEqual(block["nudges"], [])
        self.assertEqual(d.state["heartbeat"]["score"], 88)
        self.assertIn("at", d.state["heartbeat"])
        ev = [e for e in d.state["journal"]
              if e.get("kind") == "heartbeat"][-1]
        self.assertIn("heartbeat score 88/100 · 7/8 checks", ev["msg"])
        self.assertIn("pocketbase", ev["msg"])          # failed check listed

    def test_all_ok_scores_100(self):
        d = self.make_daemon()
        with mock.patch.object(daemon.Daemon, "_hb_check_pocketbase",
                               lambda self: (True, "up")):
            block = d.heartbeat_cycle(dry_run=True)
        self.assertEqual(block["score"], 100)

    def test_disabled_returns_none(self):
        d = self.make_daemon()
        d.config["heartbeat"]["enabled"] = False
        self.assertIsNone(d.heartbeat_cycle(dry_run=True))
        self.assertNotIn("heartbeat", d.state)

    def test_broken_check_is_fail_soft_not_crash(self):
        d = self.make_daemon()
        with mock.patch.object(daemon.Daemon, "_hb_check_screen_fresh",
                               side_effect=RuntimeError("boom")):
            block = d.heartbeat_cycle(dry_run=True)   # must not raise
        self.assertFalse(block["checks"]["screen_fresh"]["ok"])
        self.assertIn("boom", block["checks"]["screen_fresh"]["detail"])

    def test_whole_cycle_failure_journals_and_survives(self):
        d = self.make_daemon()
        # even the config read exploding must not propagate
        with mock.patch.object(daemon.Daemon, "_heartbeat_cfg",
                               side_effect=RuntimeError("cfg gone")):
            self.assertIsNone(d.heartbeat_cycle(dry_run=True))
        kinds = [e.get("kind") for e in d.state["journal"]]
        self.assertIn("heartbeat-error", kinds)

    def test_config_overrides_defaults(self):
        d = self.make_daemon()
        d.config["heartbeat"] = {"interval_s": 60, "screen_stale_s": 10,
                                 "error_rate_warn": 0.5}
        self.assertEqual(d._heartbeat_interval_s(), 60.0)
        cfg = d._heartbeat_cfg()
        self.assertEqual(cfg["screen_stale_s"], 10)
        # code defaults when the section is absent entirely
        d.config.pop("heartbeat")
        self.assertEqual(d._heartbeat_interval_s(), 900.0)
        self.assertEqual(d._heartbeat_cfg()["error_rate_warn"], 0.3)


class TestHeartbeatNudges(HeartbeatHarness):
    def test_stale_screen_queues_rescreen(self):
        d = self.make_daemon()
        with mock.patch.object(daemon.Daemon, "_hb_check_screen_fresh",
                               lambda self: (False, "age 99999s")):
            block = d.heartbeat_cycle(dry_run=False)
        self.assertIn("rescreen queued", json.dumps(block["nudges"]))
        self.assertTrue(d.consume_rescreen())
        nud = [e for e in d.state["journal"]
               if e.get("kind") == "heartbeat-nudge"]
        self.assertEqual(len(nud), 1)
        self.assertIn("screen cache stale", nud[0]["msg"])

    def test_stale_optimizer_sets_flag(self):
        d = self.make_daemon()
        with mock.patch.object(daemon.Daemon, "_hb_check_optimizer_fresh",
                               lambda self: (False, "no cycle")):
            block = d.heartbeat_cycle(dry_run=False)
        self.assertIn("optimize queued", json.dumps(block["nudges"]))
        self.assertTrue(d.consume_optimize())

    def test_dry_run_never_nudges(self):
        d = self.make_daemon()
        with mock.patch.object(daemon.Daemon, "_hb_check_screen_fresh",
                               lambda self: (False, "stale")), \
             mock.patch.object(daemon.Daemon, "_hb_check_optimizer_fresh",
                               lambda self: (False, "stale")):
            block = d.heartbeat_cycle(dry_run=True)
        self.assertEqual(block["nudges"], [])
        self.assertFalse(d.consume_rescreen())
        self.assertFalse(d.consume_optimize())

    def test_healthy_cycle_never_nudges(self):
        d = self.make_daemon()
        with mock.patch.object(daemon.Daemon, "_hb_check_pocketbase",
                               lambda self: (True, "up")):
            block = d.heartbeat_cycle(dry_run=False)
        self.assertEqual(block["nudges"], [])


class TestHeartbeatChecks(ManageHarness):
    """The raw checks against seeded state (no stubbing here)."""

    def test_tvcli_health_unreachable(self):
        ok, detail = daemon._tvcli_health("http://127.0.0.1:59999")
        self.assertFalse(ok)
        self.assertIn("unreachable", detail)

    def test_wt_observe_error_detection(self):
        d = self.make_daemon()
        d.state["last_observe"] = {"1": {"status": "error",
                                         "error": "session down"}}
        ok, _ = d._hb_check_wt_observe()
        self.assertFalse(ok)
        d.state["last_observe"] = {"1": {"status": "active"}, "2": {}}
        ok, detail = d._hb_check_wt_observe()
        self.assertTrue(ok)
        self.assertIn("2 bot(s)", detail)
        d.state["last_observe"] = {}
        self.assertFalse(d._hb_check_wt_observe()[0])

    def test_screen_freshness_bounds(self):
        import time as _t
        d = self.make_daemon()
        self.assertFalse(d._hb_check_screen_fresh(2400)[0])   # no cache
        d.state["screen_cache"] = {"at": _t.time() - 100}
        self.assertTrue(d._hb_check_screen_fresh(2400)[0])
        self.assertFalse(d._hb_check_screen_fresh(10)[0])

    def test_po_freshness(self):
        d = self.make_daemon()
        d.state["active_bots"]["1"] = {
            "position_optimizer": {"last_analyzed_at": time.time() - 600}}
        d.state["active_bots"]["2"] = {
            "position_optimizer": {"last_analyzed_at": time.time() - 99999}}
        d.state["active_bots"]["3"] = {"position_optimizer": {}}
        ok, detail = d._hb_check_po_fresh()
        self.assertFalse(ok)
        self.assertIn("2", detail)
        self.assertIn("3", detail)

    def test_journal_error_rate(self):
        d = self.make_daemon()
        now = time.time()
        from datetime import datetime, timezone
        iso = lambda s: datetime.fromtimestamp(
            now - s, tz=timezone.utc).isoformat(timespec="seconds")
        d.state["journal"] = [
            {"kind": "rescreen-error", "msg": "e1", "at": iso(60)},
            {"kind": "screen", "msg": "ok", "at": iso(120)},
            {"kind": "health-error", "msg": "e2", "at": iso(180)},
            {"kind": "cycle", "msg": "ok", "at": iso(240)},
        ]
        # 2/4 = 50% ≥ 30% → fail
        self.assertFalse(d._hb_check_journal_errors(0.3)[0])
        # below the configured warn rate → pass
        self.assertTrue(d._hb_check_journal_errors(0.6)[0])
        # entries older than the trailing hour never count: 3 more
        # errors OUTSIDE the window leave the in-hour rate at 2/4 = 50%
        # (5/7 = 71% would fail a 0.51 bound if they counted)
        for i in range(3):
            d.state["journal"].append({"kind": "adopt-error", "msg": "old",
                                      "at": iso(7200 + i)})
        self.assertTrue(d._hb_check_journal_errors(0.51)[0])

    def test_pnl_feed(self):
        d = self.make_daemon()
        # no snapshot in the ring → fail
        self.assertFalse(d._hb_check_pnl_feed()[0])
        d.state["journal"] = [{"kind": "pnl-snapshot", "msg": "x",
                               "at": daemon.utcnow()}]
        ok, detail = d._hb_check_pnl_feed()
        self.assertTrue(ok)
        self.assertIn("bound 600s", detail)
        # disabled feed (interval 0) → skipped, not failed
        d.config["watch"]["pnl_snapshot_interval_s"] = 0
        ok, detail = d._hb_check_pnl_feed()
        self.assertTrue(ok)
        self.assertIn("disabled", detail)

    def test_optimizer_fresh_skipped_when_disabled(self):
        d = self.make_daemon()
        d.optimizer = None
        ok, detail = d._hb_check_optimizer_fresh()
        self.assertTrue(ok)
        d.optimizer = object()
        d.config["optimizer"]["enabled"] = False
        with mock.patch.object(daemon.Daemon, "optimizer_interval_s",
                               lambda self: 0):
            ok, detail = d._hb_check_optimizer_fresh()
        self.assertTrue(ok)
        self.assertIn("disabled", detail)

    def test_optimizer_fresh_parses_iso_last_at(self):
        """state['optimizer']['last_at'] is an ISO string (optimizer.py
        stores report['at']) — float() on it always raises, which made
        the check permanently fail on live state (regression: az00
        2026-09-07, every heartbeat nudged a healthy optimizer)."""
        import datetime as _dt
        d = self.make_daemon()
        d.optimizer = object()
        d.state.setdefault("optimizer", {})
        with mock.patch.object(daemon.Daemon, "optimizer_interval_s",
                               lambda self: 180):
            # ISO string 30s old → fresh
            d.state["optimizer"]["last_at"] = (
                _dt.datetime.now(_dt.timezone.utc)
                - _dt.timedelta(seconds=30)).isoformat()
            ok, detail = d._hb_check_optimizer_fresh()
            self.assertTrue(ok)
            # ISO string 10 min old → stale (bound 3×180s = 540s)
            d.state["optimizer"]["last_at"] = (
                _dt.datetime.now(_dt.timezone.utc)
                - _dt.timedelta(minutes=10)).isoformat()
            ok, detail = d._hb_check_optimizer_fresh()
            self.assertFalse(ok)
            # raw epoch float still supported
            d.state["optimizer"]["last_at"] = time.time() - 30
            ok, detail = d._hb_check_optimizer_fresh()
            self.assertTrue(ok)
            # garbage → no cycle, never a raise
            d.state["optimizer"]["last_at"] = "not-a-time"
            ok, detail = d._hb_check_optimizer_fresh()
            self.assertFalse(ok)


class TestHeartbeatStatusSurfacing(HeartbeatHarness):
    """ctl /status: heartbeat block + the fail-soft data_sources tails
    (the parallel worker's market_regime.fetch_events_tail /
    merge.last_hunt_stats — absent here, must degrade to empty)."""

    def test_status_payload_heartbeat_and_data_sources(self):
        import ctl_http
        d = self.make_daemon()
        d.heartbeat_cycle(dry_run=True)
        payload = ctl_http.status_payload(d)
        self.assertEqual(payload["heartbeat"]["score"], 88)
        self.assertIn("checks", payload["heartbeat"])
        # fail-soft shapes: keys always present, market_regime absent
        # here → [] (or the worker's tail if it exists), hunt_stats is
        # whatever merge.last_hunt_stats reports (zero shape in-process
        # when the subprocess never set it)
        ctl_http._DS_CACHE["payload"] = None        # drop the ttl cache
        ds = payload["data_sources"]
        self.assertEqual(sorted(ds), ["fetch_events", "hunt_stats"])
        self.assertIsInstance(ds["fetch_events"], list)
        self.assertIsInstance(ds["hunt_stats"], dict)
        # backward compat: everything pre-existing is still there
        for key in ("slots", "active_bots", "committed", "live_allow",
                    "profiles", "capacity", "account_limits",
                    "capabilities", "env", "last_cycle", "journal_tail",
                    "pnl", "demo_cap"):
            self.assertIn(key, payload)

    def test_data_sources_picks_up_the_contract_fail_soft(self):
        import ctl_http
        calls = {"n": 0}

        def fake_tail(n=50):
            calls["n"] += 1
            return [{"ts": 1.0, "venue": "binance", "symbol": "DOGE",
                     "interval": "1h", "hop": "tvcli", "rows": 300,
                     "ms": 42}]

        class _MR:
            fetch_events_tail = staticmethod(fake_tail)

        ctl_http._DS_CACHE["payload"] = None
        with mock.patch.dict(sys.modules, {"market_regime": _MR}):
            out = ctl_http._data_sources_payload()
        self.assertEqual(out["fetch_events"][0]["hop"], "tvcli")
        # a raising source degrades to the empty shape, never raises
        class _MRBoom:
            @staticmethod
            def fetch_events_tail(n=50):
                raise RuntimeError("down")
        ctl_http._DS_CACHE["payload"] = None
        with mock.patch.dict(sys.modules, {"market_regime": _MRBoom}):
            out = ctl_http._data_sources_payload()
        self.assertEqual(out["fetch_events"], [])
        ctl_http._DS_CACHE["payload"] = None    # leave clean for others

    def test_screen_journal_unchanged_when_hunt_stats_absent(self):
        """The parallel worker's merge.last_hunt_stats is absent here —
        surfacing data_sources must not add/error anything in the screen
        journal (pure read path)."""
        import ctl_http
        d = self.make_daemon()
        before = list(d.state["journal"])
        ctl_http._DS_CACHE["payload"] = None
        ctl_http.status_payload(d)
        self.assertEqual(d.state["journal"], before)

    def test_data_sources_hunt_stats_from_journal_primary(self):
        """The screen runs merge.py in a SUBPROCESS, so the daemon process's
        merge.last_hunt_stats() only ever sees the in-process zero shape.
        The newest kind=="screen" journal entry's hunt_stats must win."""
        import ctl_http
        st = {"journal": [
            {"kind": "screen", "at": "2026-09-07T01:00:00+00:00",
             "msg": "old screen",
             "hunt_stats": {"at": 1.0,
                            "skills": {"squeeze": {"hunted": 9, "ok": 7}},
                            "candidates_boosted": 4, "errors": []}},
            {"kind": "pnl-snapshot", "at": "2026-09-07T01:05:00+00:00",
             "msg": "x"},
            {"kind": "screen", "at": "2026-09-07T02:00:00+00:00",
             "msg": "newest screen",
             "hunt_stats": {"at": 2.0,
                            "skills": {"squeeze": {"hunted": 10, "ok": 10},
                                       "choppiness": {"hunted": 10, "ok": 9}},
                            "candidates_boosted": 6, "errors": ["e"]}},
        ]}
        ctl_http._DS_CACHE["payload"] = None
        out = ctl_http._data_sources_payload(st)
        self.assertEqual(out["hunt_stats"]["skills"]["squeeze"]["ok"], 10)
        self.assertEqual(out["hunt_stats"]["candidates_boosted"], 6)
        # no journaled hunt_stats (old daemon / absent) → module fallback
        # (the in-process zero shape: no skills, nothing boosted) — never
        # a raise
        ctl_http._DS_CACHE["payload"] = None
        out = ctl_http._data_sources_payload({"journal": [
            {"kind": "screen", "at": "2026-09-07T01:00:00+00:00",
             "msg": "no stats here"}]})
        self.assertEqual(out["hunt_stats"].get("skills"), {})
        self.assertEqual(out["hunt_stats"].get("candidates_boosted"), 0)


if __name__ == "__main__":
    unittest.main()
