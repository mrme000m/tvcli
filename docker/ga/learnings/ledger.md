# GA learnings ledger

Distilled knowledge from operating and improving the GA stack.
Reverse-chronological — newest first. The loop contract, the entry format,
and the write rules live in [README.md](README.md).

## 2026-09-08 — GHCR propagation race: buildx push succeeds but the immediate host pull 404s the digest

ga-deploy (az00) failed at 'Pull the image on the host' with NotFound: content digest ... — build+push had succeeded seconds earlier. Root cause: GHCR eventual consistency; the manifest is not immediately pullable right after the push step completes (~3s gap here). The running container is untouched when the pull fails (deploy is pull-then-replace), so a failure here is SAFE — no partial state. Remedy: 'gh run rerun <id> --failed' once propagation has caught up (succeeded on first retry ~5 min later; buildx cache makes the rebuild cheap). If it recurs every deploy, consider adding a small retry loop or a sleep after push in the workflow.

Changes:
- .github/workflows/ga-deploy.yml


## 2026-09-08 — Heartbeat freshness bounds must absorb rescreen blocking; delegation gates must be pure shell

Two findings from the 2026-09-08 fresh-deploy audit: (1) The manage loop is sequential, so a rescreen cycle (~every 15 min, 6-9 min long) blocks the optimizer and pnl-snapshot lanes; heartbeat freshness bounds of 3x optimizer interval (540s) and 2x pnl interval (600s) sat right at the worst-case age and flapped amber on every post-rescreen heartbeat (542s vs 540s, 680s vs 600s), each flap firing a pointless self-nudge. Widened to 4x (optimizer) and 3x (pnl) — a dead lane is still caught within 12-15 min. Lesson: heartbeat bounds must be interval + worst-case-blocking, not small multiples. (2) prime-agent delegation autonomousGates strings are executed as shell commands verbatim — appending success-criterion prose like '(skips allowed)' after the command makes /bin/sh fail with a syntax error before anything runs, so the gate can never pass no matter the work quality (worker 0547f2a1 diagnosed this). Gate strings must be pure shell; put criteria in the task prose instead.

Changes:
- agents/grid-autonomy/daemon.py
- agents/grid-autonomy/tests/test_daemon_heartbeat.py

## 2026-09-08 — LLM fallback legs rot: verify with /api/llm/validate, not key presence

The deployment audit checklist should include POST console /api/llm/validate (live provider ping), because provider keys can be present while the configured models are dead: az00 2026-09-08 nvidia leg returned HTTP 410 (meta/llama-3.3-70b-instruct removed from integrate.api.nvidia.com) and openrouter leg HTTP 404 (arcee-ai/trinity-large-preview:free delisted) — the chain ran fine on mistral+cf so nothing looked broken until the LLM brains panel showed two red legs. Fix path that needs NO restart: POST /api/llm {providers:{nvidia:{model:...},openrouter:{model:...}}} writes state/llm.env (sidecar), the daemon picks it up at the next LLM call via self-heal; then POST /api/llm/validate to confirm. NVIDIA's /v1/models list is public (no auth) and OpenRouter's /api/v1/models is public — use them to pick in-catalog ids. Verified replacements: nvidia/nemotron-3.5-lightning-30b-a3b (direct) and nvidia/nemotron-3.5-lightning:free (openrouter, slow ~40-60s free tier but alive). Source defaults updated so fresh deployments don't boot on dead ids.

Changes:
- agents/grid-autonomy/llm/provider.py
- agents/grid-autonomy/console/server.py

## 2026-09-08 — Deploy pipeline silently demotes the fleet to dry-run when the daemon was re-promoted in-container

vps-run.sh GRID_MODE=preserve reads the OLD container's ENV (docker inspect .Config.Env), not the running daemon's ACTUAL mode. When the daemon is re-promoted to live-paper from inside the container (console lifecycle op / start.sh --live-paper), the container env keeps GRID_MODE=dry-run and goes stale — the next push deploy then 'preserves' the stale env and silently boots the new container in dry-run (az00 2026-09-08: the 04:51 push deploy logged 'preserving previous GRID_MODE (dry-run)' although the daemon had been creating paper bots all morning; the fleet ran planning-only for ~47 min until someone re-promoted it at 05:40). Remediation applied: dispatched grid-autonomy-deploy.yml with mode=live-paper explicitly (run 34192676894) so the env is honest again — the dispatch input is the only deliberate mode-change path. Permanent fix (pending): vps-run.sh should detect the daemon's real mode when preserving (e.g. docker exec pgrep -f 'daemon.py --live-paper' on the old container, or the daemon should write its mode to a state-volume marker vps-run.sh reads) — and the deploy health-gate should assert the post-deploy daemon mode matches the pre-deploy daemon mode.

## 2026-09-08 — Fresh-deploy audit: deploy-loop slot-consumption bug, WT paper-sleeve testnet constraint, workbench env fixes

az00 2026-09-08 audit of the fresh grid-autonomy deployment found: (1) daemon.py rescreen deploy loop consumed the slot on a FAILED live create (deployments.append/free.remove/deployed+=1 ran unconditionally after commit_deploy) — the RAY WT-400 at 02:57 wrongly triggered open_slot, re-splitting the binance sleeve 1x$120 -> 2x$60 ($30 caps) which guard-vetoed every later binance candidate (12+ vetos, 3 rescreens); MON's demo-cap 400 on slot 7 then met slots_hard_max on the next HL candidate. Fix: gate the three consumption points on deploy_ok = dry_run or slot in active_bots (mirrors the existing capacity-note pattern); next same-venue candidate now falls through into the SAME slot in the same cycle. (2) The RAY 400 root cause is a WT-side constraint: RAYUSDT is ABSENT from the Binance futures TESTNET (the venue WT's Binance paper engine executes against) while ZROUSDT trades there — the pair resolves on mainnet futures so guardrails pass and only the create fails; ~10 of the top-60 spot universe affected. Fix: fail-open paper_pair_supported() guard in execution/resolve.py (24h-cached public testnet exchangeInfo) wired into screen_binance BEFORE candle fetches. (3) The grid-ga workbench container is missing pydantic (wtclient import) — 14 phantom test errors; fix: pip3 install --break-system-packages pydantic. (4) tests/test_repair_ledger depends on uncommitted local wt_audit fixtures (root .gitignore *.json rule) and errored in every fresh clone — added a fixture-presence skipUnless (8 expected skips). (5) prime-agent daemon must be started in this container (prime-agent --mode daemon) before daemonBacked:true delegations; delegation event logs may stay empty — poll the session JSONL at /data/dsh/prime-agent/sessions/ instead. (6) resolve.STATE_DIR is a module global that multiple test modules re-point at import time (last import wins) — cache tests must patch it per-test, not at import. Verification bar after all changes: 839 tests OK (skipped=8), order-robust, new regression tests fail on pre-fix code.

Changes:
- agents/grid-autonomy/daemon.py
- agents/grid-autonomy/execution/resolve.py
- agents/grid-autonomy/screen/merge.py
- agents/grid-autonomy/tests/test_deploy_failure_fallthrough.py
- agents/grid-autonomy/tests/test_paper_pair_guard.py
- agents/grid-autonomy/tests/test_merge_screen.py
- agents/grid-autonomy/tests/test_repair_ledger.py

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
