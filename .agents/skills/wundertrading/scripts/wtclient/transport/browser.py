"""Optional headful-browser transport (CDP fetch-in-page).

This is the reliable backend for the Cloudflare-fingerprinted ``/en/trader``
and ``:2087`` surfaces. It keeps the real browser TLS fingerprint by
evaluating ``fetch()`` inside a logged-in CloakBrowser page over CDP, exactly
like ``browser-debug/wt-grid.mjs``.

The ``websockets`` dependency is imported lazily so the rest of the package
works without it. A running CloakBrowser with a WunderTrading tab is required.
"""
from __future__ import annotations

import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..config import DEFAULT_TIMEOUT
from ..errors import WunConfigError, WunTransportError
from ..response import Response
from .base import BaseTransport

_CDP_BASE = "http://127.0.0.1:9222"


def _require_websockets():
    try:
        import websockets  # type: ignore
    except ImportError as exc:
        raise WunConfigError(
            "BrowserTransport requires the optional 'websockets' package",
            remediation="install it with: uv pip install websockets",
        ) from exc
    return websockets


class BrowserTransport(BaseTransport):
    """CDP transport that runs ``fetch()`` inside a logged-in browser page."""

    name = "browser"

    def __init__(
        self,
        *,
        cdp_base: str = _CDP_BASE,
        timeout: float = 20.0,
        user_agent: str | None = None,
    ) -> None:
        super().__init__(timeout=timeout, user_agent=user_agent or "browser")
        self.cdp_base = cdp_base

    # -- async internals -------------------------------------------------
    async def _find_page_async(self) -> dict[str, Any]:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.cdp_base}/json")
                resp.raise_for_status()
                targets = resp.json()
        except Exception as exc:
            raise WunTransportError(
                f"cannot reach CloakBrowser CDP at {self.cdp_base}: {exc}",
                remediation="start it with: node browser-debug/launch.mjs",
                cause=exc,
            ) from exc
        pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        for target in pages:
            if "wundertrading.com" in (target.get("url") or ""):
                return target
        if pages:
            return pages[0]
        raise WunConfigError(
            "no browser page target on CDP",
            remediation="run node browser-debug/wt.mjs to open a WunderTrading tab",
        )

    async def _cdp_eval_async(
        self,
        ws_url: str,
        expression: str,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        websockets = _require_websockets()
        try:
            async with websockets.connect(ws_url, max_size=None) as ws:
                await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
                    "expression": expression,
                    "awaitPromise": True,
                    "returnByValue": True,
                }}))
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout or self.timeout)
        except Exception as exc:
            raise WunTransportError(
                f"CDP Runtime.evaluate failed: {exc}",
                remediation="refresh the wedged browser tab and retry",
                cause=exc,
            ) from exc
        msg = json.loads(raw)
        if msg.get("error"):
            raise WunTransportError(f"CDP error: {msg['error']}")
        return msg

    def _fetch_js(self, method: str, path: str, body: Any, *, credentials: str) -> str:
        body_js = "undefined" if body is None else json.dumps(body)
        return (
            "(async () => {\n"
            f"  const method = {json.dumps(method)};\n"
            f"  const path = {json.dumps(path)};\n"
            f"  const body = {body_js};\n"
            "  const headers = { 'Accept': 'application/json' };\n"
            "  if (body !== undefined) headers['Content-Type'] = 'application/json';\n"
            "  if (!['GET','HEAD'].includes(method)) headers['X-W-CSRF-Token'] = window.baseServerConfig.appCsrfToken;\n"
            "  try {\n"
            f"    const r = await fetch(path, {{ method, headers, credentials: {json.dumps(credentials)}, "
            "      ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });\n"
            "    const text = await r.text();\n"
            "    let j = null; try { j = JSON.parse(text); } catch {}\n"
            "    return { status: r.status, ok: r.ok, json: j, text: j ? undefined : text.slice(0, 1200) };\n"
            "  } catch (e) { return { status: 0, ok: false, error: String(e) }; }\n"
            "})()"
        )

    def _market_js(self, url: str) -> str:
        return (
            "(async () => {\n"
            "  try {\n"
            f"    const r = await fetch({json.dumps(url)}, {{ credentials: 'omit' }});\n"
            "    const text = await r.text();\n"
            "    let j = null; try { j = JSON.parse(text); } catch {}\n"
            "    return { status: r.status, ok: r.ok, json: j, text: j ? undefined : text.slice(0, 1200) };\n"
            "  } catch (e) { return { status: 0, ok: false, error: String(e) }; }\n"
            "})()"
        )

    async def fetch_in_page_async(
        self,
        method: str,
        path: str,
        body: Any = None,
    ) -> dict[str, Any]:
        page = await self._find_page_async()
        msg = await self._cdp_eval_async(
            page["webSocketDebuggerUrl"],
            self._fetch_js(method, path, body, credentials="include"),
        )
        value = msg.get("result", {}).get("result", {}).get("value")
        if value is None:
            details = msg.get("result", {}).get("exceptionDetails")
            raise WunTransportError(
                f"fetch-in-page returned no value: {json.dumps(details)[:300] if details else 'empty'}"
            )
        return value

    async def fetch_market_async(self, url: str) -> dict[str, Any]:
        page = await self._find_page_async()
        msg = await self._cdp_eval_async(page["webSocketDebuggerUrl"], self._market_js(url))
        value = msg.get("result", {}).get("result", {}).get("value")
        if value is None:
            raise WunTransportError("market fetch-in-page returned no value")
        return value

    # -- sync wrappers ---------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        value = _run_sync(self.fetch_in_page_async(method, path, body))
        return _response_from_page(value, method=method, url=path)

    def fetch_market(self, url: str) -> Response:
        value = _run_sync(self.fetch_market_async(url))
        return _response_from_page(value, method="GET", url=url)


def _run_sync(coro: Any) -> Any:
    """Run a coroutine to completion, safe from both sync and async callers."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # An event loop is already running (e.g. the agent REPL): run the coroutine
    # in a worker thread with its own loop instead of blocking the caller's.
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


def _response_from_page(value: dict[str, Any], *, method: str, url: str) -> Response:
    if value.get("error"):
        return Response(status_code=0, headers={}, text=value["error"], url=url, method=method)
    status = int(value.get("status") or 0)
    if value.get("json") is not None:
        text = json.dumps(value["json"])
    else:
        text = value.get("text") or ""
    return Response(status_code=status, headers={}, text=text, url=url, method=method)


__all__ = ["BrowserTransport"]
