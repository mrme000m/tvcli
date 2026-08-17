#!/usr/bin/env bash
# pine2tool.sh — turn ANY TradingView Pine script into a reusable analysis tool.
#
# Steps (all artifacts land under --out):
#   1. fetch the source (pull) if a Pine ID is given; otherwise use the local .pine
#   2. introspect canonical Pine inputs from metaInfo (schema)
#   3. run the script with optional custom inputs and capture RAW output
#   4. analyze it with the universal analyzer -> agent-ready JSON
#   5. emit a reusable skill stub (.SKILL.md + .skill.yaml + .inputs.json)
#
# Usage:
#   pine2tool.sh <pineId|local.pine> [--input k=v[,k2=v2]] [--symbol X] [--tf T] [--out DIR]
#
# Env: TVCLI (binary path, default ./tvcli), TV_SYMBOL, TV_TF

set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
WS="$(cd "$SELF_DIR/../../../.." && pwd)"       # repo root: /Volumes/ExMac/code/tradingview/go
TVCLI="${TVCLI:-$WS/tvcli}"
[ -x "$TVCLI" ] || { echo "❌ tvcli not built. run:  go build -o tvcli ./cmd/tvcli  (in $WS)"; exit 1; }

# ---- parse args ----------------------------------------------------------
TARGET=""
SYMBOL="${TV_SYMBOL:-BINANCE:BTCUSDT}"
TF="${TV_TF:-1H}"
OUT_DIR="skill_work/pine2tool_out"
INPUTS=()
POS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --symbol) SYMBOL="$2"; shift 2 ;;
    --tf|--timeframe) TF="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    --input) INPUTS+=("$2"); shift 2 ;;
    --input=*) INPUTS+=("${1#--input=}"); shift ;;
    *) POS+=("$1"); shift ;;
  esac
done
TARGET="${POS[0]:-}"
[ -n "$TARGET" ] || { echo "❌ usage: pine2tool.sh <pineId|local.pine> [--input k=v] [--symbol X] [--tf T] [--out DIR]"; exit 1; }

mkdir -p "$OUT_DIR"

# ---- 1. resolve source ---------------------------------------------------
IS_LOCAL=0
PINE_ID=""
SRC_FILE=""
case "$TARGET" in
  *.pine) IS_LOCAL=1; SRC_FILE="$TARGET"; PINE_ID="LOCAL:$TARGET" ;;
  *)      PINE_ID="$TARGET" ;;
esac

if [ "$IS_LOCAL" -eq 0 ]; then
  echo "▶ Downloading source for $PINE_ID"
  PULL_OUT="$("$TVCLI" pull "$PINE_ID" 2>&1)"
  echo "$PULL_OUT" | sed 's/^/    /'
  # pull saves to .tv-scripts/<n>--<slug>.pine ; find the newest tracked copy
  NEWEST="$(ls -1t "$WS"/.tv-scripts/*.pine 2>/dev/null | head -n1)"
  if [ -n "$NEWEST" ] && [ -f "$NEWEST" ]; then
    SRC_FILE="$NEWEST"
    # slug from filename for stable artifact names
    SLUG="$(basename "$NEWEST" .pine)"
    SLUG="${SLUG#*--}"
    [ -n "$SLUG" ] || SLUG="$(echo "$PINE_ID" | tr -cd '[:alnum:]' | cut -c1-20)"
  fi
fi

if [ -n "$SRC_FILE" ] && [ -f "$SRC_FILE" ]; then
  BASE="$(basename "$SRC_FILE" .pine)"
  SLUG="${SLUG:-$BASE}"
  cp "$SRC_FILE" "$OUT_DIR/$SLUG.source.pine" 2>/dev/null
fi
SLUG="${SLUG:-$(echo "$PINE_ID" | tr -cd '[:alnum:]' | cut -c1-24)}"
echo "▶ slug: $SLUG"

# ---- 2. introspect inputs -----------------------------------------------
# `tv inputs` needs a Pine ID (or skill name), not a local path. For a local
# .pine we lack a saved pineId; emit a pointer instead so users can introspect
# once the source is run (the eval flow saves+deletes a temp script).
INPUT_JSON="$OUT_DIR/$SLUG.inputs.json"
if [ "$IS_LOCAL" -eq 1 ]; then
  cat > "$INPUT_JSON" <<'IZ'
{"note": "local .pine source: introspect inputs by running the script via `tvcli eval <file> --raw` (the temp script carries metaInfo), or register it and use `tvcli inputs <skillName>`.", "source": "'"$SRC_FILE"'" }
IZ
  echo "▶ inputs (local placeholder): $INPUT_JSON"
else
  "$TVCLI" inputs "$PINE_ID" --raw --json > "$INPUT_JSON" 2>/dev/null \
    && echo "▶ saved inputs: $INPUT_JSON" \
    || echo "⚠ could not introspect inputs for $PINE_ID"
fi

