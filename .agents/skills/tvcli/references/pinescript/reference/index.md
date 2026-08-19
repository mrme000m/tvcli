# Layer 12: Reference Index — Complete Lookup

> **Prerequisite:** All previous layers. Quick reference for functions, types, endpoints, commands, and parsers.

---

## Pine Script Quick Reference (v5/v6)

### Declaration
```pine
//@version=6
indicator("Name", overlay=true, max_labels_count=500)
strategy("Name", overlay=true, initial_capital=10000, commission_type=strategy.commission.percent, commission_value=0.04)
```

### Inputs
```pine
input.int(defval, title, minval, maxval, step, tooltip, group, inline, confirm)
input.float(defval, title, minval, maxval, step, tooltip, group, inline, confirm)
input.bool(defval, title, tooltip, group, inline)
input.string(defval, title, options, tooltip, group, inline)
input.source(defval, title, tooltip, group, inline)
input.color(defval, title, tooltip, group, inline)
input.time(defval, title, tooltip, group, inline)
input.timeframe(defval, title, tooltip, group, inline)
input.session(defval, title, tooltip, group, inline)
```

### Core Functions
| Category | Functions |
|----------|-----------|
| **MAs** | `ta.sma`, `ta.ema`, `ta.rma`, `ta.wma`, `ta.vwma`, `ta.swma`, `ta.alma` |
| **Oscillators** | `ta.rsi`, `ta.stoch`, `ta.macd`, `ta.cci`, `ta.mfi`, `ta.uo`, `ta.williams` |
| **Volatility** | `ta.atr`, `ta.bb`, `ta.kc`, `ta.donchian`, `ta.stdev` |
| **Volume** | `ta.obv`, `ta.cmf`, `ta.ad`, `ta.adosc`, `ta.vwap` |
| **Trend** | `ta.adx`, `ta.dmi`, `ta.aroon`, `ta.psar`, `ta.supertrend` |
| **Math** | `math.min`, `max`, `abs`, `round`, `ceil`, `floor`, `sqrt`, `pow`, `log`, `exp`, `sin`, `cos`, `tan` |
| **Logic** | `ta.cross`, `crossover`, `crossunder`, `rising`, `falling`, `change` |
| **Time** | `timeframe.period`, `isintraday`, `isdaily`, `isweekly`, `ismonthly`, `session.ismarket` |
| **Request** | `request.security`, `request.earnings`, `request.dividends`, `request.splits` |
| **Array** | `array.new_*`, `push`, `pop`, `shift`, `unshift`, `get`, `set`, `size`, `slice`, `copy`, `reverse`, `sort`, `sum`, `avg`, `min`, `max`, `indexof`, `includes` |
| **Matrix** | `matrix.new`, `get`, `set`, `rows`, `columns`, `transpose`, `mult`, `sum`, `det`, `inv` |
| **Map** | `map.new`, `put`, `get`, `remove`, `clear`, `size`, `keys`, `values`, `contains` |
| **Plotting** | `plot`, `plotshape`, `plotchar`, `plotarrow`, `plotcandle`, `plotbar`, `hline`, `fill` |
| **Drawings** | `label.new`, `line.new`, `box.new`, `polyline.new`, `label.delete`, `line.delete`, `box.delete` |
| **Tables** | `table.new`, `cell`, `merge_cells` |
| **Alerts** | `alertcondition`, `alert` |

### Special Variables
```pine
bar_index, barstate.ishistory, barstate.isrealtime, barstate.islast, barstate.isconfirmed
open, high, low, close, volume, hl2, hlc3, ohlc4
time, time_close, timeframe.period, syminfo.tickerid, syminfo.pointvalue, syminfo.mintick
na, nz, fixnan
```

---

## Pine Facade API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/translate/<pineId>/<version>` | Full script + metaInfo |
| GET | `/get/<pineId>/<version>` | Raw source only |
| POST | `/translate_light` | Compile/validate |
| POST | `/save/new` | Create script |
| POST | `/save/next/<pineId>` | Update script |
| POST | `/delete/<pineId>` | Delete script |
| GET | `/versions/<pineId>` | Version history |
| GET | `/list?filter=saved` | List private scripts |
| GET | `pubscripts-suggest-json/` | Search public |

