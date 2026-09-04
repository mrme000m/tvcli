#!/usr/bin/env bash
# Stop the grid-autonomy daemon: POST /kill to the ctl plane, then SIGTERM.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

STATE_DIR="$HERE/state"
PID_FILE="$STATE_DIR/daemon.pid"
PORT="${GRID_DAEMON_PORT:-8799}"

if [ ! -f "$PID_FILE" ]; then
  echo "no PID file at $PID_FILE — nothing to stop"
  exit 0
fi
PID="$(cat "$PID_FILE")"

# 1. Ask the ctl plane to halt (writes the KILL file).
python3 - "$PORT" <<'PY'
import sys
import urllib.request

port = sys.argv[1]
try:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/kill", data=b"", method="POST")
    urllib.request.urlopen(req, timeout=3)
except Exception:
    pass
PY

# 2. SIGTERM the pid, then wait (then escalate).
if kill -0 "$PID" 2>/dev/null; then
  kill -TERM "$PID" 2>/dev/null || true
  waited=0
  while kill -0 "$PID" 2>/dev/null && [ "$waited" -lt 30 ]; do
    sleep 0.5
    waited=$((waited + 1))
  done
  if kill -0 "$PID" 2>/dev/null; then
    echo "daemon (PID $PID) did not exit after SIGTERM — sending SIGKILL" >&2
    kill -KILL "$PID" 2>/dev/null || true
  fi
fi

rm -f "$PID_FILE"
echo "daemon stopped (was PID $PID)"

# 3. Also stop PocketBase (the event-driven persistence side channel), if we
# started it. Low risk: leaves pb_data intact so state survives a restart.
PB_PID_FILE="$HERE/.pocketbase/pb.pid"
if [ -f "$PB_PID_FILE" ]; then
  PB_PID="$(cat "$PB_PID_FILE")"
  if kill -0 "$PB_PID" 2>/dev/null; then
    kill -TERM "$PB_PID" 2>/dev/null || true
    echo "PocketBase stopped (was PID $PB_PID)"
  else
    echo "PocketBase already stopped (stale PID $PB_PID)."
  fi
  rm -f "$PB_PID_FILE"
fi
