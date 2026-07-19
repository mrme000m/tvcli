package cmd

import (
	"fmt"
	"io"
)

// helpText is the static CLI help shown by `tvcli help` and unknown-command
// fallback. Kept as a constant so it can be diffed/reviewed at a glance.
const helpText = `TradingView Pine Script Manager (Go)

Usage: tv-cli <command> [options]

Commands:
  list                          List all tracked scripts
    -r, --remote                 List remote saved scripts
    -p, --public                 List public TradingView scripts
  publist                       List public TradingView scripts
    --offset N                   Pagination offset (default: 0)
    --limit N                   Max results (default: 20)
    --json                      JSON output
  top                           Fetch top public scripts to JSON
    --limit N                   Number of scripts (default: 100)
    --output <file>             Output file (default: top_scripts.json)
  create <file.pine>            Create new remote script
    --name "Name"               Script name
  pull <id|pineId>              Pull remote script to local
  push <id|file>                Push local changes
    --force                     Push even if unchanged
  delete <id>                   Delete script
    --yes                       Confirm deletion
  compile <file.pine>           Compile script
  fetch                         Fetch raw OHLCV data (no indicator needed)
    --symbol EXCHANGE:SYMBOL     Market symbol (default: OANDA:XAUUSD)
    --tf 5m                     Timeframe (default: 5m)
    --bars 180                  Number of bars (free tier: 180)
    --dir <dir>                 Output directory (default: .)
    --json-out <file>           Custom JSON output path
    --csv-out <file>            Custom CSV output path
  sync                          Fetch + compress OHLCV to .json.gz (gap-fills existing)
    --symbol EXCHANGE:SYMBOL     Market symbol (default: OANDA:XAUUSD)
    --tf 5m                     Timeframe (default: 5m)
    --bars 5000                 Max bars to request
    --dir <dir>                 Output directory (default: .)
    --out <file>                Output file path (default: SYMBOL_tf.json.gz)
    --force                     Ignore existing file, re-fetch everything
    --loop <interval>           Keep syncing (e.g. 5m, 1h). Gap-fills each cycle.
  run <pineId>                  Run script with chart session
    --symbol EXCHANGE:SYMBOL     Market symbol (e.g., OANDA:XAUUSD, BINANCE:BTCUSDT)
    --tf 5m                     Timeframe
    --bars 500                  Number of bars
    --json                      JSON output
    --raw                       Dump raw unprocessed capture (periods + graphic + strategyReport)
    --raw-out <file>            Write raw dump to file (implies --raw)
    --out <file>                Save output to file
    --signals                   Emit script-agnostic extracted signals (JSON with --json, compact text default)
    --schema                    Show parsed metaInfo schema (plots, styles, palettes) without running
    --multi-run, --sweep        Generate input sweep configurations (shows what would be varied)
    --settle <ms>               Wait after first data update for follow-up graphics/backfill (default 1500)
    --force-cleanup             Aggressively retry when study limit hit (web UI indicators blocking)
    --persistent                Keep WS connection open across runs (no reconnect between runs)
    --loop <interval>           Re-run periodically (e.g. 30s, 5m, 1h). Implies --persistent.

  Symbol formats:
    Forex:    OANDA:XAUUSD, OANDA:EURUSD, FXCM:GBPUSD
    Crypto:   BINANCE:BTCUSDT, COINBASE:BTCUSD, BYBIT:ETHUSDT
    Stocks:   NASDAQ:AAPL, NYSE:TSLA, AMEX:SPY
    Auto:     XAUUSD → OANDA:XAUUSD, BTCUSDT → BINANCE:BTCUSDT
  search <query>                Search public scripts
    --limit N                   Max results (default: 20)
    --json                      JSON output

  skills                        List available indicator skills
    --json                      JSON output

  inputs <pineId|skillName>     Inspect Pine inputs (Pine-actual vs Go-declared)
    --json                      Structured JSON output
    --raw                       Raw Pine input list (id/name/type/defval/options)
    No skill name → Pine-only view; skill name → side-by-side diff with status:
      ok | type-mismatch | missing-in-go | go-only/phantom

  Indicator Skills (run with tv <skill>):
    tv bsv          Buy/Sell Volume analysis
    tv dvi          Delta Volume Intensity
    tv ust          Ultra Sensitive SuperTrend
    tv swingarm     SwingArm ATR Trend
    tv ema-atr      EMA + ATR Pro Engine
    tv sr-breaks    Support/Resistance Breaks
    tv shemar       SHEMAR HMA ST + SMC Confidence
    tv quantum      Quantum Ribbon Lite
    tv vgaps        Volume Gaps & Imbalances
    tv anchored-vp  Anchored Volume Profile
    tv mtf          XAUUSD MTF Trend Dashboard
    tv sniper       Precision Sniper
    tv smc          Smart Money Concepts
    tv golden       Golden Rule Strategy
    tv trend        Self-Aware Trend System
    tv ict          ICT Auto-Validated SMC
    tv liq-sweep    Institutional Liquidity Sweep & Volume Breakout
    tv order-flow   Volume Spike Order Flow
    tv gold-divergence  Gold RSI Divergence
    tv xau-trend    XAUUSD EMA + Bollinger Trend

    Use --help on any skill for details (e.g. tv sniper --help)
    Use --json --agent for agent-ready JSON output

Authentication:
  Extract SESSION and SIGNATURE cookies from your browser:
    1. Log in to tradingview.com
    2. Open DevTools → Application → Cookies
    3. Copy sessionid and sessionid_sign values
    4. Set in .env file (loaded automatically):

  SESSION=<sessionid cookie value>
  SIGNATURE=<sessionid_sign cookie value>
  TV_USER=<your TradingView username>

  Write operations (create/push/delete) require all three.
  Read operations (list/pull/search/compile) work with SESSION+SIGNATURE.
  run works with any auth (anonymous fallback available).

Subscription Tier (set TV_TIER to match your plan):
  TV_TIER=free       1 chart, 2 indicators, 2 connections, 180d bars, 20s calc
  TV_TIER=essential  2 charts, 5 indicators, 10 connections, 365d bars, 40s calc
  TV_TIER=plus       4 charts, 10 indicators, 20 connections, unlimited bars, 40s calc
  TV_TIER=premium    8 charts, 25 indicators, 50 connections, unlimited bars, 40s calc
  TV_TIER=ultimate   16 charts, 50 indicators, 200 connections, unlimited bars, 100s calc

  Default: free. The run command auto-cleans studies and caps bars to your tier.
`

// PrintHelp writes the CLI help text to w.
func PrintHelp(w io.Writer) {
	fmt.Fprint(w, helpText)
}
