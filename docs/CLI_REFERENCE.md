# CLI Reference

Complete reference for all `tvcli` commands.

---

## Global Usage

```
tvcli <command> [options] [positional args]
```

All commands accept `--json` for JSON output. Skill commands also accept `--agent` for agent-ready v2 envelopes.

---

## fetch

Fetch raw OHLCV candle data. No authentication required (uses public data feed).

```
tvcli fetch [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--symbol <SYM>` | `OANDA:XAUUSD` | Market symbol (e.g., `BINANCE:BTCUSDT`) |
| `--tf <TF>` | `5m` | Timeframe (`1m`, `5m`, `15m`, `1H`, `4H`, `1D`, `1W`) |
| `--bars <N>` | `180` | Number of bars (free tier capped at 180) |
| `--to <ts>` | — | Anchor the window at a past moment (unix seconds or RFC3339). The last bar **closes** at `to` — no lookahead |
| `--dir <dir>` | `.` | Output directory |
| `--json-out <file>` | auto | Custom JSON output path |
| `--csv-out <file>` | auto | Custom CSV output path |

**Examples:**
```bash
./tvcli fetch --symbol BINANCE:BTCUSDT --tf 1H --bars 100
./tvcli fetch --symbol OANDA:XAUUSD --tf 15m --json-out gold.json
# point-in-time: 150 M15 bars ending at 2026-08-28 14:30 UTC
./tvcli fetch --symbol OANDA:XAUUSD --tf 15 --bars 150 --to 2026-08-28T14:30:00Z
```

**Output:** CSV and JSON files with `{time, open, high, low, close, volume}` per bar.

---

## sync

Fetch OHLCV data and compress to `.json.gz`. Gap-fills existing files.

```
tvcli sync [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--symbol <SYM>` | `OANDA:XAUUSD` | Market symbol |
| `--tf <TF>` | `5m` | Timeframe |
| `--bars <N>` | `5000` | Max bars to request |
| `--dir <dir>` | `.` | Output directory |
| `--out <file>` | `SYMBOL_TF.json.gz` | Output file path |
| `--force` | false | Ignore existing file, re-fetch |
| `--loop <interval>` | — | Keep syncing (e.g., `5m`, `1h`) |

---

## analyze / universal

Universal script analyzer — auto-analyze any Pine script using the two-layer
generic graphics design. No per-script matchers needed.

```
tvcli analyze "PUB;<id>" [options]
tvcli analyze "PUB;<id>" --input in_0=20,in_1=5  # custom inputs
```

**Two-layer design:**
- Layer 1 (`pipeline/extract.go`): flat signal extraction from all draw types
- Layer 2 (`graphics_generic.go`): topology-based structural analysis (groups by shared edges, width, extension → POC/VAH/VAL, order blocks, FVGs, breaker blocks, liquidity levels)

| Flag | Default | Description |
|------|---------|-------------|
| `--symbol <SYM>` | `OANDA:XAUUSD` | Market symbol |
| `--tf <TF>` | `5m` | Timeframe |
| `--bars <N>` | `500` | Number of bars |
| `--input k=v` | — | Pine input override (comma list form) |
| `--input.k=v` | — | Dotted Pine input override |
| `--json` | false | JSON output |
| `--report` | false | Generate analysis report |
| `--list-inputs` | false | List script inputs and exit |
| `--validate-inputs` | false | Validate inputs against schema |
| `--force-schema` | false | Force re-fetch schema |
| `--out <file>` | — | Save output to file |
| `--verbose` | false | Verbose output |

## eval

Run arbitrary Pine Script source without a pre-published Pine ID. Compiles via Pine Facade, saves as a temporary script, runs on WebSocket, then deletes the temp script.

