#!/usr/bin/env python3
"""Self-healing layer tests — browser watchdog, env re-import, observe
error semantics, observe-outage escalation, PB mirror upsert. Hermetic: no
network, no pgrep/ps, no real CDP browser."""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "execution"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "policy"))

import daemon  # noqa: E402
import observe  # noqa: E402

BOT = {"bot_code": "SOMEBOT", "venue": "hyperliquid", "symbol": "ETH"}


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── observe: fetch failure vs missing resource ───────────────────────────
class TestObserveErrorSemantics(unittest.TestCase):
    def test_fetch_failure_says_browser_down(self):
        with mock.patch.object(observe, "_api_json", return_value=None):
            out = observe.observe_all({"1": dict(BOT)})
        self.assertIn("unavailable", out["1"]["error"])
        self.assertIn("browser/session", out["1"]["error"])

    def test_healthy_list_without_bot_says_not_found(self):
        raw = {"_embedded": {"items": [
            {"resource": {"code": "OTHER", "status": "active"}}]}}
        with mock.patch.object(observe, "_api_json", return_value=raw):
            out = observe.observe_all({"1": dict(BOT)})
        self.assertEqual(out["1"]["error"],
                         "grid resource not found in status list")

    def test_healthy_list_with_bot_has_no_error(self):
        raw = {"_embedded": {"items": [
            {"resource": {"code": "SOMEBOT", "status": "active"}}]}}
        with mock.patch.object(observe, "_api_json", return_value=raw):
            out = observe.observe_all({"1": dict(BOT)})
        self.assertNotIn("error", out["1"])
        self.assertEqual(out["1"]["status"], "active")


