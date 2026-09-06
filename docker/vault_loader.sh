#!/usr/bin/env bash
# vault_loader.sh — materialize grid-autonomy secrets from the Bitwarden vault.
#
# Machine-authenticates the bw CLI against the self-hosted vault (Vaultwarden),
# then resolves a fixed set of items into runtime env/files:
#   wundertrading        (folder grid-autonomy, login)  → WT_EMAIL/WT_PASSWORD
#   provider-keys        (fields)                       → NVIDIA_API_KEY, OPENROUTER_API_KEY,
#                                                        MISTRAL_VIBE_API_KEY→MISTRAL_API_KEY, [NVIDIA_BASE_URL]
#   opencode-cloudflare  (notes KEY=VAL)                → CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_KEY
#   tvcli-primary-env    (notes KEY=VAL)                → /app/.env (only when absent — mounted file wins)
#   wundertrading-session(notes WT_*)                   → wt-session.env (only when absent — cookies win)
#
# Outputs:
#   GRID_VAULT_ENV_OUT (default /data/secrets/grid-vault.env) — export lines, chmod 600
#
# Fail-soft: no BW_* env → exit 0 "vault disabled". Missing individual items →
# WARN + skip. Wrong master password (unlock fails) → exit 1 (no point continuing).
# NEVER prints secret values — only item names and field presence.
#
# Env: BW_URL, BW_CLIENTID, BW_CLIENTSECRET, BW_PASSWORD (all four required)
#      BITWARDENCLI_APPDATA_DIR (default /data/bw-cli — persistent CLI state)
#      BW_VAULT_ONLY=llm,wt,tv,cf,session (optional subset filter)
#      GRID_VAULT_ENV_OUT / TVCLI_ENV_OUT / WT_SESSION_OUT (path overrides, CI)
set -euo pipefail

BW_URL="${BW_URL:-}"; BW_CLIENTID="${BW_CLIENTID:-}"
BW_CLIENTSECRET="${BW_CLIENTSECRET:-}"; BW_PASSWORD="${BW_PASSWORD:-}"

VAULT_ENV_OUT="${GRID_VAULT_ENV_OUT:-/data/secrets/grid-vault.env}"
TVCLI_ENV_OUT="${TVCLI_ENV_OUT:-/app/.env}"
WT_SESSION_OUT="${WT_SESSION_OUT:-/app/browser-debug/secrets/runtime/wt-session.env}"

log()  { echo "[vault_loader] $*"; }
warn() { echo "[vault_loader] WARN: $*" >&2; }

if [ -z "$BW_URL" ] || [ -z "$BW_CLIENTID" ] || [ -z "$BW_CLIENTSECRET" ] || [ -z "$BW_PASSWORD" ]; then
  log "vault disabled — BW_URL/BW_CLIENTID/BW_CLIENTSECRET/BW_PASSWORD not all set"
  exit 0
fi

export BITWARDENCLI_APPDATA_DIR="${BITWARDENCLI_APPDATA_DIR:-/data/bw-cli}"
mkdir -p "$BITWARDENCLI_APPDATA_DIR" "$(dirname "$VAULT_ENV_OUT")" "$(dirname "$TVCLI_ENV_OUT")" "$(dirname "$WT_SESSION_OUT")"

BW_VAULT_ONLY="${BW_VAULT_ONLY:-}"
want() { [ -z "$BW_VAULT_ONLY" ] && return 0; case ",$BW_VAULT_ONLY," in *",$1,"*) return 0;; *) return 1;; esac; }

# ── authenticate: config server → login (idempotent) → unlock → sync ───────
log "configuring bw CLI server"
bw config server "$BW_URL" >/dev/null

set +e; login_out="$(bw login --apikey 2>&1)"; login_rc=$?; set -e
if [ "$login_rc" -eq 0 ]; then
  log "bw login --apikey done"
elif grep -q "already logged in" <<<"$login_out"; then
  log "bw already logged in"
else
  echo "[vault_loader] FATAL: bw login --apikey failed (rc=$login_rc) — wrong BW_CLIENTID/BW_CLIENTSECRET or unreachable $BW_URL" >&2
  exit 1
fi

BW_SESSION="$(bw unlock --passwordenv BW_PASSWORD --raw)" || {
  echo "[vault_loader] FATAL: bw unlock failed — wrong BW_PASSWORD?" >&2
  exit 1
}
export BW_SESSION
log "vault unlocked"

bw sync >/dev/null
log "vault synced"

