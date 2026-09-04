#!/usr/bin/env python3
"""run_launchd.py — launchd entrypoint for the grid-autonomy daemon.

Runs the daemon IN THE FOREGROUND (under this interpreter) so launchd can
supervise it: KeepAlive={SuccessfulExit: false} restarts only on a real
crash; stop.sh/SIGTERM exits 0 and stays stopped. The KILL file is still
honored by the daemon itself (startup + every loop tick).

Why Python and not a bash wrapper: the repo lives on a removable volume
(/Volumes/ExMac). launchd-spawned bash cannot read it (macOS TCC
"Removable Volumes" denies /bin/bash), while the Homebrew python3 binary
this script runs under holds the grant (same as com.tvcli.watchtower).
The launcher therefore must stay stdlib-only and run under the granted
interpreter.

CF Workers AI keys are imported from the running `dsh web` process env
(Linux /proc, macOS `ps -Eww`) or inherited when already exported.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(os.path.dirname(__file__)))  # grid-autonomy/
STATE_DIR = os.path.join(HERE, "state")
PID_FILE = os.path.join(STATE_DIR, "daemon.pid")
KILL_FILE = os.path.join(HERE, "KILL")
CF_RE = re.compile(r"^CLOUDFLARE_(ACCOUNT_ID|API_KEY|AI_TOKEN)=(\S+)$")


def _dsh_pid():
    try:
        out = subprocess.run(["pgrep", "-f", "dsh web"],
                             capture_output=True, text=True, timeout=5)
        pids = out.stdout.split()
        return pids[0] if pids else None
    except Exception:
        return None


def import_cf_env():
    """Import CLOUDFLARE_* from the dsh web process env (never print them)."""
    if os.environ.get("CLOUDFLARE_ACCOUNT_ID") and (
            os.environ.get("CLOUDFLARE_API_KEY")
            or os.environ.get("CLOUDFLARE_AI_TOKEN")):
        return  # already exported
    pid = _dsh_pid()
    if not pid:
        return
    raw = ""
    environ = f"/proc/{pid}/environ"
    if os.path.exists(environ):
        try:
            with open(environ, "rb") as f:
                raw = f.read().decode("utf-8", "replace").replace("\0", "\n")
        except OSError:
            pass
    if not raw:
        try:
            raw = subprocess.run(
                ["ps", "-Eww", "-o", "command=", "-p", pid],
                capture_output=True, text=True, timeout=5).stdout
        except Exception:
            return
    for tok in raw.split():
        m = CF_RE.match(tok)
        if m:
            os.environ[m.group(1)] = m.group(2)


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    # a start.sh-launched daemon already alive: let it be (clean exit →
    # launchd does not restart-loop)
    try:
        with open(PID_FILE) as f:
            old = int(f.read().strip())
        os.kill(old, 0)
        print(f"daemon already running (PID {old}) — not starting another",
              flush=True)
        return
    except (OSError, ValueError):
        pass
    if os.path.exists(KILL_FILE):
        print(f"KILL file present — not starting (rm -f {KILL_FILE} to re-enable)",
              flush=True)
        return
    import_cf_env()
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    os.chdir(HERE)
    sys.argv = [os.path.join(HERE, "daemon.py"), "--live-paper"]
    import runpy
    runpy.run_path(sys.argv[0], run_name="__main__")


if __name__ == "__main__":
    main()
