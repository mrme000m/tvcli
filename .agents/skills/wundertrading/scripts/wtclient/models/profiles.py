"""Exchange-account profile model for the my-exchanges session surface.

``GET /en/trader/my-exchanges/master-api-profile/grid`` returns a HAL
collection whose items carry (at least): ``id`` (numeric), ``code`` (hex
string — the key for edit/delete actions), ``name``, ``exchangeFamily``
("HYPERLIQUID" or "BINANCE"), ``paperTrading``, ``enabled``, ``marginMode``,
``tradeMode`` and ``favorite``, plus an ``actions`` block with the verified
``delete`` affordance. Fields are tolerated as missing — the cabinet has
been seen to omit some of them depending on the exchange family.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass
class Profile:
    """One exchange-account profile (paper or live) from the cabinet."""

    id: str | None = None
    code: str | None = None
    name: str | None = None
    exchange_family: str | None = None
    paper_trading: bool | None = None
    enabled: bool | None = None
    margin_mode: str | None = None
    trade_mode: str | None = None
    favorite: bool | None = None

    @classmethod
    def from_hal(cls, item: Any) -> "Profile":
        """Build a :class:`Profile` from one HAL ``_embedded.items`` entry.

        Tolerates missing fields, non-dict items, and nested ``resource``
        wrappers (some cabinet endpoints nest the payload).
        """
        if not isinstance(item, dict):
            return cls()
        source = item.get("resource") if isinstance(item.get("resource"), dict) else item
        return cls(
            id=source.get("id"),
            code=source.get("code"),
            name=source.get("name"),
            exchange_family=source.get("exchangeFamily"),
            paper_trading=source.get("paperTrading"),
            enabled=source.get("enabled"),
            margin_mode=source.get("marginMode"),
            trade_mode=source.get("tradeMode"),
            favorite=source.get("favorite"),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-ready dict (wire naming, ``None`` fields kept)."""
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "exchangeFamily": self.exchange_family,
            "paperTrading": self.paper_trading,
            "enabled": self.enabled,
            "marginMode": self.margin_mode,
            "tradeMode": self.trade_mode,
            "favorite": self.favorite,
        }


def parse_profiles(items: Iterable[Any] | None) -> list[Profile]:
    """Parse HAL ``_embedded.items`` (or any iterable) into profiles."""
    if not items:
        return []
    return [Profile.from_hal(item) for item in items]


__all__ = ["Profile", "parse_profiles"]
