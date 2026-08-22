# TradingView Subscription Tier Limits

Current resource caps for each TradingView subscription plan, and how the `tvcli`
CLI models them.

> **Source:** scraped live from [tradingview.com/pricing](https://www.tradingview.com/pricing/)
> (annual billing, current). The CLI-side mapping lives in `internal/config/tiers.go`
> and is read from the `TV_TIER` env var.

## Hard limits modeled by `tvcli`

`internal/config/tiers.go` defines a `TierLimits` struct and a per-tier table:

```go
type TierLimits struct {
	MaxCharts       int // charts per tab
	MaxIndicators   int // indicators per chart
	MaxConnections  int // simultaneous WebSocket connections
	MaxBars         int // historical bars (minute); 0 = unlimited
	CalcTimeoutSecs int // calculation time limit
}
```

| Tier | Charts/tab | Indicators/chart | WS connections | Minute bars | Calc timeout |
|------|-----------:|-----------------:|---------------:|------------:|-------------:|
| `free`      | 1  | 2  | 2  | 180 | 20s  |
| `essential` | 2  | 5  | 10 | 365 | 40s  |
| `plus`      | 4  | 10 | 20 | unlimited (`0`) | 40s |
| `premium`   | 8  | 25 | 50 | unlimited (`0`) | 40s |
| `ultimate`  | 16 | 50 | 200 | unlimited (`0`) | 100s |

> Note: the historical-bars column in the CLI model (`MaxBars`) is **minute-history
> days**, NOT the API "historical bars" column (10K/10K/20K/40K). See
> `DATA_LIMITS_ESSENTIAL_VS_PREMIUM.md` for the full data-depth matrix.

Set the tier in `.env`:

```env
TV_TIER=essential
```

The CLI uses these to **auto-cap requested bars** to the plan's allowance and to
**clean up study slots** so scripts don't hit per-chart indicator limits.

## Full current feature/limit matrix (from the official pricing page)

Annual billing prices shown (list price in parentheses).

| Plan | Price (annual) | Charts | Ind./chart | Connections | Calc timeout | Hist. bars* | Min. history | Price alerts | Watchlist alerts |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **Free** | $0 | 1 | 2 | 2 | **20s** | 5K | **180 days** | 3 | 0 |
| **Essential** | **$12.95/mo** ($15.49) | 2 | 5 | 10 | **40s** | **10K** | **365 days** | 20 | 0 |
| Plus | $29.95/mo ($35.99) | 4 | 10 | 20 | 40s | **10K** | All | 100 | 0 |
| Premium | $59.95/mo ($71.99) | 8 | 25 | 50 | 40s | **20K** | All | 400 | 2 |
| Ultimate | $199.95/mo ($239.99) | 16 | 50 | 200 | **100s** | **40K** | All | 1,000 | 15 |

\* "Historical bars available" per chart — the API quote cap. See
`DATA_LIMITS_ESSENTIAL_VS_PREMIUM.md` for the live Essential-vs-Premium
comparison (including orderbook / DOM / tick / volume-footprint features).

\* "Historical bars available" — a distinct column from minute-history days.

### Key feature gates by plan

**Free** (`$0`):
- 400+ pre-built indicators only; **no** indicator-on-indicator, volume profile,
  volume footprint, volume candles, Bar Replay, custom timeframes, alerts that
  don't expire, or publishing invite-only scripts.
- Only **1 saved layout**, **1 indicator-on-indicator stack**, **2 charts**, **2
  connections**, **20s calc**, **180 days** of minute history.

**Essential** (the paid tier this repo is tested against, `TV_TIER=essential`):
- Adds **volume profile, custom timeframes, custom range bars, Bar Replay,
  indicator-on-indicator (up to 9 stacks), alerts that don't expire, publishing
  invite-only scripts**, multi-condition alerts, second/tick-based intervals,
  chart data export, and "indicators on indicators".
- **40s calc time** and **10 connections** — the key unlock for heavier Pine
  scripts (see the `request.security` MTF finding below).

**Plus / Premium / Ultimate** add more charts, indicators, connections, full
minute-history, deeper financials, deep backtesting, and higher-timeframe/alerts
allowances.

## Why the tier matters for `tvcli` Pine scripts

The practical difference observed live between free and essential:

| Capability | free | essential |
|---|---|---|
| Historical bars fetched | 180 | **365** |
| Calc timeout | 20s | **40s** |
| `request.security()` MTF dashboard | ❌ silent `PeriodCount:0` | ✅ **465 periods** |

Heavy Pine scripts (multi-timeframe via `request.security`, arrays, DMI) that
**silently return no data on free** run correctly on essential because of the
extra calc budget / connections. Always check `Meta.PeriodCount > 0` before
trusting a script's output.

## Notes / nuances

- Config `MaxBars` (180 / 365 / `0`=unlimited) is used to model **minute-history
  days**, not the API "historical bars" column (5K / 10K / 20K / 40K).
- Only the **Ultimate** plan is available to professional users for trading.
- Prices are annual-billing specials; monthly billing is higher.
- The `/health` server endpoint reports the active tier and user, e.g.
  `{"endpoint": "...", "status":"ok", "tier":"essential", "user":"rmuaq0394"}`.

*Prices and caps are subject to change — re-scrape `tradingview.com/pricing` to
refresh this reference.*
