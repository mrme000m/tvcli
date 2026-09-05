#!/usr/bin/env python3
"""run_console.py — launchd entrypoint for the grid-autonomy mission console.

Runs console/server.py IN THE FOREGROUND (under this interpreter) so launchd
can supervise it: KeepAlive={SuccessfulExit: false} restarts only on a real
crash. Mirrors scripts/run_launchd.py (same TCC + stdio-redirect reasoning —
launchd itself cannot open files on this removable volume, so the log is
opened in-process, where the Homebrew python3 binary holds the
"Removable Volumes" grant).

Sigterm → exit 0 so `dev stop` / launchctl bootout keeps it stopped instead
of restart-looping.
"""
import os
import signal
import sys

HERE = os.path.dirname(os.path.abspath(os.path.dirname(__file__)))  # grid-autonomy/
STATE_DIR = os.path.join(HERE, "state")
CONSOLE = os.path.join(HERE, "console", "server.py")


def redirect_stdio_to_state_log():
    """stdout/stderr → state/logs/console.log (append)."""
    try:
        log_dir = os.path.join(STATE_DIR, "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "console.log")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        if fd > 2:
            os.close(fd)
    except OSError:
        pass  # never block startup on logging


def main():
    redirect_stdio_to_state_log()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    sys.argv = [CONSOLE]
    import runpy
    runpy.run_path(CONSOLE, run_name="__main__")


if __name__ == "__main__":
    main()
