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

## How the universal analyzer handles ANY script's graphics

The universal analyzer uses a **generic, topology-based approach** (in
`internal/agent/graphics_generic.go`) that requires **no per-script matchers**.
Instead of hand-coding patterns for each indicator, it:

1. **Normalizes color encoding** — detects whether `bc`/`ci` are small indices
   (5,6,7) or full RGBA integers (4278190085) by analyzing the value
   distribution, so downstream logic works regardless of how a script encodes
   colors.

2. **Groups boxes by geometric topology** — clusters boxes by:
   - **Shared left edge** → volume-profile stacks (POC = widest, VAH/VAL =
     stack top/bottom). This works for any script that draws volume bars.
   - **Narrow width (1-3 bars)** → FVG/gap boxes. Confidence boosted if text
     contains `%` signs.
   - **Wide extension (x2 >> x1, x2 = last bar)** → active order-block zones.
   - **Remaining** → generic price zones.

3. **Groups lines by geometry** — classifies as:
   - **Vertical (x1 ≈ x2)** → session/sweep markers.
   - **Horizontal dashed + extend right** → liquidity/breaker levels.
   - **Horizontal solid** → support/resistance.
   - **Sloped** → trendlines (up/down).

4. **Associates boxes with their bounding lines** — matches box top/bottom
   Y-coordinates to line Y-coordinates with X-overlap, so a box flanked by
   dashed extended lines is correctly classified as a **breaker block**.

5. **Groups labels by text content** — normalizes text and maps to semantic
   types (buy/sell/BOS/CHOCH/liquidity_sweep/POC/VAH/VAL/etc.).

6. **Prevents double-classification** — boxes claimed by a higher-priority
   group (e.g., volume_profile) are excluded from lower-priority groups
   (FVG, order_block, zone).

### Architecture: two-layer design

| Layer | Location | Role |
|-------|----------|------|
| **Layer 1: flat signal extraction** | `pkg/pipeline/extract.go` | Universal handlers for every TV draw type (dwglabels→events, dwglines→levels, dwgboxes→S/R levels, hhists→volume bins, dwgtables→grids). Zero script-specific code. |
| **Layer 2: structural topology analysis** | `internal/agent/graphics_generic.go` | Groups graphic elements by geometric topology and infers semantics from group properties — not from script-specific layouts. |

Both layers run automatically for every `tv analyze` and `tv eval --agent` call.
Per-script parsers in `internal/skill/parsers/` remain only for **registered
skills** where exact Pine field names and plot semantics are known.

### Inspecting and extending

Inspect `<slug>.raw.json` for the raw `graphic` map and `periods`. The
generic analyzer's topology logic lives in `internal/agent/graphics_generic.go`.
The old per-script matchers in `graphics_ext.go` are preserved as documentation
but no longer called — `postProcessGraphics` routes to
`postProcessGraphicsGeneric`.

If a new script's layout produces incorrect classifications, the right fix is
to add a **topology rule** in `buildBoxTopology` / `buildLineTopology` (e.g., a
new geometric grouping criterion), not a per-script matcher. This keeps the
approach generic: any script's graphics are analyzed by the same universal
topology rules, regardless of how they arrange boxes, lines, and labels.

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
