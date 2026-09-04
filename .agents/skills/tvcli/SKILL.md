---
name: tvcli
description: >
  TradingView Pine Script market analysis toolkit — compile, run, and extract
  structured signals from any Pine Script indicator on live market data via
  the Go tvcli binary. Includes 20 built-in indicator skills (SMC, EMA stack,
  SuperTrend, RSI, Squeeze Momentum, Ichimoku, Volume Profile, CVD, Camarilla,
  Choppiness, MTF Confluence, and a consolidated XAUUSD Scalping Confluence Engine), an async
  HTTP server with a multi-account pool (~50 TradingView accounts, per-account
  concurrency + round-robin failover) and a `POST /hunt` batch endpoint for
  multi-symbol / multi-account sweeps, and progressive reference docs for Pine
  Script v5 development. Use when analyzing XAUUSD or any TradingView symbol
  for market structure, signals, support/resistance, order flow, or trend
  detection.
license: MIT
compatibility: Requires Go 1.22+ (to build), TradingView account cookies in .env (SESSION, SIGNATURE, TV_USER, DEVICE_T, TV_TIER)
metadata:
  author: ch99q
  version: "1.0"
  binary: tvcli
  skills: "smc,dvi,liq-sweep,sr-breaks,gold-divergence,xau-trend,vp,vp-pro,swingarm,golden,sniper,ust,quantum,squeeze,ichimoku,camarilla,cvd,choppiness,xau-scalp,mtf-confluence"
---

# tvcli — TradingView Pine Script Market Analysis Toolkit

A Go CLI that compiles, runs, and extracts structured output from TradingView
Pine Scripts via the WebSocket + HTTP APIs. Designed for AI agent market
analysis with JSON output, agent-ready envelopes, and an async HTTP server.

## Prerequisites

```bash
# Build the binary (from the repo/package root, wherever it is installed)
go build -o tvcli ./cmd/tvcli

# Configure auth (.env file)
SESSION=<sessionid cookie>
SIGNATURE=<sessionid_sign cookie>
TV_USER=<TradingView username>
DEVICE_T=<device_t cookie>
TV_TIER=free
```

## Quick Start

```bash
# Check auth (includes server state)
./tvcli check-auth --json

# Run a built-in skill (agent-ready JSON)
./tvcli smc --symbol OANDA:XAUUSD --tf 1H --bars 180 --json --agent

# Run the consolidated XAUUSD scalping engine (all-in-one, ~4s)
./tvcli xau-scalp --symbol OANDA:XAUUSD --tf 1H --bars 180 --json --agent --allow-private

# Start the HTTP server in background (non-blocking)
./tvcli serve --daemon
# Then: curl http://localhost:8765/health
```

## Built-in Skills (20)

All skills output `--json --agent` for agent-ready v2 envelopes with market
data, structure, opportunities, narrative, and conformance scoring.

| Skill | Category | Description |
|-------|----------|-------------|
| `xau-scalp` | other | All-in-one EMA+ST+RSI+Squeeze+BB+Volume composite signal (one run instead of 6 separate indicators; private `USER;` script — works only on accounts that own it) |
| `mtf-confluence` | trend | MTF Confluence Engine — chart TF + 2 higher-TF composites in one run (single study slot) |
| `smc` | smc | Smart Money Concepts — BOS/CHoCH, FVG, Order Blocks |
| `dvi` | volume | Delta Volume Intensity — trend, S/R, momentum |
| `liq-sweep` | smc | Institutional Liquidity Sweep & Volume Breakout |
| `sr-breaks` | levels | Support/Resistance Breaks — pivot-based |
| `gold-divergence` | divergence | Gold RSI divergence — bullish/bearish |
| `xau-trend` | trend | XAUUSD EMA + Bollinger structure |
| `vp` | volume | Volume Profile Zones — POC, VAH, VAL |
| `vp-pro` | volume | Volume Profile Pro — fixed-range POC/VAH/VAL (private; see xau-scalp caveat below) |
| `swingarm` | smc | SwingArm ATR Trend — trailing stop + Fibonacci |
| `golden` | other | Golden Rule — multi-TF weekly/daily/4H alignment |
| `sniper` | other | BS Buy & Sell Signals — multi-EMA confluence |
| `ust` | other | Ultra Sensitive SuperTrend — dual ST |
| `quantum` | other | EMA Ribbon — 8-layer alignment |
| `squeeze` | volatility | Squeeze Momentum [LazyBear] — volatility + momentum |
| `ichimoku` | trend | CM Enhanced Ichimoku Cloud V5 |
| `camarilla` | levels | Camarilla Pivot Points — 8 daily S/R levels |
| `cvd` | orderflow | Cumulative Delta Volume — order flow + divergence |
| `choppiness` | regime | Choppiness Index — trending vs choppy |

