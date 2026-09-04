"""Validated payloads for the classic/DCA strategy tools.

These models mirror the live JSON schemas returned by the MCP ``tools/list``
endpoint (verified 2026-09-04) plus the extra cross-field rules in
``references/mcp-tools.md``.

Percent-style fields (``portfolio``, ``priceDeviation``, ``stopLoss``, …)
accept ``0.6``, ``"60%"``, or ``"60"`` and canonicalize to a decimal string
(``"0.6"``) so the payload is deterministic.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import (
    AmountPerTradeType,
    OrderType,
    PositivePercentString,
    PositivePercentFloat,
    Side,
    SignedPercentString,
)

CLIENT_ID_PATTERN = r"^[~=-a-zA-Z0-9]+$"


class PlaceTakeProfit(BaseModel):
    """One take-profit target for :class:`PlaceStrategyTrade`.

    Exactly one of ``price`` or ``priceDeviation`` must be provided.
    """

    model_config = ConfigDict(extra="forbid")

    price: float | None = Field(default=None, gt=0)
    priceDeviation: PositivePercentString | None = None
    portfolio: PositivePercentString

    @model_validator(mode="after")
    def _exactly_one_price(self) -> "PlaceTakeProfit":
        if (self.price is None) == (self.priceDeviation is None):
            raise ValueError("takeProfits items require exactly one of 'price' or 'priceDeviation'")
        return self


class EditTakeProfit(BaseModel):
    """One take-profit target for :class:`EditTradeStrategy` (price-based)."""

    model_config = ConfigDict(extra="forbid")

    price: float = Field(..., gt=0)
    portfolio: PositivePercentString


class PlaceStrategyTrade(BaseModel):
    """Validated ``place_strategy_trade`` arguments.

    Required fields: ``exchangeCode``, ``pairCode``, ``profilesCodes``,
    ``side``, ``orderType``, ``amountPerTrade``, ``amountPerTradeType``.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    clientId: str | None = Field(
        default=None,
        min_length=32,
        max_length=64,
        pattern=CLIENT_ID_PATTERN,
        description="Idempotency key (32-64 chars)",
    )
    exchangeCode: str = Field(..., min_length=1)
    pairCode: str = Field(..., min_length=1)
    profilesCodes: list[str] = Field(..., min_length=1)
    side: Side
    orderType: OrderType
    price: float | None = Field(default=None, gt=0)
    timeToLive: int | None = Field(default=None, ge=5, le=20160)
    amountPerTrade: float = Field(..., gt=0)
    amountPerTradeType: AmountPerTradeType
    amountPerTradeMultiplier: int | None = Field(default=None, ge=1, le=125)
    leverage: int | None = Field(default=None, ge=1, le=125)
    extraOrderCount: int | None = Field(default=None, ge=1, le=30)
    extraOrderDeviation: float | None = Field(default=None, ge=0.001, le=0.2)
    extraOrderVolumeMultiplier: float | None = Field(default=None, ge=0.1, le=10)
    extraOrderDeviationMultiplier: float | None = Field(default=None, ge=1, le=10)
    extraOrderCostAveraging: Literal["base", "quote"] | None = None
    applyDcaForFirstSafetyOrder: bool | None = None
    takeProfits: list[PlaceTakeProfit] | None = Field(default=None, min_length=1, max_length=10)
    takeProfitBaseOn: Literal["entry_order", "average_price"] | None = None
    stopLoss: PositivePercentString | None = None
    stopLossPrice: float | None = Field(default=None, gt=0)
    stopLossBaseOn: Literal["entry_order", "average_price"] | None = None
    stopLossMove: PositivePercentString | None = None
    stopLossMoveExecute: SignedPercentString | None = None
    trailingStopActivation: PositivePercentString | None = None
    trailingStopExecute: PositivePercentString | None = None
    reduceOnly: bool | None = None
    keepPositionOpen: bool | None = None
    placeConditionalOrdersOnExchange: bool | None = None

    @field_validator("amountPerTrade", mode="before")
    @classmethod
    def _coerce_amount(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if text.endswith("%"):
                return float(text[:-1]) / 100.0
            return float(text)
        return value

    @model_validator(mode="after")
    def _limit_rules(self) -> "PlaceStrategyTrade":
        if self.orderType is OrderType.LIMIT:
            if self.price is None or self.timeToLive is None:
                raise ValueError("orderType='limit' requires price and timeToLive")
        else:
            if self.price is not None or self.timeToLive is not None:
                raise ValueError("orderType='market' must not set price/timeToLive")
        return self

    @model_validator(mode="after")
    def _amount_unit_rules(self) -> "PlaceStrategyTrade":
        if self.amountPerTradeType is AmountPerTradeType.PERCENTS and self.amountPerTrade > 1:
            raise ValueError("amountPerTrade must be <= 1 (100%) when amountPerTradeType='percents'")
        return self

    @model_validator(mode="after")
    def _tp_portfolio_sum(self) -> "PlaceStrategyTrade":
        if self.takeProfits:
            total = sum(_portfolio_float(tp.portfolio) for tp in self.takeProfits)
            if abs(total - 1.0) > 1e-6:
                raise ValueError("takeProfits portfolios must sum to 1.0")
        return self

    @model_validator(mode="after")
    def _stop_move_rules(self) -> "PlaceStrategyTrade":
        if self.stopLossMoveExecute is not None and self.stopLossMove is None:
            raise ValueError("stopLossMoveExecute requires stopLossMove")
        return self

    def payload(self) -> dict:
        """Return the JSON-ready payload with ``None`` fields omitted."""
        return self.model_dump(mode="json", exclude_none=True)


class EditTradeStrategy(BaseModel):
    """Validated ``edit_trade_strategy`` arguments.

    ``strategyGroupType`` is not part of the tool schema; pass it to
    :meth:`validate_for_group` (from ``get_strategy``) before sending.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(..., min_length=1)
    takeProfits: list[EditTakeProfit] | None = Field(default=None, max_length=6)
    takeProfitBaseOn: Literal["entry_order", "average_price"] | None = None
    stopLossPrice: float | None = Field(default=None, gt=0)
    stopLossBaseOn: Literal["entry_order", "average_price"] | None = None
    stopLossMovePrice: float | None = Field(default=None, gt=0)
    stopLossMoveExecutePrice: float | None = Field(default=None, gt=0)
    trailingStopActivation: PositivePercentFloat | None = Field(default=None, gt=0, le=1)
    trailingStopExecute: PositivePercentFloat | None = Field(default=None, gt=0, le=1)
    reduceOnly: bool | None = None
    placeConditionalOrdersOnExchange: bool | None = None
    extraOrderCount: int | None = Field(default=None, ge=1, le=30)
    extraOrderDeviation: PositivePercentFloat | None = Field(default=None, ge=0.001, le=0.2)
    extraOrderVolumeMultiplier: float | None = Field(default=None, ge=0.1, le=10)
    extraOrderDeviationMultiplier: float | None = Field(default=None, ge=1, le=10)

    _DCA_FIELDS = (
        "extraOrderCount",
        "extraOrderDeviation",
        "extraOrderVolumeMultiplier",
        "extraOrderDeviationMultiplier",
    )

    @model_validator(mode="after")
    def _tp_portfolio_sum(self) -> "EditTradeStrategy":
        if self.takeProfits:
            total = sum(_portfolio_float(tp.portfolio) for tp in self.takeProfits)
            if abs(total - 1.0) > 1e-6:
                raise ValueError("takeProfits portfolios must sum to 1.0")
        return self

    @model_validator(mode="after")
    def _move_price_rules(self) -> "EditTradeStrategy":
        if self.stopLossMoveExecutePrice is not None and self.stopLossMovePrice is None:
            raise ValueError("stopLossMoveExecutePrice requires stopLossMovePrice")
        return self

    def has_dca_fields(self) -> bool:
        return any(getattr(self, name) is not None for name in self._DCA_FIELDS)

    def validate_for_group(self, strategy_group_type: str) -> "EditTradeStrategy":
        """Enforce classic/DCA rules from ``get_strategy`` output."""
        if strategy_group_type == "classic" and self.has_dca_fields():
            raise ValueError(
                "strategyGroupType='classic' must not carry extraOrder* DCA fields"
            )
        return self

    def payload(self) -> dict:
        return self.model_dump(mode="json", exclude_none=True)


def _portfolio_float(value: str) -> float:
    """Parse a canonical decimal-string portfolio back to float."""
    return float(value)


__all__ = [
    "PlaceTakeProfit",
    "EditTakeProfit",
    "PlaceStrategyTrade",
    "EditTradeStrategy",
]
