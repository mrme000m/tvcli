#!/usr/bin/env bash
# Download, configure, and start PocketBase as the grid-autonomy event-driven
# persistence side channel. Idempotent — safe to re-run.
#
# What it does:
#   1. Download the pinned PocketBase binary (darwin-arm64) into a local tool
#      dir (unless already present).
#   2. Create the admin superuser (from PB_ADMIN_EMAIL/PB_ADMIN_PASS, or the
#      env, or sensible local defaults) and an API-key auth record for the
#      daemon.
#   3. Copy the pb_hooks/ JS (collections + fan-out hooks) next to the binary.
#   4. Start `pocketbase serve` on the configured host/port.
#   5. Write a `pb.env` file exporting PB_URL / PB_TOKEN / PB_TIMEOUT so the
#      daemon's pbclient.py picks it up (source it, or export in start.sh).
#
# Env overrides (all optional):
#   PB_VERSION       PocketBase version to pin (default 0.40.2)
#   PB_HOST          bind host (default 127.0.0.1)
#   PB_PORT          bind port (default 8090)
#   PB_DIR           install dir for binary + pb_data + pb_hooks
#                    (default <repo>/agents/grid-autonomy/.pocketbase)
#   PB_ADMIN_EMAIL   superuser email (default admin@localhost)
#   PB_ADMIN_PASS    superuser password (default: generated, shown once)
#   PB_API_KEY_NAME  API-key record name for the daemon (default grid-autonomy)
#
# Usage:
#   scripts/setup_pocketbase.sh            # download + configure + start
#   scripts/setup_pocketbase.sh --no-start # download + configure only
#   source scripts/setup_pocketbase.sh     # exports PB_URL/PB_TOKEN for this shell
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

PB_VERSION="${PB_VERSION:-0.40.2}"
PB_HOST="${PB_HOST:-127.0.0.1}"
PB_PORT="${PB_PORT:-8090}"
PB_DIR="${PB_DIR:-$HERE/.pocketbase}"
PB_ADMIN_EMAIL="${PB_ADMIN_EMAIL:-admin@example.com}"
PB_API_KEY_NAME="${PB_API_KEY_NAME:-grid-autonomy}"

PB_URL="http://${PB_HOST}:${PB_PORT}"
BIN="$PB_DIR/pocketbase"
DATA_DIR="$PB_DIR/pb_data"
HOOKS_DIR="$PB_DIR/pb_hooks"
ENV_FILE="$PB_DIR/pb.env"

START=1
if [ "${1:-}" = "--no-start" ]; then
  START=0
fi

# ── 1. Download (pinned) ────────────────────────────────────────────────────
mkdir -p "$PB_DIR"
if [ ! -x "$BIN" ]; then
  ARCH="$(uname -m)"
  case "$ARCH" in
    arm64|aarch64) PB_ARCH="arm64" ;;
    x86_64|amd64)  PB_ARCH="amd64" ;;
    *) echo "ERROR: unsupported arch $ARCH" >&2; exit 1 ;;
  esac
  ZIP="pocketbase_${PB_VERSION}_darwin_${PB_ARCH}.zip"
  URL="https://github.com/pocketbase/pocketbase/releases/download/v${PB_VERSION}/${ZIP}"
  echo "Downloading PocketBase v${PB_VERSION} (darwin-${PB_ARCH})…"
  curl -fsSL "$URL" -o "$PB_DIR/$ZIP"
  unzip -o -q "$PB_DIR/$ZIP" -d "$PB_DIR"
  rm -f "$PB_DIR/$ZIP"
  chmod +x "$BIN"
  echo "  -> $BIN"
else
  echo "PocketBase already present: $BIN"
fi

# ── 2. Copy migrations + hooks (collections + fan-out) ──────────────────────
SKILL_DIR="$HERE/../../.agents/skills/pocketbase"
mkdir -p "$HOOKS_DIR" "$PB_DIR/pb_migrations"
HOOKS_SRC="$SKILL_DIR/pb_hooks/main.pb.js"
MIGRATIONS_SRC="$SKILL_DIR/pb_migrations/1700000000_grid_autonomy_collections.js"
if [ -f "$HOOKS_SRC" ]; then
  cp "$HOOKS_SRC" "$HOOKS_DIR/main.pb.js"
  echo "Copied hooks: $HOOKS_DIR/main.pb.js"
else
  echo "WARN: hooks not found at $HOOKS_SRC — skipping." >&2
fi
if [ -f "$MIGRATIONS_SRC" ]; then
  cp "$MIGRATIONS_SRC" "$PB_DIR/pb_migrations/1700000000_grid_autonomy_collections.js"
  echo "Copied migration: $PB_DIR/pb_migrations/1700000000_grid_autonomy_collections.js"
else
  echo "WARN: migration not found at $MIGRATIONS_SRC — skipping." >&2
fi

