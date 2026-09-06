# grid-autonomy image — build notes (2026-09-06)

## Build

```
docker build -t grid-autonomy:local -f docker/Dockerfile .
```

- Multi-stage: `golang:1.26-bookworm` builder (static, `CGO_ENABLED=0 GOARCH=$TARGETARCH`,
  cross-compiled from `--platform=$BUILDPLATFORM` so amd64/arm64 both build without qemu)
  → `debian:bookworm-slim` runtime.
- Built and verified on linux/arm64 (colima, Apple Silicon). TARGETARCH-aware, so
  `docker buildx build --platform linux/amd64` works identically.
- Image size: **1.96GB disk / 458MB content** (`docker images grid-autonomy:local`).
- Build context after `.dockerignore`: **~5MB / ~470 files** (whitelist-style ignore:
  `*` then re-includes for go.mod/go.sum, cmd/, internal/, pkg/, docker/{Dockerfile,
  entrypoint.sh, healthcheck.sh, patch_config.py}, agents/grid-autonomy/** (source only),
  .agents/skills/wundertrading/scripts/**, .agents/skills/pocketbase/{pb_hooks,pb_migrations}/**,
  browser-debug/{launch,wt,socks-relay}.mjs). The other worker's compose/env.example/verify.sh/README
  are deliberately excluded from the context (they are not needed to build the image).

## Baked into the image

- `/app/tvcli` — static Go build (12.5MB), `tvcli skills --json` lists the 20-skill registry.
- `/opt/pocketbase/pocketbase` — PocketBase 0.40.2, TARGETARCH zip, `--version` verified
  in-container. Entrypoint copies it into `agents/grid-autonomy/.pocketbase/` and runs the
  (patched, now OS-aware) `scripts/setup_pocketbase.sh`.
- `/app/browser-debug/cloakbrowser/chromium-146.0.7680.177.3/` — CloakBrowser pre-baked at
  build time: `xvfb-run -a env CB_PROFILE=/tmp/cb-build node launch.mjs` downloaded
  (198MB, SHA-256 verified), extracted, launched headful under Xvfb, CDP :9222 answered,
  then chrome was pkilled and the throwaway profile wiped. Build asserts the binary exists.
- Node 22 (NodeSource), python3 + `httpx websockets pytest` (`--break-system-packages`),
  Xvfb + the full Chromium runtime lib set, `xauth` (needed by xvfb-run — first build
  failed without it), procps/psmisc for the supervisor + healthcheck, openssl (PB password
  generation), tzdata.
- `/app/browser-debug/secrets/runtime/` — empty dir; operators bind-mount `wt-session.env`.

## Verified (in-container, `docker run --rm --entrypoint bash grid-autonomy:local -c …`)

| Check | Result |
|---|---|
| `python3 -m unittest discover -s tests -t .` (offline suite) | **520 tests, OK** (11.3s) |
| `node --check` launch.mjs / wt.mjs / socks-relay.mjs | OK |
| `/opt/pocketbase/pocketbase --version` | 0.40.2 |
| `ls` tvcli / pocketbase / cloakbrowser chrome + `tvcli skills --json` | OK (all present, executable) |
| `python3 -c "import httpx, websockets"` | OK (0.28.1 / 17.1) |
| `patch_config.py` against a copy of config.yaml | rewrites both `watch.browser_launch_cmd` → `node /app/browser-debug/launch.mjs` and `watch.wt_restore_cmd` → `node /app/browser-debug/wt.mjs`; indentation + trailing comments preserved; `config.yaml.bak` written only on first change; **second run is a silent no-op (md5 identical)** |
| Secret scan `grep -rE "(SESSION\|SIGNATURE\|DEVICE_T\|PB_ADMIN_PASS\|API_KEY)=" /app /opt` | no secret values — only variable-name references in source/tests |
| Full-stack boot (accidental, see below) | entrypoint brought up all components fail-soft; Docker reported the container **healthy** |

### Accidental full-stack boot (bonus evidence)

During self-testing a `docker run` was issued with `--entrypoint bash` placed after the
image name, so the real `grid-entrypoint` ran the entire stack (no `-p` ports published):
banner/env summary, config patch, Xvfb, PocketBase (baked binary + setup script),
tvcli serve, CloakBrowser, daemon, console all started fail-soft, and the built-in
HEALTHCHECK (`curl -fs :8799/health` + pgrep python3) went **healthy** — i.e. the
daemon's ctl surface answered /health in-container without any external setup.

## Entrypoint behavior worth knowing

- Graceful shutdown (SIGTERM/SIGINT): plain SIGTERM to the daemon (same as
  `scripts/stop.sh`), up to ~25s, then SIGKILL — deliberately **NOT** `POST /kill`
  on :8799, because that endpoint writes the KILL file and would brick the daemon
  across auto-restarts. The KILL file is only warned about at boot (operator clears
  it with `docker compose exec grid-autonomy rm -f /app/agents/grid-autonomy/KILL`);
  the container keeps running.
- `state/daemon.pid` is removed at every fresh boot (single-writer guard from a
  previous container generation would otherwise block startup).
- Daemon death shuts the whole container down (restart policy brings it back);
  other component deaths are logged and the rest keep running.
- Missing LLM env keys are WARNs by default; `GRID_STRICT_ENV=1` upgrades to fatal
  (mirrors `scripts/start.sh`'s strictness, relaxed for Docker).
- `GRID_COMPONENTS` subset control: `xvfb,pb,serve,browser,daemon,console`
  (default all); excluding everything gives an idle toolbox shell (`sleep infinity`).

## Known caveats

- **linux-arm64 CloakBrowser is the fallback build**: the GitHub API reported "no
  release with a binary for this platform" for linux-arm64 during the arm64 build, so
  `launch.mjs` used its bundled fallback 146.0.7680.177.3 (still downloaded from
  cloakbrowser.dev, checksum-verified, CDP-tested). linux-amd64 builds resolve the
  latest release normally. The arm64 CloakBrowser may lag the x64 one in version.
- The image runs as root (Xvfb/chrome under `--no-sandbox`, which launch.mjs defaults
  to). Hardening to a non-root user would need profile/browser permissions work —
  deferred; run on a trusted VPS.
- No secrets are baked; `POST /kill` on :8799 exists at runtime (console/ctl contract)
  but the entrypoint never calls it (see above).
- `agents/grid-autonomy/watchdog/` runtime artifacts (LEDGER.jsonl, STATE.md,
  LAST_FOREIGN_CHECK, reviews/, snapshots/) are excluded from the image — they are
  regenerated at runtime. The daemon's durable state (state/, .pocketbase/pb_data)
  should be bind-mounted/volumed by the compose file (deploy worker's side).
- First browser start in a fresh container reuses the baked browser binary but a fresh
  `/data/browser-profile` volume — WunderTrading cookies must be restored by bind-mounting
  `wt-session.env` at `/app/browser-debug/secrets/runtime/` (wt.mjs picks it up).

## Post-build integration fixes (orchestrator verification pass)

1. **Container bind fix:** `ctl_http.py` + `console/server.py` hardcoded `127.0.0.1`
   binds — published ports (`-p 8798/8799`) were dead inside the container. Both now
   read `GRID_BIND_HOST` (default `127.0.0.1`, so local Mac behavior is unchanged);
   the image sets `ENV GRID_BIND_HOST=0.0.0.0`.
2. **Dockerfile layer order:** the CloakBrowser bake moved BEFORE the app-source
   COPYs, so grid-autonomy source edits no longer invalidate the ~350MB browser
   layer.

## Final runtime verification (orchestrator, full stack + real secrets)

Smoke: `docker run -d` full stack, dry-run mode, real CF LLM keys + TV auth +
WT session cookies, isolated volumes, ports published to 18798/18799 — **10/10
checks PASS**: ctl :8799/health + /status via published port, console :8798
HTTP 200, PocketBase :8090 healthy, tvcli serve :8765 healthy, CloakBrowser
CDP :9222 up, config browser-commands auto-patched, WT page present in the
browser, graceful `docker stop` in 2s. Runtime evidence from the smoke state
volume: the daemon **adopted the live 4-bot fleet** (GIGGLE/CHIP/LTC/GRAM),
read the WT subscription state (gridBots 5/200 premium), reconciled slot
budgets, and wrote decisions with `llm_degraded: false` — the CF Workers AI
LLM chain worked through the container env.

## Phase 2 — vault-driven deployment (WunderTrading auto-login + cookie persistence)

### What was added

- **`docker/vault_loader.sh`** — Bitwarden machine-auth + secret materialization:
  `bw config server` → `bw login --apikey` (idempotent: "already logged in" → OK,
  real failure → FATAL exit 1) → `bw unlock --passwordenv BW_PASSWORD --raw`
  (exported as BW_SESSION) → `bw sync` → resolves the vault items
  (`wundertrading` in folder `grid-autonomy` → WT_EMAIL/WT_PASSWORD;
  `provider-keys` fields → NVIDIA_API_KEY/OPENROUTER_API_KEY/
  MISTRAL_VIBE_API_KEY→MISTRAL_API_KEY/[NVIDIA_BASE_URL];
  `opencode-cloudflare` notes → CLOUDFLARE_ACCOUNT_ID/API_KEY;
  `tvcli-primary-env` notes → /app/.env only when absent; `wundertrading-session`
  notes → wt-session.env only when absent) into `/data/secrets/grid-vault.env`
  (exports, chmod 600). Path overrides `GRID_VAULT_ENV_OUT`/`TVCLI_ENV_OUT`/
  `WT_SESSION_OUT` + `BW_VAULT_ONLY` subset filter for CI/ops. No BW_* env →
  exit 0 "vault disabled". Secret values are NEVER printed.
- **`browser-debug/wt-login.mjs`** — credential login driven by the OFFICIAL
  `cloakbrowser` npm stealth driver on `puppeteer-core` (attached to the
  persistent CloakBrowser via `connect()`; `patchBrowser` swaps page.click/
  page.type for the human layer — Bézier-curve mouse with overshoot,
  per-character typing with 2% mistype-and-correct, shift symbols via CDP
  Input.dispatchKeyEvent = isTrusted=true, isolated-world state checks):
  probe grid_bots (multi-signal, cfChallenge/evaluate-fail never count as
  authed) → if already authed, re-export cookies; else /en/login →
  **dismiss the CookieHub consent overlay first** (`.ch2-allow-all-btn` —
  its backdrop intercepts the submit click; verified live) → humanized
  click+type email/password → click the `button.g-recaptcha.login-button`
  → poll auth up to ~45s (the invisible Google reCAPTCHA executes on
  submit; Cloudflare interstitials keep waiting) → cookies via
  `Storage.getCookies` (Chrome ≥115 removed `Network.getAllCookies`;
  fallback kept for older builds) filtered to wundertrading.com → writes
  `WT_COOKIES_JSON` + `WT_PHPSESSID` + `WT_CF_CLEARANCE` +
  `WT_SESSION_SAVED_AT` (exact format wt.mjs parses), chmod 600. Always
  `browser.disconnect()` — the browser belongs to launch.mjs. Password
  never logged. Driver is imported by absolute path from
  `node_modules/cloakbrowser/dist/human-puppeteer/index.js` (not in the
  package exports map); deps pinned in `browser-debug/package.json` +
  lock, installed via `npm ci --omit=dev` in a dedicated image layer.
  Live-verified E2E (grid-smoke3): consent → fill → POST /en/login 200 →
  recaptcha api2/clr token accepted → redirect /en/trader/dashboard → 11
  cookies persisted → wt.mjs api 200.
- **`docker/entrypoint.sh`** — new step (5a) vault load + source grid-vault.env
  (after llm.env, before validation — vault wins over llm.env); browser step
  now does `wt.mjs open` auth probe → on AUTH FAIL with creds runs wt-login.mjs
  (timeout 120, non-fatal) → re-runs wt.mjs; new untracked background
  **WT session keeper** (every `WT_KEEPER_INTERVAL`, default 1800s: probe →
  re-login → re-assert page); shutdown also TERMs the keeper. mkdir /data/secrets
  + /data/bw-cli at boot.
- **`docker/Dockerfile`** — `npm install -g @bitwarden/cli` (bw 2026.8.0
  verified in-image), COPY wt-login.mjs + vault_loader.sh, ENV
  WT_KEEPER_INTERVAL=1800, /data/secrets + /data/bw-cli baked.
- **compose/env.example/README** — BW_* env documented (available as GitHub
  repo secrets of mrme000m/tvcli); `grid-secrets` named volume REPLACES the
  ./wt-session.env bind-mount (pre-seed via docker cp); documented precedence:
  mounted files win over vault.
- **`.github/workflows/grid-autonomy-vault-smoke.yml`** — manual
  workflow_dispatch CI check: real vault login on ubuntu-latest (bw via npm),
  loader with $RUNNER_TEMP outputs, asserts the materialized keys. No image
  build.

### Phase-2 verification (in-container)

| Check | Result |
|---|---|
| `bw --version` | 2026.8.0 |
| `node --check wt-login.mjs`, `bash -n vault_loader.sh`, `bash -n grid-entrypoint` | OK |
| Offline unittest suite | **520 tests, OK** (12.7s) |
| Secret scan (incl. BW_PASSWORD/BW_CLIENTSECRET patterns) | code references only, no values |
| Baked vault_loader vs mock bw CLI (auth flow, item resolution, folder filter, field mapping, notes parsing, chmod 600, idempotent second run with files winning) | all pass, `summary: vault: wt-creds ok, llm+3, cf+2, tv-env ok, session ok` |
| Real-bw failure path (empty vault state) | clean `FATAL: bw login --apikey failed (rc=1)` + exit 1 |

The mock-bw in-container test was run by piping the test script via stdin
(colima does not bind-share host `/tmp`, and the first mock had a case-syntax
bug — both test-harness issues, not loader issues; final run above is green).

### Phase-2 caveats

- Live vault login + live WunderTrading credential login were intentionally
  NOT exercised here (real credentials — orchestrator's verification step).
  The loader's bw-auth path is the one the orchestrator verified live with
  the real bw CLI; wt-login.mjs follows the wt.mjs CDP patterns exactly.
- `grid-secrets` volume is new in compose; existing deployments that had a
  bind-mounted wt-session.env must pre-seed the volume once (`docker cp`).
- The bw CLI adds ~150MB to the image (2.13GB disk / 458MB+content).

## Phase 3 — cloakbrowser stealth driver for the WT login (2026-09-06)

**Problem (found live in grid-smoke3):** with bogus stale cookies seeded, the
auto-login path correctly detected `AUTH FAIL` but wt-login.mjs (raw CDP
Runtime.evaluate native-setter fills + synthetic clicks) never produced a
session — the form POSTed nothing. Root causes, from live page state + a
network-instrumented diagnostic (browser-debug/diag-login.mjs, dev-only):

1. **Invisible Google reCAPTCHA** (`sa=submit`, site key
   6Lc8NHsa…) gates the Symfony login form (`_username`/`_password`/
   `_csrf_token`, submit = `button.g-recaptcha.login-button`). It scores the
   interaction; CDP-evaluate fills = non-trusted events + evaluate stack
   traces = no token, no POST.
2. **CookieHub consent overlay** (`.ch2-*` buttons) — its backdrop intercepts
   the click on the login button until "Allow all cookies" is clicked.
3. Chrome 146 removed `Network.getAllCookies` (moved to `Storage.getCookies`).

**Fix:** the official `cloakbrowser` npm package (v0.5.10) — the stealth
driver layer on `puppeteer-core` (v25.10.0) — attached at runtime to the SAME
persistent CloakBrowser binary launch.mjs owns: `connect({browserURL:9222})`
→ `patchBrowser(browser, resolveConfig('default'))` → humanized
click/type (Bézier curves, per-char typing with 2% mistype-and-correct,
isTrusted CDP Input keys, isolated-world checks). Driver imported by absolute
path from `dist/human-puppeteer/index.js` (outside the package exports map).
Deps pinned in `browser-debug/package.json` + lock; new Dockerfile layer does
`npm ci --omit=dev` + an import self-check (fails the build if the driver
can't load). wt-login.mjs: consent dismissal first → humanized fill → click
`button.g-recaptcha` → poll ~45s → cookies via `Storage.getCookies` → always
`browser.disconnect()`.

**Live-verified E2E (grid-smoke5, final image, ONLY BW_* env + bogus
`WT_PHPSESSID` seeded):** vault load (8 exports) → wt.mjs restore (bogus,
"existing cookies win") → `AUTH FAIL (stale session?)` → **credential login
in 30s** (POST /en/login 200, recaptcha api2/clr token accepted, redirect
/en/trader/dashboard) → cookies persisted (`WT_SESSION_SAVED_AT` == the
login wall-clock second, mode 600) → session re-restored → `wt.mjs open` →
**AUTH OK** → daemon started (dry-run planning) + keeper (1800s). Graceful
docker stop 0s. 11/12 smoke checks passed; the single FAIL was a grep-wording
mismatch in the smoke script itself ("WT login OK" vs the entrypoint's "WT
credential login OK") — fixed in run_smoke4.sh, which now also requires
`WT_SESSION_SAVED_AT` so a pre-seeded bogus file can't satisfy the
persistence check.