## Key Commands

```bash
# Auth + tier check (fail-fast, includes server state)
./tvcli check-auth --json

# Server management (async, non-blocking)
./tvcli serve --daemon         # Start in background
./tvcli serve --status         # Check health
./tvcli serve --stop            # Stop

# Data fetching (no indicator needed)
./tvcli fetch --symbol OANDA:XAUUSD --tf 5m --bars 180 --json-out data.json

# Run any Pine Script (eval = no pre-publish needed)
./tvcli eval script.pine --signals --agent --symbol OANDA:XAUUSD --tf 1H

# Turn any Pine script into a skill
./.agents/skills/pine2tool/bin/pine2tool.sh "PUB;abc123" --symbol OANDA:XAUUSD --tf 1H
```

## Free Tier Limits (per account)

- **2 indicators per chart, per account** — the cap is *per TradingView account*,
  not global. With the multi-account pool (~50 accounts) the engine analyzes
  **N×cap symbols in parallel** by fanning symbols across accounts (see
  `/hunt` below). The consolidated `xau-scalp` script avoids the cap on a single
  account; the pool avoids it across the fleet.
- **180 bars** (auto-capped by CLI)
- **20s calc timeout** (the consolidated script runs well under it in practice)

## Assets

- `assets/xau-scalp.pine` — Consolidated XAUUSD Scalping Confluence Engine
  (EMA stack, SuperTrend, RSI, Squeeze Momentum, Bollinger Bands, Volume
  Delta, composite signal — 14 named plots)

## References

Deep-dive documentation is in `references/`:

- `references/pinescript/README.md` — Progressive 13-layer Pine Script reference
- `references/pinescript/core/essentials.md` — Pine Script in 5 minutes
- `references/pinescript/core/language.md` — Language core
- `references/pinescript/api/websocket.md` — WebSocket runtime
- `references/pinescript/api/pine-facade.md` — Pine Facade HTTP API
- `references/pinescript/integration/go-tvcli.md` — Go tvcli integration
- `references/pinescript/runtime/execution.md` — Execution model + limits
- `references/pinescript/runtime/debugging.md` — Debugging guide
- `references/pinescript/patterns/common.md` — Common Pine patterns

## Critical Gotchas

1. **IL blob**: The `text` field in `create_study` is the compiled IL blob
   from Pine Facade, NOT raw Pine source. Passing raw source causes
   `line 1:12 no viable alternative at character '\n'`.

2. **Private scripts**: `USER;` skills (`xau-scalp`, `vp-pro`) are bound to the
   author's TradingView account — TradingView rejects accounts that don't own
   them. `--allow-private` only bypasses the CLI's local access gate. On your
   own account, run the local source instead:
   `./tvcli eval .agents/skills/tvcli/assets/xau-scalp.pine --signals --agent --symbol OANDA:XAUUSD --tf 1H`
   (or use `pine2tool` to register your own copy under a different ID).

3. **Non-`var` recursive series → silent 0 periods**: A series that references
   its own history and is declared WITHOUT `var` (e.g.
   `stDir = close > stUpper[1] ? ... : nz(stDir[1], 1)`) **compiles** clean via
   Pine Facade but the headless runner resolves its type as "undetermined",
   silently degrading the whole study to 0 fields / 0 periods (plots lost,
   ST_Dir stuck at +100, no short side). `compile` passing does NOT guarantee a
   runnable study. If a script that used to return periods suddenly gets 0,
   look for self-referential series and replace them with the `var`-state form
   (`var float stLine = na` / `var int stDir = 1`). `assets/xau-scalp.pine` is
   annotated with this guard at the SuperTrend block.

4. **`var` + pivots**: Scripts using `var` + `ta.pivothigh/lows` may return
   0 periods in headless mode. Remove these constructs if you get 0 periods.

5. **Consolidation**: Combining indicators into one script avoids the
   2-indicator limit and is much faster than running each indicator separately
   (one round-trip instead of many). See `assets/xau-scalp.pine`.

## HTTP Server Endpoints

