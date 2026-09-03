# Worked example — 2026-09-03/04: BTC trend-ride on Hyperliquid

A complete, real run of the wundertrading skill's Phase A–D against the live
account. Everything below was executed this session (read-only); **no trade
was placed** — Phase E blocked execution. Use as a template: copy the shape,
refresh the numbers.

## Phase A — recon (live MCP calls)

```
get_api_profiles {}                          →
  HYPERLIQUID          no_assets  (spot profile, nothing to trade)
  HYPERLIQUID_SWAP     active     4.56 USDC free   ← the only usable profile
get_live_strategies {}                      → none running
get_exchange_markets {HYPERLIQUID_SWAP}     → 280 markets, numeric pairCodes
```

Active profile code: `c629f5ba3a643a82137e7864`. PairCodes on Hyperliquid are
numeric strings: BTC-USDC → `"0"`, ETH → `"1"`, SOL → `"5"`, HYPE → `"159"`.

## Phase B/C — token screen (scripts/token_screen.py, live)

```
rank  symbol  regime      ADX    ATR%   RSI    Δ7d%   spread%  score
  1   BTC     trend_up    41.2   0.70   74.9   +1.43  0.000    7.0
  2   SOL     trend_up    36.8   0.95   68.1   −3.89  0.000    6.84
  3   ETH     trend_up    36.7   0.82   68.2   −0.26  0.000    6.83
  4   HYPE    trend_up    28.3   1.03   68.8   +0.49  0.000    6.42
```

**Pick: BTC.** Cleanest trend (ADX 41.2, EMA20>EMA50>EMA200, ATR only 0.70%,
RSI 74.9 hot but not yet > 78 overheat threshold), zero spread, deepest book.

Classifier cross-check: `market_regime.py hyperliquid BTC --interval 1h` →
`trend_up`, price 81,599 — consistent with WunderTrading's own market tick
(BTC-USDC last 81,600.5).

## Phase D — assembled config (NOT executed)

```json
{
  "exchangeCode": "HYPERLIQUID_SWAP",
  "pairCode": "0",
  "profilesCodes": ["c629f5ba3a643a82137e7864"],
  "side": "long",
  "orderType": "market",
  "takeProfits": [
    {"priceDeviation": "2%", "portfolio": "40%"},
    {"priceDeviation": "4%", "portfolio": "30%"},
    {"priceDeviation": "8%", "portfolio": "30%"}
  ],
  "takeProfitBaseOn": "entry_order",
  "stopLoss": "3%",
  "stopLossMove": "2%",
  "stopLossMoveExecute": "0%",
  "trailingStopActivation": "4%",
  "trailingStopExecute": "2%",
  "amountPerTrade": 1.14,
  "amountPerTradeType": "quote",
  "clientId": "wun-skill-btc-trendup-202609040353"
}
```

## Why it did NOT execute (Phase E checklist)

1. No explicit user confirmation of this specific trade — non-negotiable.
2. Profile balance is **4.56 USDC** — below Hyperliquid's ~$10 minimum order.
   `amountPerTrade: 1.14` (25% of balance, quote units) is illustrative.

Next real steps if this were to run: fund the swap profile (or use a demo
profile), re-run the screener for a fresh regime read, get user confirmation,
then `place_strategy_trade` with this shape. Monitor per Phase F, and after
≥ 30 closes evaluate per Phase G (`export_strategies_history`) before
scaling.
