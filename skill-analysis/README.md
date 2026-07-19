# skill-analysis — Skill Reference Workspace

This directory holds reference material for the TradingView Pine Script skill commands in `tvcli`. It is designed to be used together with the Go code, not against the old JavaScript runners.

> **Authoritative sources now:**
> - Go skill layer: `internal/skill/`, `internal/skill/parsers/`
> - Generic schema-guided extractor: `pkg/pipeline/`, `internal/cmd/shared.go`
> - Captured reference payloads: `skill-analysis/dumps/<skill>/payload.json`

The historical JS runner files (`/Volumes/ExMac/code/tradingview/js-experiment06/*.cjs`) were used only as loose reference material during the original port. They are **not** the source of truth.

---

## Files

| File | Purpose |
|------|---------|
| [`PINE_RESPONSE_SKILL.md`](PINE_RESPONSE_SKILL.md) | **Start here.** Anatomy of a TradingView Pine run response (`periods`, `graphics`, `strategyReport`), how to read `metaInfo`/schema, which inputs matter, and the CLI commands that parse the response. |
| [`VOLUME_PROFILE_SKILL.md`](VOLUME_PROFILE_SKILL.md) | How the video method maps to the `vp` skill, the mapped Pine inputs, recommended timeframes/ranges, and a better numeric VP script for future upgrades. |
| [`PARSING_PROTOCOL_FOR_GO.md`](PARSING_PROTOCOL_FOR_GO.md) | Protocol reference for the `tv <skill>` commands, including the new `--signals` generic-extraction path. |
| [`SKILL_REFERENCE_INDEX.md`](SKILL_REFERENCE_INDEX.md) | Per-skill cross-reference of Pine IDs, Go parser files, captured payloads, and known discrepancies. |
| [`dumps/`](dumps/) | Captured reference payloads (`payload.json`) plus the JS runner `stdout.txt`, `stderr.txt`, and `help.txt` from the original port. Use `payload.json` as the target output; use `tv run ... --raw` to inspect actual period/graphics data. |
| [`meta/`](meta/) | Machine-readable metadata extracted from each skill. |
| `*.py` | Historical generator scripts that produced these docs and dumps from the JS runners. Kept for reference; they are not part of the Go build. |

---

## New preferred commands

The Go CLI now has a script-agnostic extraction path that works for any Pine script, registered skill or not:

```bash
# Generic agent-ready output for any public Pine script
./tvcli run "PUB;..." --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json

# Same generic path on a registered skill (bypasses the hand-coded parser)
./tvcli dvi --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json

# Inspect the raw period/graphics response
./tvcli run "PUB;..." --symbol BTCUSDT --tf 1h --bars 50 --raw --json

# Inspect the Pine schema (inputs, plots, styles, graphics flags)
./tvcli run "PUB;..." --symbol BTCUSDT --schema --json
```

Use these before editing a hand-coded parser. If `--signals --agent` already produces enough structure/series/levels/events, the custom parser can be left alone or removed.

---

## Notes

- **XAUUSD on weekends:** there are no bars. Use `BTCUSDT` for testing outside market hours.
- The captured dumps under `dumps/<skill>/stdout.txt` are the JS runner's output, not the raw TradingView period/graphics response. For the raw response, use `tv run ... --raw`.
