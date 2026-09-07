"""Unit tests for agents/reflect.py + swarm memory/market_context wiring.

Offline only: GRID_STATE_DIR is pointed at a temp dir per test, so no
repository state is touched and no network is used.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))

import reflect  # noqa: E402
from swarm import brief_text  # noqa: E402


def _brief(**kw):
    b = {"symbol": "PUMP", "venue": "hyperliquid",
         "regime": "chop_high_volatility", "score_final": 111.0,
         "step": 1.087, "metrics": {"price": 1.0, "atr_pct": 2.174},
         "slot": {"slot": 1, "balance": 125.0},
         "stagnation_policy": {"cooldown_h": 18.0}}
    b.update(kw)
    return b


def _ticket(**kw):
    t = {"symbol": "PUMP", "venue": "hyperliquid",
         "regime": "chop_high_volatility", "grid_type": "neutral",
         "decision": "GO", "rationale": "range harvest",
         "llm_degraded": False, "max_alloc_mult": 0.8, "step_mult": 1.0,
         "risk": {"conservative": {"max_alloc_mult": 0.8, "step_mult": 1.0}}}
    t.update(kw)
    return t


class ReflectCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old_env = os.environ.get("GRID_STATE_DIR")
        os.environ["GRID_STATE_DIR"] = self.tmp.name
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._old_env is None:
            os.environ.pop("GRID_STATE_DIR", None)
        else:
            os.environ["GRID_STATE_DIR"] = self._old_env

    def _decisions_path(self):
        return os.path.join(self.tmp.name, "decisions.jsonl")


class TestRecordRoundTrip(ReflectCase):
    def test_record_decision_outcome_round_trip(self):
        payloads = {"grid_bot": {"channel": {"low": 0.95, "mid": 1.0,
                                             "high": 1.05},
                                 "profit_per_grid_pct": 1.087, "grids": 12}}
        did = reflect.record_decision(_ticket(), _brief(), "deploy", payloads)
        self.assertRegex(did, r"^d\d{8}-\d{3}$")
        path = self._decisions_path()
        self.assertTrue(os.path.isfile(path))
        with open(path) as f:
            line = json.loads(f.readline())
        self.assertEqual(line["id"], did)
        self.assertEqual(line["symbol"], "PUMP")
        self.assertEqual(line["venue"], "hyperliquid")
        self.assertEqual(line["action"], "deploy")
        self.assertEqual(line["step_pct"], 1.087)
        self.assertEqual(line["slot"], 1)
        self.assertEqual(line["channel"]["mid"], 1.0)
        self.assertFalse(line["llm_degraded"])
        self.assertEqual(line["risk_multipliers"]["max_alloc_mult"], 0.8)
        self.assertEqual(len(line["payload_digest"]), 32)
        self.assertNotIn("outcome", line)

        final = {"closed_at": "2026-09-04T10:00:00+00:00", "reason": "rotated",
                 "realized_pnl": 3.21, "fills": 12, "holding_h": 26.5,
                 "observed": {}}
        reflect.record_outcome(did, final)
        with open(path) as f:
            updated = json.loads(f.readline())
        self.assertEqual(updated["id"], did)
        self.assertEqual(updated["outcome"]["reason"], "rotated")
        self.assertEqual(updated["outcome"]["realized_pnl"], 3.21)
        self.assertEqual(updated["outcome"]["closed_at"],
                         "2026-09-04T10:00:00+00:00")
        # the append plus the atomic rewrite must preserve a valid journal
        did2 = reflect.record_decision(_ticket(), _brief(), "adjust", payloads)
        self.assertNotEqual(did, did2)
        with open(path) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["outcome"]["reason"], "rotated")
        self.assertEqual(lines[1]["action"], "adjust")

    def test_record_outcome_missing_decision_raises(self):
        with self.assertRaises(KeyError):
            reflect.record_outcome("d19700101-001", {"reason": "manual"})


class TestMemories(ReflectCase):
    def _seed(self, rows):
        path = self._decisions_path()
        with open(path, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def test_missing_file_returns_empty(self):
        self.assertEqual(reflect.memories_for(_brief(), k=3), [])
        self.assertEqual(reflect.memories_for(_brief(), k=0), [])

    def test_ranking_symbol_beats_family_beats_recency(self):
        self._seed([
            {"id": "d20260101-001", "at": "2026-01-01T10:00:00+00:00",
             "symbol": "PUMP", "venue": "hyperliquid",
             "regime": "chop_high_volatility", "grid_type": "neutral",
             "action": "deploy", "llm_degraded": False,
             "outcome": {"closed_at": "2026-01-01T12:00:00+00:00",
                         "reason": "rotated", "realized_pnl": 1.0,
                         "holding_h": 2.0}},
            {"id": "d20260101-002", "at": "2026-01-05T10:00:00+00:00",
             "symbol": "DOGE", "venue": "hyperliquid", "regime": "squeeze",
             "grid_type": "neutral", "action": "deploy", "llm_degraded": True,
             "outcome": {"closed_at": "2026-01-05T12:00:00+00:00",
                         "reason": "stagnant", "realized_pnl": -0.5,
                         "holding_h": 9.0}},
            {"id": "d20260101-003", "at": "2026-01-10T10:00:00+00:00",
             "symbol": "BTC", "venue": "binance", "regime": "trend_up",
             "grid_type": "long", "action": "deploy", "llm_degraded": False,
             "outcome": {"closed_at": "2026-01-10T12:00:00+00:00",
                         "reason": "stop_trigger", "realized_pnl": 2.0,
                         "holding_h": 3.0}},
            # same symbol+venue but NO outcome -> must be excluded
            {"id": "d20260101-004", "at": "2026-01-11T10:00:00+00:00",
             "symbol": "PUMP", "venue": "hyperliquid", "regime": "neutral",
             "grid_type": "neutral", "action": "adjust", "llm_degraded": False},
        ])
        mems = reflect.memories_for(_brief(), k=3)
        self.assertEqual([m["symbol"] for m in mems], ["PUMP", "DOGE", "BTC"])
        self.assertEqual(len(mems), 3)
        self.assertFalse(any(m["symbol"] == "PUMP" and m["action"] == "adjust"
                             for m in mems))
        self.assertEqual(mems[0]["reason"], "rotated")
        self.assertEqual(mems[1]["llm_degraded"], True)

    def test_regime_family_is_range_family(self):
        self._seed([
            {"id": "d20260101-001", "at": "2026-01-01T10:00:00+00:00",
             "symbol": "DOGE", "venue": "hyperliquid", "regime": "neutral",
             "grid_type": "neutral", "action": "deploy", "llm_degraded": False,
             "outcome": {"closed_at": "2026-01-01T12:00:00+00:00",
                         "reason": "manual", "realized_pnl": 0.1}},
            {"id": "d20260101-002", "at": "2026-01-02T10:00:00+00:00",
             "symbol": "BTC", "venue": "hyperliquid", "regime": "trend_up",
             "grid_type": "long", "action": "deploy", "llm_degraded": False,
             "outcome": {"closed_at": "2026-01-02T12:00:00+00:00",
                         "reason": "manual", "realized_pnl": 0.2}},
        ])
        # chop query -> neutral (range) wins over trend_up despite older close
        mems = reflect.memories_for(_brief(regime="chop_high_volatility"), k=2)
        self.assertEqual([m["symbol"] for m in mems], ["DOGE", "BTC"])


class TestRunCard(ReflectCase):
    def test_run_card_writes_json_and_md(self):
        report = {
            "at": "2026-09-04T09:00:00+00:00",
            "cycle_kind": "rescreen",
            "paper": True,
            "screen": {"n_candidates": 5,
                       "top3": [{"venue": "hyperliquid", "symbol": "PUMP",
                                 "regime": "squeeze", "score_final": 111.0,
                                 "step": 1.087}]},
            "deliberations": [{"symbol": "PUMP", "decision": "GO",
                               "confidence": 0.7, "llm_degraded": True,
                               "veto": False}],
            "guard": [{"symbol": "PUMP", "ok": True, "violations": []}],
            "deployments": [{"slot": 1, "symbol": "PUMP", "venue": "hyperliquid",
                             "grid_type": "neutral", "step_pct": 1.087,
                             "amount": 10.0, "multiplier": 0.8}],
            "rotations": [],
            "observed": {"1": {"fills_24h": 2.0, "realized_ratio": 0.1,
                               "dd_vs_atr_band": 0.2}},
            "reliability": {"samples": 4, "profit_factor": 1.1},
            "caveats": ["demo fixture"],
        }
        md_path = reflect.write_run_card(report)
        self.assertTrue(md_path.endswith(".md"))
        json_path = md_path[:-3] + ".json"
        self.assertTrue(os.path.isfile(json_path))
        with open(json_path) as f:
            self.assertEqual(json.load(f), report)
        with open(md_path) as f:
            md = f.read()
        for section in ("# Run card", "TL;DR", "## Route", "## Ground",
                        "## Deliberate", "## Guard", "## Deploy",
                        "## Observe", "## Reflect", "## Caveats"):
            self.assertIn(section, md)
        self.assertIn("paper mode", md)
        self.assertIn("llm_degraded", md)
        self.assertIn("PUMP", md)


class TestSwarmBrief(ReflectCase):
    def _seed_memory(self):
        path = self._decisions_path()
        with open(path, "w") as f:
            f.write(json.dumps({
                "id": "d20260101-001", "at": "2026-01-01T10:00:00+00:00",
                "symbol": "PUMP", "venue": "hyperliquid",
                "regime": "chop_high_volatility", "grid_type": "neutral",
                "action": "deploy", "llm_degraded": False,
                "outcome": {"closed_at": "2026-01-01T12:00:00+00:00",
                            "reason": "rotated", "realized_pnl": 1.0,
                            "holding_h": 2.0}}) + "\n")

    def test_brief_text_includes_memory_and_market_context(self):
        self._seed_memory()
        brief = _brief(market_context={"funding_rate": 0.0001,
                                       "oi_change_pct": 1.5,
                                       "vol_usd": 9_000_000.0})
        obj = json.loads(brief_text(brief))
        self.assertIn("memory", obj)
        self.assertEqual(obj["memory"][0]["symbol"], "PUMP")
        self.assertIn("market_context", obj)
        self.assertEqual(obj["market_context"]["funding_rate"], 0.0001)

    def test_brief_text_omits_when_absent(self):
        obj = json.loads(brief_text(_brief()))
        self.assertNotIn("memory", obj)
        self.assertNotIn("market_context", obj)


if __name__ == "__main__":
    unittest.main()


class EvidenceBlockCase(unittest.TestCase):
    """record_decision's `evidence` block — the console's decision-detail
    view renders this verbatim, so the contract is pinned here: debate
    theses + per-agent LLM provider attribution, risk stances, tvcli
    confluence, injected memories, fill/harvest model. Degraded tickets
    must surface rule-fallback providers, never look LLM-served."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old_env = os.environ.get("GRID_STATE_DIR")
        os.environ["GRID_STATE_DIR"] = self.tmp.name
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._old_env is None:
            os.environ.pop("GRID_STATE_DIR", None)
        else:
            os.environ["GRID_STATE_DIR"] = self._old_env

    def _decisions_path(self):
        return os.path.join(self.tmp.name, "decisions.jsonl")

    def test_evidence_block_shape(self):
        ticket = _ticket(
            confidence=0.72, facilitator_llm="mistral",
            debate={"bull": {"thesis": "wide ATR range pays the grid",
                             "confidence": 0.8, "_llm": "mistral"},
                    "bear": {"risks": ["regime flip to trend", "spread blowout"],
                             "confidence": 0.4, "_llm": "mistral"}},
            risk={"conservative": {"approve": True, "max_alloc_mult": 0.7,
                                   "step_mult": 1.0, "notes": "cap exposure",
                                   "_llm": "nvidia"}})
        brief = _brief(confluence_bonus=2.5,
                       confluence_notes=["high-chop-harvest", "moves-large"],
                       confluence={"choppiness": True, "squeeze": True,
                                   "vp": False, "errors": {}},
                       tvcli_fit={"chop": 66.1, "atr_pct": 2.7,
                                  "squeeze_on": False},
                       memories=[{"symbol": "PUMP", "venue": "hyperliquid",
                                  "regime": "chop_high_volatility",
                                  "outcome_pnl": 3.21, "reason": "rotated",
                                  "at": "2026-09-01T00:00:00+00:00"}],
                       expected_fills_per_24h=7.8,
                       harvest_net_pct_24h=4.2)
        reflect.record_decision(ticket, brief, {"kind": "NO-GO"})
        with open(self._decisions_path()) as f:
            line = json.loads(f.readline())
        ev = line["evidence"]
        self.assertEqual(ev["confidence"], 0.72)
        self.assertEqual(ev["llm"]["facilitator"], "mistral")
        self.assertEqual(ev["llm"]["bull"], "mistral")
        self.assertFalse(ev["llm"]["degraded"])
        self.assertEqual(ev["debate"]["bull_thesis"],
                         "wide ATR range pays the grid")
        self.assertEqual(len(ev["debate"]["bear_risks"]), 2)
        st = ev["risk_stances"]["conservative"]
        self.assertTrue(st["approve"])
        self.assertEqual(st["llm"], "nvidia")
        self.assertEqual(ev["confluence"]["bonus"], 2.5)
        self.assertEqual(ev["confluence"]["ok"], 2)
        self.assertEqual(ev["confluence"]["notes"],
                         ["high-chop-harvest", "moves-large"])
        self.assertEqual(ev["confluence"]["fit"]["chop"], 66.1)
        self.assertEqual(ev["memories"][0]["outcome_pnl"], 3.21)
        self.assertEqual(ev["expected_fills_24h"], 7.8)
        self.assertEqual(ev["harvest_net_pct_24h"], 4.2)

    def test_evidence_block_degrades_to_rule_fallback(self):
        # rule-fallback debate objects carry no _llm markers beyond the
        # fallback itself; nothing may raise, nothing may be fabricated
        reflect.record_decision(_ticket(), _brief(), {"kind": "NO-GO"})
        with open(self._decisions_path()) as f:
            line = json.loads(f.readline())
        ev = line["evidence"]
        self.assertEqual(ev["llm"]["bull"], None)
        self.assertEqual(ev["confluence"]["bonus"], None)
        self.assertEqual(ev["confluence"]["ok"], 0)
        self.assertEqual(ev["memories"], [])

    def test_evidence_block_reads_openings_after_rebuttal(self):
        # deliberate() replaces the debate bull/bear with rebuttal dicts
        # ({"refined","concedes"}) — the evidence block must surface the
        # stashed openings (bull_open/bear_open), which hold the actual
        # thesis/risks/kill_triggers the agents argued from
        ticket = _ticket(
            confidence=0.81, facilitator_llm="mistral",
            debate={"bull": {"refined": "post-rebuttal refinement",
                             "concedes": ["thin OI"], "confidence": 0.81,
                             "_llm": "mistral"},
                    "bear": {"refined": "post-rebuttal caution",
                             "concedes": ["harvest model"],
                             "confidence": 0.3, "_llm": "mistral"},
                    "bull_open": {"thesis": "EMA stack 20>50>200 confirms",
                                  "invalidation": "regime flip",
                                  "confidence": 0.82, "_llm": "mistral"},
                    "bear_open": {"risks": ["thin OI slippage",
                                            "ADX near threshold"],
                                  "kill_triggers": ["PF<1.0 over last 20"],
                                  "confidence": 0.3, "_llm": "mistral"}})
        brief = _brief(confluence={"squeeze": True, "choppiness": False,
                                   "vp": True, "errors": {}},
                       confluence_bonus=3.0,
                       confluence_notes=["moves-large"])
        reflect.record_decision(ticket, brief, {"kind": "NO-GO"})
        with open(self._decisions_path()) as f:
            line = json.loads(f.readline())
        ev = line["evidence"]
        self.assertEqual(ev["debate"]["bull_thesis"],
                         "EMA stack 20>50>200 confirms")
        self.assertEqual(len(ev["debate"]["bear_risks"]), 2)
        self.assertEqual(ev["debate"]["bear_risks"][0], "thin OI slippage")
        self.assertEqual(ev["debate"]["kill_triggers"],
                         ["PF<1.0 over last 20"])
        self.assertEqual(ev["confluence"]["ok"], 2)
        # without stashed openings, falls back to the rebuttal dict — never
        # crashes, never fabricates
        ticket["debate"].pop("bull_open")
        ticket["debate"].pop("bear_open")
        reflect.record_decision(ticket, brief, {"kind": "NO-GO"})
        with open(self._decisions_path()) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        ev2 = lines[1]["evidence"]
        self.assertEqual(ev2["debate"]["bull_thesis"],
                         "post-rebuttal refinement")
        self.assertEqual(ev2["debate"]["bear_risks"], [])
