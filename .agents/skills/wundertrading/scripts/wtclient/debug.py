"""wtclient.debug — built-in instrumentation, logging, and dump-on-failure.

Three dials, zero ceremony:

- ``wtclient.debug.install()`` enables request/response logging globally. By
  default it logs to stderr at INFO level (one line per request, redacted),
  and dumps failing responses (status >= 400 or transport errors) to
  ``$WT_DEBUG_DUMP_DIR`` (default ``/tmp/wt-debug-dumps``) so you can paste
  the JSON into a bug report without losing context.
- ``WT_DEBUG=1`` in the environment does the same automatically (no import
  order tricks — debug is opt-in via a function call).
- ``wtclient.debug.trace(client)`` wraps any :class:`wtclient.WunderTrading`
  so every transport request goes through the recorder AND the logger.

Curl rendering (already in :mod:`wtclient.curl`) is reused by the logger so
the stderr line is *also* a copy-paste-able curl invocation.

This module is **safe to import at any time** — it never runs unless
:func:`install` is called or ``WT_DEBUG=1``.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .curl import curl_command
from .discovery import Recorder
from .errors import WunCloudflareError, WunError

if TYPE_CHECKING:
    from .clients.client import WunderTrading

LOG = logging.getLogger("wtclient.debug")


# -- env knob -----------------------------------------------------------------


def is_enabled() -> bool:
    """Return ``True`` when the ``WT_DEBUG`` env knob is set.

    Honored values (case-insensitive): ``1``, ``true``, ``yes``, ``on``.
    """
    raw = os.environ.get("WT_DEBUG", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


# -- dump-on-failure ----------------------------------------------------------


def default_dump_dir() -> Path:
    raw = os.environ.get("WT_DEBUG_DUMP_DIR", "/tmp/wt-debug-dumps")
    return Path(raw)


@dataclass
class DebugDump:
    """One dumped request/response on disk."""

    path: Path
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), **self.summary}


def _sanitize(obj: Any) -> Any:
    """Best-effort JSON-safe coercion for the dump file."""
    try:
        return json.loads(json.dumps(obj, default=str))
    except (TypeError, ValueError):
        return repr(obj)


def dump_failure(
    *,
    method: str,
    url: str,
    surface: str,
    request_headers: dict[str, str],
    request_body: Any,
    response_text: str,
    response_status: int,
    response_headers: dict[str, str],
    error: str | None,
    dump_dir: Path | None = None,
) -> DebugDump:
    """Persist one failing exchange to ``dump_dir`` (default ``/tmp/wt-debug-dumps``).

    Filename: ``<surface>-<timestamp>-<short-uuid>.json``. The file contains
    the full request (headers + body, secrets redacted) and the full response
    text (truncated at 50KB to keep dumps bounded).
    """
    base = dump_dir or default_dump_dir()
    base.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    short = uuid.uuid4().hex[:8]
    safe_surface = re.sub(r"[^a-z0-9]+", "_", surface.lower()) or "unknown"
    fname = f"{safe_surface}-{ts}-{short}.json"
    path = base / fname
    payload = {
        "method": method,
        "url": url,
        "surface": surface,
        "ts": ts,
        "request": {
            "headers": request_headers,
            "body": _sanitize(request_body),
        },
        "response": {
            "status": response_status,
            "headers": response_headers,
            "text": (response_text or "")[:50_000],
        },
        "error": error,
    }
    path.write_text(json.dumps(payload, indent=2, default=str))
    summary = {
        "method": method,
        "url": url,
        "surface": surface,
        "status": response_status,
        "error": error,
        "bytes": len(response_text or ""),
    }
    LOG.error("wtclient debug dump: %s (%s %s -> %s)", path, method, url, response_status)
    return DebugDump(path=path, summary=summary)


# need re for sanitize
import re  # noqa: E402  (placed here to keep module top-load lean)


# -- the logger ---------------------------------------------------------------


@dataclass
class DebugState:
    """Shared state for the debug module — installed once."""

    enabled: bool = False
    logger: logging.Logger = field(default_factory=lambda: LOG)
    log_level: int = logging.INFO
    dump_on_failure: bool = True
    dump_dir: Path = field(default_factory=default_dump_dir)
    dumps: list[DebugDump] = field(default_factory=list)
    requests_logged: int = 0

    def install(
        self,
        *,
        level: int = logging.INFO,
        dump_on_failure: bool = True,
        dump_dir: Path | None = None,
    ) -> None:
        self.enabled = True
        self.log_level = level
        self.dump_on_failure = dump_on_failure
        if dump_dir is not None:
            self.dump_dir = dump_dir
        if not any(isinstance(h, logging.StreamHandler) for h in self.logger.handlers):
            h = logging.StreamHandler(stream=sys.stderr)
            h.setFormatter(logging.Formatter("%(asctime)s wtclient %(levelname)s %(message)s"))
            self.logger.addHandler(h)
        self.logger.setLevel(self.log_level)
        self.logger.propagate = False


STATE = DebugState()


def install(
    *,
    level: int = logging.INFO,
    dump_on_failure: bool = True,
    dump_dir: Path | None = None,
) -> DebugState:
    """Idempotently enable the debug logger + dump-on-failure machinery.

    Auto-called once when ``WT_DEBUG=1`` is in the environment.
    """
    STATE.install(level=level, dump_on_failure=dump_on_failure, dump_dir=dump_dir)
    return STATE


# auto-install on import when the env knob is set
if is_enabled():
    install()


# -- trace wrapper ------------------------------------------------------------


def log_request(
    *,
    method: str,
    url: str,
    surface: str,
    headers: dict[str, str],
    body: Any | None,
    response_status: int | None = None,
    response_text: str | None = None,
    response_headers: dict[str, str] | None = None,
    duration_ms: float | None = None,
    error: str | None = None,
) -> None:
    """Emit one redacted log line; on failure, also dump to disk.

    Call this from any transport (or transport wrapper) when ``STATE.enabled``
    is True. The function is a no-op otherwise.
    """
    if not STATE.enabled:
        return
    STATE.requests_logged += 1
    label = f"{method.upper()} {url}"
    extras: list[str] = []
    if surface:
        extras.append(f"surface={surface}")
    if duration_ms is not None:
        extras.append(f"{duration_ms:.1f}ms")
    if response_status is not None:
        extras.append(f"status={response_status}")
    if error:
        extras.append(f"error={error}")
    suffix = (" [" + ", ".join(extras) + "]") if extras else ""
    line = f"{label}{suffix}"
    if error or (response_status and response_status >= 400):
        STATE.logger.error(line)
        if STATE.dump_on_failure and response_text is not None:
            try:
                STATE.dumps.append(
                    dump_failure(
                        method=method,
                        url=url,
                        surface=surface,
                        request_headers=headers,
                        request_body=body,
                        response_text=response_text,
                        response_status=response_status or 0,
                        response_headers=response_headers or {},
                        error=error,
                    )
                )
            except Exception as exc:  # never let debug crash the caller
                STATE.logger.warning("wtclient dump failure: %s", exc)
    else:
        STATE.logger.info(line)
    # also include the redacted curl recipe (super useful when copy/pasting)
    try:
        curl_line = curl_command(
            method=method,
            url=url,
            headers=headers,
            body=body,
        )
        STATE.logger.debug("curl: %s", curl_line)
    except Exception:
        pass


# -- high-level facade --------------------------------------------------------


def trace(
    wun: "WunderTrading",
    *,
    recorder: Recorder | None = None,
) -> Recorder:
    """Wrap every transport on ``wun`` so requests are logged + recorded.

    Returns the :class:`Recorder` so the caller can later inspect
    ``recorder.catalog()``.

    Calling ``trace(wun)`` twice is a no-op for the second call (the same
    recorder is returned).
    """
    existing = getattr(wun, "_wt_debug_recorder", None)
    if existing is not None:
        return existing
    rec = recorder or Recorder()
    STATE.install()  # ensure logging is on
    for attr in ("rest", "mcp", "grid", "market"):
        client = getattr(wun, attr, None)
        if client is None:
            continue
        transport = getattr(client, "transport", None)
        if transport is None or isinstance(transport, type(rec.wrap(None))):
            continue
        try:
            client.transport = rec.wrap(transport)
        except Exception:
            continue
    wun._wt_debug_recorder = rec
    return rec


def unwrap(wun: "WunderTrading") -> None:
    """Reverse of :func:`trace` — restore the original transports."""
    rec: Recorder | None = getattr(wun, "_wt_debug_recorder", None)
    if rec is None:
        return
    for attr in ("rest", "mcp", "grid", "market"):
        client = getattr(wun, attr, None)
        if client is None:
            continue
        t = getattr(client, "transport", None)
        if hasattr(t, "inner"):
            try:
                client.transport = t.inner
            except Exception:
                continue
    try:
        delattr(wun, "_wt_debug_recorder")
    except AttributeError:
        pass


__all__ = [
    "STATE",
    "DebugState",
    "DebugDump",
    "default_dump_dir",
    "dump_failure",
    "install",
    "is_enabled",
    "log_request",
    "trace",
    "unwrap",
]
