"""Shared client plumbing."""
from __future__ import annotations

from typing import Any

from ..errors import WunApiError
from ..response import Response
from ..transport.base import BaseTransport


class BaseClient:
    """Thin base class that binds a transport and standardizes JSON handling."""

    def __init__(self, transport: BaseTransport) -> None:
        self.transport = transport

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> "BaseClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _json(self, response: Response, *, what: str = "response") -> Any:
        """Parse JSON or raise a typed API error."""
        try:
            return response.json()
        except WunApiError:
            response.raise_for_status()
            raise

    def _get_json(
        self,
        method: str,
        url: str,
        *,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = self.transport.request(method, url, body=body, headers=headers)
        response.raise_for_status()
        return self._json(response)


__all__ = ["BaseClient"]
