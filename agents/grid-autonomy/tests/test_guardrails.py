"""Unit tests for execution/guardrails.py + grid_adapter.compute_upsert."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "execution"))

from guardrails import deploy
from grid_adapter import compute_upsert


def base_ctx(**kw):
    ctx = {
        "kill_file": "/nonexistent/KILL",
        "pair_code": "159",
        "profiles_active": {"prof1": 1000.0},
        "profile_code": "prof1",
        "slot_balance": 125.0, "max_alloc": 0.5,
        "total_commitment": 60.0,
        "deployable_ceiling": 425.0, "committed_now": 0.0,
        "step_pct": 1.0, "spread_pct": 0.05,
        "venue": "hyperliquid", "grid_type": "neutral",
        "paper": True,
        "is_rotation": False,
    }
    ctx.update(kw)
    return ctx


GO = {"decision": "GO"}


class TestGuardrails(unittest.TestCase):
    def test_all_pass_paper(self):
        ok, v = deploy(GO, base_ctx())
        self.assertTrue(ok, v)

    def test_ticket_nogo_blocks(self):
        ok, v = deploy({"decision": "NO_GO", "veto": "bad"}, base_ctx())
        self.assertFalse(ok)
        self.assertTrue(any("not GO" in x for x in v))

    def test_kill_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            ok, v = deploy(GO, base_ctx(kill_file=path))
            self.assertFalse(ok)
            self.assertTrue(any("KILL" in x for x in v))
        finally:
            os.unlink(path)

    def test_paircode_unresolved(self):
        ok, v = deploy(GO, base_ctx(pair_code="<RESOLVE>"))
        self.assertFalse(ok)

    def test_profile_inactive(self):
        ok, v = deploy(GO, base_ctx(profile_code="ghost"))
        self.assertFalse(ok)
        ok, v = deploy(GO, base_ctx(profiles_active={}))
        self.assertFalse(ok)

    def test_oversize_blocked(self):
        ok, v = deploy(GO, base_ctx(total_commitment=62.6))
        self.assertFalse(ok)  # 62.6 > 125×0.5=62.5
        ok, v = deploy(GO, base_ctx(total_commitment=62.5))
        self.assertTrue(ok, v)

    def test_portfolio_ceiling(self):
        ok, v = deploy(GO, base_ctx(committed_now=400.0, total_commitment=60.0))
        self.assertFalse(ok)

    def test_unharvestable_step(self):
        ok, v = deploy(GO, base_ctx(step_pct=0.05, spread_pct=0.05))
        self.assertFalse(ok)

    def test_spot_short_rejected(self):
        ok, v = deploy(GO, base_ctx(venue="binance", grid_type="short"))
        self.assertFalse(ok)

    def test_live_needs_samples(self):
        ok, v = deploy(GO, base_ctx(paper=False, reliability={"samples": 5, "profit_factor": 2.0}))
        self.assertFalse(ok)
        ok, v = deploy(GO, base_ctx(paper=False,
                                    reliability={"samples": 40, "profit_factor": 1.5}))
        self.assertTrue(ok, v)
        ok, v = deploy(GO, base_ctx(paper=False, reliability={"samples": 40,
                                                              "profit_factor": 1.5, "recent_pf": 0.8}))
        self.assertFalse(ok)

    def test_rotation_gates(self):
        ok, v = deploy(GO, base_ctx(is_rotation=True, cooldown_ok=False,
                                    candidate_score=100, incumbent_score=50))
        self.assertFalse(ok)
        ok, v = deploy(GO, base_ctx(is_rotation=True, cooldown_ok=True,
                                    candidate_score=52, incumbent_score=50))
        self.assertFalse(ok)  # Δ2 < 5
        ok, v = deploy(GO, base_ctx(is_rotation=True, cooldown_ok=True,
                                    candidate_score=60, incumbent_score=50))
        self.assertTrue(ok, v)


class TestUpsert(unittest.TestCase):
    def test_geometry(self):
        u = compute_upsert("PUMP", "hyperliquid", 100.0, 2.0, 1.0, 10,
                           20.0, "neutral", "prof1", "123")
        self.assertEqual(u["exchangeCode"], "HYPERLIQUID_SWAP")
        self.assertEqual(u["gridTradingType"], "neutral")
        self.assertEqual(u["gridPercentStep"], 0.01)
        self.assertEqual(u["stopCondition"], "stop_and_close_all")
        self.assertTrue(u["pumpProtection"])
        # channel ±6% → geometric lines: levels>2, bracketing init
        self.assertGreater(u["gridLevels"], 5)
        self.assertLessEqual(u["closestLowLevelPrice"], 100.0)
        self.assertGreater(u["closestHighLevelPrice"], 100.0)
        self.assertAlmostEqual(u["lowPrice"], 94.0)
        self.assertAlmostEqual(u["highPrice"], 106.0)

    def test_binance_spot(self):
        u = compute_upsert("BTC", "binance", 50000.0, 1.0, 0.5, 10,
                           20.0, "long", "p", "0")
        self.assertEqual(u["exchangeCode"], "BINANCE")


if __name__ == "__main__":
    unittest.main()


class TestRotationNoneScores(unittest.TestCase):
    """Regression: adopted incumbents carry score_final=None — the rotation
    gate must coerce None to 0 instead of raising float - None."""

    def test_none_incumbent_score_does_not_raise(self):
        ok, v = deploy(GO, base_ctx(is_rotation=True, cooldown_ok=True,
                                    candidate_score=102.0,
                                    incumbent_score=None, hysteresis=5.0))
        self.assertTrue(ok, v)
        # None incumbent coerces to 0: challenger passes the Δscore gate

    def test_none_candidate_score_does_not_raise(self):
        ok, v = deploy(GO, base_ctx(is_rotation=True, cooldown_ok=True,
                                    candidate_score=None,
                                    incumbent_score=0.0, hysteresis=5.0))
        self.assertFalse(ok)
        self.assertTrue(any("hysteresis" in x for x in v))
