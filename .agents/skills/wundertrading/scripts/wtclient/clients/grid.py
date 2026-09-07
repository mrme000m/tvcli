"""Client for the session-auth Grid bot surface.

Uses :class:`SessionTransport` (raw httpx replay, needs fresh cf_clearance) or
:class:`BrowserTransport` (reliable fetch-in-page). Every management URL is
deterministic as documented in ``references/grid-bot.md``.
"""
from __future__ import annotations

import time
from typing import Any

from ..models.common import GridMarket, PnlCompareType, StopCondition
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
                    "amountPerTrade": resource.get("amountPerTrade"),
                    "takeProfit": resource.get("takeProfit"),
                    "stopLoss": resource.get("stopLoss"),
                    "stopLossPnlCompareType": resource.get("stopLossPnlCompareType"),
                    "trailingStopActivation": resource.get("trailingStopActivation"),
                    "trailingStopExecute": resource.get("trailingStopExecute"),
                    "trailingStopPnlCompareType": resource.get("trailingStopPnlCompareType"),
                    "strategyProfitCondition": resource.get("strategyProfitCondition"),
                    "strategyStopLossFixedPercentRatio": resource.get("strategyStopLossFixedPercentRatio"),
                    "pumpProtection": resource.get("pumpProtection"),
                    "pumpProtectionOrderType": resource.get("pumpProtectionOrderType"),
                    "stopCondition": resource.get("stopCondition"),
                    "startCondition": resource.get("startCondition"),
                    "actions": actions,
                }
            )
        return out

    def _fetch_resource(self, code: str, *, limit: int = 50) -> dict[str, Any]:
        """Return the raw ``resource`` dict for a grid bot by code.

        GETs the grid list without status criteria (so stopped bots are
        included) and finds the item whose ``resource.code`` matches.
        """
        path = append_query("/en/trader/grid_bots/grid", {"page": 1, "limit": limit})
        data = self.request("GET", path)
        items = (data or {}).get("_embedded", {}).get("items") or []
        for item in items:
            resource = item.get("resource") or {}
            if resource.get("code") == code:
                return resource
        raise ValueError(f"grid bot {code!r} not found")

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

    def backtest(
        self,
        payload: GridUpsertPayload | dict[str, Any],
        *,
        timeframe: int = 15,
        days: int = 31,
        from_ms: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Run the client-side grid backtest engine on a config payload.

        Mirrors the Edit form's Backtest button (live-verified 2026-09-07):
        one public ``GET :2087/ohlc`` history fetch, then the pure engine
        (``wtclient.backtest.run_backtest``) computes the UI summary.
        ``payload`` is the upsert-style config dict (or a
        :class:`GridUpsertPayload`); ``timeframe`` is minutes (15m UI
        default), ``days`` the lookback (31 → the UI's 30-day period),
        ``from_ms`` an explicit epoch-milliseconds start, ``limit`` an
        explicit candle cap (default ``days * 96`` capped at 2976).
        """
        from .. import backtest as backtest_engine

        cfg = payload.payload() if isinstance(payload, GridUpsertPayload) else dict(payload)
        exchange_code = cfg.get("exchangeCode")
        pair_code = cfg.get("pairCode")
        if not exchange_code or not pair_code:
            raise ValueError("payload needs exchangeCode and pairCode")
        code = f"{exchange_code}:{pair_code}"
        candle_limit = limit if limit is not None else min(days * 96, 2976)
        start = from_ms if from_ms is not None else int(time.time() * 1000 - timeframe * 60 * 1000 * candle_limit)
        fetched = self._market_client().ohlc(
            code, timeframe=timeframe, limit=candle_limit, from_ms=start
        )
        candles = fetched.get("data") if isinstance(fetched, dict) else fetched
        if not isinstance(candles, list) or not candles:
            raise ValueError(f"no candles returned for {code}")
        engine_input = backtest_engine.build_input(cfg, candles)
        result = backtest_engine.run_backtest(engine_input, candles)
        result["summary"] = backtest_engine.summary(result)
        return result

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

    def set_exits(
        self,
        code: str,
        *,
        take_profit: float | None = None,
        stop_loss: float | None = None,
        pnl_compare_type: str | PnlCompareType | None = None,
        trailing_activation: float | None = None,
        trailing_execute: float | None = None,
        positions_trailing_stop: bool | None = None,
        positions_stop_loss_pct: float | None = None,
        order_type: str | None = None,
        grid_market: str | GridMarket | None = None,
    ) -> Any:
        """Edit ONLY the exit/risk fields of an existing grid bot.

        Fetches the bot's current resource, rebuilds the full upsert body
        from it, overlays the provided exit kwargs, and POSTs it through the
        same edit path as :meth:`edit`. The bot is NOT stopped/restarted —
        exit-field edits apply live to active bots (verified 2026-09-07).

        ``pnl_compare_type`` sets both ``stopLossPnlCompareType`` and
        ``trailingStopPnlCompareType`` ("total" | "unrealized").
        ``positions_trailing_stop`` maps to ``strategyProfitCondition``
        (True -> "trailing_stop", False -> "take_profit").
        ``positions_stop_loss_pct`` takes a percent like the UI (5 -> 0.05).
        ``order_type`` maps to ``pumpProtectionOrderType`` ("market"|"limit").
        """
        resource = self._fetch_resource(code)
        pair = resource.get("pair") or {}
        # Resource pair.unifiedCode is the display form ("NEAR-USDT"); the
        # upsert needs the raw pairCode ("NEARUSDT").
        pair_code = pair.get("code") or str(pair.get("unifiedCode") or "").replace("-", "")
        profiles_codes = [
            entry.get("profile", {}).get("code")
            for entry in (resource.get("profiles") or [])
            if isinstance(entry, dict)
        ]
        profiles_codes = [p for p in profiles_codes if p]
        indicators = resource.get("indicators")
        body: dict[str, Any] = {
            "exchangeCode": (resource.get("exchange") or {}).get("code"),
            "pairCode": pair_code,
            "profilesCodes": profiles_codes,
            "gridType": resource.get("gridType"),
            "gridMethod": resource.get("gridMethod"),
            "gridTradingType": resource.get("gridTradingType"),
            "gridPercentStep": resource.get("gridPercentStep"),
            "gridTickStep": resource.get("gridTickStep"),
            "gridLevels": resource.get("gridLevels"),
            "midPrice": resource.get("midPrice"),
            "initPrice": resource.get("initPrice"),
            "closestHighLevelPrice": resource.get("closestHighLevelPrice"),
            "closestLowLevelPrice": resource.get("closestLowLevelPrice"),
            "amountPerTrade": resource.get("amountPerTrade"),
            "amountPerTradeType": resource.get("amountPerTradeType"),
            "stopOnOutOfGrid": resource.get("stopOnOutOfGrid"),
            "startCondition": resource.get("startCondition"),
            "signalCode": resource.get("signalCode"),
            "maxRequiredAmount": resource.get("maxRequiredAmount"),
            "leverage": resource.get("leverage"),
            "highPrice": resource.get("highPrice"),
            "lowPrice": resource.get("lowPrice"),
            "stopCondition": resource.get("stopCondition"),
            "profitCurrencyType": resource.get("profitCurrencyType"),
            "pumpProtection": resource.get("pumpProtection"),
            "pumpProtectionOrderType": resource.get("pumpProtectionOrderType"),
            "takeProfit": resource.get("takeProfit"),
            "stopLoss": resource.get("stopLoss"),
            "stopLossPnlCompareType": resource.get("stopLossPnlCompareType"),
            "trailingStopActivation": resource.get("trailingStopActivation"),
            "trailingStopExecute": resource.get("trailingStopExecute"),
            "trailingStopPnlCompareType": resource.get("trailingStopPnlCompareType"),
            "strategyProfitCondition": resource.get("strategyProfitCondition"),
            "strategyStopLossFixedPercentRatio": resource.get("strategyStopLossFixedPercentRatio"),
        }
        if isinstance(indicators, dict) and indicators:
            body["indicators"] = indicators
        if resource.get("signalSource") is not None:
            body["signalSource"] = resource.get("signalSource")

        # Overlay ONLY the provided exit fields.
        if take_profit is not None:
            body["takeProfit"] = take_profit
        if stop_loss is not None:
            body["stopLoss"] = stop_loss
        if pnl_compare_type is not None:
            compare = pnl_compare_type.value if isinstance(pnl_compare_type, PnlCompareType) else pnl_compare_type
            body["stopLossPnlCompareType"] = compare
            body["trailingStopPnlCompareType"] = compare
        if trailing_activation is not None:
            body["trailingStopActivation"] = trailing_activation
        if trailing_execute is not None:
            body["trailingStopExecute"] = trailing_execute
        if positions_trailing_stop is not None:
            body["strategyProfitCondition"] = (
                "trailing_stop" if positions_trailing_stop else "take_profit"
            )
        if positions_stop_loss_pct is not None:
            body["strategyStopLossFixedPercentRatio"] = float(positions_stop_loss_pct) / 100.0
        if order_type is not None:
            body["pumpProtectionOrderType"] = order_type

        model = GridUpsertPayload.model_validate(body)
        market: str
        if grid_market is not None:
            market = grid_market.value if isinstance(grid_market, GridMarket) else grid_market
        elif resource.get("gridMarket") in ("spot", "derivative"):
            market = resource["gridMarket"]
        else:
            market = self._grid_market(model, None)
        model.validate_for_market(market)
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
