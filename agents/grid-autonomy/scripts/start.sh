#!/usr/bin/env bash
# Start the grid-autonomy daemon. Default = dry-run planning mode.
# Pass --live-paper to actually create paper bots (integration phase only).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

STATE_DIR="$HERE/state"
PID_FILE="$STATE_DIR/daemon.pid"
LOG_FILE="$STATE_DIR/daemon.log"
KILL_FILE="$HERE/KILL"

# 1. CF Workers AI keys live in the `dsh web` process env (never print them).
DSH_PID="$(pgrep -f 'dsh web' | head -1 || true)"
CF_ENV=""
if [ -n "$DSH_PID" ] && [ -r "/proc/$DSH_PID/environ" ]; then
  # Linux: read the process environ directly.
  CF_ENV="$(tr '\0' '\n' < "/proc/$DSH_PID/environ" | grep -E '^CLOUDFLARE_(ACCOUNT_ID|API_KEY|AI_TOKEN)=' | sed 's/^/export /' || true)"
elif [ -n "$DSH_PID" ]; then
  # macOS (ps -Eww shows the env of user-owned processes).
  CF_ENV="$(ps -Eww -o command= -p "$DSH_PID" | tr ' ' '\n' | grep -E '^CLOUDFLARE_(ACCOUNT_ID|API_KEY|AI_TOKEN)=' | sed 's/^/export /' || true)"
fi
if [ -z "$CF_ENV" ]; then
  if [ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ] && { [ -n "${CLOUDFLARE_API_KEY:-}" ] || [ -n "${CLOUDFLARE_AI_TOKEN:-}" ]; }; then
    CF_ENV="true"  # already exported in this shell — nothing to import
  else
    echo "ERROR: could not read CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_KEY (or AI_TOKEN) from the dsh web process env." >&2
    echo "Start the dsh web process first (it holds the CF keys), then re-run start.sh." >&2
    echo "Linux pattern: eval \"\$(tr '\0' '\n' < /proc/\$(pgrep -f 'dsh web' | head -1)/environ | grep -E '^CLOUDFLARE_(ACCOUNT_ID|API_KEY)=' | sed 's/^/export /')\"." >&2
    echo "macOS pattern: export \"\$(ps -Eww -o command= -p \$(pgrep -f 'dsh web' | head -1) | tr ' ' '\n' | grep -E '^CLOUDFLARE_(ACCOUNT_ID|AI_TOKEN)=' | sed 's/^/export /')\"." >&2
    exit 1
  fi
fi
eval "$CF_ENV"

mkdir -p "$STATE_DIR"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "ERROR: daemon already running (PID $(cat "$PID_FILE")). Stop it first: scripts/stop.sh" >&2
  exit 1
fi

if [ -f "$KILL_FILE" ]; then
  echo "ERROR: KILL file present at $KILL_FILE — the daemon refuses to run." >&2
  echo "Clear it with: rm -f "$KILL_FILE"" >&2
  exit 1
fi

# 2. Launch. GRID_LLM_CHAIN (if exported) rides through the environment.
nohup python3 daemon.py "$@" >> "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"
echo "daemon started (PID $PID)"
echo "  log:      $LOG_FILE"
echo "  mode:     dry-run planning (default); pass --live-paper for paper deploys"
echo "  stop:     scripts/stop.sh"
