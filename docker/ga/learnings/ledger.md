# GA learnings ledger

Distilled knowledge from operating and improving the GA stack.
Reverse-chronological — newest first. The loop contract, the entry format,
and the write rules live in [README.md](README.md).

## 2026-09-11 — carry-pray parks free the daemon slot but keep the WT demo bot running — demo-cap gate undercounts and 400-storms

Verified incident 2026-09-11: carry-pray-enter (daemon.py _enter_carry_pray) moves a bot from active_bots into state.carry_pray and frees the daemon slot, but the WT-side grid bot keeps running on the paper profile with a server-side takeProfit — it still consumes one of the 5 WT demo grid-bot slots. Every demo-cap gate (deploy loop break, transition veto journal, queue_rescreen refill-nudge skip, _prune_unfillable_slots at_demo_cap, per-profile gate, ctl /status demo_cap block) counted only len(state.active_bots) vs the learned cap, so 3 tracked bots with 2 carry-pray phantoms looked like headroom-2 → the daemon deliberated and attempted deploys into free slots, every create 400ing with 'You've reached the maximum number of Demo Trading Grid Bots! (Limit: 5)' (130+ deploy-failed entries in ~1.5h, 64% of 00 capital idle). The authoritative WT-side count is observe.grid_capacity() used_pairs[EXCHANGE][profile_code] (already in state.capacity). Fix (delegated, verified, 878 tests green): _count_paper_bots now returns max(WT used_pairs count, tracked active+carry_pray count) fail-closed, _count_paper_bots_total for the scalar gates, all five gate sites use the live total, and a transition-only 'phantom-bot' journal fires when used_pairs > tracked. ctl_http replicates the helper for the /status demo_cap block. Next time: any demo-bot-cap gate must count WT-side live bots, not daemon-tracked ones; a carry-pray park is still a WT bot.

Changes:
- agents/grid-autonomy/daemon.py
- agents/grid-autonomy/ctl_http.py
- agents/grid-autonomy/tests/test_demo_cap_live_count.py
- docker/ga/notes/wt-demo-cap-cleanup-2026-09-11.md


## 2026-09-09 — PO geometry applies can 400 on initPrice when the analysis price goes stale

VERIFIED 2026-09-09 (live): a revalue-grid apply for XPL (Δ+103.83%) was rejected by WT with 400 'Current price (0.09451) must be between 0.09798 and 0.099071' — the rec's channel was built around the analysis-time 1h-candle price, which the fast-moving token left seconds later. The watch-loop recenters (adjust_bot, live-price-based compute_upsert) DO pass WT validation; the PO apply path uses the analysis-time payload and hits the edge intermittently (2 earlier applies succeeded). The 10-min failed-edit backoff + per-bot PO cooldown contain it; the next analysis regenerates the rec around the fresh price. Pre-existing behavior, unrelated to the PB applied-flip fix; noted for a possible future fix (refresh live price before geometry applies).

## 2026-09-09 — PB recommendation applied-flip never landed: persist closure returned the PB auto id

VERIFIED 2026-09-09: daemon._pb_recommendation_persist returned the PocketBase record id, and the engine's persist step then did rec['id'] = rid — clobbering the engine recommendation uuid. The apply path's _pb_recommendation_update passes rec['id'] into pbclient.recommendation_update, which filters on the recommendation_id field (filled by pbclient.recommendation from the ORIGINAL rec['id'] uuid) — so the filter matched nothing and applied never flipped on persisted records (2 geometry applies journaled, all PB recs applied=false). FIX: _pb_recommendation_persist returns rec.get('id') (the engine uuid); rec['id'] survives the reassignment; recommendation_update matches. +1 regression test pinning the persist→apply id chain; suite 867→868.

Changes:
- agents/grid-autonomy/daemon.py
- agents/grid-autonomy/tests/test_daemon_position_optimizer.py

## 2026-09-09 — Console pnl-chart + sparklines extracted to separate components (wave 3)

VERIFIED 2026-09-09: operator asked for additional frontend components in separate files instead of the already-large app.js (3304 L). Extracted drawPnlChart → components/pnl-chart.js (window.PnlChart.draw, 125 L) and the two sparkline painters → components/sparklines.js (window.Sparklines.render/scoreSVG, 113 L) with thin top-level shims kept in app.js so classic-script globals (window.drawPnlChart) and the mobile.js resize handler survive unchanged. app.js 3304→3180 L. Key gotcha: top-level `let`/`const` bindings in app.js are NOT window properties (the P0-1 lastPnlPoints bug) — components must carry local mirrors (isNum) and call-time-guard shared helpers (window.relTime). Byte-identical A/B smoke + 61/61 console tests + 867 suite green.

Changes:
- agents/grid-autonomy/console/static/components/pnl-chart.js
- agents/grid-autonomy/console/static/components/sparklines.js
- agents/grid-autonomy/console/static/app.js
- agents/grid-autonomy/console/static/index.html

