#!/usr/bin/env bash
# ga-entrypoint — single-purpose supervisor for the grid-ga container.
#
# The GA agent (dsh web serving the `ga` preset, DeepSeek Harness +
# prime-agent) runs as PID 1 in the FOREGROUND: its death IS the container's
# death, and the `--restart unless-stopped` policy brings it back. There is
# deliberately nothing else to supervise — no browser, no PocketBase, no
# daemon. The grid-autonomy trading stack lives in its OWN container on the
# same docker network (grid-net) and is reached over docker DNS.
#
# Boot order:
#   (1) vault_loader.sh with BW_VAULT_ONLY=cf,llm — materialize the
#       Cloudflare vault items (opencode-cloudflare + cloudflare-tunnels)
#       AND the LLM provider keys (provider-keys: NVIDIA/OpenRouter/Mistral)
#       into /data/secrets/grid-vault.env; fail-soft when the BW_* env is
#       unset (manual/local runs).
#   (2) source the vault env when produced + bridge the CF env names the
#       dsh/prime-agent Cloudflare stack reads.
#   (3) seed the runtime DSH home (/data/dsh — the grid-dsh volume SHARED
#       with the grid-autonomy container, holding yesterday's seeded GA
#       home) from the baked seed home /opt/dsh-home, with the same
#       revision-refresh logic as the grid entrypoint's dsh component:
#       first-boot copy, .baked-rev/.seeded-rev compare, per-file preset
#       refresh preserving operator edits, settings.yaml render guarded by
#       sha256 hand-edit detection, prime-agent config render.
#   (4) bootstrap the /srv/tvcli workbench (host-mounted git clone of the
#       PUBLIC repo — anonymous read; GH_TOKEN enables authenticated push).
#   (5) exec dsh web in the foreground.
set -uo pipefail

GA=/app/ga
export DSH_HOME="${DSH_HOME:-/data/dsh}"
export DSH_WEB_PORT="${DSH_WEB_PORT:-3081}"
export GA_REPO="${GA_REPO:-/srv/tvcli}"
export PRIME_AGENT_CODING_AGENT_DIR="$DSH_HOME/prime-agent"
SEED_HOME=/opt/dsh-home

log()  { echo "[ga-entrypoint $(date -u +%H:%M:%S)] $*"; }
warn() { echo "[ga-entrypoint $(date -u +%H:%M:%S)] WARN: $*" >&2; }

log "grid-ga container starting (dsh web :$DSH_WEB_PORT, DSH_HOME=$DSH_HOME, repo=$GA_REPO)"
log "  arch: $(uname -m)   node: $(node --version 2>/dev/null || echo n/a)   python: $(python3 --version 2>&1)"

mkdir -p "$DSH_HOME" /data/secrets /data/bw-cli

# ── (1) vault-driven secrets (Cloudflare + LLM provider items) ──────────────
# The GA container loads NO trading secrets (no WT creds, no session
# cookies) — BW_VAULT_ONLY=cf,llm restricts vault_loader.sh to: the two
# Cloudflare items (opencode-cloudflare: CLOUDFLARE_ACCOUNT_ID/API_KEY;
# cloudflare-tunnels: CF_ACCOUNT_ID + CF_API_TOKEN_READ/WRITE for the cf
# skill) and the LLM provider keys (provider-keys → NVIDIA_API_KEY,
# OPENROUTER_API_KEY, MISTRAL_API_KEY, NVIDIA_BASE_URL — the GA's worker
# fallback chain on dsh's llm-pi-ai providers and prime-agent's
# nvidia/openrouter/mistral providers, merged fail-soft by
# prime_agent_config.py only for present keys). Fail-soft: no BW_* env →
# exit 0 "vault disabled" inside the loader; a failed load warns and
# continues (bind-mounted/inline env).
if [ -n "${BW_URL:-}" ] && [ -n "${BW_CLIENTID:-}" ] && [ -n "${BW_CLIENTSECRET:-}" ] && [ -n "${BW_PASSWORD:-}" ]; then
  log "vault enabled — running vault_loader.sh (BW_VAULT_ONLY=cf,llm)"
  if BW_VAULT_ONLY=cf,llm bash "$GA/vault_loader.sh"; then
    log "vault load complete"
  else
    warn "vault_loader.sh failed (exit $?) — continuing with inline env"
  fi
else
  log "vault disabled (no BW_* env) — using inline env"
fi

# ── (2) source the vault env + bridge the CF env names ─────────────────────
if [ -f /data/secrets/grid-vault.env ]; then
  # shellcheck disable=SC1091
  . /data/secrets/grid-vault.env
  log "sourced /data/secrets/grid-vault.env"
  # exec shells (the GA agent, operators) don't inherit PID-1's env — give
  # interactive bash the same secrets on login (guarded, never clobbers)
  for rc in /root/.bashrc /root/.profile; do
    if ! grep -q 'grid-vault.env' "$rc" 2>/dev/null; then
      printf '\n# grid-ga: vault-resolved runtime secrets (CF tokens, …)\n[ -f /data/secrets/grid-vault.env ] && . /data/secrets/grid-vault.env\n' >> "$rc"
    fi
  done
