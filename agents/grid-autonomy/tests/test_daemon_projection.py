#!/usr/bin/env python3
"""Projected /24h fleet income (pnl_snapshot extension) + the position
optimizer's tvcli hunt_fn wiring (Daemon._po_hunt_structure).

The projection is an observability measure only — never a gate. These
tests pin the fee semantics (single-side crossings halved to round
trips, round-trip fee netted against the grid step), the missing-field
fail-soft path, and the hunt_fn compact-structure contract.
"""
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


def bot(exp=10.0, step_pct=0.5, amt=20.0, venue="hyperliquid",
        via_upsert=False):
    """One active bot with a full stagnation-policy model (defaults chosen
    so the projection math is hand-checkable)."""
    b = {"symbol": "DOGE", "venue": venue, "bot_code": "B1",
         "observed": {"status": "active", "realized_pnl": 0.0,
                      "unrealized_pnl": 0.0, "fills_24h": 0},
         "stagnation_policy": {"expected_fills_per_24h": exp}}
    if via_upsert:
        b["upsert"] = {"amountPerTrade": amt, "gridPercentStep": step_pct / 100.0}
    else:
        b["channel"] = {"low": 0.9, "mid": 1.0, "high": 1.1,
                        "step_pct": step_pct, "grids": 10}
        b["upsert"] = {"amountPerTrade": amt}
    return b


