#!/usr/bin/env python3
"""Grid-autonomy deliberation swarm — TradingAgents role pattern, CF-first LLMs.

Pipeline per candidate (max 8 LLM calls, then deterministic fallback):
  Bull open → Bear open → Bull rebuttal → Bear rebuttal → Facilitator verdict
  → Risk team (seeking / neutral / conservative) → trade_ticket or veto.

Every agent returns STRICT JSON (schemas below). Any LLM failure degrades to
the rule-based fallback (regime→grid map from the playbook), so the daemon
never blocks on an LLM outage — it just logs `llm_degraded: true`.

Usage:
  from swarm import deliberate
  ticket = deliberate(brief)                      # uses live provider chain
  ticket = deliberate(brief, _chain=[...])        # stubbed in tests
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "llm"))

from provider import chat_json  # noqa: E402

GRID_TYPE = {
    "chop_high_volatility": "neutral",
    "squeeze": "neutral",
    "neutral": "neutral",
    "trend_up": "long",
    "trend_down": "short",
}

SYS = ("You are a crypto grid-trading analyst. Reply with STRICT JSON only, "
       "no markdown fences, no commentary. Obey the requested schema exactly. "
       "Be terse: every string value at most 25 words, arrays at most 3 items.")


def _call(messages, schema_hint, _chain, fallback):
    try:
        _name, obj = chat_json(messages, _chain=_chain)
        if not isinstance(obj, dict):
            raise ValueError("non-dict reply")
        obj["_llm"] = _name
        return obj, False
    except Exception:
        fb = dict(fallback)
        fb["_llm"] = "rule-fallback"
        return fb, True


def _try_reflect():
    """Lazy import of reflect.memories_for; None when unavailable."""
    try:
        from reflect import memories_for
        return memories_for
    except Exception:
        return None


def _memory_list(brief):
    fn = _try_reflect()
    if fn is None:
        return None
    try:
        mem = fn(brief)
        return mem or None
    except Exception:
        return None


def _memory_sentence(brief):
    mem = _memory_list(brief)
    if not mem:
        return ""
    return " Past outcomes for this token/venue (learn): " + json.dumps(mem) + "."


def brief_text(brief):
    m = brief.get("metrics", {})
    obj = {
        "symbol": brief.get("symbol"), "venue": brief.get("venue"),
        "regime": brief.get("regime"), "score": brief.get("score_final"),
        "price": m.get("price"), "atr_pct": m.get("atr_pct"),
        "adx": m.get("adx14"), "rsi": m.get("rsi14"),
        "bb_pctile": m.get("bb_width_pctile"), "spread_pct": brief.get("spread_pct"),
        "grid_step_pct": brief.get("step"),
        "confluence": brief.get("confluence_notes"),
        "evidence": brief.get("evidence"),
        "stagnation": brief.get("stagnation_policy"),
        "slot": brief.get("slot"),
    }
    memory = _memory_list(brief)
    if memory:
        obj["memory"] = memory
    market_context = brief.get("market_context")
    if isinstance(market_context, dict) and market_context:
        obj["market_context"] = market_context
    return json.dumps(obj)


def bull_open(brief, _chain=None):
    fb = {"side": "long" if brief.get("regime") != "trend_down" else "short",
          "thesis": "rule-fallback: regime has harvestable range",
          "invalidation": "regime switch or spread > step",
          "confidence": 0.5}
    return _call([
        {"role": "system", "content": SYS},
        {"role": "user", "content":
         f"Argue FOR deploying a grid bot on this candidate. Schema: "
         f'{{"side":"long|short|neutral","thesis":str,"invalidation":str,'
         f'"confidence":0-1}}. Candidate: {brief_text(brief)}{_memory_sentence(brief)}'}],
        None, _chain, fb)


def bear_open(brief, _chain=None):
    fb = {"risks": ["rule-fallback: unquantified tail risk"],
          "kill_triggers": ["PF<1.0 over last 20"],
          "confidence": 0.5}
    return _call([
        {"role": "system", "content": SYS},
        {"role": "user", "content":
         f"Argue AGAINST deploying a grid bot here. Schema: "
         f'{{"risks":[str],"kill_triggers":[str],"confidence":0-1}}. '
         f'Candidate: {brief_text(brief)}{_memory_sentence(brief)}'}],
        None, _chain, fb)


def rebuttal(brief, own, other, stance, _chain=None):
    fb = {"refined": f"rule-fallback {stance} stands", "concedes": [],
          "confidence": 0.5}
    return _call([
        {"role": "system", "content": SYS},
        {"role": "user", "content":
         f"You are the {stance} debater. Your opening: {json.dumps(own)}. "
         f"Opponent: {json.dumps(other)}. Respond with schema "
         f'{{"refined":str,"concedes":[str],"confidence":0-1}}. '
         f'Candidate: {brief_text(brief)}'}],
        None, _chain, fb)


def facilitator(brief, bull, bear, _chain=None):
    fb = {"decision": "GO" if brief.get("score_final", 0) > 45 else "NO_GO",
          "grid_type": GRID_TYPE.get(brief.get("regime"), "neutral"),
          "rationale": "rule-fallback: score gate", "confidence": 0.5}
    return _call([
        {"role": "system", "content": SYS},
        {"role": "user", "content":
         f"Pick the prevailing side. Bull: {json.dumps(bull)}. "
         f"Bear: {json.dumps(bear)}. Schema: "
         f'{{"decision":"GO|NO_GO","grid_type":"long|short|neutral",'
         f'"rationale":str,"confidence":0-1}}. Candidate: {brief_text(brief)}{_memory_sentence(brief)}'}],
        None, _chain, fb)


def risk_review(brief, ticket, stance, _chain=None):
    fb = {"approve": True, "max_alloc_mult": 1.0 if stance != "conservative" else 0.5,
          "step_mult": 1.0, "notes": f"rule-fallback {stance}", "veto_reason": None}
    if stance == "conservative" and brief.get("venue") == "binance" \
            and ticket.get("grid_type") == "short":
        fb = {"approve": False, "max_alloc_mult": 0.0, "step_mult": 1.0,
              "notes": "rule-fallback", "veto_reason": "spot cannot short"}
        return fb, True
    return _call([
        {"role": "system", "content": SYS},
        {"role": "user", "content":
         f"You are the {stance} risk manager. Ticket: {json.dumps(ticket)}. "
         f"Schema: {{\"approve\":bool,\"max_alloc_mult\":0-1,"
         f'"step_mult":0.5-2,"notes":str,"veto_reason":str|null}}. '
         f'Candidate: {brief_text(brief)}'}],
        None, _chain, fb)


def deliberate(brief, _chain=None, debate_rounds=1):
    """Full pipeline → trade_ticket dict (or veto). Max 5+3=8 LLM calls."""
    degraded = False
    bull, d = bull_open(brief, _chain)
    degraded |= d
    bear, d = bear_open(brief, _chain)
    degraded |= d
    for _ in range(max(debate_rounds, 0)):
        bull, d = rebuttal(brief, bull, bear, "bullish", _chain)
        degraded |= d
        bear, d = rebuttal(brief, bear, bull, "bearish", _chain)
        degraded |= d
        break  # 1 rebuttal round each = 4 calls so far; keep budget for risk
    verdict, d = facilitator(brief, bull, bear, _chain)
    degraded |= d
    ticket = {
        "symbol": brief.get("symbol"), "venue": brief.get("venue"),
        "tv_symbol": brief.get("tv_symbol"),
        "regime": brief.get("regime"),
        "decision": verdict.get("decision", "NO_GO"),
        "grid_type": verdict.get("grid_type",
                                GRID_TYPE.get(brief.get("regime"), "neutral")),
        "rationale": verdict.get("rationale", ""),
        "confidence": verdict.get("confidence", 0.5),
        "debate": {"bull": bull, "bear": bear},
        "llm_degraded": degraded,
    }
    if ticket["decision"] != "GO":
        ticket["veto"] = "facilitator NO_GO"
        return ticket
    risks = {}
    for stance in ("seeking", "neutral", "conservative"):
        r, d = risk_review(brief, ticket, stance, _chain)
        degraded |= d
        risks[stance] = r
    ticket["llm_degraded"] = degraded
    ticket["risk"] = risks
    vetoes = [f"{s}: {r['veto_reason']}" for s, r in risks.items()
              if not r.get("approve", True)]
    if vetoes:
        ticket["decision"] = "NO_GO"
        ticket["veto"] = "; ".join(vetoes)
        return ticket
    # Tightest constraint wins: min alloc multiplier, geometric-mean step.
    ticket["max_alloc_mult"] = min(r.get("max_alloc_mult", 1.0) for r in risks.values())
    import math
    steps = [max(r.get("step_mult", 1.0), 0.1) for r in risks.values()]
    ticket["step_mult"] = math.exp(sum(math.log(s) for s in steps) / len(steps))
    ticket["risk_notes"] = [r.get("notes", "") for r in risks.values()]
    return ticket


if __name__ == "__main__":
    demo = {"symbol": "PUMP", "venue": "hyperliquid", "tv_symbol": "BINANCE:PUMPUSDT",
            "regime": "chop_high_volatility", "score_final": 111.0, "step": 1.087,
            "spread_pct": 0.046, "metrics": {"price": 1.0, "atr_pct": 2.174,
            "adx14": 18.7, "rsi14": 52.8, "bb_width_pctile": 52.6},
            "confluence_notes": ["squeeze-fires"], "evidence": {},
            "slot": {"slot": 1, "balance": 125.0, "max_commitment": 62.5}}
    print(json.dumps(deliberate(demo), indent=2))
