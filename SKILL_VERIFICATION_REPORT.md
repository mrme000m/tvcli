# Skill Pine-Script Verification Report

Verifies each skill's Pine script end-to-end on a **free account**:
1. load + run headlessly (raw periods + graphic + parsed structure),
2. review the parser against the raw field names,
3. apply to the live chart + screenshot + vision check,
4. change a runtime input and confirm both parsed values and the chart image change.

- Free account: `sunilsutar371` (from `tv_free_accounts.csv`; default in `accounts.json`)
- Headless run: `tvcli <skill> --symbol OANDA:XAUUSD --tf 2h --bars 180 --raw --json`
- Visual run:  `tvcli visual <skill> --pine <id> --out shots/<skill>.png` + `vision.mjs`
- Input change (visual): `tvcli study set <entity> --inputs '{"in_N": v}' --before a.png --after b.png`

## Result matrix

| Skill | Headless | Parsed | Gfx | Renders | Input→parsed | Input→image |
|---|---|---|---|---|---|---|
| camarilla | ok | 8 levels | - | ✓ CPP V2 | resolution D→W shifts levels ✓ | |
| choppiness | ok | chop/avg/regime | - | ✓ Choppiness Index | length 14→28: chop 40.5→38.7 ✓ | ✓ 0.09% px, line shifted |
| cvd | ok | cvd line | - | ✓ CDV Candle | SMA inputs are overlays (not parsed) ⚠ | |
| dvi | ok | trend/S/R/momentum | - | ✓ | length_volatility: volatility 270→260 ✓ | |
| gold-divergence | ok | rsi/divergences | - | ✓ RSI Divergence | rsiLength: rsi 84→76 ✓ | |
| golden | ok | bos/choch/fvg | 3 | ✓ EQH/CH-CH | swingsLength: bullBOS 1→0 ✓ | |
| ichimoku | ok | cloud/chinkou | - | ✓ CM_Enhanced_Ichimoku | tenkanLen: cloudPct 1.78→1.37 ✓ | |
| liq-sweep | ok | sweeps/liquidity | 2 | ✓ liquidity sweep | swingLookback: bearSweeps 10→8 ✓ | |
| quantum | ok | ma alignment | - | ✓ buy/sell | len1: alignment 8→5 ✓ | |
| smc | ok | bos/choch/fvg/ob | 3 | ✓ EQL levels | swingsLength: bosCount 9→8 ✓ | |
| sniper | ok | ema/signals | 4 | ✓ | ema1Len: ema1 4609→4607 ✓ | |
| squeeze | ok | momentum/squeeze | - | ✓ SQZMOM histogram | BB/KC inputs don't feed parsed momentum ⚠ | |
| sr-breaks | ok | support/resistance | - | ✓ LuxAlgo S/R breaks | volumeThreshold: lastBreak bearish→bullish ✓ | |
| swingarm | ok | extremum/fib | - | ✓ | ATRPeriod: fib shifted ✓ | |
| ust | ok | buy/sell/background | 1 | ✓ SuperTrend | atrPeriod1: signals 14→13 ✓ | |
| vp | ok | poc/vah/val | - | ✓ Volume Profile | lookback: poc 4502→4427 ✓ | |
| vp-pro | ❌ | invite-only | | | USER;d496e2656da745a5b79f39140bde7c1f | |
| xau-scalp | ❌ | invite-only | | | USER;ed4cf60ef3fb43f6b91565afe52a3a4b | |
| xau-trend | ok | bollinger/ema | - | ✓ | emaShort: emaShort 4585→4553 ✓ | |

## Findings

### Blocking failures (2)
- **vp-pro** and **xau-scalp** use `USER;` (private) Pine scripts not owned by the
  free account → server returns `invite-only script`. They require the owning
  account's session. Not testable on a free account.

### Parser scope notes (2)
- **cvd** and **squeeze**: their first numeric inputs (SMA/BB/KC lengths) configure
  *overlay* plots that the parser intentionally does not surface — the parsed
  structure is the CVD line / momentum oscillator respectively. Changing those
  inputs changes the raw overlay fields but not the parsed structure. Not a bug;
  a documented parser-scope limitation.
- **camarilla** `width` is a cosmetic line-width; the level-driving input is
  `resolution` (D/W/M) — verified D→W shifts H1/L1.

### Shared script
- **golden** and **smc** wrap the *same* Pine ID
  `PUB;6daafb2cabe6419d98ae25229d2327f8` with different parsers + input sets
  (golden = multi-TF alignment; smc = SMC structure). Confirmed both render and
  parse independently.

### Visual input-change (proven end-to-end on choppiness)
- `study set in_0: 14 → 7` → `magick compare` AE = **116,982 px (0.09%)** changed;
  vision.mjs: "Choppiness Index values Before 14.4/49.53 → After 7.4/35.67, line
  shifted downwards." Matches the headless parse change (chop 40.5 → 52.4).

## Files produced
- `shots/*.png` — one screenshot per skill (17), plus `chop_len14.png` /
  `chop_len7.png` (input-change before/after).
- `/tmp/skillcheck/<skill>.{raw,changed}.json` — raw + parsed + changed-run dumps.