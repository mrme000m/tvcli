# grid-autonomy — self-contained VPS image (one container, all components).
#
# Stage 1 builds the tvcli Go binary (static, TARGETARCH-aware, so the same
# Dockerfile produces linux/amd64 + linux/arm64 without qemu emulation).
# Stage 2 is a debian:bookworm-slim runtime carrying: python3 (+httpx/
# websockets/pytest), Node 22, Xvfb + the Chromium runtime libs, a baked
# PocketBase binary, and a pre-downloaded CloakBrowser build (the daemon's
# WT session transport).
#
# Secrets are NEVER baked: .env / accounts.json / browser-debug/secrets/ are
# excluded by .dockerignore and bind-mounted at runtime.

FROM --platform=$BUILDPLATFORM golang:1.26-bookworm AS builder
ARG TARGETARCH
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd/ ./cmd/
COPY internal/ ./internal/
COPY pkg/ ./pkg/
RUN CGO_ENABLED=0 GOOS=linux GOARCH=${TARGETARCH} \
    go build -trimpath -o /out/tvcli ./cmd/tvcli

FROM debian:bookworm-slim AS runtime
ARG TARGETARCH
ARG PB_VERSION=0.40.2

ENV DEBIAN_FRONTEND=noninteractive

# ── base OS packages: runtime deps + Chromium/Xvfb libs (from the
#    verified .devcontainer list) + ca-certificates/curl/unzip/bash/procps
#    for the setup scripts, healthcheck and browser bake. ──────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl unzip bash procps openssl tzdata xauth \
      xvfb libglib2.0-0 libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
      libcups2 libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 \
      libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
      libatspi2.0-0 libgtk-3-0 fonts-liberation \
      python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# ── Node 22 (NodeSource) — launch.mjs/wt.mjs need >= 22 (global WebSocket) ──
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && node --version

# ── Bitwarden CLI (vault_loader.sh machine-auth at container boot) ────────
RUN npm install -g --no-fund --no-audit @bitwarden/cli \
    && bw --version

# ── Python deps for the daemon's helper imports (wt_browser.py) + tests ────
RUN pip3 install --break-system-packages --no-cache-dir httpx websockets pytest

# ── PocketBase, pinned per-TARGETARCH (baked; the entrypoint copies it into
#    agents/grid-autonomy/.pocketbase/ and scripts/setup_pocketbase.sh runs it) ──
RUN set -eux; \
    case "${TARGETARCH}" in amd64|arm64) ;; *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; esac; \
    mkdir -p /opt/pocketbase; \
    curl -fsSL -o /tmp/pb.zip \
      "https://github.com/pocketbase/pocketbase/releases/download/v${PB_VERSION}/pocketbase_${PB_VERSION}_linux_${TARGETARCH}.zip"; \
    unzip -o -q /tmp/pb.zip -d /opt/pocketbase; \
    rm -f /tmp/pb.zip; \
    chmod +x /opt/pocketbase/pocketbase; \
    /opt/pocketbase/pocketbase --version

# ── app tree (context is whitelist-pruned by .dockerignore) ────────────────
WORKDIR /app

# Browser scripts FIRST + CloakBrowser bake (~350MB download at build time):
# launch.mjs resolves the latest free-tier linux build, downloads + extracts
# it under browser-debug/cloakbrowser/chromium-<version>/, then spawns a
# detached headful chrome under a throwaway Xvfb + throwaway CB_PROFILE. We
# kill the spawned chrome, wipe the throwaway profile, and assert the binary
# exists (build fails otherwise — no browser, no image). Layer-order note:
# this step precedes the app-source COPYs so grid-autonomy source edits do
# NOT invalidate the expensive browser layer.
COPY browser-debug/launch.mjs browser-debug/wt.mjs browser-debug/socks-relay.mjs browser-debug/wt-login.mjs /app/browser-debug/
RUN set -eux; \
    xvfb-run -a env CB_PROFILE=/tmp/cb-build node /app/browser-debug/launch.mjs; \
    sleep 3; \
    pkill -f chrome || true; \
    sleep 1; \
    rm -rf /tmp/cb-build; \
    test -x /app/browser-debug/cloakbrowser/chromium-*/chrome

