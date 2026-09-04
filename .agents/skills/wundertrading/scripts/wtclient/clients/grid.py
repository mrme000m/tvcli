"""Client for the session-auth Grid bot surface.

Uses :class:`SessionTransport` (raw httpx replay, needs fresh cf_clearance) or
:class:`BrowserTransport` (reliable fetch-in-page). Every management URL is
deterministic as documented in ``references/grid-bot.md``.
"""
from __future__ import annotations

from typing import Any

from ..models.common import GridMarket, StopCondition
from ..models.grid import GridUpsertPayload
from ..query import append_query
from ..transport.base import BaseTransport
from ..transport.browser import BrowserTransport
from ..transport.session import SessionTransport
from .base import BaseClient
from .market import MarketDataClient

_BOT_TYPES = {
    "signal": "/en/trader/signal_bots",
    "grid": "/en/trader/grid_bots",
    "dca": "/en/trader/dca_bots",
    "mn": "/en/trader/market_neutral",
    "mp": "/en/trader/multi_pair_grid_bot",
}


class GridClient(BaseClient):
    """Grid-bot configurator client over the session-auth web surface."""

    def __init__(
        self,
        transport: BaseTransport | None = None,
        *,
        market: BaseTransport | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(transport or SessionTransport(**kwargs))
        self._market = market

    def _market_client(self) -> MarketDataClient:
        if self._market is not None:
            return MarketDataClient(self._market)
        if isinstance(self.transport, BrowserTransport):
            return MarketDataClient(self.transport)
        return MarketDataClient()

    def _coerce_payload(self, payload: GridUpsertPayload | dict[str, Any], market: str | GridMarket | None) -> GridUpsertPayload:
        model = payload if isinstance(payload, GridUpsertPayload) else GridUpsertPayload.model_validate(payload)
        if market is not None:
            model.validate_for_market(market)
        return model

    def _grid_market(self, payload: GridUpsertPayload, market: str | GridMarket | None) -> str:
        if market is not None:
            return market.value if isinstance(market, GridMarket) else market
        hint = payload.gridMarketHint
        if hint in ("spot", "derivative"):
            return hint
        return "derivative"

    # -- raw --------------------------------------------------------------
    def request(self, method: str, path: str, *, body: Any = None) -> Any:
        response = self.transport.request(method, path, body=body)
        response.raise_for_status()
        return self._json(response)

    # -- read --------------------------------------------------------------
    def list(self, *, active_only: bool = True, limit: int = 50) -> list[dict[str, Any]]:
        path = append_query(
            "/en/trader/grid_bots/grid",
            {
                "page": 1,
                "limit": limit,
                **({"criteria[statuses][value][]": "active"} if active_only else {}),
            },
        )
        data = self.request("GET", path)
        items = (data or {}).get("_embedded", {}).get("items") or []
        out: list[dict[str, Any]] = []
        for item in items:
            resource = item.get("resource") or {}
            actions = {
                key: f"{value.get('data', {}).get('method')} {value.get('data', {}).get('link')}"
                for key, value in (item.get("actions") or {}).items()
                if isinstance(value.get("data"), dict) and value["data"].get("link")
            }
            out.append(
                {
                    "code": resource.get("code"),
                    "status": resource.get("status"),
                    "pair": (resource.get("pair") or {}).get("unifiedCode"),
                    "exchange": (resource.get("exchange") or {}).get("code"),
                    "paperTrading": resource.get("paperTrading"),
                    "gridTradingType": resource.get("gridTradingType"),
                    "gridType": resource.get("gridType"),
                    "step": resource.get("gridPercentStep"),
                    "levels": resource.get("gridLevels"),
                    "high": resource.get("highPrice"),
                    "low": resource.get("lowPrice"),
                    "actions": actions,
                }
            )
        return out

    def list_bots(self, bot_type: str, *, active_only: bool = True, limit: int = 50) -> list[dict[str, Any]]:
        """List one of ``signal``/``grid``/``dca``/``mn``/``mp`` bots."""
        base = _BOT_TYPES.get(bot_type)
        if not base:
            raise ValueError(f"unknown bot type {bot_type!r}; use one of {sorted(_BOT_TYPES)}")
        params: dict[str, Any] = {}
        if bot_type in ("signal", "grid", "dca"):
            params["page"] = 1
            params["limit"] = limit
            if active_only:
                params["criteria[statuses][value][]"] = "active"
        elif bot_type == "mp":
            params["limit"] = 200
        path = append_query(f"{base}/grid", params)
        data = self.request("GET", path)
        items = (data or {}).get("_embedded", {}).get("items") or []
        out: list[dict[str, Any]] = []
        for item in items:
            resource = item.get("resource") or item
            out.append(
                {
                    "code": resource.get("code"),
                    "id": resource.get("id"),
                    "status": resource.get("status"),
                    "pair": (resource.get("pair") or {}).get("unifiedCode") or resource.get("pairCode"),
                    "type": resource.get("gridTradingType") or resource.get("dcaTradingType") or resource.get("type"),
                }
            )
        return out

    def analyze(self, code: str) -> dict[str, Any]:
        """Return market metadata + last candle + 30-day high/low for a pair code."""
        market = self._market_client()
        return {
            "market": market.market(code),
            "lastCandle": market.ohlc_last(code, timeframe=15),
            "thirtyDayHighLow": market.ohlc_low_high(code, timeframe=15, limit=2976),
        }

    def positions(self, code: str) -> Any:
        return self.request("GET", f"/en/trader/grid_bots/{code}/positions/grid")

    def positions_history(self, code: str) -> Any:
        return self.request("GET", f"/en/trader/grid_bots/{code}/positions-history/grid")

    def presets(self, limit: int = 10) -> Any:
        path = append_query("/en/trader/grid_bots/presets", {"page": 1, "limit": limit})
        return self.request("GET", path)

    def profiles(self) -> Any:
        """Form-init data, including ``exchangesProfiles`` with balances."""
        return self.request("GET", "/en/trader/grid_bots/upsert")

    # -- write --------------------------------------------------------------
    def create(
        self,
        payload: GridUpsertPayload | dict[str, Any],
        *,
        grid_market: str | GridMarket | None = None,
    ) -> Any:
        model = self._coerce_payload(payload, grid_market)
        market = self._grid_market(model, grid_market)
        path = append_query("/en/trader/grid_bots/upsert", {"gridMarket": market})
        return self.request("POST", path, body=model.payload())

    def edit(
        self,
        code: str,
        payload: GridUpsertPayload | dict[str, Any],
        *,
        grid_market: str | GridMarket | None = None,
    ) -> Any:
        model = self._coerce_payload(payload, grid_market)
        market = self._grid_market(model, grid_market)
        path = append_query(
            "/en/trader/grid_bots/upsert", {"gridMarket": market, "code": code}
        )
        return self.request("POST", path, body=model.payload())

    def stop(self, code: str, stop_condition: str | StopCondition = StopCondition.STOP_ONLY) -> Any:
        condition = stop_condition.value if isinstance(stop_condition, StopCondition) else stop_condition
        path = append_query(
            f"/en/trader/grid_bots/{code}/stop",
            {"stopCondition": condition, "awaitStartSignal": "true"},
        )
        return self.request("POST", path, body={})

    def restart(self, code: str) -> Any:
        return self.request("POST", f"/en/trader/grid_bots/{code}/restart")

    def close_all(self, code: str) -> Any:
        return self.request("POST", f"/en/trader/grid_bots/{code}/close-all", body={})

    def delete(self, code: str) -> Any:
        return self.request("DELETE", f"/en/trader/grid_bots/{code}/delete")


__all__ = ["GridClient"]
