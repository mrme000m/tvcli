# Skill Improve Loop

Runs the `tvcli.skill-improver` subagent against every skill in `.agents/skills/`.

## Run it

Paste this tool call into the parent Pi session (current working directory must be the repo root):

```typescript
subagent({
  workflowScript: `
    const mode = "improve"; // set to "audit" to report without edits

    // Skill directories under .agents/skills/. Add new skills here.
    const skillNames = ["openknowledge", "pine2tool", "tvcli"];

    const results = await runs.all(skillNames.map(name => ({
      key: name,
      agent: "tvcli.skill-improver",
      task: \`Audit and improve one skill.\\n\\nskillPath: .agents/skills/\${name}\\nrepoRoot: .\\nmode: \${mode}\`,
      output: \`skill-runs/\${name}-\${mode}.md\`
    })));

    return {
      total: skillNames.length,
      skills: skillNames,
      completed: results.filter(r => r.ok).length,
      failed: results.filter(r => !r.ok).length,
      reports: results.map(r => ({ skill: r.key, ok: r.ok, output: r.output }))
    };
  `,
  async: true
})
```

Then wait for completion with `subagent_wait()` or subscribe with `subagent_wait({ id: "...", nonBlocking: true })`.

## What it does

1. Runs one `tvcli.skill-improver` child per skill listed in `skillNames` (parallel).
2. Each child reads the skill, runs safe `--help` / build checks, writes an audit report to `skill-runs/<skill>-audit.md`, and (in `improve` mode) applies safe fixes.
3. Aggregates a completion summary.

Add a new skill by appending its directory name to the `skillNames` array.

## Outputs

- `skill-runs/<skill>-audit.md` — per-skill audit + change log (written by the child)
- `skill-runs/<skill>-<mode>.md` — child handoff summary (workflow output, e.g. `tvcli-improve.md`)

## Switching to audit-only

Change `const mode = "improve";` to `const mode = "audit";` in the workflow script.

## Notes

- Uses the project agent default model (`cloudflare-workers-ai/@cf/moonshotai/kimi-k2.7-code`). To switch models, edit the `model:` field in `.agents/agents/skill-improver.md` or add a `subagents.agentOverrides` entry in `.pi/settings.json`.
- Children do not run live TradingView calls, paid API calls, or remote mutations.
