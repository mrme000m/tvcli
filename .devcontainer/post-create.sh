#!/usr/bin/env bash
# post-create.sh — tvcli devcontainer bootstrap, runs once after container create.
# Keeps browser-debug/ansible/deps.yml the single source of truth for the
# browser-debug dependency stack; this script only bootstraps ansible itself.
set -euo pipefail

WS="$(cd "$(dirname "$0")/.." && pwd)"
cd "$WS"

if ! command -v ansible-playbook >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ansible
fi

# Debian's ansible 7.7 ships community.docker 3.4.7; docker_compose_v2 (guacamole
# tasks, opt-in) needs >= 3.6.0 to resolve at parse time.
ansible-galaxy collection install community.docker >/dev/null 2>&1 || true

ansible-playbook browser-debug/ansible/deps.yml \
  -i localhost, \
  -e ansible_connection=local \
  -e ansible_python_interpreter=/usr/bin/python3 \
  -e tv_workspace="$WS/browser-debug" \
  -e install_xvfb=true \
  -e install_x11vnc=true \
  --tags display,bdg,bw

go build -o tvcli ./cmd/tvcli

[ -x "$HOME/.opencode/bin/opencode" ] || curl -fsSL https://opencode.ai/install | bash

# Runtime secrets from the Bitwarden vault (bw CLI + secrets/manifest.json).
# Idempotent; exit 2 (credentials not configured) is a warning, not a failure
# — the container builds and runs, just without live credentials.
bash browser-debug/secrets/bw-provision.sh || \
  echo "  [post-create] WARN: secrets not provisioned (set BW_CLIENTID / BW_CLIENTSECRET / BW_PASSWORD as codespace secrets) — see browser-debug/secrets/README.md"
