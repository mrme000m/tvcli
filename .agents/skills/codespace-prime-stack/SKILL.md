---
name: codespace-prime-stack
description: Operate, verify, and extend the bootstrapped DSH prime-orchestrator stack in this repo's GitHub Codespace/devcontainer — dsh + dsh-prime-orchestrator plugin + prime-agent CLI with Cloudflare Workers AI models, plus the specialist agent fleet (tv-scout, tv-investigator, wt-investigator) and its grid-trading wiring. Use when asked about the codespace prime stack, re-running or fixing the bootstrap playbook (bootstrapping/ansible/prime-stack.yml), starting or debugging the dsh Web GUI / Prime fleet column on port 3081, provisioning its secrets, adding plugins/presets to the codespace dsh installation, or operating the autonomous grid-trading loop (bootstrapping/docs/grid-fleet.md).
---

# codespace-prime-stack — the devcontainer's prime intelligence & agent fleet

This workspace's devcontainer (`.devcontainer/`, GitHub Codespace) is
bootstrapped into a **DSH prime-orchestrator host**: the DeepSeek Harness
(`dsh` 0.1.1-rc.2, exact — plugin compatibility) running the
`dsh-prime-orchestrator` plugin in profile `web`, with the
`prime-orchestrator` agent preset as the default, the `prime-agent` CLI on
PATH, and Cloudflare Workers AI as the LLM provider.

## What is installed where (inside the codespace)

| Component | Location |
|---|---|
| `dsh` CLI (npm global, exact `0.1.1-rc.2`) | `/usr/local/share/nvm/current/bin/dsh` — **resets on every codespace rebuild; the playbook reinstalls it** |
| dsh profile `web` (bundles: `dsh-base`, `dsh-web-app`, `dsh-prime-orchestrator`) | `~/.dsh/profiles/web/` |
| `prime-orchestrator` agent preset (plugin-managed, sha256 marker) | `~/.dsh/.agent-presets/prime-orchestrator/` |
| Specialist fleet presets (vendored, marker `managedBy: prime-stack-bootstrap`) — tv-scout, tv-investigator, wt-investigator | `~/.dsh/.agent-presets/<name>/` (sources: `bootstrapping/presets/`) |
| Grid-fleet profile rows: `mcp-wundertrading` MCP + `wt-tools` cloakDir override (keys templated from vault at provision time, mode 0600) | `~/.dsh/profiles/web/cordis.patch.yml` |
| WunderTrading runtime env (vault item `wundertrading-api`: WT_API_KEY / WT_API_SECRET) | `browser-debug/secrets/runtime/wt.env` |
| tvcli multi-account server autostart marker (`.tvcli-autoserve` → `/hunt` fan-out over the accounts.json pool on :8765) | repo root |
| prime-agent CLI (npm global via official installer) | on PATH (`prime-agent`) |
| dsh settings (CF Workers AI provider, default model `@cf/zai-org/glm-5.3`) | `~/.dsh/settings.yaml` |
| prime-agent runtime config | `~/.prime/agent/{models,auth,settings}.json` |
| dsh Web GUI (Prime fleet column) | port **3081** (forwarded; `.dsh-autoweb` marker enables autostart) |

## Re-running the bootstrap (idempotent)

The single source of truth is **`bootstrapping/ansible/prime-stack.yml`**
(Debian ansible 7.7 compatible, builtin modules only). It is invoked by
`.devcontainer/post-create.sh` and can always be re-run standalone:

```bash
cd /workspaces/tvcli   # repo root
ansible-playbook bootstrapping/ansible/prime-stack.yml \
  -i localhost, \
  -e ansible_connection=local \
  -e ansible_python_interpreter=/usr/bin/python3 \
  -e tv_workspace="$PWD"
```

Knobs: `-e prime_stack_strict=true` (missing CF secrets fatal),
`-e prime_stack_force_settings=true` (replace `~/.dsh/settings.yaml`
after backing it up). Tags: `dsh`, `plugin`, `preset`, `agent`, `env`,
`dsh-config`, `prime-config`, `secrets`, `fleet` (specialist presets +
grid-trading wiring), `always` — e.g. `--tags plugin,preset`.

A second run does nothing (every task checks before writing); a
half-installed state (plugin listed but not built) self-heals.

## The specialist fleet (grid trading)

