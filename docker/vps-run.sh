#!/usr/bin/env bash
# vps-run.sh — (re)start the grid-autonomy container on the deployment host.
# Invoked over SSH by .github/workflows/grid-autonomy-deploy.yml after the
# image has reached the host (registry pull or docker load); also runnable
# by hand on the host. Idempotent: named volumes are created on first use
# (state, PocketBase, browser profile, secrets, bw-cli all survive
# redeploys), a running container is stopped GRACEFULLY first (SIGTERM +
# 60s — never the KILL file), then replaced.

# Env:
#   IMAGE      image to run                       (default grid-autonomy:local)
#   NAME       container name                     (default grid-autonomy)
#   ENV_FILE   BW_* env file for vault_loader     (default /opt/grid-autonomy/.env)
#   GRID_MODE  dry-run | live-paper | preserve    (default preserve: keep the
#              running container's mode — pushes must never silently change
#              a fleet's posture; a first deploy with no previous container
#              falls back to live-paper, the deployment default — the VPS
#              runs on its own WunderTrading account)
#
# Ports are published on 127.0.0.1 ONLY — the console (:8798), the ctl
# (:8799) and the dsh web UI (host :3082 → container :3081, the GA agent)
# carry no built-in auth; reach them through an SSH tunnel:
#   ssh -L 8798:localhost:8798 -L 8799:localhost:8799 -L 3082:localhost:3081 <host>
# (dsh.00m.indevs.in on the CF tunnel is the public path for :3081 — ingress
# is ensured by the deploy workflow's Cloudflare-API step.)
set -euo pipefail

IMAGE="${IMAGE:-grid-autonomy:local}"
NAME="${NAME:-grid-autonomy}"
ENV_FILE="${ENV_FILE:-/opt/grid-autonomy/.env}"
MODE="${GRID_MODE:-preserve}"

[ -f "$ENV_FILE" ] || { echo "vps-run: missing env file $ENV_FILE" >&2; exit 1; }

# docker needs sudo when the ssh user is not in the docker group
DOCKER="${DOCKER_CMD:-}"
if [ -z "$DOCKER" ]; then
  if docker info >/dev/null 2>&1; then DOCKER=docker; else DOCKER="sudo docker"; fi
fi

for v in grid-state grid-pb grid-profile grid-secrets grid-bwcli grid-dsh; do
  $DOCKER volume create "$v" >/dev/null
done

# grid-net carries the public traffic: the cloudflared connector container
# joins it and reaches the grid-autonomy container's ports by docker DNS
# (http://grid-autonomy:PORT) — no host port publishing needed for the
# tunnel. Kept idempotent: created when missing, rejoined on every redeploy.
$DOCKER network create grid-net >/dev/null 2>&1 || true

# graceful replace: SIGTERM + up to 60s settle (entrypoint traps and shuts
# down PB/serve/browser/daemon cleanly), then force-remove the leftovers.
# The running mode is captured FIRST so a preserve (default) redeploy keeps
# the fleet's live/dry posture — an automatic push deploy must never
# silently CHANGE the mode in either direction.
OLD_MODE=""
if $DOCKER inspect "$NAME" >/dev/null 2>&1; then
  OLD_MODE="$($DOCKER inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$NAME" \
               | sed -n 's/^GRID_MODE=//p' | tail -1)"
  echo "vps-run: stopping old $NAME (graceful, up to 60s)…"
  $DOCKER stop -t 60 "$NAME" >/dev/null 2>&1 || true
  $DOCKER rm -f "$NAME" >/dev/null 2>&1 || true
fi

if [ "$MODE" = "preserve" ]; then
  MODE="${OLD_MODE:-live-paper}"
  echo "vps-run: preserving previous GRID_MODE ($MODE)"
fi
case "$MODE" in
  dry-run|live-paper) ;;
  *) echo "vps-run: unknown GRID_MODE '$MODE' (want dry-run|live-paper|preserve)" >&2; exit 1 ;;
esac

echo "vps-run: starting $NAME from $IMAGE (GRID_MODE=$MODE)…"
$DOCKER run -d --name "$NAME" \
  --restart unless-stopped \
  --stop-timeout 60 \
  --network grid-net \
  --env-file "$ENV_FILE" \
  -e GRID_MODE="$MODE" \
  -e PB_HOST=0.0.0.0 \
  -p 127.0.0.1:8798:8798 \
  -p 127.0.0.1:8799:8799 \
  -p 127.0.0.1:3082:3081 \   # host :3082 (az00's caddy owns :3081)
  -v grid-state:/app/agents/grid-autonomy/state \
  -v grid-pb:/app/agents/grid-autonomy/.pocketbase \
  -v grid-profile:/data/browser-profile \
  -v grid-secrets:/app/browser-debug/secrets/runtime \
  -v grid-bwcli:/data/bw-cli \
  -v grid-dsh:/data/dsh \
  "$IMAGE"

echo "vps-run: container up — boot (vault load → browser → WT auth → daemon) takes 1-4 min"

# ── cloudflared connector (public hostnames on the CF tunnel) ───────────────
# GRID_TUNNEL_TOKEN in the env file = connector token of the remotely-managed
# 'grid-autonomy' tunnel (ingress + DNS live in Cloudflare; see
# .agents/skills/cf). Ensured on every redeploy so the stack is self-healing
# after host reboots too. Without it, this whole block is a no-op.
TUNNEL_TOKEN="$(grep -E '^GRID_TUNNEL_TOKEN=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
if [ -n "$TUNNEL_TOKEN" ]; then
  if $DOCKER inspect grid-cloudflared >/dev/null 2>&1; then
    echo "vps-run: cloudflared connector already present (grid-cloudflared)"
  else
    echo "vps-run: starting cloudflared connector (grid-cloudflared)…"
    $DOCKER run -d --name grid-cloudflared --restart unless-stopped \
      --network grid-net \
      cloudflare/cloudflared:latest tunnel --no-autoupdate run --token "$TUNNEL_TOKEN"
  fi
else
  echo "vps-run: no GRID_TUNNEL_TOKEN in $ENV_FILE — public tunnel not managed here"
fi

# image hygiene: drop dangling layers, then remove older SHA-tagged
# versions of this image left by previous redeploys — the root disk is
# 29GB and each image version is ~2GB, so accumulation would be fatal.
# The running image (by full ID) and every other repository are kept;
# the moving `main` and `buildcache` tags are also kept.
$DOCKER image prune -f >/dev/null || true
CUR_IMG="$($DOCKER inspect --format '{{.Image}}' "$NAME" 2>/dev/null || true)"
for img in $($DOCKER images --format '{{.Repository}}:{{.Tag}}' \
              | awk -F: '$1 ~ /grid-autonomy$/ && $2 !~ /^(main|buildcache|local|<none>)$/'); do
  full="$($DOCKER inspect --format '{{.Id}}' "$img" 2>/dev/null || true)"
  [ -n "$full" ] && [ "$full" != "$CUR_IMG" ] && $DOCKER rmi "$img" >/dev/null 2>&1 || true
done
