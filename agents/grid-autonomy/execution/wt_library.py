#!/usr/bin/env python3
"""wt_library — direct in-process adapter to the wtclient Python library.

Mirrors the public surface of ``execution/grid_adapter.py`` but uses
``wtclient.WunderTrading`` directly instead of shelling out to
``wt_browser.py`` via subprocess. The result is one process, fewer
forks, structured trace logging, and a single place to attach the
discovery/debug machinery (``wtclient.debug.trace(wun)``).

Used by:

- ``execution/grid_adapter.py`` — the deploy/stop/edit/delete calls
- ``scripts/repair_ledger.py`` — the read-only ``grid list`` refresh

All write functions honor ``dry_run=True`` (the default) and never mutate
WunderTrading without the guardrail layer having signed off.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

# Make wtclient importable without installing it as a wheel.
REPO_ROOT = Path(__file__).resolve().parents[3]
WUN_SCRIPTS = REPO_ROOT / ".agents" / "skills" / "wundertrading" / "scripts"
if str(WUN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WUN_SCRIPTS))

from wtclient import (  # noqa: E402
    WunderTrading,
    Recorder,
    trace as _debug_trace,
    unwrap as _debug_unwrap,
)
from wtclient.discovery import EndpointCatalog  # noqa: E402
from wtclient.errors import WunError  # noqa: E402

# -- process-local singleton --------------------------------------------------


_lock = threading.Lock()
_wun: WunderTrading | None = None
_recorder: Recorder | None = None


def _ensure_wun(*, browser: bool = True) -> WunderTrading:
    """Return a cached :class:`WunderTrading` instance.

    Created lazily so we don't open a CDP page when no caller actually wants
    the browser transport. Set ``WTCLIENT_BROWSWER=0`` in the environment to
    force the raw session transport (requires a fresh ``cf_clearance``).
    """
    global _wun
    if _wun is not None:
        return _wun
    with _lock:
        if _wun is None:
            use_browser = browser and os.environ.get("WTCLIENT_BROWSWER", "1") != "0"
            _wun = WunderTrading(browser=use_browser)
    return _wun


def get_wun() -> WunderTrading:
    """Return the shared :class:`WunderTrading` (browser transport)."""
    return _ensure_wun(browser=True)


def reset_wun() -> None:
    """Close the cached facade (next call rebuilds it)."""
    global _wun, _recorder
    with _lock:
        if _recorder is not None and _wun is not None:
            try:
                _debug_unwrap(_wun)
            except Exception:
                pass
        if _wun is not None:
            try:
                _wun.close()
            except Exception:
                pass
        _wun = None
        _recorder = None


# -- read-only helpers --------------------------------------------------------


def grid_list(active_only: bool = True, limit: int = 50) -> list[dict[str, Any]]:
    """List grid bots (active by default)."""
    return get_wun().grid.list(active_only=active_only, limit=limit)


def grid_analyze(code: str) -> dict[str, Any]:
    return get_wun().grid.analyze(code)


def grid_positions(code: str) -> Any:
    return get_wun().grid.positions(code)


def grid_positions_history(code: str) -> Any:
    return get_wun().grid.positions_history(code)


def grid_presets(limit: int = 10) -> Any:
    return get_wun().grid.presets(limit=limit)


def grid_profiles() -> Any:
    return get_wun().grid.profiles()


def api_profiles(*, limit: int | None = None) -> Any:
    """Read API profile state via MCP (no browser)."""
    return get_wun().mcp.api_profiles(limit=limit)


def live_strategies(*, statuses: list[str] | None = None) -> Any:
    return get_wun().mcp.live_strategies(statuses=statuses)


# -- write helpers ------------------------------------------------------------


def grid_create(upsert_payload: dict[str, Any], *, dry_run: bool = True) -> dict[str, Any]:
    """Create a grid bot. ``dry_run=True`` returns the planned request."""
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "transport": "wtclient.GridClient.create",
            "payload_preview": _redact(upsert_payload),
        }
    wun = get_wun()
    market = _market_for(upsert_payload.get("exchangeCode", ""))
    try:
        result = wun.grid.create(upsert_payload, grid_market=market)
        return {"ok": True, "transport": "wtclient.GridClient.create", "result": result}
    except WunError as exc:
        return {"ok": False, "transport": "wtclient.GridClient.create", "error": str(exc)}


def grid_stop(
    code: str, condition: str = "stop_and_close_all", *, dry_run: bool = True
) -> dict[str, Any]:
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "transport": "wtclient.GridClient.stop",
            "code": code,
            "condition": condition,
        }
    wun = get_wun()
    try:
        result = wun.grid.stop(code, condition)
        return {"ok": True, "transport": "wtclient.GridClient.stop", "result": result}
    except WunError as exc:
        return {"ok": False, "transport": "wtclient.GridClient.stop", "error": str(exc)}


def grid_delete(code: str, *, dry_run: bool = True) -> dict[str, Any]:
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "transport": "wtclient.GridClient.delete",
            "code": code,
        }
    wun = get_wun()
    try:
        result = wun.grid.delete(code)
        return {"ok": True, "transport": "wtclient.GridClient.delete", "result": result}
    except WunError as exc:
        return {"ok": False, "transport": "wtclient.GridClient.delete", "error": str(exc)}


def grid_edit(
    code: str,
    upsert_payload: dict[str, Any],
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    body = dict(upsert_payload or {})
    body.pop("gridMarketHint", None)
    market = _market_for(body.get("exchangeCode", ""))
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "transport": "wtclient.GridClient.edit",
            "code": code,
            "market": market,
            "payload_preview": _redact(body),
        }
    wun = get_wun()
    try:
        result = wun.grid.edit(code, body, grid_market=market)
        return {"ok": True, "transport": "wtclient.GridClient.edit", "result": result}
    except WunError as exc:
        return {"ok": False, "transport": "wtclient.GridClient.edit", "error": str(exc)}


# -- discovery/debug helpers --------------------------------------------------


def enable_debug() -> Recorder:
    """Install the recorder + logger on the shared wun; return the recorder."""
    global _recorder
    wun = get_wun()
    _recorder = _debug_trace(wun)
    return _recorder


def catalog() -> EndpointCatalog:
    """Return the recorded endpoint catalog (empty if recorder not enabled)."""
    if _recorder is None:
        return EndpointCatalog([])
    return _recorder.catalog()


def recorder() -> Recorder | None:
    """Return the current recorder (None if debug is not enabled)."""
    return _recorder


@contextmanager
def traced() -> Iterator[Recorder]:
    """Context manager: enable debug for the block, dump catalog at exit.

    Usage::

        from execution.wt_library import traced
        with traced() as rec:
            grid_list()
        print(rec.catalog().to_dict())
    """
    rec = enable_debug()
    try:
        yield rec
    finally:
        pass  # keep recorder for inspection; call reset_wun() to drop


# -- private ------------------------------------------------------------------


_EXCHANGE_MARKET = {"HYPERLIQUID_SWAP": "derivative", "BINANCE": "spot"}


def _market_for(exchange_code: str) -> str:
    return _EXCHANGE_MARKET.get((exchange_code or "").upper(), "derivative")


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip nested secrets/cookies from the payload before logging."""
    if not isinstance(payload, dict):
        return {}
    sensitive = {"profilesCodes", "apiKey", "api_key", "secret", "secretKey"}
    return {k: ("<redacted>" if k in sensitive else v) for k, v in payload.items()}


__all__ = [
    "get_wun",
    "reset_wun",
    "grid_list",
    "grid_analyze",
    "grid_positions",
    "grid_positions_history",
    "grid_presets",
    "grid_profiles",
    "grid_create",
    "grid_stop",
    "grid_delete",
    "grid_edit",
    "api_profiles",
    "live_strategies",
    "enable_debug",
    "catalog",
    "recorder",
    "traced",
]
