#!/usr/bin/env bash
# install_launchd.sh — supervise the grid-autonomy daemon + tvcli server with
# launchd so the paper-trading loop survives crashes and reboots.
#
# Installs (per-user LaunchAgents):
#   com.tvcli.grid-autonomy          — scripts/run_launchd.py (foreground, --live-paper)
#   com.tvcli.grid-autonomy-console  — scripts/run_console.py (mission console :8798)
#   com.tvcli.serve                  — tvcli serve (foreground, :8765 confluence)

# All per-process stdio is redirected IN-PROCESS into state/logs/ (launchd
# itself cannot open files on this removable volume — TCC); the plists send
# launchd's own early stdio to /dev/null.
#
# Semantics: KeepAlive={SuccessfulExit: false} restarts only on a real crash;
# scripts/stop.sh (SIGTERM) exits 0 and stays stopped until the next
# boot/load; a leftover KILL file also blocks startup on purpose.
#
# NOTE (macOS TCC): the repo lives on a removable volume. launchd-spawned
# /bin/bash cannot read it — the grid-autonomy agent therefore runs under
# the Homebrew python3 (/opt/homebrew/bin/python3), which holds the
# "Removable Volumes" grant (same as com.tvcli.watchtower). Keep that
# interpreter as ProgramArguments[0].
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHD_DIR="$HERE/launchd"
DEST="$HOME/Library/LaunchAgents"
UID_="$(id -u)"

mkdir -p "$DEST"
for plist in "$LAUNCHD_DIR"/*.plist; do
  name="$(basename "$plist" .plist)"
  cp "$plist" "$DEST/$name.plist"
  launchctl bootout "gui/$UID_/$name" 2>/dev/null || true
  # wait for the bootout to fully land — bootstrapping while the old job is
  # still unloading fails with EIO 5 (observed 2026-09-05). Services take a
  # few seconds to save state and exit.
  waited=0
  while launchctl print "gui/$UID_/$name" >/dev/null 2>&1 \
      && [ "$waited" -lt 15 ]; do
    sleep 1
    waited=$((waited + 1))
  done
  launchctl bootstrap "gui/$UID_" "$DEST/$name.plist"
  echo "installed + loaded: $name"
done

echo "verify: launchctl list | grep tvcli   (grid-autonomy + serve should have PIDs)"