```
tvcli eval <file.pine> [options]
tvcli eval --script '//@version=5 ...' [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--compile-only` | false | Validate syntax only, don't run |
| `--script <src>` | — | Inline source (alternative to file arg) |
| `--signals` | false | Extract trading signals from output |
| `--agent` | false | Output agent-ready v2 JSON envelope |
| `--json` | false | JSON output (for signals) |
| `--symbol <SYM>` | `OANDA:XAUUSD` | Market symbol |
| `--tf <TF>` | `1H` | Timeframe |
| `--force-cleanup` | false | Aggressively retry on study limit |
| `--raw` | false | Dump raw unprocessed capture |
| `--raw-out <file>` | — | Write raw dump to file |
| `--out <file>` | — | Save output to file |
| `--settle <ms>` | `1500` | Wait after first update for graphics |
| `<key>=<value>` | — | Pine input override (positional, after the file path) |
| `--input key=value` | — | Pine input override, space or comma list form |
| `--input.k=v` | — | Dotted Pine input override (agent/universal form) |
| `--<in_N>=<v>` | — | Raw TV input ID override (e.g. `--in_1=4`) |

All input spellings are merged by `internal/cmd/inputs_util.go` and resolved to
the canonical TV input ID by ID, index, or name (`PineIndicator.SetOption`).

**Signal extraction fields:**
- `Meta`: pineId, symbol, timeframe, periodCount, timestamp
- `Classifications`: plot field → category (price, signal, metric, noise, style)
- `Last`: latest values for all plot fields
- `Series`: bar-by-bar data (last N bars)
- `Levels`: support/resistance/band/poc price levels with deduplication
- `Events`: buy/sell/alert/state events with timestamps
- `GraphicCounts`: box/line/label/table counts
- `Report`: full strategy report (21 metrics + per-trade data)
- `Bias`: directional bias (bullish/bearish/neutral)
- `Confidence`: 0.0-1.0 score

**Strategy report fields:**
- `NetProfit`, `NetProfitPercent`, `GrossProfit`, `GrossLoss`
- `WinRate`, `TotalTrades`, `WinningTrades`, `LosingTrades`
- `ProfitFactor`, `AvgTrade`, `LargestWin`, `LargestLoss`
- `MaxDrawdown`, `MaxDDPercent`, `CommissionPaid`
- `SharpeRatio`, `SortinoRatio`, `BuyHoldReturn`, `OpenPL`
- `Currency`
- `Trades`: per-trade array with `{Side, Entry, Price, Qty, Profit, ID}`

**Examples:**
```bash
./tvcli eval my_script.pine --compile-only
./tvcli eval my_script.pine --signals --json --symbol BINANCE:BTCUSDT --tf 1H
./tvcli eval my_script.pine --signals --agent --json --symbol BTCUSDT --tf 15m
./tvcli eval my_script.pine --signals --raw --symbol BTCUSDT --tf 5m length=20
```

---

## run

Run a pre-published Pine Script by its Pine ID.

```
tvcli run <pineId> [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--symbol <SYM>` | `OANDA:XAUUSD` | Market symbol |
| `--tf <TF>` | `5m` | Timeframe |
| `--bars <N>` | `500` | Number of bars (auto-capped to tier) |
| `--json` | false | JSON output |
| `--signals` | false | Extract trading signals |
| `--agent` | false | Agent-ready v2 envelope |
| `--raw` | false | Raw unprocessed capture |
| `--raw-out <file>` | — | Write raw dump to file |
| `--out <file>` | — | Save output to file |
| `--schema` | false | Show Pine metaInfo schema without running |
| `--settle <ms>` | `1500` | Wait after first update for graphics |
| `--force-cleanup` | false | Aggressively retry on study limit |
| `--persistent` | false | Keep WS connection across runs |
| `--loop <interval>` | — | Re-run periodically (implies --persistent) |
| `--multi-run` | false | Generate input sweep configs |
| `--input key=value` | — | Override Pine input (comma lists allowed: `--input "in_1=4,in_0=3"`) |
| `--input.k=v` | — | Dotted Pine input override |
| `--in_N=v` | — | Raw TV input ID override |
| `--preset <name>` | — | Use a skill preset |

