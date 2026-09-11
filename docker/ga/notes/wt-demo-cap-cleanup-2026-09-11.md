# WT-side cleanup playbook — demo-cap phantom bots (2026-09-11)

Status: **DRAFT — requires explicit human confirmation before any WT account-state edit.**

## What happened

On 2026-09-10 the daemon's `carry-pray` flow parked 3 Hyperliquid bots into
`state.carry_pray` (CASHCAT 22:34, JUP 22:42, ARB 22:44). `carry-pray-enter`
frees the daemon slot but **does not stop or delete the WT-side grid bot** —
it keeps running on the `demo-hype` profile with a server-side takeProfit,
consuming one of the 5 demo grid-bot slots.

Result: WT's `demo-hype` profile is at **5/5 demo bots** while the daemon
only tracks 3 (`active_bots`). The two extra WT-side bots are the phantoms.
Evidence:
- `GET /status` → `used_pairs.HYPERLIQUID_SWAP` has 5 pair codes
  (`11, 90, 203, 31, 178`); tracked bots map to 203=XPL, 31=WLD, 178=VVV.
- `capacity.active.premium.HYPERLIQUID_SWAP: 5`, `account_limits.gridBots.active: 5`.
- Repeated `capacity-veto: hyperliquid:ARB already has a bot` → ARB is one phantom.
- 130+ `deploy-failed` journal entries since 22:54 UTC, all 400
  "You've reached the maximum number of Demo Trading Grid Bots! (Limit: 5)".
- Slots 2 and 3 sit free; $381.53 of $600 committed capital idle (64%).

## The two phantom bots

Likely **ARB** and **one of CASHCAT/JUP** (the third carry-pray bot may have
already stopped on WT — the daemon's completion sweep drops it). Pair codes
11 and 90 on demo-hype are the untracked ones. Exact identity must be
verified in the WT UI / grid status list (which symbols map to pair 11/90).

## Cleanup options (choose one, then confirm)

### Option A — delete the phantom bots on WT (recommended to unblock deploys)
Delete exactly the 2 phantom grid bots on the `demo-hype` paper profile in
the WunderTrading UI (the ARB bot + whichever of CASHCAT/JUP is still live).

- **Effect:** WT demo count drops 5 → 3; the daemon's next observe cycle
  sees headroom (via `used_pairs`), the carry-pray completion sweep drops the
  entries, and the deploy gate starts filling slots 2 and 3 again.
- **Cost:** books the carry-pray positions' current unrealized PnL
  (ARB ≈ −$10.7 at park time). This is a deliberate close at a loss — the
  carry-pray design was "hold until TP", so this abandons that hold.
- **How:** WT web UI → grid bots list → demo-hype profile → delete the 2
  identified bots. (Or via the container's `wt_browser.py grid delete` —
  same effect, requires operator shell in the grid-autonomy container.)
- **After:** confirm `GET /status` shows `demo_cap` active < cap and
  `used_pairs` ≤ 3; watch the next rescreen deploy into slot 2/3.

### Option B — leave the phantoms, accept idle capital
Do nothing on WT; the code fix (demo-cap gate counting WT-side live bots)
stops the futile deploy storm and the journal noise. Slots 2/3 stay empty
until the carry TPs fire (ARB target $11.13 — likely days away or never,
given the deep drawdown).

- **Effect:** no WT edit; the daemon fails closed at 5/5; 64% of capital
  stays idle indefinitely.
- **Cost:** forgone deployment of better candidates (screen top COTI 121.8,
  SAHARA 117.98, VET 117.47 …).

### Option C — hybrid: raise the demo cap instead of deleting
Not available — WT hard-caps demo grid bots at 5; the cap cannot be raised
from the daemon side (the relearn only lifts an under-learned cap, and 5 is
the platform maximum).

## Recommended sequence

1. **Verify** the phantom identities in the WT UI (grid status list on
   demo-hype: which pair codes/symbols are 11 and 90).
2. **Confirm** Option A with the human (this file is the approval record).
3. Delete the 2 phantoms on WT.
4. Wait ~1 observe cycle (60 s) and confirm `GET /status`:
   - `demo_cap.per_profile[demo-hype].active` < 5,
   - `used_pairs.HYPERLIQUID_SWAP` has 3 entries,
   - next rescreen deploys into slot 2 (journal `deploy-paper`, no 400).
5. Optional: `POST /rescreen` on ctl :8799 to trigger immediately.

## Guardrails (do not violate)

- The daemon's `loss-veto` (never close at a loss to reallocate) applies to
  the daemon's own rotations — a human-initiated WT-side delete of carry-pray
  bots is an operator decision, but it must be EXPLICIT and confirmed; it
  books the loss by intent, not by accident.
- Never delete demo-bn bots or the 3 tracked bots (XPL/WLD/VVV) by mistake.
- Do not run `dev reset-wt` — that deletes ALL paper bots on the account.