# ---- build input flags ---------------------------------------------------
# The CLI reassembles "--input k=v" and comma lists into one map
# (internal/cmd/inputs_util.go), so pass each user-specified list as ONE flag.
# Multiple "--input" flags would collapse in the flag map (only the last wins).
INPUT_FLAGS=()
for kv in "${INPUTS[@]}"; do
  [ -n "$kv" ] && INPUT_FLAGS+=("--input" "$kv")
done

# ---- 3+4. run raw + analyze ---------------------------------------------
RAW_JSON="$OUT_DIR/$SLUG.raw.json"
ANALYSIS_JSON="$OUT_DIR/$SLUG.json"

if [ "$IS_LOCAL" -eq 1 ]; then
  echo "▶ running local source with inputs: ${INPUTS[*]:-<defaults>}"
  # Pre-compile check to catch syntax errors early (Pine v5 needs ta. prefix)
  "$TVCLI" eval "$SRC_FILE" --compile-only 2>&1 | head -5
  "$TVCLI" eval "$SRC_FILE" --raw --symbol "$SYMBOL" --tf "$TF" \
    "${INPUT_FLAGS[@]}" > /tmp/p2t_raw_$$.out 2>/tmp/p2t_raw_$$.err
  # strip the leading informational line(s) before the JSON document
  python3 -c "import sys,re;d=sys.stdin.read();i=d.find('{');print(d[i:] if i>=0 else '{}')" \
    < /tmp/p2t_raw_$$.out > "$RAW_JSON" || echo '{}' > "$RAW_JSON"
  echo "▶ raw: $RAW_JSON"
  "$TVCLI" eval "$SRC_FILE" --signals --agent --json --symbol "$SYMBOL" --tf "$TF" \
    "${INPUT_FLAGS[@]}" 2>/dev/null > /tmp/p2t_agent_$$.out \
    && python3 -c "import sys;d=sys.stdin.read();i=d.find('{');print(d[i:] if i>=0 else '{}')" \
       < /tmp/p2t_agent_$$.out > "$ANALYSIS_JSON" \
    && echo "▶ analysis: $ANALYSIS_JSON" \
    || echo "⚠ agent analysis failed; see JSON"
else
  echo "▶ analyzing $PINE_ID with inputs: ${INPUTS[*]:-<defaults>}"
  "$TVCLI" analyze "$PINE_ID" --json --symbol "$SYMBOL" --tf "$TF" \
    "${INPUT_FLAGS[@]}" --out "$ANALYSIS_JSON" 2>/dev/null \
    && echo "▶ analysis: $ANALYSIS_JSON"
fi

# ---- 5. emit reusable skill stub ----------------------------------------
SKILL_MD="$OUT_DIR/$SLUG.SKILL.md"
SKILL_YAML="$OUT_DIR/$SLUG.skill.yaml"
cat > "$SKILL_MD" <<EOF
# $SLUG — Pine script analysis tool

$([ "$IS_LOCAL" -eq 1 ] && echo "Source: local \`$SRC_FILE\`" || echo "Pine ID: \`$PINE_ID\`")
Symbol: \`$SYMBOL\` · Timeframe: \`$TF\`

Reusable analysis artifacts in this directory. To make this a first-class
\`tvcli\` skill, register a \`skill.Skill\` in \`internal/skill/registry.go\`
using \`$SLUG.skill.yaml\` and add a doc under \`docs/skills/\`.
EOF
SKILL_NAME="${SLUG//-/_}"
printf 'name: %s\npineId: %s\nsource: %s\nsymbol: %s\ntimeframe: %s\ninputs:\n' \
  "$SKILL_NAME" "$PINE_ID" "$([ "$IS_LOCAL" -eq 1 ] && echo "$SRC_FILE" || echo "PUB")" \
  "$SYMBOL" "$TF" > "$SKILL_YAML"
# append canonical inputs as a skeleton
if [ -f "$INPUT_JSON" ]; then
  python3 - "$SKILL_YAML" "$INPUT_JSON" <<'PY'
import json,sys
y,ij=sys.argv[1],sys.argv[2]
try:
    rows=json.load(open(ij))
    for r in rows:
        if r.get("isFake") or r.get("isHidden"): continue
        opts=""
        if r.get("options"): opts="  options: "+", ".join(str(o) for o in r["options"])
        print(f'  - {r["id"]}:\n      name: "{r.get("name","")}"\n      type: "{r["type"]}"\n      default: {json.dumps(r.get("defval"))}{opts}', file=open(y,"a"))
except Exception as e:
    print("# input introspect failed: %s"%e, file=open(y,"a"))
PY
fi
echo "▶ skill stub: $SKILL_MD"
echo "▶ skill stub: $SKILL_YAML"

echo ""
echo "============================================================"
echo "✅ Done. Analysis tool artifacts in: $OUT_DIR"
echo "   agent-ready JSON : $ANALYSIS_JSON"
echo "   raw             : $RAW_JSON"
echo "   inputs          : $INPUT_JSON"
echo "   reusable skill  : $SKILL_MD  +  $SKILL_YAML"
echo "============================================================"