class TestProjected24h(ManageHarness):
    def test_projection_math_hyperliquid(self):
        # exp 10 crossings/24h → 5 round trips; step 0.5% vs HL rt fee
        # 0.10% → 0.4% net × $20 = $0.08/trip × 5 = $0.40
        d = self.make_daemon()
        d.state["active_bots"]["1"] = bot()
        snap = d.pnl_snapshot()
        self.assertEqual(snap["bots"]["1"]["projected_24h_usd"], 0.40)
        self.assertEqual(snap["fleet"]["projected_24h_usd"], 0.40)

    def test_projection_math_binance_fee(self):
        # binance round-trip fee 0.20% → 0.3% net × $20 × 5 trips = $0.30
        d = self.make_daemon()
        d.state["active_bots"]["1"] = bot(venue="binance")
        self.assertEqual(d.pnl_snapshot()["fleet"]["projected_24h_usd"], 0.30)

    def test_step_below_fee_floors_at_zero(self):
        # a grid step thinner than the round-trip fee projects ZERO,
        # never negative income
        d = self.make_daemon()
        d.state["active_bots"]["1"] = bot(step_pct=0.05)   # < 0.10 rt fee
        self.assertEqual(d.pnl_snapshot()["fleet"]["projected_24h_usd"], 0.0)

    def test_unknown_venue_uses_fallback_fee(self):
        # 0.15 fallback → 0.35% net × $20 × 5 = $0.35
        d = self.make_daemon()
        d.state["active_bots"]["1"] = bot(venue="kraken")
        self.assertEqual(d.pnl_snapshot()["fleet"]["projected_24h_usd"], 0.35)

    def test_step_falls_back_to_upsert_grid_percent(self):
        # adopted/re-analyzed bots can lose channel.step_pct — the
        # upsert's fraction (0.005 → 0.5%) is the fallback
        d = self.make_daemon()
        d.state["active_bots"]["1"] = bot(via_upsert=True)
        snap = d.pnl_snapshot()
        self.assertEqual(snap["bots"]["1"]["projected_24h_usd"], 0.40)

    def test_fleet_sums_bots_and_realized_net_alias(self):
        d = self.make_daemon()
        d.state["active_bots"]["1"] = bot(exp=10, step_pct=0.5, amt=20.0)
        d.state["active_bots"]["2"] = bot(exp=20, step_pct=1.0, amt=10.0)
        d.state["active_bots"]["1"]["observed"]["realized_pnl"] = 1.5
        d.state["active_bots"]["2"]["observed"]["unrealized_pnl"] = -0.5
        snap = d.pnl_snapshot()
        # 0.40 + (20/2 × 10 × 0.9% net HL fee) = 0.40 + 0.90
        self.assertEqual(snap["fleet"]["projected_24h_usd"], 1.30)
        f = snap["fleet"]
        self.assertEqual(f["realized_net"], f["net"])
        self.assertAlmostEqual(f["net"], 1.0, places=4)

    def test_missing_fields_fail_soft(self):
        d = self.make_daemon()
        d.state["active_bots"]["1"] = {"symbol": "X", "venue": "hyperliquid"}
        d.state["active_bots"]["2"] = {"symbol": "Y", "venue": "hyperliquid",
                                       "stagnation_policy": {"junk": 1}}
        d.state["active_bots"]["3"] = bot()
        d.state["active_bots"]["3"]["stagnation_policy"] = \
            {"expected_fills_per_24h": "n/a"}
        d.state["active_bots"]["4"] = bot()
        d.state["active_bots"]["4"]["upsert"] = {}     # amountPerTrade gone
        snap = d.pnl_snapshot()      # must not raise
        self.assertEqual(snap["fleet"]["projected_24h_usd"], 0.0)
        for k in "1234":
            self.assertEqual(snap["bots"][k]["projected_24h_usd"], 0.0)

    def test_fee_table_direct(self):
        self.assertEqual(daemon._round_trip_fee_pct("hyperliquid"), 0.10)
        self.assertEqual(daemon._round_trip_fee_pct("binance"), 0.20)
        self.assertEqual(daemon._round_trip_fee_pct(None), 0.15)
        self.assertEqual(daemon._round_trip_fee_pct("kraken"), 0.15)

    def test_fee_table_import_failure_falls_back(self):
        # guardrails import blowing up must degrade to the 0.15 fallback
        import builtins
        real_import = builtins.__import__

        def boom(name, *a, **k):
            if name == "execution.guardrails":
                raise ImportError("gone")
            return real_import(name, *a, **k)

        with mock.patch("builtins.__import__", side_effect=boom):
            self.assertEqual(daemon._round_trip_fee_pct("hyperliquid"), 0.15)

    def test_returns_metrics_annualize_and_denominators(self):
        # proj $0.40/24h on $100 committed / $500 fund → $146/yr,
        # 0.40%/24h, 146%/yr on committed, 29.2%/yr on the full fund
        d = self.make_daemon()
        d.state["active_bots"]["1"] = bot()
        d.state["committed"]["1"] = 100.0
        f = d.pnl_snapshot()["fleet"]
        self.assertEqual(f["projected_24h_usd"], 0.40)
        self.assertEqual(f["projected_annual_usd"], 146.0)
        self.assertEqual(f["projected_24h_return_pct"], 0.40)
        self.assertEqual(f["projected_annual_return_pct"], 146.0)
        self.assertEqual(f["projected_annual_return_total_pct"], 29.2)
        self.assertEqual(f["projected_double_days"], 281)  # ln2/ln(2.46)*365
        self.assertEqual(d.pnl_snapshot()["bots"]["1"]
                         ["projected_annual_usd"], 146.0)
        self.assertEqual(d.pnl_snapshot()["bots"]["1"]
                         ["projected_annual_return_pct"], 146.0)
        self.assertEqual(d.pnl_snapshot()["bots"]["1"]
                         ["projected_double_days"], 281)

    def test_returns_metrics_zero_denominator_fail_soft(self):
        # no committed capital → % returns are None (never a raise, never
        # a division by zero); annual USD and the fund-denominated % stay
        d = self.make_daemon()
        d.state["active_bots"]["1"] = bot()
        snap = d.pnl_snapshot()
        self.assertEqual(snap["fleet"]["projected_annual_usd"], 146.0)
        self.assertIsNone(snap["fleet"]["projected_24h_return_pct"])
        self.assertIsNone(snap["fleet"]["projected_annual_return_pct"])
        self.assertIsNone(snap["fleet"]["projected_double_days"])
        self.assertEqual(snap["fleet"]
                         ["projected_annual_return_total_pct"], 29.2)
        self.assertIsNone(snap["bots"]["1"]
                          ["projected_annual_return_pct"])
        self.assertIsNone(snap["bots"]["1"]["projected_double_days"])
        # zero fund → even the fund-denominated % degrades to None
        d.config["portfolio"]["total_usd"] = 0
        self.assertIsNone(d.pnl_snapshot()["fleet"]
                          ["projected_annual_return_total_pct"])

    def test_journal_msg_carries_projection(self):
        d = self.make_daemon()
        d.state["active_bots"]["1"] = bot()
        d._journal_pnl_snapshot()
        ev = [e for e in d.state["journal"]
              if e.get("kind") == "pnl-snapshot"][-1]
        self.assertIn("proj/24h $0.40", ev["msg"])
        self.assertEqual(ev["fleet"]["projected_24h_usd"], 0.40)
        self.assertEqual(ev["bots"]["1"]["projected_24h_usd"], 0.40)


