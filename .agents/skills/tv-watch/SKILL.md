---
name: tv-watch
description: Watchtower agent protocol — how to act when invoked by a market-watch trigger event. Use when an invocation package from agents/watchtower/episodes/ is delivered, when the WATCHTOWER mission fires an alert, or when asked to assess/react to a XAUUSD watchtower event with analysis context.
---

# tv-watch — Watchtower Agent Protocol

You are being invoked by the **watchtower** — the post-analysis market watch
machinery. An analysis (recorded in a MISSION document) speculated a market
direction with explicit levels; a trigger just fired; you now act on it with
full context and fresh numbers.

## 1. Read the context (in this order)

1. **Invocation package** (delivered with this prompt, path in
   `agents/watchtower/episodes/<ID>.md`): what fired, elapsed time, real-time
   metrics, armed-level distances, and the directed action level (L1/L2).
2. **Mission**: `agents/watchtower/MISSION.md` — situation, thesis, standing
   orders (§4), self-improvement protocol (§5).
3. **Active spec**: `agents/watchtower/specs/ACTIVE_SPEC.json` — the armed
   trigger set and its status.
4. **Journal tail**: `agents/watchtower/journal.jsonl` — what already fired.

## 2. Verify the present (never advise on stale numbers)

Run from `/Volumes/ExMac/code/tradingview/`:

```bash
python3 agents/watchtower/bin/watchtower.py status        # last poll state
python3 agents/watchtower/bin/watchtower.py poll           # fresh poll if stale
```

Optionally re-run the analysis skill the trigger came from:

```bash
cd go && ./tvcli xau-scalp --symbol OANDA:XAUUSD --tf 15 --json --agent --allow-private
```

(tvcli needs cwd `go/` or `TV_ACCOUNTS_FILE` + `TV_META_FILE` exported — see
AGENTS.md. Registry auth = accounts.json, default sunilsutar371.)

## 3. Act per the directed-action level

- **L1 — inform:** Deliver a concise human-facing notice: what fired, why it
  matters for the thesis (confirm / invalidate / neutral), current numbers
  (price, Δ vs baseline, distance to the next 2–3 levels), and what would
  change the picture. NO bare opinions — every claim carries a number.
- **L2 — re-analyze:** Run `xau-scalp` + `mtf-confluence` (+ situational
  `vp` / `gold-divergence` / `smc` as the event suggests), DIFF against the
  MISSION §1 baseline table, and if the read materially changed, render a
  chart via the tvvisual channel (see tv-scout skill) and update
  `specs/ACTIVE_SPEC.json` triggers accordingly (bump episode if the thesis
  changed). Then advise hold / add / reduce / flat WITH the numbers.
- **L0 — observe:** journal only; no user-facing output.
- **L3 — execute:** FORBIDDEN without explicit human consent for this episode
  (see MISSION §4). Advise instead.

## 4. Close the loop (always)

1. **Journal**: append your decision + rationale (1–2 lines) to
   `agents/watchtower/episodes/<ID>.md` under a `## Agent response` heading.
2. **Outcome sampling** happens automatically (`watchtower.py sample` at
   T+15m/60m/240m) — do not disable it.
3. **Spec hygiene**: if the episode is closed (TP2/L1/terminal), author the
   NEXT episode spec from the new market state, applying review lessons from
   `agents/watchtower/reviews/`.
4. **Durable lessons** (only when genuinely learned): save to agent memory.

## 5. Style rules

- Numbers first, narrative second. Cite levels from the mission table.
- State elapsed time since analysis and whether the thesis window is still open.
- Distinguish: CONFIRMED (thesis intact) / INVALIDATED (thesis dead) / NEUTRAL
  (watch continues). Say which and why in one line.
- Never print credentials or cookie values. Analysis is advisory, not financial
  advice; say so when advising a human.
