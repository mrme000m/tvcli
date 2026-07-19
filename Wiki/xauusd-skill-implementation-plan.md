---
type: Plan
title: XAUUSD Skill Commands Implementation Plan
description: How to turn the research in xauusd-pine-scripts.md and xauusd-youtube-video-analysis.md into tvcli skill commands with presets, based on the existing skill framework.
tags: [tradingview, pine-script, xauusd, skill-commands, implementation-plan]
timestamp: 2026-07-19T07:30:00Z
okf_publish: false
---

# XAUUSD Skill Commands Implementation Plan

**Status:** In progress. The first skill (`liq-sweep`) is implemented and tested. Remaining skills are planned below.

This document uses the existing `tvcli` skill-framework code and the research captured in:

- [`Wiki/xauusd-pine-scripts.md`](xauusd-pine-scripts.md) — Pine IDs + scoring recipes
- [`Wiki/xauusd-youtube-video-analysis.md`](xauusd-youtube-video-analysis.md) — NotebookLM-extracted tutorial details
- Load-test field maps were captured during research (now cleaned up)

## 1. Existing skill architecture (the code we extend)

| File / package | Responsibility |
|---|---|
| `internal/skill/skill.go` | `Skill` struct, `InputDef`, `SkillResult`, `AgentResult`, preset helpers |
| `internal/skill/registry.go` | Global `Register`/`Get`/`All` registry |
| `internal/skill/parsers/*.go` | One file per skill. Declares `Skill`, implements `ParseOutput` + `FormatText`, calls `skill.Register` in `init()` |
| `internal/cmd/shared.go` | Blank-imports `internal/skill/parsers` and calls `RegisterSkills()` for every registered skill |
| `internal/cmd/skillcmd.go` | Runtime: resolves `--preset`, builds TV input map, runs script, calls `ParseOutput`, emits JSON/agent/text |
| `pkg/pipeline/extract.go` | Generic schema-guided extractor used by `--signals` |
| `internal/cmd/help.go` | Static help text listing all skills (must be updated) |
| `skill-analysis/SKILLS.md` | Skill framework reference: architecture, adding skills, raw response, schema, error codes. |

**Key conventions from existing parsers:**

- `latestClosed(periods)` returns `periods[1]` (last closed bar).
- `getField(period, []string{"Close", "close"})` tries multiple aliases.
- `Structure` holds numeric features; `Opportunities` holds ranked setups; `Conformance.AgenticScore` is the confidence metric.
- Presets are keyed by input name and translated to `in_N` IDs at runtime.

## 2. Implementation log

| Skill | Status | Files changed | Notes |
|---|---|---|---|
| `liq-sweep` | ✅ Done | `internal/skill/parsers/liq_sweep.go`, `liq_sweep_test.go`, `testdata/liq_sweep_fixture.json`, `internal/cmd/help.go`, `internal/skill/parsers/helpers.go` | Script has no `Close` plot; price is derived from the latest `dwglabels` price. Tests pass. |
| `order-flow` | Planned | — | Clear event flags; straightforward parser. |
| `xau-trend` | Planned | — | Numeric EMA/BB fields; slightly richer parser. |
| `gold-divergence` | Planned | — | Clear divergence flags + RSI. |
| Composite stack | Proposed | — | Shell or Go wrapper after individual skills are stable. |

## 3. What already exists in the skill registry

Several researched aspects are already covered by existing skills. Do not duplicate them.

