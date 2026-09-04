---
name: tv-usecases
description: >
  Discover, develop, and make available for agentic usage all major TradingView
  strategy and indicator use cases — from programmatic script search and
  classification (strategy vs indicator), through headless backtesting and
  signal extraction, to live-chart parameter sweeps and visual representation.
  Bridges the local bdg `tv` command group
  (/Volumes/ExMac/code/tradingview/minimal-mjs) for network-API + chart-frontend
  investigation with the Go tvcli client
  (/Volumes/ExMac/code/tradingview/go) for headless Pine execution and analysis.
  Use when building or extending agentic TradingView workflows that discover,
  classify, run, sweep, and visualize any Pine script. Complements the tvcli,
  pine2tool, tv-scout, and tv-network skills.
license: MIT
metadata:
  author: dsh-agent
  version: "1.0"
---

# tv-usecases — TradingView Strategy & Indicator Use Case Catalog

This skill is the orchestration layer that ties together every tool in the
TradingView workspace for **agentic discovery, execution, and visualization**
of Pine scripts. It catalogs the major use cases, maps each to the concrete
commands that implement it, and provides the programmatic invocation path
through the DSH TV Network Investigator agent.

## The two-toolbridge

| Tool | Location | Role |
|------|----------|------|
| **bdg `tv` group** | `/Volumes/ExMac/code/tradingview/minimal-mjs/bdg/dist/index.js` | Live-chart investigation: capture WS protocol, read chart model, add/remove studies, change symbol/timeframe, read graphics, screenshot |
| **Go tvcli** | `/Volumes/ExMac/code/tradingview/go/tvcli` | Headless execution: compile, run, extract signals, analyze graphics, backtest strategies, parameter sweeps, HTTP server |

The bdg tool talks to a **running headful browser** (CloakBrowser + CDP); the
tvcli tool talks to TradingView's **WebSocket + HTTP APIs** directly (no
browser needed). Both drive the same `create_study` wire protocol — bdg through
the browser's chart widget, tvcli through a minimal Go WebSocket client.

**Build before use:**
```bash
# bdg (minimal-mjs)
cd /Volumes/ExMac/code/tradingview/minimal-mjs/bdg && npm install && npm run build

# tvcli (go)
cd /Volumes/ExMac/code/tradingview/go && go build -o tvcli ./cmd/tvcli
```

## Major use cases

### 1. Discover strategies and indicators by kind (`tv scan`)

**Problem:** TradingView has thousands of public Pine scripts. An agent needs
to find strategies (for backtesting) vs indicators (for signal extraction) and
build a corpus for robust sweeps.

**Commands (tvcli):**
```bash
# Search and classify — search API's extra.kind (fast)
tv scan "RSI" --type strategy --limit 10
tv scan "RSI,MACD,EMA" --type indicator --verify --limit 15

# Verify classification against authoritative metaInfo
tv scan "supertrend" --type strategy --verify --verify-max 25 --json

# Feed results into sweeps
tv scan "strategy" --type strategy --verify --limit 25 --json --out corpus.json
```

**Classification from two sources:**
1. Search API `extra.kind` — TradingView's own label (`"strategy"` vs
   `"study"`=indicator), fast, no extra fetches
2. `--verify` — fetches each script's metaInfo via pine-facade `/translate/`
   and cross-checks the **authoritative** `pine.isStrategy` flag + input/plot
   counts, **flagging any mismatch**

**Implementation:** `internal/cmd/scan.go` (scan command with classify + verify)

### 2. Run any script headlessly with custom inputs (`tv run` / `tv eval`)

**Problem:** An agent needs to execute a Pine script with specific input
values and get structured output — signals, graphics, backtest results —
without a browser.

