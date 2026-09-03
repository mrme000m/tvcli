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
  --tags display,bdg

go build -o tvcli ./cmd/tvcli

curl -fsSL https://opencode.ai/install | bash

ACC="${CLOUDFLARE_ACCOUNT_ID:-}"
KEY="${CLOUDFLARE_API_KEY:-}"
[ -n "$ACC" ] || ACC="$(sed -n 's/^CLOUDFLARE_ACCOUNT_ID=//p' /workspaces/.codespaces/shared/.env-secrets 2>/dev/null || true)"
[ -n "$KEY" ] || KEY="$(sed -n 's/^CLOUDFLARE_API_KEY=//p' /workspaces/.codespaces/shared/.env-secrets 2>/dev/null || true)"

grep -q 'opencode/bin' "$HOME/.profile" 2>/dev/null || \
  printf 'export PATH="$HOME/.opencode/bin:$PATH"\nexport CLOUDFLARE_ACCOUNT_ID="%s"\nexport CLOUDFLARE_API_KEY="%s"\n' \
    "$ACC" "$KEY" >> "$HOME/.profile"

grep -q 'CLOUDFLARE_ACCOUNT_ID' "$HOME/.bashrc" 2>/dev/null || \
  printf 'export CLOUDFLARE_ACCOUNT_ID="%s"\nexport CLOUDFLARE_API_KEY="%s"\n' \
    "$ACC" "$KEY" >> "$HOME/.bashrc"
