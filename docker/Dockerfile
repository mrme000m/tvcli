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
#    pydantic: the wtclient library (paper-profile bootstrap, the wt_library
#    exit-edit seam, the grid-backtest engine — all fail-soft imports) needs
#    it at import time; without it every seam silently degrades to its
#    no-wtclient fallback and the 6 wtclient-dependent tests error on import.
RUN pip3 install --break-system-packages --no-cache-dir httpx websockets pytest pydantic

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

COPY agents/grid-autonomy/ /app/agents/grid-autonomy/
COPY .agents/skills/cf/ /app/.agents/skills/cf/
COPY .agents/skills/wundertrading/scripts/ /app/.agents/skills/wundertrading/scripts/
COPY .agents/skills/pocketbase/pb_hooks/ /app/.agents/skills/pocketbase/pb_hooks/
COPY .agents/skills/pocketbase/pb_migrations/ /app/.agents/skills/pocketbase/pb_migrations/
COPY --from=builder /out/tvcli /app/tvcli
RUN chmod +x /app/tvcli \
    && mkdir -p /app/browser-debug/secrets/runtime \
              /app/agents/grid-autonomy/state \
              /app/agents/grid-autonomy/.pocketbase \
              /data/browser-profile \
              /data/secrets \
              /data/bw-cli

# ── entrypoint + healthcheck ──────────────────────────────────────────────
COPY docker/entrypoint.sh /usr/local/bin/grid-entrypoint
COPY docker/healthcheck.sh /usr/local/bin/grid-healthcheck
COPY docker/patch_config.py /app/docker/patch_config.py
COPY docker/vault_loader.sh /app/docker/vault_loader.sh
RUN chmod +x /usr/local/bin/grid-entrypoint /usr/local/bin/grid-healthcheck \
             /app/docker/patch_config.py /app/docker/vault_loader.sh

ENV GRID_MODE=live-paper \
    GRID_COMPONENTS=xvfb,pb,serve,browser,daemon,console \
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

EXPOSE 8798 8799
STOPSIGNAL SIGTERM
HEALTHCHECK --start-period=3m --interval=30s --timeout=10s --retries=3 \
  CMD ["grid-healthcheck"]
ENTRYPOINT ["grid-entrypoint"]
