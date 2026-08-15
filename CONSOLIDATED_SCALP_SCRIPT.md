# Consolidated Custom ("cust") Scalping Script — Pine Source Analysis & Proposal

> **Implementation status (2026-07-19):** Implemented. The consolidated
> indicator lives at `.tv-src/ScalpQuant.pine` (Pine v5, quant-only, zero
> graphics). It compiles cleanly against the live Pine facade and is published
> as a saved script `USER;631a730a8dfc4fa8aa4757109ab14af9`. Run it with
> `./tvcli cust --symbol OANDA:XAUUSD --tf 1h --bars 250 --allow-private`
> (the `--allow-private` flag is required because it is a saved, not published
> `PUB;`, script). The `cust` skill + numeric parser are in
> `internal/skill/parsers/scalpquant.go`.

## 1. How the sources were obtained

The 21 skill scripts are **third-party published TradingView indicators** (`PUB;` namespace, `scriptAccess: open_no_auth` — all free-tier runnable, no invitation needed).

- `tv pull <pineID>` originally hit `/translate/{id}/last`, which returns only the **opaque IL blob** (`result.IL`, base64-encoded intermediate language). That is *not* readable Pine and cannot be analyzed or re-compiled by us.
- I added `Client.GetSource()` which hits **`/get/{id}/last`** and returns the **original human-readable Pine source** plus `scriptName`/`version`/`scriptAccess`.
- All 21 readable sources are saved in `.tv-src/*.pine` (and re-pulled into `.tv-scripts/`).

> **Bug confirmed:** `golden` and `smc` share the same PineID `PUB;6daafb2cabe6419d98ae25229d2327f8`. `golden` therefore pulls the **Smart Money Concepts** script, not a "Golden" indicator — the `KnownBroken` note in `golden.go` is correct.

Versions in the wild are mixed: **v3 (quantum), v4 (order-flow, sr-breaks, swingarm), v5 (mtf, bsv, gold-divergence, sniper), v6 (everything else)**. A consolidated script must pick one version (v5/v6 recommended).

## 2. Per-skill analysis (21 skills → 20 unique scripts)

Legend: **TF** = uses `request.security` (multi-timeframe). **Out** = dominant output primitive. **Quant** = exposes clean numeric series a parser can read directly.