**Commands (tvcli):**
```bash
# Run a published script by Pine ID (headless WS)
tv run "PUB;6daafb2cabe6419d98ae25229d2327f8" \
  --symbol OANDA:XAUUSD --tf 1H --bars 180 --signals --json

# Run with custom input overrides
tv run "PUB;454" --input.in_0=7 --symbol BINANCE:BTCUSDT --tf 15m --json

# Run arbitrary Pine source (no pre-published ID needed)
tv eval xau-scalp.pine --signals --agent --json

# Parameter sweep (multiple input values)
tv run "PUB;rcRbtoBiKGtjZq96XWjZtnEs2k2dBWaj" \
  --input.in_0=5,10,15 --symbol OANDA:XAUUSD --tf 1h --signals --json
```

**Output shapes:**
- `--signals` → extracted events (buy/sell), levels (S/R), graphic counts
- `--raw` / `--raw-out` → full primitives (lines, boxes, labels with coordinates)
- `--agent` → agent-ready v2 JSON envelope (Market, Structure, Opportunities, Narrative)
- Strategy scripts → backtest report (Net PnL, Profit Factor, win rate, trade count)

### 3. Universal script analysis (`tv analyze`)

**Problem:** An agent gets an unknown Pine script and needs to understand its
inputs, outputs, and analysis value without reading Pine source.

**Commands (tvcli):**
```bash
# Analyze any public script
tv analyze "PUB;3b3ebba156574e058cc8ae73dc5c7fa2" \
  --symbol OANDA:XAUUSD --tf 1H --json

# List available inputs from schema
tv analyze "PUB;454" --list-inputs --json

# With custom inputs + report
tv analyze "PUB;6daafb2cabe6419d98ae25229d2327f8" \
  --input.in_15=30 --report --format markdown --out analysis.md
```

**What the analyzer does:**
- Fetches metaInfo (inputs, plots, styles)
- Runs the script via WS with optional input overrides
- Extracts signals using universal topology-based graphics analysis (no per-script matchers)
- Groups boxes by geometry (volume-profile stacks, FVG/gaps, order-block zones)
- Groups lines by geometry (vertical markers, horizontal S/R, sloped trendlines)
- Associates boxes with bounding lines for semantic classification

### 4. Live-chart study management (`bdg tv study`)

**Problem:** An agent needs to add, modify, and read studies on the live chart
with custom input values — the visual counterpart to headless runs.

**Commands (bdg):**
```bash
BDG="node /Volumes/ExMac/code/tradingview/minimal-mjs/bdg/dist/index.js"

# List studies on the chart
$BDG tv studies

# Add ANY script with custom inputs (indicators + strategies + event overlays)
$BDG tv study add "RSI" --inputs '{"length": 21}'
$BDG tv study add "SMC" --pine "PUB;6daafb2cabe6419d98ae25229d2327f8" \
  --inputs '{"in_15": 30}'

# Read a study's inputs (authoritative in-page in_N map)
$BDG tv study inputs <entityId>

# Read computed plot values
$BDG tv study values -f RSI

# Read Pine graphics (lines, labels, boxes with coordinates)
$BDG tv study graphics -f SMC

# Remove a study (free tier: max 2 user studies)
$BDG tv study remove <entityId>
```

### Verified fix: pineVersion must be "last" (not "1.0")

**Problem:** `bdg tv study add --pine <id>` silently failed — the study was not
added and no error was returned. The browser console showed:
```
Chart.Studies.StudyInserter:Cannot get study {"type":"pine","pineId":"PUB;131","pineVersion":"1.0"}
```

**Root cause:** The `createStudy` pine descriptor used `pineVersion: "1.0"`,
but TradingView's StudyInserter expects `pineVersion: "last"` to resolve the
latest version. With `"1.0"`, the study metaInfo fetch fails silently.

**Fix:** Changed `pineVersion` from `"1.0"` to `"last"` in
`bdg/src/commands/tv/scripts.ts` (`buildStudyAddScript`). Also wrapped the
`createStudy` promise in `Promise.resolve().then()` so the entity id from the
resolved promise is used directly (instead of only polling for dataSource
changes, which could miss slow registrations).

