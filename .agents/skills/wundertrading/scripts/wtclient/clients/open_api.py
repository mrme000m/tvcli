"""Typed client for the HMAC ``/open_api`` REST surface."""
from __future__ import annotations

from typing import Any

from ..models.trade import EditTradeStrategy, PlaceStrategyTrade
from ..query import append_query
from ..transport.hmac import OpenApiTransport
from .base import BaseClient


class OpenApiClient(BaseClient):
    """HMAC-authenticated REST client (no browser, no MCP client).

    Query filters mirror the MCP tool arguments; list values are encoded as
    comma-separated strings as the server expects.
    """

    def __init__(self, transport: OpenApiTransport | None = None, **kwargs: Any) -> None:
        super().__init__(transport or OpenApiTransport(**kwargs))

    @property
    def hmac(self) -> OpenApiTransport:
        return self.transport  # type: ignore[return-value]

    # -- raw -------------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: dict[str, Any] | None = None,
        curl: bool = False,
    ) -> Any:
        """Send a raw signed request and return the parsed JSON body."""
        response = self.hmac.request(method, path, body=body, params=params, curl=curl)
        response.raise_for_status()
        return self._json(response)

    # -- read endpoints ---------------------------------------------------
    def exchanges(self) -> Any:
        """List supported exchanges."""
        return self.request("GET", "/open_api/exchanges")

    def markets(self, exchanges: list[str] | tuple[str, ...] | str) -> Any:
        """Markets for one or more exchange codes."""
        if isinstance(exchanges, str):
            exchanges = [exchanges]
        return self.request("GET", "/open_api/markets", params={"exchanges": exchanges})

    def api_profiles(
        self,
        *,
        limit: int | None = None,
        page: int | None = None,
        exchanges: list[str] | None = None,
        api_profiles: list[str] | None = None,
        statuses: list[str] | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if page is not None:
            params["page"] = page
        if exchanges:
            params["exchanges"] = exchanges
        if api_profiles:
            params["apiProfiles"] = api_profiles
        if statuses:
            params["statuses"] = statuses
        return self.request("GET", "/open_api/api_profiles", params=params)

    def live_strategies(
        self,
        *,
        limit: int | None = None,
        page: int | None = None,
        exchanges: list[str] | None = None,
        api_profiles: list[str] | None = None,
        statuses: list[str] | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if page is not None:
            params["page"] = page
        if exchanges:
            params["exchanges"] = exchanges
        if api_profiles:
            params["apiProfiles"] = api_profiles
        if statuses:
            params["statuses"] = statuses
        return self.request("GET", "/open_api/strategies/live", params=params)

    def strategies_history(
        self,
        *,
        limit: int | None = None,
        page: int | None = None,
        exchanges: list[str] | None = None,
        api_profiles: list[str] | None = None,
        statuses: list[str] | None = None,
    ) -> Any:
        """Strategy history.

        Note: the equivalent MCP tool is currently broken server-side; prefer
        ``export_strategies_history`` on the MCP surface.
        """
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if page is not None:
            params["page"] = page
        if exchanges:
            params["exchanges"] = exchanges
        if api_profiles:
            params["apiProfiles"] = api_profiles
        if statuses:
            params["statuses"] = statuses
        return self.request("GET", "/open_api/strategies/history", params=params)

    def strategy(self, strategy_id: str) -> Any:
        return self.request("GET", f"/open_api/strategies/{strategy_id}")

    def strategy_orders(self, strategy_id: str) -> Any:
        return self.request("GET", f"/open_api/strategies/{strategy_id}/orders")

    # -- write endpoints --------------------------------------------------
    def trade(self, payload: PlaceStrategyTrade | dict[str, Any]) -> Any:
        """Place a strategy trade (opens a real position — confirm first)."""
        body = payload.payload() if isinstance(payload, PlaceStrategyTrade) else payload
        return self.request("POST", "/open_api/strategies/trade", body=body)

    def edit_trade(self, payload: EditTradeStrategy | dict[str, Any]) -> Any:
        """Edit a live strategy (PATCH)."""
        body = payload.payload() if isinstance(payload, EditTradeStrategy) else payload
        return self.request("PATCH", "/open_api/strategies/trade", body=body)

    def market_enter(self, strategy_id: str) -> Any:
        """Force a LIMIT strategy's entry at market now."""
        return self.request("PUT", f"/open_api/strategies/{strategy_id}/market_enter")

    def swing(self, strategy_id: str, *, client_id: str | None = None) -> Any:
        """Flip an entered futures strategy to swing."""
        body = {"clientId": client_id} if client_id else None
        return self.request("POST", f"/open_api/strategies/{strategy_id}/swing", body=body)

    def cancel(self, strategy_id: str) -> Any:
        """Cancel a strategy."""
        return self.request("DELETE", f"/open_api/strategies/{strategy_id}/cancel")

    def market_close(self, strategy_id: str) -> Any:
        """Close the position at market immediately."""
        return self.request("DELETE", f"/open_api/strategies/{strategy_id}/market_close")


__all__ = ["OpenApiClient"]
