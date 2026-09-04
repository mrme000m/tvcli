"""Unit tests for agents/swarm.py — stubbed LLM chain, no network."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))

from swarm import deliberate, risk_review

BRIEF = {"symbol": "PUMP", "venue": "hyperliquid", "tv_symbol": "BINANCE:PUMPUSDT",
         "regime": "chop_high_volatility", "score_final": 111.0, "step": 1.087,
         "spread_pct": 0.046, "metrics": {"price": 1.0, "atr_pct": 2.174,
         "adx14": 18.7, "rsi14": 52.8, "bb_width_pctile": 52.6},
         "confluence_notes": ["squeeze-fires"], "evidence": {},
         "slot": {"slot": 1, "balance": 125.0, "max_commitment": 62.5}}


def stub_chain(replies):
    """_chain of (name, fn) stubs popping canned JSON replies in order."""
    import json as _json
    it = iter(replies)

    def fn(msgs, mt):
        return _json.dumps(next(it))
    return [("stub", fn)] * 8


GO_REPLIES = [
    {"side": "neutral", "thesis": "range", "invalidation": "breakout", "confidence": 0.8},
    {"risks": ["breakout"], "kill_triggers": ["PF<1"], "confidence": 0.4},
    {"refined": "still go", "concedes": ["tail"], "confidence": 0.75},
    {"refined": "risk noted", "concedes": ["range"], "confidence": 0.45},
    {"decision": "GO", "grid_type": "neutral", "rationale": "edge", "confidence": 0.7},
    {"approve": True, "max_alloc_mult": 1.0, "step_mult": 1.0, "notes": "ok", "veto_reason": None},
    {"approve": True, "max_alloc_mult": 0.8, "step_mult": 1.2, "notes": "trim", "veto_reason": None},
    {"approve": True, "max_alloc_mult": 0.5, "step_mult": 0.9, "notes": "half", "veto_reason": None},
]


class TestSwarm(unittest.TestCase):
    def test_go_ticket_tightest_wins(self):
        t = deliberate(dict(BRIEF), _chain=stub_chain(list(GO_REPLIES)))
        self.assertEqual(t["decision"], "GO")
        self.assertEqual(t["grid_type"], "neutral")
        self.assertEqual(t["max_alloc_mult"], 0.5)  # conservative wins
        self.assertFalse(t["llm_degraded"])

    def test_facilitator_nogo(self):
        replies = list(GO_REPLIES)
        replies[4] = {"decision": "NO_GO", "grid_type": "neutral",
                      "rationale": "no", "confidence": 0.9}
        t = deliberate(dict(BRIEF), _chain=stub_chain(replies))
        self.assertEqual(t["decision"], "NO_GO")
        self.assertIn("veto", t)

    def test_risk_veto(self):
        replies = list(GO_REPLIES)
        replies[7] = {"approve": False, "max_alloc_mult": 0.0, "step_mult": 1.0,
                      "notes": "x", "veto_reason": "too hot"}
        t = deliberate(dict(BRIEF), _chain=stub_chain(replies))
        self.assertEqual(t["decision"], "NO_GO")
        self.assertIn("too hot", t["veto"])

    def test_total_llm_outage_falls_back(self):
        def dead(msgs, mt):
            raise RuntimeError("all providers down")

        t = deliberate(dict(BRIEF), _chain=[("dead", dead)])
        self.assertTrue(t["llm_degraded"])
        self.assertIn(t["decision"], ("GO", "NO_GO"))
        # rule fallback: score 111 > 45 → GO, neutral grid
        self.assertEqual(t["decision"], "GO")
        self.assertEqual(t["grid_type"], "neutral")

    def test_low_score_fallback_nogo(self):
        def dead(msgs, mt):
            raise RuntimeError("down")

        b = dict(BRIEF, score_final=10.0)
        t = deliberate(b, _chain=[("dead", dead)])
        self.assertEqual(t["decision"], "NO_GO")

    def test_spot_short_rule_veto(self):
        b = dict(BRIEF, venue="binance")
        ticket = {"grid_type": "short"}
        r, degraded = risk_review(b, ticket, "conservative")
        self.assertFalse(r["approve"])
        self.assertIn("short", r["veto_reason"])


if __name__ == "__main__":
    unittest.main()
