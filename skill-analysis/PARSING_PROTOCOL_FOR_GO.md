# TradingView Skill Runner — Parsing Protocol for Go

Reference for implementing / fixing the Go skill commands. The authoritative parsing logic lives in:

- `internal/skill/parsers/` — hand-coded per-skill parsers
- `pkg/pipeline/` — generic schema-guided extractor (preferred for new scripts)

For the shape of the raw TradingView response, see [`PINE_RESPONSE_SKILL.md`](PINE_RESPONSE_SKILL.md).

---

## Two ways to run a skill command

### 1. Hand-coded parser (default)

```bash
./tvcli <skill> <SYMBOL> --tf <timeframe> --json --agent
```

The Go skill runner in `internal/cmd/skillcmd.go` builds a `service.RunRequest`, calls `service.RunScript`, and invokes the per-skill `ParseOutput` in `internal/skill/parsers/<skill>.go`. This is the original path and is useful when the script needs custom graphic/table extraction that the generic layer cannot yet handle.

### 2. Generic schema-guided extractor (new)

```bash
# Registered skill
./tvcli <skill> <SYMBOL> --tf <timeframe> --signals --agent --json

# Any public Pine script
./tvcli run <pine-id> <SYMBOL> --tf <timeframe> --signals --agent --json
```

This bypasses the hand-coded parser. It uses Pine `metaInfo` to rename `plot_N` keys, classify values (price / signal / metric / band / style / noise), and emit a standard `Signals` object plus an `agent-ready-v2` envelope.

Use this path first when a skill parser is broken or when onboarding a new Pine script. See [`PINE_RESPONSE_SKILL.md`](PINE_RESPONSE_SKILL.md) for the underlying mechanics.

---

## How to inspect the raw response

The captured `dumps/<skill>/stdout.txt` files are the **JS runner's stdout**, not the actual TradingView period/graphics data. To see the real raw response for any script:

```bash
./tvcli run <pine-id> --symbol BTCUSDT --tf 1h --bars 50 --raw --json
./tvcli run <pine-id> --symbol BTCUSDT --tf 1h --bars 50 --raw-out my.raw.json
```

The raw JSON contains:

```json
{
  "pineId": "PUB;...",
  "symbol": "...",
  "timeframe": "...",
  "bars": 50,
  "periodCount": 150,
  "periods": [ /* bar-by-bar plot/candle values */ ],
  "graphic": { /* drawing primitives */ },
  "strategyReport": { /* strategies only */ }
}
```

---

## Payload structure

Every agent payload contains a common envelope plus skill-specific domain keys.

Common envelope:

```json
{
  "status": "ok",
  "exitCode": 0,
  "timestamp": "...",
  "execution": { "durationMs": ..., "attempts": ... },
  "agentContext": { "workflow": "...", "symbol": "...", "timeframe": "..." },
  "schemaVersion": "agent-ready-v2.0.0"
}
```

Domain-specific keys live alongside this envelope. With the generic `--signals` path, `structure` contains:

```json
{
  "classifications": { "Support": "price", "Momentum_ROC": "metric", ... },
  "last": { /* most recent closed-bar values */ },
  "series": [ /* last 50 chronological bars */ ],
  "levels": [ /* support/resistance/band levels */ ],
  "events": [ /* buy/sell/alert events from signal plots and labels */ ],
  "graphicCounts": { "dwgboxes": 12, "dwglabels": 4 },
  "meta": { "pineId": "...", "periodCount": 150 }
}
```

See each skill's captured payload under `dumps/<skill>/payload.json` and the key list in `SKILL_REFERENCE_INDEX.md`.

---

## Routing / skill identification

The Go skill registry in `internal/skill/registry.go` maps command names (`tv <name>`) to `Skill` descriptors:

- `Name` — command name (e.g. `smc`)
- `PineID` — public Pine Script ID
- `Workflow` — stable workflow tag
- `ParseOutput` — the Go function that produces the payload

The generic `--signals` path does not use the registry; it works from `schema.PineSchema` instead. Use `agentContext.workflow` to identify which skill/workflow produced a payload.

---

## Inputs / overrides

Every skill command supports:

| CLI flag | Meaning |
|----------|---------|
| `--symbol <SYMBOL>` | Trading symbol (default `OANDA:XAUUSD` for skills) |
| `--tf <timeframe>` | `1m`, `5m`, `15m`, `1h`, `4h`, `1D`, etc. |
| `--bars <n>` | Number of historical bars |
| `--input key=value` | Override one Pine input by friendly variable name |
| `--preset <name>` | Use a bundled preset (skills with presets) |
| `--json` | Pretty-printed full JSON |
| `--agent` | Agent-mode JSON envelope |
| `--signals` | Use generic schema-guided signal extractor |
| `--raw` / `--raw-out` | Dump raw periods + graphic |
| `--help` | Skill-specific help |

For deciding which inputs actually affect the response (vs. cosmetic color options), see the input-filtering section of [`PINE_RESPONSE_SKILL.md`](PINE_RESPONSE_SKILL.md).

---

## Errors to expect

| Exit code | Meaning |
|-----------|---------|
| 0 | Success (always check parsed payload for `status`/`error`) |
| 1 | Critical error (auth, connection) |
| 2 | No data returned |
| 3 | Timeout / cancelled / validation |
| 4 | Validation error (e.g. invalid pine id, bad symbol) |

Some skills may return a JSON error object instead of a non-zero exit code; always inspect the parsed payload.

---

## Files in this reference package

- [`PINE_RESPONSE_SKILL.md`](PINE_RESPONSE_SKILL.md) — raw response anatomy, schema usage, input filtering
- [`SKILL_REFERENCE_INDEX.md`](SKILL_REFERENCE_INDEX.md) — per-skill table, parser files, payload keys
- [`README.md`](README.md) — workspace overview
- `dumps/<skill>/payload.json` — captured reference agent payloads
- `dumps/<skill>/stdout.txt` — historical JS runner stdout (reference only)
- `meta/*.json` — machine-readable metadata per skill

## Source of truth

- Go skill layer: `internal/skill/`, `internal/skill/parsers/`, `internal/cmd/skillcmd.go`, `internal/service/run.go`
- Generic extractor: `pkg/pipeline/`, `internal/cmd/shared.go`
- Captured reference payloads: `skill-analysis/dumps/`
- Historical JS runners (loose reference only): `/Volumes/ExMac/code/tradingview/js-experiment06/*.cjs`
