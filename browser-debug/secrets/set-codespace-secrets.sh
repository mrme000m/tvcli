#!/usr/bin/env bash
# set-codespace-secrets.sh — publish the Bitwarden credentials as GitHub
# Codespaces REPOSITORY secrets, so every codespace of this repo receives
# BW_CLIENTID / BW_CLIENTSECRET / BW_PASSWORD as environment variables and
# post-create.sh can run browser-debug/secrets/bw-provision.sh unattended.
#
# One-time setup per machine:
#   1. gh authenticated with admin:repo scope for the repo (gh auth login)
#   2. python3 with pynacl  (pip3 install --user --break-system-packages pynacl)
#   3. a Bitwarden API key (web vault → Settings → Security → API Key)
#
# Usage (values never echoed; pass via env):
#   BW_CLIENTID=xxx BW_CLIENTSECRET=yyy BW_PASSWORD=zzz \
#     bash browser-debug/secrets/set-codespace-secrets.sh [owner/repo]   # API key
#   BW_EMAIL=you@example.com BW_PASSWORD=zzz \
#     bash browser-debug/secrets/set-codespace-secrets.sh [owner/repo]  # email+password
#
# Test mode (--test): round-trips a dummy secret through the real API
# (PUT + DELETE) to verify connectivity/scopes without touching real values.
set -euo pipefail

REPO="${REPO:-mrme000m/tvcli}"
MODE="set"
[ "${1:-}" = "--test" ] && MODE="test"
[ -n "${1:-}" ] && [ "$1" != "--test" ] && REPO="$1"

log() { printf '  [cs-secrets] %s\n' "$*" >&2; }

command -v gh >/dev/null 2>&1 || { echo "gh CLI required" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "jq required" >&2; exit 1; }
python3 -c 'import nacl' 2>/dev/null || {
  echo "pynacl required: pip3 install --user --break-system-packages pynacl" >&2; exit 1; }

# encrypt <plaintext> <b64-public-key> → b64 sealed box (libsodium crypto_box_seal)
encrypt() {
  ENC_KEY="$2" SECRET="$1" python3 - <<'PY'
import base64, os
from nacl import encoding, public
pk = public.PublicKey(os.environ["ENC_KEY"].encode(), encoding.Base64Encoder())
sealed = public.SealedBox(pk).encrypt(os.environ["SECRET"].encode())
print(base64.b64encode(sealed).decode())
PY
}

# put_secret <name> <value> — fetch repo public key, seal, PUT
put_secret() {
  local name="$1" value="$2" key_json key_id key encrypted status
  key_json="$(gh api "repos/$REPO/codespaces/secrets/public-key")"
  key_id="$(jq -r .key_id <<<"$key_json")"
  key="$(jq -r .key <<<"$key_json")"
  encrypted="$(encrypt "$value" "$key")"
  status="$(gh api -X PUT "repos/$REPO/codespaces/secrets/$name" \
    -f key_id="$key_id" -f encrypted_value="$encrypted" --include 2>/dev/null \
    | head -1 | tr -d '\r' | awk '{print $2}')"
  log "$name → $REPO codespace secret (HTTP ${status:-?})"
  [ "${status:-}" = "201" ] || [ "${status:-}" = "204" ]
}

if [ "$MODE" = "test" ]; then
  log "test mode: round-tripping a dummy secret on $REPO"
  put_secret BW_PROBE_TEST "tvcli-probe-$(date +%s)" || { echo "PUT failed" >&2; exit 1; }
  gh api -X DELETE "repos/$REPO/codespaces/secrets/BW_PROBE_TEST" --include 2>/dev/null | head -1 | tr -d '\r' | grep -q " 204" \
    && log "probe deleted (HTTP 204) — full path verified" \
    || { echo "DELETE failed (probe secret BW_PROBE_TEST may linger — remove it in repo settings)" >&2; exit 1; }
  exit 0
fi

if [ -n "${BW_CLIENTID:-}" ] && [ -n "${BW_CLIENTSECRET:-}" ] && [ -n "${BW_PASSWORD:-}" ]; then
  put_secret BW_CLIENTID "$BW_CLIENTID"
  put_secret BW_CLIENTSECRET "$BW_CLIENTSECRET"
  put_secret BW_PASSWORD "$BW_PASSWORD"
elif [ -n "${BW_EMAIL:-}" ] && [ -n "${BW_PASSWORD:-}" ]; then
  put_secret BW_EMAIL "$BW_EMAIL"
  put_secret BW_PASSWORD "$BW_PASSWORD"
  log "email+password mode (bw-provision logs in with 'bw login <email> --passwordenv')"
else
  echo "set BW_CLIENTID+BW_CLIENTSECRET+BW_PASSWORD (API key) or BW_EMAIL+BW_PASSWORD" >&2
  exit 1
fi
log "done — rebuild the codespace; post-create will provision from the vault"
