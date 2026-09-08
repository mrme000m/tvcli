# GA learnings ledger

Distilled knowledge from operating and improving the GA stack.
Reverse-chronological — newest first. The loop contract, the entry format,
and the write rules live in [README.md](README.md).


## 2026-09-08 — dsh web --host 0.0.0.0 is hard-rejected in the published npm tarball

The published `@deepseek-ai/dsh-web-app` npm tarball hard-rejects
`dsh web --host 0.0.0.0` (the container MUST bind all interfaces for the
docker-network tunnel path). The Mac-local source checkout only warns — the
"ponytail" behavior differs between the two, so what works on the Mac can
fail in the image. `docker/ga/dsh_ponytail_patch.py` patches the installed
tarball at build time with an exact-string match: if a future dsh release
changes the guard text, the patch (and therefore the image build) FAILS
instead of silently losing the bind-all fix. Do not "fix" that failure by
loosening the match — re-derive the patch against the new source.

Changes:
- docker/ga/dsh_ponytail_patch.py


## 2026-09-08 — pnpm 10 vs 11: git-dep build-script gating uses two different config shapes

pnpm gates build scripts of git-hosted dependencies with
`ERR_PNPM_GIT_DEP_PREPARE_NOT_ALLOWED`. The failure log prints the needed
keys, but the config syntax depends on the major: pnpm 10.x wants an
`onlyBuiltDependencies:` LIST, while pnpm >= 11 uses an `allowBuilds:` MAP.
`docker/ga/pnpm_allowbuilds.py` parses the demanded `name@<tarball-url-sha>`
keys from the failure log and writes BOTH shapes into the profile's
pnpm-workspace.yaml, so the plugin install retry works on either major.

Changes:
- docker/ga/pnpm_allowbuilds.py
- docker/ga/Dockerfile


## 2026-09-08 — az00 root disk is tight — purge install caches in the SAME layer

The az00 VPS has a ~29G root disk with only ~5G free. The
prime-agent installer leaves ~1GB of uv kernel-venv download cache in
`/root/.cache/uv` — it once broke the image pull with "no space left on
device". The npm `_cacache` is the same class of problem. Caches must be
`rm`'d IN THE SAME `RUN` layer so the bytes never enter the image history
(deleting in a later layer only masks them).

Changes:
- docker/ga/Dockerfile


## 2026-09-08 — grid-ga dsh web publishes on 127.0.0.1:3082 — caddy owns az00's :3081

az00's host caddy already binds 127.0.0.1:3081, so the grid-ga
container cannot publish there. The host publish is
`-p 127.0.0.1:3082:3081` (reach via `ssh -L 3082:localhost:3082 <host>`).
The public path is unaffected: the Cloudflare tunnel connector reaches
dsh web over grid-net docker DNS (`http://grid-ga:3081`), which never
touches the host port.

Changes:
- docker/ga/ga-run.sh


## 2026-09-08 — dsh scrubbedParentEnv() drops GH_TOKEN from agent shells — persist gh auth instead

dsh spawns agent shells through a scrubbed parent env
(`scrubbedParentEnv()` in dsh-subprocess drops `GH_TOKEN`/`GITHUB_TOKEN`),
so relying on the env var makes git push / gh CLI fail inside agent shells.
The fix is to PERSIST the token once: `gh auth login --with-token` writes
`/root/.config/gh/hosts.yml`. Run it under
`env -u GH_TOKEN -u GITHUB_TOKEN` — gh refuses to persist the credential
while the variable is set — after which git push and the gh CLI work
env-free in every shell.

Changes:
- docker/ga/entrypoint.sh


## 2026-09-08 — Track the baked settings TEMPLATE's sha, not the rendered file's

dsh rewrites its own `settings.yaml` at runtime. The original
re-render guard compared the volume's settings.yaml against a remembered
hash, so dsh's own rewrite looked like a hand-edit and template updates
never reached the volume. Fix: track the sha of the baked TEMPLATE
(`.settings-template.sha256`) instead. Image template changes win (the
template is re-rendered); operator edits are preserved only when the
template itself is unchanged.

Changes:
- docker/ga/entrypoint.sh


## 2026-09-08 — CF Workers AI context windows come from ai/models/search `context_window`

Cloudflare Workers AI model context windows are NOT guessable from
model family; they come from the `ai/models/search` response's
`context_window` property. Verified values: glm-5.3 / glm-5.3-flash /
deepseek-v4-flash = 1.31M, deepseek-v4-pro = 1.05M tokens. `maxTokens` is
normalized to 16384 across the model list (prime_agent_config.py renders
these for the GA agent's prime-agent workers).

Changes:
- docker/ga/prime_agent_config.py

