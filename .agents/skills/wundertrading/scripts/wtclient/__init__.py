"""wtclient — a modular, validated, type-safe Python client for WunderTrading.

Surfaces covered:

- ``/open_api``  HMAC-SHA256 REST (no browser)
- ``:2083/mcp``  MCP streamable HTTP (no browser)
- ``/en/trader`` session web surface, incl. Grid bots and my-exchanges
  profile management (raw httpx replay **or** optional headful-browser CDP
  transport)
- ``:2087``      public market data (raw httpx **or** browser transport)

Discovery + debug machinery is baked in:

- :class:`wtclient.discovery.Recorder` wraps any transport and captures every
  request/response into a queryable :class:`wtclient.discovery.EndpointCatalog`.
- :class:`wtclient.discovery.Probe` tries an arbitrary ``(method, path)`` across
  all surfaces and reports the first one that answered 2xx.
- :mod:`wtclient.debug` installs a redacted stderr logger + dump-on-failure
  (auto-enabled when ``WT_DEBUG=1``). Call :func:`wtclient.debug.trace(wun)` to
  instrument a :class:`WunderTrading` instance end-to-end.

Typical use::

    from wtclient import WunderTrading

    wun = WunderTrading()                    # reads provisioned secrets
    exchanges = wun.rest.exchanges()         # HMAC REST
    profiles  = wun.mcp.api_profiles(limit=5)
    wun.grid.list()                          # needs session cookies (or browser=True)

    wun = WunderTrading(browser=True)        # reliable for Cloudflare surfaces
    wun.grid.list()

    # Debug / discovery (zero-cost when not used):
    import wtclient.debug as dbg
    rec = dbg.trace(wun)                     # wrap every transport
    catalog = rec.catalog()                  # grouped by surface/method/path

Everything raises :class:`wtclient.errors.WunError` subclasses.
"""
from ._version import __version__
from .debug import DebugDump, DebugState, STATE, dump_failure, install, log_request, trace, unwrap
from .discovery import (
    PUBLIC_SURFACES,
    EndpointCatalog,
    EndpointSummary,
    Probe,
    ProbeResult,
    Recorder,
    RecordedRequest,
    RecordedTransport,
    surface_index,
)
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
from .clients.bots import BotsClient
from .clients.client import WunderTrading
from .clients.exchanges import ExchangesClient
from .clients.open_api import OpenApiClient
from .clients.mcp import McpClient
from .clients.grid import GridClient
from .clients.market import MarketDataClient
from .transport.browser import BrowserTransport
from .transport.hmac import OpenApiTransport
from .transport.mcp import McpTransport
from .models.profiles import Profile
from .transport.session import SessionTransport
from .transport.market import MarketTransport

__all__ = [
    "__version__",
    "WunderTrading",
    "OpenApiClient",
    "McpClient",
    "GridClient",
    "BotsClient",
    "ExchangesClient",
    "Profile",
    "MarketDataClient",
    "OpenApiTransport",
    "McpTransport",
    "SessionTransport",
    "MarketTransport",
    "BrowserTransport",
    "Recorder",
    "RecordedRequest",
    "RecordedTransport",
    "EndpointCatalog",
    "EndpointSummary",
    "Probe",
    "ProbeResult",
    "surface_index",
    "PUBLIC_SURFACES",
    "DebugState",
    "DebugDump",
    "STATE",
    "install",
    "log_request",
    "dump_failure",
    "trace",
    "unwrap",
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
