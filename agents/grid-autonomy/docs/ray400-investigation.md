# binance:RAY grid-bot create → WT 400 — root-cause investigation

**Date:** 2026-09-08 (az00 deployment evidence) · investigated offline from
`/srv/tvcli` with public market data only (no WT session calls).

## Verdict (one line)

RAYUSDT is **absent from the Binance FUTURES TESTNET** — the venue
WunderTrading's Binance paper (demo) engine runs against — while ZROUSDT is
`TRADING` there; WT therefore cannot create a paper grid bot for RAY and
answers the generic form-validation 400 "Please check the highlighted fields
for errors and try again." (deterministic, no field detail). This is a
**WT-side constraint**, not a payload/sizing defect; the screen-side guard is
implemented in `execution/resolve.py::paper_pair_supported()`.

## Evidence chain

1. **Both payloads reproduced offline, exactly.** Using public Binance spot
   candles (data-api.binance.vision, 1h, ending at the incident minutes) and
   the daemon's real code path (`grid_adapter.build_ticket_payloads` +
   the daemon's tier-cap → size-fit sequence), the journal numbers fall out
   digit-for-digit:
   - RAY: slot 4 = $120 (worst $60 = 50% of slot, as journalled),
     base tier 0.25 → per-line raised to the $10 min floor → **12 grids @
     1.707% step, $10/line, side_lines 6, worst-case $60 = 50% of slot**
     ("size-floor" branch). ✔ matches the journal.
   - ZRO: after the RAY failure the sleeve re-split to $60 slots (the
     fallthrough bug documented in `tests/test_deploy_failure_fallthrough.py`),
     tier-cap 12 → size-fit `int(2·$30/$10)=6` grids, step widened to
     **step_max 2.0%, worst-case $30**. ✔ matches the journal.
2. **Payload diff (reconstructed; identical fields omitted):**

   | field | RAY (400 @ 02:57:47) | ZRO (200 @ 03:23:46) |
   |---|---|---|
   | `pairCode` | `RAYUSDT` | `ZROUSDT` |
   | `gridTradingType` | `long` | `neutral` |
   | `gridPercentStep` | `0.01707` | `0.02` |
   | `gridLevels` | `12` | `6` |
   | `midPrice` = `initPrice` | 1.2219 | 1.129 |
   | `closestHighLevelPrice` | 1.224698 | 1.150135 |
   | `closestLowLevelPrice` | 1.204144 | 1.127583 |
   | `highPrice` / `lowPrice` | 1.337370 / 1.106430 | 1.195453 / 1.062547 |
   | `amountPerTrade` (type `base`) | 10.0 (USD) | 10.0 (USD) |
   | everything else (exchangeCode `BINANCE_FUTURES`, profile `demo-bn`, `gridType interval`, `gridMethod classic`, `leverage` 1, `maxRequiredAmount` null, `stopCondition stop_and_close_all`, `pumpProtection` true, …) | **identical** | **identical** |

   Price/ATR inputs come from public candles at the incident minutes
   (RAY ≈ $1.21–1.22, ATR% ≈ 3.1; ZRO ≈ $1.13, ATR% ≈ 2.0); derived
   step/grids/worst-case match the journal exactly, so the reconstruction is
   faithful.
