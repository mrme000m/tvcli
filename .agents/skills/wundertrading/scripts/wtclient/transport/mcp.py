"""MCP streamable-HTTP transport (sessionless JSON-RPC ``tools/call``)."""
from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any

from ..config import DEFAULT_TIMEOUT, MCP_URL
from ..curl import curl_command
from ..errors import WunApiError, WunConfigError, WunTransportError
from ..response import Response
from .base import BaseTransport, httpx_request


def _strip_sse(text: str) -> str:
    """Extract the JSON payload from an SSE ``text/event-stream`` body."""
    for line in text.splitlines():
        if line.startswith("data:"):
            return line[len("data:"):].strip()
    return text.strip()


class McpTransport(BaseTransport):
    """Transport for WunderTrading's MCP streamable HTTP endpoint.

    Uses the same ``X-API-Key`` / ``X-Secret-Key`` header auth as the HMAC
    surface (no HMAC signing, no browser, no Cloudflare issue).
    """

    name = "mcp"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        url: str = MCP_URL,
        timeout: float = 40.0,
        user_agent: str | None = None,
    ) -> None:
        if not api_key or not api_secret:
            raise WunConfigError(
                "McpTransport requires both api_key and api_secret",
                remediation="export WUN_API_KEY / WUN_SECRET_KEY",
            )
        super().__init__(timeout=timeout, user_agent=user_agent or "wtclient")
        self.api_key = api_key
        self.api_secret = api_secret
        self.url = url
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "X-API-Key": self.api_key,
            "X-Secret-Key": self.api_secret,
            "User-Agent": self.user_agent,
        }

    def request(
        self,
        method: str,
        url: str,
        *,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        """Send a raw JSON-RPC payload (mostly for parity/debugging)."""
        merged = self._headers()
        if headers:
            merged.update(headers)
        content = None
        if body is not None:
            content = json.dumps(body, separators=(",", ":")).encode("utf-8")
            merged.setdefault("Content-Type", "application/json")
        return httpx_request(
            method,
            url,
            headers=merged,
            content=content,
            timeout=self.timeout,
        )

    def call_tool(self, tool: str, arguments: dict[str, Any], *, curl: bool = False) -> Any:
        """Call one MCP tool and return its parsed ``result`` payload.

        Raises:
            WunApiError: On a JSON-RPC error response or an unparseable body.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        body_str = json.dumps(payload, separators=(",", ":"))
        headers = self._headers()
        if curl:
            print(
                "# curl equivalent (mcp):\n"
                + curl_command("POST", self.url, headers=headers, body=body_str),
                file=sys.stderr,
            )
        resp = httpx_request(
            "POST",
            self.url,
            headers=headers,
            content=body_str.encode("utf-8"),
            timeout=self.timeout,
        )
        try:
            data = json.loads(_strip_sse(resp.text))
        except json.JSONDecodeError as exc:
            raise WunApiError(
                f"MCP returned an unparseable body (status {resp.status_code})",
                status_code=resp.status_code,
                url=self.url,
                response_text=resp.text[:300],
                cause=exc,
            ) from exc
        if "error" in data and data.get("error"):
            raise WunApiError(
                f"MCP tool {tool!r} error: {data['error']}",
                status_code=resp.status_code,
                url=self.url,
                response_text=json.dumps(data["error"])[:300],
            )
        result = data.get("result")
        if not isinstance(result, dict):
            return result
        if result.get("isError"):
            raise WunApiError(
                f"MCP tool {tool!r} returned isError",
                status_code=resp.status_code,
                url=self.url,
                response_text=json.dumps(result)[:300],
            )
        # Most tools return result.content[0].text = JSON string.
        content = result.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            text = content[0].get("text")
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
        return result


__all__ = ["McpTransport"]