# ── 3. Superuser + API key (idempotent) ─────────────────────────────────────
# Ensure we have a password before creating the superuser. `PB_ADMIN_PASS`
# may be unset on first run, so resolve it defensively (set -u friendly).
if [ -z "${PB_ADMIN_PASS:-}" ]; then
  if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    PB_ADMIN_PASS="$(grep -E '^export PB_ADMIN_PASS=' "$ENV_FILE" | sed 's/^export PB_ADMIN_PASS=//; s/"//g' || true)"
  fi
  if [ -z "${PB_ADMIN_PASS:-}" ]; then
    PB_ADMIN_PASS="$(openssl rand -hex 16 2>/dev/null || head -c16 /dev/urandom | xxd -p)"
    echo "Generated admin password (stored in pb.env): $PB_ADMIN_PASS"
  fi
fi
export PB_ADMIN_PASS

# If pb_data has never been initialized (no superuser), create one.
# NOTE: pass explicit dirs so it uses our hooks/migrations and doesn't load
# anything stale from the CWD. `superuser create` also applies migrations.
if [ ! -d "$DATA_DIR" ]; then
  echo "Initializing PocketBase data dir…"
  "$BIN" superuser create "$PB_ADMIN_EMAIL" "$PB_ADMIN_PASS" \
    --dir="$DATA_DIR" \
    --hooksDir="$HOOKS_DIR" \
    --migrationsDir="$PB_DIR/pb_migrations"
fi

# ── 4. Start the server ──────────────────────────────────────────────────────
if [ "$START" = "1" ]; then
  if [ -f "$PB_DIR/pb.pid" ] && kill -0 "$(cat "$PB_DIR/pb.pid")" 2>/dev/null; then
    echo "PocketBase already running (PID $(cat "$PB_DIR/pb.pid"))."
  else
    echo "Starting PocketBase on $PB_URL …"
    nohup "$BIN" serve \
      --http="$PB_HOST:$PB_PORT" \
      --dir="$PB_DIR/pb_data" \
      --hooksDir="$HOOKS_DIR" \
      --migrationsDir="$PB_DIR/pb_migrations" \
      >> "$PB_DIR/pb.log" 2>&1 &
    echo "$!" > "$PB_DIR/pb.pid"
    echo "  PID: $(cat "$PB_DIR/pb.pid")  log: $PB_DIR/pb.log"
  fi
fi

# ── 5. Authenticate as superuser + write pb.env ──────────────────────────────
# Log in as the superuser and persist the JWT so pbclient.py can write through
# with no manual token injection. The JWT expires (~14 days); pbclient.py
# transparently re-authenticates on 401 using PB_ADMIN_EMAIL/PB_ADMIN_PASS, so
# the persisted token is just a fast-path — not the only path.
PB_TOKEN=""
if [ "$START" = "1" ]; then
  # Wait briefly for the server to accept connections before authenticating.
  for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null "$PB_URL/api/health" 2>/dev/null; then
      break
    fi
    sleep 0.5
  done
  PB_TOKEN="$(curl -fsS -X POST "$PB_URL/api/collections/_superusers/auth-with-password" \
    -H 'Content-Type: application/json' \
    -d "{\"identity\":\"$PB_ADMIN_EMAIL\",\"password\":\"$PB_ADMIN_PASS\"}" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("token",""))' 2>/dev/null || true)"
  if [ -n "$PB_TOKEN" ]; then
    echo "Authenticated as superuser; token persisted to pb.env."
  else
    echo "WARN: could not fetch superuser token; PB_TOKEN left empty (pbclient will re-auth lazily)." >&2
  fi
fi

cat > "$ENV_FILE" <<EOF
export PB_URL="$PB_URL"
export PB_TOKEN="$PB_TOKEN"
export PB_TIMEOUT="5.0"
export PB_ADMIN_EMAIL="$PB_ADMIN_EMAIL"
export PB_ADMIN_PASS="$PB_ADMIN_PASS"
export PB_API_KEY_NAME="$PB_API_KEY_NAME"
export PB_DIR="$PB_DIR"
EOF
echo "Wrote $ENV_FILE"
echo "Source it into the daemon env:  source $ENV_FILE"

# ── 6. Export for `source` usage ─────────────────────────────────────────────
export PB_URL
export PB_TOKEN
export PB_DIR
export PB_ADMIN_EMAIL
export PB_ADMIN_PASS

echo
echo "PocketBase configured."
echo "  URL:      $PB_URL          (dashboard: $PB_URL/_/)"
echo "  Data:     $DATA_DIR"
echo "  Hooks:    $HOOKS_DIR"
echo "  Env:      $ENV_FILE"
echo "  Stop:     kill \$(cat $PB_DIR/pb.pid)"
echo
echo "Next: auth is automatic — pbclient.py reads PB_TOKEN from pb.env and"
echo "re-authenticates on 401 via PB_ADMIN_EMAIL/PB_ADMIN_PASS. Nothing to do."