The `fleet` tag installs the three vendored specialist presets and the
grid-trading wiring so the dsh agents can run the autonomous loop described
in [bootstrapping/docs/grid-fleet.md](../../bootstrapping/docs/grid-fleet.md):

- **research + screen** — tv-investigator (tvcli `/hunt` fan-out across the
  `accounts.json` multi-account cookie pool; `wundertrading` skill's
  token_screen.py regime ranking)
- **configure** — wt-investigator (WunderTrading grid/DCA/signal bots via the
  wt CLI, HMAC REST, MCP row `mcp-wundertrading`, and headful UI automation)
- **manage** — prime-orchestrator reacting to `tvcli watch` triggers
  (edit/swing/close/cancel + reliability exports)
- **confirm** — tv-scout (visual confluence on the live chart)

Presets are marker-preserved: a preset dir without the
`prime-stack-bootstrap` marker is user-owned and never overwritten; vendored
updates in `bootstrapping/presets/` propagate to marked copies on re-run.
The wundertrading MCP row's keys are read from the vault at provision time
(`wundertrading-api` → `browser-debug/secrets/runtime/wt.env`) and are never
committed or logged — re-run `--tags fleet` after a key rotation to refresh
the row.

## Secrets contract

- **`CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_KEY`** — repo-level GitHub
  codespace secrets; injected into the lifecycle env; templated into
  `settings.yaml` / `models.json` / `auth.json` (`no_log`, mode 0600) and
  bridged to `CLOUDFLARE_AI_TOKEN` / `CF_ACCOUNT_ID` in `~/.profile` +
  `~/.bashrc` (the plugin's llm-cf-provider + cf-tools read those names).
- **Bitwarden** (`bw` CLI via `browser-debug/secrets/bw-provision.sh`,
  vault `https://keys.00m.indevs.in`, auth `BW_EMAIL`/`BW_PASSWORD` or
  `BW_CLIENTID`/`BW_CLIENTSECRET` codespace secrets): provisions `.env`,
  `accounts.json`, `browser-debug/.env`, `opencode.env`, `wt.env`
  (WunderTrading API key pair), and `tv-proxy.env`.
- Missing secrets never break the build — config tasks warn + skip.

## Verification checklist (run inside the codespace)

```bash
dsh --version                                   # 0.1.1-rc.2
prime-agent --version
ls ~/.dsh/.agent-presets/prime-orchestrator/   # preset.yml + agent.cordis.yml
ls ~/.dsh/.agent-presets/                      # + tv-scout, tv-investigator, wt-investigator (fleet tag)
grep -c "mcp-wundertrading\|wt-tools" ~/.dsh/profiles/web/cordis.patch.yml  # 2 fleet rows
grep -A2 agent-default-model ~/.dsh/settings.yaml   # cloudflare-workers-ai / glm-5.3
test -f ~/.dsh/profiles/web/node_modules/dsh-prime-orchestrator/lib/index.js
test -f .tvcli-autoserve && curl -sf http://127.0.0.1:8765/health   # multi-account /hunt server
jq -r '.defaultProvider' ~/.prime/agent/settings.json
curl -sf http://127.0.0.1:3081/ >/dev/null && echo "dsh web up"
```

LLM smoke (token read from `~/.prime/agent/auth.json`, never echoed):

```bash
tok="$(jq -r '.["cloudflare-workers-ai"].key' ~/.prime/agent/auth.json)"
acct="$(grep -oE 'accounts/[0-9a-f]{24,}/ai/v1' ~/.dsh/settings.yaml | cut -d/ -f2)"
curl -s "https://api.cloudflare.com/client/v4/accounts/$acct/ai/v1/chat/completions" \
  -H "Authorization: Bearer $tok" -H 'Content-Type: application/json' \
  -d '{"model":"@cf/zai-org/glm-5.2","max_tokens":300,"messages":[{"role":"user","content":"Say ok"}]}'
```

(Reasoning models burn tokens on `reasoning_content` before the visible
answer — with too small a budget you get `content: ""` and
`finish_reason: length`; 300 is the safe floor.)

## dsh Web GUI

Opt-in autostart: `touch .dsh-autoweb` (repo root) then restart the
codespace — `post-start.sh` launches `dsh web --port 3081 --host 127.0.0.1
--no-open` (logs `/tmp/dsh-web.log`). Manual start: same command from `~`
with `CLOUDFLARE_AI_TOKEN`/`CF_ACCOUNT_ID` exported. In the GUI: toggle
the **Prime fleet column** at the sidebar foot; the default agent preset
is `prime-orchestrator`; Settings → Prime Orchestration configures the
engine.

## Hard-won gotchas (all empirically verified)

1. **pnpm 11 blocks git-dep builds** — `dsh plugin add github:…` fails with
   `ERR_PNPM_GIT_DEP_PREPARE_NOT_ALLOWED` and prints the exact `allowBuilds`
   key it wants: `"name@https://codeload.github.com/<owner>/<repo>/tar.gz/<sha>": true`
   (plain-name and `github:`spec keys do NOT work; the key changes with
   every plugin release). The playbook parses it from the failed output,
   merges it into the profile's `pnpm-workspace.yaml`, and retries once.
   pnpm 10 needs no remedy (git-dep `prepare` runs unconditionally).
2. **The codespace lifecycle runs commands under a pty** — installers whose
   prompt helper reads `/dev/tty` (prime-agent's install.sh) hang forever.
   Fix: download the script and run `setsid --wait sh file </dev/null`.
3. **`dsh web` refuses `--host 0.0.0.0`** by design (remote-code-execution
   safety). Bind `127.0.0.1` — the codespace port forwarder covers it.
4. **Vault item `opencode-cloudflare`'s `CLOUDFLARE_API_KEY` value is a
   `$`-pointer** (`$CLOUDFLARE_API_TOKEN`), not a literal token — it cannot
   substitute for the gh codespace secret (and sourcing it under `set -u`
   is fatal).
5. **Codespace rebuild keeps `~` but resets npm globals** — dsh and
   prime-agent reinstall automatically on each rebuild via post-create;
   `~/.dsh`, `~/.prime`, and the pnpm store survive.
6. **A crashed npm replace corrupts the dsh global tree** — `dsh` vanishes
   while `$ npm root -g`/`@deepseek-ai/dsh` keeps ENOTEMPTY leftovers;
   reinstalling over that fails (koffi wants cmake). The install task now
   removes the leftover tree first (safe: it only runs when the binary is
   MISSING). Node 24's npm also blocks dependency install scripts
   (`npm warn install-scripts`) — verified harmless: koffi's prebuilt
   binary loads without its script; do NOT pass `--allow-scripts`.
7. **The codespace edge cannot front the dsh-mobile gateway** (probed live
   with a public port + header logger, 2026-09-04): GitHub terminates TLS
   at the edge (DigiCert `*.app.github.dev`), dials the container over
   loopback with PLAIN http, and rewrites `Host` to `localhost:<port>`
   (the public vhost rides only in `X-Forwarded-Host`). dsh-mobile's trust
   model (direct Host/Origin matching) cannot work through that under any
   setup.json shape — `publicOrigin` forces the listen port to the URL
   port (443 → EACCES) while the edge dials the subdomain port; explicit
   authority ports must equal `listenPort`; tls-disabled flips the trust
   scheme to http so phone-side https Origin headers 403. The engine pins
   the loopback shape (tls disabled, `localhost`/`127.0.0.1` authorities,
   `instanceId` = pairing CA fingerprint256) and phone pairing goes
   through the plugin's own remote providers (Tailscale Funnel / cpolar,
   publicTls gateways) enabled from the web UI. Local check:
   `curl -s http://127.0.0.1:3443/` → `401 {"error":"authentication_failed"}`
   is the HEALTHY unpaired response.

## Extending

- More dsh plugins: `dsh plugin --profile web add <spec>` (mind gotcha 1).
- More Workers AI models: edit `MODEL_CATALOG` in
  `bootstrapping/python/prime_stack/config.py` (the single source of truth;
  surface-specific deltas are the `DSH_MODEL_ORDER`/`DSH_MODEL_OVERRIDES`
  tables there). Both the ansible playbook and the standalone
  `bootstrapping/python/bin/prime-stack` runner pick it up.
- New engine stages: one module in `bootstrapping/python/prime_stack/stages/`,
  registered in `stages/__init__.py`, one `python3 -m prime_stack <stage>`
  task in the playbook, a unit test in `bootstrapping/python/tests/`, and
  the stage list in `bootstrapping/README.md`.
- The playbook lives in `bootstrapping/` (not `browser-debug/ansible/`,
  which owns the CloakBrowser/debug stack only). See
  `bootstrapping/README.md` for the full operator manual.