Input overrides are resolved to canonical TV input IDs by ID, index, or name.

**Examples:**
```bash
./tvcli run "PUB;6daafb2cabe6419d98ae25229d2327f8" --signals --agent --json --symbol BTCUSDT --tf 1H
./tvcli run "PUB;ff1a0136336340f38e908eeb12ea33aa" --raw --raw-out dump.json
./tvcli run "PUB;6daafb2cabe6419d98ae25229d2327f8" --schema
```

---

## compile

Compile a Pine Script file (syntax validation only).

```
tvcli compile <file.pine>
```

---

## search

Search TradingView's public script library.

```
tvcli search <query> [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--limit <N>` | `20` | Max results |
| `--json` | false | JSON output |

---

## publist

List public TradingView scripts (paginated).

```
tvcli publist [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--offset <N>` | `0` | Pagination offset |
| `--limit <N>` | `20` | Max results |
| `--json` | false | JSON output |

---

## top

Fetch top public scripts to a JSON file.

```
tvcli top [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--limit <N>` | `100` | Number of scripts |
| `--output <file>` | `top_scripts.json` | Output file |

---

## create

Create a new remote Pine Script.

```
tvcli create <file.pine> --name "Script Name"
```

Requires `SESSION`, `SIGNATURE`, and `TV_USER`.

---

## push

Push local changes to a remote script.

```
tvcli push <id|pineId> <file.pine> [--force]
```

| Flag | Description |
|------|-------------|
| `--force` | Push even if unchanged |

---

## pull

Pull a remote script source to a local `.pine` file.

```
tvcli pull <id|pineId>
```

---

## delete

Delete a remote script.

```
tvcli delete <id|pineId> --yes
```

---

## list

List all tracked scripts (local metadata database).

```
tvcli list [options]
```

| Flag | Description |
|------|-------------|
| `-r, --remote` | List remote saved scripts instead of local |
| `-p, --public` | List public TradingView scripts |
| `--json` | JSON output |

---

## inputs

Inspect Pine script inputs: compare Pine-actual inputs vs Go-declared inputs.

```
tvcli inputs <pineId|skillName> [options]
```

| Flag | Description |
|------|-------------|
| `--json` | Structured JSON output |
| `--raw` | Raw Pine input list (id/name/type/defval/options) |

Without a skill name: Pine-only view. With a skill name: side-by-side diff showing `ok`, `type-mismatch`, `missing-in-go`, `go-only/phantom`.

---

## skills

List all registered indicator skills.

```
tvcli skills [--json]
```

Each skill reports: name, synopsis, Pine ID, category, tier, knownBroken status, inputs, and presets.

---

## clean

Clean chart sessions to free indicator slots (for free accounts).

```
tvcli clean [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--iterations <N>` | `3` | Number of cleanup cycles |
| `--delay <ms>` | `500` | Delay between cycles |
| `--symbol <SYM>` | `BINANCE:BTCUSDT` | Symbol for chart sessions |

Creates a chart session, removes all existing studies, deletes the session. Repeats N times.

---

## serve

Start an HTTP server for AI agent integration.

```
tvcli serve [--addr :8765]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--addr <addr>` | `:8765` | Listen address |

**Endpoints:**

| Endpoint | Method | Body | Purpose |
|----------|--------|------|---------|
| `/health` | GET | — | Status check |
| `/compile` | POST | `{"source":"..."}` | Compile Pine Script |
| `/fetch` | POST | `{"symbol":"...","tf":"...","bars":N,"to":unix?}` | Fetch OHLCV (optionally ending at a past moment) |
| `/clean` | POST | `{}` | Clean chart sessions |
| `/run` | POST | `{"source":"...","symbol":"...","tf":"...","to":unix?}` | Compile + run script |
| `/run-skill` | POST | `{"skill":"...","symbol":"...","tf":"...","to":unix?}` | Run a registered skill |