# ── item resolution helpers ─────────────────────────────────────────────────
# pick_item <name> [folder-name] → item JSON on stdout, or nothing.
pick_item() {
  local name="$1" folder="${2:-}" folder_id=""
  if [ -n "$folder" ]; then
    folder_id="$(bw list folders 2>/dev/null | F="$folder" python3 -c '
import json, os, sys
try: data = json.load(sys.stdin)
except Exception: data = []
print(next((f["id"] for f in data if f.get("name") == os.environ["F"]), ""))' || true)"
  fi
  bw list items --search "$name" 2>/dev/null | N="$name" FID="$folder_id" python3 -c '
import json, os, sys
try: data = json.load(sys.stdin)
except Exception: data = []
N, FID = os.environ["N"], os.environ.get("FID", "")
items = [i for i in data if i.get("name") == N]
if FID:
    inf = [i for i in items if i.get("folderId") == FID]
    if inf: items = inf
if items: print(json.dumps(items[0]))' || true
}

# env_append <KEY> <shell-quoted-value> — one export line into the vault env.
ENV_TMP="$(mktemp)"
trap 'rm -f "$ENV_TMP"' EXIT
env_append() { printf 'export %s=%s\n' "$1" "$2" >> "$ENV_TMP"; }

SUMMARY=""

# ── wt: WunderTrading credentials (login item in folder grid-autonomy) ──────
if want wt; then
  item="$(pick_item wundertrading grid-autonomy)"
  if [ -n "$item" ]; then
    got="$(printf '%s' "$item" | python3 -c '
import json, shlex, sys
i = json.load(sys.stdin)
u = (i.get("login") or {}).get("username") or ""
p = (i.get("login") or {}).get("password") or ""
if u: print("WT_EMAIL\t" + shlex.quote(u))
if p: print("WT_PASSWORD\t" + shlex.quote(p))')"
    while IFS=$'\t' read -r k v; do
      [ -n "$k" ] && env_append "$k" "$v"
    done <<< "$got"
    SUMMARY="${SUMMARY} wt-creds ok,"
    log "item 'wundertrading' (folder grid-autonomy): username/password loaded"
  else
    SUMMARY="${SUMMARY} wt-creds MISSING,"
    warn "vault item 'wundertrading' (folder grid-autonomy) not found"
  fi
fi

# ── llm: provider-keys (custom fields) ──────────────────────────────────────
if want llm; then
  item="$(pick_item provider-keys)"
  if [ -n "$item" ]; then
    got="$(printf '%s' "$item" | python3 -c '
import json, shlex, sys
i = json.load(sys.stdin)
want = {"NVIDIA_API_KEY": "NVIDIA_API_KEY", "OPENROUTER_API_KEY": "OPENROUTER_API_KEY",
        "MISTRAL_VIBE_API_KEY": "MISTRAL_API_KEY", "NVIDIA_BASE_URL": "NVIDIA_BASE_URL"}
n = 0
for f in (i.get("fields") or []):
    k = want.get((f.get("name") or "").strip())
    v = (f.get("value") or "").strip()
    if k and v:
        print(f"{k}\t{shlex.quote(v)}"); n += 1
print(f"__COUNT\t{n}")')"
    cnt=0
    while IFS=$'\t' read -r k v; do
      if [ "$k" = "__COUNT" ]; then cnt="$v"; continue; fi
      env_append "$k" "$v"
    done <<< "$got"
    SUMMARY="${SUMMARY} llm+${cnt},"
    log "item 'provider-keys': ${cnt} provider fields loaded"
  else
    SUMMARY="${SUMMARY} llm MISSING,"
    warn "vault item 'provider-keys' not found"
  fi
fi

# ── cf: opencode-cloudflare (notes KEY=VAL) ─────────────────────────────────
if want cf; then
  item="$(pick_item opencode-cloudflare)"
  if [ -n "$item" ]; then
    got="$(printf '%s' "$item" | python3 -c '
import json, shlex, sys
i = json.load(sys.stdin)
want = {"CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_KEY"}
for line in (i.get("notes") or "").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line: continue
    k, _, v = line.partition("=")
    k, v = k.strip(), v.strip()
    if k in want and v: print(f"{k}\t{shlex.quote(v)}")')"
    n=0
    while IFS=$'\t' read -r k v; do
      [ -n "$k" ] && { env_append "$k" "$v"; n=$((n+1)); }
    done <<< "$got"
    SUMMARY="${SUMMARY} cf+${n},"
    log "item 'opencode-cloudflare': ${n} keys loaded"
  else
    SUMMARY="${SUMMARY} cf MISSING,"
    warn "vault item 'opencode-cloudflare' not found"
  fi
