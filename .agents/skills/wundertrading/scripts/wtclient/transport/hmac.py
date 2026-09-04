"""HMAC-signed ``/open_api`` transport (pure httpx, no browser)."""
from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

from ..config import DEFAULT_RECV_WINDOW, DEFAULT_TIMEOUT, WT_ORIGIN, now_ms
from ..curl import curl_command
from ..errors import WunConfigError
from ..query import append_query, serialize_body
from ..response import Response
from .base import BaseTransport, httpx_request


def sign_payload(
    secret: str,
    method: str,
    path: str,
    timestamp_ms: str | int,
    recv_window: str,
    body: str,
) -> str:
    """Compute the Base64 HMAC-SHA256 signature for an ``/open_api`` request.

    Payload order is fixed and must match the server byte-for-byte:
    ``METHOD\\nPATH\\nTIMESTAMP\\nRECV_WINDOW\\nBODY``.
    """
    payload = "\n".join(
        [method.upper(), path, str(timestamp_ms), str(recv_window), body or ""]
    )
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


class OpenApiTransport(BaseTransport):
    """Transport for the HMAC-authenticated REST surface.

    Args:
        api_key: Cabinet API key (same pair as MCP).
        api_secret: Cabinet API secret (signing only; never sent).
        recv_window: Request validity window in milliseconds. Must equal the
            value signed into the payload and the ``X-Recv-Window`` header.
        base_url: Origin, override for tests/proxies.
    """

    name = "open_api"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        recv_window: str = DEFAULT_RECV_WINDOW,
        base_url: str = WT_ORIGIN,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str | None = None,
    ) -> None:
        if not api_key or not api_secret:
            raise WunConfigError(
                "OpenApiTransport requires both api_key and api_secret",
                remediation="export WUN_API_KEY / WUN_SECRET_KEY",
            )
        super().__init__(timeout=timeout, user_agent=user_agent or WT_ORIGIN)
        self.api_key = api_key
        self.api_secret = api_secret
        self.recv_window = recv_window
        self.base_url = base_url

    def sign(
        self,
        method: str,
        path: str,
        timestamp_ms: str | int,
        recv_window: str,
        body: str,
    ) -> str:
        return sign_payload(self.api_secret, method, path, timestamp_ms, recv_window, body)

    def prepare(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: Mapping[str, Any] | None = None,
    ) -> tuple[str, dict[str, str], str]:
        """Build ``(url, headers, body_str)`` for a request without sending it."""
        path = append_query(path, params)
        body_str = serialize_body(body)
        timestamp = str(now_ms())
        signature = self.sign(method, path, timestamp, self.recv_window, body_str)
        request_headers: dict[str, str] = {
            "X-API-Key": self.api_key,
            "X-Signature": signature,
            "X-Timestamp": timestamp,
            "X-Recv-Window": self.recv_window,
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        if body_str:
            request_headers["Content-Type"] = "application/json"
        url = f"{self.base_url}{path}"
        return url, request_headers, body_str

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        curl: bool = False,
    ) -> Response:
        """Send an HMAC-signed request.

        Args:
            method: HTTP method.
            path: Path **including any literal query string** (signed exactly).
            body: JSON-serializable body (or pre-serialized string).
            headers: Extra headers merged over the signed defaults.
            params: Optional query params appended to ``path`` before signing.
            curl: Print a redacted curl equivalent to stderr (and still send).
        """
        url, request_headers, body_str = self.prepare(method, path, body=body, params=params)
        if headers:
            request_headers.update(headers)
        if curl:
            print(
                "# curl equivalent (open_api):\n"
                + curl_command(method, url, headers=request_headers, body=body_str),
                file=__import__("sys").stderr,
            )
        content = body_str.encode("utf-8") if body_str else None
        return httpx_request(
            method,
            url,
            headers=request_headers,
            content=content,
            timeout=self.timeout,
        )


__all__ = ["OpenApiTransport", "sign_payload"]
