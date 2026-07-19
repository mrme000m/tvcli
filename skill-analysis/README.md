# skill-analysis — Skill Reference Workspace

Reference material for the TradingView Pine Script skill commands in `tvcli`.

## Files

| File | Purpose |
|------|---------|
| [`SKILLS.md`](SKILLS.md) | **Start here.** Skill framework architecture, adding new skills, raw response anatomy, schema usage, generic `--signals` extractor, and error codes. |
| [`PINE_RESPONSE_SKILL.md`](PINE_RESPONSE_SKILL.md) | Deep dive into the raw TradingView Pine run response: periods, graphics, strategy reports, metaInfo/schema, and which inputs matter. |
| [`PINE_TO_SKILL_SYSTEM.md`](PINE_TO_SKILL_SYSTEM.md) | The universal Pine-to-skill system: how `--signals --agent` works for any Pine Script, when to use it vs a custom parser, and how to register new skills. |
| [`VOLUME_PROFILE_SKILL.md`](VOLUME_PROFILE_SKILL.md) | Volume Profile skill: the video method, mapped Pine inputs, presets, and the numeric VP script. |
| [`XAUUSD_INDICATOR_STACK.md`](XAUUSD_INDICATOR_STACK.md) | Research into 7 public Pine Scripts for XAUUSD analysis (volume profile, liquidity, market structure, order flow, trend, price action, gaps). |
| [`pplx-research/`](pplx-research/) | Perplexity research output for XAUUSD scripts and YouTube tutorials. |

## Source of Truth

- **Go skill layer:** `internal/skill/`, `internal/skill/parsers/`
- **Generic extractor:** `pkg/pipeline/`
- **Live raw response:** `./tvcli run <pine-id> --raw-out my.raw.json`
- **Skill registry listing:** `./tvcli skills --json`

## Quick Commands

```bash
# Generic agent-ready output for any public Pine script
./tvcli run "PUB;..." --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json

# Same on a registered skill (bypasses custom parser)
./tvcli dvi --symbol BTCUSDT --tf 1h --bars 50 --signals --agent --json

# Inspect raw period/graphics response
./tvcli run "PUB;..." --symbol BTCUSDT --tf 1h --bars 50 --raw --json

# Inspect Pine schema
./tvcli run "PUB;..." --symbol BTCUSDT --schema --json
```

Use these before editing a hand-coded parser. If `--signals --agent` already produces enough structure, the custom parser can be left alone or removed.

## Notes

- **XAUUSD on weekends:** no bars. Use `BTCUSDT` for testing outside market hours.
- Raw TradingView data is best captured live with `tv run ... --raw`.
