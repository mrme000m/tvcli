#!/usr/bin/env bash
# grid-healthcheck — Docker HEALTHCHECK for the grid-autonomy container.
#
# Primary signal: the daemon's ctl HTTP surface on 127.0.0.1:8799
# (GET /health). Secondary sanity: a python3 process must be alive.
#
# When GRID_COMPONENTS does not include "daemon" there is no ctl to probe —
# fall back to "some supervised component is alive"; if literally nothing is
# enabled the container is a deliberate idle shell and is healthy by design.
set -uo pipefail

COMPONENTS="${GRID_COMPONENTS:-xvfb,pb,serve,browser,daemon,console}"

has() { case ",$COMPONENTS," in *",$1,"*) return 0;; *) return 1;; esac; }

if has daemon; then
  if ! curl -fsS -m 5 http://127.0.0.1:8799/health >/dev/null 2>&1; then
    echo "healthcheck: no answer from daemon ctl :8799/health" >&2
    exit 1
  fi
  if ! pgrep -x python3 >/dev/null 2>&1; then
    echo "healthcheck: daemon /health answers but no python3 process found" >&2
    exit 1
  fi
  exit 0
fi

# No daemon: expect at least one of the other long-lived components.
if has xvfb || has pb || has serve || has console; then
  alive=0
  has console   && pgrep -f "console/server.py"      >/dev/null 2>&1 && alive=1
  has serve     && pgrep -x tvcli                     >/dev/null 2>&1 && alive=1
  has xvfb      && pgrep -x Xvfb                      >/dev/null 2>&1 && alive=1
  has pb        && pgrep -x pocketbase                >/dev/null 2>&1 && alive=1
  if [ "$alive" = "0" ]; then
    echo "healthcheck: no enabled component process is alive" >&2
    exit 1
  fi
fi
exit 0
