#!/usr/bin/env bash
# vps-run.sh — (re)start the grid-autonomy container on the deployment host.
# Invoked over SSH by .github/workflows/grid-autonomy-deploy.yml after the
# image has been streamed in with `docker load`; also runnable by hand on
# the host. Idempotent: named volumes are created on first use (state,
# PocketBase, browser profile, secrets, bw-cli all survive redeploys), a
# running container is stopped GRACEFULLY first (SIGTERM + 60s — never the
# KILL file), then replaced.
#
# Env:
#   IMAGE      image to run                       (default grid-autonomy:local)
#   NAME       container name                     (default grid-autonomy)
#   ENV_FILE   BW_* env file for vault_loader     (default /opt/grid-autonomy/.env)
#   GRID_MODE  dry-run | live-paper               (default dry-run)
#
# Ports are published on 127.0.0.1 ONLY — the console (:8798) and ctl
# (:8799) carry no built-in auth; reach them through an SSH tunnel:
#   ssh -L 8798:localhost:8798 -L 8799:localhost:8799 <host>
set -euo pipefail

IMAGE="${IMAGE:-grid-autonomy:local}"
NAME="${NAME:-grid-autonomy}"
ENV_FILE="${ENV_FILE:-/opt/grid-autonomy/.env}"
MODE="${GRID_MODE:-dry-run}"

[ -f "$ENV_FILE" ] || { echo "vps-run: missing env file $ENV_FILE" >&2; exit 1; }

# docker needs sudo when the ssh user is not in the docker group
DOCKER="${DOCKER_CMD:-}"
if [ -z "$DOCKER" ]; then
  if docker info >/dev/null 2>&1; then DOCKER=docker; else DOCKER="sudo docker"; fi
fi

for v in grid-state grid-pb grid-profile grid-secrets grid-bwcli; do
  $DOCKER volume create "$v" >/dev/null
done

# graceful replace: SIGTERM + up to 60s settle (entrypoint traps and shuts
# down PB/serve/browser/daemon cleanly), then force-remove the leftovers
if $DOCKER inspect "$NAME" >/dev/null 2>&1; then
  echo "vps-run: stopping old $NAME (graceful, up to 60s)…"
  $DOCKER stop -t 60 "$NAME" >/dev/null 2>&1 || true
  $DOCKER rm -f "$NAME" >/dev/null 2>&1 || true
fi

echo "vps-run: starting $NAME from $IMAGE (GRID_MODE=$MODE)…"
$DOCKER run -d --name "$NAME" \
  --restart unless-stopped \
  --stop-timeout 60 \
  --env-file "$ENV_FILE" \
  -e GRID_MODE="$MODE" \
  -p 127.0.0.1:8798:8798 \
  -p 127.0.0.1:8799:8799 \
  -v grid-state:/app/agents/grid-autonomy/state \
  -v grid-pb:/app/agents/grid-autonomy/.pocketbase \
  -v grid-profile:/data/browser-profile \
  -v grid-secrets:/app/browser-debug/secrets/runtime \
  -v grid-bwcli:/data/bw-cli \
  "$IMAGE"

echo "vps-run: container up — boot (vault load → browser → WT auth → daemon) takes 1-4 min"
