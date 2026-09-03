#!/usr/bin/env bash
# bw-provision.sh — provision tvcli runtime secrets from the Bitwarden vault.
#
# The tvcli devcontainer is a complete TradingView analysis/debugging system.
# Every secret it needs (TradingView cookies, the multi-account WS pool,
# OpenCode/Cloudflare credentials, SOCKS proxy creds, Mistral vision key) lives
# in the Bitwarden vault; this script materializes them as runtime files that
# are ALWAYS gitignored. Values are never echoed, logged, or inlined into shell
# profiles — profiles only gain guarded `source` lines.
#
# Auth (either works):
#   1. BW_SESSION already exported (a wrapper already unlocked the vault).
#   2. BW_CLIENTID + BW_CLIENTSECRET (API key) + BW_PASSWORD (master password)
#      — these are injected as devcontainer/codespace secrets; the script runs
#      `bw config server`, `bw login --apikey`, `bw unlock` itself.
#
# Usage:
#   bw-provision.sh                 # fetch all manifest items → runtime files
#   bw-provision.sh --dry-run       # validate manifest + creds, no writes
#   bw-provision.sh --export ITEM FILE
#                                   # push FILE's content into vault item ITEM
#                                   # (notes of a Secure Note; create-or-update)
#   bw-provision.sh --relock        # provision, then `bw lock` (CI hygiene)
#
# Manifest: manifest.json (same dir) — declarative item→target mapping.
# Exit codes: 0 ok · 1 error · 2 not configured (missing bw credentials).
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="$(cd "$SCRIPT_DIR/../.." && pwd)" # repo root (go/)
MANIFEST="$SCRIPT_DIR/manifest.json"
RUNTIME_DIR="$SCRIPT_DIR/runtime"
MODE="provision"
EXPORT_ITEM=""
EXPORT_FILE=""
RELOCK=0

log()  { printf '  [bw] %s\n' "$*" >&2; }
warn() { printf '  [bw][warn] %s\n' "$*" >&2; }
die()  { printf '  [bw][ERROR] %s\n' "$*" >&2; exit 1; }
die_unconfigured() { printf '  [bw][ERROR] %s\n' "$*" >&2; exit 2; }

usage() {
  sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) MODE="dry-run" ;;
    --export)  MODE="export"; EXPORT_ITEM="${2:?--export needs ITEM FILE}"; EXPORT_FILE="${3:?--export needs ITEM FILE}"; shift 2 ;;
    --relock)  RELOCK=1 ;;
    -h|--help) usage ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

[ -f "$MANIFEST" ] || die "manifest not found: $MANIFEST"
command -v jq >/dev/null 2>&1 || die "jq is required (apt install jq / brew install jq)"
jq -e . "$MANIFEST" >/dev/null || die "manifest is not valid JSON: $MANIFEST"

# --- Bitwarden CLI ---------------------------------------------------------
ensure_bw() {
  command -v bw >/dev/null 2>&1 && return 0
  command -v npm >/dev/null 2>&1 || return 1
  npm install -g @bitwarden/cli >/dev/null 2>&1 || return 1
  command -v bw >/dev/null 2>&1
}

bw_() { bw --nointeraction "$@"; }

acquire_session() {
  if [ -n "${BW_SESSION:-}" ]; then
    log "using existing BW_SESSION"
    return 0
  fi
  ensure_bw || die_unconfigured "neither bw CLI nor npm (to install it) available"
  local server
  server="$(jq -r '.vault.server // empty' "$MANIFEST")"
  if [ -n "$server" ]; then
    bw_ config server "$server" >/dev/null 2>&1 || warn "bw config server $server failed (continuing)"
  fi
  [ -n "${BW_CLIENTID:-}" ] && [ -n "${BW_CLIENTSECRET:-}" ] && [ -n "${BW_PASSWORD:-}" ] ||
    die_unconfigured "no BW_SESSION and missing BW_CLIENTID/BW_CLIENTSECRET/BW_PASSWORD (set them as codespace secrets — see README.md)"
  bw_ login --apikey >/dev/null 2>&1 || true # already-logged-in is fine
  BW_SESSION="$(bw_ unlock --passwordenv BW_PASSWORD --raw)"
  [ -n "$BW_SESSION" ] || die "bw unlock returned an empty session"
  export BW_SESSION
  log "vault unlocked"
}

expand_path() { # expand a leading $HOME / ~ in a manifest path
  local p="$1"
  case "$p" in
    '$HOME'*) p="$HOME${p#'$HOME'}" ;;
    '~'*)    p="$HOME${p#\~}" ;;
  esac
  printf '%s' "$p"
}

# --- validation ------------------------------------------------------------
validate_env() { # $1=item $2=content
  printf '%s' "$2" | grep -qE '^[A-Za-z_][A-Za-z0-9_]*=' ||
    die "item '$1' has no KEY=VALUE lines (env format)"
}
validate_json() { # $1=item $2=content $3=optional jq count expression
  printf '%s' "$2" | jq -e . >/dev/null || die "item '$1' is not valid JSON"
  if [ -n "${3:-}" ]; then
    local count
    count="$(printf '%s' "$2" | jq -r "$3" 2>/dev/null || echo 0)"
    [ "$count" -gt 0 ] 2>/dev/null || die "item '$1': jq '$3' returned ${count:-nothing} (expected > 0)"
    log "item '$1': jq '$3' → $count"
  fi
}

