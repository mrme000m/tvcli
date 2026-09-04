"""Typed exceptions for the WunderTrading client framework.

Every exception raised by :mod:`wtclient` derives from :class:`WunError`.
Callers can therefore catch one base class and still distinguish the concrete
failure with ``isinstance`` when they need to react differently.
"""
from __future__ import annotations

from typing import Any


class WunError(Exception):
    """Base class for every wtclient error.

    Attributes:
        message: Human-readable description of what failed.
        remediation: Optional, concrete next step a caller or user can take.
        cause: Optional underlying exception.
    """

    def __init__(
        self,
        message: str,
        *,
        remediation: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.remediation = remediation
        self.cause = cause

    def __str__(self) -> str:
        if self.remediation:
            return f"{self.message}\nRemediation: {self.remediation}"
        return self.message


class WunConfigError(WunError):
    """Missing, malformed, or unusable configuration/credentials."""


class WunValidationError(WunError):
    """A request payload failed client-side validation."""


class WunTransportError(WunError):
    """Network/transport failure (DNS, TLS, timeout, connection reset)."""


class WunApiError(WunError):
    """A WunderTrading API returned a non-success response.

    Attributes:
        status_code: HTTP status code (or 0 when unknown).
        url: Request URL.
        response_text: Response body preview (already truncated).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
        response_text: str | None = None,
        remediation: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, remediation=remediation, cause=cause)
        self.status_code = status_code
        self.url = url
        self.response_text = response_text


class WunAuthError(WunApiError):
    """Authentication failure (bad key, expired key, invalid signature)."""


class WunCloudflareError(WunApiError):
    """Cloudflare challenged a raw HTTP request.

    Raw :mod:`httpx` was fingerprinted and got ``403 Just a moment``. The
    remediation almost always points at refreshing ``cf_clearance`` through a
    real browser session or using the browser transport.
    """


class WunCsrfError(WunApiError):
    """A session request was rejected with ``Invalid CSRF token``."""


class WunRateLimitError(WunApiError):
    """A rate limit was reached (HTTP 429 or remaining budget exhausted)."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


__all__ = [
    "WunError",
    "WunConfigError",
    "WunValidationError",
    "WunTransportError",
    "WunApiError",
    "WunAuthError",
    "WunCloudflareError",
    "WunCsrfError",
    "WunRateLimitError",
]
