---
name: tvcli
description: >
  TradingView Pine Script market analysis toolkit — compile, run, and extract
  structured signals from any Pine Script indicator on live market data via
  the Go tvcli binary. Includes 18 built-in indicator skills (SMC, EMA stack,
  SuperTrend, RSI, Squeeze Momentum, Ichimoku, Volume Profile, CVD, Camarilla,
  Choppiness, and a consolidated XAUUSD Scalping Confluence Engine), an async
  HTTP server for agent integration, and progressive reference docs for Pine
  Script v5 development. Use when analyzing XAUUSD or any TradingView symbol
  for market structure, signals, support/resistance, order flow, or trend
  detection.
license: MIT
compatibility: Requires Go 1.22+ (to build), TradingView account cookies in .env (SESSION, SIGNATURE, TV_USER, DEVICE_T, TV_TIER)
metadata:
  author: ch99q
  version: "1.0"
  binary: tvcli
  skills: "smc,dvi,liq-sweep,sr-breaks,gold-divergence,xau-trend,vp,swingarm,golden,sniper,ust,quantum,squeeze,ichimoku,camarilla,cvd,choppiness,xau-scalp"
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

## Built-in Skills (18)

All skills output `--json --agent` for agent-ready v2 envelopes with market
data, structure, opportunities, narrative, and conformance scoring.

| Skill | Category | Description |
|-------|----------|-------------|
| `xau-scalp` | consolidated | All-in-one EMA+ST+RSI+Squeeze+BB+Volume composite signal (~4s, replaces 17 separate runs) |
| `smc` | smc | Smart Money Concepts — BOS/CHoCH, FVG, Order Blocks |
| `dvi` | volume | Delta Volume Intensity — trend, S/R, momentum |
| `liq-sweep` | smc | Institutional Liquidity Sweep & Volume Breakout |
| `sr-breaks` | levels | Support/Resistance Breaks — pivot-based |
| `gold-divergence` | divergence | Gold RSI divergence — bullish/bearish |
| `xau-trend` | trend | XAUUSD EMA + Bollinger structure |
| `vp` | volume | Volume Profile Zones — POC, VAH, VAL |
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

## Free Tier Limits

- **2 indicators per chart** (use `xau-scalp` consolidated script to avoid this)
- **180 bars** (auto-capped by CLI)
- **20s calc timeout** (consolidated script runs in ~4s)

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

2. **Private scripts**: `USER;` scripts need `--allow-private` and must be
   pushed via `tvcli push` before the skill/run path works.

3. **`var` + pivots**: Scripts using `var` + `ta.pivothigh/lows` may return
   0 periods in headless mode. Remove these constructs if you get 0 periods.

4. **Consolidation**: Combining indicators into one script gives ~15× speedup
   and avoids the 2-indicator limit. See `assets/xau-scalp.pine`.

## HTTP Server Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Status + auth + tier info |
| GET | `/check-auth` | Auth cookie validation |
| POST | `/compile` | Compile Pine source |
| POST | `/fetch` | Fetch OHLCV data |
| POST | `/run` | Compile + run Pine source |
| POST | `/clean` | Clean chart sessions |

## Validation

Non-destructive checks passed for this skill documentation:

```bash
# Build from repo root
go build -o tvcli ./cmd/tvcli              # exit 0; matches the `./tvcli` examples above

# CLI help and skill registry
./tvcli --help                              # exit 0
./tvcli skills --json                       # exit 0; returns 18 registered skills
./tvcli serve --status                      # exit 0 (server stopped, no hang)
./tvcli serve --stop                        # exit 0 (server stopped, no hang)

# Per-skill help for every skill listed above
for s in smc dvi liq-sweep sr-breaks gold-divergence xau-trend vp \
         swingarm golden sniper ust quantum squeeze ichimoku \
         camarilla cvd choppiness xau-scalp; do
  ./tvcli "$s" --help > /dev/null 2>&1
  echo "$s: $?"
done
# All 18 exit 0
```

Notes:
- Skill commands are top-level commands: use `./tvcli smc ...`, not `tv smc ...`.
- The binary’s built-in help text shows `Usage: tv-cli ...` and `tv <skill>`. This is
  cosmetic/stale; the actual binary name and dispatcher accept `./tvcli <skill>`.
- `--allow-private` is accepted by skill commands but is omitted from skill-specific
  `--help` output; use it when running the private `xau-scalp` skill.