| # | skill | Real name | Type | Core math | TF | Output | Quant? | Scalping relevance | Notes |
|---|-------|-----------|------|-----------|----|--------|-------|--------------------|-------|
| 1 | mtf | XAUUSD MTF Trend Analyzer | Trend/HTF | SMA+RSI+MACD+BB+DMI+PSAR → 6-vote trend score per TF | ✅ M15/M30/H1/H4/D1 | Table | ✅ (score) | ⭐⭐⭐ Core bias filter | Graphics-only; weight higher TFs for scalping |
| 2 | bsv | Buying/Selling Volume | Volume | `(C-L)/(H-L)` buy/sell vol split + MA | ❌ | plot+columns | ✅ | ⭐⭐ Pressure confirm | Cheap, good confluence |
| 3 | dvi | Trend w/ Vol & Momentum | Trend/Vol | ATR + ROC + D/SR via `request.security` | ✅ D/W | plot+bgcolor | ✅ | ⭐⭐ Context | Slower TF; bias only |
| 4 | ema-atr | EMA+ATR PRO Engine | Trend/Stop | EMA filter + ATR trailing stop + swing break | ❌ | plot+labels | ✅ | ⭐⭐⭐ Entries & trail | Excellent for scalp entry/SL |
| 5 | gold-divergence | Adv Gold Scalping (RSI div) | Osc/Div | RSI + pivots divergence + BB | ❌ | plot+shapes | ✅ (RSI) | ⭐⭐ Reversal | XAU-focused; RSI div signal |
| 6 | smc | Smart Money Concepts [LuxAlgo] | SMC | BOS/CHoCH, OB, FVG, swept liquidity (HTF) | ✅ FVG TF | box/line/label | ❌ graphic | ⭐⭐⭐ Structure | Drawings only; no numeric series |
| 7 | ict | ICT Validated SMC v1 | SMC | OB/Breaker/FVG/BPR/Liquidity/Killzones | ✅ HTF | box/line/label+table | ❌ graphic | ⭐⭐⭐ Structure | 2015 lines; heaviest; very draw-heavy |
| 8 | liq-sweep | Inst. Liquidity Sweep & Vol Breakout | SMC/Vol | HH/LL sweep + volume × mult | ❌ | shapes+labels+lines | ✅ (state) | ⭐⭐⭐ Entries | Compact; expose sweep as ±1 series |
| 9 | order-flow | Volume Spike Strategy | Volume | Vol SMA × mult + close vs coin SMA | ❌ (v4) | plotchar | ✅ (state) | ⭐⭐ Spike trigger | Tiny (27 lines); v4 |
| 10 | quantum | EMA Ribbon [Krypt] | Trend | 8× EMA ribbon | ❌ (v3) | plot×8 | ✅ | ⭐ Trend visual | v3; redundant with EMA filters |
| 11 | shemar | SHEMAR HMA ST + SMC Conf | Trend/SMC | HMA + Supertrend + Squeeze + SMC confidence score | ✅ 60/5 | plot+shapes | ✅⭐ (score 0-100) | ⭐⭐⭐ Confluence | Scores conviction numerically; very useful |
| 12 | sniper | BS Buy&Sell Signals w/ EMA | EMA cross | EMA14/21 cross + 3 TP lines | ❌ | plot+lines+labels | ✅ | ⭐⭐⭐ Entries & TP | Concrete TP levels = good for scalps |
| 13 | sr-breaks | S/R Levels w/ Breaks [LuxAlgo] | Levels | pivot S/R + volume-osc break | ❌ (v4) | plot+shapes | ✅⭐ (levels) | ⭐⭐⭐ Key levels | Dynamic S/R as numeric lines |
| 14 | swingarm | Blackflag FTS | Trend/Stop | ATR trail + fib entry levels | ✅ (self) | plot+shapes | ✅ | ⭐⭐ Trail + fib | ATR trailing stop variant |
| 15 | trend | Self-Aware Trend System | Trend/Quality | TQI (4-factor quality) + adaptive ATR bands | ❌ | Table + plot | ✅⭐ (TQI 0-1) | ⭐⭐ Quality gate | Returns a *table* (not periods) — explains why our period parser yields 0 |
| 16 | ust | Ultra Sensitive SuperTrend | Trend | 2× SuperTrend (sensitive+ultra) + HA filter | ❌ | plot+shapes | ✅ | ⭐⭐⭐ Entries | Fast ST flips; scalp-friendly |
| 17 | vgaps | Volume Gaps & Imbalances (Zeiierman) | Volume/VP | Binned VP + zero-vol zones + delta | ❌ | box drawings | ❌ graphic | ⭐⭐⭐ VP zones | Drawings only; POC not a series |
| 18 | vp | Fixed Range VP Zones | Volume/VP | Binned VP → VAL/VAH/POC + percentile buffers | ❌ | plot+fill+shapes | ✅⭐ (VAL/VAH/POC) | ⭐⭐⭐ VP zones | Clean POC/VAH/VAL; good numeric |
| 19 | xau-trend | XAUUSD Trend Strategy | Trend/Strategy | EMA50/200 + RSI + BB | ❌ (strategy) | plot | ✅ | ⭐ Context | Swing, not scalp; EMA/RSI/BB only |
| 20 | anchored-vp | Anchored Clusters VP [LuxAlgo] | Volume/VP | K-means clusters + per-cluster VP (POC) | ❌ | box/label drawings | ❌ graphic | ⭐⭐⭐ VP zones | Needs anchor time range; drawings |
| 21 | golden | *(mis-wired → SMC)* | — | — | — | — | — | ❌ BROKEN | Shares SMC's PineID; remove/repair |

