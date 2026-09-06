"""wtclient.discovery — endpoint catalog + recording + probing.

Bakes the "discover before you commit" loop into the client:

1. :class:`Recorder` wraps any transport and writes redacted request/response
   rows to an in-memory ring buffer or a JSONL file. Useful while clicking
   through the WunderTrading cabinet so we can later replay the API.
2. :class:`EndpointCatalog` aggregates the rows into a queryable map
   (by surface / method / path / status / content-type) with rough schema
   inference for JSON bodies.
3. :class:`Probe` tries an arbitrary ``(method, path)`` across the available
   transports (browser → session → raw) and reports the first surface that
   answered 2xx, so callers can pick the cheapest working transport.
4. :func:`find_endpoints` is the convenience entry-point — run a recorder
   against a session for ``N`` seconds, return the catalog, stop.

All instrumentation here is **zero-cost when disabled** (a Recorder attached
to a transport is a single ``is None`` check per request).
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .errors import WunCloudflareError, WunTransportError
from .response import Response

# -- transport-side redactor --------------------------------------------------

_REDACT_HEADERS = {
    "x-api-key",
    "x-secret-key",
    "x-w-csrf-token",
    "x-csrf-token",
    "authorization",
    "cookie",
    "set-cookie",
    "phpsessid",
    "cf_clearance",
}

_BODY_KEYS = re.compile(
    r"(?i)\b(api[_-]?key|secret[_-]?key|client[_-]?secret|access[_-]?token|password|"
    r"csrf[_-]?token|phpsessid|cf_clearance|authorization)\b"
)


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        k: ("<redacted>" if k.lower() in _REDACT_HEADERS else v) for k, v in headers.items()
    }


def _redact_body(body: Any) -> Any:
    """Redact sensitive keys at any nesting depth."""
    if not isinstance(body, dict):
        if isinstance(body, list):
            return [_redact_body(b) for b in body]
        return body
    out: dict[str, Any] = {}
    for k, v in body.items():
        if _BODY_KEYS.search(k):
            out[k] = "<redacted>"
        else:
            out[k] = _redact_body(v)
    return out


# -- recorded row -------------------------------------------------------------


@dataclass
class RecordedRequest:
    """One recorded HTTP exchange.

    Always safe to JSON-serialize (headers/body are redacted).
    """

    method: str
    url: str
    status: int
    surface: str
    started_at: float
    duration_ms: float
    request_headers: dict[str, str]
    request_body: Any | None
    response_headers: dict[str, str]
    response_body_preview: str  # first ~500 chars
    response_content_type: str
    ok: bool

    def to_row(self) -> dict[str, Any]:
        return {
            "ts": self.started_at,
            "method": self.method,
            "url": self.url,
            "status": self.status,
            "surface": self.surface,
            "duration_ms": round(self.duration_ms, 2),
            "ok": self.ok,
            "request": {
                "headers": self.request_headers,
                "body": self.request_body,
            },
            "response": {
                "headers": self.response_headers,
                "preview": self.response_body_preview,
                "content_type": self.response_content_type,
            },
        }


# -- recorder -----------------------------------------------------------------


class Recorder:
    """Wrap a transport-like object and emit :class:`RecordedRequest` rows.

    Usage::

        rec = Recorder()
        wun = WunderTrading(browser=True)
        wun.grid._client_set_transport(rec.wrap(wun.grid.transport))  # see below
        # … drive UI …
        rows = rec.rows
        catalog = rec.catalog()

    Or use :class:`wtclient.WunderTracing` for a higher-level facade.
    """

    def __init__(
        self,
        *,
        sink: Callable[[RecordedRequest], None] | None = None,
        max_rows: int = 5000,
    ) -> None:
        self._sink = sink
        self._max_rows = max_rows
        self._rows: list[RecordedRequest] = []

    @property
    def rows(self) -> list[RecordedRequest]:
        return list(self._rows)

    def reset(self) -> None:
        self._rows.clear()

    def catalog(self) -> "EndpointCatalog":
        return EndpointCatalog(self._rows)

    def to_jsonl(self, path: str | Path) -> Path:
        p = Path(path)
        with p.open("w") as f:
            for r in self._rows:
                f.write(json.dumps(r.to_row()) + "\n")
        return p

    @staticmethod
    def from_jsonl(path: str | Path) -> list[RecordedRequest]:
        """Re-import a previously-dumped JSONL (lossy; bodies are previews)."""
        rows: list[RecordedRequest] = []
        for line in Path(path).read_text().splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            req = raw.get("request") or {}
            resp = raw.get("response") or {}
            rows.append(
                RecordedRequest(
                    method=raw["method"],
                    url=raw["url"],
                    status=raw["status"],
                    surface=raw.get("surface", "unknown"),
                    started_at=raw.get("ts", 0.0),
                    duration_ms=raw.get("duration_ms", 0.0),
                    request_headers=req.get("headers") or {},
                    request_body=req.get("body"),
                    response_headers=resp.get("headers") or {},
                    response_body_preview=resp.get("preview", ""),
                    response_content_type=resp.get("content_type", ""),
                    ok=raw.get("ok", False),
                )
            )
        return rows

    def record(self, row: RecordedRequest) -> None:
        self._rows.append(row)
        if len(self._rows) > self._max_rows:
            # drop oldest (FIFO ring)
            del self._rows[: len(self._rows) - self._max_rows]
        if self._sink is not None:
            try:
                self._sink(row)
            except Exception:  # never let the sink kill the caller
                pass

    # transport wrapping ------------------------------------------------------

    def wrap(self, transport: Any) -> "RecordedTransport":
        """Wrap any transport so each call goes through :meth:`record`."""
        return RecordedTransport(transport, self)

    def __call__(
        self,
        row: RecordedRequest | None = None,
        *,
        method: str | None = None,
        url: str | None = None,
        surface: str | None = None,
        request_headers: dict[str, str] | None = None,
        request_body: Any | None = None,
        response: Response | None = None,
        duration_ms: float | None = None,
    ) -> RecordedRequest:
        """Record one exchange.

        Two call styles::

            rec(row)                            # direct: a pre-built RecordedRequest
                                                 # (we redact on the way in so it is
                                                 # always safe to dump)
            rec(method=..., url=..., response=...) # indirect: build from parts

        Always returns a *new* :class:`RecordedRequest` whose headers/body
        have been redacted (the caller's row is not mutated).
        """
        if row is not None:
            if not isinstance(row, RecordedRequest):
                raise TypeError(f"expected RecordedRequest, got {type(row).__name__}")
            # build a redacted copy so callers that re-pass the same row still
            # get a fresh, safe one
            redacted = RecordedRequest(
                method=row.method,
                url=row.url,
                status=row.status,
                surface=row.surface,
                started_at=row.started_at,
                duration_ms=row.duration_ms,
                request_headers=_redact_headers(row.request_headers),
                request_body=_redact_body(row.request_body) if row.request_body is not None else None,
                response_headers=_redact_headers(row.response_headers),
                response_body_preview=row.response_body_preview,
                response_content_type=row.response_content_type,
                ok=row.ok,
            )
            self.record(redacted)
            return redacted
        if method is None or url is None or response is None:
            raise TypeError(
                "rec(...) needs either a RecordedRequest or (method, url, response)"
            )
        built = RecordedRequest(
            method=method.upper(),
            url=url,
            status=response.status_code,
            surface=surface or "unknown",
            started_at=time.time(),
            duration_ms=duration_ms or 0.0,
            request_headers=_redact_headers(request_headers or {}),
            request_body=_redact_body(request_body) if request_body is not None else None,
            response_headers=_redact_headers({k: v for k, v in response.headers.items()}),
            response_body_preview=(response.text or "")[:500],
            response_content_type=response.headers.get("content-type", ""),
            ok=response.ok,
        )
        self.record(built)
        return built


# -- recorded transport wrapper ----------------------------------------------


class RecordedTransport:
    """A drop-in wrapper around any BaseTransport.

    Behavior:
      - delegates ``request()`` to the inner transport
      - captures the request + response into the parent :class:`Recorder`
      - records zero-overhead when ``recorder._sink`` is None and ``rows`` is
        empty (just one method call + Response copy)
    """

    def __init__(self, inner: Any, recorder: Recorder, surface: str | None = None) -> None:
        self._inner = inner
        self._recorder = recorder
        self._surface = surface or getattr(inner, "name", "transport")

    @property
    def inner(self) -> Any:
        return self._inner

    @property
    def name(self) -> str:
        return f"recorded:{self._surface}"

    def request(
        self,
        method: str,
        url: str,
        *,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        merged_headers = dict(getattr(self._inner, "_last_headers", {}) or {})
        if headers:
            merged_headers.update(headers)
        start = time.monotonic()
        try:
            response = self._inner.request(method, url, body=body, headers=headers)
        except (WunCloudflareError, WunTransportError) as exc:
            # record synthetic failure row so discovery sees it
            duration = (time.monotonic() - start) * 1000.0
            synth = Response(
                status_code=getattr(exc, "status_code", 0) or 0,
                headers={},
                text=str(exc),
                url=url,
                method=method,
            )
            self._recorder(
                method=method,
                url=url,
                surface=self._surface,
                request_headers=merged_headers,
                request_body=body,
                response=synth,
                duration_ms=duration,
            )
            raise
        duration = (time.monotonic() - start) * 1000.0
        self._recorder(
            method=method,
            url=url,
            surface=self._surface,
            request_headers=merged_headers,
            request_body=body,
            response=response,
            duration_ms=duration,
        )
        return response

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()


# -- endpoint catalog ---------------------------------------------------------


@dataclass
class EndpointSummary:
    """Aggregated view of one observed endpoint."""

    surface: str
    method: str
    path: str
    calls: int
    statuses: Counter = field(default_factory=Counter)
    last_status: int = 0
    last_seen: float = 0.0
    avg_duration_ms: float = 0.0
    success_rate: float = 0.0
    content_types: Counter = field(default_factory=Counter)
    request_body_keys: set[str] = field(default_factory=set)
    response_top_level_keys: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "method": self.method,
            "path": self.path,
            "calls": self.calls,
            "statuses": dict(self.statuses),
            "last_status": self.last_status,
            "last_seen": self.last_seen,
            "avg_duration_ms": round(self.avg_duration_ms, 2),
            "success_rate": round(self.success_rate, 3),
            "content_types": dict(self.content_types),
            "request_body_keys": sorted(self.request_body_keys),
            "response_top_level_keys": sorted(self.response_top_level_keys),
        }


_PATH_KEY_RE = re.compile(r"/([\w\-]+)/?")


def _normalize_path(url: str) -> str:
    """Drop query string + numeric IDs so paths aggregate sensibly.

    Examples:
        /open_api/api_profiles/abc123def456 → /open_api/api_profiles/<id>
        /en/trader/grid_bots/abc/stop       → /en/trader/grid_bots/<id>/stop
        /open_api/strategies/trade          → /open_api/strategies/trade
        /en/trader/grid_bots/grid           → /en/trader/grid_bots/grid
    """
    # url may be a full URL; we keep only the path
    path = url.split("://", 1)[-1]
    if "/" in path:
        slash = path.find("/")
        path = path[slash:]
    if "?" in path:
        path = path.split("?", 1)[0]
    # collapse long alphanumeric IDs that look like codes/uuids; keep short
    # words and well-known resource names ("trade", "grid", "upsert", …) intact
    COMMON_RESOURCE_NAMES = {
        "trade", "trades", "grid", "grids", "upsert", "delete", "stop", "restart",
        "close-all", "cancel", "positions", "history", "presets", "orders",
        "market", "exchanges", "profiles", "bots", "bot",
        "open_api", "en", "trader", "supported-markets", "all-markets",
        "low-high", "last", "ohlc", "find_notional_prices", "fetch_profiles_leverage",
        "swing", "market_enter", "market_close",
    }

    def repl(match: re.Match[str]) -> str:
        s = match.group(0)
        if s.lower() in COMMON_RESOURCE_NAMES:
            return s
        # pure UUID (8-4-4-4-12 with hyphens) → <id>
        if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", s):
            return "<id>"
        # hex blob >= 16 chars → <id>
        if re.fullmatch(r"[0-9a-fA-F]{16,}", s):
            return "<id>"
        # mixed alphanumeric >= 20 chars (likely a code/UUID without hyphens) → <id>
        if re.fullmatch(r"[A-Za-z0-9_-]{20,}", s):
            return "<id>"
        return s

    path = re.sub(r"[A-Za-z0-9_\-]{8,}", repl, path)
    return path


class EndpointCatalog:
    """Index of observed endpoints, grouped by (surface, method, path)."""

    def __init__(self, rows: Iterable[RecordedRequest] | None = None) -> None:
        self._summaries: dict[tuple[str, str, str], EndpointSummary] = {}
        for r in rows or ():
            self.add(r)

    def add(self, row: RecordedRequest) -> None:
        path = _normalize_path(row.url)
        key = (row.surface, row.method, path)
        s = self._summaries.get(key)
        if s is None:
            s = EndpointSummary(
                surface=row.surface,
                method=row.method,
                path=path,
                calls=0,
            )
            self._summaries[key] = s
        s.calls += 1
        s.statuses[row.status] += 1
        s.last_status = row.status
        s.last_seen = row.started_at
        # rolling avg
        s.avg_duration_ms = ((s.avg_duration_ms * (s.calls - 1)) + row.duration_ms) / s.calls
        s.success_rate = sum(c for st, c in s.statuses.items() if 200 <= st < 300) / s.calls
        if row.response_content_type:
            s.content_types[row.response_content_type.split(";", 1)[0]] += 1
        if isinstance(row.request_body, dict):
            s.request_body_keys.update(row.request_body.keys())
        # parse response preview if it's JSON
        try:
            body = json.loads(row.response_body_preview)
            if isinstance(body, dict):
                s.response_top_level_keys.update(body.keys())
            elif isinstance(body, list) and body and isinstance(body[0], dict):
                s.response_top_level_keys.update(body[0].keys())
        except (json.JSONDecodeError, ValueError):
            pass

    def all(self) -> list[EndpointSummary]:
        return sorted(
            self._summaries.values(),
            key=lambda s: (-s.calls, s.surface, s.method, s.path),
        )

    def by_surface(self, surface: str) -> list[EndpointSummary]:
        return [s for s in self.all() if s.surface == surface]

    def __iter__(self) -> Iterator[EndpointSummary]:
        return iter(self.all())

    def __len__(self) -> int:
        return len(self._summaries)

    def to_dict(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.all()]


# -- probe --------------------------------------------------------------------


@dataclass
class ProbeResult:
    """One transport's answer to a probe attempt."""

    surface: str
    ok: bool
    status: int
    elapsed_ms: float
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "ok": self.ok,
            "status": self.status,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "error": self.error,
        }