class TestPoHuntStructure(ManageHarness):
    """hunt_fn wiring: the compact tvcli structure attached to every
    re-analyzed bot's position-optimizer recommendation."""

    HUNTS = {
        "squeeze": {"BINANCE:DOGEUSDT": {"result": {"status": "ok",
            "structure": {"squeezeOn": True, "squeezeBars": 7,
                          "momentumDir": "bullish"}}}},
        "choppiness": {"BINANCE:DOGEUSDT": {"result": {"status": "ok",
            "structure": {"chop": 64.2, "regime": "choppy"}}}},
    }

    def test_engine_wired_with_hunt_fn(self):
        d = self.make_daemon()
        self.assertEqual(d.position_optimizer.hunt_fn.__name__,
                         "_po_hunt_structure")
        self.assertIs(d.position_optimizer.hunt_fn.__self__, d)

    def test_structure_shape_and_calls(self):
        d = self.make_daemon()
        seen = []

        def fake_hunt(skill, tv_symbols, timeframe="1H", bars=180):
            seen.append((skill, tuple(tv_symbols), timeframe, bars))
            return dict(self.HUNTS[skill])

        with mock.patch("merge.tv_hunt", side_effect=fake_hunt):
            out = d._po_hunt_structure(
                {"symbol": "DOGE", "venue": "hyperliquid"})
        self.assertIsInstance(out, dict)
        self.assertTrue(out["at"] <= time.time())
        self.assertEqual(out["squeeze"], {"squeezeOn": True,
                                          "squeezeBars": 7,
                                          "momentumDir": "bullish"})
        self.assertEqual(out["choppiness"], {"chop": 64.2,
                                            "regime": "choppy"})
        # exactly 2 hunts: squeeze + choppiness, 15m tape, 96 bars,
        # BINANCE:<SYM>USDT symbol convention for BOTH venues
        self.assertEqual(seen, [
            ("squeeze", ("BINANCE:DOGEUSDT",), "15m", 96),
            ("choppiness", ("BINANCE:DOGEUSDT",), "15m", 96)])

    def test_failure_returns_none(self):
        d = self.make_daemon()
        with mock.patch("merge.tv_hunt",
                        side_effect=RuntimeError("tvcli down")):
            self.assertIsNone(d._po_hunt_structure(
                {"symbol": "DOGE", "venue": "hyperliquid"}))
        # no symbol at all → None without any hunt
        with mock.patch("merge.tv_hunt") as fake:
            self.assertIsNone(d._po_hunt_structure({"symbol": "", }))
            fake.assert_not_called()

    def test_missing_structure_fields_tolerated(self):
        d = self.make_daemon()
        with mock.patch("merge.tv_hunt",
                        return_value={"BINANCE:DOGEUSDT": {"result": {}}}):
            out = d._po_hunt_structure({"symbol": "DOGE"})
        self.assertEqual(out["squeeze"], {"squeezeOn": False,
                                          "squeezeBars": None,
                                          "momentumDir": None})
        self.assertEqual(out["choppiness"], {"chop": None, "regime": None})

    def test_engine_attaches_structure_to_rec(self):
        """End-to-end: the engine calls hunt_fn per analyzed bot and the
        structure lands on the recommendation (fail-soft on None)."""
        d = self.make_daemon()
        d.position_optimizer.hunt_fn = lambda bot: {"at": 1.0,
                                                    "squeeze": {},
                                                    "choppiness": {}}
        rec = d.position_optimizer.post_deploy(
            {"symbol": "DOGE", "venue": "hyperliquid", "bot_code": "NEWBOT",
             "channel": {"low": 0.09, "mid": 0.10, "high": 0.11,
                         "step_pct": 0.5, "grids": 10},
             "upsert": {"amountPerTrade": 10.0, "lowPrice": 0.09,
                        "midPrice": 0.10, "highPrice": 0.11,
                        "gridLevels": 10, "gridPercentStep": 0.005},
             "ticket": {"regime": "neutral"},
             "stagnation_policy": {"regime": "neutral", "step": 0.005}},
            "1", dry_run=True)
        self.assertTrue(rec is None or isinstance(rec, dict))
        if rec:
            self.assertEqual(rec.get("tvcli_structure"),
                             {"at": 1.0, "squeeze": {}, "choppiness": {}})