**Verified live:** RSI Chart Bars (`PUB;131`) added successfully with entity
id returned from the promise, inputs applied, and screenshots captured at
each parameter value (7, 14, 21, 28).

### 5. Live-chart parameter sweeps (`tv study set` + `tv study report`)

**Problem:** An agent needs to sweep a strategy's input parameters on the live
chart and read the backtest results for each configuration.

**Commands (tvcli + bdg):**
```bash
# List studies to find the entity ID
tv study list

# Sweep: change input → read backtest report
tv study set <entityId> --inputs '{"in_0": 7}' --before a.png --after b.png
tv study report <entityId> --signals 10 --json

# Strategy backtest report fields:
#   Net PnL, Profit Factor, Max Drawdown, win rate, trade count
#   + buy/sell trade list (entry price, exit price, profit)
```

**Verified sweep pattern (XAUUSD 2h, RSI Strategy):**
```
in_0=5  → -1643 / 0.75 PF / 220 trades
in_0=9  → +99 / 1.03 PF / 82 trades
in_0=14 → +299 / 1.14 PF / 40 trades (sweet spot)
in_0=21 → -519 / 0.75 PF / 12 trades
```

### Verified sweep workflow (RSI Chart Bars, XAUUSD 1H)

**Full loop: scan → add with custom inputs → sweep → screenshot → analyze → pine2tool**

```bash
# 1. Scan for indicators that work on free tier
tv scan "RSI" --type indicator --verify --limit 8 --json

# 2. Add indicator with custom inputs and screenshot (visual command)
tv visual "RSI Chart Bars" --pine "PUB;131"   --inputs '{"in_0": 7}' --keep --out rsi-07.png

# 3. Sweep input values with live screenshots
tv study set <entityId> --inputs '{"in_0": 14}' --after rsi-14.png
tv study set <entityId> --inputs '{"in_0": 21}' --after rsi-21.png
tv study set <entityId> --inputs '{"in_0": 28}' --after rsi-28.png

# 4. Read current input values at any point
tv study inputs <entityId> --json

# 5. Run headless analysis with same inputs
tv analyze "PUB;131" --symbol OANDA:XAUUSD --tf 1H --input.in_0=14 --json

# 6. Generate reusable skill from the indicator
./.agents/skills/pine2tool/bin/pine2tool.sh "PUB;131"   --symbol OANDA:XAUUSD --tf 1H --input "in_0=14" --out skill_work/p2t-rsi-chart-bars
```

**Verified results (RSI Chart Bars, PUB;131, XAUUSD 1H, 2026-08-22):**

| in_0 (Length) | Signal changes (50 bars) | Screenshot |
|---------------|------------------------|------------|
| 7             | 3                      | ✓ 113KB    |
| 14 (default)  | 6                      | ✓ 91KB     |
| 21            | 2                      | ✓ 93KB     |
| 28            | 4                      | ✓ 93KB     |

The indicator colors chart bars green (RSI > UpLevel) or red (RSI < DownLevel).
Shorter length = more responsive (more color changes); longer = smoother (fewer).

**Generated artifacts:** `skill_work/tv-usecases/p2t-rsi-chart-bars/` contains:
- `rsi-chart-bars.json` — agent-ready analysis envelope
- `rsi-chart-bars.inputs.json` — canonical input IDs/types/defaults
- `rsi-chart-bars.source.pine` — downloaded Pine source
- `rsi-chart-bars.SKILL.md` — reusable skill doc
- `rsi-chart-bars.skill.yaml` — registrable skill definition

### 6. Change symbol and timeframe programmatically (`tv sym` / `tv tf`)

**Problem:** An agent needs to test a strategy across multiple symbols and
timeframes without manual UI interaction.

**Commands (tvcli):**
```bash
# Change symbol (auto-exchange: BTCUSDT → BINANCE:BTCUSDT)
tv sym OANDA:XAUUSD
tv sym BINANCE:BTCUSDT --out shot.png  # also screenshot

# Change timeframe
tv tf 15m
tv tf 1h
tv tf 4h
tv tf 1D
```