| Aspect | Existing skill | Pine ID | Why it is sufficient |
|---|---|---|---|
| Volume Profile | `vp` | `PUB;a4e251b831084685afecaa9192f2a3c5` | Numeric `POC`, `VAH`, `VAL` plots and presets (`weekly`, `daily`, `intraday`, `scalping`) already exist. The researched `10x Bull vs Bear VP` is graphics-only and returns zero periods, so it is a poor CLI target (see VOLUME_PROFILE_SKILL.md). |
| Market Structure | `smc` / `ict` | `PUB;6daafb2cabe6419d98ae25229d2327f8` / `PUB;789a5c79bfe9443585da09e85ece73de` | Both expose `BOSCount`, `CHoCHCount`, `FVGCount`, `OBCount`. Research script `Mistab XAUUSD Strength Dashboard` timed out even on `TV_TIER=ultimate`. |
| Trend / Volatility | `trend`, `ema-atr`, `ust`, `quantum` | various | EMA, ATR, Supertrend, ribbon skills already produce bias + opportunities. |
| Price Action | `sniper`, `sr-breaks` | various | Already detect breakout / S-R setups. |
| Order Flow / Volume | `bsv`, `dvi`, `vgaps` | various | Volume dominance, delta, gap-imbalance already exposed. |

## 4. Load-test verdict on the 7 researched scripts

Scripts were run with `./tvcli` against `OANDA:XAUUSD` on `1h / 50 bars`.

| # | Aspect | Script / Pine ID | Status | Parser approach | Recommendation |
|---|---|---|---|---|---|
| 1 | Volume Profile | `10x Bull Vs. Bear VP` `PUB;0999ba6cd86e4709ad54bfa93034f5db` | Loads but returns **0 periods**; all data is in `dwgboxes/labels/polylines` | Graphics-only; very brittle | **Skip new skill** — use existing `vp` |
| 2 | Liquidity | `Institutional Liquidity Sweep & Volume Breakout` `PUB;b9372355c2e6483f952ca49a21d2ebbb` | ✅ Loads; periods expose `Bullish_Sweep_Shape`, `Bearish_Sweep_Shape` + `dwglabels` | Count event flags + labels | **Add skill `liq-sweep`** |
| 3 | Market Structure | `XAUUSD Strength Dashboard` `PUB;427c58b6f07c451f8abc24afcf202f69` | ❌ Timeout up to 100 s even on `ultimate` tier | Unknown | **Skip** — use existing `smc` / `ict` |
| 4 | Order Flow | `Volume Spike Strategy` `PUB;7uP2LNPDc8I150lUzs3Aqa5ju9usF0ZN` | ✅ Loads; periods expose `Buy`, `Sell`, `bell`, `sell`, plots | Count buy/sell/bell signals | **Add skill `order-flow`** (or consider existing `bsv`/`dvi`) |
| 5 | Volatility/Trend | `XAUUSD Trend Strategy` `PUB;a4e47455574243fe9731423c4ddb50ca` | ✅ Loads; periods expose `EMA_Court_Terme`, `EMA_Long_Terme`, `Bollinger_*`, plots | EMA spread + BB position | **Add skill `xau-trend`** |
| 6 | Price Action | `Advanced Gold Scalping` `PUB;779d25a800b242cf9e2ecbe6f350c366` | ✅ Loads; periods expose `Bullish_Divergence`, `Bearish_Divergence`, `RSI`, plots | Divergence flags + RSI | **Add skill `gold-divergence`** |
| 7 | Sentiment/Gap | `XAUUSD Weekly Gap` `PUB;9cdc4275992a4521809d3417a0f7e9da` | ❌ Timeout up to 100 s | Unknown | **Skip** — use existing `vgaps` or implement a simple custom gap script |

**Conclusion:** out of the 7 researched scripts, **only 4 are good immediate CLI targets**. For volume profile, market structure, and weekly gaps we already have better/cheaper alternatives in the registry.

## 5. Proposed new skills

### 5.1 `tv liq-sweep` — Liquidity Sweep + Volume Breakout

