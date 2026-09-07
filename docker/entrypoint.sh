#!/usr/bin/env bash
# grid-entrypoint — supervisor for the grid-autonomy all-in-one container.
#
# Brings up, in order: config patch, Xvfb, PocketBase, tvcli serve,
# CloakBrowser (launch.mjs + wt.mjs session restore), the daemon, the
# console, dsh web (the GA agent) — then supervises: if the DAEMON dies the
# whole container shuts down (the VPS restart policy brings it back) —
# EXCEPT an operator stop (KILL file present), which keeps the console up so
# the daemon can be restarted from the UI (mirroring the Mac's launchd
# split); any other component dying is logged and the rest keep running.
#
# GRID_COMPONENTS=xvfb,pb,serve,browser,daemon,console,dsh (default: all)
set -uo pipefail

APP=/app
GRID="${APP}/agents/grid-autonomy"
export GRID_MODE="${GRID_MODE:-live-paper}"
export GRID_STRICT_ENV="${GRID_STRICT_ENV:-0}"
export CB_PROFILE="${CB_PROFILE:-/data/browser-profile}"
export DISPLAY="${DISPLAY:-:99}"
export TVCLI_SERVER="${TVCLI_SERVER:-http://127.0.0.1:8765}"
export PB_HOST="${PB_HOST:-127.0.0.1}"
export PB_PORT="${PB_PORT:-8090}"
export PB_VERSION="${PB_VERSION:-0.40.2}"
export PB_ADMIN_EMAIL="${PB_ADMIN_EMAIL:-admin@example.com}"

COMPONENTS="${GRID_COMPONENTS:-xvfb,pb,serve,browser,daemon,console,dsh}"
has() { case ",$COMPONENTS," in *",$1,"*) return 0;; *) return 1;; esac; }
enabled_count() { local n=0 c; for c in xvfb pb serve browser daemon console dsh; do has "$c" && n=$((n+1)); done; echo "$n"; }

log() { echo "[grid-entrypoint $(date -u +%H:%M:%S)] $*"; }
warn() { echo "[grid-entrypoint $(date -u +%H:%M:%S)] WARN: $*" >&2; }

# ── (0) banner + env summary (NEVER print secret values) ────────────────────
log "grid-autonomy container starting"
log "  mode:            ${GRID_MODE}"
log "  components:      ${COMPONENTS}"
log "  strict env:      ${GRID_STRICT_ENV}"
log "  arch:            $(uname -m)   node: $(node --version 2>/dev/null || echo n/a)   python: $(python3 --version 2>&1)"
for v in SESSION SIGNATURE TV_USER DEVICE_T CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_KEY \
         CLOUDFLARE_AI_TOKEN NVIDIA_API_KEY OPENROUTER_API_KEY MISTRAL_API_KEY PB_ADMIN_PASS; do
  if [ -n "${!v:-}" ]; then echo "[grid-entrypoint]   ${v}: set"; else echo "[grid-entrypoint]   ${v}: (unset)"; fi
done

# ── (1) nothing enabled → idle shell (image as a toolbox) ───────────────────
if [ "$(enabled_count)" = "0" ]; then
  log "no components enabled (GRID_COMPONENTS='${COMPONENTS}') — idling"
  exec sleep infinity
fi

cd "$APP"

# ── (2) patch container-inappropriate paths in config.yaml ─────────────────
if has daemon || has browser || has console; then
  if ! python3 "$APP/docker/patch_config.py" "$GRID/config.yaml"; then
    warn "patch_config.py failed — daemon may try to launch the browser from stale macOS paths"
  fi
fi

# ── (3) fresh container boot: drop a stale single-writer pid guard ──────────
mkdir -p "$GRID/state" /data/secrets /data/bw-cli
rm -f "$GRID/state/daemon.pid"

# ── (4) operator stop flag ──────────────────────────────────────────────────
if [ -e "$GRID/KILL" ]; then
  warn "KILL file present at $GRID/KILL — the daemon will NOT be started (operator stop)"
  warn "the console stays up; resume with its Start action (clears KILL) or: docker exec <ctr> rm -f /app/agents/grid-autonomy/KILL"
fi

# ── (5) env validation (WARN by default; GRID_STRICT_ENV=1 → fatal) ─────────
# Source the persisted LLM sidecar first so keys set from the console count.
LLM_ENV="$GRID/state/llm.env"
if [ -f "$LLM_ENV" ]; then
  # shellcheck disable=SC1090
  . "$LLM_ENV"
  log "sourced state/llm.env (persisted LLM provider settings)"
