#!/usr/bin/env python3
"""Paper-profile bootstrap tests — hermetic, no network/browser/subprocess.

Covers the self-heal path that replaces the permanent deploy veto when an
allowlisted paper profile (e.g. demo-bn on BINANCE) is missing from the
WunderTrading account:

  - wt_library exchanges passthroughs (wtclient.ExchangesClient surface,
    dry_run defaults, live wrapping, never-raise ensure)
  - execution/profiles.ensure_paper_profiles (spec from the config venue
    map, legacy flat-list fallback, execute gating, exception envelope)
  - daemon boot bootstrap (live-paper executes + refreshes + journals;
    dry-run boots skip silently)
  - daemon health-cycle retry (cooldown respected, journal on attempts)

Everything is mocked at the execution.profiles / execution.wt_library /
daemon-boundary level — wtclient is never invoked for real.
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="grid-paper-profile-")
os.environ["GRID_STATE_DIR"] = _TMP
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "execution"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "policy"))

import daemon  # noqa: E402
from execution import profiles  # noqa: E402
from execution import wt_library  # noqa: E402
from tests.test_daemon_manage import ManageHarness, PROFILES  # noqa: E402

DEMO_HYPE = {"code": "profile-1", "name": "demo-hype", "balance": 300.0,
             "exchange": "HYPERLIQUID_SWAP", "paperTrading": True}
DEMO_BN = {"code": "profile-2", "name": "demo-bn", "balance": 200.0,
           "exchange": "BINANCE_FUTURES", "paperTrading": True}

CFG = {"autonomy": {"paper_profiles": {"hyperliquid": ["demo-hype"],
                                       "binance": ["demo-bn"]}}}

WTC_RESULT = {"ok": True,
              "venues": {"binance": {"demo-bn": {"state": "created"}}},
              "created": ["binance/demo-bn"], "errors": []}


# ── wt_library exchanges passthroughs ─────────────────────────────────


class TestWtLibraryExchanges(unittest.TestCase):
    def setUp(self):
        wt_library.reset_wun()

    def tearDown(self):
        wt_library.reset_wun()

    def _wun(self, exchanges):
        wun = mock.MagicMock()
        wun.exchanges = exchanges
        return wun

    def test_list_profiles_passthrough(self):
        ex = mock.MagicMock()
        ex.list_profiles.return_value = [{"name": "demo-hype",
                                          "paperTrading": True}]
        with mock.patch("execution.wt_library.get_wun",
                        return_value=self._wun(ex)):
            out = wt_library.exchanges_list_profiles()
        self.assertEqual(out, [{"name": "demo-hype", "paperTrading": True}])
        ex.list_profiles.assert_called_once_with()

    def test_account_limits_passthrough(self):
        ex = mock.MagicMock()
        ex.account_limits.return_value = {"gridBots": {"active": 3, "max": 5}}
        with mock.patch("execution.wt_library.get_wun",
                        return_value=self._wun(ex)):
            out = wt_library.exchanges_account_limits()
        self.assertEqual(out, {"gridBots": {"active": 3, "max": 5}})
        ex.account_limits.assert_called_once_with()

    def test_create_paper_profile_dry_run_default(self):
        res = wt_library.create_paper_profile("demo-bn", "BINANCE")
        self.assertTrue(res["ok"])
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["transport"],
                         "wtclient.ExchangesClient.create_paper_profile")
        self.assertEqual(res["name"], "demo-bn")
        self.assertEqual(res["exchange_family"], "BINANCE")

    def test_create_paper_profile_live_wraps_result(self):
        ex = mock.MagicMock()
        ex.create_paper_profile.return_value = {"created": True}
        with mock.patch("execution.wt_library.get_wun",
                        return_value=self._wun(ex)):
            res = wt_library.create_paper_profile(
                "demo-bn", "BINANCE", dry_run=False)
        self.assertTrue(res["ok"])
        self.assertFalse(res.get("dry_run", False))
        self.assertEqual(res["result"], {"created": True})
        ex.create_paper_profile.assert_called_once_with("demo-bn", "BINANCE")

    def test_create_paper_profile_live_wun_error(self):
        from wtclient.errors import WunApiError
        ex = mock.MagicMock()
        ex.create_paper_profile.side_effect = WunApiError("boom",
                                                          status_code=500)
        with mock.patch("execution.wt_library.get_wun",
                        return_value=self._wun(ex)):
            res = wt_library.create_paper_profile(
                "demo-bn", "BINANCE", dry_run=False)
        self.assertFalse(res["ok"])
        self.assertIn("boom", res["error"])

    def test_ensure_paper_profiles_dry_run_default(self):
        res = wt_library.ensure_paper_profiles(
            {"hyperliquid": ["demo-hype"], "binance": ["demo-bn"]})
        self.assertTrue(res["ok"])
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["transport"],
                         "wtclient.ExchangesClient.ensure_paper_profiles")
        self.assertEqual(res["spec"], {"hyperliquid": ["demo-hype"],
                                       "binance": ["demo-bn"]})

    def test_ensure_paper_profiles_live_wraps_result(self):
        ex = mock.MagicMock()
        ex.ensure_paper_profiles.return_value = dict(WTC_RESULT)
        with mock.patch("execution.wt_library.get_wun",
                        return_value=self._wun(ex)):
            res = wt_library.ensure_paper_profiles(
                CFG["autonomy"]["paper_profiles"], dry_run=False)
        self.assertTrue(res["ok"])
        self.assertEqual(res["result"], WTC_RESULT)
        ex.ensure_paper_profiles.assert_called_once_with(
            {"hyperliquid": ["demo-hype"], "binance": ["demo-bn"]})

    def test_ensure_paper_profiles_never_raises(self):
        # unexpected non-Wun exception still returns a report dict
        ex = mock.MagicMock()
        ex.ensure_paper_profiles.side_effect = RuntimeError("session gone")
        with mock.patch("execution.wt_library.get_wun",
                        return_value=self._wun(ex)):
            res = wt_library.ensure_paper_profiles(
                {"binance": ["demo-bn"]}, dry_run=False)
        self.assertFalse(res["ok"])
        self.assertIn("session gone", res["error"])

    def test_ensure_paper_profiles_live_wun_error(self):
        from wtclient.errors import WunApiError
        ex = mock.MagicMock()
        ex.ensure_paper_profiles.side_effect = WunApiError("denied",
                                                           status_code=403)
        with mock.patch("execution.wt_library.get_wun",
                        return_value=self._wun(ex)):
            res = wt_library.ensure_paper_profiles(
                {"binance": ["demo-bn"]}, dry_run=False)
        self.assertFalse(res["ok"])
        self.assertIn("denied", res["error"])


# ── execution/profiles.ensure_paper_profiles ──────────────────────────


class TestProfilesEnsure(unittest.TestCase):
    def test_spec_built_from_config_venue_map(self):
        calls = []

        def fake_ensure(spec, dry_run=True):
            calls.append((spec, dry_run))
            return {"ok": True, "dry_run": dry_run,
                    "transport": "wtclient.ExchangesClient.ensure_paper_profiles"}

        with mock.patch("execution.wt_library.ensure_paper_profiles",
                        side_effect=fake_ensure):
            report = profiles.ensure_paper_profiles(CFG, execute=True)
        self.assertEqual(
            calls, [({"hyperliquid": ["demo-hype"], "binance": ["demo-bn"]},
                     False)])
        self.assertTrue(report["ok"])
        self.assertTrue(report["executed"])
        self.assertEqual(report["spec"],
                         {"hyperliquid": ["demo-hype"], "binance": ["demo-bn"]})
        self.assertIsNone(report["error"])

    def test_execute_false_never_goes_live(self):
        calls = []

        def fake_ensure(spec, dry_run=True):
            calls.append(dry_run)
            return {"ok": True, "dry_run": dry_run}

        with mock.patch("execution.wt_library.ensure_paper_profiles",
                        side_effect=fake_ensure):
            report = profiles.ensure_paper_profiles(CFG, execute=False)
        self.assertEqual(calls, [True])  # dry_run=True — planned, no mutation
        self.assertFalse(report["executed"])
        self.assertTrue(report["ok"])  # planned reports are ok=True

    def test_legacy_flat_list_maps_to_hyperliquid(self):
        calls = []

        def fake_ensure(spec, dry_run=True):
            calls.append(spec)
            return {"ok": True}

        flat = {"autonomy": {"paper_profiles": ["demo-hype"]}}
        with mock.patch("execution.wt_library.ensure_paper_profiles",
                        side_effect=fake_ensure):
            profiles.ensure_paper_profiles(flat, execute=False)
        self.assertEqual(calls, [{"hyperliquid": ["demo-hype"]}])

    def test_unexpected_exception_ok_false_envelope(self):
        with mock.patch("execution.wt_library.ensure_paper_profiles",
                        side_effect=RuntimeError("wtclient exploded")):
            report = profiles.ensure_paper_profiles(CFG, execute=True)
        self.assertFalse(report["ok"])
        self.assertIn("wtclient exploded", report["error"])
        self.assertEqual(report["spec"],
                         {"hyperliquid": ["demo-hype"], "binance": ["demo-bn"]})

    def test_wrapped_failure_surfaced(self):
        with mock.patch("execution.wt_library.ensure_paper_profiles",
                        return_value={"ok": False,
                                      "error": "list_profiles failed"}):
            report = profiles.ensure_paper_profiles(CFG, execute=True)
        self.assertFalse(report["ok"])
        self.assertIn("list_profiles failed", report["error"])

    def test_paper_profile_spec_empty_shapes(self):
        self.assertEqual(profiles.paper_profile_spec({}), {})
        self.assertEqual(
            profiles.paper_profile_spec(
                {"autonomy": {"paper_profiles": None}}), {})
        self.assertEqual(
            profiles.paper_profile_spec(
                {"autonomy": {"paper_profiles": []}}), {})

    def test_legacy_wrappers_still_work(self):
        body = profiles.paper_profile_body("demo-bn", "BINANCE")
        self.assertTrue(body["paperTrading"])
        self.assertEqual(body["name"], "demo-bn")
        self.assertEqual(body["exchangeFamily"], "BINANCE")
        with self.assertRaises(ValueError):
            profiles.paper_profile_body("   ")
        # create wrapper: dry-run default -> planned dict, no wtclient call
        with mock.patch("execution.wt_library.create_paper_profile") as cp:
            res = profiles.create_paper_profile("demo-bn", "BINANCE")
            cp.assert_called_once_with("demo-bn", "BINANCE", dry_run=True)
        self.assertEqual(res, cp.return_value)
        # list wrapper: fail-soft []
        with mock.patch("execution.wt_library.exchanges_list_profiles",
                        side_effect=RuntimeError("down")):
            self.assertEqual(profiles.list_profiles(), [])


# ── daemon missing-detection + boot bootstrap ─────────────────────────


class TestDaemonMissingDetection(unittest.TestCase):
    def test_missing_detection(self):
        self.assertEqual(daemon._missing_paper_profiles(CFG, [DEMO_HYPE,
                                                              DEMO_BN]), {})
        self.assertEqual(daemon._missing_paper_profiles(CFG, [DEMO_HYPE]),
                         {"binance": ["demo-bn"]})
        # non-paper / wrong-family same-name profile counts as missing
        non_paper = dict(DEMO_BN, paperTrading=False)
        self.assertEqual(daemon._missing_paper_profiles(CFG, [DEMO_HYPE,
                                                               non_paper]),
                         {"binance": ["demo-bn"]})
        # empty snapshot -> everything missing (self-heals a wiped account)
        self.assertEqual(daemon._missing_paper_profiles(CFG, []),
                         {"hyperliquid": ["demo-hype"],
                          "binance": ["demo-bn"]})

    def test_missing_detection_fail_soft(self):
        class Boom:
            def get(self, key):
                raise RuntimeError("not a dict")

        self.assertEqual(daemon._missing_paper_profiles(
            CFG, [Boom()]), {"hyperliquid": ["demo-hype"],
                             "binance": ["demo-bn"]})


class TestDaemonBootBootstrap(ManageHarness):
    def _journal_kinds(self, d):
        return [e.get("kind") for e in d.state["journal"]]

    def test_live_paper_boot_ensures_and_refreshes(self):
        profiles_seen = []
        refresh = {"hyperliquid": [DEMO_HYPE]}

        def fake_grid_profiles_safe():
            # first call (init snapshot) and the post-ensure refresh: the
            # ensure "created" demo-bn, so the snapshot now carries it
            profiles_seen.append(dict(refresh))
            if len(profiles_seen) == 1:
                return [dict(DEMO_HYPE)]
            return [dict(DEMO_HYPE), dict(DEMO_BN)]

        with mock.patch("daemon.grid_profiles_safe",
                        side_effect=fake_grid_profiles_safe), \
                mock.patch("daemon._profiles_ensure",
                           return_value={"ok": True, "executed": True,
                                         "spec": {"hyperliquid": ["demo-hype"],
                                                  "binance": ["demo-bn"]},
                                         "result": WTC_RESULT,
                                         "error": None}) as ensure_mock:
            d = daemon.Daemon(live_paper=True)

        # ensure executed with the full config (execute=True, live-paper)
        ensure_mock.assert_called_once()
        args, kwargs = ensure_mock.call_args
        self.assertEqual(kwargs.get("execute"), True)
        self.assertEqual(args[0]["autonomy"]["paper_profiles"],
                         {"hyperliquid": ["demo-hype"],
                          "binance": ["demo-bn"]})
        # profiles refreshed from the post-create snapshot
        self.assertEqual([p["name"] for p in d.profiles],
                         ["demo-hype", "demo-bn"])
        self.assertEqual(d.state["profiles"], d.profiles)
        # journal event carries the report
        events = [e for e in d.state["journal"]
                  if e.get("kind") == "profile-bootstrap"]
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertTrue(ev["executed"])
        self.assertEqual(ev["missing"], {"binance": ["demo-bn"]})
        self.assertEqual(ev["report"]["result"], WTC_RESULT)
        # cooldown timestamp recorded (health cycle backs off)
        self.assertGreater(d._profile_bootstrap_ts, 0.0)

    def test_dry_run_boot_skips_silently(self):
        with mock.patch("daemon.grid_profiles_safe",
                        return_value=[dict(DEMO_HYPE)]), \
                mock.patch("daemon._profiles_ensure") as ensure_mock:
            d = daemon.Daemon()  # live_paper defaults to False
        ensure_mock.assert_not_called()
        self.assertNotIn("profile-bootstrap", self._journal_kinds(d))
        self.assertEqual([p["name"] for p in d.profiles], ["demo-hype"])

    def test_live_paper_boot_noop_when_nothing_missing(self):
        with mock.patch("daemon.grid_profiles_safe",
                        return_value=[dict(DEMO_HYPE), dict(DEMO_BN)]), \
                mock.patch("daemon._profiles_ensure") as ensure_mock:
            d = daemon.Daemon(live_paper=True)
        ensure_mock.assert_not_called()
        self.assertNotIn("profile-bootstrap", self._journal_kinds(d))


# ── daemon health-cycle retry ─────────────────────────────────────────


class TestDaemonHealthCycleRetry(ManageHarness):
    def _ensure_and_journal(self, d):
        return ([e for e in d.state["journal"]
                 if e.get("kind") == "profile-bootstrap"],
                getattr(d, "_profile_bootstrap_ts", 0.0))

    def _stale_missing_snapshot(self, d):
        """Simulate a live-paper daemon whose WT account lacks demo-bn."""
        d.profiles = [dict(DEMO_HYPE)]
        d.state["profiles"] = d.profiles
        d._profile_bootstrap_ts = 0.0  # cooldown elapsed

    def test_retry_executes_and_journals(self):
        d = self.make_daemon()
        self._stale_missing_snapshot(d)
        with mock.patch("daemon.grid_profiles_safe",
                        return_value=[dict(DEMO_HYPE)]), \
                mock.patch("daemon._profiles_ensure",
                           return_value={"ok": True, "executed": True,
                                         "spec": {"binance": ["demo-bn"]},
                                         "result": WTC_RESULT,
                                         "error": None}) as ensure_mock:
            d._retry_paper_profile_bootstrap()
        ensure_mock.assert_called_once()
        self.assertEqual(ensure_mock.call_args.kwargs.get("execute"), True)
        events, ts = self._ensure_and_journal(d)
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["executed"])
        self.assertGreater(ts, 0.0)

    def test_retry_cooldown_respected(self):
        d = self.make_daemon()
        self._stale_missing_snapshot(d)
        d._profile_bootstrap_ts = time.time()  # attempted moments ago
        with mock.patch("daemon.grid_profiles_safe",
                        return_value=[dict(DEMO_HYPE)]), \
                mock.patch("daemon._profiles_ensure") as ensure_mock:
            d._retry_paper_profile_bootstrap()
        ensure_mock.assert_not_called()
        self.assertEqual(self._ensure_and_journal(d)[0], [])

    def test_second_attempt_within_cooldown_skipped(self):
        d = self.make_daemon()
        self._stale_missing_snapshot(d)
        with mock.patch("daemon.grid_profiles_safe",
                        return_value=[dict(DEMO_HYPE)]), \
                mock.patch("daemon._profiles_ensure",
                           return_value={"ok": True, "executed": True,
                                         "spec": {}, "result": WTC_RESULT,
                                         "error": None}) as ensure_mock:
            d._retry_paper_profile_bootstrap()  # first attempt
            d._retry_paper_profile_bootstrap()  # within 1800 s — skipped
        self.assertEqual(ensure_mock.call_count, 1)
        events, _ = self._ensure_and_journal(d)
        self.assertEqual(len(events), 1)

    def test_retry_noop_when_nothing_missing(self):
        d = self.make_daemon()
        d._profile_bootstrap_ts = 0.0
        # the harness snapshots carry BOTH profiles
        with mock.patch("daemon._profiles_ensure") as ensure_mock:
            d._retry_paper_profile_bootstrap()
        ensure_mock.assert_not_called()
        self.assertEqual(self._ensure_and_journal(d)[0], [])

    def test_rescreen_cycle_runs_retry_live_paper_only(self):
        # rescreen (health-adjacent cycle) triggers the retry in live-paper
        d = self.make_daemon()
        self._stale_missing_snapshot(d)
        with mock.patch("daemon.run_merge",
                        return_value={"results": []}), \
                mock.patch("daemon.grid_profiles_safe",
                           return_value=[dict(DEMO_HYPE)]), \
                mock.patch("daemon._profiles_ensure",
                           return_value={"ok": True, "executed": True,
                                         "spec": {"binance": ["demo-bn"]},
                                         "result": WTC_RESULT,
                                         "error": None}) as ensure_mock:
            d.rescreen_cycle(dry_run=False, max_new=0)
        ensure_mock.assert_called_once()

    def test_rescreen_cycle_dry_run_never_executes(self):
        d = self.make_daemon()
        self._stale_missing_snapshot(d)
        with mock.patch("daemon.run_merge",
                        return_value={"results": []}), \
                mock.patch("daemon.grid_profiles_safe",
                           return_value=[dict(DEMO_HYPE)]), \
                mock.patch("daemon._profiles_ensure") as ensure_mock:
            d.rescreen_cycle(dry_run=True, max_new=0)
        ensure_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