else
  log "no /data/secrets/grid-vault.env (vault disabled or nothing resolved)"
fi
# env bridge — the exact names the dsh/prime-agent Cloudflare stack reads
# (prime_stack stages/env_bridge.py): token CLOUDFLARE_AI_TOKEN (falling
# back to CLOUDFLARE_API_KEY), account CF_ACCOUNT_ID (falling back to
# CLOUDFLARE_ACCOUNT_ID).
export CLOUDFLARE_AI_TOKEN="${CLOUDFLARE_AI_TOKEN:-${CLOUDFLARE_API_KEY:-}}"
export CF_ACCOUNT_ID="${CF_ACCOUNT_ID:-${CLOUDFLARE_ACCOUNT_ID:-}}"

# ── (3) seed the runtime DSH home from the baked seed home ─────────────────
# Same revision-refresh logic as the grid entrypoint's dsh component (the
# block grid-ga inherited): the grid-dsh volume carries the GA home seeded
# by the PREVIOUS image (grid-autonomy's or grid-ga's), so a first grid-ga
# boot with a changed image revision refreshes the baked GA preset files
# PER-FILE — a file the operator edited locally (no longer identical to the
# previously seeded copy under .last-seed/) is preserved untouched — while
# the pnpm-managed web profile is replaced wholesale unless .keep-profile
# marks it. settings.yaml is DERIVED: rendered each boot from the baked
# template with the runtime CF account id (never baked); the render skips
# (warns) when a hand-edited settings.yaml is detected.
BAKED_REV="$(cat "$SEED_HOME/.baked-rev" 2>/dev/null || echo unknown)"
SEEDED_REV="$(cat "$DSH_HOME/.seeded-rev" 2>/dev/null || echo none)"
if [ "$BAKED_REV" != "$SEEDED_REV" ]; then
  if [ "$SEEDED_REV" = "none" ]; then
    log "dsh home: first boot — seeding $DSH_HOME from $SEED_HOME (rev $BAKED_REV)"
    cp -a "$SEED_HOME/." "$DSH_HOME/"
  else
    log "dsh home: image revision changed ($SEEDED_REV → $BAKED_REV) — refreshing baked files"
    for f in preset.yml agent.cordis.yml skills/ga-operations/SKILL.md; do
      if [ ! -e "$DSH_HOME/.agent-presets/ga/$f" ] || cmp -s "$DSH_HOME/.last-seed/ga/$f" "$DSH_HOME/.agent-presets/ga/$f"; then
        mkdir -p "$DSH_HOME/.agent-presets/ga/$(dirname "$f")"
        cp -a "$SEED_HOME/.agent-presets/ga/$f" "$DSH_HOME/.agent-presets/ga/$f"
      else
        warn "dsh home: .agent-presets/ga/$f was edited locally — preserved (image update skipped for it)"
      fi
    done
    if [ -e "$DSH_HOME/.keep-profile" ]; then
      log "dsh home: .keep-profile present — baked web profile left untouched"
    else
      rm -rf "$DSH_HOME/profiles/web"
      mkdir -p "$DSH_HOME/profiles"
      cp -a "$SEED_HOME/profiles/web" "$DSH_HOME/profiles/web"
    fi
  fi
  echo "$BAKED_REV" > "$DSH_HOME/.seeded-rev"
  rm -rf "$DSH_HOME/.last-seed"
  mkdir -p "$DSH_HOME/.last-seed"
  cp -a "$SEED_HOME/.agent-presets/ga" "$DSH_HOME/.last-seed/ga"
fi

# settings.yaml — derived from the baked template each boot. Skip (warn)
# when the current file differs from the last render (hand-edited).
if [ -n "$CF_ACCOUNT_ID" ]; then
  render=1
  if [ -f "$DSH_HOME/settings.yaml" ] && ! grep -q '@CF_ACCOUNT_ID@' "$DSH_HOME/settings.yaml"; then
    CUR_SHA="$(sha256sum "$DSH_HOME/settings.yaml" | cut -d' ' -f1)"
    OLD_SHA="$(cat "$DSH_HOME/.settings.sha256" 2>/dev/null || true)"
    if [ -n "$OLD_SHA" ] && [ "$CUR_SHA" != "$OLD_SHA" ]; then
      render=0
      warn "dsh home: $DSH_HOME/settings.yaml was edited locally — preserved (not re-rendered)"
    fi
  fi
  if [ "$render" = "1" ]; then
    sed "s|@CF_ACCOUNT_ID@|$CF_ACCOUNT_ID|g" "$SEED_HOME/settings.yaml" > "$DSH_HOME/settings.yaml"
    chmod 600 "$DSH_HOME/settings.yaml"
    sha256sum "$DSH_HOME/settings.yaml" | cut -d' ' -f1 > "$DSH_HOME/.settings.sha256"
    log "dsh home: settings.yaml rendered for the runtime CF account (default preset: ga)"
  fi
