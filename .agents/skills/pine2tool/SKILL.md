---
name: pine2tool
description: >
  Turn ANY TradingView Pine Script (indicator or strategy) into a reusable,
  structured analysis tool — no hand-written parser required. Use this skill
  whenever the user wants to take a public/private Pine script ID or a local
  .pine source file and get agent-ready signals, levels and graphics out of it
  (order blocks, fair-value gaps, volume-profile POC/VAH/VAL, liquidity,
  session markers, strategy metrics), with optional custom Pine inputs applied.
  Also produces a drop-in skill definition so the script can be re-registered
  and invoked as a first-class tvcli skill.
---

# pine2tool — Any Pine Script → Analysis Tool

The project ships `tvcli`, a Go CLI that compiles, runs and extracts structured
output from arbitrary TradingView Pine scripts (WebSocket + HTTP APIs). This
skill wraps that runtime so you can turn *any* script into an analysis tool
correctly and efficiently: fetch source → introspect inputs → run with the
inputs → extract semantics → emit an agent-readable result **and** a reusable
skill stub.

## When to use

- User gives a `PUB;…` / `USER;…` Pine ID and wants it analyzed.
- User has a local `.pine` file and wants it run/analyzed.
- User wants a script converted into a first-class `tvcli` skill.
- You need to see a script's raw output to build/extend an extractor.

## Prerequisites

- `.env` with `SESSION`, `SIGNATURE`, `DEVICE_T` (and `TV_USER` for writes).
- A built binary: `go build -o tvcli ./cmd/tvcli` (from `/Volumes/ExMac/code/tradingview/go`).
- `TV_TIER` matching the account to cap bars/studies appropriately.

## Quick workflow (the correct order)

Use the bundled orchestrator so resumable, raw artifacts land on disk:

```bash
# 1. From a Pine ID  — download source, list inputs, run+analyze, emit skill
./.agents/skills/pine2tool/bin/pine2tool.sh \
     "PUB;3b3ebba156574e058cc8ae73dc5c7fa2" \
     --symbol BINANCE:BTCUSDT --tf 1H \
     --input "in_1=4,in_0=3" \
     --out skill_work/p2t_vpob

# 2. From a local .pine source file (no pre-published ID needed)
./.agents/skills/pine2tool/bin/pine2tool.sh \
     .tv-scripts/35--smc-visuals-....pine \
     --symbol BINANCE:BTCUSDT --tf 1H --out skill_work/p2t_smc
```

Each run writes, under `--out` (or `skill_work/pine2tool_<slug>`):

| Artifact | Contents |
|----------|----------|
| `<slug>.json` | Agent-ready v2 envelope (market, signals, levels, graphics, summary) |
| `<slug>.inputs.json` | The script's canonical Pine input IDs/types/defaults from metaInfo |
| `<slug>.source.pine` | The downloaded Pine source (if a Pine ID was given) |
| `<slug>.raw.json` | Raw uncompressed periods + graphic map + strategy report |
| `<slug>.SKILL.md` | A reusable skill doc describing this script as an analysis tool |
| `<slug>.skill.yaml` | A registrable skill definition (name, pineId, inputs, presets) |

> **Output shape:** from a **Pine ID** the tool runs `tv analyze`, whose JSON
> nests the agent-ready-v2 envelope under `.agent` and adds convenience sections
> `.script`, `.market`, `.signals`, `.graphic` and `.summary` (levels, patterns,
> risk metrics, POC/VAH/VAL, zones). From a **local .pine** it runs
> `tv eval --signals --agent --json`, which emits the same envelope at the **top
> level** (`Status / AgentContext / Market / Structure / Opportunities /
> Narrative / Conformance / SchemaVersion`) — identical to the skill command
> output. Raw `periods`+`graphic` and canonical schema inputs are always in
> `<slug>.raw.json` / `<slug>.inputs.json` regardless of source.

## Custom inputs — spellings that all work now

tvcli accepts input overrides in several equivalent spellings (all reassembled
by `internal/cmd/inputs_util.go` and resolved to the correct TV input ID by
name/index via `PineIndicator.SetOption`). Custom inputs have been **verified**
to change the graphic output at runtime — e.g., increasing "Show Last Bullish
OB" from 3 to 5 on LuxAlgo Order Blocks increased box count from 8 to 11 and
line count from 16 to 22, confirming the inputs reach the TradingView runtime
and alter the indicator's drawing output:

| Spelling | Example |
|----------|---------|
| `--input in_1=4` (or comma list) | `--input "in_1=4,in_0=3"` |
| `--input.<name-or-id>=VALUE` | `--input.lookback=50` |
| positional `key=value` after the spine | `tvcli eval s.pine amount_of_boxes=8` |
| raw TV ID flag | `tvcli eval s.pine --in_1=4` |

To discover the canonical IDs first:

```bash
tvcli inputs "PUB;3b3ebba156574e058cc8ae73dc5c7fa2" --raw --json
tvcli analyze --list-inputs "PUB;3b3ebba156574e058cc8ae73dc5c7fa2" --json
```

## Searching for a script

```bash
tvcli search "volume profile order blocks" --limit 10 --json
tvcli top --limit 50 --output top.json
```

## Extending the extractor after a run

Inspect `<slug>.raw.json` — it contains the real `graphic` map (`dwgboxes`,
`dwglines`, `dwglinefills`, `dwglabels`) and `periods`. The universal analyzer's
deep-graphics recovery lives in `internal/agent/graphics_ext.go` and currently
recovers, from the drawing layer alone:

- **Volume-profile POC/VAH/VAL** — boxes sharing a left edge, stacked, X-extent ∝ volume.
- **Order-block zones** — `dwglinefills` bounded by two horizontal rails.
- **Session markers** — vertical lines (`x1 == x2`).
- **FVG / OB / liquidity / buy-sell** from boxes, lines and label text.

If a new script layout isn't captured, add a matcher in `graphics_ext.go`
(`findVolumeProfilePeaks` / `findLineFillZones`) with the observed raw keys,
then re-run and compare `<slug>.json`.

## Registering the script as a reusable tvcli skill

Matrix: `docs/skills/<name>.md` (human doc) + a `skill.Skill` registration in
`internal/skill/registry.go` (name, PineID, InputDef slice keyed by TVInputID,
optional presets). The emitted `<slug>.skill.yaml` has the skeleton to fill in.
After registering, the script becomes `tvcli <name> --symbol … --tf …`.

## Gotchas

- Free/essential tiers cap bars (e.g. 365) and study count; the CLI auto-caps
  but expect `study limit` warnings on busy accounts — pass `--force-cleanup`.
- Some scripts (volume profile, order blocks) emit **no plot columns**; all the
  value is in the graphics. Use `analyze` (not just `--signals`) so the graphic
  layer is inspected. `analyze` also falls back to OHLCV/graphics for last price.
- Boolean/show toggles can be *declared but unused* in the Pine source; changing
  them is a no-op. Verify by toggling and comparing graphic counts.
