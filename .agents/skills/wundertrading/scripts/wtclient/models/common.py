"""Shared enums and percentage coercion helpers."""
from __future__ import annotations

import enum
from typing import Annotated

from pydantic import BeforeValidator


class Side(str, enum.Enum):
    LONG = "long"
    SHORT = "short"


class OrderType(str, enum.Enum):
    MARKET = "market"
    LIMIT = "limit"


class AmountPerTradeType(str, enum.Enum):
    QUOTE = "quote"
    BASE = "base"
    DOLLARS = "$"
    PERCENTS = "percents"
    CONTRACTS = "contracts"


class StrategyGroupType(str, enum.Enum):
    CLASSIC = "classic"
    DCA = "dca"


class StrategyStatus(str, enum.Enum):
    NEW = "new"
    ENTERED = "entered"
    COMPLETED = "completed"
    CANCELED = "canceled"
    CANCELLING = "cancelling"
    PANIC_EXITED = "panic_exited"
    PANIC_EXITING = "panic_exiting"
    UNLINKED = "unlinked"
    FAILED = "failed"


class GridMarket(str, enum.Enum):
    SPOT = "spot"
    DERIVATIVE = "derivative"


class GridType(str, enum.Enum):
    INTERVAL = "interval"
    INFINITE = "infinite"


class GridMethod(str, enum.Enum):
    CLASSIC = "classic"
    TWO_WAY = "two_way"


class GridTradingType(str, enum.Enum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"
    TWO_WAY = "two_way"


class AmountType(str, enum.Enum):
    BASE = "base"
    PERCENTS = "percents"


class StartCondition(str, enum.Enum):
    IMMEDIATE = "immediate"
    INDICATOR = "indicator"
    WEBHOOK_ALERT = "webhook_alert"


class StopCondition(str, enum.Enum):
    STOP_ONLY = "stop_only"
    STOP_AND_CLOSE_ALL = "stop_and_close_all"
    STOP_AND_CLOSE_ALL_AND_CONVERT = "stop_and_close_all_and_convert_to_profit_currency"


class ProfitCurrencyType(str, enum.Enum):
    BASE = "base"
    QUOTE = "quote"


class PnlCompareType(str, enum.Enum):
    TOTAL = "total"


class PositionsProfitCondition(str, enum.Enum):
    TRAILING_STOP = "trailing_stop"


class SignalSource(str, enum.Enum):
    TRADING_VIEW = "trading_view"


def _fmt_decimal(value: float) -> str:
    """Format a float as a short decimal string (no trailing zeros)."""
    if value == 0:
        return "0"
    text = f"{value:.8f}".rstrip("0").rstrip(".")
    if text in ("", "-", "-0"):
        return "0"
    return text


def _parse_percent(value: object, *, allow_negative: bool = False) -> str | None:
    """Normalize ``0.6`` / ``'60%'`` / ``'60'`` to a canonical decimal string.

    WunderTrading's schema types several deviation/portfolio fields as
    strings that accept all three formats and all mean the same decimal
    fraction. Canonicalizing to a decimal string (``"0.6"``) keeps the payload
    deterministic while remaining accepted by the API.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("percentage must not be a boolean")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("percentage must not be empty")
        if text.endswith("%"):
            number = float(text[:-1]) / 100.0
        else:
            number = float(text)
    else:
        number = float(value)
    if abs(number) > 1.0 and not (allow_negative and -1.0 <= number <= 1.0):
        # Bare values above 1 are percentages (60 == 60% == 0.6).
        number = number / 100.0
    if not allow_negative and number <= 0:
        raise ValueError("percentage must be greater than 0")
    if not allow_negative and number > 1.0:
        raise ValueError("percentage must not exceed 1 (100%)")
    if allow_negative and not (-1.0 <= number <= 1.0):
        raise ValueError("percentage must be between -1 and 1")
    return _fmt_decimal(number)


def _positive_percent(value: object) -> str | None:
    if value is None:
        return None
    return _parse_percent(value)


def _signed_percent(value: object) -> str | None:
    if value is None:
        return None
    return _parse_percent(value, allow_negative=True)


def _percent_float(value: object) -> float | None:
    """Like :func:`_parse_percent` but returns a decimal float (edit tools)."""
    if value is None:
        return None
    text = _parse_percent(value)
    return float(text) if text is not None else None


#: Canonical decimal string percent, e.g. ``0.6`` (0-1 range enforced later).
PositivePercentString = Annotated[str, BeforeValidator(_positive_percent)]
#: Canonical decimal string percent allowing negatives, e.g. ``-0.6``.
SignedPercentString = Annotated[str, BeforeValidator(_signed_percent)]
#: Decimal float percent for edit-tool numeric fields.
PositivePercentFloat = Annotated[float, BeforeValidator(_percent_float)]


__all__ = [
    "Side",
    "OrderType",
    "AmountPerTradeType",
    "StrategyGroupType",
    "StrategyStatus",
    "GridMarket",
    "GridType",
    "GridMethod",
    "GridTradingType",
    "AmountType",
    "StartCondition",
    "StopCondition",
    "ProfitCurrencyType",
    "PnlCompareType",
    "PositionsProfitCondition",
    "SignalSource",
    "PositivePercentString",
    "SignedPercentString",
    "PositivePercentFloat",
]