```go
var LiqSweepSkill = &skill.Skill{
    Name:     "liq-sweep",
    Synopsis: "Institutional Liquidity Sweep & Volume Breakout — SMC sweep detection",
    PineID:   "PUB;b9372355c2e6483f952ca49a21d2ebbb",
    Inputs: []skill.InputDef{
        {Name: "swingLookback", TVInputID: "in_0", Type: "int", Default: 20},
        {Name: "volumeMultiplier", TVInputID: "in_1", Type: "float", Default: 1.5},
        {Name: "showLabels", TVInputID: "in_2", Type: "bool", Default: true},
    },
    Presets: map[string]map[string]any{
        "default":  {"swingLookback": 20, "volumeMultiplier": 1.5},
        "scalping": {"swingLookback": 10, "volumeMultiplier": 1.2},
        "swing":    {"swingLookback": 50, "volumeMultiplier": 2.0},
    },
    ParseOutput: parseLiqSweep,
    FormatText:  formatLiqSweep,
}
```

**Parser logic:**
- Read `Close` from `latestClosed`.
- Count `Bullish_Sweep_Shape == 1` and `Bearish_Sweep_Shape == 1` over `historicalBars(periods)`.
- Bias: bullish if `bullCount > bearCount`, else bearish.
- `Conformance.AgenticScore = min(0.99, 0.4 + 0.2*bool(bullCount||bearCount) + 0.3*ratio)`.
- Emit an `Opportunity` when the latest closed bar has a sweep.

**Structure output:**
```json
{
  "bullSweeps": 3,
  "bearSweeps": 1,
  "latestSweep": "bullish",
  "price": 3325.50
}
```

### 5.2 `tv order-flow` — Volume Spike Strategy

```go
var OrderFlowSkill = &skill.Skill{
    Name:     "order-flow",
    Synopsis: "Volume Spike Strategy — buy/sell/bell order-flow flags",
    PineID:   "PUB;7uP2LNPDc8I150lUzs3Aqa5ju9usF0ZN",
    Inputs: []skill.InputDef{
        {Name: "volumeMALen", TVInputID: "in_0", Type: "int", Default: 20},
        {Name: "volumeMultiplierPct", TVInputID: "in_1", Type: "int", Default: 500},
        {Name: "coinMALen", TVInputID: "in_2", Type: "int", Default: 5},
        {Name: "showSells", TVInputID: "in_3", Type: "bool", Default: true},
    },
    Presets: map[string]map[string]any{
        "default":  {"volumeMALen": 20, "volumeMultiplierPct": 500},
        "scalping": {"volumeMALen": 10, "volumeMultiplierPct": 300},
        "swing":    {"volumeMALen": 50, "volumeMultiplierPct": 700},
    },
    ...
}
```

**Parser logic:**
- Count `Buy == 1`, `Sell == 1`, `bell == 1`, `sell == 1` over recent bars.
- Latest signal drives bias and `Opportunities`.
- `Structure`: `{buyCount, sellCount, bellCount, latestSignal}`.

### 5.3 `tv xau-trend` — EMA + RSI + Bollinger Trend Strategy

```go
var XAUTrendSkill = &skill.Skill{
    Name:     "xau-trend",
    Synopsis: "XAUUSD Trend Strategy — EMA + RSI + Bollinger",
    PineID:   "PUB;a4e47455574243fe9731423c4ddb50ca",
    Inputs: []skill.InputDef{
        {Name: "emaShort", TVInputID: "in_0", Type: "int", Default: 9},
        {Name: "emaLong", TVInputID: "in_1", Type: "int", Default: 21},
        {Name: "rsiLen", TVInputID: "in_2", Type: "int", Default: 14},
        {Name: "bbPeriod", TVInputID: "in_5", Type: "int", Default: 20},
        {Name: "bbMult", TVInputID: "in_6", Type: "float", Default: 2.0},
    },
    Presets: map[string]map[string]any{
        "default":  {"emaShort": 9, "emaLong": 21, "bbPeriod": 20},
        "scalping": {"emaShort": 5, "emaLong": 13, "bbPeriod": 20},
        "swing":    {"emaShort": 21, "emaLong": 55, "bbPeriod": 20},
    },
    ...
}
```

