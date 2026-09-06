#!/usr/bin/env bash
# grid-autonomy — host-side deployment smoke check.
#
# Verifies, from the HOST (not inside the container):
#   1. the Docker daemon is reachable,
#   2. the grid-autonomy image exists,
#   3. the container is up (compose or plain docker),
#   4. the daemon control plane answers on :8799/health,
#   5. the mission console answers on :8798,
#   6. (optional) PocketBase answers on a published :8090.
#
# Usage:
#   ./verify.sh                                   # defaults: localhost 8799/8798
#   ./verify.sh vps.example.com                   # override host
#   ./verify.sh vps.example.com 8799 8798         # override host + both ports
#   PB_CHECK=1 ./verify.sh [...]                  # also check :8090 (published)
#   HOST=... CTL_PORT=... CONSOLE_PORT=... PB_PORT=... ./verify.sh   # env form
#
# Any FAIL makes the final exit status non-zero.
set -euo pipefail

HOST="${HOST:-localhost}"
CTL_PORT="${CTL_PORT:-8799}"
CONSOLE_PORT="${CONSOLE_PORT:-8798}"
PB_PORT="${PB_PORT:-8090}"
IMAGE="${IMAGE:-grid-autonomy:local}"
CONTAINER="${CONTAINER:-grid-autonomy}"
COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Positional overrides: [HOST] [CTL_PORT] [CONSOLE_PORT] [--pb]
NARG=0
while [ $# -gt 0 ]; do
  case "$1" in
    --pb) PB_CHECK=1 ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)
      NARG=$((NARG + 1))
      case "$NARG" in
        1) HOST="$1" ;;
        2) CTL_PORT="$1" ;;
        3) CONSOLE_PORT="$1" ;;
        *) echo "verify.sh: unexpected extra argument: $1" >&2; exit 2 ;;
      esac ;;
  esac
  shift
done

PASS=0
FAIL=0
ok()  { printf 'PASS  %s\n' "$1"; PASS=$((PASS + 1)); }
bad() { printf 'FAIL  %s\n' "$1"; FAIL=$((FAIL + 1)); }
skip(){ printf 'SKIP  %s\n' "$1"; }

hr() { printf -- '──── %s ────\n' "$1"; }

# ── 1. Docker daemon ───────────────────────────────────────────────────────
hr "docker daemon"
if docker info >/dev/null 2>&1; then
  ok "docker daemon reachable ($(docker version --format '{{.Server.Version}}' 2>/dev/null || echo version?))"
else
  bad "docker daemon not reachable (is dockerd running? are you in the docker group?)"
fi

# ── 2. Image ──────────────────────────────────────────────────────────────
hr "image"
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  ok "image $IMAGE present"
else
  bad "image $IMAGE missing (build it: docker build -t $IMAGE -f docker/Dockerfile . from the repo root — or docker load a shipped image and retag it)"
fi

# ── 3. Container up ───────────────────────────────────────────────────────
hr "container"
if [ -f "$COMPOSE_DIR/docker-compose.yml" ]    && docker compose -f "$COMPOSE_DIR/docker-compose.yml" ps -q grid-autonomy >/dev/null 2>&1    && [ -n "$(docker compose -f "$COMPOSE_DIR/docker-compose.yml" ps -q grid-autonomy 2>/dev/null)" ]; then
  if docker ps --filter "name=$CONTAINER" --filter "status=running" -q | grep -q .; then
    STATE="$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || true)"
    ok "compose service grid-autonomy running${STATE:+ (health: $STATE)}"
  else
    bad "compose service grid-autonomy exists but is not running (docker compose up -d, then docker compose logs -f)"
  fi
elif [ -n "$(docker ps -a --filter "name=$CONTAINER" -q 2>/dev/null)" ]; then
  if [ -n "$(docker ps --filter "name=$CONTAINER" --filter "status=running" -q)" ]; then
    ok "container $CONTAINER running"
  else
    bad "container $CONTAINER exists but is not running"
  fi
else
  bad "no running grid-autonomy container found (docker compose up -d from docker/)"
fi

# ── 4. Daemon control plane :8799/health ──────────────────────────────────
hr "daemon control plane ($HOST:$CTL_PORT)"
BODY="$(curl -fsS -m 10 "http://$HOST:$CTL_PORT/health" 2>/dev/null || true)"
if [ -n "$BODY" ]; then
  if printf '%s' "$BODY" | grep -q '"status"'; then
    ok "GET /health answered: $BODY"
  else
    bad "GET /health answered without a status payload: $BODY"
  fi
else
  bad "GET http://$HOST:$CTL_PORT/health did not answer (daemon not up yet? tunnel/firewall? docker compose logs -f)"
fi

# ── 5. Mission console :8798 ──────────────────────────────────────────────
hr "mission console ($HOST:$CONSOLE_PORT)"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -m 10 "http://$HOST:$CONSOLE_PORT/" || true)"
if [ "$CODE" = "200" ]; then
  ok "console answered HTTP 200"
elif [ -n "$CODE" ] && [ "$CODE" != "000" ]; then
  bad "console answered HTTP $CODE (expected 200)"
else
  bad "console at http://$HOST:$CONSOLE_PORT/ unreachable"
fi

# ── 6. PocketBase :8090 (only when published) ──────────────────────────────
hr "pocketbase ($HOST:$PB_PORT, optional)"
if [ "${PB_CHECK:-0}" = "1" ]; then
  if curl -fsS -m 10 "http://$HOST:$PB_PORT/api/health" >/dev/null 2>&1; then
    ok "pocketbase answered on the published port"
  else
    bad "pocketbase not reachable on http://$HOST:$PB_PORT/api/health (published? PB_HOST=0.0.0.0? debug profile running?)"
  fi
else
  skip "pocketbase check not requested (pass --pb or PB_CHECK=1; normally :8090 stays container-internal — check it with: docker compose exec grid-autonomy curl -s http://127.0.0.1:8090/api/health)"
fi

# ── Summary ────────────────────────────────────────────────────────────────
printf '──── summary: %d passed, %d failed ────\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
  printf 'verify: FAILED\n'
  exit 1
fi
printf 'verify: OK\n'
