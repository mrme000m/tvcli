# grid-ga — the standalone GA-agent image (dsh web + prime-agent, NO trading).
#
# Sibling of the grid-autonomy container (docker/Dockerfile): the GA agent
# (the `ga` dsh preset served over dsh web) is split OUT of the trading
# container into its own `grid-ga` container so agent work (code edits in
# /srv/tvcli, git pushes, prime-agent workers) never restarts the trading
# stack, and trading-stack redeploys never kill the agent's web sessions.
# Git is the hand-off: changes the GA agent pushes to main auto-deploy the
# grid-autonomy container; only docker/ga/** changes rebuild THIS image.
#
# Deliberately slim: NO Go build, NO browser/Xvfb, NO PocketBase, NO tvcli
# binary — the agent talks to the grid stack over docker DNS
# (http://grid-autonomy:8798 console, :8799 ctl, :8090 PocketBase) and reads
# grid-autonomy source from the /srv/tvcli host-mounted git workbench, not
# from the image.
#
# Secrets are NEVER baked: the GA preset / settings template land in the
# SEED home /opt/dsh-home with an @CF_ACCOUNT_ID@ placeholder, rendered at
# boot from runtime env (vault_loader → /data/secrets/grid-vault.env).

FROM debian:bookworm-slim AS runtime

ENV DEBIAN_FRONTEND=noninteractive

# ── base OS packages: runtime deps + the setup/bootstrap tools
#    (ca-certificates/curl/unzip/bash/procps for the scripts and healthcheck,
#    git for the /srv/tvcli workbench bootstrap + the `github:` plugin spec,
#    jq for the deploy tooling, python3/python3-pip for prime-agent and the
#    preset-render helpers). Layer-order rationale (mirrors the grid
#    Dockerfile): OS-level layers first — they change least often, so every
#    layer above them stays a cache hit. ──────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl unzip bash procps tzdata \
      git jq \
      python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# ── Node 22 (NodeSource) — same pattern as the grid Dockerfile; dsh and the
#    dsh-prime-orchestrator plugin forwarder need >= 22 (global WebSocket). ──
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && node --version

