"""wtclient — a modular, validated, type-safe Python client for WunderTrading.

Surfaces covered:

- ``/open_api``  HMAC-SHA256 REST (no browser)
- ``:2083/mcp``  MCP streamable HTTP (no browser)
- ``/en/trader`` session web surface, incl. Grid bots (raw httpx replay **or**
  optional headful-browser CDP transport)
- ``:2087``      public market data (raw httpx **or** browser transport)

Typical use::

    from wtclient import WunderTrading

    wun = WunderTrading()                    # reads provisioned secrets
    exchanges = wun.rest.exchanges()         # HMAC REST
    profiles  = wun.mcp.api_profiles(limit=5)
    wun.grid.list()                          # needs session cookies (or browser=True)

    wun = WunderTrading(browser=True)        # reliable for Cloudflare surfaces
    wun.grid.list()

Everything raises :class:`wtclient.errors.WunError` subclasses.
"""
from ._version import __version__
from .errors import (
    WunApiError,
    WunAuthError,
    WunCloudflareError,
    WunConfigError,
    WunCsrfError,
    WunError,
    WunRateLimitError,
    WunTransportError,
    WunValidationError,
)
from .secrets import Secrets, load_secrets
from .clients.client import WunderTrading
from .clients.open_api import OpenApiClient
from .clients.mcp import McpClient
from .clients.grid import GridClient
from .clients.market import MarketDataClient
from .transport.browser import BrowserTransport
from .transport.hmac import OpenApiTransport
from .transport.mcp import McpTransport
from .transport.session import SessionTransport
from .transport.market import MarketTransport

__all__ = [
    "__version__",
    "WunderTrading",
    "OpenApiClient",
    "McpClient",
    "GridClient",
    "MarketDataClient",
    "OpenApiTransport",
    "McpTransport",
    "SessionTransport",
    "MarketTransport",
    "BrowserTransport",
    "Secrets",
    "load_secrets",
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
