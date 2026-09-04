"""Typed client for the public ``:2087`` market-data surface."""
from __future__ import annotations

from typing import Any

from ..config import MARKET_ORIGIN
from ..query import append_query
from ..response import Response
from ..transport.base import BaseTransport
from ..transport.browser import BrowserTransport
from ..transport.market import MarketTransport
from .base import BaseClient


class MarketDataClient(BaseClient):
    """Public WunderTrading market data (``:2087``).

    Can be backed by :class:`MarketTransport` (raw httpx, often Cloudflare
    403) or :class:`BrowserTransport` (reliable, uses the browser TLS
    fingerprint).
    """

    def __init__(self, transport: BaseTransport | None = None, **kwargs: Any) -> None:
        super().__init__(transport or MarketTransport(**kwargs))

    def _get(self, url: str) -> Any:
        if isinstance(self.transport, BrowserTransport):
            full = url if url.startswith(("http://", "https://")) else f"{MARKET_ORIGIN}{url}"
            response = self.transport.fetch_market(full)
        else:
            response = self.transport.request("GET", url)
        response.raise_for_status()
        return self._json(response)

    def all_markets(self, *, market: str | None = None, market_expiry_group: str | None = None) -> Any:
        path = "/all-markets"
        params: dict[str, Any] = {}
        if market:
            params["market"] = market
        if market_expiry_group:
            params["marketExpiryGroup"] = market_expiry_group
        url = append_query(path, params)
        return self._get(url)

    def market(self, market_code: str) -> Any:
        if not market_code:
            raise ValueError("market_code is required")
        return self._get(append_query("/market", {"marketCode": market_code}))

    def ohlc_last(self, code: str, timeframe: int = 15) -> Any:
        return self._get(append_query("/ohlc/last", {"code": code, "timeframe": timeframe}))

    def ohlc(
        self,
        code: str,
        *,
        timeframe: int = 15,
        limit: int = 2976,
        from_ms: int | None = None,
    ) -> Any:
        params: dict[str, Any] = {"code": code, "timeframe": timeframe, "limit": limit}
        if from_ms is not None:
            params["from"] = from_ms
        return self._get(append_query("/ohlc", params))

    def ohlc_low_high(self, code: str, *, timeframe: int = 15, limit: int = 2976) -> Any:
        return self._get(append_query("/ohlc/low-high", {"code": code, "timeframe": timeframe, "limit": limit}))

    def supported_markets(self) -> Any:
        return self._get("/supported-markets")


__all__ = ["MarketDataClient"]
