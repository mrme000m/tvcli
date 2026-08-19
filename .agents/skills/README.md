# Workspace agent skills

This directory holds project-scoped agent skills (Agent Skills standard format)
for working with the `tvcli` TradingView Pine pipeline.

| Skill | Purpose |
|-------|---------|
| `tvcli` | TradingView Pine Script market analysis toolkit — 18 built-in indicator skills, consolidated XAUUSD Scalping Confluence Engine, async HTTP server, and progressive Pine Script reference docs. |
| `pine2tool` | Turn ANY Pine script (Pine ID or local `.pine`) into a reusable, structured analysis tool — download source, introspect inputs, run with custom inputs, extract signals/levels/graphics, and emit a registrable skill stub. |
| `openknowledge` | Work with the Open Knowledge knowledge base connected to this repository and capture durable insights. |

## Packaging

The workspace is packaged as a [Pi package](https://pi.dev/docs/latest/packages)
via `package.json` at the repo root. The `pi.skills` key points to the skill
directories above. Skills follow the [Agent Skills standard](https://agentskills.io/specification).

### Install in Pi

```bash
# From local path
pi install /Volumes/ExMac/code/tradingview/go

# From git
pi install git:github.com/mrme000m/tvcli-skills
```

### Use with any Agent Skills-compatible harness

Skills in `.agents/skills/` are auto-discovered by harnesses that support
the Agent Skills standard (Pi, OpenCode, DSH, etc.). Point your harness to
the `.agents/skills/` directory.

Harnesses without native `.agents/skills/` support are covered by symlinks:

- **Claude Code** (reads `.claude/skills/`): run `scripts/link-skills.sh`
  to create/update `.claude/skills/*` symlinks and prune stale ones.
- **Codex / Gemini CLI** (read global `~/.agents/skills/` only):
  `ln -s "$(pwd)/.agents/skills/"* ~/.agents/skills/`
