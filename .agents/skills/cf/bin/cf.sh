#!/usr/bin/env bash
# thin wrapper: python3 scripts/cf.py with the skill dir resolved
set -euo pipefail
D="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$D/scripts/cf.py" "$@"
