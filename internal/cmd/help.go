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
    --bars N                    Number of most-recent bars (default: 180)
    --deep N                    Fetch N total bars by backfilling older history
    --all                       Fetch all available history (walks back via request_more_data)
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
  backtest <pineId>              Run a STRATEGY with custom inputs, extract backtest results
    --symbol EXCHANGE:SYMBOL     Market symbol (default: OANDA:XAUUSD)
    --tf 5m                     Timeframe (default: 5m)
    --bars 500                  Number of bars (calc window)
    --inputs '<json>'           Custom input overrides, e.g. '{"length": 20}'
    --json                      JSON output (metrics + full trade list)
    --out <file>                Write JSON to file
  confirm                        Apply each script to the live chart, screenshot, report
    --file builtin-indicators.json  Catalog of pine ids (default)
    --type indicator|strategy|all   Filter by kind
    --limit N                   Stop after N scripts
    --out DIR                   Screenshot + report directory (default: shots/)
    --settle MS                 Wait for Pine graphics (default: 2500)
    --keep                      Keep studies on chart (default: removed)
    --json                      Also print the JSON report to stdout

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

  eval <file.pine>              Run arbitrary Pine Script source (no pre-published pineId needed)
    --compile-only              Just validate syntax, don't run via WS
    --script 'source'          Pass source inline instead of a file
    --signals                   Extract trading signals from output
    --agent                     Output agent-ready JSON envelope
    --json                      JSON output
    --force-cleanup             Aggressively retry when study limit hit
  clean                         Clean chart sessions to free indicator slots
    --iterations N              Number of cleanup cycles (default 3)
    --delay Ms                  Delay between cycles in ms (default 500)
    --symbol S                  Symbol to use for chart sessions (default BINANCE:BTCUSDT)
  check-auth                    Verify TradingView auth cookies and subscription tier
    --json                      JSON output (for agent consumption)
    Diagnoses expired cookies — the #1 cause of silent "study limit" errors.
  account                       Manage multiple TradingView accounts (accounts.json)
    account list [--json]       List accounts (credentials masked)
    account show [name]         Show one account
    account add <name> --session <id> --signature <sig> --device-t <d> [--user --tier --role --proxy]
    account use <name>          Set the default account
    account remove <name>       Delete an account
    --account <name>            Global flag: run any command as that account
  layouts                       List/create/rename/delete saved chart layouts
    layouts list [--limit N] [--json]   List layouts (default)
    layouts show <name|chartSlug|id>    Show one layout
    layouts create <name> --symbol E:S --tf 15   Create a new empty layout (HTTP)
    layouts rename <newName>            Rename the current chart's layout (live browser)
    layouts delete <chartSlug|id> [...] Delete layouts (HTTP)
  serve [--addr :8765]          Start HTTP server for AI agent integration
    --daemon / -d    Start in background (non-blocking)
    --stop           Stop background server
    --status         Check server status + health
    Endpoints: /health /compile /fetch /clean /run
  inputs <pineId|skillName>     Inspect Pine inputs (Pine-actual vs Go-declared)
    --json                      Structured JSON output
    --raw                       Raw Pine input list (id/name/type/defval/options)
    No skill name → Pine-only view; skill name → side-by-side diff with status:
      ok | type-mismatch | missing-in-go | go-only/phantom
  input-map <pineId>            Show Pine input ID mapping: Go client vs Browser (resolves in_N offset)
    --browser-entity <id>       Study entity ID from bdg tv studies
    --json                      JSON output
  screenshot [output.png]       Capture chart screenshot via bdg CDP
    --out FILE                  Output file (default: tv-screenshot-TIMESTAMP.png)
    --full, --full-page         Full page capture (default: viewport)
    --selector CSS              Capture specific element
    --scroll SELECTOR           Scroll to element before capture
    --format png|jpeg           Output format (default: png)
    --quality N                 JPEG quality 1-100 (default: 90)
    --no-resize                 Disable auto-resize for large pages
  visual <name|pineId>          Add any Pine script to the LIVE chart with custom inputs, then screenshot it
    --pine ID                   Pine id (USER;/PUB;) — adds any saved/public script
    --inputs '<json>'           Custom input overrides, e.g. '{"in_15": 30, "length": 21}'
    --out FILE                  Screenshot output (default: tv-visual-TIMESTAMP.png)
    --settle MS                 Wait for graphics to render (default: 4000)
    --full, --full-page         Full page capture (default: viewport)
    --selector CSS              Capture a specific element
    --keep                      Keep the study on the chart after capture (default: removed)
    --verbose                   Show the underlying bdg commands
  tf <timeframe>                Change the live chart's timeframe programmatically (model setInterval via bdg)
    --verbose                   Show what is being called
    Timeframes: 1m..45m, 1h..12h, 1D, 1W, 1M (or plain minutes: 15, 60, 240)
  sym <symbol>                  Change the live chart's symbol programmatically (widget setSymbol via bdg)
    --out FILE                  Also screenshot the new symbol to FILE
    --full                      Full-page screenshot (with --out)
    --verbose                   Show what is being called
    Symbols: BTCUSDT, OANDA:XAUUSD, BINANCE:ETHUSDT, NASDAQ:AAPL, ...
  study <list|inputs|report|set>  List studies on the live chart, read/set inputs, read strategy backtests
    tv study list                          List studies (--json)
    tv study inputs <entityId>             Read a study's input values (--json)
    tv study report <entityId>             Read a STRATEGY's backtest report + buy/sell signals (--signals N, --json)
    tv study set <entityId> --inputs '<json>' [--before a.png] [--after b.png]
      Sends modify_study on the WS; server recomputes (du) and the pane redraws.
      For strategies, combine set + report for parameter sweeps.
  scan <query...>               Search public scripts classified strategy vs indicator (feed sweeps/input tests)
    --type strategy|indicator|any   Filter by kind (default: any)
    --limit N                       Max total results (default: 20)
    --per-query N                   Max results per query (default: 20)
    --verify                        Confirm kind via metaInfo pine.isStrategy + input/plot counts
    --verify-max N                  Max scripts to verify (default: 10)
    --json                          JSON output
    Examples: tv scan RSI --type strategy | tv scan "RSI,MACD" --type indicator --verify
  analyze <pineId>              Universal script analyzer - auto-analyze any Pine script
    --symbol EXCHANGE:SYMBOL     Market symbol (default: OANDA:XAUUSD)
    --tf 5m                     Timeframe (default: 5m)
    --bars 500                  Number of bars
    --input.key=VALUE           Input overrides (e.g., --input.length=20)
    --list-inputs               List available inputs from schema and exit
    --validate-inputs           Validate inputs against schema before running
    --settle 1500               Settle time in ms (default: 1500)
    --timeout 120               Timeout in seconds
    --force-schema              Re-fetch schema from TradingView
    --json                      Output full JSON
    --report                    Generate analysis report
    --format markdown|html|marketing|text  Report format (default: markdown)
    --title TITLE               Report title
    --out FILE                  Save output to file
    --verbose                   Verbose output

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
