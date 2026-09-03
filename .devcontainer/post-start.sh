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
