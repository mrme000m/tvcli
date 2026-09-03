# bootstrapping/ — devcontainer bootstrap playbooks

Repeatable Ansible bootstrap for the tvcli GitHub Codespace / devcontainer.
Each playbook is idempotent (safe to re-run), `ansible_connection=local`
against `localhost`, and compatible with the container's Debian bookworm
ansible 7.7 (ansible-core 2.14, builtin modules only).

```
bootstrapping/
├── README.md               this file
└── ansible/
    └── prime-stack.yml     the DSH prime-orchestrator stack (see below)
```

`.devcontainer/post-create.sh` invokes the playbook (with `--skip-tags
secrets` — the Bitwarden step directly above it already ran) after the
existing browser-debug deps steps; a failure there is a **warning, never a
build breaker** (re-run by hand inside the codespace: `bash .devcontainer/post-create.sh`).
Cloudflare credentials come from the `CLOUDFLARE_ACCOUNT_ID` /
`CLOUDFLARE_API_KEY` repo codespace secrets, injected into the lifecycle
env. NB: the bw-provisioned `browser-debug/secrets/runtime/opencode.env`
can NOT substitute for them — its API-key value is a `$`-pointer, not a
literal token.

## The prime stack

The stack turns the codespace into a prime-intelligence/agent-fleet host:

| Component | What it is | Where |
|---|---|---|
| `dsh` CLI (`@deepseek-ai/dsh` **0.1.1-rc.2** — exactly this version; 0.1.2-alpha is incompatible with the plugin) | DeepSeek Harness host | npm global, `dsh` on PATH |
| `pnpm` >= 10 | required by `dsh plugin` (it forwards to pnpm) | npm global |
| `dsh-prime-orchestrator` plugin (v0.4.x) | Prime Agent orchestration for dsh: fleet column in the Web GUI, `prime_agent` tool, CF Workers AI LLM provider + tools | profile `web` (`~/.dsh/profiles/web/`) |
| `prime-orchestrator` agent preset | materialized by the plugin at every host boot into `~/.dsh/.agent-presets/prime-orchestrator/` (sha256-marked; user edits are preserved) | `$DSH_HOME/.agent-presets/` |
| `prime-agent` CLI | Prime Intellect agent CLI (self-contained) | `~/.local/bin/prime-agent` |
| CF Workers AI config | dsh `~/.dsh/settings.yaml` + prime-agent `~/.prime/agent/{models,auth,settings}.json`, all Cloudflare-account templated from env | `~/.dsh`, `~/.prime/agent` |

Default model: **`@cf/zai-org/glm-5.3`** on provider `cloudflare-workers-ai`,
with a curated 8-model catalog (GLM-5.2/5.3/5.3-Flash, DeepSeek V4 Flash/Pro,
Qwen3.8-27B, Kimi K2.6/K2.7-Code). `defaultThinkingLevel: high`.

## Running standalone (inside the codespace)

From the repo root (`go/`):

```sh
ansible-playbook bootstrapping/ansible/prime-stack.yml \
  -i localhost, \
  -e ansible_connection=local \
  -e ansible_python_interpreter=/usr/bin/python3 \
  -e tv_workspace="$PWD"
```

Extra knobs:

- `-e prime_stack_strict=true` — missing Cloudflare secrets are **fatal**
  instead of warn + skip.
- `-e prime_stack_force_settings=true` — overwrite an existing
  `~/.dsh/settings.yaml` with the template (a backup is written to
  `~/.dsh/settings.yaml.pre-prime-stack.bak` first).

## Tags

| Tag | Scope |
|---|---|
| `dsh` | dsh CLI (exact 0.1.1-rc.2) + pnpm >= 10 |
| `plugin` | `dsh plugin --profile web add github:mrme000m/dsh-prime-orchestrator`, incl. the pnpm >= 11 `allowBuilds` remedy + one retry + built-artifact check (`lib/index.js`) |
| `preset` | warm-boot the web profile once so the preset materializes, then assert `~/.dsh/.agent-presets/prime-orchestrator/preset.yml` (runs after `agent` — the booted engine looks for the prime-agent binary) |
| `agent` | prime-agent CLI install |
| `env` | PATH + Cloudflare env bridge in `~/.profile` / `~/.bashrc` |
| `dsh-config` | `~/.dsh/settings.yaml` |
| `prime-config` | `~/.prime/agent/{models,auth,settings}.json` (merge, never destroying other providers/keys) |
| `secrets` | `browser-debug/secrets/bw-provision.sh` (never fatal) |
| `always` | preflight warnings + final summary |

