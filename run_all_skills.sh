#!/usr/bin/env bash
# Run all skill commands with core/default inputs and capture results.
set -u
cd "$(dirname "$0")"
OUT=skill-runs
mkdir -p "$OUT"
SYM="OANDA:XAUUSD"
TF="5m"
BARS=300
SKILLS=$(./tvcli skills --json 2>/dev/null | python3 -c "import sys,json;print('\n'.join(s['name'] for s in json.load(sys.stdin)))")
echo "$SKILLS" | while read -r name; do
  [ -z "$name" ] && continue
  start=$(date +%s)
  timeout 150 ./tvcli "$name" --json --symbol "$SYM" --tf "$TF" --bars "$BARS" \
    >"$OUT/$name.json" 2>"$OUT/$name.err"
  rc=$?
  end=$(date +%s)
  dur=$((end-start))
  echo "$name rc=$rc dur=${dur}s"
done
echo "DONE"