# ── env self-heal ───────────────────────────────────────────────────────
class TestSelfHealEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = {k: os.environ.pop(k, None) for k in
                    ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_KEY",
                     "CLOUDFLARE_AI_TOKEN", "PB_TOKEN", "PB_ADMIN_EMAIL",
                     "NVIDIA_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY")}
        # hermetic: a machine-local state/llm.env sidecar must not leak a
        # real "llm" heal into these assertions
        patcher = mock.patch.object(daemon, "_load_llm_env", lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _importer(self, sets):
        def fn():
            for k, v in sets.items():
                os.environ[k] = v
        return fn

    def test_heals_cf_and_journals(self):
        state = {"journal": []}
        sets = {"CLOUDFLARE_ACCOUNT_ID": "a", "CLOUDFLARE_AI_TOKEN": "t"}
        with mock.patch.object(daemon, "_import_cf_env",
                               self._importer(sets)), \
                mock.patch.object(daemon, "_load_pb_env", lambda: None):
            healed = daemon.self_heal_env(state)
        self.assertEqual(healed, ["cf"])
        self.assertTrue(any(j.get("kind") == "env-heal"
                            for j in state["journal"]))

    def test_noop_when_env_complete(self):
        os.environ["CLOUDFLARE_ACCOUNT_ID"] = "a"
        os.environ["PB_TOKEN"] = "tok"
        state = {"journal": []}
        with mock.patch.object(daemon, "_import_cf_env",
                               side_effect=AssertionError("must not run")):
            self.assertEqual(daemon.self_heal_env(state), [])
        self.assertEqual(state["journal"], [])

    def test_pb_heal_via_pb_env(self):
        state = {"journal": []}
        sets = {"PB_TOKEN": "tok"}
        with mock.patch.object(daemon, "_import_cf_env", lambda: None), \
                mock.patch.object(daemon, "_load_pb_env",
                                  self._importer(sets)):
            healed = daemon.self_heal_env(state)
        self.assertEqual(healed, ["pb"])

    def test_missing_importers_degrade_to_noop(self):
        with mock.patch.object(daemon, "_import_cf_env", None), \
                mock.patch.object(daemon, "_load_pb_env", None):
            self.assertEqual(daemon.self_heal_env(None), [])


# ── CDP probe + command resolution ──────────────────────────────────────
class TestCdpProbe(unittest.TestCase):
    def test_alive(self):
        with mock.patch.object(daemon.urllib.request, "urlopen",
                               return_value=FakeResponse(200)):
            self.assertTrue(daemon.cdp_alive("http://127.0.0.1:9222"))

    def test_dead(self):
        with mock.patch.object(daemon.urllib.request, "urlopen",
                               side_effect=OSError("refused")):
            self.assertFalse(daemon.cdp_alive("http://127.0.0.1:9222"))

    def test_resolve_cmd_passes_absolute(self):
        self.assertEqual(
            daemon.resolve_cmd("/usr/bin/env -i"),
            ["/usr/bin/env", "-i"])

    def test_resolve_cmd_resolves_from_path(self):
        with mock.patch.object(daemon.shutil, "which",
                               return_value="/usr/local/bin/node"):
            self.assertEqual(
                daemon.resolve_cmd("node x.mjs"),
                ["/usr/local/bin/node", "x.mjs"])

    def test_resolve_cmd_unresolvable_passthrough(self):
        with mock.patch.object(daemon.shutil, "which", return_value=None), \
                mock.patch.object(daemon.os.path, "isfile",
                                  return_value=False):
            self.assertEqual(daemon.resolve_cmd("node x"),
                             ["node", "x"])

    def test_resolve_cmd_scans_install_roots(self):
        # launchd's minimal PATH hides homebrew/mise binaries: the watchdog
        # must still resolve `node` via the usual install roots
        with mock.patch.object(daemon.shutil, "which", return_value=None), \
                mock.patch.object(
                    daemon.os.path, "isfile",
                    side_effect=lambda p: p == "/opt/homebrew/bin/node"):
            self.assertEqual(daemon.resolve_cmd("node x.mjs"),
                             ["/opt/homebrew/bin/node", "x.mjs"])


# ── browser watchdog ────────────────────────────────────────────────────
def make_daemon(config=None, state=None):
    """Daemon via __new__: no I/O, watchdog attrs set like __init__ does."""
    d = daemon.Daemon.__new__(daemon.Daemon)
    d.config = json.loads(json.dumps(daemon.DEFAULT_CONFIG))
    d.config["watch"].update(config or {})
    d.port = 8799
    d.state = state or json.loads(json.dumps(daemon.DEFAULT_STATE))
    d.profiles = []
    d.reliability = {}
    import threading
    d._lock = threading.Lock()
    d._rescreen_flag = False
    d._reliability_flag = False
    d._browser_down_since = None
    d._last_browser_restart = 0.0
    return d


class TestBrowserWatchdog(unittest.TestCase):
    def _run(self, d, cdp_alive_side, runs):
        with mock.patch.object(daemon, "cdp_alive",
                               side_effect=cdp_alive_side), \
                mock.patch.object(daemon, "resolve_cmd", lambda c: c.split()), \
                mock.patch.object(daemon.subprocess, "run",
                                  side_effect=lambda cmd, **kw: runs.append(cmd)
                                  or type("P", (), {"stdout": "", "stderr": "",
                                                    "returncode": 0})()):
            return d.browser_watchdog()

    def test_healthy_cd_returns_true_no_spawn(self):
        d = make_daemon()
        runs = []
        self.assertTrue(self._run(d, lambda *a: True, runs))
        self.assertEqual(runs, [])

    def test_down_triggers_relaunch_and_journals(self):
        d = make_daemon({"browser_launch_cmd": "node launch.mjs",
                         "wt_restore_cmd": "node wt.mjs"})
        runs = []
        # down for the probe before restart, up after the commands ran
        results = iter([False, True])
        self.assertTrue(self._run(d, lambda *a: next(results), runs))
        self.assertEqual(runs, [["node", "launch.mjs"], ["node", "wt.mjs"]])
        kinds = [j["kind"] for j in d.state["journal"]]
        self.assertIn("browser-restart", kinds)
        self.assertIn("relaunch ok", next(j["msg"] for j in d.state["journal"]
                                          if j["kind"] == "browser-restart"))

    def test_cooldown_blocks_second_relaunch(self):
        d = make_daemon({"browser_launch_cmd": "node launch.mjs"})
        runs = []
        self.assertFalse(self._run(d, lambda *a: False, runs))  # restart failed
        self.assertEqual(len(runs), 1)
        # second call within the cooldown: no new spawn attempt
        self.assertFalse(self._run(d, lambda *a: False, runs))
        self.assertEqual(len(runs), 1)

    def test_no_configured_commands_journal_only(self):
        d = make_daemon()
        runs = []
        self.assertFalse(self._run(d, lambda *a: False, runs))
        self.assertEqual(runs, [])
        self.assertTrue(any(j["kind"] == "browser-restart"
                            for j in d.state["journal"]))


# ── observe-outage escalation in health_cycle ────────────────────────────
class TestObserveOutageEscalation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch("daemon.STATE_PATH",
                             os.path.join(self.tmp.name, "state.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _health(self, d, n):
        for _ in range(n):
            d.health_cycle(dry_run=True)

    def test_all_error_sweeps_escalate_after_30(self):
        d = make_daemon()
        d.config["watch"]["interval_s"] = 60
        d.state["active_bots"] = {
            "1": {"symbol": "ETH", "venue": "hyperliquid",
                  "bot_code": "B1", "stagnation_policy": {}}}
        err = {"1": {"error": "grid status list unavailable", "status": "unknown",
                     "price": None, "fills_24h": 0, "realized_ratio": 0.0,
                     "unrealized_pnl": None, "ladder_full": False,
                     "dd_vs_atr_band": 0.0}}
        with mock.patch.object(daemon, "cdp_alive", lambda *a: True), \
                mock.patch.object(daemon, "observe_all_safe",
                                  return_value=err):
            self._health(d, 30)
        self.assertEqual(d.state["observe_error_sweeps"], 0)  # fired + reset
        outs = [j for j in d.state["journal"] if j["kind"] == "observe-outage"]
        self.assertEqual(len(outs), 1)
        self.assertIn("unobservable", outs[0]["msg"])
        # 10 more blind sweeps: not yet re-fired (30 needed again)
        with mock.patch.object(daemon, "cdp_alive", lambda *a: True), \
                mock.patch.object(daemon, "observe_all_safe",
                                  return_value=err):
            self._health(d, 10)
        self.assertEqual(
            len([j for j in d.state["journal"] if j["kind"] == "observe-outage"]),
            1)

    def test_partial_errors_do_not_escalate(self):
        d = make_daemon()
        d.state["active_bots"] = {
            "1": {"symbol": "ETH", "venue": "hyperliquid",
                  "bot_code": "B1", "stagnation_policy": {}},
            "2": {"symbol": "SOL", "venue": "binance",
                  "bot_code": "B2", "stagnation_policy": {}}}
        obs = {"1": {"error": "x", "status": "unknown", "price": None,
                      "fills_24h": 0, "realized_ratio": 0.0},
               "2": {"status": "active", "price": 100.0, "fills_24h": 5,
                     "realized_ratio": 1.0}}
        with mock.patch.object(daemon, "cdp_alive", lambda *a: True), \
                mock.patch.object(daemon, "observe_all_safe",
                                  return_value=obs):
            self._health(d, 40)
        self.assertEqual(
            len([j for j in d.state["journal"] if j["kind"] == "observe-outage"]),
            0)
        self.assertEqual(d.state["observe_error_sweeps"], 0)


# ── gone-bot reconciliation in health_cycle ────────────────────────────
GONE_ERR = "grid resource not found in status list"
TRANSPORT_ERR = "grid status list unavailable (browser/session down)"


class TestGoneBotReconciliation(unittest.TestCase):
    """A tracked bot absent from a HEALTHY WT grid status list is a gone
    bot: warn once per missing episode, free the slot only after
    watch.gone_clear_min of CONTINUOUS absence; transport-class observe
    errors (browser/session down) never count toward removal."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch("daemon.STATE_PATH",
                             os.path.join(self.tmp.name, "state.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _daemon(self, watch=None):
        d = make_daemon(watch)
        d.state["active_bots"] = {
            "1": {"symbol": "CASHCAT", "venue": "hyperliquid",
                  "bot_code": "B1", "stagnation_policy": {}}}
        d.state["committed"] = {"1": 150.0}
        return d

    @staticmethod
    def _err(err):
        return {"1": {"error": err, "status": "unknown", "price": None,
                      "fills_24h": 0, "realized_ratio": 0.0,
                      "unrealized_pnl": None, "ladder_full": False,
                      "dd_vs_atr_band": 0.0}}

    def _health(self, d, obs, n=1):
        with mock.patch.object(daemon, "cdp_alive", lambda *a: True), \
                mock.patch.object(daemon, "observe_all_safe",
                                  return_value=obs):
            for _ in range(n):
                d.health_cycle(dry_run=True)

    def test_missing_bot_warns_once_then_stays_quiet(self):
        d = self._daemon()  # defaults: gone_warn_after=3, gone_clear_min=30
        self._health(d, self._err(GONE_ERR), n=8)
        warns = [j for j in d.state["journal"]
                 if j["kind"] == "health-warn" and GONE_ERR in j["msg"]]
        self.assertEqual(len(warns), 1)
        # default gone_clear_min (30 m) not elapsed — bot still tracked
        self.assertIn("1", d.state["active_bots"])
        self.assertTrue(d.state["active_bots"]["1"]["gone_warned"])

    def test_gone_bot_removed_and_journaled_after_clear_min(self):
        d = self._daemon({"gone_warn_after": 1, "gone_clear_min": 5})
        t = {"now": 1_000_000.0}
        with mock.patch.object(daemon.time, "time",
                               side_effect=lambda: t["now"]):
            self._health(d, self._err(GONE_ERR))  # episode starts, warns
            t["now"] += 5 * 60 + 1                 # 5 min continuous
            self._health(d, self._err(GONE_ERR))   # past gone_clear_min
        self.assertNotIn("1", d.state["active_bots"])   # slot freed
        self.assertNotIn("1", d.state["committed"])
        gones = [j for j in d.state["journal"] if j["kind"] == "bot-gone"]
        self.assertEqual(len(gones), 1)
        g = gones[0]
        self.assertEqual(g["slot"], "1")
        self.assertEqual(g["symbol"], "CASHCAT")
        self.assertEqual(g["venue"], "hyperliquid")
        self.assertGreaterEqual(g["minutes_missing"], 5.0)
        # exactly one bot-gone — no repeated spam on later ticks
        self._health(d, self._err(GONE_ERR), n=3)
        self.assertEqual(
            len([j for j in d.state["journal"] if j["kind"] == "bot-gone"]),
            1)

    def test_transport_error_never_removes_and_resets_episode(self):
        d = self._daemon({"gone_warn_after": 1, "gone_clear_min": 5})
        t = {"now": 2_000_000.0}
        with mock.patch.object(daemon.time, "time",
                               side_effect=lambda: t["now"]):
            self._health(d, self._err(GONE_ERR))  # seed a missing episode
            self.assertIsNotNone(
                d.state["active_bots"]["1"].get("gone_missing_since"))
            # browser/session down for far longer than gone_clear_min
            for _ in range(8):
                t["now"] += 10 * 60
                self._health(d, self._err(TRANSPORT_ERR))
        self.assertIn("1", d.state["active_bots"])  # fail closed: kept
        self.assertIn("1", d.state["committed"])
        bot = d.state["active_bots"]["1"]
        # the in-progress missing episode was reset, not accumulated
        for k in ("gone_missing_since", "gone_ticks", "gone_warned"):
            self.assertNotIn(k, bot)
        self.assertEqual(
            [j for j in d.state["journal"] if j["kind"] == "bot-gone"], [])

    def test_all_gone_fleet_is_not_an_observe_outage(self):
        # a fleet of gone bots (healthy list, bots deleted WT-side) must
        # NOT trigger the browser-outage escalation — that is transport
        # blindness only.
        d = self._daemon({"gone_warn_after": 1, "gone_clear_min": 0.2})
        t = {"now": 4_000_000.0}
        with mock.patch.object(daemon.time, "time",
                               side_effect=lambda: t["now"]):
            # long past gone_clear_min → fleet removed, never escalated
            for _ in range(3):
                t["now"] += 60          # clock advances between sweeps
                self._health(d, self._err(GONE_ERR))
        self.assertEqual(d.state.get("observe_error_sweeps", 0), 0)
        self.assertEqual(
            [j for j in d.state["journal"] if j["kind"] == "observe-outage"],
            [])
        self.assertNotIn("1", d.state["active_bots"])

    def test_reappearance_resets_episode_without_clearing(self):
        d = self._daemon({"gone_warn_after": 1, "gone_clear_min": 30})
        healthy = {"1": {"status": "active", "price": 100.0,
                         "fills_24h": 5, "realized_ratio": 1.0}}
        t = {"now": 3_000_000.0}
        with mock.patch.object(daemon.time, "time",
                               side_effect=lambda: t["now"]):
            self._health(d, self._err(GONE_ERR))   # episode 1: warns
            first_since = d.state["active_bots"]["1"]["gone_missing_since"]
            self._health(d, healthy)                # back → episode reset
            t["now"] += 120                         # well under 30 min
            self._health(d, self._err(GONE_ERR))    # fresh episode 2
        # slot NOT cleared (only 2 min into the fresh episode)
        self.assertIn("1", d.state["active_bots"])
        bot = d.state["active_bots"]["1"]
        self.assertEqual(bot["gone_ticks"], 1)
        self.assertTrue(bot["gone_warned"])          # re-warned once again
        self.assertGreater(bot["gone_missing_since"], first_since)
        # one warn per episode → exactly two across the two episodes
        warns = [j for j in d.state["journal"]
                 if j["kind"] == "health-warn" and GONE_ERR in j["msg"]]
        self.assertEqual(len(warns), 2)
        self.assertEqual(
            [j for j in d.state["journal"] if j["kind"] == "bot-gone"], [])


# ── PB mirror upsert ────────────────────────────────────────────────────
class TestPbMirrorUpsert(unittest.TestCase):
    def test_mirror_uses_upsert_and_handles_list_slots(self):
        calls = []

        class FakePB:
            def upsert(self, coll, field, data):
                calls.append((coll, data["slot"]))
                return {}

        state = {"active_bots": {"2": {"symbol": "ETH"}},
                 "slots": [{"slot": 1, "balance": 150.0},
                           {"slot": 2, "balance": 150.0}]}
        with mock.patch.object(daemon, "_pb", lambda: FakePB()):
            daemon._pb_mirror_state(state)
        # bots mirrored once per active bot, slots once per slot (list form)
        self.assertEqual(sorted(calls), [("bots", "2"), ("slots", "1"),
                                         ("slots", "2")])

    def test_pb_down_is_silent(self):
        state = {"active_bots": {"2": {"symbol": "ETH"}}, "slots": []}
        with mock.patch.object(daemon, "_pb", lambda: None):
            daemon._pb_mirror_state(state)  # must not raise


# ── pbclient.upsert semantics ───────────────────────────────────────────
class TestPbUpsert(unittest.TestCase):
    def _pb(self, rows):
        pb = daemon._PB.__new__(daemon._PB)
        pb.url = "http://127.0.0.1:8090"
        pb.token = "t"
        pb.timeout = 1.0
        pb.strict = False
        pb.disabled = False
        pb._admin_email = pb._admin_pass = None
        recs = {i: r for i, r in enumerate(rows)}

        def fake_list(coll, filter=None, sort=None, page=1, per_page=50):
            if filter and 'slot = "2"' in filter and recs:
                return [recs[0]]
            return []

        def fake_update(coll, rid, data):
            recs[int(rid)].update(data)
            return recs[int(rid)]

        def fake_create(coll, data):
            nid = max(recs, default=-1) + 1
            recs[nid] = dict(data)
            return recs[nid]

        pb.list = fake_list
        pb.update = fake_update
        pb.create = fake_create
        return pb, recs

    def test_existing_row_patched_not_duplicated(self):
        pb, recs = self._pb([{"id": "0", "slot": "2", "spec": "old"}])
        pb.upsert("bots", "slot", {"slot": "2", "spec": "new"})
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["spec"], "new")

    def test_missing_row_created(self):
        pb, recs = self._pb([])
        pb.upsert("bots", "slot", {"slot": "3", "spec": "x"})
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["slot"], "3")


# ── run_launchd: the CLOUDFLARE_ prefix bug (regression) ────────────────
class TestRunLaunchdEnvPrefix(unittest.TestCase):
    def test_import_cf_env_sets_prefixed_names(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                        "scripts"))
        import run_launchd  # noqa: E402
        old = {k: os.environ.pop(k, None) for k in
               ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_AI_TOKEN",
                "CLOUDFLARE_API_KEY", "ACCOUNT_ID", "AI_TOKEN")}
        self.addCleanup(lambda: [
            os.environ.pop(k, None) or (v and os.environ.__setitem__(k, v))
            for k, v in old.items()])

        def fake_run(cmd, **kw):
            return type("P", (), {
                "stdout": "/bin/dsh web CLOUDFLARE_ACCOUNT_ID=acct123 "
                          "CLOUDFLARE_AI_TOKEN=tok456"})()

        with mock.patch.object(run_launchd, "_dsh_pid",
                               lambda: "4242"), \
                mock.patch.object(run_launchd.subprocess, "run",
                                  side_effect=fake_run):
            run_launchd.import_cf_env()
        self.assertEqual(os.environ.get("CLOUDFLARE_ACCOUNT_ID"), "acct123")
        self.assertEqual(os.environ.get("CLOUDFLARE_AI_TOKEN"), "tok456")
        # the bug: bare suffixes leaked into the env
        self.assertNotIn("ACCOUNT_ID", os.environ)
        self.assertNotIn("AI_TOKEN", os.environ)


if __name__ == "__main__":
    unittest.main()
