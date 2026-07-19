# TradingView Skill Command Reference (auto-generated)

This workspace documents the TradingView skill commands implemented in Go. Use it when fixing, extending, or verifying the corresponding `tv <name>` commands.

## Quick links

- **Per-skill index, Pine IDs, parser locations, and sample payloads:**
  [`skill-analysis/SKILL_REFERENCE_INDEX.md`](skill-analysis/SKILL_REFERENCE_INDEX.md)
- **How the Go skill layer parses raw TradingView responses:**
  [`skill-analysis/PARSING_PROTOCOL_FOR_GO.md`](skill-analysis/PARSING_PROTOCOL_FOR_GO.md)
- **Captured raw response dumps + parsed payloads:**
  [`skill-analysis/dumps/`](skill-analysis/dumps/)
- **Machine-readable metadata per skill:**
  [`skill-analysis/meta/`](skill-analysis/meta/)

## How it was built

1. Enumerated every Go skill registered in `internal/skill/parsers/`.
2. For each skill, captured its Pine Script ID, friendly name, inputs, presets, and workflow ID from the Go source.
3. Captured raw TradingView responses (periods, graphics, strategy reports) for sample runs and stored them under `skill-analysis/dumps/`.
4. Extracted the resulting JSON payloads and generated `SKILL_REFERENCE_INDEX.md` + `PARSING_PROTOCOL_FOR_GO.md` + structured JSON metadata under `skill-analysis/meta/`.
5. The historical JS runner files (`/Volumes/ExMac/code/tradingview/js-experiment06/*.cjs`) were used only as loose reference material during the initial port; the Go source and the captured raw dumps are now the source of truth.

## Regenerating

```bash
cd /Volumes/ExMac/code/tradingview/go
python3 skill-analysis/analyze_skills.py          # use cached meta where available
python3 skill-analysis/analyze_skills.py --force  # re-run every live sample
```

## Summary snapshot

| Skill | Workflow | Historical JS runner | Payload? |
|-------|----------|----------------------|----------|
| anchored-clusters-vp | anchored-clusters-vp | `anchored-clusters-vp.cjs` | ✅ |
| buying-selling-volume | buying-selling-volume | `buying-selling-volume.cjs` | ✅ |
| delta-volume-intensity | trend-following-sr-break | `delta-volume-intensity.cjs` | ✅ |
| ema-atr-pro-engine | ema-atr-structure | `ema-atr-pro-engine.cjs` | ✅ |
| generic-indicator | generic-indicator-analysis | `generic-indicator.cjs` | ✅ |
| golden-rule-strategy | golden-rule-strategy | `golden-rule-strategy.cjs` | ✅ |
| ict-auto-validated-smc | ict-smc-structure | `ict-auto-validated-smc.cjs` | ✅ |
| nlm-cli-skill | N/A | N/A | ❌ |
| precision-sniper | ema-confluence-sniper | `precision-sniper.cjs` | ✅ |
| quantum-ribbon | quantum-ribbon | `quantum-ribbon.cjs` | ✅ |
| self-aware-trend-system | adaptive-supertrend-quality | `self-aware-trend-system.cjs` | ✅ |
| shemar-smc-confidence | shemar-smc-confidence | `shemar-smc-confidence.cjs` | ✅ |
| smart-money-concepts | smart-money-concepts | `smart-money-concepts.cjs` | ✅ |
| support-resistance-breaks | support-resistance-breaks | `support-resistance-breaks.cjs` | ✅ |
| swingarm-atr-trend-indicator | N/A | `swingarm-atr-trend-indicator.cjs` | ✅ |
| tv-cron-orchestrator | N/A | `tv-cron-orchestrator/scripts/...cjs` | ✅ |
| tv-indicator | N/A | `tv-indicator/scripts/tvcli.js` | ⚠️ error JSON |
| ultra-sensitive-supertrend | ultra-sensitive-supertrend | `ultra-sensitive-supertrend.cjs` | ✅ |
| volume-gaps-imbalances-zeiierman | trend-following-gap-rejection | `volume-gaps-imbalances-zeiierman.cjs` | ✅ |
| xauusd-mtf-trend | xauusd-mtf-trend | `xauusd-mtf-trend.cjs` | ✅ |
| youtube-to-tv-pine | N/A | `youtube-to-tv-pine/scripts/...cjs` | ❌ |

See the full index for input maps, payload schemas, sample commands, and raw dumps.