else
  warn "dsh home: no CF account id in env — settings.yaml not rendered (GA LLM sessions need the CLOUDFLARE_* env)"
fi

# prime-agent runtime config — keyed merges from runtime env (never baked).
if [ -n "$CF_ACCOUNT_ID" ] && [ -n "$CLOUDFLARE_AI_TOKEN" ]; then
  if python3 "$GA/prime_agent_config.py"; then
    log "dsh home: prime-agent config rendered (workers delegate on Cloudflare Workers AI)"
  else
    warn "dsh home: prime_agent_config.py failed — delegated prime-agent workers have no CF provider"
  fi
else
  warn "dsh home: CF keys incomplete — prime-agent config not rendered (delegated workers cannot run CF models)"
fi

# ── (4) bootstrap the /srv/tvcli workbench (persistent git clone) ──────────
# The host bind-mounts /opt/tvcli → /srv/tvcli (created by ga-run.sh as the
# invoking user when missing). First container boot on a fresh host clones
# the PUBLIC repo anonymously (read needs no auth); every later boot does a
# best-effort `git pull --ff-only` (warn on divergence — a divergence means
# local work is ahead: NEVER reset it). With GH_TOKEN set (mapped from the
# GH_PUSH_TOKEN repo secret by ga-run.sh), `gh auth setup-git` wires the git
# credential helper so pushes to origin work.
git config --global --add safe.directory "$GA_REPO" || true
if [ ! -d "$GA_REPO/.git" ]; then
  log "workbench: $GA_REPO empty — cloning https://github.com/mrme000m/tvcli.git (public, anonymous read)"
  if mkdir -p "$GA_REPO" && git clone https://github.com/mrme000m/tvcli.git "$GA_REPO"; then
    log "workbench: clone complete"
  else
    warn "workbench: clone failed — the GA agent will retry on demand (no local repo this boot)"
  fi
else
  log "workbench: $GA_REPO present — attempting git pull --ff-only"
  if git -C "$GA_REPO" pull --ff-only; then
    log "workbench: up to date with origin"
  else
    warn "workbench: git pull --ff-only failed (diverged or offline) — local work preserved, never reset"
  fi
fi
# Push auth: persist the token into gh's own config store, NOT just the
# env. dsh model shells are spawned with a scrubbed parent env (dsh-subprocess
# scrubbedParentEnv() drops token-named vars like GH_TOKEN/GITHUB_TOKEN), so a
# GH_TOKEN env var alone never reaches the GA agent's bash tool — git push /
# gh CLI auth failed with "token not found" in the dsh web UI. Persisting the
# token via `gh auth login --with-token` (writes /root/.config/gh/hosts.yml)
# makes `gh auth git-credential` (the /root/.gitconfig helper below) and
# `gh run watch` resolve it WITHOUT any env var. Re-provisioned on every boot
# (/root is not a volume) — idempotent.
if [ -n "${GH_TOKEN:-}" ]; then
  if gh auth setup-git >/dev/null 2>&1 \
     && printf '%s' "$GH_TOKEN" | gh auth login --with-token --hostname github.com >/dev/null 2>&1; then
    log "workbench: gh auth persisted (GH_TOKEN → gh config; git push + gh CLI work env-free)"
  else
    warn "workbench: gh auth setup-git / login failed — git pushes may lack credentials"
  fi
else
  log "workbench: no GH_TOKEN — read-only workbench (no pushes from this boot)"
fi

# ── (5) dsh web — the GA agent, FOREGROUND (its death = container death) ────
# --trusted-host: the /api browser-trust fence accepts the bind host by
# default — requests arriving through the CF tunnel carry
# Host: dsh.00m.indevs.in and would be rejected without it (dsh-web-app
# lib/startup.js; its 0.0.0.0 warning — patched warn-only in the image —
# asks exactly for this flag). Logs go to BOTH /data/dsh/dsh-web.log (the
# persistent grid-dsh volume) and stdout (docker logs).
cd "$GA_REPO" 2>/dev/null || cd /
log "starting dsh web on 0.0.0.0:$DSH_WEB_PORT (trusted host: ${DSH_TRUSTED_HOST:-dsh.00m.indevs.in})"
exec > >(tee -a "$DSH_HOME/dsh-web.log") 2>&1
exec dsh web \
  --host "${GRID_BIND_HOST:-0.0.0.0}" \
  --port "$DSH_WEB_PORT" \
  --no-open \
  --trusted-host "${DSH_TRUSTED_HOST:-dsh.00m.indevs.in}"