# ── GitHub CLI from the official apt repo (cli.github.com/packages) — the
#    GA agent monitors deploy pipelines (`gh run watch`) and pushes code
#    updates from the /srv/tvcli workbench (`gh auth setup-git` with the
#    runtime GH_TOKEN). ─────────────────────────────────────────────────────
RUN set -eux; \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /usr/share/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends gh; \
    rm -rf /var/lib/apt/lists/*; \
    gh --version

# ── Bitwarden CLI (vault_loader.sh machine-auth at container boot) ────────
RUN npm install -g --no-fund --no-audit @bitwarden/cli \
    && bw --version

# ── Python deps: the grid-autonomy unittest bar — the GA agent verifies its
#    own improvement work packages against the same suite
#    (python3 -m unittest discover -s tests -t .) inside the container. ────
RUN pip3 install --break-system-packages --no-cache-dir httpx websockets pytest

# ── DSH (deepseek-harness) + prime-agent — the GA agent stack ──────────────
# The verified install set from bootstrapping/python/prime_stack (stages/
# {dsh,agent,plugin}.py): pnpm >= 10 (the dsh plugin forwarder requirement),
# dsh EXACTLY 0.1.1-rc.2 (plugin compatibility table: 0.1.0-rc.7 / 0.1.1-rc.2
# only), and the ponytail patch so `dsh web --host 0.0.0.0` is allowed (the
# GA container must bind all interfaces for the docker-network tunnel path —
# only reachable via grid-net + the 127.0.0.1 host publish).
# Layer-order note (same rationale as the grid Dockerfile): AFTER the
# base/Node layers (OS edits never rebuild it) and BEFORE the payload COPYs
# (preset edits never rebuild it either). Secrets are NEVER baked: the CF
# account id is an @CF_ACCOUNT_ID@ placeholder in the settings template,
# rendered at boot from runtime env; API keys stay runtime-env via
# vault_loader. Disk gotcha (az00 root disk): the npm cache is dropped IN THE
# SAME LAYER so the bytes never enter the image history.
ARG GA_PRESET_REV=dev
COPY docker/ga/dsh_ponytail_patch.py /tmp/dsh_ponytail_patch.py
RUN set -eux; \
    npm install -g --no-fund --no-audit pnpm@10; \
    npm install -g --no-fund --no-audit @deepseek-ai/dsh@0.1.1-rc.2; \
    rm -rf /root/.npm/_cacache; \
    python3 /tmp/dsh_ponytail_patch.py \
        "$(npm root -g)/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-web-app/lib/startup.js"; \
    dsh --version

# prime-agent CLI — official installer (the verified pattern from the
# prime_stack agent stage). Under docker build there is no controlling tty,
# so every prompt falls back to its default; `setsid --wait` + closed stdin
# make that explicit (the installer's /dev/tty prompt helper). The uv cache
# the installer's kernel-venv provisioning leaves behind is pure download
# cache (~1GB of scipy/numpy wheels — it broke the az00 pull with "no space
# left on device", run 34170323380) and is removed IN THE SAME LAYER so the
# bytes never enter the image history.
RUN set -eux; \
    curl -fsSL https://app.primeintellect.ai/prime-agent/install.sh -o /tmp/prime-agent-install.sh; \
    setsid --wait sh /tmp/prime-agent-install.sh </dev/null; \
    rm -f /tmp/prime-agent-install.sh; \
    rm -rf /root/.cache/uv /root/.cache/pip /root/.npm/_cacache; \
    command -v prime-agent; \
    prime-agent --version

# dsh-prime-orchestrator into the baked `web` profile — `dsh plugin` forwards
# to pnpm in the profile dir (git from the base layer resolves the github:
# spec). pnpm blocks the git-hosted dep's build scripts with
# ERR_PNPM_GIT_DEP_PREPARE_NOT_ALLOWED (verified on pnpm 10.x in CI, run
# 34169182646 — the prime_stack plugin stage's "pnpm 10 needs no remedy"
# note is stale), so the install runs the stage's verified allowBuilds
# remedy: parse the demanded `name@<tarball-url-with-sha>` keys from the
# failure log, merge them into the profile's pnpm-workspace.yaml, retry.
# The built-artifact assert mirrors the stage: a listed-but-unbuilt
# dependency is a half-state the runtime must not inherit.
COPY docker/ga/pnpm_allowbuilds.py /tmp/pnpm_allowbuilds.py
RUN set -eux; \
    if ! DSH_HOME=/opt/dsh-home dsh plugin --profile web add github:mrme000m/dsh-prime-orchestrator \
            > /tmp/plugin-add.log 2>&1; then \
        cat /tmp/plugin-add.log; \
        python3 /tmp/pnpm_allowbuilds.py /opt/dsh-home/profiles/web /tmp/plugin-add.log; \
        DSH_HOME=/opt/dsh-home dsh plugin --profile web add github:mrme000m/dsh-prime-orchestrator; \
    fi; \
    test -f /opt/dsh-home/profiles/web/node_modules/dsh-prime-orchestrator/lib/index.js

# ── GA payloads (cheap COPYs — always LAST so they never invalidate the
#    expensive layers above). All preset payloads land in the SEED home
#    /opt/dsh-home; the entrypoint seeds the writable runtime home
#    (DSH_HOME=/data/dsh, the grid-dsh volume — it already holds the GA home
#    seeded by the earlier coupled deployment, so a grid-ga first boot is a
#    revision refresh, not a wipe). The preset references the
#    /srv/tvcli workbench (host-mounted git clone), NOT baked paths. ──────
COPY docker/ga/ga-preset/ /opt/dsh-home/.agent-presets/ga/
COPY docker/ga/dsh-settings.yaml /opt/dsh-home/settings.yaml
# ga scripts: the runtime supervisor, healthcheck, vault loader (BW_VAULT_ONLY=cf
# subset — only the Cloudflare items) and the prime-agent config renderer.
COPY docker/ga/entrypoint.sh /app/ga/entrypoint.sh
COPY docker/ga/healthcheck.sh /app/ga/healthcheck.sh
COPY docker/ga/vault_loader.sh /app/ga/vault_loader.sh
COPY docker/ga/prime_agent_config.py /app/ga/prime_agent_config.py
RUN chmod +x /app/ga/entrypoint.sh /app/ga/healthcheck.sh /app/ga/vault_loader.sh \
    && mkdir -p /data/dsh /data/secrets /data/bw-cli /srv/tvcli \
    && printf '%s\n' "${GA_PRESET_REV}" > /opt/dsh-home/.baked-rev

ENV DSH_HOME=/data/dsh \
    DSH_WEB_PORT=3081 \
    GA_REPO=/srv/tvcli \
    GRID_BIND_HOST=0.0.0.0

# dsh web only (in-container :3081; the host publish is 127.0.0.1:3082 —
# az00's caddy owns :3081 — and the public path is the CF tunnel).
EXPOSE 3081
STOPSIGNAL SIGTERM
HEALTHCHECK --start-period=2m --interval=30s --timeout=10s --retries=3 \
  CMD curl -fs -m 5 http://127.0.0.1:3081/ || exit 1
ENTRYPOINT ["/app/ga/entrypoint.sh"]
