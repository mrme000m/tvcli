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
- ``execution/profiles.py`` — the paper-profile bootstrap
  (``exchanges`` passthroughs: list/limits/ensure)

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
from wtclient.errors import WunError, WunApiError  # noqa: E402


def _wun_error_envelope(exc, transport):
    """Build a WunError catch envelope with full diagnostic context.

    The previous shape only carried ``str(exc)`` — which on WunApiError
    was just the message and dropped the status_code / url / response_text
    that the exception already carries. The gap-report saw the daemon
    journal `position-optimizer-error` with only `HTTP 500` and the
    operator had no way to see the WT response body to diagnose.

    Adds (when present on the exception):
      * ``status_code`` (int)
      * ``url`` (str)
      * ``response_text`` (truncated to 1000 chars to keep the envelope
        JSON-safe; the body is the most useful diagnostic for an
        Internal Server Error)
      * ``exception_type`` (the class name, useful when the message
        is generic like "Internal Server Error")
    """
    out = {"ok": False, "transport": transport,
           "error": str(exc),
           "exception_type": type(exc).__name__}
    if isinstance(exc, WunApiError):
        if exc.status_code is not None:
            out["status_code"] = exc.status_code
        if exc.url:
            out["url"] = exc.url
        if exc.response_text:
            out["response_text"] = (exc.response_text or "")[:1000]
    return out

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


# -- exchanges (paper profiles / plan limits) --------------------------------


def exchanges_list_profiles() -> list[dict[str, Any]]:
    """List WT exchange profiles via ``wtclient.ExchangesClient.list_profiles``.

    Returns the ``Profile.as_dict()`` shape: ``id``, ``name``,
    ``exchangeFamily``, ``paperTrading``, ``enabled``, ``marginMode``,
    ``tradeMode``, ``favorite``. Read-only; propagates transport errors so
    the daemon's ``*_safe`` wrappers decide the fallback.
    """
    return get_wun().exchanges.list_profiles() or []


def exchanges_account_limits() -> dict[str, Any]:
    """Plan limits via ``wtclient.ExchangesClient.account_limits``.

    ``{"gridBots": {"active": n, "max": m, ...}, ...}`` — the dashboard
    account-limits view (the tier caps actually enforced by
    ``grid_bots/upsert`` come from ``grid_capacity`` instead).
    """
    return get_wun().exchanges.account_limits() or {}


def create_paper_profile(
    name: str, exchange_family: str, *, dry_run: bool = True
) -> dict[str, Any]:
    """Create one paper profile (no real exchange keys — wtclient submits
    the same random 32-hex placeholders the WT UI sends).

    Binance paper resolves to ``BINANCE_FUTURES`` (USDT-M); there is no
    Binance spot paper mode. Prefer :func:`ensure_paper_profiles` — it
    checks existence first instead of relying on the 400-duplicate reply.
    """
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "transport": "wtclient.ExchangesClient.create_paper_profile",
            "name": name,
            "exchange_family": exchange_family,
        }
    wun = get_wun()
    try:
        result = wun.exchanges.create_paper_profile(name, exchange_family)
        return {"ok": True,
                "transport": "wtclient.ExchangesClient.create_paper_profile",
                "result": result}
    except WunError as exc:
        return _wun_error_envelope(
            exc, "wtclient.ExchangesClient.create_paper_profile")