### Output-style clustering (for context only)
- **Dashboard tables** (graphics): `mtf`, `trend` → these are the very graphics we are dropping from the consolidated design.
- **Chart drawings** (box/line/label, no numeric series): `smc`, `ict`, `vgaps`, `anchored-vp`, `liq-sweep` → inherently graphic; not quant-parseable without reconstruction.
- **Numeric plot/shape emitters**: the rest → these expose (or can be coerced to expose) **quantitative series**, which is what the consolidated script wants.

## 3. Design principle: quantitative data, not graphics

The consolidated "cust" script must be built around **exposing clean, named numeric market data** — nothing else.

- **No `table`, no `box`, no `line`, no `label`, no `plotshape` drawings.** All of that is graphics complexity we explicitly do not want.
- Every metric is emitted as a **`plot()` series with a stable, known title** (e.g. `mtfBias`, `atrStopLong`, `poc`, `buyVol`, `rsi`, `structState`). The TradingView facade returns these as **`periods`** — directly parseable numeric arrays, no reconstruction.
- The consumer (tvcli parser) reads them through the existing **`periods` / `ResolveAny` / `ParseWithSchema` numeric path**. The graphic-resolution machinery (`ResolveGraphicDashboard`, `ReconstructTables`, `GraphicLabels`) is **unnecessary for this script** and can be ignored for it.
- **Why this is better than graphics:** deterministic machine parsing, no object-cap limits (500 boxes/labels), no table-layout fragility, and one run yields the full quantitative picture instead of 21 separate calls.

The single hard rule for every indicator we fold in: **"if it can't be a number, it's out."** Scripts whose value is purely visual (SMC drawings, VP cluster boxes) are either dropped or reduced to a single numeric state.

## 4. Which skills translate to clean quantitative output

| Keep (exposes numeric series) | What the cust script reads | Drop / reduce |
|------|------|------|
| `mtf` | per-TF trend score → `biasM15..biasD1`, `strength` | — (convert its table → plots) |
| `ema-atr` | `atrStopLong`, `atrStopShort`, `emaTrend` (±1) | labels dropped |
| `ust` | `st1`, `st2`, `stTrend` (±1) | shapes dropped |
| `swingarm` | `trail`, `fib1..fib3` | fib-shape markers dropped |
| `sniper` | `ema14`..`ema200`, `signal` (±1), `tp1..tp3` | TP lines dropped (keep numeric levels) |
| `quantum` | `ema1`..`ema8` (or just keep 2-3) | ribbon visual dropped |
| `bsv` | `buyVol`, `sellVol`, `maBuy`, `maSell` | columns/barcolor dropped |
| `vp` | `poc`, `vah`, `val` | shaded zones dropped |
| `sr-breaks` | `support`, `resistance` | break shapes dropped |
| `shemar` | `hma`, `supertrend`, `confidence` (0-100) | all labels/shapes dropped |
| `gold-divergence` | `rsi`, `bullDiv`/`bearDiv` (±1) | divergence markers dropped |
| `dvi` | `trend` (-1/0/1), `atr`, `roc` | bgcolor dropped |
| `xau-trend` | `emaFast`, `emaSlow`, `rsi`, `bbUpper`/`bbLower` | (swing-only; optional) |
| `liq-sweep` | `bullSweep`/`bearSweep` (±1) | lines/labels dropped |
| `order-flow` | `volSpikeBuy`/`volSpikeSell` (±1) | plotchar dropped |
| `trend` (TQI) | `tqi` (0-1), `bandUpper`/`bandLower` | **table dropped → plots** |
| **Drop entirely** | — | `smc`, `ict`, `vgaps`, `anchored-vp` (inherently graphic; no clean numeric core) |

