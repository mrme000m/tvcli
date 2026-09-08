#!/usr/bin/env python3
"""scripts/repair_ledger.py — fixture-based dry-run + apply idempotency.

Fixtures: the 2026-09-06 WT audit's raw positions-history dumps, copied to
tests/fixtures/wt_audit/ (WT keeps deleted bots' history reachable by code,
so these are full-life ground truth — see
state/reports/audit-20260906-wt-history.md).
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import repair_ledger as rl  # noqa: E402
import reliability_grid as rg  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "wt_audit")
ARB_HL = rl.AUDIT_CODES[("ARB", "hyperliquid")]
ARB_BN = rl.AUDIT_CODES[("ARB", "binance")]
FART = rl.AUDIT_CODES[("FARTCOIN", "hyperliquid")]
XVG = rl.AUDIT_CODES[("XVG", "binance")]
# The wt_audit fixtures are raw WunderTrading account dumps and stay OUT of
# the public repo (the root .gitignore's *.json rule blocks them; the local
# re-include in agents/grid-autonomy/.gitignore only covers the fixtures
# TOP level). They exist only on the dev host, so a fresh clone — CI, the
# deployed container, any workbench — must SKIP this suite cleanly instead
# of erroring in seed_state's dumps[code] lookup (KeyError on the missing
# bot code).
FIXTURES_PRESENT = bool(
    os.path.isdir(FIX) and [n for n in os.listdir(FIX)
                            if n.startswith("hist_")
                            and n.endswith(".json")])


def decision(rid, symbol, venue, closed_at, realized, reason, regime=None):
    return {
        "id": rid,
        "at": closed_at,
        "action": {"kind": "DEPLOY-PAPER", "msg": f"slot {symbol}",
                   "slot": 1, "symbol": symbol, "venue": venue},
        "decision": "GO",
        "regime": regime or "chop_high_volatility",
        "slot": 1,
        "symbol": symbol,
        "venue": venue,
        "outcome": {"closed_at": closed_at, "fills": 0, "observed": {},
                    "realized_pnl": realized, "reason": reason},
    }


def closed_decisions():
    """The fleet's 6 closed trips + a pre-reset wipe + a deploy failure."""
    return [
        decision("d-reset", "ARB", "hyperliquid",
                 "2026-09-05T12:55:43+00:00", 0,
                 "reset-wt (paper bots deleted)"),
        decision("d-005", "ARB", "hyperliquid",
                 "2026-09-05T18:35:20+00:00", 0.4822,
                 "manual rotate (ctl /rotate)"),
        decision("d-007", "ARB", "binance",
                 "2026-09-05T19:19:50+00:00", 0.4896,
                 "optimizer swap (fast lane)"),
        decision("d-009", "FARTCOIN", "hyperliquid",
                 "2026-09-05T20:22:18+00:00", 0.0941,
                 "optimizer swap (fast lane)"),
        decision("d-010", "UNI", "hyperliquid",
                 "2026-09-05T22:00:24+00:00", 0.3546,
                 "optimizer swap (fast lane)"),
        decision("d-020", "ROBO", "binance",
                 "2026-09-05T22:34:17+00:00", 0.0,
                 "optimizer swap (fast lane)"),
        decision("d-024", "XVG", "binance",
                 "2026-09-05T23:30:21+00:00", 0.0,
                 "optimizer swap (fast lane)"),
        decision("d-fail", "ENA", "hyperliquid",
                 "2026-09-05T22:00:32+00:00", None, "deploy-failed"),
    ]


