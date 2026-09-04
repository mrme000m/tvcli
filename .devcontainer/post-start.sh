#!/usr/bin/env bash
# post-start.sh — idempotent display stack for headful CloakBrowser: Xvfb on
# :99 (1600x1000x24) exported over VNC on :5900. pgrep -x (exact process
# name) guards: pgrep -f would match this very script's text. Mirrors the
# display tasks in browser-debug/ansible/deps.yml.
set -euo pipefail

sudo sh -c 'pgrep -x Xvfb >/dev/null || (setsid nohup Xvfb :99 -screen 0 1600x1000x24 >/tmp/xvfb.log 2>&1 &)'
for i in $(seq 1 20); do pgrep -x Xvfb >/dev/null && break; sleep 0.5; done
pgrep -x Xvfb >/dev/null || { echo "Xvfb failed to start:"; cat /tmp/xvfb.log; exit 1; }

sudo sh -c 'pgrep -x x11vnc >/dev/null || (setsid nohup x11vnc -display :99 -forever -nopw -rfbport 5900 >/tmp/x11vnc.log 2>&1 &)'
for i in $(seq 1 20); do pgrep -x x11vnc >/dev/null && break; sleep 0.5; done
pgrep -x x11vnc >/dev/null || { echo "x11vnc failed to start:"; cat /tmp/x11vnc.log; exit 1; }

# websockify died once after a clean start (banner logged, process gone) —
# setsid + a second attempt make the noVNC bridge resilient.
sudo sh -c 'pgrep -x websockify >/dev/null || (setsid nohup websockify --web /usr/share/novnc 6080 localhost:5900 >/tmp/websockify.log 2>&1 &)'
for i in $(seq 1 20); do pgrep -x websockify >/dev/null && break; sleep 0.5; done
if ! pgrep -x websockify >/dev/null; then
  sudo sh -c '(setsid nohup websockify --web /usr/share/novnc 6080 localhost:5900 >/tmp/websockify.log 2>&1 &)'
  sleep 2
fi
pgrep -x websockify >/dev/null || { echo "websockify failed to start:"; cat /tmp/websockify.log; exit 1; }

echo "display ready: DISPLAY=:99 (Xvfb $(pgrep -x Xvfb | head -1)), VNC :5900, noVNC http://localhost:6080/vnc.html (websockify $(pgrep -x websockify | head -1))"

# Optional: auto-start the tvcli multi-account HTTP server (parallel
# multi-symbol analysis via POST /hunt, account pool + failover).
# Enable once per workspace with:  touch .tvcli-autoserve
WS="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$WS/.tvcli-autoserve" ] && [ -x "$WS/tvcli" ] && [ -f "$WS/accounts.json" ]; then
  (cd "$WS" && ./tvcli serve --daemon >/dev/null 2>&1) \
    && echo "tvcli server ready: http://localhost:8765 (GET /health /skills /accounts /queue-stats; POST /hunt /run-skill)" \
    || echo "tvcli serve --daemon failed — inspect $WS/.tvcli-server.log"
fi

# Optional: auto-start the dsh Web GUI — the Prime fleet column with the
# prime-orchestrator preset (dsh + dsh-prime-orchestrator plugin, provisioned by
# bootstrapping/ansible/prime-stack.yml). Needs the CLOUDFLARE_ACCOUNT_ID /
# CLOUDFLARE_API_KEY codespace secrets in env (the LLM provider; the
# bw-provisioned opencode.env cannot substitute — its key value is a
# $-pointer). Enable once per workspace with:  touch .dsh-autoweb
if [ -f "$WS/.dsh-autoweb" ] && command -v dsh >/dev/null 2>&1; then
  if [ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ] && [ -n "${CLOUDFLARE_API_KEY:-}" ]; then
    export CLOUDFLARE_AI_TOKEN="$CLOUDFLARE_API_KEY"
    export CF_ACCOUNT_ID="$CLOUDFLARE_ACCOUNT_ID"
    if ! curl -sf http://127.0.0.1:3081/ >/dev/null 2>&1; then
      # 127.0.0.1 only: dsh refuses --host 0.0.0.0 on purpose (it would
      # expose remote code execution to the network). The codespace port
      # forwarder picks up localhost-bound ports anyway.
      # Trust the forwarded host so the Codespace URL's Host header passes
      # the /api browser-trust fence (otherwise WS upgrades via the
      # forwarded URL are treated as untrusted).
      TRUSTED_ARGS=()
      if [ -n "${CODESPACE_NAME:-}" ]; then
        TRUSTED_ARGS+=(--trusted-host "*.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}" --trusted-host "${CODESPACE_NAME}-3081.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}")
      else
        TRUSTED_ARGS+=(--trusted-host "*.app.github.dev")
      fi
      (cd "$HOME" && setsid nohup dsh web --port 3081 --host 127.0.0.1 "${TRUSTED_ARGS[@]}" --no-open >/tmp/dsh-web.log 2>&1 &)
      for i in $(seq 1 60); do curl -sf http://127.0.0.1:3081/ >/dev/null 2>&1 && break; sleep 1; done
      curl -sf http://127.0.0.1:3081/ >/dev/null 2>&1 \
        && echo "dsh web ready: http://localhost:3081 (Prime fleet column in the Web GUI; preset: prime-orchestrator)" \
        || echo "dsh web failed to start — inspect /tmp/dsh-web.log"
    else
      echo "dsh web already running (:3081)"
    fi
    # Codespace port visibility: the forwarded https://<name>-3081.app.github.dev
    # was returning 302 → github.dev/pf-signin when the port was private and the
    # request had no GitHub auth cookie (curl, shared URLs, unauthenticated
    # browser). The devcontainer's portsAttributes now uses onAutoForward but the
    # live visibility still defaults to private — flip it to public so the URL
    # returns 200 without a login redirect (the URL itself is the secret).
    if command -v gh >/dev/null 2>&1 && [ -n "${CODESPACE_NAME:-}" ]; then
      gh codespace ports visibility 3081:public -c "$CODESPACE_NAME" >/dev/null 2>&1 \
        && echo "dsh web port 3081 visibility → public (https://${CODESPACE_NAME}-3081.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}/)" \
        || true
    fi
  else
    echo "dsh web autostart: CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_KEY not in env — skipping (set them as codespace secrets)"
  fi
fi

# Optional: pre-launch the headful CloakBrowser on the Xvfb display
# (logged-out; run `node browser-debug/tv.mjs` to authenticate from
# browser-debug/.env). Enable with:  touch .tv-autobrowser
if [ -f "$WS/.tv-autobrowser" ] && command -v node >/dev/null 2>&1; then
  if ! curl -sf http://127.0.0.1:9222/json/version >/dev/null 2>&1; then
    (cd "$WS/browser-debug" && setsid nohup node launch.mjs >/dev/null 2>&1 &)
    # Cold start (version resolution + first Chromium boot) can take ~60s.
    for i in $(seq 1 60); do curl -sf http://127.0.0.1:9222/json/version >/dev/null 2>&1 && break; sleep 1; done
    curl -sf http://127.0.0.1:9222/json/version >/dev/null 2>&1 \
      && echo "CloakBrowser ready on CDP :9222 (authenticate with browser-debug/tv.mjs)" \
      || echo "CloakBrowser pre-launch failed — run browser-debug/launch.mjs manually"
  else
    echo "CloakBrowser already running (CDP :9222)"
  fi
fi