fi

# ── (5a) vault-driven secrets (Bitwarden) ───────────────────────────────────
# When all four BW_* machine-auth env vars are present, materialize the vault
# items (WT creds, provider keys, CF keys, tvcli .env, WT session cookies)
# into /data/secrets/grid-vault.env + runtime files, then source them — vault
# values win over llm.env (vault-driven deployment). Mounted files always win
# over vault writes for .env / wt-session.env.
if [ -n "${BW_URL:-}" ] && [ -n "${BW_CLIENTID:-}" ] && [ -n "${BW_CLIENTSECRET:-}" ] && [ -n "${BW_PASSWORD:-}" ]; then
  log "vault enabled — running docker/vault_loader.sh"
  if bash "$APP/docker/vault_loader.sh"; then
    log "vault load complete"
  else
    warn "vault_loader.sh failed (exit $?) — continuing with bind-mounted/inline env"
  fi
  if [ -f /data/secrets/grid-vault.env ]; then
    # shellcheck disable=SC1091
    . /data/secrets/grid-vault.env
    log "sourced /data/secrets/grid-vault.env"
    # exec shells (agents, operators) don't inherit PID-1's env — give
    # interactive bash the same secrets on login (guarded, never clobbers)
    for rc in /root/.bashrc /root/.profile; do
      if ! grep -q 'grid-vault.env' "$rc" 2>/dev/null; then
        printf '\n# grid-autonomy: vault-resolved runtime secrets (CF tokens, LLM keys, …)\n[ -f /data/secrets/grid-vault.env ] && . /data/secrets/grid-vault.env\n' >> "$rc"
      fi
    done
  fi
else
  log "vault disabled (no BW_* env) — using bind-mounted files"
fi
env_fail() {
  if [ "$GRID_STRICT_ENV" = "1" ]; then
    echo "[grid-entrypoint] FATAL (GRID_STRICT_ENV=1): $*" >&2
    exit 1
  fi
  warn "$* (GRID_STRICT_ENV=0 — continuing; the daemon's LLM chain will fall back)"
}
if [ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ] || { [ -z "${CLOUDFLARE_API_KEY:-}" ] && [ -z "${CLOUDFLARE_AI_TOKEN:-}" ]; }; then
  env_fail "Cloudflare Workers AI keys missing (need CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_KEY or CLOUDFLARE_AI_TOKEN)"
fi
[ -n "${NVIDIA_API_KEY:-}" ]      || env_fail "NVIDIA_API_KEY unset"
[ -n "${OPENROUTER_API_KEY:-}" ]  || env_fail "OPENROUTER_API_KEY unset"
[ -n "${MISTRAL_API_KEY:-}" ]     || env_fail "MISTRAL_API_KEY unset"

DAEMON_ARGS=""
if [ "$GRID_MODE" = "live-paper" ]; then
  DAEMON_ARGS="--live-paper"
  log "mode live-paper: the daemon WILL create paper grid bots on WunderTrading"
else
  log "mode dry-run: planning only (no bot changes)"
fi

# Child bookkeeping for the supervisor loop + shutdown.
declare -a CHILD_PIDS=()
declare -a CHILD_NAMES=()
DAEMON_PID=""
track() { CHILD_PIDS+=("$!"); CHILD_NAMES+=("$1"); }
is_daemon_pid() { [ -n "$DAEMON_PID" ] && [ "$1" = "$DAEMON_PID" ]; }