**Implementation:** Pure WebSocket — `modify_series` message. No XHR for the
change itself. The HIGH path (`window._exposed_chartWidgetCollection
.setSymbol()`) updates toolbar, legend, and document title.

### 7. Visual representation on the chart (`tv visual` / `tv screenshot`)

**Problem:** An agent needs to show its analysis on the chart — add a study
with custom inputs, wait for graphics to render, and capture a screenshot.

**Commands (tvcli):**
```bash
# One-command workflow: add study → settle → screenshot → remove
tv visual "SMC" --pine "PUB;6daafb2cabe6419d98ae25229d2327f8" \
  --inputs '{"in_15": 30}' --out shot.png

# Keep the study on the chart
tv visual "RSI" --inputs '{"length": 21}' --out rsi.png --keep

# Capture current chart state
tv screenshot chart.png
tv screenshot --full fullpage.png
tv screenshot --selector ".chart-container" element.png
```

### 8. Capture the WebSocket protocol (`bdg tv ws`)

**Problem:** An agent needs to understand TradingView's private chart-data
WebSocket protocol to debug the Go client or build new network tools.

**Commands (bdg):**
```bash
BDG="node /Volumes/ExMac/code/tradingview/minimal-mjs/bdg/dist/index.js"

# Capture handshake + OUT/IN flow summary
$BDG tv ws -s 8

# Raw frame payloads (deep inspect)
$BDG tv ws --full -s 10 -j

# Passive WS connections (no reload)
$BDG network websockets -j
```

**Protocol messages:** `~m~<len>~m~<json>` frames:
- `set_auth_token` — authentication
- `chart_create_session` — chart session
- `resolve_symbol` — symbol resolution
- `create_study` — add indicator/strategy
- `modify_study` — change inputs
- `modify_series` — change symbol/timeframe
- `du` / `timescale_update` — data updates (same message)

### 9. Read the live chart model (`bdg tv chart` / `bdg tv studies` / `bdg tv drawings`)

**Problem:** An agent needs to read what's on the chart — symbols, studies,
drawings, data sources — without parsing the DOM.

**Commands (bdg):**
```bash
$BDG tv chart      # URL, chartId, main series, dataSources
$BDG tv studies    # studies: pine/event/other + pineIds
$BDG tv drawings   # drawings + drawing-layer capabilities
```

### 10. Turn any Pine script into a reusable tool (`pine2tool`)

**Problem:** An agent finds a useful Pine script and wants to make it a
first-class, repeatable analysis tool with structured output.

**Commands (tvcli + pine2tool skill):**
```bash
# From a Pine ID — download source, list inputs, run+analyze, emit skill
./.agents/skills/pine2tool/bin/pine2tool.sh \
  "PUB;3b3ebba156574e058cc8ae73dc5c7fa2" \
  --symbol BINANCE:BTCUSDT --tf 1H \
  --input "in_1=4,in_0=3" \
  --out skill_work/p2t_vpob

# From a local .pine source file
./.agents/skills/pine2tool/bin/pine2tool.sh \
  .tv-scripts/35--smc-visuals.pine \
  --symbol BINANCE:BTCUSDT --tf 1H --out skill_work/p2t_smc
```

**Output:** Agent-ready JSON envelope + reusable skill stub + skill.yaml for
registration as a first-class `tvcli <name>` command.

### 11. Consolidated multi-indicator engine (`tv xau-scalp`)

**Problem:** Free tier allows only 2 studies per chart. Running multiple
indicators separately is slow (60s for 17 skills vs 4s consolidated).

**Commands (tvcli):**
```bash
# Run the consolidated XAUUSD scalping engine (all-in-one, ~4s)
tv xau-scalp --symbol OANDA:XAUUSD --tf 1H --bars 180 --json --agent --allow-private

# The consolidated engine combines: EMA stack, SuperTrend, RSI, Squeeze
# Momentum, Bollinger Bands, Volume Delta, and a weighted composite signal
# into one script with 14 named plots
```

