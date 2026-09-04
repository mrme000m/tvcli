"""One facade for every WunderTrading surface."""
from __future__ import annotations

from functools import cached_property
from typing import Any

from ..secrets import Secrets, load_secrets
from ..transport.browser import BrowserTransport
from ..transport.hmac import OpenApiTransport
from ..transport.market import MarketTransport
from ..transport.mcp import McpTransport
from ..transport.session import SessionTransport
from .grid import GridClient
from .market import MarketDataClient
from .mcp import McpClient
from .open_api import OpenApiClient


class WunderTrading:
    """Facade that wires credentials into typed clients for each surface.

    Example:
        >>> from wtclient import WunderTrading
        >>> wun = WunderTrading()
        >>> wun.rest.exchanges()
        >>> wun.mcp.supported_exchanges()

    Grid/market surfaces are created lazily so a caller that only has API keys
    (and no session cookies) can still use the HMAC + MCP surfaces.

    Args:
        secrets: Preloaded :class:`Secrets` (defaults to the provisioned files).
        browser: Use the headful CloakBrowser transport for grid/market.
        cdp_base: CDP endpoint when ``browser=True``.
    """

    def __init__(
        self,
        secrets: Secrets | None = None,
        *,
        browser: bool = False,
        cdp_base: str = "http://127.0.0.1:9222",
    ) -> None:
        self.secrets = secrets or load_secrets()
        self.use_browser = browser
        self.cdp_base = cdp_base

    @cached_property
    def rest(self) -> OpenApiClient:
        key, secret = self.secrets.require_api_keys()
        return OpenApiClient(OpenApiTransport(key, secret))

    @cached_property
    def mcp(self) -> McpClient:
        key, secret = self.secrets.require_api_keys()
        return McpClient(McpTransport(key, secret))

    def _grid_transport(self):
        if self.use_browser:
            return BrowserTransport(cdp_base=self.cdp_base)
        return SessionTransport(self.secrets.require_session())

    @cached_property
    def grid(self) -> GridClient:
        transport = self._grid_transport()
        market = transport if isinstance(transport, BrowserTransport) else MarketTransport()
        return GridClient(transport, market=market)

    @cached_property
    def market(self) -> MarketDataClient:
        if self.use_browser:
            return MarketDataClient(BrowserTransport(cdp_base=self.cdp_base))
        return MarketDataClient(MarketTransport())

    def close(self) -> None:
        for name in ("rest", "mcp", "grid", "market"):
            client = self.__dict__.get(name)
            if client is not None:
                client.transport.close()

    def __enter__(self) -> "WunderTrading":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


__all__ = ["WunderTrading"]