## 2026-09-09 — Idle capital anatomy: 277 of 600 idle is by-design + two structural causes

VERIFIED 2026-09-09 on the live deployment: committed 323 / ceiling 510 (85% of 600) / idle 277 (=600-323). Breakdown: (a) 15% cash buffer = 90 (design); (b) the $120 Binance sleeve is STRANDED — WT demo grid-bot cap is 5/5 consumed by Hyperliquid bots (HL is premium/200 so it wins the paper slots; demo-cap-veto skips new deploys) — BN can never deploy; (c) risk-team max_alloc_mult 0.6-0.7 throttles per-slot commitment to 60-70 of the 90 cap (the LLM risk managers discount the tier target). The sizing math itself is CORRECT (tier × mult over side lines, $10/line exchange floor, worst-case ≤ 50% slot). LEVER: swarm.risk_review prompt now instructs max_alloc_mult semantics (1.0 = full tier target; discount only for concrete risk factors) so the design intent — idle capital into fatter ladders — is actually reachable. Idle_committed_usd=0 confirms no committed capital sits in idle bots.

Changes:
- agents/grid-autonomy/agents/swarm.py

## 2026-09-09 — Rotation double-eval misalignment vetoed every rescreen swap

VERIFIED 2026-09-09: the rescreen rotation pass pre-checks incumbent stagnation with the incumbent's OWN fresh regime + score decay (daemon.py rescreen pass, is_stagnant with fresh.get('regime')), then execute_rotation re-litigates it via should_rotate with CHALLENGER-relative inputs (candidate.regime, inc_score - cand_score). When the challenger's regime matched the policy regime and fills/realized were healthy, the re-check vetoed 'incumbent healthy' — 45 rotation-veto storms in ~7h on healthy incumbents (ARB/JUP/NEAR), one per rescreen cycle, and legitimate swaps on genuinely-decayed incumbents were blocked too. FIX: should_rotate/execute_rotation now accept stag_ok/stag_reasons/inc_score_fresh — the rescreen threads its verdict through and only the Δscore hysteresis gate (vs the FRESH incumbent score) applies. All loss-veto rules untouched; +10 tests; suite 857→867.

Changes:
- agents/grid-autonomy/daemon.py
- agents/grid-autonomy/tests/test_rotation_alignment.py
- agents/grid-autonomy/tests/test_integration.py

## 2026-09-08 — Tier sizing divisor + WT edit contract: two live bugs that capped capital use and broke PO applies

Verified live 2026-09-08 (fresh deployment, 5 paper bots, full-tier archetype at 40 samples / PF 99, committed $280 of $600, $320 idle): (1) build_ticket_payloads divided the tier worst-case budget by grids_n (ALL lines) though only side_lines ≈ half can fill adversely — the $10 exchange floor then dominated every tier, so even a full-tier neutral-risk bot could only reach ~33% of its $180 slot instead of the designed 50% cap ($90). The reliability ladder was symbolic on capital. Fix: per_line = max(min_cost, alloc_usd / side_lines) — worst-case lands exactly on the tier target; the guard chain (worst ≤ min(tier, 0.5)×slot, committed+worst ≤ 85% ceiling) still binds, size-fit math grids ≤ 2·cap/min_cost still exact. Fatter sizing reaches existing bots at their natural recycle points (profit-exit, rotation) because WT applies amountPerTrade only via stop→edit→restart (echoed-not-applied on live edits — verified in browser-debug/docs/wt/grid-bot-api.md) and stopping an underwater bot would realize losses (never-close-at-a-loss). (2) The position-optimizer apply path 500'd on WT while the daemon's own adjust path succeeded on the same bots: _edit_payload emitted a 7-field partial payload, but the upsert endpoint needs the full compute_upsert contract (exchangeCode, profilesCodes, gridType, initPrice, closest*LevelPrice, stopOnOutOfGrid, …). Fix: overlay the geometry onto a copy of the bot's stored deploy upsert (exit keys stripped first so geometry edits never silently rewrite server exits). (3) The 5-demo-bot paper cap (5/5 active) is the binding constraint on fleet size — fatter per-slot sizing is the only idle-capital lever; capital visibility now ships in the console capital-rail component. (4) Orchestrator gotcha: two delegated workers running the same unittest suite concurrently can fail each other's autonomous quality gate with exit-2 collection errors — always re-run the suite on the integrated tree yourself before trusting either worker's green.

Changes:
- agents/grid-autonomy/execution/grid_adapter.py
- agents/grid-autonomy/position_optimizer.py
- agents/grid-autonomy/tests/test_grid_adapter_exits.py
- agents/grid-autonomy/tests/test_position_optimizer.py
- agents/grid-autonomy/console/static/components/capital-rail.js

## 2026-09-08 — Orchestration: subprocess-backed prime-agent delegations cannot be steered mid-flight