**Parser logic:**
- Read `EMA_Court_Terme`, `EMA_Long_Terme`, `Bollinger_Upper`, `Bollinger_Lower`, `Close`.
- Bias = bullish if `shortEMA > longEMA && close > upperBB` (or simpler: price vs BB mid).
- Score based on EMA spread normalized by ATR and price position inside/outside BB.

### 5.4 `tv gold-divergence` — RSI Divergence Scalping

```go
var GoldDivergenceSkill = &skill.Skill{
    Name:     "gold-divergence",
    Synopsis: "Advanced Gold Scalping Strategy with RSI Divergence",
    PineID:   "PUB;779d25a800b242cf9e2ecbe6f350c366",
    Inputs: []skill.InputDef{
        {Name: "rsiLen", TVInputID: "in_0", Type: "int", Default: 14},
        {Name: "maType", TVInputID: "in_2", Type: "string", Default: "SMA"},
        {Name: "maLen", TVInputID: "in_3", Type: "int", Default: 14},
        {Name: "bbStdDev", TVInputID: "in_4", Type: "float", Default: 2.0},
        {Name: "showDivergence", TVInputID: "in_5", Type: "bool", Default: true},
    },
    Presets: map[string]map[string]any{
        "default":  {"rsiLen": 14, "maLen": 14},
        "scalping": {"rsiLen": 7,  "maLen": 7},
        "swing":    {"rsiLen": 21, "maLen": 21},
    },
    ...
}
```

**Parser logic:**
- Read `Bullish_Divergence`, `Bearish_Divergence`, `RSI`, `Close`.
- Latest divergence event → opportunity.
- Bias from RSI relative to 50 and divergence direction.

## 6. Composite / stack skill (optional but powerful)

Introduce a meta command that runs the relevant skills and blends them into a single `C_scalp` or `C_swing` score, matching the recipes in `xauusd-pine-scripts.md`.

Two options, from simplest to most robust:

### Option A — shell-level wrapper (now)

```bash
python3 skill-analysis/xauusd_stack.py \
  --symbol OANDA:XAUUSD --tf 5m --style scalp
```

The script runs `./tvcli vp`, `./tvcli liq-sweep`, `./tvcli smc`, `./tvcli order-flow`, `./tvcli xau-trend`, `./tvcli gold-divergence`, `./tvcli vgaps` in parallel, normalizes each `Conformance.AgenticScore`, applies weights, and prints a composite JSON.

### Option B — Go meta skill `tv xauusd-stack` (later)

Add `internal/skill/parsers/xauusd_stack.go` that calls `service.RunScript` directly for a hard-coded list of Pine IDs in one WebSocket session, then blends the results. This avoids spawning 7 processes.

**Default weights:**

```text
Scalp:  0.30*liq-sweep + 0.25*order-flow + 0.20*smc + 0.15*vp + 0.10*gold-divergence
Swing:  0.30*smc + 0.25*xau-trend + 0.20*vgaps + 0.15*vp + 0.10*liq-sweep
```

## 7. Implementation checklist

1. **Add parser files**
   - `internal/skill/parsers/liq_sweep.go`
   - `internal/skill/parsers/order_flow.go`
   - `internal/skill/parsers/xau_trend.go`
   - `internal/skill/parsers/gold_divergence.go`

   Each file: `Skill` var, `parse*` and `format*` funcs, `init() { skill.Register(...) }`.

2. **Ensure registration**
   - `internal/cmd/shared.go` already blank-imports `internal/skill/parsers`. No change needed.

3. **Update help text**
   - Edit `internal/cmd/help.go` to include the 4 new skills under **Indicator Skills**.

4. **Add tests**
   - `internal/skill/parsers/*_test.go` with raw JSON fixtures saved under `internal/skill/parsers/testdata/<skill>_fixture.json`.
   - Capture fixtures with `./tvcli run <pine-id> --raw-out internal/skill/parsers/testdata/<skill>_fixture.json`.

5. **（Optional）Composite wrapper**
   - `skill-analysis/xauusd_stack.py` or `internal/skill/parsers/xauusd_stack.go`.