# ── WT login stealth driver: the official cloakbrowser npm package — the
#    human layer (Bezier-curve mouse, per-char typing with mistype
#    simulation, isTrusted CDP key events, isolated-world state checks) on
#    puppeteer-core, attached at runtime to the baked CloakBrowser binary.
#    The WT /en/login form is gated by an invisible Google reCAPTCHA which
#    scores the interaction; raw CDP evaluate form-fills fail it (verified
#    live). cloakbrowser is pinned: the driver is imported by absolute path
#    from dist/human-puppeteer/index.js (not in the package exports map). ──
COPY browser-debug/package.json browser-debug/package-lock.json /app/browser-debug/
RUN set -eux; \
    cd /app/browser-debug; \
    npm ci --no-fund --no-audit --omit=dev; \
    node --input-type=module \
      -e "import {pathToFileURL} from 'node:url'; const m = await import(pathToFileURL('/app/browser-debug/node_modules/cloakbrowser/dist/human-puppeteer/index.js').href); const c = m.resolveConfig('default'); console.log('cloakbrowser driver OK (typing_delay=' + c.typing_delay + 'ms)');"

# ── DSH (deepseek-harness) + prime-agent — the GA agent stack ──────────────
# The verified install set from bootstrapping/python/prime_stack (stages/
# {dsh,agent,plugin}.py): pnpm >= 10 (the dsh plugin forwarder requirement),
# dsh EXACTLY 0.1.1-rc.2 (plugin compatibility table: 0.1.0-rc.7 / 0.1.1-rc.2
# only), git (the `github:` plugin spec clones), the dsh-prime-orchestrator
# plugin baked into a `web` profile under the baked seed home /opt/dsh-home,
# and the prime-agent CLI. Layer-order note (same rationale as the
# CloakBrowser bake): AFTER the expensive browser layers (browser/OS edits
# never rebuild it) and BEFORE the app-source COPYs (grid-autonomy source
# edits never rebuild it either). Secrets are NEVER baked: the CF account id
# is an @CF_ACCOUNT_ID@ placeholder in the settings template, rendered at
# boot from runtime env; API keys stay runtime-env via vault_loader.
ARG GA_PRESET_REV=dev
COPY docker/dsh_ponytail_patch.py /tmp/dsh_ponytail_patch.py
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends git; \
    rm -rf /var/lib/apt/lists/*; \
    npm install -g --no-fund --no-audit pnpm@10; \
    npm install -g --no-fund --no-audit @deepseek-ai/dsh@0.1.1-rc.2; \
    rm -rf /root/.npm/_cacache; \
    python3 /tmp/dsh_ponytail_patch.py \
        "$(npm root -g)/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-web-app/lib/startup.js"; \
    dsh --version

# prime-agent CLI — official installer (the verified pattern from the
# prime_stack agent stage). Under docker build there is no controlling tty,
# so every prompt falls back to its default; `setsid --wait` + closed stdin
# make that explicit (the installer's /dev/tty prompt helper). The npm-global
# install lands `prime-agent` on PATH (/usr/bin) for the runtime supervisor.
# The uv cache the installer's kernel-venv provisioning leaves behind is
# pure download cache (~1GB of scipy/numpy wheels — it broke the az00 pull
# with "no space left on device", run 34170323380) and is removed IN THE
# SAME LAYER so the bytes never enter the image history.
RUN set -eux; \
    curl -fsSL https://app.primeintellect.ai/prime-agent/install.sh -o /tmp/prime-agent-install.sh; \
    setsid --wait sh /tmp/prime-agent-install.sh </dev/null; \
    rm -f /tmp/prime-agent-install.sh; \
    rm -rf /root/.cache/uv /root/.cache/pip; \
    command -v prime-agent; \
    prime-agent --version

# dsh-prime-orchestrator into the baked `web` profile — `dsh plugin` forwards
# to pnpm in the profile dir (git from the layer above resolves the github:
# spec). pnpm blocks the git-hosted dep's build scripts with
# ERR_PNPM_GIT_DEP_PREPARE_NOT_ALLOWED (verified on pnpm 10.x in CI, run
# 34169182646 — the prime_stack plugin stage's "pnpm 10 needs no remedy"
# note is stale), so the install runs the stage's verified allowBuilds
# remedy: parse the demanded `name@<tarball-url-with-sha>` keys from the
# failure log, merge them into the profile's pnpm-workspace.yaml, retry.
# The built-artifact assert mirrors the stage: a listed-but-unbuilt
# dependency is a half-state the runtime must not inherit.
COPY docker/pnpm_allowbuilds.py /tmp/pnpm_allowbuilds.py
RUN set -eux; \
    if ! DSH_HOME=/opt/dsh-home dsh plugin --profile web add github:mrme000m/dsh-prime-orchestrator \
            > /tmp/plugin-add.log 2>&1; then \
        cat /tmp/plugin-add.log; \
        python3 /tmp/pnpm_allowbuilds.py /opt/dsh-home/profiles/web /tmp/plugin-add.log; \
        DSH_HOME=/opt/dsh-home dsh plugin --profile web add github:mrme000m/dsh-prime-orchestrator; \
    fi; \
    test -f /opt/dsh-home/profiles/web/node_modules/dsh-prime-orchestrator/lib/index.js

COPY agents/grid-autonomy/ /app/agents/grid-autonomy/
COPY .agents/skills/cf/ /app/.agents/skills/cf/
COPY .agents/skills/wundertrading/scripts/ /app/.agents/skills/wundertrading/scripts/
COPY .agents/skills/pocketbase/pb_hooks/ /app/.agents/skills/pocketbase/pb_hooks/
COPY .agents/skills/pocketbase/pb_migrations/ /app/.agents/skills/pocketbase/pb_migrations/
# GA agent stack payloads: the skill dirs the GA preset's customSkillDirs
# references (its second entry is /app/.agents/skills — grid-autonomy is the
# operating manual the persona mandates loading, grid-bot the bot-type
# reference), plus the vendored GA preset (docker/ga-preset/, container
# paths) and the dsh settings template. All land in the SEED home
# /opt/dsh-home; the entrypoint seeds the writable runtime home
# (DSH_HOME=/data/dsh, the grid-dsh volume) from it on first boot.
COPY .agents/skills/grid-autonomy/ /app/.agents/skills/grid-autonomy/
COPY .agents/skills/grid-bot/ /app/.agents/skills/grid-bot/
COPY docker/ga-preset/ /opt/dsh-home/.agent-presets/ga/
COPY docker/dsh-settings.yaml /opt/dsh-home/settings.yaml
COPY --from=builder /out/tvcli /app/tvcli
RUN chmod +x /app/tvcli \
    && mkdir -p /app/browser-debug/secrets/runtime \
              /app/agents/grid-autonomy/state \
              /app/agents/grid-autonomy/.pocketbase \
              /data/browser-profile \
              /data/secrets \
              /data/bw-cli \
              /data/dsh \
    && printf '%s\n' "${GA_PRESET_REV}" > /opt/dsh-home/.baked-rev

# ── entrypoint + healthcheck ──────────────────────────────────────────────
COPY docker/entrypoint.sh /usr/local/bin/grid-entrypoint
COPY docker/healthcheck.sh /usr/local/bin/grid-healthcheck
COPY docker/patch_config.py /app/docker/patch_config.py
COPY docker/vault_loader.sh /app/docker/vault_loader.sh
COPY docker/prime_agent_config.py /app/docker/prime_agent_config.py
RUN chmod +x /usr/local/bin/grid-entrypoint /usr/local/bin/grid-healthcheck \
             /app/docker/patch_config.py /app/docker/vault_loader.sh \
             /app/docker/prime_agent_config.py

ENV GRID_MODE=live-paper \
    GRID_COMPONENTS=xvfb,pb,serve,browser,daemon,console,dsh \
    DSH_HOME=/data/dsh \
    GRID_STRICT_ENV=0 \
    WT_KEEPER_INTERVAL=1800 \
    GRID_BIND_HOST=0.0.0.0 \
    CB_PROFILE=/data/browser-profile \
    DISPLAY=:99 \
    TVCLI_SERVER=http://127.0.0.1:8765 \
    PB_ADMIN_EMAIL=admin@example.com \
    PB_HOST=127.0.0.1 \
    PB_PORT=8090 \
    PB_VERSION=0.40.2

EXPOSE 8798 8799 3081
STOPSIGNAL SIGTERM
HEALTHCHECK --start-period=3m --interval=30s --timeout=10s --retries=3 \
  CMD ["grid-healthcheck"]
ENTRYPOINT ["grid-entrypoint"]