# ── (12, defined early) graceful shutdown ───────────────────────────────────
SHUTTING_DOWN=0
shutdown() {
  [ "$SHUTTING_DOWN" = "1" ] && return 0
  SHUTTING_DOWN=1
  log "shutting down ($1)…"
  # NOTE: deliberately NOT calling POST /kill on :8799 — that endpoint writes
  # the KILL file, and a leftover KILL makes the daemon refuse to start on
  # next boot (an auto-restarting container would stay bricked). Plain
  # SIGTERM is the graceful path (same as scripts/stop.sh).
  local i pid
  # SIGTERM every tracked child first (the daemon exits gracefully on TERM);
  # an adopted console-started daemon is tracked via DAEMON_PID only.
  for i in "${!CHILD_PIDS[@]}"; do
    pid="${CHILD_PIDS[$i]}"
    kill -TERM "$pid" 2>/dev/null || true
  done
  [ -n "${DAEMON_PID:-}" ] && kill -TERM "$DAEMON_PID" 2>/dev/null || true
  [ -n "${KEEPER_PID:-}" ] && kill -TERM "$KEEPER_PID" 2>/dev/null || true
  pkill -TERM -f "browser-debug/cloakbrowser" 2>/dev/null || true
  # Give the daemon up to ~25s to exit cleanly, then SIGKILL stragglers.
  for _ in $(seq 1 125); do
    local any=0
    for i in "${!CHILD_PIDS[@]}"; do kill -0 "${CHILD_PIDS[$i]}" 2>/dev/null && any=1; done
    [ -n "${DAEMON_PID:-}" ] && kill -0 "$DAEMON_PID" 2>/dev/null && any=1
    [ "$any" = "0" ] && break
    sleep 0.2
  done
  for i in "${!CHILD_PIDS[@]}"; do kill -KILL "${CHILD_PIDS[$i]}" 2>/dev/null || true; done
  log "shutdown complete"
  exit 0
}
trap 'shutdown SIGTERM' TERM
trap 'shutdown SIGINT' INT

# ── (6) Xvfb on :99 ─────────────────────────────────────────────────────────
if has xvfb || has browser; then
  if has xvfb; then
    rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true
    Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
    track xvfb
    for _ in $(seq 1 50); do
      [ -S /tmp/.X11-unix/X99 ] && break
      sleep 0.2
    done
    if [ -S /tmp/.X11-unix/X99 ]; then
      log "Xvfb up on :99 (pid $(pgrep -x Xvfb | head -1))"
    else
      warn "Xvfb did not come up on :99 (see /tmp/xvfb.log) — the browser cannot run"
    fi
  fi
fi

# ── (7) PocketBase (idempotent setup: admin, hooks, migrations, serve) ───────
if has pb; then
  mkdir -p "$GRID/.pocketbase"
  if [ ! -x "$GRID/.pocketbase/pocketbase" ]; then
    log "installing baked PocketBase into $GRID/.pocketbase/"
    cp /opt/pocketbase/pocketbase "$GRID/.pocketbase/pocketbase"
    chmod +x "$GRID/.pocketbase/pocketbase"
  fi
  log "running scripts/setup_pocketbase.sh (idempotent)"
  if ! (cd "$APP" && bash "$GRID/scripts/setup_pocketbase.sh") >>"$GRID/.pocketbase/setup.log" 2>&1; then
    warn "PocketBase setup failed (see $GRID/.pocketbase/setup.log) — daemon runs without the PB side channel"
  fi
  PB_ENV="$GRID/.pocketbase/pb.env"
  if [ -f "$PB_ENV" ]; then
    # shellcheck disable=SC1090
    . "$PB_ENV"
    log "PocketBase ready at ${PB_URL:-http://127.0.0.1:8090} (pb.env sourced)"
  fi
fi

# ── (8) tvcli serve (foreground child; TradingView auth from bind-mounted .env) ──
if has serve; then
  if [ ! -f "$APP/.env" ]; then
    warn "/app/.env not found (bind-mount it) — tvcli serve will start but auth-dependent calls will fail"
  fi
  "$APP/tvcli" serve >>"$GRID/state/tvcli-serve.log" 2>&1 &
  track tvcli
  up=0
  for _ in $(seq 1 120); do
    if curl -fsS -m 2 http://127.0.0.1:8765/health >/dev/null 2>&1; then up=1; break; fi
    sleep 0.5
  done
  if [ "$up" = "1" ]; then
    log "tvcli serve up on :8765"
  else
    warn "tvcli serve did not answer :8765/health within 60s — continuing (screening degrades fail-soft)"
  fi
fi

