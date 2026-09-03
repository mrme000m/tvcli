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
#   bw-provision.sh --strict        # missing vault items are FATAL (default:
#                                   # warn + skip, so a partial vault still
#                                   # provisions everything that exists)
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
STRICT=0

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
    --strict)  STRICT=1 ;;
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

ensure_server_configured() { # idempotent; errors when already logged in (fine)
  local server
  server="$(jq -r '.vault.server // empty' "$MANIFEST")"
  [ -n "$server" ] || return 0
  bw_ config server "$server" >/dev/null 2>&1 || warn "bw config server $server failed (already logged in? continuing)"
}

acquire_session() {
  if [ -n "${BW_SESSION:-}" ]; then
    ensure_server_configured
    log "using existing BW_SESSION"
    return 0
  fi
  ensure_bw || die_unconfigured "neither bw CLI nor npm (to install it) available"
  ensure_server_configured
  if [ -n "${BW_CLIENTID:-}" ] && [ -n "${BW_CLIENTSECRET:-}" ]; then
    : "${BW_PASSWORD:?BW_PASSWORD required with BW_CLIENTID/BW_CLIENTSECRET}"
    bw_ login --apikey >/dev/null 2>&1 || true # already-logged-in is fine
  elif [ -n "${BW_EMAIL:-}" ]; then
    : "${BW_PASSWORD:?BW_PASSWORD required with BW_EMAIL}"
    bw_ login "$BW_EMAIL" --passwordenv BW_PASSWORD >/dev/null 2>&1 || true
  else
    die_unconfigured "no BW_SESSION and no BW_CLIENTID+BW_CLIENTSECRET or BW_EMAIL+BW_PASSWORD (set them as codespace secrets — see README.md)"
  fi
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

wire_source_into() { # $1=env file (abs) $2=shell rc (abs) — guarded, never-clobber source
  [ -f "$1" ] || return 0
  touch "$2" 2>/dev/null || { warn "cannot write $2 (skipping source wiring)"; return 0; }
  # Idempotent: the guarded block for this file is already wired.
  grep -qF "bw-provision guarded source: $1" "$2" 2>/dev/null && return 0
  # Upgrade: drop any legacy unguarded source line for this file (a plain
  # `. file` clobbers env-injected values — e.g. the codespace secrets that
  # login shells also receive via /etc/profile.d — with the vault runtime
  # file's content, and that file may legitimately reference other shell
  # variables that do not exist in this environment).
  if [ -s "$2" ] && grep -qF "$1" "$2" 2>/dev/null; then
    sed -i.bw-provision '\#'"$1"'#d' "$2" 2>/dev/null \
      || warn "could not remove the legacy source line in $2"
  fi
  local body
  body="$(cat <<'BODY'
# bw-provision guarded source: @FILE@ — a value applies only when the variable
# is unset/empty, so environment-injected secrets (codespace secrets, lifecycle
# env) always take precedence over this vault runtime file.
if [ -f "@FILE@" ]; then
  while IFS= read -r __bw_line; do
    case "$__bw_line" in ''|\#*) continue ;; esac
    __bw_k="${__bw_line%%=*}"
    case "$__bw_k" in ''|*[!A-Za-z0-9_]*) continue ;; esac
    [ -n "${!__bw_k:-}" ] && continue
    eval "export $__bw_line"
  done < "@FILE@"
fi
BODY
)"
  printf '\n%s\n' "${body//@FILE@/$1}" >> "$2"
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

export_item() { # push local file content into vault item (Secure Note)
  [ -s "$EXPORT_FILE" ] || die "source file missing/empty: $EXPORT_FILE"
  acquire_session
  bw_ sync >/dev/null
  local existing_id size base template payload
  existing_id="$(bw_ list items --search "$EXPORT_ITEM" \
    | jq -r --arg n "$EXPORT_ITEM" 'map(select(.name == $n))[0].id // empty')"
  size="$(wc -c <"$EXPORT_FILE" | tr -d ' ')"
  base="$(basename "$EXPORT_FILE")"
  template="$(bw_ get template item)"
  if [ "$size" -le 9500 ]; then
    payload="$(jq --arg n "$EXPORT_ITEM" --rawfile notes "$EXPORT_FILE" \
      '.name = $n | .notes = $notes | .type = 2' <<<"$template")"
    if [ -n "$existing_id" ]; then
      printf '%s' "$payload" | bw_ encode | bw_ edit item "$existing_id" >/dev/null
      log "updated vault item '$EXPORT_ITEM' (id ${existing_id:0:8}…)"
    else
      printf '%s' "$payload" | bw_ encode | bw_ create item >/dev/null
      log "created vault item '$EXPORT_ITEM'"
    fi
  else
    # Bitwarden caps cipher notes at 10000 characters — large payloads ride
    # as an ATTACHMENT (up to 100MB), with a marker note.
    payload="$(jq --arg n "$EXPORT_ITEM" --arg m "payload in attachment: $base" \
      '.name = $n | .notes = $m | .type = 2' <<<"$template")"
    if [ -n "$existing_id" ]; then
      printf '%s' "$payload" | bw_ encode | bw_ edit item "$existing_id" >/dev/null
    else
      printf '%s' "$payload" | bw_ encode | bw_ create item >/dev/null
    fi
    local item_id att_id
    item_id="$(bw_ list items --search "$EXPORT_ITEM" \
      | jq -r --arg n "$EXPORT_ITEM" 'map(select(.name == $n))[0].id // empty')"
    [ -n "$item_id" ] || die "item '$EXPORT_ITEM' not found after create"
    # rotation: drop a same-named attachment before re-uploading
    att_id="$(bw_ list items --search "$EXPORT_ITEM" \
      | jq -r --arg n "$EXPORT_ITEM" --arg f "$base" \
      'map(select(.name == $n))[0].attachments // [] | map(select(.fileName == $f))[0].id // empty')"
    [ -z "$att_id" ] || bw_ delete attachment "$att_id" --itemid "$item_id" >/dev/null 2>&1 || true
    bw_ create attachment --file "$EXPORT_FILE" --itemid "$item_id" >/dev/null
    log "vault item '$EXPORT_ITEM' carries attachment '$base' ($size bytes)"
  fi
  [ "$RELOCK" -eq 1 ] && bw_ lock >/dev/null 2>&1 || true
}

