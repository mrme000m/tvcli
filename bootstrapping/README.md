# bootstrapping/ — devcontainer bootstrap (Ansible orchestrator + Python engine)

Repeatable bootstrap for the tvcli GitHub Codespace / devcontainer that turns
it into a **DSH prime-orchestrator host**. Ansible owns what it is good at
(apt packages, orchestration, tags, preflight); every install/config step
lives in a modular, unit-tested **Python engine**.

```
bootstrapping/
├── README.md               this file
├── ansible/
│   └── prime-stack.yml     slim orchestrator: apt + preflight + one task per
│                           stage (parses each stage's JSON envelope)
├── python/
│   ├── bin/prime-stack     standalone runner (no Ansible needed)
│   ├── prime_stack/        the engine (importable library)
│   │   ├── core.py         StageResult envelope, dry-run-aware Context
│   │   │                   (exec/mutate/write_text), marker blocks, versions
│   │   ├── config.py       Config + the single CF model catalog (surface
│   │   │                   deltas as explicit override tables)
│   │   ├── cli.py          dispatcher (stdout = JSON envelopes, stderr = logs)
│   │   ├── stages/         one module per stage (18 stages, 3 groups)
│   │   └── templates/      static file templates (code-review-graph MCP patch)
│   └── tests/              stdlib unittest suite (python3 -m unittest discover)
├── presets/                vendored specialist agent presets (fleet tag)
│   ├── tv-scout/           visual confluence on the live chart
│   ├── tv-investigator/    multi-session TV network-API screening + research
│   ├── wt-investigator/    WunderTrading bot configuration + management
│   └── web-discovery/      web-platform reverse-engineering + tool forging
└── docs/
    └── grid-fleet.md       the autonomous grid-trading loop blueprint
```

## The two runners

**Ansible (the documented path — what post-create.sh runs):**

```sh
ansible-playbook bootstrapping/ansible/prime-stack.yml \
  -i localhost, \
  -e ansible_connection=local \
  -e ansible_python_interpreter=/usr/bin/python3 \
  -e tv_workspace="$PWD"
```

Each playbook task runs one engine stage via
`python3 -m prime_stack <stage>` and parses its JSON envelope (always the
last stdout line). Human logs go to stderr. `ansible-playbook --check` maps
to engine dry-run: stage tasks still execute (`check_mode: false`) but
perform no writes and run no installers — check mode is a faithful
"what would change" preview.

**Python engine (standalone — same stages, no Ansible):**

```sh
bootstrapping/python/bin/prime-stack --list          # stages + groups
bootstrapping/python/bin/prime-stack --dry-run all   # preview everything
bootstrapping/python/bin/prime-stack dsh plugin      # run selected stages
```

Stdout carries one JSON envelope per stage (and a final aggregate when
several run); exit code is 1 as soon as a stage fails (unless
`--exit-zero`). Groups: `all`, `extras`, `fleet`.

Stage list (mirrors the playbook tags):

`packages` (python fallback; the playbook uses the apt module) · `dsh` ·
`plugin` · `agent` · `preset` · `env` · `dsh-config` · `prime-config` ·
`extras-plugins` · `extras-mnemon` · `extras-mobile` · `extras-mcp` ·
`secrets` · `fleet-presets` · `fleet-patch` · `fleet-autoserve` ·
`stealth-browser`

## Knobs (both runners)

