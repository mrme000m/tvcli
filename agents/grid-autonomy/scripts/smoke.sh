#!/usr/bin/env bash
# One-shot dry-run E2E smoke test — no WunderTrading mutations.
# Runs screen → deliberate → guard → deploy-plan (dry-run) once, over the
# top candidates, without tvcli confluence. Requires public exchange APIs
# (Binance + Hyperliquid market data); the LLM chain falls back to the rule
# map when no provider keys are set, so it also works keyless.
#
# Usage: scripts/smoke.sh [--top N] [--live-paper]
#
# Note: the daemon refuses to start while a live daemon holds
# state/daemon.pid (single-writer guard) — stop the daemon first, or set
# GRID_NO_PIDGUARD=1 to run the smoke against shared state deliberately.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

exec python3 daemon.py --once --no-confluence --top 5 "$@"