wire_source_into() { # $1=env file (abs) $2=shell rc (abs) — add a guarded source line only
  [ -f "$1" ] || return 0
  touch "$2" 2>/dev/null || { warn "cannot write $2 (skipping source wiring)"; return 0; }
  grep -qF "$1" "$2" 2>/dev/null || printf '\n# tvcli runtime secrets (managed by bw-provision.sh)\n[ -f "%s" ] && . "%s"\n' "$1" "$1" >> "$2"
}

# --- modes -----------------------------------------------------------------
dry_run() {
  log "manifest: $MANIFEST"
  log "mode: dry-run (no fetch, no writes)"
  ensure_bw && log "bw: $(bw --version 2>/dev/null || echo present)" || warn "bw CLI not installed and npm unavailable"
  [ -n "${BW_SESSION:-}" ] && log "session: BW_SESSION present" ||
    { [ -n "${BW_CLIENTID:-}" ] && [ -n "${BW_CLIENTSECRET:-}" ] && [ -n "${BW_PASSWORD:-}" ] && log "session: API-key creds present"; } ||
    warn "no session/API-key credentials — real runs would exit 2 (not configured)"
  while IFS= read -r entry; do
    item="$(jq -r '.item' <<<"$entry")"
    target="$(jq -r '.target' <<<"$entry")"
    format="$(jq -r '.format' <<<"$entry")"
    log "would provision: $item → $target ($format)"
  done < <(jq -c '.provision[]' "$MANIFEST")
  log "dry-run OK"
}

export_item() { # push local file content into vault item (Secure Note notes)
  [ -s "$EXPORT_FILE" ] || die "source file missing/empty: $EXPORT_FILE"
  acquire_session
  bw_ sync >/dev/null
  local existing_id
  existing_id="$(bw_ list items --search "$EXPORT_ITEM" \
    | jq -r --arg n "$EXPORT_ITEM" 'map(select(.name == $n))[0].id // empty')"
  local template payload
  template="$(bw_ get template item)"
  payload="$(jq --arg n "$EXPORT_ITEM" --rawfile notes "$EXPORT_FILE" \
    '.name = $n | .notes = $notes | .type = 2' <<<"$template")"
  if [ -n "$existing_id" ]; then
    printf '%s' "$payload" | bw_ encode | bw_ edit item "$existing_id" >/dev/null
    log "updated vault item '$EXPORT_ITEM' (id ${existing_id:0:8}…)"
  else
    printf '%s' "$payload" | bw_ encode | bw_ create item >/dev/null
    log "created vault item '$EXPORT_ITEM'"
  fi
  [ "$RELOCK" -eq 1 ] && bw_ lock >/dev/null 2>&1 || true
}

provision() {
  acquire_session
  bw_ sync >/dev/null
  mkdir -p "$RUNTIME_DIR"
  local n=0
  while IFS= read -r entry; do
    item="$(jq -r '.item' <<<"$entry")"
    target_rel="$(jq -r '.target' <<<"$entry")"
    format="$(jq -r '.format' <<<"$entry")"
    chmod_mode="$(jq -r '.chmod // "600"' <<<"$entry")"
    json_validate="$(jq -r '.jsonValidate // empty' <<<"$entry")"
    case "$target_rel" in
      /*) target="$target_rel" ;;
      *)  target="$WS/$target_rel" ;;
    esac
    mkdir -p "$(dirname "$target")"
    notes="$(bw_ get notes "$item")"
    [ -n "$notes" ] || die "vault item '$item' is empty or missing (bw get notes)"
    case "$format" in
      env) validate_env "$item" "$notes" ;;
      json) validate_json "$item" "$notes" "$json_validate" ;;
      raw) : ;;
      *) die "unknown format '$format' for item '$item'" ;;
    esac
    printf '%s\n' "$notes" > "$target"
    chmod "$chmod_mode" "$target"
    while IFS= read -r rc; do
      [ -n "$rc" ] || continue
      wire_source_into "$target" "$(expand_path "$rc")"
    done < <(jq -r '.sourceInto[]?' <<<"$entry")
    log "provisioned '$item' → ${target#"$WS"/} ($format, $(wc -c <"$target") bytes)"
    n=$((n+1))
  done < <(jq -c '.provision[]' "$MANIFEST")
  log "provisioned $n item(s) from vault; all targets are gitignored runtime files"
  [ "$RELOCK" -eq 1 ] && bw_ lock >/dev/null 2>&1 || true
}

case "$MODE" in
  dry-run)   dry_run ;;
  export)   export_item ;;
  provision) provision ;;
  *)         die "unreachable mode: $MODE" ;;
esac