# ── (9) CloakBrowser (CDP :9222) + WT session restore ────────────────────────
cdp_up() { curl -fsS -m 3 http://127.0.0.1:9222/json/version >/dev/null 2>&1; }
if has browser; then
  mkdir -p "$CB_PROFILE"
  ok=0
  for attempt in 1 2 3; do
    log "launching CloakBrowser (attempt $attempt, profile $CB_PROFILE)"
    DISPLAY="${DISPLAY}" CB_PROFILE="$CB_PROFILE" \
      node "$APP/browser-debug/launch.mjs" >>"$GRID/state/browser-launch.log" 2>&1
    # give the detached chrome a moment to bring CDP up
    for _ in $(seq 1 40); do
      cdp_up && break
      sleep 0.5
    done
    if cdp_up; then ok=1; break; fi
    warn "CloakBrowser CDP :9222 not answering (attempt $attempt)"
    sleep 2
  done
  if [ "$ok" = "1" ]; then
    log "CloakBrowser CDP ready on :9222"
    # WT session restore / page assert — non-fatal on failure.
    if timeout 300 env CB_PROFILE="$CB_PROFILE" DISPLAY="${DISPLAY}" \
        node "$APP/browser-debug/wt.mjs" >>"$GRID/state/wt-restore.log" 2>&1; then
      log "WunderTrading session restored (wt.mjs OK)"
    else
      warn "wt.mjs restore failed/timeout — see $GRID/state/wt-restore.log"
    fi
    # Auth probe (wt.mjs open exits non-zero on AUTH FAIL); when the stored
    # session is stale AND credentials are available, log in with wt-login.mjs
    # and re-run the restore once.
    if timeout 60 env CB_PROFILE="$CB_PROFILE" DISPLAY="${DISPLAY}" \
        node "$APP/browser-debug/wt.mjs" open https://wundertrading.com/en/trader/grid_bots \
        >>"$GRID/state/wt-restore.log" 2>&1; then
      log "WT auth probe: AUTH OK"
    else
      warn "WT auth probe: AUTH FAIL (stale session?)"
      if [ -n "${WT_EMAIL:-}" ] && [ -n "${WT_PASSWORD:-}" ]; then
        log "attempting credential login (wt-login.mjs)…"
        if timeout 300 env CB_PROFILE="$CB_PROFILE" DISPLAY="${DISPLAY}" WT_EMAIL="$WT_EMAIL" WT_PASSWORD="$WT_PASSWORD" \
            node "$APP/browser-debug/wt-login.mjs" >>"$GRID/state/wt-restore.log" 2>&1; then
          log "WT credential login OK — re-running session restore"
          timeout 300 env CB_PROFILE="$CB_PROFILE" DISPLAY="${DISPLAY}" \
            node "$APP/browser-debug/wt.mjs" >>"$GRID/state/wt-restore.log" 2>&1 \
            && log "WT session re-restored (wt.mjs OK)" \
            || warn "wt.mjs re-restore failed (see $GRID/state/wt-restore.log)"
        else
          warn "wt-login.mjs failed/timeout (creds wrong? Cloudflare challenge?) — see $GRID/state/wt-restore.log"
        fi
      else
        warn "no WT_EMAIL/WT_PASSWORD for credential login — vault 'wundertrading' item missing?"
      fi
    fi
  else
    warn "CloakBrowser did not come up after 3 attempts — daemon browser transport unavailable (watchdog will retry)"
  fi
fi

# ── (10) daemon (the reason this container exists) ───────────────────────────
if has daemon; then
  if [ -e "$GRID/KILL" ]; then
    # Operator stop: the daemon stays down but the REST of the stack
    # (console incl.) keeps running, so the stop is recoverable from the
    # UI — mirroring the Mac's launchd split where KILL pauses only the
    # daemon job. The supervisor (13) re-adopts the daemon once the
    # console starts it (scripts/start.sh rewrites state/daemon.pid).
    warn "KILL file present — daemon NOT started (operator stop); Start it from the console (clears KILL) or: docker exec <ctr> rm -f $GRID/KILL"
  else
    (cd "$GRID" && exec python3 daemon.py $DAEMON_ARGS) >>"$GRID/state/daemon.log" 2>&1 &
    track daemon
    DAEMON_PID=$!
    echo "$DAEMON_PID" > "$GRID/state/daemon.pid"
    log "daemon started (pid $DAEMON_PID, args: '${DAEMON_ARGS:-dry-run planning}')"
  fi
fi

# ── (11) console ────────────────────────────────────────────────────────────
if has console; then
  (cd "$GRID" && exec python3 console/server.py) >>"$GRID/state/console.log" 2>&1 &
  track console
  log "console started on :8798"
fi
# ── (11b) dsh web — the GA agent (DeepSeek Harness + prime-agent) :3081 ─────
# Serves the GA-preset default agent over the web UI. The runtime DSH home
# is the persistent grid-dsh volume (DSH_HOME=/data/dsh, image env); it is
# seeded from the baked seed home (/opt/dsh-home) on first boot, and on later
# boots with a changed image revision (.baked-rev) the baked GA preset files
# refresh per-file — a file the operator edited locally (no longer identical
# to the previously seeded copy under .last-seed/) is preserved untouched —
# while the pnpm-managed web profile is replaced wholesale unless
# .keep-profile marks it. settings.yaml is DERIVED: rendered each boot from
# the baked template with the runtime CF account id (never baked); the
# render skips (warns) when a hand-edited settings.yaml is detected.
# prime-agent's config (PRIME_AGENT_CODING_AGENT_DIR, also in the volume) is
# rendered from runtime env by /app/docker/prime_agent_config.py — keyed
# merges, 0600, the key never logged.
if has dsh; then
  SEED_HOME=/opt/dsh-home
  RUN_HOME="${DSH_HOME:-/data/dsh}"
  export DSH_HOME="$RUN_HOME"
  export PRIME_AGENT_CODING_AGENT_DIR="$RUN_HOME/prime-agent"
  mkdir -p "$RUN_HOME"

  BAKED_REV="$(cat "$SEED_HOME/.baked-rev" 2>/dev/null || echo unknown)"
  SEEDED_REV="$(cat "$RUN_HOME/.seeded-rev" 2>/dev/null || echo none)"
  if [ "$BAKED_REV" != "$SEEDED_REV" ]; then
    if [ "$SEEDED_REV" = "none" ]; then
      log "dsh: first boot — seeding $RUN_HOME from $SEED_HOME (rev $BAKED_REV)"
      cp -a "$SEED_HOME/." "$RUN_HOME/"
    else
      log "dsh: image revision changed ($SEEDED_REV → $BAKED_REV) — refreshing baked files"
      for f in preset.yml agent.cordis.yml skills/ga-operations/SKILL.md; do
        if [ ! -e "$RUN_HOME/.agent-presets/ga/$f" ] || cmp -s "$RUN_HOME/.last-seed/ga/$f" "$RUN_HOME/.agent-presets/ga/$f"; then
          mkdir -p "$RUN_HOME/.agent-presets/ga/$(dirname "$f")"
          cp -a "$SEED_HOME/.agent-presets/ga/$f" "$RUN_HOME/.agent-presets/ga/$f"
        else
          warn "dsh: .agent-presets/ga/$f was edited locally — preserved (image update skipped for it)"
        fi
      done
      if [ -e "$RUN_HOME/.keep-profile" ]; then
        log "dsh: .keep-profile present — baked web profile left untouched"
      else
        rm -rf "$RUN_HOME/profiles/web"
        mkdir -p "$RUN_HOME/profiles"
        cp -a "$SEED_HOME/profiles/web" "$RUN_HOME/profiles/web"
      fi
    fi
    echo "$BAKED_REV" > "$RUN_HOME/.seeded-rev"
    rm -rf "$RUN_HOME/.last-seed"
    mkdir -p "$RUN_HOME/.last-seed"
    cp -a "$SEED_HOME/.agent-presets/ga" "$RUN_HOME/.last-seed/ga"
  fi

  # env bridge — the exact names the dsh/prime-agent Cloudflare stack reads
  # (prime_stack stages/env_bridge.py): token CLOUDFLARE_AI_TOKEN (falling
  # back to CLOUDFLARE_API_KEY), account CF_ACCOUNT_ID (falling back to
  # CLOUDFLARE_ACCOUNT_ID).
  export CLOUDFLARE_AI_TOKEN="${CLOUDFLARE_AI_TOKEN:-${CLOUDFLARE_API_KEY:-}}"
  export CF_ACCOUNT_ID="${CF_ACCOUNT_ID:-${CLOUDFLARE_ACCOUNT_ID:-}}"

  # settings.yaml — derived from the baked template each boot. Skip (warn)
  # when the current file differs from the last render (hand-edited).
  if [ -n "$CF_ACCOUNT_ID" ]; then
    render=1
    if [ -f "$RUN_HOME/settings.yaml" ] && ! grep -q '@CF_ACCOUNT_ID@' "$RUN_HOME/settings.yaml"; then
      CUR_SHA="$(sha256sum "$RUN_HOME/settings.yaml" | cut -d' ' -f1)"
      OLD_SHA="$(cat "$RUN_HOME/.settings.sha256" 2>/dev/null || true)"
      if [ -n "$OLD_SHA" ] && [ "$CUR_SHA" != "$OLD_SHA" ]; then
        render=0
        warn "dsh: $RUN_HOME/settings.yaml was edited locally — preserved (not re-rendered)"
      fi
    fi
    if [ "$render" = "1" ]; then
      sed "s|@CF_ACCOUNT_ID@|$CF_ACCOUNT_ID|g" "$SEED_HOME/settings.yaml" > "$RUN_HOME/settings.yaml"
      chmod 600 "$RUN_HOME/settings.yaml"
      sha256sum "$RUN_HOME/settings.yaml" | cut -d' ' -f1 > "$RUN_HOME/.settings.sha256"
      log "dsh: settings.yaml rendered for the runtime CF account (default preset: ga)"
    fi
  else
    warn "dsh: no CF account id in env — settings.yaml not rendered (GA LLM sessions need the CLOUDFLARE_* env)"
  fi

  # prime-agent runtime config — keyed merges from runtime env (never baked).
  if [ -n "$CF_ACCOUNT_ID" ] && [ -n "$CLOUDFLARE_AI_TOKEN" ]; then
    if python3 "$APP/docker/prime_agent_config.py"; then
      log "dsh: prime-agent config rendered (workers delegate on Cloudflare Workers AI)"
    else
      warn "dsh: prime_agent_config.py failed — delegated prime-agent workers have no CF provider"
    fi
  else
    warn "dsh: CF keys incomplete — prime-agent config not rendered (delegated workers cannot run CF models)"
  fi

  # --trusted-host: the /api browser-trust fence accepts the bind host by
  # default — requests arriving through the CF tunnel carry
  # Host: dsh.00m.indevs.in and would be rejected without it (dsh-web-app
  # lib/startup.js; its 0.0.0.0 warning asks exactly for this flag).
  dsh web --host "${GRID_BIND_HOST:-0.0.0.0}" --port "${DSH_WEB_PORT:-3081}" --no-open \
    --trusted-host "${DSH_TRUSTED_HOST:-dsh.00m.indevs.in}" \
    >>"$GRID/state/dsh-web.log" 2>&1 &
  track dsh
  up=0
  for _ in $(seq 1 60); do
    if curl -s -o /dev/null -m 3 "http://127.0.0.1:${DSH_WEB_PORT:-3081}/" 2>/dev/null; then up=1; break; fi
    sleep 1
  done
  if [ "$up" = "1" ]; then
    log "dsh web up on :${DSH_WEB_PORT:-3081} (GA agent, DSH_HOME=$RUN_HOME)"
  else
    warn "dsh web did not answer :${DSH_WEB_PORT:-3081} within 60s — continuing (see $GRID/state/dsh-web.log)"
  fi
fi


# ── (12a) WT session keeper — periodic auth probe + credential re-login ────
# Plain background subshell (NOT tracked in the supervisor: its death must
# never kill the container). Every WT_KEEPER_INTERVAL seconds it probes the
# WT session; on AUTH FAIL with credentials available it re-logs-in via
# wt-login.mjs and re-asserts the page via wt.mjs.
KEEPER_PID=""
if has browser && has daemon; then
  (
    interval="${WT_KEEPER_INTERVAL:-1800}"
    echo "[wt-keeper] started (interval ${interval}s)"
    while :; do
      sleep "$interval"
      if ! timeout 60 env CB_PROFILE="${CB_PROFILE}" DISPLAY="${DISPLAY}" \
          node "$APP/browser-debug/wt.mjs" open https://wundertrading.com/en/trader/grid_bots \
          >>"$GRID/state/wt-keeper.log" 2>&1; then
        echo "[wt-keeper] auth probe FAILED — $(date -u +%H:%M:%SZ)"
        if [ -n "${WT_EMAIL:-}" ] && [ -n "${WT_PASSWORD:-}" ]; then
          if timeout 300 env CB_PROFILE="${CB_PROFILE}" DISPLAY="${DISPLAY}" \
              WT_EMAIL="${WT_EMAIL}" WT_PASSWORD="${WT_PASSWORD}" \
              node "$APP/browser-debug/wt-login.mjs" >>"$GRID/state/wt-keeper.log" 2>&1; then
            echo "[wt-keeper] credential re-login OK — re-asserting WT page"
            timeout 300 env CB_PROFILE="${CB_PROFILE}" DISPLAY="${DISPLAY}" \
              node "$APP/browser-debug/wt.mjs" >>"$GRID/state/wt-keeper.log" 2>&1 \
              && echo "[wt-keeper] page re-asserted" \
              || echo "[wt-keeper] WARN: wt.mjs re-assert failed"
          else
            echo "[wt-keeper] WARN: credential re-login failed (see $GRID/state/wt-keeper.log)"
          fi
        else
          echo "[wt-keeper] WARN: auth lost, no WT_EMAIL/WT_PASSWORD to re-login"
        fi
      else
        echo "[wt-keeper] auth probe OK"
      fi
    done
  ) &
  KEEPER_PID=$!
  log "WT session keeper started (pid $KEEPER_PID, interval ${WT_KEEPER_INTERVAL:-1800}s)"
fi

# ── (13) supervise: daemon death = container death (unless KILL); others = warn ──
log "all components launched; supervising (${#CHILD_PIDS[@]} tracked children)"
daemon_live() { [ -n "$DAEMON_PID" ] && kill -0 "$DAEMON_PID" 2>/dev/null; }
is_zombie() {
  # kill -0 reports an exited-but-unreaped child as alive; the /proc stat
  # state field (Z) tells them apart so a daemon crash is never missed.
  [ "$(cut -d' ' -f3 "/proc/$1/stat" 2>/dev/null)" = "Z" ]
}
adopt_daemon() {
  # The console can (re)start the daemon in place via scripts/start.sh after
  # an operator stop. Adopt its pid (verified against the pidfile + cmdline)
  # so a later daemon crash still shuts the container down for a restart.
  [ -e "$GRID/KILL" ] && return 1
  [ -r "$GRID/state/daemon.pid" ] || return 1
  local newpid
  newpid="$(cat "$GRID/state/daemon.pid" 2>/dev/null || true)"
  [ -n "$newpid" ] || return 1
  kill -0 "$newpid" 2>/dev/null || return 1
  grep -qa "daemon.py" "/proc/$newpid/cmdline" 2>/dev/null || return 1
  DAEMON_PID="$newpid"
  log "adopted console-started daemon (pid $newpid)"
  return 0
}
while :; do
  sleep 2
  # Reap any zombie children first — otherwise the liveness scan below sees
  # them as alive and their exit (a daemon crash!) goes unnoticed.
  while :; do
    any_zombie=0
    for i in "${!CHILD_PIDS[@]}"; do
      if is_zombie "${CHILD_PIDS[$i]}"; then any_zombie=1; break; fi
    done
    [ "$any_zombie" = "1" ] || break
    wait -n || true   # instant: at least one child has exited
  done
  # Re-adopt a console-started daemon so supervision survives operator
  # stop→start cycles.
  daemon_live || adopt_daemon
  # Identify who exited (gone, not zombie — zombies were reaped above).
  exited=""
  for i in "${!CHILD_PIDS[@]}"; do
    pid="${CHILD_PIDS[$i]}"
    if ! kill -0 "$pid" 2>/dev/null; then
      exited="${CHILD_NAMES[$i]}"
      unset "CHILD_PIDS[$i]" "CHILD_NAMES[$i]"
    fi
  done
  # Re-index after unsetting (sparse arrays break the count check above).
  tmp_pids=(); tmp_names=()
  for i in "${!CHILD_PIDS[@]}"; do tmp_pids+=("${CHILD_PIDS[$i]}"); tmp_names+=("${CHILD_NAMES[$i]}"); done
  CHILD_PIDS=("${tmp_pids[@]+"${tmp_pids[@]}"}"); CHILD_NAMES=("${tmp_names[@]+"${tmp_names[@]}"}")
  if [ -n "$exited" ]; then
    if [ "$exited" = "daemon" ]; then
      DAEMON_PID=""
      if [ -e "$GRID/KILL" ]; then
        # Operator stop via the console (KILL + SIGTERM): the container must
        # NOT suicide-restart here — the KILL file persists in the container's
        # writable layer, so every restart would re-refuse the daemon and
        # boot-loop the console into 502s. Keep the console up; the UI's
        # Start/Restart actions recover the daemon, and the supervisor
        # re-adopts it via state/daemon.pid.
        log "daemon exited with KILL present (operator stop) — console stays up; start the daemon from the console (clears KILL)"
      else
        log "daemon exited — shutting the container down (restart policy will bring it back)"
        shutdown "daemon-exit"
      fi
    else
      warn "component '$exited' exited — continuing without it (remaining: ${CHILD_NAMES[*]:-none})"
    fi
  fi
done