def ensure_paper_profiles(
    spec: dict[str, list[str]], *, dry_run: bool = True
) -> dict[str, Any]:
    """Ensure the venue-keyed paper profiles exist (idempotent, never raises).

    ``spec`` maps a venue key to the profile names wanted on that venue's
    exchange family, e.g. ``{"hyperliquid": ["demo-hype"],
    "binance": ["demo-bn"]}``. wtclient treats an existing profile of the
    wrong shape (non-paper / family mismatch) as an "error" state and never
    mutates it, so this is safe to re-run on every boot.
    """
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "transport": "wtclient.ExchangesClient.ensure_paper_profiles",
            "spec": dict(spec or {}),
        }
    wun = get_wun()
    try:
        result = wun.exchanges.ensure_paper_profiles(spec)
        return {"ok": True,
                "transport": "wtclient.ExchangesClient.ensure_paper_profiles",
                "result": result}
    except WunError as exc:
        return _wun_error_envelope(
            exc, "wtclient.ExchangesClient.ensure_paper_profiles")
    except Exception as exc:  # belt-and-braces: the ensure path never raises
        return {"ok": False,
                "transport": "wtclient.ExchangesClient.ensure_paper_profiles",
                "error": f"{type(exc).__name__}: {exc}"}


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
        return _wun_error_envelope(exc, "wtclient.GridClient.create")


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
        return _wun_error_envelope(exc, "wtclient.GridClient.stop")


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
        return _wun_error_envelope(exc, "wtclient.GridClient.delete")


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
        return _wun_error_envelope(exc, "wtclient.GridClient.edit")


def grid_set_exits(
    code: str,
    *,
    take_profit: float | None = None,
    stop_loss: float | None = None,
    pnl_compare_type: str | None = None,
    trailing_activation: float | None = None,
    trailing_execute: float | None = None,
    positions_trailing_stop: bool | None = None,
    positions_stop_loss_pct: float | None = None,
    order_type: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Edit ONLY the exit/risk fields of an existing (active) grid bot.

    Thin wrapper over ``wtclient.GridClient.set_exits`` (live-verified
    2026-09-07): the edit goes through the same upsert path as
    :func:`grid_edit` but overlays only the provided exit fields, so the
    bot is NOT stopped/restarted — exit edits apply live to an active bot.
    KWarg semantics (mirrors wtclient):

    * ``take_profit`` / ``stop_loss`` — $ thresholds on cumulative Total
      PnL (``stop_loss`` is a POSITIVE magnitude; the engine models risk
      as a negative USD level and sends ``abs()``),
    * ``pnl_compare_type`` — "total" | "unrealized", sets both
      ``stopLossPnlCompareType`` and ``trailingStopPnlCompareType``,
    * ``trailing_activation`` / ``trailing_execute`` — trailing arm /
      give-back thresholds (passed through verbatim, same convention as
      grid_adapter.compute_upsert),
    * ``positions_trailing_stop`` — True maps ``strategyProfitCondition``
      to "trailing_stop" (per-position trailing), False to "take_profit",
    * ``positions_stop_loss_pct`` — UI percent (5 → 0.05 ratio) per-line
      stop loss,
    * ``order_type`` — "market" | "limit" (pump protection order type).

    ``dry_run=True`` (the default) returns the planned call envelope
    WITHOUT touching wtclient; never raises (WunError / any exception →
    ``{"ok": False, "error": ...}``, same shape as grid_edit).
    """
    kwargs = {
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "pnl_compare_type": pnl_compare_type,
        "trailing_activation": trailing_activation,
        "trailing_execute": trailing_execute,
        "positions_trailing_stop": positions_trailing_stop,
        "positions_stop_loss_pct": positions_stop_loss_pct,
        "order_type": order_type,
    }
    payload = {k: v for k, v in kwargs.items() if v is not None}
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "transport": "wtclient.GridClient.set_exits",
            "code": code,
            "payload": payload,
        }
    wun = get_wun()
    try:
        result = wun.grid.set_exits(code, **payload)
        return {"ok": True, "transport": "wtclient.GridClient.set_exits",
                "result": result}
    except WunError as exc:
        return _wun_error_envelope(exc, "wtclient.GridClient.set_exits")
    except Exception as exc:  # belt-and-braces: never raise (grid_edit shape)
        return {"ok": False, "transport": "wtclient.GridClient.set_exits",
                "error": f"{type(exc).__name__}: {exc}"}


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
    "grid_set_exits",
    "api_profiles",
    "live_strategies",
    "exchanges_list_profiles",
    "exchanges_account_limits",
    "create_paper_profile",
    "ensure_paper_profiles",
    "enable_debug",
    "catalog",
    "recorder",
    "traced",
]
