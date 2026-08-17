# Skill Analyze Loop (deep)

Runs `tvcli.skill-analyzer` sequentially across every built-in indicator skill. Unlike the dry `skill-improve-loop`, this downloads each skill's PineScript, analyzes the source, and runs live TradingView calls with varied custom inputs.

## Run it

Paste into the parent Pi session (cwd = repo root):

```typescript
subagent({
  workflowScript: `
    const mode = "analyze"; // "improve" to apply safe doc fixes too

    // Built-in indicator skills (from `./tvcli skills --json`).
    const skillNames = [
      "smc","dvi","liq-sweep","sr-breaks","gold-divergence","xau-trend",
      "vp","swingarm","golden","sniper","ust","quantum","squeeze",
      "ichimoku","camarilla","cvd","choppiness","xau-scalp"
    ];

    // Sequential: free tier allows only 2 concurrent studies, so one skill at a time.
    const results = [];
    for (const name of skillNames) {
      results.push(await runs.run(name, {
        agent: "tvcli.skill-analyzer",
        task: \\\`Analyze one tvcli skill.\\\\n\\\\nskillName: \\\${name}\\\\nrepoRoot: .\\\\nmode: \\\${mode}\\\`,
        output: \\\`skill-analysis/\\\${name}-\\\${mode}.md\\\`
      }));
    }

    return {
      total: skillNames.length,
      completed: results.filter(r => r.ok).length,
      failed: results.filter(r => !r.ok).length,
      reports: results.map(r => ({ skill: r.key, ok: r.ok, output: r.output }))
    };
  \`,
  async: true
})
```

Then wait with `subagent_wait()`.

## What it does

1. Runs one `tvcli.skill-analyzer` child per indicator skill, **sequentially**.
2. Each child: looks up the skill's `pineId`, downloads the PineScript (`tvcli pull`), reads/analyzes the source, runs 2-3 live variations with different custom inputs, verifies JSON output, and (in `improve` mode) fixes the skill doc.
3. Writes per-skill reports to `skill-analysis/<skill>.md`.
4. Aggregates a completion summary.

## Outputs

- `skill-analysis/<skill>.md` — per-skill source analysis + run results + fixes
- `skill-analysis/<skill>-<mode>.md` — child handoff summary

## Notes

- Model: `nvidia/nemotron-3.5-lightning-30b-a3b` (NVIDIA NIM, direct). Auth key is stored in `~/.pi/agent/auth.json` under `nvidia`; the model ships in pi's built-in catalog, so no config change is needed.
- Live calls respect free tier (`--bars 180`, auto-clean between studies). If study-limit errors recur, add `./tvcli clean` inside the child's guardrails (already instructed).
- Add a skill by appending to `skillNames`; remove `xau-scalp` if you don't want the private engine.
