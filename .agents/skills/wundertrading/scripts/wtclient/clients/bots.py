"""BotsClient — generic session-auth surface for the cabinet's bot pages.

The WunderTrading cabinet exposes four bot configurators under ``/en/trader``:
``signal_bots``, ``dca_bots``, ``market_neutral`` (mn), and
``multi_pair_grid_bot`` (mp). All four follow the same pattern as the Grid
surface (PHPSESSID + CSRF; Cloudflare-fingerprinted so the browser transport
is the reliable one). Use :class:`BotsClient` for read-only listing; use
:class:`wtclient.GridClient` (or extend this class) for writes until the
write APIs are pinned down for signal/dca/mn/mp.
"""
from __future__ import annotations

from typing import Any

from ..query import append_query
from .base import BaseClient

# Mirror :data:`wtclient.clients.grid._BOT_TYPES` so callers don't need to
# import a private symbol.
_BOT_BASE = {
    "signal": "/en/trader/signal_bots",
    "dca": "/en/trader/dca_bots",
    "mn": "/en/trader/market_neutral",
    "mp": "/en/trader/multi_pair_grid_bot",
}

_BOT_KINDS = sorted(_BOT_BASE.keys())


class BotsClient(BaseClient):
    """Cabinet-side list/inspect surface for signal/dca/mn/mp bots.

    Example::

        with BotsClient() as bots:
            dcas = bots.list_active("dca")
            for dca in dcas:
                print(dca["code"], dca.get("pair"), dca.get("type"))
    """

    def list_active(self, kind: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return the active bots of one kind."""
        return self.list(kind, active_only=True, limit=limit)

    def list(
        self,
        kind: str,
        *,
        active_only: bool = True,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List bots of one kind.

        ``kind`` ∈ ``signal|dca|mn|mp``.
        """
        base = _BOT_BASE.get(kind)
        if base is None:
            raise ValueError(f"unknown bot kind {kind!r}; use one of {_BOT_KINDS}")
        params: dict[str, Any] = {}
        if kind in ("signal", "dca"):
            params["page"] = 1
            params["limit"] = limit
            if active_only:
                params["criteria[statuses][value][]"] = "active"
        elif kind == "mp":
            params["limit"] = 200
        path = append_query(f"{base}/grid", params)
        data = self._get_json("GET", path)
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
                    "type": (
                        resource.get("gridTradingType")
                        or resource.get("dcaTradingType")
                        or resource.get("type")
                    ),
                    "exchange": (resource.get("exchange") or {}).get("code"),
                }
            )
        return out

    def raw(self, kind: str) -> Any:
        """Return the raw cabinet response for the bot list endpoint.

        Useful when the caller wants to inspect ``_embedded.items`` or the
        ``actions`` links in full.
        """
        base = _BOT_BASE.get(kind)
        if base is None:
            raise ValueError(f"unknown bot kind {kind!r}; use one of {_BOT_KINDS}")
        return self._get_json("GET", f"{base}/grid")

    def supports(self, kind: str) -> bool:
        return kind in _BOT_BASE

    @property
    def kinds(self) -> list[str]:
        return list(_BOT_KINDS)


__all__ = ["BotsClient"]