6. **Update docs**
   - Refresh `Wiki/xauusd-skill-implementation-plan.md` status/log.
   - Update `skill-analysis/PINE_TO_SKILL_SYSTEM.md` if the workflow changed.

7. **Rebuild**
   ```bash
   go build -o tvcli ./cmd/tvcli
   ./tvcli skills --json
   ```

## 8. Preset design

| Skill | `default` | `scalping` | `swing` | Rationale |
|---|---|---|---|---|
| `liq-sweep` | lookback 20, vol ×1.5 | lookback 10, ×1.2 | lookback 50, ×2.0 | Faster timeframes need tighter swings and lower multiplier to avoid missed sweeps. |
| `order-flow` | VMA 20, mult 500% | VMA 10, mult 300% | VMA 50, mult 700% | Lower multiplier on scalp = more frequent spikes; higher on swing = only strong participation. |
| `xau-trend` | EMA 9/21 | EMA 5/13 | EMA 21/55 | Classic fast/medium/slow EMA pairings. |
| `gold-divergence` | RSI 14 | RSI 7 | RSI 21 | Faster RSI for scalping, slower for swing. |

## 9. First quick wins without new code

The generic `--signals` extractor already produces agent-ready output for any loadable public script. Use it to validate the scripts before writing parsers:

```bash
./tvcli run PUB;b9372355c2e6483f952ca49a21d2ebbb \
  --symbol OANDA:XAUUSD --tf 5m --bars 100 --signals --agent --json
```

This will classify `Bullish_Sweep_Shape`/`Bearish_Sweep_Shape` as signals and emit events/levels immediately. If the generic output is enough for an agent pipeline, a hand-coded parser can be deferred.

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| `Mistab XAUUSD Strength Dashboard` and `XAUUSD Weekly Gap` time out even on `TV_TIER=ultimate` | Use existing `smc`/`ict` and `vgaps` skills instead; do not add heavy scripts to the registry. |
| `10x Bull vs Bear VP` is graphics-only and returns zero periods | Use existing numeric `vp` skill. |
| Unnamed `in_N` inputs in several scripts make CLI flags confusing | Only expose a small number of clearly named inputs per skill; rely on defaults for the rest. |
| Existing `--signals` may classify event shapes poorly | Add aliases to the parser or switch to a custom parser once the raw response is stable. |
| CLI binary: ensure `./tvcli` is used for all skill commands | All new skills are registered in `tvcli`; the older `./tv` binary does not support the skill framework. |

## 11. Suggested execution order

1. Implement **`liq-sweep`** first — it loads fast, has clear 0/1 event fields, and maps directly to a researched aspect.
2. Implement **`gold-divergence`** next — clear event flags + RSI metric.
3. Implement **`xau-trend`** — more numeric fields, slightly richer parser.
4. Implement **`order-flow`** — depends on interpreting `Buy/Sell/bell` fields; verify semantics with raw dump.
5. Add composite wrapper after the individual skills are stable.
6. Update relevant docs (see step 6 above).

## 12. Files to change

```text
new  internal/skill/parsers/liq_sweep.go
new  internal/skill/parsers/order_flow.go
new  internal/skill/parsers/xau_trend.go
new  internal/skill/parsers/gold_divergence.go
new  internal/skill/parsers/*_test.go              (optional but recommended)
new  internal/skill/parsers/testdata/*_fixture.json (test fixtures)
edit internal/cmd/help.go                         (add skills to help text)
edit Wiki/xauusd-skill-implementation-plan.md     (status/log)
edit skill-analysis/PINE_TO_SKILL_SYSTEM.md       (workflow notes, if needed)
new  Wiki/xauusd-stack.md                         (if composite skill is built)
```

---

**Next action:** pick the first skill (`liq-sweep`) and implement it end-to-end, then run `./tvcli liq-sweep --symbol OANDA:XAUUSD --tf 5m --preset scalping --agent --json` to confirm output shape.
