# BTCUSD Skill Command Audit

Generated: 2026-07-19T08:15:18.503038+00:00

Symbol: **BINANCE:BTCUSDT** — each skill run with a rational preset/timeframe combination.

Two output paths were tested for every skill:
1. **Custom parser** (`--agent --json`) — the skill's hand-written Go parser.
2. **Generic signals extractor** (`--signals --agent --json`) — schema-guided extraction.

## Executive Summary
- **PASS**: 14 skills produce structurally valid, useful output.
- **WARN**: 1 skills run but generic signals are empty (parser still works).
- **FAIL / NO_DATA**: 6 skills cannot return usable data for BTCUSD under the default (free-tier) configuration.

## Per-Skill Results

| Skill | Preset | TF | Parser | Signals | Price | Bias | Parser Fields | Signals Fields | Verdict | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| anchored-vp | default | 1h | no_data | no_data | — | — | 0 | 0 | NO_DATA | graphics-only script; no period data on any symbol; BTCUSD errors |
| bsv | scalping | 15m | ok | ok | 64,722.43 | neutral | 7 | 6 | PASS | parser fields extracted |
| dvi | default | 1h | ok | ok | 64,722.42 | bullish | 8 | 8 | PASS | parser fields extracted |
| ema-atr | default | 1h | ok | ok | 64,601.29 | bearish | 7 | 5 | PASS | parser fields extracted |
| gold-divergence | default | 1h | ok | ok | 64,722.41 | bearish | 6 | 3 | PASS | parser fields extracted |
| golden | default | 1h | no_data | ok | — | — | 0 | 17 | NO_DATA | wrong PineID; Verdict field missing |
| ict | default | 1h | FAIL | ok | — | — | 0 | 0 | FAIL | heavy script (80 inputs); needs higher TV_TIER |
| liq-sweep | scalping | 15m | ok | ok | 64,967.25 | bullish | 6 | 2 | PASS | parser fields extracted |
| mtf | default | 1h | no_data | ok | — | — | 0 | 0 | NO_DATA | XAUUSD-specific; 0 periods on BTCUSD |
| order-flow | default | 15m | ok | ok | 64,711.79 | neutral | 5 | 0 | WARN | signals empty |
| quantum | default | 1h | ok | ok | 64,328.75 | neutral | 7 | 6 | PASS | parser fields extracted |
| shemar | default | 1h | ok | ok | 64,719.45 | neutral | 4 | 10 | PASS | parser fields extracted |
| smc | default | 1h | ok | ok | 64,711.05 | bullish | 12 | 17 | PASS | parser fields extracted |
| sniper | crypto | 15m | ok | ok | 64,718.75 | neutral | 6 | 4 | PASS | parser fields extracted |
| sr-breaks | default | 1h | ok | ok | 63,462.77 | bearish | 6 | 5 | PASS | parser fields extracted |
| swingarm | default | 1h | ok | ok | 63,776.04 | bullish | 7 | 22 | PASS | parser fields extracted |
| trend | crypto | 1h | FAIL | FAIL | — | — | 0 | 0 | FAIL | heavy script (78 inputs); needs higher TV_TIER |
| ust | default | 15m | ok | ok | 64,691.37 | mixed | 13 | 7 | PASS | parser fields extracted |
| vgaps | default | 1h | FAIL | FAIL | — | — | 0 | 0 | FAIL | server-side timeout; needs higher TV_TIER |
| vp | daily | 1h | ok | ok | 64,711.69 | bullish | 9 | 12 | PASS | parser fields extracted |
| xau-trend | default | 1h | ok | ok | 64,691.84 | bullish | 9 | 5 | PASS | parser fields extracted |

## Parser Structure Previews (fixed runs)

