"""Public ``:2087`` market-data transport (no auth)."""
from __future__ import annotations

import sys
from typing import Any

from ..config import DEFAULT_TIMEOUT, MARKET_ORIGIN
from ..curl import curl_command
from ..errors import WunCloudflareError
from ..query import normalize_url
from ..response import Response
from .base import BaseTransport, httpx_request


class MarketTransport(BaseTransport):
    """GET transport for the public ``wundertrading.com:2087`` origin.

    Live note (2026-09-04): this origin is Cloudflare-managed; raw
    httpx/curl often gets ``403 Just a moment`` without a fresh
    ``cf_clearance``. The browser transport works reliably for the same URLs.
    """

    name = "market"

    def __init__(
        self,
        *,
        base_url: str = MARKET_ORIGIN,
        timeout: float = 20.0,
        user_agent: str | None = None,
    ) -> None:
        super().__init__(timeout=timeout, user_agent=user_agent or "wtclient")
        self.base_url = base_url

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        headers: dict[str, str] | None = None,
        curl: bool = False,
    ) -> Response:
        url = normalize_url(path, self.base_url)
        request_headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        if headers:
            request_headers.update(headers)
        if curl:
            print(
                "# curl equivalent (market):\n"
                + curl_command(method, url, headers=request_headers),
                file=sys.stderr,
            )
        resp = httpx_request(
            method,
            url,
            headers=request_headers,
            timeout=self.timeout,
        )
        if resp.status_code == 403 and "Just a moment" in resp.text:
            raise WunCloudflareError(
                "market :2087 is Cloudflare-gated (403 'Just a moment')",
                status_code=resp.status_code,
                url=url,
                response_text=resp.text[:300],
                remediation=(
                    "use exchange public APIs (market_regime.py) or the "
                    "browser transport for :2087 fetches"
                ),
            )
        return resp


__all__ = ["MarketTransport"]
