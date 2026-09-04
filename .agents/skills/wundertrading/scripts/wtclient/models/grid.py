"""Validated payload + geometry for the session-auth Grid bot surface."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import (
    AmountType,
    GridMarket,
    GridMethod,
    GridTradingType,
    GridType,
    PnlCompareType,
    PositionsProfitCondition,
    ProfitCurrencyType,
    SignalSource,
    StartCondition,
    StopCondition,
)


def grid_line_geometry(
    low_price: float,
    high_price: float,
    step_percent: float,
    init_price: float,
) -> tuple[list[float], int, float, float]:
    """Replicate the configurator's grid-line geometry.

    Returns ``(lines, grid_levels, closest_low, closest_high)``. The algorithm
    matches ``browser-debug/docs/wt/grid-bot-api.md``: start at ``lowPrice``,
    multiply by ``1 + step/100`` while still inside the channel, then append
    ``highPrice``. ``closestLow/High`` are the two lines bracketing
    ``initPrice``.
    """
    low = float(low_price)
    high = float(high_price)
    step = float(step_percent)
    price = float(init_price)
    if step <= 0:
        raise ValueError("step_percent must be > 0")
    if high <= low:
        raise ValueError("high_price must be greater than low_price")

    factor = 1.0 + step / 100.0
    lines: list[float] = []
    current = low
    while current <= high and (high - current) / current * 100 >= step:
        lines.append(current)
        current *= factor
    lines.append(high)

    closest_low: float | None = None
    closest_high: float | None = None
    for level in lines:
        if level <= price:
            closest_low = level
        if level >= price and closest_high is None:
            closest_high = level
    if closest_low is None or closest_high is None:
        raise ValueError("init_price is outside the channel")
    return lines, len(lines), closest_low, closest_high


def bracket_levels(
    low_price: float,
    high_price: float,
    step_percent: float,
    init_price: float,
    *,
    round_to: int = 6,
) -> dict[str, Any]:
    """Return rounded ``closestLowLevelPrice``/``closestHighLevelPrice``/``gridLevels``."""
    _, levels, low_level, high_level = grid_line_geometry(
        low_price, high_price, step_percent, init_price
    )
    return {
        "gridLevels": levels,
        "closestLowLevelPrice": round(low_level, round_to),
        "closestHighLevelPrice": round(high_level, round_to),
    }


class GridUpsertPayload(BaseModel):
    """Payload for ``POST /en/trader/grid_bots/upsert`` (create and edit)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    exchangeCode: str = Field(..., min_length=1)
    pairCode: str = Field(..., min_length=1)
    profilesCodes: list[str] = Field(..., min_length=1)
    gridType: GridType = GridType.INTERVAL
    gridMethod: GridMethod = GridMethod.CLASSIC
    gridTradingType: GridTradingType = GridTradingType.LONG
    gridPercentStep: float = Field(..., gt=0, description="Profit per GRID as a decimal (3% -> 0.03)")
    gridTickStep: float | None = None
    gridLevels: int | None = Field(default=None, ge=1)
    midPrice: float | None = None
    initPrice: float | None = None
    closestHighLevelPrice: float | None = None
    closestLowLevelPrice: float | None = None
    amountPerTrade: float = Field(..., gt=0)
    amountPerTradeType: AmountType = AmountType.BASE
    stopOnOutOfGrid: bool | None = None
    startCondition: StartCondition = StartCondition.IMMEDIATE
    signalCode: str | None = None
    maxRequiredAmount: float | None = None
    leverage: int | None = Field(default=None, ge=1, le=125)
    highPrice: float | None = None
    lowPrice: float | None = None
    stopCondition: StopCondition = StopCondition.STOP_ONLY
    profitCurrencyType: ProfitCurrencyType = ProfitCurrencyType.BASE
    pumpProtection: bool | None = None
    pumpProtectionOrderType: Literal["market"] | None = None
    takeProfit: float | None = Field(default=None, gt=0)
    stopLoss: float | None = Field(default=None, gt=0)
    stopLossPnlCompareType: PnlCompareType | None = None
    trailingStopExecute: float | None = None
    trailingStopActivation: float | None = None
    trailingStopPnlCompareType: PnlCompareType | None = None
    indicators: dict[str, Any] | None = None
    signalSource: SignalSource | None = None
    strategyProfitCondition: PositionsProfitCondition | None = None
    strategyStopLossFixedPercentRatio: float | None = None
    # Client-side hint used by the CLI to choose the ?gridMarket= query param.
    gridMarketHint: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _grid_type_rules(self) -> "GridUpsertPayload":
        if self.gridType is GridType.INTERVAL:
            if self.highPrice is None or self.lowPrice is None:
                raise ValueError("gridType='interval' requires highPrice and lowPrice")
            if self.highPrice <= self.lowPrice:
                raise ValueError("highPrice must be greater than lowPrice")
        else:
            if self.highPrice is not None or self.lowPrice is not None:
                raise ValueError("gridType='infinite' must not set highPrice/lowPrice")
        return self

    @model_validator(mode="after")
    def _start_condition_rules(self) -> "GridUpsertPayload":
        if self.startCondition is StartCondition.INDICATOR and not self.indicators:
            raise ValueError("startCondition='indicator' requires 'indicators'")
        if self.startCondition is StartCondition.WEBHOOK_ALERT and self.signalSource is None:
            raise ValueError("startCondition='webhook_alert' requires 'signalSource'")
        if self.startCondition is not StartCondition.WEBHOOK_ALERT and self.signalCode is not None:
            raise ValueError("signalCode is only valid with startCondition='webhook_alert'")
        return self

    def with_channel(
        self,
        low_price: float,
        high_price: float,
        *,
        init_price: float | None = None,
        round_to: int = 6,
    ) -> "GridUpsertPayload":
        """Apply an interval channel and compute grid levels + bracket prices."""
        price = init_price if init_price is not None else (high_price + low_price) / 2.0
        geometry = bracket_levels(
            low_price,
            high_price,
            self.gridPercentStep * 100.0,
            price,
            round_to=round_to,
        )
        return self.model_copy(
            update={
                "gridType": GridType.INTERVAL,
                "lowPrice": round(low_price, round_to),
                "highPrice": round(high_price, round_to),
                "midPrice": round(price, round_to),
                "initPrice": round(price, round_to),
                **geometry,
            }
        )

    def validate_for_market(self, grid_market: str | GridMarket) -> "GridUpsertPayload":
        """Validate spot/derivative-specific rules.

        Spot allows ``profitCurrencyType`` base|quote; derivative only base.
        """
        market = grid_market if isinstance(grid_market, GridMarket) else GridMarket(grid_market)
        if market is GridMarket.DERIVATIVE and self.profitCurrencyType is not ProfitCurrencyType.BASE:
            raise ValueError("derivative grids require profitCurrencyType='base'")
        return self

    def payload(self) -> dict:
        """Return the JSON-ready payload (hints and ``None`` fields removed)."""
        return self.model_dump(mode="json", exclude_none=True)


__all__ = ["GridUpsertPayload", "grid_line_geometry", "bracket_levels"]
