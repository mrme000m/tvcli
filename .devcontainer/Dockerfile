# tvcli devcontainer — Go toolchain + browser-debug debugger stack.
# Base keeps Go + vscode user/sudo; this layer bakes in everything the
# browser-debug workspace needs at runtime so post-create stays fast:
#   - Chromium GUI runtime libs (CloakBrowser stealth build)
#   - Virtual display Xvfb + x11vnc (VNC :5900) + noVNC viewer (:6080)
#   - ansible (runs browser-debug/ansible/deps.yml)
#   - jq (bw-provision manifest parsing; bw CLI itself installs via npm)
FROM mcr.microsoft.com/devcontainers/go:1.26-bookworm

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ansible jq \
    xvfb x11vnc novnc websockify \
    libglib2.0-0 libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 \
    libgtk-3-0 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