delegate without daemonBacked creates a subprocess-backed session: prime_agent send/send_message/prompt all fail with 'Unknown active session' because there is no daemon active session id to address. To add scope to a running subprocess delegation you must stop it and re-delegate a fully self-contained continuation brief (git diff carries the in-flight work; reference it in the new brief instead of describing the code again). Also observed twice this session: workers hitting their autonomousMaxTokens ceiling still finish their work and emit a complete final report while exiting code 1 ('Autonomous quality gate still failing after attempt 1/3: exited 1; autonomous limit reached: maxTokens reached') — the orchestrator must treat the exit code as 'verify me', run the verification bar itself, and not re-dispatch on the exit code alone. And: the delegate 'continue' flag can fail with 'Session is already active' if the most recent saved session is still held — a fresh delegation with a self-contained brief is the reliable path.

## 2026-09-08 — Console /api/llm/health: async pending pattern turns a 70s blocking ping into 13ms

The console's /api/llm/health ran llm/provider.py --ping synchronously inside the request handler: the four-provider chain (mistral 0.4s, cf 2s, nvidia 31s, openrouter 37s — measured live) blocked cold-cache responses ~70s under a 180s subprocess timeout, leaving the 'LLM brains' card on its placeholder after boot and piling up browser retries. Fix (verified locally: cold 13ms, warm 1ms): serve the fresh 60s cache synchronously; on cold/expired answer immediately with pending:true + last-known-good results (a module-level _LLM_HEALTH_LAST that is never evicted on read, only replaced by a successful refresh) while ONE background daemon thread runs the probe (guarded by a lock + refreshing flag so concurrent cold requests spawn exactly one thread); a failed refresh with prior data serves stale:true + the error note. Frontend half: a singleton in-flight promise dedupes the 5s poll ticks, an AbortController bounds the client wait, and a monotonic guard drops responses older than the last rendered one. Generalizable: any console endpoint that shells out to a slow subprocess should follow this pending/stale contract instead of blocking the handler.

Changes:
- agents/grid-autonomy/console/server.py
- agents/grid-autonomy/console/static/components/llm-health.js

## 2026-09-08 — Console UI/mobile wave: top-level let is not a window property; mock the PB ladder's real seams

Verified on the fresh az00 deployment (2026-09-08). (1) A top-level `let` in a classic <script> is a global LEXICAL binding, not a window property — mobile.js's resize handler read window.lastPnlPoints (always undefined) and redrew the PnL canvas with [], wiping real data to a false 'no history' state on every phone rotation. Classic scripts share the global lexical environment, so the bare reference works; always typeof-guard cross-file global reads. (2) console/test_upgrade.py's recommendations test mocked server._http_json but recommendations_payload's PocketBase ladder never reaches it: _pb_client() constructs pbclient.PB lazily (a live client even with a dead PB_URL) and _pb_get() is its own raw-urllib path. Tests must patch server._pb_client AND server._pb_get, and reset the module-level _PB_CLIENT cache in setUp/tearDown or clients leak between tests (order dependence). (3) tests/test_console.py and console/test_upgrade.py encoded OPPOSITE contracts for a dead ctl (raw 'urlopen error' in body.error vs normalized 'ctl unreachable'); the reconciled contract is error='ctl unreachable' + the raw transport string in body.detail.transport — _http_json transport failures must not stuff the exception into body['error'] or _ctl_err() masks a dead daemon as a daemon-answered error.

Changes:
- agents/grid-autonomy/console/static/mobile.js
- agents/grid-autonomy/console/server.py
- agents/grid-autonomy/console/test_upgrade.py
- agents/grid-autonomy/tests/test_console.py

## 2026-09-08 — Keep recs are not applies: cap accounting and a live-network test leak

Two fixes from the recommendations-queue audit (2026-09-08): (1) position_optimizer._persist counted post-deploy entry KEEPS toward max_apply_per_day — four deploys in a day silently filled the 4/day PB-persist cap before any actionable rec could persist (masked only by the twice-restarted daemon resetting the in-memory counter). Keeps are baseline audit records, not applies: they now persist without consuming the cap, the console's persisted_today skips them, they carry no blocked_by verdict (they used to show 'rate limit'), and the UI's Pending table filters them out. (2) tests/test_daemon_manage.test_adopt_records_decision_and_archetype mocked daemon.reclassify_regime, but adopt_existing's regime comes from an INLINE market_regime import over LIVE 1h candles — the test was secretly network-dependent and flipped with the real market (ZEC classified trend_up at 13:30 UTC, chop by 18:30). Fix: mock market_regime.fetch_candles with the flat harness fixture (classifies deterministically as trend_up, matching the existing assertion). Lesson twice over: a mock of function X only proves anything if the code path actually calls X — verify which module attribute the production path imports at runtime.

Changes:
- agents/grid-autonomy/position_optimizer.py
- agents/grid-autonomy/console/server.py
- agents/grid-autonomy/console/static/app.js
- agents/grid-autonomy/tests/test_daemon_manage.py
- agents/grid-autonomy/tests/test_position_optimizer.py
- agents/grid-autonomy/tests/test_console.py

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