### 12. HTTP server for agent integration (`tv serve`)

**Problem:** An agent (or external service) needs to call tvcli over HTTP
instead of spawning processes.

**Commands (tvcli):**
```bash
# Start the HTTP server in background
tv serve --daemon

# Endpoints
curl http://localhost:8765/health
curl -X POST http://localhost:8765/compile -d '{"source":"..."}'
curl -X POST http://localhost:8765/run -d '{"pineId":"PUB;...","symbol":"OANDA:XAUUSD","tf":"1H"}'
curl http://localhost:8765/fetch?symbol=OANDA:XAUUSD&tf=5m&bars=180
```

## The full discovery-to-visualization loop

```
                    ┌──────────────────┐
                    │   tv scan        │  Discover scripts by kind
                    │   (strategy/     │  (search API + verify via metaInfo)
                    │    indicator)    │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │  tv run / eval   │  Headless execution
                    │  --signals --json│  (WS + HTTP, no browser)
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
    ┌─────────▼────┐  ┌──────▼──────┐  ┌────▼──────────┐
    │ tv analyze   │  │ tv study   │  │ tv visual     │
    │ (universal   │  │ set/report │  │ (add +        │
    │  graphics)   │  │ (live      │  │  screenshot)  │
    └──────────────┘  │  sweeps)  │  └───────────────┘
                      └───────────┘
                             │
                    ┌────────▼─────────┐
                    │  pine2tool       │  Register as reusable
                    │  (skill stub)    │  first-class command
                    └──────────────────┘
```

## Invoking the DSH TV Network Investigator agent

### Headless one-shot invocation

```bash
# Using the tv-investigator DSH profile (composes agent-presets over headless)
dsh --profile tv-investigator "Load the tv-usecases skill. Discover and run all major RSI strategy variants on XAUUSD 1h, then report the best-performing parameters."

# With explicit working directory
cd /Volumes/ExMac/code/tradingview/go && \
  dsh --profile tv-investigator "Load the tv-usecases skill. Scan for Supertrend strategies, verify them, run headless backtests on BTCUSDT 4h."

# Using the wrapper script
./.agents/skills/tv-usecases/bin/tv-investigator.sh \
  "Scan for MACD strategies, run parameter sweeps on XAUUSD 1h, report results as JSON"
```

### What the agent gets

The headless DSH agent runs with:
- **bash** — run tvcli and bdg commands
- **fs** + **fs-search** — read/write/search project files
- **skill** — load workspace skills (tvcli, pine2tool, tv-scout, tv-usecases from go repo; tv-network from minimal-mjs)
- **web_search** — search the web for TradingView documentation
- **subagent** / **workflow** — delegate parallel investigation tasks
- **goal** / **todo** — track long-running investigations across rounds
- **plan mode** — design before executing

### The tv-investigator preset (web UI)

The DSH web UI (`dsh --profile web` or `dsh web`) supports agent presets. The
`tv-investigator` preset is at `~/.dsh/.agent-presets/tv-investigator/` and
composes the full TradingView investigation tool surface:

- **Persona:** "You are TV Network Investigator, a TradingView network-API +
  chart-frontend coding agent."
- **Tools:** bash/pwsh, fs, fs-search, jobs, skills, goal, plan-mode,
  compaction, delegation (subagent + workflow + ralph), ask-user, todo, web
- **Skills:** tv-network (from minimal-mjs), tvcli + pine2tool + tv-scout
  (from go repo), tv-usecases (this skill)

Select the preset from the web UI's agent-preset picker (General settings).

## Extending the system

### Adding a new use case

1. **Discover** the capability via `bdg tv ws` (protocol) or `bdg tv studies`
   (frontend model)
2. **Implement** in tvcli (`internal/cmd/<name>.go`) or bdg
   (`src/commands/tv/scripts.ts`)
3. **Document** in this skill and the `tv-network` skill
4. **Register** the skill in `.agents/skills/` with a SKILL.md
5. **Test** headlessly: `dsh --profile tv-investigator "Load the tv-usecases skill. Test <command>..."`