3. **Exchange-level facts (public data):**
   - RAYUSDT and ZROUSDT are both `TRADING` on Binance **spot** (the
     screener's universe source: `screen/merge.py::binance_spot_universe`).
   - RAYUSDT and ZROUSDT are both live on Binance **USDT-M mainnet futures**
     — Binance Vision dump listings for both run through the same last day
     (`data/futures/um/daily/klines/{RAY,ZRO}USDT/1h/…-2026-09-06.zip`),
     so neither is delisted. This is why WT's `:2087/all-markets` derivative
     map contains both and the daemon's `pairCode_from_get_exchange_markets`
     guard **passes** for RAY.
   - **`https://testnet.binancefuture.com/fapi/v1/exchangeInfo` (735
     symbols): `ZROUSDT` present & `TRADING`; `RAYUSDT` absent.**
   - WunderTrading's Binance paper sleeve is BINANCE_FUTURES-only and its
     demo flow signs up against the Binance futures demo/testnet
     (`agents/grid-autonomy/docs/binance-paper-profile.md`).
4. **Blast radius:** of the current top-60 Binance spot USDT universe, 10
   pairs are missing on the futures testnet (`RAYUSDT`, `ICPUSDT`, `PEPEUSDT`, `BONKUSDT`, `XAUTUSDT`, `UUSDT`, `MARSCOINUSDT`, `GRAMUSDT`, `CFGUSDT`, `CRCLBUSDT`). A persistent top screener pick among
   them burns 3 create retries every rescreen cycle, all day — exactly the
   observed RAY pattern.

## Hypothesis ranking

| # | Hypothesis | Status |
|---|---|---|
| (c) pair not runnable on the WT **paper** sleeve (absent on Binance futures testnet) | **ROOT CAUSE** — direct public-data confirmation; explains determinism, the identical-success ZRO control, and the generic no-detail error (WT cannot even price the pair on the demo venue) |
| (f) unmodeled confounder: `gridTradingType long` (RAY) vs `neutral` (ZRO) | Low. `long` is verified-live on a derivative paper create (HYPERLIQUID_SWAP, `browser-debug/dumps/wt-grid-investigation-2026-09-04.jsonl`, 200 + gridBotCode). Disambiguating experiment (operator, one paper create, no code): create RAYUSDT on demo-bn with `gridTradingType: neutral`, same geometry — if it still 400s, (f) is dead; alternatively open the WT UI pair picker on demo-bn and check RAYUSDT availability |
| (b) `gridPercentStep` 1.707% below a per-pair WT minimum | Weak — no known per-pair minimum mechanism; 1.707% is far above any fee-based floor. Would also not be RAY-specific |
| (a) price-field tick-size violations | **Refuted** — ZRO's accepted payload contains off-tick 6-dp prices (1.195453 vs the 0.001 spot tick / 0.0001 testnet tick); the verified HY create sent midPrice 0.066436. WT does not strictly tick-validate price fields |
| (d) `gridLevels × amountPerTrade` / `maxRequiredAmount` interplay | **Refuted** — `amountPerTrade` 10.0, `maxRequiredAmount` null, identical for both; $10k paper balance dwarfs both worst cases |
| (e) `initPrice` vs closest-level ordering after rounding | **Refuted** — recomputing the documented geometry from the *rounded* payload prices reproduces `gridLevels` (12/12, 6/6) and the closest levels for both payloads, with comfortable loop margins (RAY's stop margin 0.34% away from the 1.707% threshold) |

## What was NOT wrong

- `compute_upsert` / `build_ticket_payloads` sizing and geometry are
  self-consistent and match WT's documented client-side geometry; both
  journal outcomes were reproduced offline through the real code.
- No price-field rounding defect (see (a) refutation); no amount-currency
  mistake ($10 = 10 USD-stable per grid line, both creates).

## Fix

WT-side constraint → **screen-side guard** (cheapest safe), implemented:

### Implemented (this investigation)

- **`agents/grid-autonomy/execution/resolve.py`** (additive, +129 lines):
  - `binance_paper_futures_symbols()` — fetches the public, unauthenticated,
    non-geo-gated Binance futures **testnet** `exchangeInfo`, keeps only
    `TRADING` symbols, caches 24h in `state/paper_futures_symbols.json`
    (same cache pattern as `market_meta.json`; stale-cache fallback).
  - `paper_pair_supported(venue, symbol, market=None)` →
    `True` (supported / no constraint), `False` (provably not runnable on
    the paper sleeve → screen out), `None` (unknown → **fail open**, let
    WT decide). Only binds for `binance` + `derivative` (the
    BINANCE_FUTURES paper sleeve); Hyperliquid paper runs WT's own engine
    and is unconstrained.
  - CLI: `resolve.py --venue binance --symbol RAY --market derivative
    --paper-check` → `{"paper_pair_supported": false}` (exit 1); ZRO →
    `true` (exit 0). Live-smoked against the real testnet.
- **`agents/grid-autonomy/tests/test_paper_pair_guard.py`** — 13 focused
  unit tests, network fully mocked: TRADING-only filtering, fail-soft fetch,
  fail-open on unknown testnet, symbol normalization (RAY / RAYUSDT /
  RAY/USDT / ray), venue/market scoping, 24h cache (fetch-once),
  stale-cache fallback, empty-unknown. **All pass.**

Tests run (focused only, per instructions):

```
python3 -m unittest discover -s agents/grid-autonomy/tests -t agents/grid-autonomy   -p test_paper_pair_guard.py -v   # 13 tests OK
python3 -m unittest discover -s agents/grid-autonomy/tests -t agents/grid-autonomy   -p test_resolve.py -v            # 13 existing resolve tests OK (no regression)
```

### Proposed wiring (files owned by others — for the daemon/worker owner)

1. **Screen level (preferred, cheapest):** in
   `screen/merge.py::screen_binance`, drop a candidate when
   `paper_pair_supported("binance", base, "derivative") is False` — RAY
   never reaches deliberation, guardrails, or the doomed create retries.
2. **Deploy level (defense in depth):** in `daemon.py`'s deploy path (next
   to the `pair_meta` lookup), treat `False` as a `guard-veto`
   ("pair not runnable on the BINANCE_FUTURES paper sleeve — absent on
   Binance futures testnet") so even hand-picked/rotated candidates are
   covered.
3. **400-learner (optional):** extend the demo-cap learner pattern — a
   create-400 whose message is the *generic* "Please check the highlighted
   fields…" (i.e. not "Maximum number of Grid Bots…") on retry-exhaustion
   should add the pair to a runtime per-venue blacklist, so an unknown
   paper-venue rejection (testnet unreachable at screen time) still
   converges instead of retrying all day.

## Reproduction artifacts

- Reconstruction script inputs: `data-api.binance.vision` 1h klines for
  RAYUSDT (endTime 1788836267000 = 2026-09-08T02:57:47Z) and ZROUSDT
  (endTime 1788837826000 = 2026-09-08T03:23:46Z); metrics via
  `market_regime.compute_metrics` (RAY: price 1.2073–1.2219, atr_pct 3.09–3.15;
  ZRO: price 1.129, atr_pct 1.962 — the small band difference only shifts
  the reconstructed ATR needed for 12 vs 13 RAY lines).
- Testnet membership: `GET https://testnet.binancefuture.com/fapi/v1/exchangeInfo`
  (735 symbols; ZROUSDT TRADING, RAYUSDT absent).
- Mainnet-futures liveness: Binance Vision S3 listing
  `data/futures/um/daily/klines/{RAY,ZRO}USDT/1h/` both through 2026-09-06.