**Base:** `https://pine-facade.tradingview.com/pine-facade`
**Auth:** Cookie header with SESSION, SIGNATURE, DEVICE_T

---

## WebSocket API

**Endpoint:** `wss://data.tradingview.com/socket.io/websocket`
**Protocol:** `~m~<len>~m~<json>`
**Key Messages:** `chart_create_session`, `resolve_symbol`, `create_series`, `create_study`, `remove_study`, `chart_delete_session`, `set_auth_token`
**Server Messages:** `symbol_resolved`, `timescale_update`/`du`, `study_completed`, `study_error`

---

## tvcli Commands

| Command | Purpose | Key Flags |
|---------|---------|-----------|
| `compile` | Syntax check | — |
| `create` | New script | `--name` |
| `push` | Update script | `--force` |
| `pull` | Download script | — |
| `delete` | Remove script | `--yes` |
| `list` | Local scripts | `--remote`, `--public` |
| `search` | Public scripts | `--limit`, `--json` |
| `publist` | Public library | `--offset`, `--limit` |
| `top` | Top scripts | `--limit`, `--output` |
| `run` | Execute on data | `--symbol`, `--tf`, `--bars`, `--signals`, `--json`, `--raw`, `--persistent`, `--loop`, `--settle`, `--force-cleanup` |
| `eval` | Run source directly | `--compile-only`, `--script`, `--agent` |
| `fetch` | Raw OHLCV | `--symbol`, `--tf`, `--bars`, `--csv-out`, `--json-out` |
| `sync` | Gap-fill OHLCV | `--loop`, `--force`, `--out` |
| `clean` | Free study slots | `--iterations`, `--delay`, `--symbol` |
| `check-auth` | Verify cookies | `--json` |
| `serve` | HTTP server | `--addr` |
| `inputs` | Inspect inputs | `--json`, `--raw` |
| `skills` | List skills | `--json` |

---

## Indicator Skills (Built-in)

| Skill | Parser File | Description |
|-------|-------------|-------------|
| `bsv` | `bsv.go` | Buy/Sell Volume |
| `dvi` | `dvi.go` | Delta Volume Intensity |
| `ust` | `ust.go` | Ultra Sensitive SuperTrend |
| `swingarm` | `swingarm.go` | SwingArm ATR Trend |
| `ema-atr` | `ema_atr.go` | EMA + ATR Pro Engine |
| `sr-breaks` | `sr_breaks.go` | Support/Resistance Breaks |
| `shemar` | `shemar.go` | SHEMAR HMA ST + SMC |
| `quantum` | `quantum.go` | Quantum Ribbon Lite |
| `vgaps` | `vgaps.go` | Volume Gaps & Imbalances |
| `anchored-vp` | `anchored_vp.go` | Anchored Volume Profile |
| `mtf` | `mtf.go` | XAUUSD MTF Trend Dashboard |
| `sniper` | `sniper.go` | Precision Sniper |
| `smc` | `smc.go` | Smart Money Concepts |
| `golden` | `golden.go` | Golden Rule Strategy |
| `trend` | `trend.go` | Self-Aware Trend System |
| `ict` | `ict.go` | ICT Auto-Validated SMC |
| `liq-sweep` | `liq_sweep.go` | Institutional Liquidity Sweep |
| `order-flow` | `order_flow.go` | Volume Spike Order Flow |
| `gold-divergence` | `gold_divergence.go` | Gold RSI Divergence |
| `xau-trend` | `xau_trend.go` | XAUUSD EMA + Bollinger Trend |

**Usage:** `tv <skill> --symbol OANDA:XAUUSD --tf 15m --signals --json`

---

## Go Package Structure