class Probe:
    """Try an arbitrary ``(method, path)`` across available transports.

    Surfaces are tried in the order: ``browser``, ``session``, ``mcp``,
    ``hmac``, ``market`` (raw httpx last). The first 2xx wins; everything
    else is reported so the caller can pick a different surface.

    Usage::

        wun = WunderTrading(browser=True)
        probe = Probe(wun)
        results = probe.try_method("GET", "/open_api/api_profiles?limit=5")
        for r in results:
            print(r.surface, r.status, r.error)
    """

    def __init__(self, wun: Any) -> None:
        self._wun = wun

    def try_method(self, method: str, path: str) -> list[ProbeResult]:
        results: list[ProbeResult] = []
        # browser first (the one that always works for fingerprinted surfaces)
        try:
            results.append(self._try_one("browser", method, path, self._wun.grid.transport))
        except Exception as e:
            results.append(
                ProbeResult("browser", False, 0, 0.0, error=str(e))
            )
        # session
        try:
            from .transport.session import SessionTransport

            sess = SessionTransport(self._wun.secrets.require_session())
            try:
                results.append(self._try_one("session", method, path, sess))
            finally:
                sess.close()
        except Exception as e:
            results.append(
                ProbeResult("session", False, 0, 0.0, error=str(e))
            )
        # raw market
        try:
            from .transport.market import MarketTransport

            mt = MarketTransport()
            try:
                results.append(self._try_one("market", method, path, mt))
            finally:
                mt.close()
        except Exception as e:
            results.append(
                ProbeResult("market", False, 0, 0.0, error=str(e))
            )
        # hmac (only valid for /open_api paths)
        if path.startswith("/open_api"):
            try:
                key, secret = self._wun.secrets.require_api_keys()
                from .transport.hmac import OpenApiTransport

                ht = OpenApiTransport(key, secret)
                try:
                    results.append(self._try_one("hmac", method, path, ht))
                finally:
                    ht.close()
            except Exception as e:
                results.append(
                    ProbeResult("hmac", False, 0, 0.0, error=str(e))
                )
        return results

    @staticmethod
    def _try_one(surface: str, method: str, path: str, transport: Any) -> ProbeResult:
        start = time.monotonic()
        try:
            response = transport.request(method, path)
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000.0
            return ProbeResult(surface, False, 0, elapsed, error=f"{e.__class__.__name__}: {e}")
        elapsed = (time.monotonic() - start) * 1000.0
        return ProbeResult(surface, response.ok, response.status_code, elapsed)

    def first_working(self, method: str, path: str) -> ProbeResult | None:
        for r in self.try_method(method, path):
            if r.ok:
                return r
        return None


