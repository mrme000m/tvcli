#!/usr/bin/env bash
# tv-investigator.sh — programmatically invoke the DSH TV Network Investigator
# agent headlessly with a task prompt.
#
# Usage:
#   ./tv-investigator.sh "<task prompt>"
#   ./tv-investigator.sh "<task prompt>" --cwd /Volumes/ExMac/code/tradingview/go
#   ./tv-investigator.sh "<task prompt>" --model deepseek-v4-pro-0813
#
# The agent runs in the specified working directory (default: the go repo)
# with the DSH tv-investigator profile, which composes the agent-presets
# roster (tv-investigator preset) over the headless one-shot runner.
#
# The agent has bash, fs, web, and skill tools. It auto-discovers skills from
# the .agents/skills/ directory in the working directory:
#   - go repo: tvcli, pine2tool, tv-scout, tv-usecases
#   - minimal-mjs: tv-network
#
# Exit codes: 0 = task completed, 1 = error or incomplete.

set -euo pipefail

TASK=""
CWD="${TV_INVESTIGATOR_CWD:-/Volumes/ExMac/code/tradingview/go}"
MODEL="${TV_INVESTIGATOR_MODEL:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cwd) CWD="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --help|-h)
      echo "Usage: $0 <task> [--cwd DIR] [--model MODEL]"
      echo "  Invokes the DSH TV Network Investigator agent headlessly."
      echo "  --cwd DIR    Working directory (default: $CWD)"
      echo "  --model M    Override model (default: profile default)"
      exit 0
      ;;
    *) TASK="$TASK $1"; shift ;;
  esac
done

TASK=$(echo "$TASK" | sed 's/^ //')

if [[ -z "$TASK" ]]; then
  echo "Error: a task prompt is required" >&2
  exit 1
fi

# Compose the full prompt: load the tv-usecases skill, then execute the task.
FULL_PROMPT="Load the tv-usecases skill. Then: $TASK"

# Run with the tv-investigator profile.
MODEL_FLAG=""
if [[ -n "$MODEL" ]]; then
  MODEL_FLAG="--model $MODEL"
fi

cd "$CWD"
dsh --profile tv-investigator "$FULL_PROMPT"