```
/pkg/pinefacade/
  client.go     # HTTP client: Compile, SaveNew, SaveNext, Delete, Get, Search
  types.go      # CompileResult, CompileError, ScriptResult, SearchResult
  util.go       # PineID, SHA256, timeframe, symbol, version sorting
  search.go     # Public script search
  parser.go     # Pine source parsing helpers

/pkg/tradingview/
  client.go     # WSClient: Connect, auth, protocol, sessions
  chart.go      # ChartSession: symbol, series, studies
  study.go      # ChartStudy: data, graphics, strategyReport
  indicator.go  # PineIndicator, BuiltinIndicator, inputs
  protocol.go   # ~m~ framing parser/formatter
  compressed.go # zlib/zip decompression for ns.dCompressed
  auth/         # Cookie → auth_token fetch

/internal/cmd/
  compile.go    # tvcli compile
  create.go     # tvcli create
  push.go       # tvcli push
  pull.go       # tvcli pull
  delete.go     # tvcli delete
  run.go        # tvcli run (WS orchestration)
  run_persistent.go # --persistent/--loop
  fetch.go      # tvcli fetch
  sync.go       # tvcli sync
  clean.go      # tvcli clean
  check_auth.go # tvcli check-auth
  serve.go      # tvcli serve (HTTP)
  inputs.go     # tvcli inputs
  skills.go     # tvcli skills
  skillcmd.go   # tv <skill> dispatch
  universal.go  # Generic indicator runner
  eval.go       # tvcli eval
  top.go        # tvcli top
  search.go     # tvcli search
  help.go       # Help text
  shared.go     # Shared utilities

/pkg/skill/parsers/
  *.go          # 30+ skill parsers extracting signals from study data
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SESSION` | Write ops | — | `sessionid` cookie |
| `SIGNATURE` | Write ops | — | `sessionid_sign` cookie |
| `DEVICE_T` | No | — | `device_t` cookie |
| `TV_USER` | Write ops | — | TradingView username |
| `TV_TIER` | No | `free` | free\|essential\|plus\|premium\|ultimate |
| `PINE_FACADE_URL` | No | `https://pine-facade.tradingview.com/pine-facade` | API base |
| `DEBUG` | No | `false` | Enable debug logs |

---

## Tier Limits

| Tier | Charts | Studies/Chart | Connections | Bars Back | Calc Timeout |
|------|--------|---------------|-------------|-----------|--------------|
| Free | 1 | 2 | 2 | 180d / ~5K | 20s |
| Essential | 2 | 5 | 10 | 365d / ~10K | 40s |
| Plus | 4 | 10 | 20 | Unlimited / ~20K | 40s |
| Premium | 8 | 25 | 50 | Unlimited / ~50K | 40s |
| Ultimate | 16 | 50 | 200 | Unlimited / ~100K | 100s |

---

## Common File Paths

```
tvcli                    # Binary
.env                     # Auth config
.tv-meta.json            # Local script tracking
.tv-scripts/             # Downloaded .pine + .json pairs
.tv-src/                 # Cached source files
skills/pinescript/       # THIS SKILL
```

---

## Quick Troubleshooting

| Symptom | Check |
|---------|-------|
| Compile OK, run fails | Runtime error — use `--raw` |
| "study limit" | `tvcli clean`, check auth, check tier |
| Auth fails | Re-extract cookies, `tvcli check-auth` |
| Symbol error | Use `EXCHANGE:SYMBOL` format |
| No signals | Check `--settle`, verify plot names in metaInfo |
| Push unchanged | `--force` or edit file |
| Timeout | Increase `--settle`, check market hours |

---

## Skill Development: Adding a Parser

1. Create `/pkg/skill/parsers/my_skill.go`
2. Implement parser function taking `periods`, `graphic`, `strategyReport`
3. Register in `/internal/cmd/skillcmd.go`
4. Add to `help.go` skills list
5. Test: `tv my_skill --symbol OANDA:XAUUSD --tf 15m --signals`

---

## Resources

- **Pine Script Reference (v6 is current; v5 still supported):** https://www.tradingview.com/pine-script-docs
- **tvcli Source:** the repo/package root containing this skill (`cmd/`, `internal/`, `pkg/`)
- **Go Docs:** `go doc github.com/mrme000m/tvcli/...`