class TestAdoptedBotGeometryBackfill(unittest.TestCase):
    """Pure: the health-cycle merge only touches ``adopted: true`` bots.

    A non-adopted bot's ``channel`` can be in flight from a recent edit
    (the 2h ``adjust_cooldown_h`` window) — overwriting it from the
    observation would clobber a planned geometry change. Adopted bots
    were created on WT, not by this daemon, so backfilling is always
    safe.
    """

    def _bot(self, adopted, channel=None, upsert=None):
        return {"adopted": adopted, "bot_code": "B1",
                "observed": {"status": "active", "price": 1.0,
                             "fills_24h": 0, "realized_pnl": 0.0,
                             "unrealized_pnl": 0.0, "open_losing": 0,
                             "open_lines": 0},
                "channel": channel, "upsert": upsert}

    def test_adopted_bot_gets_geometry(self):
        b = self._bot(adopted=True)
        obs = {"exits": {}, "channel": {"low": 1.0, "high": 2.0,
                                       "mid": 1.5, "step_pct": 0.5,
                                       "grids": 10},
               "upsert": {"lowPrice": 1.0, "highPrice": 2.0,
                          "midPrice": 1.5, "gridPercentStep": 0.005,
                          "gridLevels": 10}}
        # emulate the merge block in health_cycle
        if isinstance(obs.get("exits"), dict):
            b["exits"] = obs["exits"]
        if b.get("adopted") and isinstance(obs.get("channel"), dict) \
                and obs.get("channel"):
            b["channel"] = obs["channel"]
        if b.get("adopted") and isinstance(obs.get("upsert"), dict) \
                and obs.get("upsert"):
            b["upsert"] = obs["upsert"]
        self.assertEqual(b["channel"]["low"], 1.0)
        self.assertEqual(b["channel"]["grids"], 10)
        self.assertEqual(b["upsert"]["gridLevels"], 10)

    def test_non_adopted_bot_keeps_existing_geometry(self):
        b = self._bot(adopted=False,
                      channel={"low": 0.5, "high": 1.5, "mid": 1.0,
                               "step_pct": 0.5, "grids": 8},
                      upsert={"gridLevels": 8})
        obs = {"exits": {},
               "channel": {"low": 99.0, "high": 100.0, "mid": 99.5,
                           "step_pct": 0.1, "grids": 99},
               "upsert": {"gridLevels": 99}}
        # emulate the merge block — note the `b.get("adopted")` gates
        if isinstance(obs.get("exits"), dict):
            b["exits"] = obs["exits"]
        if b.get("adopted") and isinstance(obs.get("channel"), dict) \
                and obs.get("channel"):
            b["channel"] = obs["channel"]
        if b.get("adopted") and isinstance(obs.get("upsert"), dict) \
                and obs.get("upsert"):
            b["upsert"] = obs["upsert"]
        # the existing geometry survives (was 0.5 / 8 lines, not 99)
        self.assertEqual(b["channel"]["grids"], 8)
        self.assertEqual(b["channel"]["low"], 0.5)
        self.assertEqual(b["upsert"]["gridLevels"], 8)


if __name__ == "__main__":
    unittest.main()