This yields a **17-in / 4-out** consolidation where every kept input contributes a named number.

## 5. Recommended consolidated design — "ScalpQuant" (quant-only overlay)

A single `indicator(..., overlay=true)` (v5/v6) that re-implements the generic math of the kept skills inline and exposes **only numeric plots + `alertcondition`s**.

### Layers → numeric outputs
| Layer | From | Plots emitted |
|-------|------|---------------|
| Bias engine | `mtf` | `biasM15`,`biasM30`,`biasH1`,`biasH4`,`biasD1` (each ±1), `biasStrength` (-6..6), `biasWeighted` (HTF-weighted) |
| Trend / stop | `ema-atr`,`ust`,`swingarm` | `emaTrend` (±1), `atrStopLong`, `atrStopShort`, `stTrend` (±1), `trail`, `fib1..fib3` |
| EMA ribbon | `quantum`,`sniper` | `emaFast`,`emaSlow`,`emaTrendCross` (±1), `tp1`..`tp3` |
| Volume | `bsv`,`vp`,`order-flow` | `buyVol`,`sellVol`,`poc`,`vah`,`val`,`volSpike` (±1) |
| Levels | `sr-breaks` | `support`,`resistance` |
| Oscillator / quality | `gold-divergence`,`shemar`,`trend`(TQI),`dvi` | `rsi`,`bullDiv`/`bearDiv` (±1), `confidence` (0-100), `tqi` (0-1), `roc`, `atr` |
| Liquidity trigger | `liq-sweep` | `bullSweep`/`bearSweep` (±1) |
| Composite signal | (new) | `scalpScore` (-100..100), `scalpSignal` (±1), `alertcondition` BUY/SELL |

### Output contract (what the parser reads as `periods`)
Stable plot titles are the contract. Example parse mapping in tvcli:
```
biasWeighted   -> overall directional bias (-1..1)
scalpSignal    -> +1 long / -1 short / 0 flat
scalpScore     -> magnitude of conviction (-100..100)
atrStopLong    -> long invalidation price
atrStopShort   -> short invalidation price
poc/vah/val    -> volume acceptance zone
support/resistance -> dynamic S/R
rsi/confidence/tqi -> filter quality
```
No table, no boxes, no labels — only `plot()` + `alertcondition()`. This drops straight into the existing `periods`-based parser path.

### Why this beats running 21 scripts separately
- One run = full quantitative scalping picture (bias + entry + structure-state + volume + quality + alert).
- No graphic reconstruction, no object caps, deterministic parsing.
- "Continual calculation" is native in Pine (recomputes every bar); the facade just reads the resulting `periods`.

## 6. Feasibility & next steps

**Feasible.** Pine cannot `import` the 21 third-party scripts, so `ScalpQuant` must **re-implement the generic math inline** (EMA/SMA cross, ATR trail, volume split, pivot S/R, binned VP→POC, RSI divergence, MTF score, TQI). All of that is standard, re-authorable TA — no proprietary logic required.

1. Author `ScalpQuant.pine` (v5/v6) implementing the layers above; emit only named numeric plots + alerts.
2. Add a `cust` skill entry + a thin parser that maps the plot titles (above) to `periods` (reuse `ParseWithSchema`/`ResolveAny`).
3. (Optional) push to TradingView via `Client.SaveNew(source, name, cookie)` so it runs continuously; keep source in `.tv-src/` for on-demand facade runs.
4. Fix `golden` (repair its PineID or remove) since it currently aliases `smc`.

### Files produced
- `.tv-src/*.pine` — 20 readable pulled sources (analysis corpus)
- `.tv-scripts/*.pine` — re-pulled as readable source (via new `GetSource`)
- `pkg/pinefacade/client.go` — `GetSource()` (`/get/` endpoint)
- `pkg/pinefacade/types.go` — `ScriptResult.Access`
- `internal/cmd/pull.go` — uses `GetSource`, prints `Access`