- **bsv**: bgConsensus=neutral, buyDominant=8, dominanceRatio=-0.15, neutral=1, recentCrosses=0
- **dvi**: momentum=14.12, resistance=64700, sideways=111, support=61,306.84, trend=1
- **ema-atr**: buyReentry=false, buySignal=false, plot0=64,601.29, plot2=64,551.83, sellReentry=false
- **gold-divergence**: bearDivergences=3, bullDivergences=2, divergenceBias=bearish, latestDivergence=none, rsi=70.17
- **liq-sweep**: bearSweeps=7, bullSweeps=11, latestSweep=none, price=64,967.25, sweepDominance=bullish
- **order-flow**: bearSpikes=0, bullSpikes=0, latestSpike=none, spikeDominance=neutral, totalSpikes=0
- **quantum**: bias=neutral, buySignal=false, price=64,328.75, ribbon={"Plot": 64328.74745380882, "Plot_10": 64334.65588278201, "Plot_2": 64390.858895, sellSignal=false
- **shemar**: buyCount=4, buySignal=false, sellCount=3, sellSignal=false
- **smc**: bearishBOS=3, bearishCHoCH=2, bearishFVG=5, bearishOB=6, bosCount=9
- **sniper**: buySignal=false, emaFast=64,718.75, emaSlow=64,724.78, emaTrend=64,648.60, score=1.27
- **sr-breaks**: bias=bearish, breakBarsAgo=111, lastBreak=bearish, price=63,462.77, resistance=64,387.99
- **swingarm**: bias=bullish, extremum=64,967.25, fib1=64,231.08, fib2=64,030.96, fib3=63,911.84
- **ust**: aligned=false, background=BULLISH, buySignals=13, combined=MIXED, currentBuy=false
- **vp**: aboveVAHBuffer=false, belowVALBuffer=false, bias=bullish, maxPrice=64,834.22, minPrice=63,952.98
- **xau-trend**: bandWidth=1,157.97, bollingerBasis=64,505.34, bollingerLower=63,926.36, bollingerUpper=65,084.33, crossover=none

## Code fixes applied
1. `internal/cmd/skillcmd.go`: stopped overwriting `result.Status` with `"ok"`; now preserves `no_data`/`error` from parsers and only back-fills `lastPrice` when the parser actually produced data.
2. `internal/skill/parsers/smc.go`: replaced non-existent aggregated count fields with explicit counting of Bullish/Bearish BOS/CHoCH/FVG/OB event flags across bars.
3. `internal/skill/parsers/ust.go`: read SuperTrend line prices from `plot_0`/`plot_2` instead of the style-mapped `ST1`/`ST2` colorers.
4. `internal/skill/parsers/sniper.go`: read EMA line prices from `plot_0`/`plot_2`/`plot_5` instead of style-mapped color-code values.
5. `internal/skill/parsers/quantum.go`: fixed `FormatText` referencing a non-existent `ribbonState` key.
6. `internal/skill/parsers/golden.go`: returns `no_data` when the expected `Verdict` field is missing (the skill is registered to the same Pine ID as `smc`).
7. `internal/skill/parsers/trend.go`: replaced obsolete `TrendDirection`/`TQI`/`Regime` lookup with `plot_0` SuperTrend line and buy/sell signal flags.

## Outstanding issues
- **anchored-vp**: Graphics-only script (no period data). Reports `no_data` on XAUUSD; errors on BTCUSD (TradingView-side).
- **vgaps**: Server-side timeout (heavy script, 19 inputs). Use `--signals --agent --json` with paid `TV_TIER`.
- **ict**: Heavy script (80 inputs) times out under free tier. Use `--signals --agent --json` with paid `TV_TIER`.
- **trend**: Heavy script (78 inputs) times out under free tier. Use `--signals --agent --json` with paid `TV_TIER`.
- **mtf**: XAUUSD-specific; emits 0 periods for BTCUSD. Reports `no_data`.
- **golden**: Wrong PineID; Verdict field missing. Reports `no_data`.

## Artifacts
- `skill-analysis/btcusd_runs/run_<skill>_<preset>.json` — custom parser agent output
- `skill-analysis/btcusd_runs/signals_<skill>_<preset>.json` — generic signals extractor output
- `skill-analysis/btcusd_runs/ANALYSIS.md` — detailed per-skill dump
- `skill-analysis/btcusd_runs/BTCUSD_SKILL_AUDIT.md` — this report