# -- find_endpoints: convenience entry-point ---------------------------------


def find_endpoints(
    wun: Any,
    *,
    duration: float = 30.0,
    path_filter: Callable[[str], bool] | None = None,
) -> EndpointCatalog:
    """Drive a recording session for ``duration`` seconds, return the catalog.

    The caller is expected to be driving the UI (or running another stimulus)
    while this recorder runs. A typical pattern::

        rec = Recorder()
        # inject recorder into the right transport
        wun.grid.transport = rec.wrap(wun.grid.transport)
        deadline = time.time() + 60
        while time.time() < deadline:
            # … drive UI / wait …
            pass
        catalog = rec.catalog()
    """
    # NOTE: this function is a stub that documents the pattern. Actual UI
    # driving belongs in the calling harness (the headful CDP loop is
    # inherently non-blocking; a wrapper here would be misleading).
    raise NotImplementedError(
        "find_endpoints is a documentation stub — drive UI externally and "
        "call Recorder.catalog() on the populated rows. See wtclient.discovery."
    )


# -- public surface index -----------------------------------------------------


PUBLIC_SURFACES: dict[str, list[str]] = {
    "hmac": [
        "GET /open_api/exchanges",
        "GET /open_api/markets",
        "GET /open_api/api_profiles",
        "GET /open_api/strategies/live",
        "GET /open_api/strategies/history",
        "GET /open_api/strategies/{id}",
        "GET /open_api/strategies/{id}/orders",
        "POST /open_api/strategies/trade",
        "PATCH /open_api/strategies/trade",
        "PUT /open_api/strategies/{id}/market_enter",
        "POST /open_api/strategies/{id}/swing",
        "DELETE /open_api/strategies/{id}/cancel",
        "DELETE /open_api/strategies/{id}/market_close",
    ],
    "mcp": [
        "get_supported_exchanges",
        "get_exchange_markets",
        "get_api_profiles",
        "get_live_strategies",
        "get_strategies_history",
        "export_strategies_history",
        "get_strategy",
        "get_strategy_orders_history",
        "export_strategy_orders_history",
        "place_strategy_trade",
        "place_strategy_market_enter",
        "place_strategy_swing",
        "edit_trade_strategy",
        "cancel_strategy",
        "close_strategy_market",
    ],
    "session": [
        "GET /en/trader/grid_bots/upsert",
        "GET /en/trader/grid_bots/grid",
        "GET /en/trader/grid_bots/presets",
        "POST /en/trader/grid_bots/upsert",
        "POST /en/trader/grid_bots/{code}/stop",
        "POST /en/trader/grid_bots/{code}/restart",
        "POST /en/trader/grid_bots/{code}/close-all",
        "DELETE /en/trader/grid_bots/{code}/delete",
        "GET /en/trader/grid_bots/{code}/positions/grid",
        "GET /en/trader/grid_bots/{code}/positions-history/grid",
        "POST /en/trader/my-exchanges/api-profile/fetch_profiles_leverage",
        "POST /en/trader/grid_bots/find_notional_prices",
        "GET /en/trader/my-exchanges/master-api-profile/grid",
        "POST /en/trader/my-exchanges/master-api-profile/upsert",
        "GET /en/trader/dashboard/account-limits",
    ],
    "market": [
        "GET /all-markets",
        "GET /market",
        "GET /ohlc",
        "GET /ohlc/last",
        "GET /ohlc/low-high",
        "GET /supported-markets",
    ],
}


def surface_index() -> dict[str, list[str]]:
    """Return the known endpoint catalog (used by tooling/help).

    Sourced from the live docs and the verified references in
    ``.agents/skills/wundertrading/references/``. Update this when a new
    endpoint is added.
    """
    return {k: list(v) for k, v in PUBLIC_SURFACES.items()}


__all__ = [
    "Recorder",
    "RecordedRequest",
    "RecordedTransport",
    "EndpointCatalog",
    "EndpointSummary",
    "Probe",
    "ProbeResult",
    "find_endpoints",
    "surface_index",
    "PUBLIC_SURFACES",
]