**Historical anchor (`to`):** `/fetch`, `/run`, and `/run-skill` accept an
optional `"to"` field (unix seconds). The chart window ends at that
moment — the last bar closes at `to` and studies compute over the
anchored window, giving a point-in-time market read with no lookahead
(bar-replay semantics without the replay session).

---

## Skill Commands (20 indicator skills)

Each skill is a shortcut for `run` with a pre-configured Pine ID and custom output parser.

```
tvcli <skill-name> [options]
```

**Common flags for all skills:**

| Flag | Description |
|------|-------------|
| `--symbol <SYM>` | Market symbol (default varies per skill) |
| `--tf <TF>` | Timeframe (default varies per skill) |
| `--bars <N>` | Number of bars (auto-capped to tier) |
| `--json` | JSON output |
| `--agent` | Agent-ready v2 JSON envelope |
| `--signals` | Use generic extractor (bypass custom parser) |
| `--raw` | Raw unprocessed capture |
| `--schema` | Show metaInfo without running |
| `--input key=value` | Override Pine input (repeatable) |
| `--preset <name>` | Use a bundled preset |
| `--force-cleanup` | Aggressively retry on study limit |

**Available skills:**
`cust`, `bsv`, `dvi`, `vgaps`, `anchored-vp`, `vp`, `order-flow`, `smc`, `ict`, `liq-sweep`, `sr-breaks`, `sniper`, `swingarm`, `ema-atr`, `quantum`, `ust`, `trend`, `golden`, `shemar`, `mtf`, `gold-divergence`, `xau-trend`

See `docs/skills/` for per-skill documentation.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SESSION` | Yes | TradingView `sessionid` cookie |
| `SIGNATURE` | Yes | TradingView `sessionid_sign` cookie |
| `TV_USER` | For writes | TradingView username |
| `DEVICE_T` | Yes (for studies) | TradingView `device_t` cookie |
| `TV_TIER` | No (default `free`) | Subscription tier (see [docs/TIER_LIMITS.md](TIER_LIMITS.md)) |
| `TW_DEBUG` | No | Set to `1` for debug logging |

---

## Output Formats

### `--json` (signals)
```json
{
  "Meta": {"pineId": "...", "symbol": "...", "timeframe": "...", "periodCount": 280},
  "Classifications": {"Fast": "price", "Slow": "price"},
  "Last": {"Fast": 63066.99, "Slow": 63064.39},
  "Series": [{"time": 1785816000, "Fast": 63723.43, "Slow": 63532.01}, ...],
  "Levels": [{"Field": "Fast", "Kind": "support", "Value": 63723.43}, ...],
  "Events": [{"Time": 1785816000, "Field": "Buy_Signal", "Kind": "buy", "Value": 1}, ...],
  "GraphicCounts": {"box": 5, "line": 407, "alert": 403},
  "Report": {
    "NetProfit": -13308.57, "SharpeRatio": 0.045, "SortinoRatio": 0.084,
    "TotalTrades": 244, "WinRate": 0.27, "Currency": "USDT",
    "Trades": [{"Side": "buy", "Entry": 71408.9, "Price": 71268.01, "Qty": 1, "Profit": -140.89}, ...]
  },
  "Bias": "neutral",
  "Confidence": 0.5
}
```

### `--agent` (agent-ready v2)
```json
{
  "Status": "ok",
  "ExitCode": 0,
  "AgentContext": {"Workflow": "...", "Symbol": "...", "Timeframe": "..."},
  "Market": {"LastPrice": 63095.32, "Bias": "neutral"},
  "Structure": {
    "classifications": {...},
    "last": {...},
    "levels": [...],
    "events": [...],
    "graphicCounts": {...},
    "meta": {...},
    "strategy": {...}
  },
  "Conformance": {"HasValidData": true, "AgenticScore": 0.5},
  "SchemaVersion": "agent-ready-v2.0.0"
}
```
