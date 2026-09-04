"""Session-auth ``/en/trader`` transport (raw httpx replay, best effort).

This surface is Cloudflare-fingerprinted. A raw :mod:`httpx` replay succeeds
when ``cf_clearance`` is fresh; otherwise it raises
:class:`wtclient.errors.WunCloudflareError` with a remediation hint. For the
reliable path, use :class:`wtclient.transport.browser.BrowserTransport`.
"""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from typing import Any

from ..config import DEFAULT_TIMEOUT, WT_ORIGIN
from ..curl import curl_command
from ..errors import WunCloudflareError, WunConfigError, WunCsrfError
from ..query import normalize_url, serialize_body
from ..response import Response
from .base import BaseTransport, httpx_request

_CSRF_PATTERNS = (
    re.compile(r'appCsrfToken\s*[:=]\s*["\']([^"\']{20,})["\']'),
    re.compile(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']', re.I),
)


class SessionTransport(BaseTransport):
    """Session-cookie transport for the ``/en/trader/*`` cabinet surface.

    Args:
        cookies: Cookie dict (must contain ``PHPSESSID``).
        base_url: Origin (default ``https://wundertrading.com``).
    """

    name = "session"

    def __init__(
        self,
        cookies: Mapping[str, str] | None = None,
        *,
        base_url: str = WT_ORIGIN,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str | None = None,
    ) -> None:
        super().__init__(timeout=timeout, user_agent=user_agent or WT_ORIGIN)
        self.cookies = dict(cookies or {})
        if not self.cookies.get("PHPSESSID"):
            raise WunConfigError(
                "SessionTransport requires a PHPSESSID cookie",
                remediation=(
                    "run node browser-debug/wt.mjs to log in and re-export "
                    "wt-session.env"
                ),
            )
        self.base_url = base_url
        self._csrf_cache: str | None = None

    def _default_headers(self, path: str) -> dict[str, str]:
        referer = f"{self.base_url}/en/trader/grid_bots"
        if path.startswith("/en/trader/"):
            referer = f"{self.base_url}{path.split('?', 1)[0]}"
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
            "Origin": self.base_url,
            "Cache-Control": "no-cache",
        }

    def fetch_csrf_token(self) -> str:
        """Extract ``appCsrfToken`` from a cabinet page (memoized)."""
        if self._csrf_cache:
            return self._csrf_cache
        for path in (
            "/en/trader/grid_bots",
            "/en/trader/dashboard",
            "/en/trader/grid_bots/upsert",
        ):
            url = f"{self.base_url}{path}"
            try:
                resp = httpx_request(
                    "GET",
                    url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml",
                        "User-Agent": self.user_agent,
                    },
                    cookies=self.cookies,
                    timeout=self.timeout,
                )
            except Exception:
                continue
            if resp.status_code != 200:
                continue
            for pattern in _CSRF_PATTERNS:
                match = pattern.search(resp.text)
                if match:
                    token = match.group(1)
                    self._csrf_cache = token
                    return token
        raise WunConfigError(
            "could not extract CSRF token — session cookies stale or Cloudflare active",
            remediation="re-login via node browser-debug/wt.mjs and re-export wt-session.env",
        )

    def invalidate_csrf(self) -> None:
        self._csrf_cache = None

    def prepare(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
    ) -> tuple[str, dict[str, str], str]:
        """Build ``(url, headers, body_str)`` without sending.

        Non-safe methods include the cached CSRF token when one is already
        known; callers that need to actually send should call
        :meth:`fetch_csrf_token` first (done automatically by ``request``).
        """
        url = normalize_url(path, self.base_url)
        request_headers = self._default_headers(path)
        if method.upper() not in ("GET", "HEAD") and self._csrf_cache:
            request_headers["X-W-CSRF-Token"] = self._csrf_cache
        body_str = serialize_body(body)
        if body_str:
            request_headers["Content-Type"] = "application/json"
        return url, request_headers, body_str

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        curl: bool = False,
    ) -> Response:
        """Send a session-auth request.

        Non-safe methods automatically get the ``X-W-CSRF-Token`` header.
        """
        if method.upper() not in ("GET", "HEAD"):
            self.fetch_csrf_token()
        url, request_headers, body_str = self.prepare(method, path, body=body)
        if headers:
            request_headers.update(headers)
        content = body_str.encode("utf-8") if body_str else None

        if curl:
            print(
                "# curl equivalent (session):\n"
                + curl_command(method, url, headers=request_headers, cookies=self.cookies, body=body_str),
                file=sys.stderr,
            )

        resp = httpx_request(
            method,
            url,
            headers=request_headers,
            content=content,
            cookies=self.cookies,
            timeout=self.timeout,
        )
        if resp.status_code == 403 and "Invalid CSRF" in resp.text:
            self.invalidate_csrf()
            raise WunCsrfError(
                "Invalid CSRF token — token rotated",
                status_code=resp.status_code,
                url=url,
                response_text=resp.text[:300],
                remediation="retry; the transport will refetch the token",
            )
        if resp.status_code == 403 and "Just a moment" in resp.text:
            raise WunCloudflareError(
                "session surface is Cloudflare-gated; raw httpx was fingerprinted",
                status_code=resp.status_code,
                url=url,
                response_text=resp.text[:300],
                remediation=(
                    "refresh cf_clearance via node browser-debug/wt.mjs and retry, "
                    "or use BrowserTransport"
                ),
            )
        return resp


__all__ = ["SessionTransport"]
