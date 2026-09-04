"""A small, typed HTTP response wrapper shared by every transport."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .errors import (
    WunApiError,
    WunCloudflareError,
    WunCsrfError,
    WunRateLimitError,
)

CLOUDFLARE_MARKERS = ("Just a moment", "cf-chl", "Attention Required")


@dataclass
class Response:
    """Normalized HTTP response returned by all transports.

    ``json()`` is lazy and memoized; ``raise_for_status()`` maps the raw
    failure to the appropriate :mod:`wtclient.errors` exception.
    """

    status_code: int
    headers: dict[str, str]
    text: str
    url: str = ""
    method: str = ""
    _json: Any = field(default=None, repr=False)
    _json_parsed: bool = field(default=False, repr=False)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        """Parse and return the JSON body (memoized)."""
        if not self._json_parsed:
            try:
                self._json = json.loads(self.text)
            except json.JSONDecodeError as exc:
                raise WunApiError(
                    f"response is not JSON (status {self.status_code})",
                    status_code=self.status_code,
                    url=self.url,
                    response_text=self.text[:300],
                    cause=exc,
                ) from exc
            self._json_parsed = True
        return self._json

    def rate_limit_remaining(self) -> int | None:
        """Return ``RateLimit-Remaining`` from headers, or ``None``."""
        raw = self.headers.get("ratelimit-remaining")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    def raise_for_status(self) -> "Response":
        """Raise a typed exception when the response is not a success."""
        if self.ok:
            return self

        if self.status_code == 403 and any(m in self.text for m in CLOUDFLARE_MARKERS):
            raise WunCloudflareError(
                "Cloudflare challenge (403 'Just a moment') — raw HTTP was fingerprinted",
                status_code=self.status_code,
                url=self.url,
                response_text=self.text[:300],
                remediation=(
                    "refresh cf_clearance with a real browser session and retry, "
                    "or use the browser transport (wtclient BrowserTransport)"
                ),
            )
        if self.status_code == 403 and "Invalid CSRF" in self.text:
            raise WunCsrfError(
                "Invalid CSRF token — the session token rotated",
                status_code=self.status_code,
                url=self.url,
                response_text=self.text[:300],
                remediation="retry; the session transport will refetch the CSRF token",
            )
        if self.status_code == 429:
            retry_after: float | None = None
            raw = self.headers.get("retry-after")
            if raw is not None:
                try:
                    retry_after = float(raw)
                except (TypeError, ValueError):
                    retry_after = None
            raise WunRateLimitError(
                "rate limit reached",
                status_code=self.status_code,
                url=self.url,
                response_text=self.text[:300],
                retry_after=retry_after,
            )
        raise WunApiError(
            f"HTTP {self.status_code}",
            status_code=self.status_code,
            url=self.url,
            response_text=self.text[:300],
        )

    @classmethod
    def from_httpx(cls, resp: Any, *, method: str = "", url: str = "") -> "Response":
        """Build a Response from an ``httpx.Response``."""
        return cls(
            status_code=resp.status_code,
            headers={k.lower(): v for k, v in resp.headers.items()},
            text=resp.text,
            url=str(resp.url) or url,
            method=method,
        )


__all__ = ["Response"]
