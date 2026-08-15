#!/bin/bash
# Open Knowledge observation script for pi
# Run this after a pi session to capture insights

set -e

WIKI="${1:-Wiki}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")"

cd "$PROJECT_DIR"

echo "Observing session for Open Knowledge insights..."
openknowledge insights observe --runtime pi 2>/dev/null || true

echo "Done."
