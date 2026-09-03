#!/usr/bin/env bash
# migrate-local.sh — one-shot migration: build every manifest item's payload
# from LOCAL sources and push it into the Bitwarden vault via
# `bw-provision.sh --export`. Run this ONCE from a machine that already has
# the working secrets (registry accounts.json, shell rc keys); afterwards the
# devcontainer provisions everything from the vault alone.
#
# Sources (nothing is ever printed):
#   tvcli-primary-env   accounts.json default entry (sessionId/signature/
#                       deviceToken/userName/tier)
#   tvcli-accounts-pool accounts.json verbatim
#   browser-debug-env   primary env + MISTRAL_API_KEY (env, ~/.zshrc,
#                       ~/.profile, ~/.bashrc)
#   opencode-cloudflare CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_KEY (env,
#                       ~/.profile, ~/.bashrc)
#   tv-proxy            TV_PROXY (env only — skipped with a warning if unset)
#
# Requires an unlocked vault (BW_SESSION) or BW_CLIENTID/BW_CLIENTSECRET/
# BW_PASSWORD. Payloads live only in a wiped 600-mode temp dir.
#
# Usage: migrate-local.sh [--dry-run]
#   --dry-run  collect + report sources WITHOUT touching the vault.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="$(cd "$SCRIPT_DIR/../.." && pwd)"
REG="$WS/accounts.json"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

log()  { printf '  [migrate] %s\n' "$*" >&2; }
warn() { printf '  [migrate][warn] %s\n' "$*" >&2; }

[ -f "$REG" ] || { echo "  [migrate][ERROR] registry not found: $REG" >&2; exit 1; }
command -v python3 >/dev/null || { echo "  [migrate][ERROR] python3 required" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# value of VAR from env, else from shell rc files (handles `export VAR=` and
# `VAR=`, with or without double quotes). Prints nothing when not found.
key_from_env_or_rc() { # $1=VAR $2...=rc files
  local var="$1" line f
  [ -n "${!var:-}" ] && { printf '%s' "${!var}"; return 0; }
  shift
  for f in "$@"; do
    [ -f "$f" ] || continue
    line="$(grep -E "^(export +)?${var}=" "$f" 2>/dev/null | tail -1 || true)"
    [ -n "$line" ] || continue
    line="${line#*${var}=}"
    line="${line%\"}"; line="${line#\"}"
    printf '%s' "$line"
    return 0
  done
  return 1
}

# --- 1+2: primary env + accounts pool from the registry default entry -------
python3 - "$REG" "$TMP/primary.env" <<'PY'
import json, sys
reg, out = sys.argv[1], sys.argv[2]
d = json.load(open(reg))
accs = d.get("accounts") or {}
name = d.get("default")
a = accs.get(name) or next(iter(accs.values()))
with open(out, "w") as f:
    f.write(f"SESSION={a['sessionId']}\n")
    f.write(f"SIGNATURE={a['signature']}\n")
    f.write(f"DEVICE_T={a.get('deviceToken','')}\n")
    f.write(f"TV_USER={a.get('userName', name)}\n")
    f.write(f"TV_TIER={a.get('tier','free')}\n")
PY
grep -q '^SESSION=.\+' "$TMP/primary.env" || { echo "  [migrate][ERROR] could not build primary env from registry" >&2; exit 1; }
cp "$REG" "$TMP/accounts.json"
log "built tvcli-primary-env + tvcli-accounts-pool from registry default"

# --- 3: browser-debug-env = primary + MISTRAL_API_KEY ------------------------
cp "$TMP/primary.env" "$TMP/browser-debug.env"
if MK="$(key_from_env_or_rc MISTRAL_API_KEY "$HOME/.zshrc" "$HOME/.profile" "$HOME/.bashrc")" && [ -n "$MK" ]; then
  printf 'MISTRAL_API_KEY=%s\n' "$MK" >> "$TMP/browser-debug.env"
  log "built browser-debug-env (primary + MISTRAL_API_KEY)"
else
  warn "MISTRAL_API_KEY not found — browser-debug-env will carry TV cookies only"
fi

# --- collect (item, payload) pairs ------------------------------------------
PAIRS=(
  "tvcli-primary-env $TMP/primary.env"
  "tvcli-accounts-pool $TMP/accounts.json"
  "browser-debug-env $TMP/browser-debug.env"
)
if AID="$(key_from_env_or_rc CLOUDFLARE_ACCOUNT_ID "$HOME/.profile" "$HOME/.bashrc")" && \
   CKEY="$(key_from_env_or_rc CLOUDFLARE_API_KEY "$HOME/.profile" "$HOME/.bashrc")" && \
   [ -n "$AID" ] && [ -n "$CKEY" ]; then
  printf 'CLOUDFLARE_ACCOUNT_ID=%s\nCLOUDFLARE_API_KEY=%s\n' "$AID" "$CKEY" > "$TMP/opencode.env"
  PAIRS+=("opencode-cloudflare $TMP/opencode.env")
  log "built opencode-cloudflare"
else
  warn "CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_KEY not found — skipping opencode-cloudflare"
fi
if TP="$(key_from_env_or_rc TV_PROXY)" && [ -n "$TP" ]; then
  printf 'TV_PROXY=%s\n' "$TP" > "$TMP/tv-proxy.env"
  PAIRS+=("tv-proxy $TMP/tv-proxy.env")
  log "built tv-proxy"
else
  warn "TV_PROXY not set in env — skipping tv-proxy"
fi

# --- push (or report) --------------------------------------------------------
if [ "$DRY_RUN" -eq 1 ]; then
  log "dry-run: ${#PAIRS[@]} item(s) ready to push:"
  for pair in "${PAIRS[@]}"; do
    set -- $pair
    log "  $1 ← $(basename "$2") ($(wc -c <"$2") bytes)"
  done
  log "dry-run OK (vault not touched)"
  exit 0
fi

for pair in "${PAIRS[@]}"; do
  set -- $pair
  bash "$SCRIPT_DIR/bw-provision.sh" --export "$1" "$2"
done
log "migration complete: ${#PAIRS[@]} item(s) in the vault — devcontainers can now self-provision"
