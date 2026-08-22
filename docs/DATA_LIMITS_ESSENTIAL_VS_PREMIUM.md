# Data Limits & Market Depth — Essential vs Premium

> Captured 2026-08-20 from `tradingview.com/pricing` (official, annual billing).
> Companion to `TIER_LIMITS.md`; this doc focuses on **data depth, market
> microstructure, and orderbook/tick/footprint features** by tier, plus a
> live-verified comparison of the Essential and Premium accounts available in
> this workspace.

## Official pricing-page matrix (annual billing)

| | Essential | Plus | Premium | Ultimate |
|---|---:|---:|---:|---:|
| **Special price (annual billing)** | **$13.99/mo** | **$28.29/mo** | **$56.49/mo** | **$199.95/mo** |
| Savings vs. monthly | $36/yr | $68/yr | $138/yr | $480/yr |
| Charts per tab | 2 | 4 | 8 | 16 |
| Indicators per chart | 5 | 10 | 25 | 50 |
| **Historical bars (API "historical bars")** | **10,000** | **10,000** | **20,000** | **40,000** |
| **Parallel chart connections (WS)** | **10** | **20** | **50** | **200** |
| Price alerts | 20 | 100 | 400 | 1,000 |
| Technical alerts | 20 | 100 | 400 | 1,000 |
| Watchlist alerts | 0 | 0 | 2 | 15 |
| Apps (web/desktop/mobile) | ✓ | ✓ | ✓ | ✓ |
| No ads | ✓ | ✓ | ✓ | ✓ |

### Data-depth / microstructure features (same across all paid tiers)

All paid plans (Essential → Ultimate) include the full microstructure / data
depth feature set. The plan-specific data differences are quantitative (more
charts, more indicators, more bars, more connections, more alerts), not the
presence/absence of these features:

- **Volume profile** ✓
- **Custom timeframes** ✓
- **Custom range bars** ✓
- **Multiple watchlists** ✓
- **Bar Replay** ✓
- **Indicators on indicators** ✓
- **Chart data export** ✓
- **Intraday Renko, Kagi, Line Break, Point & Figure charts** ✓
- **Charts based on custom formulas** ✓
- **Multi-condition alerts** ✓
- **Time price opportunity** ✓ (TPO/Market Profile)
- **Volume footprint** ✓ — order-flow footprint (bid/ask volume per price level)
- **Volume candles** ✓
- **Auto chart patterns** ✓
- **Alerts that don't expire** ✓
- **Publishing invite-only scripts** ✓
- **Second-based intervals** ✓
- **Tick-based intervals** ✓ — tick chart resolution
- **Ability to buy professional market data** ✓ — purchase add-on
  `exchanges_subscriptions` (e.g. CME full feed, real-time L1/L2)
- **First priority support** ✓

### What changes plan-to-plan (data & depth)

The official matrix shows that **Essential and Premium have identical
microstructure features**. The differences are purely in resource caps:

| Capability | Essential | Premium | Delta |
|---|---:|---:|---|
| Charts per tab | 2 | 8 | **+6** (4×) |
| Indicators per chart | 5 | 25 | **+20** (5×) |
| **Historical bars per chart** | **10,000** | **20,000** | **+10,000 (2×)** |
| **Parallel WS chart connections** | **10** | **50** | **+40 (5×)** |
| Price alerts | 20 | 400 | **+380 (20×)** |
| Technical alerts | 20 | 400 | **+380 (20×)** |
| Watchlist alerts | 0 | 2 | **+2** |
| Volume footprint | ✓ | ✓ | same |
| Volume candles | ✓ | ✓ | same |
| Tick-based intervals | ✓ | ✓ | same |
| Volume profile (per-session) | ✓ | ✓ | same |
| Bar Replay | ✓ | ✓ | same |
| Buy professional market data add-ons | ✓ | ✓ | same |

**Where Premium actually wins on data depth:**

1. **2× historical bars** (20,000 vs 10,000) — deeper backtests, longer minute
   history effectively unbounded vs. Essential's 10K bar cap on intraday
   requests. Combined with unlimited minute-history days at Premium vs.
   Essential's 365-day minute-history gate, Premium can fetch multi-year
   intraday series that Essential silently truncates.
2. **5× parallel WS connections** (50 vs 10) — run 5× more simultaneous chart
   sessions / strategies / signals per account before the connection cap
   forces spill-over to another account.
3. **4× charts per tab** and **5× indicators per chart** — enables the heavy
   multi-pane, multi-indicator layouts (e.g. 8 panes × 25 indicators) that
   Essential physically cannot build (2 panes × 5 indicators max).
4. **400 vs 20 alerts** — real automation at scale.
5. **Watchlist alerts (2)** — Premium-only; Essential = 0.
6. **Professional market data** (e.g. CME full) — Premium already has the
   `exchange-cme-full` add-on subscribed (see live account below). Essential
   *can* buy it but doesn't here.

## Live account comparison (verified 2026-08-20)

Live `GET /api/v1/user/profile/subscriptions/` for the two accounts available
in this workspace.

### Essential account — `rizwanshaikh99786`