### Extending bdg (minimal-mjs)

In-page probes: `src/commands/tv/scripts.ts` (plain ES5 IIFEs, no
template-literal backticks inside; JSON-serializable return values).
Commands: `src/commands/tv/{ws,studies,study,drawings,chart}.ts`.
Formatters: `src/ui/formatters/tv.ts`.
Rebuild: `npm run build` in the bdg dir.

### Extending tvcli (go)

Commands: `internal/cmd/<name>.go` (implements `cli.Command`).
Register: `internal/cmd/shared.go` (`RegisterAll`).
Help: `internal/cmd/help.go`.
Build: `go build -o tvcli ./cmd/tvcli`.

## Reference: complete command catalog

### tvcli commands (Go, headless)
| Command | Purpose |
|---------|---------|
| `scan` | Search + classify scripts as strategy/indicator (with --verify) |
| `run` | Run a published script by Pine ID (WS, --signals, --raw, --agent) |
| `eval` | Run arbitrary Pine source (--compile-only, --signals, --agent) |
| `analyze` | Universal script analyzer (any Pine ID, --input.key, --report) |
| `inputs` | Inspect Pine inputs (Pine-actual vs Go-declared) |
| `input-map` | Show input ID mapping (Go vs Browser in_N offset) |
| `search` | Search public scripts |
| `top` | Fetch top public scripts |
| `list` / `publist` | List tracked/public scripts |
| `create` / `push` / `pull` / `delete` | Pine CRUD |
| `compile` | Compile a Pine script |
| `fetch` / `sync` | Fetch OHLCV data |
| `clean` | Clean chart sessions (free slots) |
| `check-auth` | Verify auth cookies + tier |
| `layouts` | List saved chart layouts |
| `serve` | HTTP server for agent integration |
| `screenshot` | Chart screenshot via bdg CDP |
| `visual` | Add study + screenshot (one command) |
| `tf` | Change timeframe programmatically |
| `sym` | Change symbol programmatically |
| `study` | Live-chart study management (list/inputs/report/set) |
| `skills` | List registered indicator skills |
| 20 indicator skills | smc, dvi, liq-sweep, sr-breaks, golden, sniper, ust, quantum, squeeze, ichimoku, camarilla, cvd, choppiness, xau-scalp, mtf-confluence, ... |

### bdg `tv` commands (Node, live chart)
| Command | Purpose |
|---------|---------|
| `tv ws` | Capture WebSocket protocol (--full, -s N) |
| `tv chart` | Read chart model (URL, chartId, main series, dataSources) |
| `tv studies` | List studies (pine/event/other + pineIds) |
| `tv study add` | Add any script with custom inputs (--pine, --inputs) |
| `tv study inputs` | Read study inputs (authoritative in_N map) |
| `tv study values` | Read computed plot values |
| `tv study graphics` | Read Pine graphics (lines/labels/boxes) |
| `tv study remove` | Remove a study |
| `tv drawings` | Read drawings + layer capabilities |
| `network websockets` | Passive WS connections (-j) |

## Rules

- Never print `sessionid`/`sessionid_sign`/`device_t`/auth_token values.
- Free tier: ≤2 user studies per chart; remove before adding.
- Don't hardcode `@library-build` numbers — capture them live.
- The bdg daemon must be attached to the TradingView tab.
- Build both tools before use (bdg: `npm run build`; tvcli: `go build`).
- Pine v5 requires `ta.` prefix for all built-in functions.
- `var` keyword and pivot functions can cause 0 period data on some scripts.
- Consolidating multiple indicators into one script yields ~15× speedup
  and avoids the free-tier 2-indicator limit.
- Input IDs are offset in-page and differ between clients (go vs browser).
  Use `tvcli input-map` before cross-client input overrides.
- Strategies produce backtest reports (not plots); read them with
  `tv study report` (live) or `tv run --signals` (headless).
