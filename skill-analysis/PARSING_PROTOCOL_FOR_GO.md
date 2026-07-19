# TradingView Skill Runner — Parsing Protocol for Go

Reference for implementing / fixing the Go skill commands. The authoritative parsing logic lives in the Go parser package (`internal/skill/parsers/`); raw TradingView response samples are in `skill-analysis/dumps/<skill>/`.

## TL;DR

Run a skill through the Go CLI:

```bash
./tvcli <skill> <SYMBOL> --tf <timeframe> --json --agent
```

The Go skill runner in `internal/cmd/skillcmd.go` builds a `service.RunRequest`, calls `service.RunScript`, and produces either human text or JSON. The per-skill parser in `internal/skill/parsers/<skill>.go` turns TradingView's raw periods/graphics/strategy-report data into the agent-ready payload.

To inspect raw TradingView responses, look at `skill-analysis/dumps/<skill>/stdout.txt`, `stderr.txt`, and `payload.json`. These dumps were captured from live runs and are the reference for what the parser should produce.

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

Domain-specific keys live alongside this envelope. See each skill's payload dump under `dumps/<skill>/payload.json` and the key list in `SKILL_REFERENCE_INDEX.md`.

## Routing / skill identification

The Go skill registry in `internal/skill/registry.go` maps command names (`tv <name>`) to `Skill` descriptors. Each descriptor carries:

- `Name` — command name (e.g. `smc`)
- `PineID` — public Pine Script ID used to load the script
- `Workflow` — stable workflow tag for agent dispatch
- `ParseOutput` — the Go function that produces the payload

Use `agentContext.workflow` as the canonical dispatcher; it matches the **Workflow ID** column in `SKILL_REFERENCE_INDEX.md`.

## Inputs / overrides

Every skill command supports:

| CLI flag | Meaning |
|----------|---------|
| `--symbol <SYMBOL>` | Trading symbol (default `BTCUSDT`) |
| `--tf <timeframe>` | `1m`, `5m`, `15m`, `1h`, `4h`, `1D`, etc. |
| `--bars <n>` | Number of historical bars |
| `--input key=value` | Override one Pine input by friendly variable name |
| `--preset <name>` | Use a bundled preset (skills with presets) |
| `--json` | Pretty-printed full JSON |
| `--agent` | Compact agent-mode JSON |
| `--help` | Skill-specific help |

The friendly input names and their `tvInputId` mappings are listed per skill in `SKILL_REFERENCE_INDEX.md` **Input Map**.

## Errors to expect

| Exit code | Meaning |
|-----------|---------|
| 0 | Success (always check parsed payload for `status`/`error`) |
| 1 | Critical error (auth, connection) |
| 2 | No data returned |
| 3 | Timeout / cancelled / validation |
| 4 | Validation error (e.g. invalid pine id, bad symbol) |

Some skills may return a JSON error object instead of a non-zero exit code; always inspect the parsed payload.

## Files in this reference package

- `SKILL_REFERENCE_INDEX.md` — table + per-skill details, sample commands, payload keys
- `meta/*.json` — machine-readable metadata per skill
- `dumps/<skill>/stdout.txt` — captured raw stdout of a sample run
- `dumps/<skill>/stderr.txt` — captured stderr of a sample run
- `dumps/<skill>/help.txt` — captured `--help` output
- `dumps/<skill>/payload.json` — parsed JSON reference payload
- `analyze_skills.py` — the script that produced the dumps and metadata

## Source of truth

- Go skill layer: `internal/skill/`, `internal/skill/parsers/`, `internal/cmd/skillcmd.go`, `internal/service/run.go`
- Captured raw response dumps: `skill-analysis/dumps/`
- Machine-readable metadata: `skill-analysis/meta/`
- Historical JS runners (loose reference only): `/Volumes/ExMac/code/tradingview/js-experiment06/*.cjs`