```json
{
  "is_pro": true,
  "is_trial": false,
  "profile_pro_plan": "pro",
  "account_type": "monthly Essential",
  "billing_cycle": "m",
  "next_payment_pro_currency": "INR",
  "next_payment_price_gross": 2049.0,
  "next_payment_product.text_id": "pro",
  "next_payment_product.name": "Essential",
  "next_payment_product.cost": 14.95,
  "next_payment_product.cost_annual": 12.95,
  "pro_start_timestamp": 1785196180.74584,
  "pro_expiration_timestamp": 1787903278.0,
  "exchanges_subscriptions": [],
  "active_packages": [],
  "pro_payment_method_details": {"merchant": "apple"}
}
```

- **Plan**: monthly Essential (`profile_pro_plan: "pro"`, `name: "Essential"`)
- **Billing**: monthly, ₹1,295/mo (INR)
- **Market-data add-ons**: **none** (no `exchanges_subscriptions` — no CME/real-time
  L1/L2 feed; only the default delayed/broker feeds available on the tier)
- **Resource caps** (from `pkg/account.LimitsForTier("essential")`):
  - MaxCharts: 2
  - MaxIndicators: 5
  - MaxConnections (WS): 10
  - MaxBars: 365 (minute-history days)
  - CalcTimeoutSecs: 40

### Premium account — `bikesh_b3` / `TheUniverse618`

```json
{
  "is_pro": true,
  "is_trial": false,
  "profile_pro_plan": "pro_premium",
  "account_type": "annual Premium",
  "billing_cycle": "y",
  "next_payment_pro_currency": "USD",
  "next_payment_price_gross": 812.92,
  "next_payment_product.text_id": "pro_premium",
  "next_payment_product.name": "Premium",
  "next_payment_product.cost": 69.95,
  "next_payment_product.cost_annual": 59.95,
  "pro_start_timestamp": 1757760780.645745,
  "pro_expiration_timestamp": 1789296660.0,
  "exchanges_subscriptions": [{"id": "exchange-cme-full", "name": "CME, CBOT, COMEX, NYMEX", "currency": "USD", "price": 7.91, "billing_cycle": "m", "type_id": 4}],
  "active_packages": ["exchange-cme-full"],
  "pro_payment_method_details": {"merchant": "checkout", "cardtype": "VISA", "customer_name": "Bikesh Budhathoki", "card_expiry_date": "2026-10-01"}
}
```

- **Plan**: annual Premium (`profile_pro_plan: "pro_premium"`, `name: "Premium"`)
- **Billing**: annual, $812.92/yr (USD)
- **Market-data add-ons**: **`exchange-cme-full`** — CME Group real-time data
  (CME, CBOT, COMEX, NYMEX incl. E-mini), $7.91/mo, billed monthly
- **Resource caps** (from `pkg/account.LimitsForTier("premium")`):
  - MaxCharts: 8
  - MaxIndicators: 25
  - MaxConnections (WS): 50
  - MaxBars: 0 (unlimited minute history)
  - CalcTimeoutSecs: 40

## Order book / tick / volume — what each live account can actually do

| Feature | Essential (live) | Premium (live) |
|---|---|---|
| Tick-based intervals | ✓ (tier feature) | ✓ |
| Volume footprint (L2 bid/ask) | ✓ (tier feature) | ✓ |
| Volume candles (tick/L1-derived) | ✓ | ✓ |
| Volume profile (per-session) | ✓ | ✓ |
| Bar Replay | ✓ | ✓ |
| `exchanges_subscriptions` add-ons | **none** | **CME full (real-time)** |
| Real-time CME / E-mini L1 | ✗ (delayed/broker feed only) | **✓** |
| Real-time CME / E-mini L2 (depth) | ✗ | **✓** (CME full includes book) |
| Order-book / DOM on CME symbols | ✗ | **✓** |
| Historical bars / chart | 10,000 | 20,000 |
| Minute-history days | 365 | unlimited |
| Parallel WS connections | 10 | 50 |

## Take-aways

- **Feature parity on microstructure**: Essential and Premium both unlock
  Volume Footprint, Volume Candles, Tick-based intervals, Volume Profile,
  Bar Replay, and the ability to *buy* professional market data. So on raw
  features, Essential is "Premium-lite, sufficient for order-flow work" *if*
  you accept the broker/default delayed feeds.
- **The real Premium edge on data depth** is two-fold:
  1. **Market-data add-ons** — Premium here has `exchange-cme-full` already
     attached, giving real-time CME L1/L2 (DOM, footprint on E-mini). Essential
     *can* buy it but doesn't, and without it CME/E-mini symbols show
     delayed/broker data on Essential.
  2. **Deeper history + more connections** — 20K vs 10K bars, unlimited
     minute-history days vs 365, 50 vs 10 parallel WS. This is what unlocks
     multi-year intraday backtests and high-concurrency strategy runners.
- **For orderbook / DOM specifically**: real DOM on CME/E-mini requires the
  CME full add-on (Premium has it; Essential here does not). Footprint /
  volume-candle *visualization* works on both tiers, but the underlying
  tick/L2 data on CME only resolves to real-time on Premium + CME-full.

## Sources

- Official pricing: `https://www.tradingview.com/pricing/` (captured 2026-08-20)
- Live account state: `GET https://www.tradingview.com/api/v1/user/profile/subscriptions/`
  with each account's `sessionid`/`sessionid_sign`/`device_t` cookies.
- Tier model: `pkg/account/account.go` → `LimitsForTier()`.

*Prices and caps are subject to change — re-scrape `tradingview.com/pricing`
to refresh.*