| Knob | Ansible (`-e`) | Python (flag / env) | Effect |
|---|---|---|---|
| strict | `prime_stack_strict=true` | `--strict` / `PRIME_STACK_STRICT` | missing CF secrets are fatal instead of skip+warn |
| force settings | `prime_stack_force_settings=true` | `--force-settings` / `PRIME_STACK_FORCE_SETTINGS` | replace an existing `~/.dsh/settings.yaml` (backup written first) |
| dry run | `--check` | `--dry-run` / `PRIME_STACK_DRY_RUN` | record writes/installs without executing |
| workspace | `tv_workspace=…` | `--workspace` / `TV_WORKSPACE` | repo root (default: playbook dir's parent, cwd) |

Tags (unchanged from v1): `dsh`, `plugin`, `preset`, `agent`, `env`,
`dsh-config`, `prime-config`, `extras` (`plugins`/`mnemon`/`mobile`/`mcp`),
`secrets`, `fleet`, `stealth-browser`, `always`, `packages`. Example:
`ansible-playbook ... --tags plugin,preset`.

## The prime stack

| Component | What it is | Where |
|---|---|---|
| `dsh` CLI (`@deepseek-ai/dsh` **0.1.1-rc.2** — exactly this version; 0.1.2-alpha is incompatible with the plugin) | DeepSeek Harness host | npm global, `dsh` on PATH |
| `pnpm` >= 10 | required by `dsh plugin` (it forwards to pnpm) | npm global |
| `dsh-prime-orchestrator` plugin (v0.4.x) | Prime Agent orchestration for dsh: fleet column in the Web GUI, `prime_agent` tool, CF Workers AI LLM provider + tools | profile `web` (`~/.dsh/profiles/web/`) |
| `prime-orchestrator` agent preset | materialized by the plugin at every host boot into `~/.dsh/.agent-presets/prime-orchestrator/` (sha256-marked; user edits are preserved) | `$DSH_HOME/.agent-presets/` |
| `prime-agent` CLI | Prime Intellect agent CLI (self-contained) | `~/.local/bin/prime-agent` |
| CF Workers AI config | dsh `~/.dsh/settings.yaml` + prime-agent `~/.prime/agent/{models,auth,settings}.json`, all Cloudflare-account templated from env | `~/.dsh`, `~/.prime/agent` |
| parity plugins | dsh-mnemon, pi2dsh, dsh-mobile, @deepseek-ai/dsh-mcp-client, pi-agent-memory, vendored dsh-restart | profile `web` |
| specialist fleet | tv-scout, tv-investigator, wt-investigator, web-discovery + grid-trading wiring (wundertrading MCP row, wt-tools cloakDir, tvcli autoserve) | `$DSH_HOME/.agent-presets/` + web profile patch |
| stealth browser | vibheksoni/stealth-browser-mcp 97-tool MCP (`stealth-browser` stage + `dsh-mcp-client`, headful `DISPLAY=:99` → `x11vnc:5900`/`websockify:6080` + `dsh-cloak-panel` zoom/scale) | `tools/stealth-browser-mcp/venv` + web profile `mcp-stealth-browser` row |

Default model: **`@cf/zai-org/glm-5.3`** on provider `cloudflare-workers-ai`,
`defaultThinkingLevel: high`. The model catalog lives ONLY in
`bootstrapping/python/prime_stack/config.py` (`MODEL_CATALOG` + the
`DSH_MODEL_OVERRIDES`/`DSH_MODEL_ORDER` surface deltas) — when adding a
model, add it there and both config surfaces pick it up.

## Secrets contract (GitHub repo codespace secrets)

Secrets are read **directly from the process environment** by the engine
stages — they never appear in Ansible task arguments, logs, or the JSON
envelopes, so no `no_log` is needed anywhere.

| Secret | Level | Used for |
|---|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | repo | Cloudflare account id for Workers AI models — dsh + prime-agent LLM provider (templated into `settings.yaml` / `models.json` at runtime; **never committed**) |
| `CLOUDFLARE_API_KEY` | repo | Cloudflare Workers AI API token (written into `~/.prime/agent/auth.json`, mode 0600; bridged to `CLOUDFLARE_AI_TOKEN` in shells) |
| `WT_API_KEY` / `WT_API_SECRET` | bw vault item `wundertrading-api` | wundertrading MCP row headers (read from the bw-provisioned `browser-debug/secrets/runtime/wt.env` at provision time) |
| `BW_EMAIL` / `BW_PASSWORD` | repo | Bitwarden vault login for `bw-provision.sh` (vault `https://keys.00m.indevs.in`) |
| `BW_CLIENTID` / `BW_CLIENTSECRET` | user | Bitwarden API-key auth (alternative to BW_EMAIL) |
| `BW_URL`, `BW_GRANTTYPE` | user | optional bw CLI server/flow overrides |

## Failure semantics

- **Missing Cloudflare secrets** → install stages still run; config stages
  skip with a warning. Never a hard failure (unless strict).
- **bw-provision** exit 2 (credentials not configured) or any other
  non-zero → warning; the stack still installs.
- **Plugin add fails twice** (pnpm build block or network) → the stage fails
  with the captured exit codes; inspect `/tmp/prime-stack-plugin-add.log`.
- **Preset did not materialize within 120 s** of a warm boot → the stage
  fails; check `/tmp/dsh-warmboot.log`.
- `post-create.sh` wraps the whole playbook in `|| echo WARN` — a codespace
  build **never** breaks because of this stack.
- The `secrets` stage is never fatal by design (failures come back as
  envelope warnings).

## Idempotency

A second run does nothing: every stage checks before it writes —

- dsh/pnpm/prime-agent: installed only when the binary is absent (dsh
  version mismatch only warns; it never auto-upgrades).
- plugin: skipped when `~/.dsh/profiles/web/package.json` lists
  `dsh-prime-orchestrator` AND its built artifact `lib/index.js` exists — a
  half-installed (listed but unbuilt) state self-heals on the next run. The
  pnpm >= 11 `allowBuilds` remedy is PARSED from the failed install's
  output (pnpm prints the exact key it wants — `name@<tarball-url-with-
  commit-sha>`, which changes with every plugin release, so it is never
  hardcoded), merged into the profile's `pnpm-workspace.yaml`, and the add
  retried once. pnpm 10 needs no remedy (git-dep `prepare` runs
  unconditionally; both verified empirically).
- preset: warm boot only when `preset.yml` is absent.
- env bridge: marker-bounded block (any legacy marker generation on disk
  converges to exactly one canonical block instead of duplicating).
- `~/.dsh/settings.yaml`: created only when missing (existing file is left
  untouched — backed up when `--force-settings` replaces it).
- `~/.prime/agent/*.json`: keyed merges that are byte-stable no-ops when
  nothing changed; other providers/keys are preserved.
- fleet presets: sha256 marker semantics — unedited vendored copies track
  the source, user-owned dirs (no marker / different managedBy) are
  preserved untouched.
- The engine is unit-tested (`python3 -m unittest discover -s
  bootstrapping/python/tests -t bootstrapping/python`) — 47 tests covering
  the allowBuilds remedy, JSON merges, marker blocks, fleet semantics and
  the settings.yaml catalog.

## dsh web autostart (`.dsh-autoweb` marker)

`post-start.sh` auto-launches the dsh Web GUI on **port 3081** when ALL of:
the marker file exists (`touch .dsh-autoweb` in the repo root), `dsh` is on
PATH, and the Cloudflare secrets are in the environment. Logs go to
`/tmp/dsh-web.log`. Remove the marker to disable.

## The specialist fleet (grid trading)

The `fleet` stages install the three specialist agent presets vendored under
`bootstrapping/presets/` (Mac paths are `@TV_WORKSPACE@` / `@CLOAK_DIR@`
placeholders resolved at install time) with marker semantics — user-edited
presets are preserved, unedited ones track the vendored copies. Together
with the plugin-materialized `prime-orchestrator` they form the autonomous
grid-trading loop — research/screen (tv-investigator fanning
tvcli `/hunt` across the `accounts.json` multi-account cookie pool and
ranking regimes via token_screen.py),
configure (wt-investigator via the wt CLI / REST / MCP / headful UI),
manage (prime-orchestrator reacting to `tvcli watch` triggers), confirm
(tv-scout visual confluence). The regime→bot mapping, trigger set, and ops
checklist live in [docs/grid-fleet.md](docs/grid-fleet.md).

## Post-run verification checklist

```sh
dsh --version                                   # 0.1.1-rc.2
prime-agent --version                           # prints a version
ls ~/.dsh/.agent-presets/prime-orchestrator/    # preset.yml + agent.cordis.yml
grep -A2 agent-default-model ~/.dsh/settings.yaml
#   provider: cloudflare-workers-ai / model: "@cf/zai-org/glm-5.3"

# Web GUI (opt-in):
touch .dsh-autoweb          # then restart the codespace (or bash .devcontainer/post-start.sh)
curl -sf http://127.0.0.1:3081/ >/dev/null && echo "dsh web up"
```

Inside the Web GUI: toggle the **Prime fleet column** at the sidebar foot,
create a session with the **prime-orchestrator** preset (it is also the
configured default), and check Settings → Prime Orchestration.

## Security notes

- No secret values, account ids, or tokens are ever hardcoded in any
  committed file; the Cloudflare account id is templated at runtime from
  `CLOUDFLARE_ACCOUNT_ID`.
- Secrets flow: codespace/vault → process env (or bw-provisioned runtime
  files) → engine stage → target file (mode 0600). They never appear in
  Ansible task arguments, logs, stdout envelopes, or the repository.
- `bw-provision.sh` (reused verbatim, never reimplemented) never echoes
  values; runtime files it writes are gitignored.

## Extending

- New stage: add `bootstrapping/python/prime_stack/stages/<name>.py`
  (module docstring documents the gotchas it encodes), register it in
  `stages/__init__.py`, add a matching tagged task to the playbook, and
  unit-test the pure logic under `tests/`.
- New Workers AI model: add one entry to `MODEL_CATALOG` in
  `python/prime_stack/config.py` — both config surfaces render from it
  (per-surface caps/names via `DSH_MODEL_OVERRIDES`).
- More dsh plugins: append to `PARITY_PLUGINS` in the same config module
  (mind the pnpm >= 11 allowBuilds gotcha documented in
  `stages/plugin.py`).
- The playbook stays slim on purpose: if a task needs more than
  run-stage + parse-envelope, that logic belongs in the engine.
