"""Redacted curl-equivalent generation for debugging and docs."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

REDACTED = "…REDACTED"

SENSITIVE_HEADERS = {
    "x-api-key",
    "x-signature",
    "x-secret-key",
    "x-w-csrf-token",
    "authorization",
    "cookie",
}
SENSITIVE_COOKIES = {"phpsessid", "cf_clearance", "session", "sid"}


def redact(value: str, keep: int = 6) -> str:
    """Redact ``value``, keeping only a short identifying prefix."""
    if not value:
        return value
    if len(value) <= keep:
        return value
    return value[:keep] + REDACTED


def _header_part(name: str, value: str) -> str:
    if name.lower() in SENSITIVE_HEADERS:
        value = redact(value)
    return f'"{name}: {value}"'


def _cookie_part(cookies: Mapping[str, str] | Sequence[dict]) -> str:
    pairs: list[tuple[str, str]] = []
    if isinstance(cookies, Mapping):
        pairs = list(cookies.items())
    else:
        for c in cookies:
            if isinstance(c, dict) and c.get("name") and c.get("value"):
                pairs.append((c["name"], c["value"]))
    if not pairs:
        return ""
    # Every cookie value is redacted: even "harmless" analytics cookies can
    # embed identifying tokens, and showing only names is enough for a recipe.
    rendered = "; ".join(f"{name}={redact(value, 8)}" for name, value in pairs)
    return rendered


def curl_command(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | Sequence[dict] | None = None,
    body: object = None,
    max_body: int = 800,
) -> str:
    """Render a curl command line that reproduces a request (secrets redacted)."""
    parts = ["curl", "-sS", "-X", method.upper(), f'"{url}"']
    for name, value in (headers or {}).items():
        parts.extend(["-H", _header_part(name, value)])
    cookie_header = _cookie_part(cookies or {})
    if cookie_header:
        parts.extend(["-b", f'"{cookie_header}"'])
    if body is not None and body != "":
        if isinstance(body, (dict, list)):
            body_str = json.dumps(body, separators=(",", ":"))
        else:
            body_str = str(body)
        if body_str:
            parts.extend(["-d", f"'{body_str[:max_body]}'"])
    return " ".join(parts)


__all__ = ["curl_command", "redact", "SENSITIVE_HEADERS", "SENSITIVE_COOKIES"]
