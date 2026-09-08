#!/usr/bin/env bash
# ga-healthcheck — Docker HEALTHCHECK for the grid-ga container.
#
# Single signal: dsh web's HTTP surface on 127.0.0.1 (any response means the
# GA agent's web UI is up; connection refused means down — and since dsh web
# runs as PID 1, down means the container is going away anyway).
set -uo pipefail

curl -fs -m 5 "http://127.0.0.1:${DSH_WEB_PORT:-3081}/" || exit 1
