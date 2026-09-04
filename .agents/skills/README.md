# Workspace agent skills

This directory holds project-scoped agent skills (Agent Skills standard format)
for working with the `tvcli` TradingView Pine pipeline.

| Skill | Purpose |
|-------|---------|
| `tvcli` | TradingView Pine Script market analysis toolkit — 18 built-in indicator skills, consolidated XAUUSD Scalping Confluence Engine, async HTTP server, and progressive Pine Script reference docs. |
| `pine2tool` | Turn ANY Pine script (Pine ID or local `.pine`) into a reusable, structured analysis tool — download source, introspect inputs, run with custom inputs, extract signals/levels/graphics, and emit a registrable skill stub. |
| `tv-usecases` | Discover, develop, and orchestrate all major TradingView strategy/indicator use cases for agentic usage. Catalogs 12 use cases (scan, run, analyze, study management, sweeps, symbol/tf, visual, protocol capture, chart model, pine2tool, consolidated engine, HTTP server). Bridges bdg (live-chart) with tvcli (headless). Includes DSH tv-investigator headless invocation. |
| `tv-scout` | Progressively scout, instrument, and drive the live TradingView web chart for visual representation + vision-model confluence. |
| `tv-watch` | Watchtower agent protocol — act on market-watch trigger events (invocation packages, mission, armed specs). |
| `openknowledge` | Work with the Open Knowledge knowledge base connected to this repository and capture durable insights. |
| `codespace-prime-stack` | Operate, verify, and extend the bootstrapped DSH prime-orchestrator stack in the GitHub Codespace/devcontainer — dsh + dsh-prime-orchestrator plugin + prime-agent CLI with Cloudflare Workers AI models; the `bootstrapping/ansible/prime-stack.yml` playbook, the dsh Web GUI / Prime fleet column on port 3081, secrets contract, and the empirically verified gotchas. |
| `wundertrading` | WunderTrading execution-layer skill — classify a token's market regime (trend/chop/squeeze via public OHLCV), map it to a classic/DCA strategy config (TP ladders, stops, trailing, DCA sizing math), deploy and manage it through the wun MCP tools or HMAC-signed REST API, and prove reliability from exported strategy history before scaling. |
| `web-discovery` | Turn ANY web platform into programmable automation — investigate the live UI and network layer (XHR/REST/WS) with bdg+cloak, codify verified findings into reverse-engineered API docs, then forge reusable platform CLIs, agent skills, dsh plugin rows and fleet presets, improving them from usage feedback. |
| `grid-bot` | Create and configure a WunderTrading Grid bot for a token — screen the universe (mean-reversion/trend), derive a ready-to-apply Grid-bot config (grid type, ATR-band channel, ATR-derived Profit-per-GRID, grid count, sizing, Stop Trigger, Pump Protection) via repeatable scripts, then apply via web UI or webhook and verify on the reliability ladder. |
| `stealth-browser` | Stealth browser automation (vibheksoni/stealth-browser-mcp) — 97-tool MCP via dsh-mcp-client (Cloudflare/anti-bot bypass with real Chrome/nodriver/CDP, element cloning, network hooks, visual workflows). Usable by dsh + prime agents. |

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