def seed_state(state_dir, decisions=None):
    """A state dir shaped like the live one (compact replica)."""
    os.makedirs(state_dir, exist_ok=True)
    decisions = decisions if decisions is not None else closed_decisions()
    with open(os.path.join(state_dir, "decisions.jsonl"), "w") as fh:
        for rec in decisions:
            fh.write(json.dumps(rec) + "\n")
    # active fleet: CHIP (has trips in the fixtures) + the journal's
    # rotation-delete of ROBO (slot 3) — read-only resolution inputs
    with open(os.path.join(state_dir, "state.json"), "w") as fh:
        json.dump({
            "active_bots": {
                "2": {"symbol": "CHIP", "venue": "hyperliquid",
                      "bot_code": "c629f5ba3a643a825a3059c7",
                      "archetype": "Neutral Grid"},
                "7": {"symbol": "GRAM", "venue": "hyperliquid",
                      "bot_code": "c629f5ba3a643a825e755d52",
                      "archetype": "Long Grid"},
                "5": {"symbol": "LTC", "venue": "hyperliquid",
                      "bot_code": "c629f5ba3a643a826333a96a",
                      "archetype": "Long Grid"},
                "1": {"symbol": "DOGE", "venue": "hyperliquid",
                      "bot_code": "c629f5ba3a643a820347777a",
                      "archetype": "Long Grid"},
                "3": {"symbol": "GIGGLE", "venue": "binance",
                      "bot_code": "c629f5ba3a643a8263fb3de1",
                      "archetype": "Neutral Grid"},
            },
            "journal": [{"kind": "rotation-delete", "slot": "3",
                         "msg": f"delete {rl.AUDIT_CODES[('ROBO', 'binance')]}",
                         "at": "2026-09-05T22:34:17+00:00"}],
        }, fh)
    # archive: backfill seeds under "unknown" + real rows under the Neutral
    # Grid key — the completed rows deliberately OVERLAP the dump trips so
    # the rebuild's dedup is exercised (must not double-count)
    dumps = rl.load_dumps(FIX)
    neutral_rows = []
    for code in (ARB_HL, ARB_BN, FART):
        neutral_rows.extend(rg.parse_trades(
            [it for it in dumps[code]["items"]
             if it.get("status") == "completed"]))
    archive = {
        "unknown": [
            {"pnl_usd": 1.9461, "close_ts": 1788507611.0, "entered_at": None,
             "strategy_id": "backfill-0"},
            {"pnl_usd": 0.0791, "close_ts": 1788611561.0,
             "entered_at": "2026-09-05T15:27:50+03:00",
             "strategy_id": "6a9c0ac6f891fd79a98926b3"},
        ],
        "chop_high_volatility": neutral_rows + [
            {"pnl_usd": 0.1092, "close_ts": 1788565010.0,
             "entered_at": None, "strategy_id": "backfill-0"},
        ],
    }
    with open(os.path.join(state_dir, "reliability_archive.json"), "w") as fh:
        json.dump(archive, fh)
    ledger = rg.archetype_stats(
        {rg.ledger_key(k): v for k, v in archive.items()})
    with open(os.path.join(state_dir, "reliability.json"), "w") as fh:
        json.dump(ledger, fh)


@unittest.skipUnless(FIXTURES_PRESENT,
                     "wt_audit hist_*.json fixtures not present in this clone")
class RepairLedgerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="grid-repair-test-")
        self.addCleanup(lambda: shutil.rmtree(self.dir, True))
        seed_state(self.dir)

    def run_main(self, *extra):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = rl.main(["--dumps", FIX, "--state-dir", self.dir,
                          *extra])
        return rc, buf.getvalue()

    def decisions(self):
        with open(os.path.join(self.dir, "decisions.jsonl")) as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def by_id(self, rid):
        return {r["id"]: r for r in self.decisions()}[rid]

    # ── ground-truth math ─────────────────────────────────────────────
    def test_bot_truth_matches_audit(self):
        dumps = rl.load_dumps(FIX)
        t = rl.bot_truth(dumps[FART]["items"])
        self.assertEqual((t["completed_n"], t["panic_n"]), (1, 4))
        self.assertAlmostEqual(t["completed_pnl"], 0.0941, places=4)
        self.assertAlmostEqual(t["panic_pnl"], -1.1169, places=4)
        self.assertAlmostEqual(t["total_pnl"], -1.0228, places=4)
        t = rl.bot_truth(dumps[ARB_HL]["items"])
        self.assertAlmostEqual(t["total_pnl"], 0.7207, places=4)
        t = rl.bot_truth(dumps[ARB_BN]["items"])
        self.assertAlmostEqual(t["total_pnl"], 0.6317, places=4)
        t = rl.bot_truth(dumps[XVG]["items"])
        self.assertAlmostEqual(t["total_pnl"], 0.0134, places=4)

    # ── dry run: proof, and writes nothing ────────────────────────────
    def test_dry_run_table_and_no_writes(self):
        before = {n: open(os.path.join(self.dir, n)).read()
                 for n in ("decisions.jsonl", "reliability_archive.json",
                           "reliability.json")}
        rc, out = self.run_main()
        self.assertEqual(rc, 0)
        self.assertIn("DRY RUN", out)
        self.assertIn("-1.0228", out)          # FARTCOIN truth
        self.assertIn("+0.7207", out)           # ARB-HL truth
        self.assertIn("+0.6317", out)          # ARB-BN truth
        self.assertIn("6 decision(s) would be patched", out)
        after = {n: open(os.path.join(self.dir, n)).read()
                 for n in ("decisions.jsonl", "reliability_archive.json",
                           "reliability.json")}
        self.assertEqual(before, after)

    def test_dry_run_fleet_truth_line(self):
        rc, out = self.run_main()
        self.assertIn("+1.6585", out)   # fleet WT ground truth
        self.assertIn("-0.7229", out)    # optimistic bias removed

    # ── apply ────────────────────────────────────────────────────────
    def test_apply_patches_decisions(self):
        rc, out = self.run_main("--apply")
        self.assertEqual(rc, 0)
        d = self.by_id("d-009")
        oc = d["outcome"]
        self.assertAlmostEqual(oc["realized_pnl"], -1.0228, places=4)
        self.assertAlmostEqual(oc["realized_pnl_pre_repair"], 0.0941,
                               places=4)
        self.assertAlmostEqual(oc["realized_pnl_completed"], 0.0941,
                               places=4)
        self.assertAlmostEqual(oc["realized_pnl_panic"], -1.1169, places=4)
        self.assertEqual((oc["trips_completed"], oc["trips_panic"]), (1, 4))
        self.assertEqual(d["repair"]["source"], "audit-20260906")
        self.assertIn("at", d["repair"])
        # UNI was already exact: value unchanged, split recorded
        oc = self.by_id("d-010")["outcome"]
        self.assertAlmostEqual(oc["realized_pnl"], 0.3546, places=4)
        self.assertEqual((oc["trips_completed"], oc["trips_panic"]), (4, 0))
        # XVG's panic trip recovered
        oc = self.by_id("d-024")["outcome"]
        self.assertAlmostEqual(oc["realized_pnl"], 0.0134, places=4)
        # pre-reset wipe and deploy-failure are NOT touched
        self.assertNotIn("repair", self.by_id("d-reset"))
        self.assertNotIn("repair", self.by_id("d-fail"))
        # untouched lines byte-identical
        with open(os.path.join(self.dir, "decisions.jsonl")) as fh:
            lines = [l for l in fh.readlines() if l.strip()]
        self.assertEqual(len(lines), 8)

    def test_apply_rebuilds_archive_and_ledger(self):
        self.run_main("--apply")
        with open(os.path.join(self.dir, "reliability_archive.json")) as fh:
            arch = json.load(fh)
        neutral = arch[rg.ledger_key("chop_high_volatility")]
        # 9 pre-existing rows (ARB-HL 4 + ARB-BN 3 + FART 1 completed +
        # 1 backfill seed) + the missing trips: ARB-HL 1, ARB-BN 1, FART 4
        # panic + UNI 4 + XVG 1 = 9 + 11 = 20
        self.assertEqual(len(neutral), 20)
        # no duplicate trips (dedup by strategy_id+close_ts)
        ids = [(t["strategy_id"], t["close_ts"]) for t in neutral]
        self.assertEqual(len(ids), len(set(ids)))
        # backfill rows persisted with the synthetic flag
        seeds = [t for t in neutral if t["strategy_id"].startswith("backfill-")]
        self.assertEqual(len(seeds), 1)
        self.assertTrue(all(t.get("synthetic") for t in seeds))
        with open(os.path.join(self.dir, "reliability.json")) as fh:
            ledger = json.load(fh)
        st = ledger[rg.ledger_key("chop_high_volatility")]
        # 19 real samples (12 completed + 7 panic), 3 seeded, real totals
        self.assertEqual(st["samples"], 19)
        self.assertEqual(st["synthetic_samples"], 1)
        self.assertAlmostEqual(st["gross_profit_usd"], 1.8145, places=4)
        self.assertAlmostEqual(st["gross_loss_usd"], 1.1169, places=4)
        unk = ledger["unknown"]
        self.assertEqual(unk["samples"], 1)  # one real row seeded in fixture
        self.assertEqual(unk["synthetic_samples"], 1)

    def test_apply_is_idempotent(self):
        self.run_main("--apply")
        snap = {n: open(os.path.join(self.dir, n)).read()
                for n in ("decisions.jsonl", "reliability_archive.json",
                          "reliability.json")}
        # second --apply: all repaired, no file content changes
        rc, out = self.run_main("--apply")
        self.assertEqual(rc, 0)
        self.assertIn("already up to date", out)
        snap2 = {n: open(os.path.join(self.dir, n)).read()
                 for n in ("decisions.jsonl", "reliability_archive.json",
                           "reliability.json")}
        self.assertEqual(snap, snap2)
        # a dry run after repair reports zero pending
        rc, out = self.run_main()
        self.assertIn("0 decision(s) would be patched (6 already repaired)",
                      out)

    def test_time_guard_blocks_wrong_era_bot(self):
        # a decision claiming a close BEFORE the ARB-HL trips exist must
        # not be patched with the later bot's history (the pre-reset ARB
        # shares symbol+venue with the fleet-era ARB)
        recs = closed_decisions()
        for r in recs:
            if r["id"] == "d-reset":
                r["outcome"]["reason"] = "manual rotate (ctl /rotate)"
                r["outcome"]["realized_pnl"] = 0.4822
        d2 = tempfile.mkdtemp(dir=self.dir)
        seed_state(d2, decisions=recs)
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = rl.main(["--dumps", FIX, "--state-dir", d2])
        self.assertEqual(rc, 0)
        self.assertIn("time guard", buf.getvalue())
        with open(os.path.join(d2, "decisions.jsonl")) as fh:
            recs2 = [json.loads(l) for l in fh if l.strip()]
        reset = [r for r in recs2 if r["id"] == "d-reset"][0]
        self.assertNotIn("repair", reset)
        self.assertAlmostEqual(reset["outcome"]["realized_pnl"], 0.4822,
                               places=4)

    def test_no_dumps_no_crash(self):
        empty = tempfile.mkdtemp(dir=self.dir)
        seed_state(empty)
        rc = rl.main(["--dumps", os.path.join(self.dir, "nope"),
                     "--state-dir", empty])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
