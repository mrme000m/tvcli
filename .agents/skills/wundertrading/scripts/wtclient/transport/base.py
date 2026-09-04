"""Shared transport primitives.

Every transport returns the same :class:`wtclient.response.Response` object so
callers are insulated from the underlying HTTP/WebSocket details.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx

from ..config import DEFAULT_TIMEOUT, DEFAULT_USER_AGENT
from ..errors import WunTransportError
from ..response import Response


def httpx_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
    cookies: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    follow_redirects: bool = True,
    http2: bool = False,
) -> Response:
    """Perform one ``httpx`` request and normalize the result.

    This is the single place where raw ``httpx`` is exercised, so retry /
    instrumentation policy can be added here later without touching each
    transport.
    """
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=follow_redirects,
            http2=http2,
        ) as client:
            resp = client.request(
                method.upper(),
                url,
                headers=headers,
                content=content,
                cookies=cookies,
            )
    except httpx.HTTPError as exc:
        raise WunTransportError(
            f"{method.upper()} {url} failed: {exc.__class__.__name__}: {exc}",
            cause=exc,
        ) from exc
    return Response.from_httpx(resp, method=method, url=url)


class BaseTransport(ABC):
    """Abstract transport interface.

    Subclasses implement :meth:`request` for one WunderTrading surface.
    """

    name: str = "base"

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.timeout = timeout
        self.user_agent = user_agent

    @abstractmethod
    def request(
        self,
        method: str,
        url: str,
        *,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """Send a request and return a normalized Response."""

    def close(self) -> None:  # noqa: D401 - abstract base default
        """Release transport resources (no-op by default)."""

    def __enter__(self) -> "BaseTransport":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


__all__ = ["BaseTransport", "httpx_request"]