All endpoints require the server to be running (`tvcli serve --daemon`), default
port `8765`. The server holds the authoritative **account pool** (~50 TradingView
accounts; primary + up to `TV_FAILOVER_MAX` failover accounts, default 4) with a
per-account concurrency semaphore (`MaxIndicators` concurrent studies per
account, free tier = 2). Endpoints fan work across that pool with round-robin
failover when an account hits a study/auth/connection limit.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Status + auth + tier info |
| GET | `/check-auth` | Auth cookie validation |
| GET | `/skills` | List registered indicator skills |
| GET | `/queue-stats` | Per-account slot/semaphore usage |
| POST | `/compile` | Compile Pine source |
| POST | `/fetch` | Fetch OHLCV data |
| POST | `/run` | Compile + run Pine source |
| POST | `/run-skill` | Run a registered skill on one symbol |
| POST | `/hunt` | **Batch** skill run over many symbols (multi-account) |
| POST | `/clean` | Clean chart sessions |

### Historical anchor (`to`) — point-in-time analysis

`/fetch`, `/run`, and `/run-skill` accept an optional `"to"` field (unix
seconds). The chart window **ends at that moment**: the last bar closes at
`to`, and studies compute over the anchored window — the market read at a
past timestamp with no lookahead (bar-replay semantics without the replay
session). CLI equivalent: `tvcli fetch --to <unix-seconds|RFC3339>`. Use it
for "what did the market look like when X happened" work: signal
verification, event studies, backtest context at a decision time.

### `POST /hunt` — multi-account / multi-symbol sweep

Fans a symbol list across the account pool and runs one skill per symbol in
parallel. N valid accounts analyze N symbols concurrently; each symbol
round-robins to the next account on a failover error (up to `TV_FAILOVER_MAX`
attempts). Worker count is auto-bounded by the pool's total slot capacity and
`concurrencyCap`.

Body:

```json
{
  "skill": "squeeze",
  "timeframe": "4H",
  "bars": 180,
  "symbols": ["BINANCE:BTCUSDT", "BINANCE:ETHUSDT"],
  "inputs": { "length": "20" },
  "maxAccounts": 0,
  "concurrencyCap": 0,
  "maxSymbolsPerAccount": 0
}
```

Response:

```json
{
  "status": "ok",
  "skill": "squeeze",
  "accountPool": 50,
  "accountsUsed": ["acc1", "acc2"],
  "completed": 118,
  "failed": 2,
  "total": 120,
  "elapsedMs": 9123,
  "symbols": {
    "BINANCE:BTCUSDT": { "ok": true, "account": "acc3", "result": { "market": {}, "narrative": {}, "opportunities": [], "conformance": {"agenticScore": 72.5} } },
    "BINANCE:XYZUSDT": { "ok": false, "error": "...", "account": "acc7" }
  }
}
```

Notes:

- Private skills (`pinefacade.AccessFromPineID == "private"`) with no local
  source are rejected — they only run on accounts that own them, so a pool-wide
  fan-out would mostly fail.
- Invalid symbols are reported as `failed`, not dropped.
- The QD `HeartbeatHunter` (`backend/app/services/autonomy/hunter.py`) is the
  reference client: `TvcliService.hunt()` → `POST /hunt`, then normalizes each
  `result` into `direction` / `veto` / `confluence score` and ranks candidate
  tokens. See `QuantDinger-src/backend/docs/tvcli-multiaccount-hunter.md`.

## Validation

Non-destructive checks passed for this skill documentation:

```bash
# Build from repo root
go build -o tvcli ./cmd/tvcli              # exit 0; matches the `./tvcli` examples above

# CLI help and skill registry
./tvcli --help                              # exit 0
./tvcli skills --json                       # exit 0; returns 20 registered skills
./tvcli serve --status                      # exit 0 (server stopped, no hang)
./tvcli serve --stop                        # exit 0 (server stopped, no hang)

# Per-skill help for every skill listed above
for s in smc dvi liq-sweep sr-breaks gold-divergence xau-trend vp vp-pro \
         swingarm golden sniper ust quantum squeeze ichimoku \
         camarilla cvd choppiness xau-scalp mtf-confluence; do
  ./tvcli "$s" --help > /dev/null 2>&1
  echo "$s: $?"
done
# All 20 exit 0
```

Notes:
- Skill commands are top-level commands: use `./tvcli smc ...`, not `tv smc ...`.
- The binary’s built-in help text shows `Usage: tv-cli ...` and `tv <skill>`. This is
  cosmetic/stale; the actual binary name and dispatcher accept `./tvcli <skill>`.
- `--allow-private` is accepted by skill commands but is omitted from skill-specific
  `--help` output; use it when running the private `xau-scalp` skill.