Example: `ansible-playbook ... --tags plugin,preset`.

## Secrets contract (GitHub repo codespace secrets)

| Secret | Level | Used for |
|---|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | repo | Cloudflare account id for Workers AI models — dsh + prime-agent LLM provider (templated into `settings.yaml` / `models.json` at runtime; **never committed**) |
| `CLOUDFLARE_API_KEY` | repo | Cloudflare Workers AI API token (written into `~/.prime/agent/auth.json` with `no_log`; bridged to `CLOUDFLARE_AI_TOKEN` in shells) |
| `BW_EMAIL` / `BW_PASSWORD` | repo | Bitwarden vault login for `bw-provision.sh` (vault `https://keys.00m.indevs.in`) |
| `BW_CLIENTID` / `BW_CLIENTSECRET` | user | Bitwarden API-key auth (alternative to BW_EMAIL) |
| `BW_URL`, `BW_GRANTTYPE` | user | optional bw CLI server/flow overrides |

## Failure semantics

- **Missing Cloudflare secrets** → the install tasks (dsh, plugin, preset,
  prime-agent) still run; only the config-writing tasks are skipped with a
  warning. Never a hard failure (unless `prime_stack_strict=true`).
- **bw-provision** exit 2 (credentials not configured) or any other non-zero →
  warning, same convention as `post-create.sh`; the stack still installs.
- **Plugin add fails twice** (pnpm build block or network) → the playbook
  fails with the captured exit codes — inspect the pnpm output above the task.
- **Preset did not materialize within 90 s** of a warm boot → the playbook
  fails; check `/tmp/dsh-warmboot.log`.
- `post-create.sh` wraps the whole playbook in `|| echo WARN` — a codespace
  build **never** breaks because of this stack.

## Idempotency

A second run does nothing: every task checks before it writes —

- dsh/pnpm/prime-agent: installed only when the binary is absent (dsh version
  mismatch only warns; it never auto-upgrades).
- plugin: skipped when `~/.dsh/profiles/web/package.json` lists
  `dsh-prime-orchestrator` AND its built artifact `lib/index.js` exists — a
  half-installed (listed but unbuilt) state self-heals on the next run. The
  pnpm >= 11 `allowBuilds` remedy is PARSED from the failed install's output
  (pnpm prints the exact key it wants — `name@<tarball-url-with-commit-sha>`,
  which changes with every plugin release, so it is never hardcoded), merged
  into the profile's `pnpm-workspace.yaml`, and the add retried once. pnpm 10
  needs no remedy (git-dep `prepare` runs unconditionally; both verified
  empirically).
- preset: warm boot only when `preset.yml` is absent.
- `~/.profile` / `~/.bashrc`: marker-bounded `blockinfile` block.
- `~/.dsh/settings.yaml`: created only when missing (existing file is left
  untouched — backed up first when `prime_stack_force_settings=true`).
- `~/.prime/agent/*.json`: keyed merges that compare before/after and report
  `changed`/`unchanged`; other providers/keys are preserved.

## dsh web autostart (`.dsh-autoweb` marker)

`post-start.sh` auto-launches the dsh Web GUI on **port 3081**
(`http://localhost:3081` — forwarded by the devcontainer; Prime fleet column
in the Web GUI) when ALL of:

1. the marker file exists: `touch .dsh-autoweb` (repo root),
2. `dsh` is on PATH,
3. `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_KEY` are in the environment.

Logs go to `/tmp/dsh-web.log`. Remove the marker to disable.

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

- No secret values, account ids, or tokens are ever hardcoded in any committed
  file; the Cloudflare account id is templated at runtime from
  `CLOUDFLARE_ACCOUNT_ID`.
- `auth.json` writes run with `no_log: true` and mode `0600`; `settings.yaml`
  is `0600`.
- `bw-provision.sh` (reused verbatim, never reimplemented here) never echoes
  values; runtime files it writes are gitignored.
