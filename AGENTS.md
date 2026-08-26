# AGENTS.md — tvcli workspace orientation

This repository is a **TradingView Pine Script market-analysis toolkit** built
around the `tvcli` Go CLI. It is packaged for agent consumption two ways:

1. **Agent Skills** (agentskills.io standard) in `.agents/skills/` — loaded
   on demand by any compatible harness (Pi, Claude Code, Codex, DSH, etc.):

   | Skill | Use it for |
   |-------|-----------|
   | `tvcli` | Running any of the 19 built-in indicator skills (incl. `vp-pro`), `eval`/`run` of arbitrary Pine source, the async HTTP server (`serve --daemon`, `:8765`), free-tier limits, and progressive Pine Script v5 reference docs under `references/pinescript/`. |
   | `pine2tool` | Turning any `PUB;…`/`USER;…` ID or local `.pine` file into a reusable structured analysis tool + registrable skill stub. |
   | `tv-usecases` | Discover, develop, and orchestrate all major TradingView strategy/indicator use cases for agentic usage. Bridges bdg (live-chart investigation) with tvcli (headless execution). Catalogs 12 use cases: scan, run, analyze, study management, parameter sweeps, symbol/timeframe, visual representation, protocol capture, chart model reading, pine2tool, consolidated engine, HTTP server. Includes DSH tv-investigator headless invocation. |
   | `openknowledge` | Reading/writing the `Wiki/` knowledge base via the `openknowledge` CLI. |

2. **Pi package** — `package.json` at the root carries the `pi-package`
   keyword and a `pi.skills` manifest, so `pi install <this repo>` (local path
   or `git:` URL) installs all three skills in one step.

## Ground rules for agents

- Build before use: `go build -o tvcli ./cmd/tvcli` from this root.
- Auth lives in `.env` (`SESSION`, `SIGNATURE`, `TV_USER`, `DEVICE_T`,
  `TV_TIER`) — never commit it, never print cookie values.
- Prefer `xau-scalp` (consolidated script) over running indicators one by one:
  free tier allows only 2 studies per chart; the consolidated engine returns
  the full composite in a single run.
- Use `--json --agent` for machine-readable envelopes; `--raw` only for
  debugging.
- Reference docs for Pine v5 live in
  `.agents/skills/tvcli/references/pinescript/` — consult them before writing
  or debugging Pine instead of relying on memory.
- Data dumps (`*.csv`, `rawdumps/`, `skill-runs/`, `skill_work/`) are local
  working artifacts, not source.