fi

# ── cf-tunnels: cloudflare-tunnels (custom fields → cf skill env) ────────────
# Exports the exact env names .agents/skills/cf/scripts/cf.py resolves first:
# CF_ACCOUNT_ID + CF_API_TOKEN_READ / CF_API_TOKEN_WRITE. With these, agents
# (dsh presets, operator shells, anything inside the container) can run the
# cf skill with pure env auth — no vault unlock needed at call time.
if want cf-tunnels; then
  item="$(pick_item cloudflare-tunnels cloudflare)"
  if [ -n "$item" ]; then
    got="$(printf '%s' "$item" | python3 -c '
import json, shlex, sys
i = json.load(sys.stdin)
want = {"account-id": "CF_ACCOUNT_ID", "read-all": "CF_API_TOKEN_READ",
        "write-all": "CF_API_TOKEN_WRITE"}
n = 0
for f in (i.get("fields") or []):
    k = want.get((f.get("name") or "").strip())
    v = (f.get("value") or "").strip()
    if k and v:
        print(f"{k}\t{shlex.quote(v)}"); n += 1
print(f"__COUNT\t{n}")')"
    cnt=0
    while IFS=$'\t' read -r k v; do
      if [ "$k" = "__COUNT" ]; then cnt="$v"; continue; fi
      env_append "$k" "$v"
    done <<< "$got"
    SUMMARY="${SUMMARY} cf-tunnels+${cnt},"
    log "item 'cloudflare-tunnels' (folder cloudflare): ${cnt} tunnel credentials loaded (cf skill env)"
  else
    SUMMARY="${SUMMARY} cf-tunnels MISSING,"
    warn "vault item 'cloudflare-tunnels' (folder cloudflare) not found"
  fi
fi

# ── tv: tvcli-primary-env (notes KEY=VAL → /app/.env, mounted file wins) ────
if want tv; then
  if [ -f "$TVCLI_ENV_OUT" ]; then
    SUMMARY="${SUMMARY} tv-env mounted,"
    log "$TVCLI_ENV_OUT already present (bind-mount wins) — vault tvcli env not written"
  else
    item="$(pick_item tvcli-primary-env)"
    if [ -n "$item" ]; then
      printf '%s' "$item" | python3 -c '
import json, sys
i = json.load(sys.stdin)
for line in (i.get("notes") or "").splitlines():
    line = line.strip().rstrip("\r")
    if not line or line.startswith("#") or "=" not in line: continue
    print(line)' > "$TVCLI_ENV_OUT"
      chmod 600 "$TVCLI_ENV_OUT"
      SUMMARY="${SUMMARY} tv-env ok,"
      log "item 'tvcli-primary-env': wrote $TVCLI_ENV_OUT"
    else
      SUMMARY="${SUMMARY} tv-env MISSING,"
      warn "vault item 'tvcli-primary-env' not found and no mounted .env"
    fi
  fi
fi

# ── session: wundertrading-session (notes WT_* → wt-session.env, cookies win) ──
if want session; then
  if [ -f "$WT_SESSION_OUT" ]; then
    SUMMARY="${SUMMARY} session present,"
    log "$WT_SESSION_OUT already present (existing cookies win) — vault session not written"
  else
    item="$(pick_item wundertrading-session)"
    if [ -n "$item" ]; then
      printf '%s' "$item" | python3 -c '
import json, sys
i = json.load(sys.stdin)
for line in (i.get("notes") or "").splitlines():
    line = line.strip().rstrip("\r")
    if not line or line.startswith("#") or "=" not in line: continue
    if line.startswith("WT_"): print(line)' > "$WT_SESSION_OUT"
      chmod 600 "$WT_SESSION_OUT"
      SUMMARY="${SUMMARY} session ok,"
      log "item 'wundertrading-session': wrote $WT_SESSION_OUT"
    else
      SUMMARY="${SUMMARY} session MISSING,"
      warn "vault item 'wundertrading-session' not found"
    fi
  fi
fi

# ── finalize the vault env file ─────────────────────────────────────────────
if [ -s "$ENV_TMP" ]; then
  cp "$ENV_TMP" "$VAULT_ENV_OUT"
  chmod 600 "$VAULT_ENV_OUT"
  log "wrote $VAULT_ENV_OUT ($(grep -c '^export ' "$VAULT_ENV_OUT") exports)"
else
  warn "no vault values resolved — $VAULT_ENV_OUT not written"
fi

log "summary: vault:${SUMMARY%,}"
exit 0
