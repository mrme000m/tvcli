#!/usr/bin/env bash
# ga-run.sh — (re)start the grid-ga container (the standalone GA agent) on the
# deployment host. Invoked over SSH by .github/workflows/ga-deploy.yml after
# the image has reached the host (registry pull or docker load); also runnable
# by hand on the host. Idempotent: named volumes are created on first use
# (grid-dsh is SHARED with the grid-autonomy container — it holds the seeded
# GA home — plus grid-ga-secrets and grid-ga-bwcli; all survive redeploys),
# the /opt/tvcli workbench is cloned on first use as the invoking user
# (sudo-safe), a running container is stopped GRACEFULLY first (SIGTERM +
# 60s), then replaced.

# Env:
#   IMAGE     image to run                          (default grid-ga:local)
#   NAME      container name                        (default grid-ga)
#   ENV_FILE  BW_* + GH_PUSH_TOKEN env file         (default /opt/grid-ga/.env)
#
# The env file must carry BW_URL/BW_CLIENTID/BW_CLIENTSECRET/BW_PASSWORD
# (vault_loader machine-auth — only the Cloudflare items are loaded) and
# GH_PUSH_TOKEN (bridged into the container as GH_TOKEN so the GA agent can
# `git push` code updates from the /srv/tvcli workbench).
#
# Ports are published on 127.0.0.1 ONLY — dsh web's host publish is
# 127.0.0.1:3082 → container :3081 (az00's caddy owns :3081); the web UI
# carries no built-in auth, so reach it through an SSH tunnel:
#   ssh -L 3082:localhost:3082 <host>
# (dsh.00m.indevs.in on the CF tunnel — grid-net — is the public path for
# the in-container :3081; ingress is ensured by the deploy workflow's
# Cloudflare-API step, pointing at http://grid-ga:3081.)
set -euo pipefail

IMAGE="${IMAGE:-grid-ga:local}"
NAME="${NAME:-grid-ga}"
ENV_FILE="${ENV_FILE:-/opt/grid-ga/.env}"

[ -f "$ENV_FILE" ] || { echo "ga-run: missing env file $ENV_FILE" >&2; exit 1; }
for key in BW_URL BW_CLIENTID BW_CLIENTSECRET BW_PASSWORD GH_PUSH_TOKEN; do
  val="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
  [ -n "$val" ] || { echo "ga-run: $ENV_FILE is missing a value for $key" >&2; exit 1; }
done
GH_PUSH_TOKEN="$(grep -E '^GH_PUSH_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2-)"

# docker needs sudo when the ssh user is not in the docker group
DOCKER="${DOCKER_CMD:-}"
if [ -z "$DOCKER" ]; then
  if docker info >/dev/null 2>&1; then DOCKER=docker; else DOCKER="sudo docker"; fi
fi

# ── volumes ─────────────────────────────────────────────────────────────────
# grid-dsh is REUSED from the grid-autonomy deployment: it already holds the
# seeded GA home (presets, settings, web profile), so a grid-ga first deploy
# picks up yesterday's state instead of starting cold. grid-ga-secrets holds
# the vault-resolved env; grid-ga-bwcli persists the bw CLI login state.
for v in grid-dsh grid-ga-secrets grid-ga-bwcli; do
  $DOCKER volume create "$v" >/dev/null
done

# ── /opt/tvcli — the GA workbench (persistent git clone of the PUBLIC repo) ──
# Created on first use AS THE INVOKING USER (sudo-safe: root only makes the
# directory and hands it over, the clone itself runs unprivileged) so later
# boots' `git pull` and the container's `git push` keep clean ownership.
if [ ! -d /opt/tvcli/.git ]; then
  echo "ga-run: /opt/tvcli missing — cloning the repo (public, anonymous read)"
  SUDO=""
  [ -w /opt ] || SUDO="sudo"
  $SUDO mkdir -p /opt/tvcli
  $SUDO chown "$(id -u):$(id -g)" /opt/tvcli 2>/dev/null || true
  git clone https://github.com/mrme000m/tvcli.git /opt/tvcli
fi

# ── network ────────────────────────────────────────────────────────────────
# grid-net carries the public traffic: the cloudflared connector container
# joins it and reaches grid-ga's dsh web by docker DNS (http://grid-ga:3081);
# grid-ga reaches the grid-autonomy container the same way (console :8798,
# ctl :8799, PocketBase :8090). Kept idempotent: created when missing,
# rejoined on every redeploy.
$DOCKER network create grid-net >/dev/null 2>&1 || true

# ── graceful replace: SIGTERM + up to 60s settle (the entrypoint execs dsh
#    web as PID 1 — SIGTERM terminates it and with it the container), then
#    force-remove the leftovers.
if $DOCKER inspect "$NAME" >/dev/null 2>&1; then
  echo "ga-run: stopping old $NAME (graceful, up to 60s)…"
  $DOCKER stop -t 60 "$NAME" >/dev/null 2>&1 || true
  $DOCKER rm -f "$NAME" >/dev/null 2>&1 || true
fi

echo "ga-run: starting $NAME from $IMAGE…"
$DOCKER run -d --name "$NAME" \
  --restart unless-stopped \
  --stop-timeout 60 \
  --network grid-net \
  --env-file "$ENV_FILE" \
  -e GH_TOKEN="$GH_PUSH_TOKEN" \
  -p 127.0.0.1:3082:3081 \
  -v grid-dsh:/data/dsh \
  -v grid-ga-secrets:/data/secrets \
  -v /opt/tvcli:/srv/tvcli \
  -v grid-ga-bwcli:/data/bw-cli \
  "$IMAGE"

echo "ga-run: container up — boot (vault load → dsh home seed → workbench pull) takes ~30-90s"

# image hygiene: drop dangling layers, then remove older SHA-tagged
# versions of this image left by previous redeploys — the root disk is
# 29GB and each image version is ~1GB, so accumulation would be fatal.
# The running image (by full ID) and every other repository are kept;
# the moving `main` and `buildcache` tags are also kept. NOTE: the
# grid-ga repos pattern matches ONLY ghcr.io/.../grid-ga — the grid-autonomy
# image (same ghcr namespace, different repo name) is never touched.
$DOCKER image prune -f >/dev/null || true
CUR_IMG="$($DOCKER inspect --format '{{.Image}}' "$NAME" 2>/dev/null || true)"
for img in $($DOCKER images --format '{{.Repository}}:{{.Tag}}' \
              | awk -F: '$1 ~ /grid-ga$/ && $2 !~ /^(main|buildcache|local|<none>)$/'); do
  full="$($DOCKER inspect --format '{{.Id}}' "$img" 2>/dev/null || true)"
  [ -n "$full" ] && [ "$full" != "$CUR_IMG" ] && $DOCKER rmi "$img" >/dev/null 2>&1 || true
done