provision() {
  acquire_session
  bw_ sync >/dev/null
  mkdir -p "$RUNTIME_DIR"
  local n=0
  local skipped=0
  while IFS= read -r entry; do
    item="$(jq -r '.item' <<<"$entry")"
    target_rel="$(jq -r '.target' <<<"$entry")"
    format="$(jq -r '.format' <<<"$entry")"
    chmod_mode="$(jq -r '.chmod // "600"' <<<"$entry")"
    json_validate="$(jq -r '.jsonValidate // empty' <<<"$entry")"
    attachment="$(jq -r '.attachment // empty' <<<"$entry")"
    case "$target_rel" in
      /*) target="$target_rel" ;;
      *)  target="$WS/$target_rel" ;;
    esac
    mkdir -p "$(dirname "$target")"
    if [ -n "$attachment" ]; then
      # Large payload: an item ATTACHMENT (Bitwarden caps notes at 10k chars).
      item_json="$(bw_ list items --search "$item" 2>/dev/null)"
      item_id="$(jq -r --arg n "$item" 'map(select(.name == $n))[0].id // empty' <<<"$item_json")"
      att_id="$(jq -r --arg n "$item" --arg f "$attachment" \
        'map(select(.name == $n))[0].attachments // [] | map(select(.fileName == $f))[0].id // empty' <<<"$item_json")"
      if [ -z "$item_id" ] || [ -z "$att_id" ]; then
        if [ "$STRICT" -eq 1 ]; then
          die "vault item '$item' or its attachment '$attachment' is missing"
        fi
        warn "vault item '$item' (attachment '$attachment') missing — skipped; --strict makes this fatal"
        skipped=$((skipped+1))
        continue
      fi
      if ! bw_ get attachment "$att_id" --itemid "$item_id" --output "$target" >/dev/null 2>&1; then
        if [ "$STRICT" -eq 1 ]; then
          die "attachment fetch failed for '$item' → $attachment"
        fi
        warn "attachment fetch failed for '$item' — skipped; --strict makes this fatal"
        skipped=$((skipped+1))
        continue
      fi
      chmod "$chmod_mode" "$target"
      case "$format" in
        env) validate_env "$item" "$(cat "$target")" ;;
        json) validate_json "$item" "$(cat "$target")" "$json_validate" ;;
        raw) : ;;
        *) die "unknown format '$format' for item '$item'" ;;
      esac
    else
      if ! notes="$(bw_ get notes "$item" 2>/dev/null)" || [ -z "$notes" ]; then
        if [ "$STRICT" -eq 1 ]; then
          die "vault item '$item' is empty or missing (bw get notes)"
        fi
        warn "vault item '$item' missing — skipped (target kept as-is); --strict makes this fatal"
        skipped=$((skipped+1))
        continue
      fi
      case "$format" in
        env) validate_env "$item" "$notes" ;;
        json) validate_json "$item" "$notes" "$json_validate" ;;
        raw) : ;;
        *) die "unknown format '$format' for item '$item'" ;;
      esac
      printf '%s\n' "$notes" > "$target"
      chmod "$chmod_mode" "$target"
    fi
    while IFS= read -r rc; do
      [ -n "$rc" ] || continue
      wire_source_into "$target" "$(expand_path "$rc")"
    done < <(jq -r '.sourceInto[]?' <<<"$entry")
    log "provisioned '$item' → ${target#"$WS"/} ($format, $(wc -c <"$target") bytes)"
    n=$((n+1))
  done < <(jq -c '.provision[]' "$MANIFEST")
  log "provisioned $n item(s) from vault (skipped: $skipped missing); all targets are gitignored runtime files"
  [ "$RELOCK" -eq 1 ] && bw_ lock >/dev/null 2>&1 || true
}

case "$MODE" in
  dry-run)   dry_run ;;
  export)   export_item ;;
  provision) provision ;;
  *)         die "unreachable mode: $MODE" ;;
esac
