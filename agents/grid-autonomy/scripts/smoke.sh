#!/usr/bin/env bash
# One-shot dry-run E2E smoke test — no WunderTrading mutations.
# Runs screen → deliberate → guard → deploy-plan (dry-run) once, over the
# top candidates, without tvcli confluence. Requires public exchange APIs
# (Binance + Hyperliquid market data); the LLM chain falls back to the rule
# map when no provider keys are set, so it also works keyless.
#
# Usage: scripts/smoke.sh [--top N] [--live-paper]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

exec python3 daemon.py --once --no-confluence --top 5 "$@"
