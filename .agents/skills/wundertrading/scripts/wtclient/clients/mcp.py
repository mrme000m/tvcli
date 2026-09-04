"""Typed client for the 15 WunderTrading MCP tools."""
from __future__ import annotations

from typing import Any

from ..errors import WunValidationError
from ..models.trade import EditTradeStrategy, PlaceStrategyTrade
from ..transport.mcp import McpTransport
from .base import BaseClient


def _comma(values: list[str] | tuple[str, ...] | str | None) -> str | None:
    if values is None:
        return None
    if isinstance(values, str):
        return values
    return ",".join(values)


class McpClient(BaseClient):
    """MCP streamable-HTTP client (same keys as REST, no browser).

    Read tools are ``get_*``/``export_*``; ``place_*``/``edit_*``/``cancel_*``/
    ``close_*`` mutate live state and must be called only after the Phase E
    checklist in the skill.
    """

    def __init__(self, transport: McpTransport | None = None, **kwargs: Any) -> None:
        super().__init__(transport or McpTransport(**kwargs))

    @property
    def mcp(self) -> McpTransport:
        return self.transport  # type: ignore[return-value]

    def call(self, tool: str, arguments: dict[str, Any] | None = None, *, curl: bool = False) -> Any:
        """Call any MCP tool by name with raw arguments."""
        return self.mcp.call_tool(tool, arguments or {}, curl=curl)

    # -- read tools -------------------------------------------------------
    def supported_exchanges(self) -> Any:
        return self.call("get_supported_exchanges")

    def exchange_markets(self, exchanges: list[str] | tuple[str, ...]) -> Any:
        if not exchanges:
            raise WunValidationError("exchange_markets requires at least one exchange code")
        return self.call("get_exchange_markets", {"exchanges": list(exchanges)})

    def api_profiles(
        self,
        *,
        limit: int | None = None,
        page: int | None = None,
        exchanges: list[str] | None = None,
        api_profiles: list[str] | None = None,
        statuses: list[str] | None = None,
    ) -> Any:
        args: dict[str, Any] = {}
        if limit is not None:
            args["limit"] = limit
        if page is not None:
            args["page"] = page
        if exchanges:
            args["exchanges"] = exchanges
        if api_profiles:
            args["apiProfiles"] = api_profiles
        if statuses:
            args["statuses"] = statuses
        return self.call("get_api_profiles", args)

    def live_strategies(
        self,
        *,
        limit: int | None = None,
        page: int | None = None,
        exchanges: list[str] | str | None = None,
        api_profiles: list[str] | str | None = None,
        statuses: list[str] | str | None = None,
    ) -> Any:
        args: dict[str, Any] = {}
        if limit is not None:
            args["limit"] = limit
        if page is not None:
            args["page"] = page
        for key, value in (
            ("exchanges", _comma(exchanges)),
            ("apiProfiles", _comma(api_profiles)),
            ("statuses", _comma(statuses)),
        ):
            if value is not None:
                args[key] = value
        return self.call("get_live_strategies", args)

    def strategies_history(
        self,
        *,
        limit: int | None = None,
        page: int | None = None,
        exchanges: list[str] | None = None,
        api_profiles: list[str] | None = None,
        statuses: list[str] | None = None,
    ) -> Any:
        """Note: currently broken server-side; prefer :meth:`export_strategies_history`."""
        args: dict[str, Any] = {}
        if limit is not None:
            args["limit"] = limit
        if page is not None:
            args["page"] = page
        if exchanges:
            args["exchanges"] = exchanges
        if api_profiles:
            args["apiProfiles"] = api_profiles
        if statuses:
            args["statuses"] = statuses
        return self.call("get_strategies_history", args)

    def export_strategies_history(
        self,
        *,
        exchanges: list[str] | None = None,
        api_profiles: list[str] | None = None,
        statuses: list[str] | None = None,
    ) -> Any:
        args: dict[str, Any] = {}
        if exchanges:
            args["exchanges"] = exchanges
        if api_profiles:
            args["apiProfiles"] = api_profiles
        if statuses:
            args["statuses"] = statuses
        return self.call("export_strategies_history", args)

    def get_strategy(self, strategy_id: str) -> Any:
        if not strategy_id:
            raise WunValidationError("get_strategy requires a strategy id or clientId")
        return self.call("get_strategy", {"id": strategy_id})

    def get_strategy_orders_history(
        self,
        profile_strategy_id: str,
        *,
        limit: int | None = None,
        page: int | None = None,
    ) -> Any:
        if not profile_strategy_id:
            raise WunValidationError("get_strategy_orders_history requires profileStrategyId")
        args: dict[str, Any] = {"profileStrategyId": profile_strategy_id}
        if limit is not None:
            args["limit"] = limit
        if page is not None:
            args["page"] = page
        return self.call("get_strategy_orders_history", args)

    def export_strategy_orders_history(self, profile_strategy_id: str) -> Any:
        if not profile_strategy_id:
            raise WunValidationError("export_strategy_orders_history requires profileStrategyId")
        return self.call("export_strategy_orders_history", {"profileStrategyId": profile_strategy_id})

    # -- write tools -------------------------------------------------------
    def place_strategy_trade(self, payload: PlaceStrategyTrade | dict[str, Any]) -> Any:
        """Create + start a strategy (opens a position). Confirm first."""
        body = payload.payload() if isinstance(payload, PlaceStrategyTrade) else payload
        return self.call("place_strategy_trade", body)

    def place_strategy_market_enter(self, strategy_id: str) -> Any:
        if not strategy_id:
            raise WunValidationError("place_strategy_market_enter requires a strategy id")
        return self.call("place_strategy_market_enter", {"id": strategy_id})

    def place_strategy_swing(self, strategy_id: str, *, client_id: str | None = None) -> Any:
        if not strategy_id:
            raise WunValidationError("place_strategy_swing requires a strategy id")
        args: dict[str, Any] = {"id": strategy_id}
        if client_id:
            args["clientId"] = client_id
        return self.call("place_strategy_swing", args)

    def edit_trade_strategy(
        self,
        payload: EditTradeStrategy | dict[str, Any],
        *,
        strategy_group_type: str | None = None,
    ) -> Any:
        """Edit a live strategy.

        ``strategy_group_type`` (``classic``/``dca``) should come from
        ``get_strategy``; classic strategies reject DCA fields.
        """
        if isinstance(payload, EditTradeStrategy):
            if strategy_group_type:
                payload.validate_for_group(strategy_group_type)
            body = payload.payload()
        else:
            body = payload
        return self.call("edit_trade_strategy", body)

    def cancel_strategy(self, strategy_id: str) -> Any:
        if not strategy_id:
            raise WunValidationError("cancel_strategy requires a strategy id")
        return self.call("cancel_strategy", {"id": strategy_id})

    def close_strategy_market(self, strategy_id: str) -> Any:
        if not strategy_id:
            raise WunValidationError("close_strategy_market requires a strategy id")
        return self.call("close_strategy_market", {"id": strategy_id})


__all__ = ["McpClient"]
