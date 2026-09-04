"""Shared constants and tiny pure helpers."""
from __future__ import annotations

import time

# Transport origins (verified live 2026-09-04; see the skill references/).
WT_ORIGIN = "https://wundertrading.com"
MCP_URL = "https://wundertrading.com:2083/mcp"
MARKET_ORIGIN = "https://wundertrading.com:2087"

# The CloakBrowser fingerprint the session surface expects. Keeping the same
# UA as the headful browser materially improves raw httpx's success rate
# against Cloudflare's fingerprint check.
CLOAK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.7680.177.5 Safari/537.36"
)

DEFAULT_TIMEOUT = 30.0
DEFAULT_RECV_WINDOW = "60000"
DEFAULT_USER_AGENT = CLOAK_UA


def now_ms() -> int:
    """Current Unix time in milliseconds (HMAC timestamp format)."""
    return int(time.time() * 1000)


__all__ = [
    "WT_ORIGIN",
    "MCP_URL",
    "MARKET_ORIGIN",
    "CLOAK_UA",
    "DEFAULT_TIMEOUT",
    "DEFAULT_RECV_WINDOW",
    "DEFAULT_USER_AGENT",
    "now_ms",
]
