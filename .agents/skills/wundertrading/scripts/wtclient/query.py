"""Query-string and body serialization helpers.

WunderTrading's REST surface uses comma-separated list filters
(``exchanges=CODE1,CODE2``), not the common ``key[]=a&key[]=b`` bracket form.
These helpers encode lists that way and keep the exact query string available
for HMAC signing.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from .errors import WunValidationError


def encode_query(params: Mapping[str, Any] | None) -> str:
    """Encode query params; list/tuple values become comma-separated strings."""
    if not params:
        return ""
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            value = ",".join(str(v) for v in value)
        pairs.append((key, str(value)))
    return urlencode(pairs)


def append_query(path: str, params: Mapping[str, Any] | None) -> str:
    """Append encoded ``params`` to ``path``, preserving existing queries."""
    qs = encode_query(params)
    if not qs:
        return path
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}{qs}"


def serialize_body(body: Any) -> str:
    """Serialize a request body to the exact string sent on the wire."""
    if body is None:
        return ""
    if isinstance(body, (dict, list)):
        return json.dumps(body, separators=(",", ":"))
    if isinstance(body, str):
        return body
    if isinstance(body, (bytes, bytearray)):
        return bytes(body).decode("utf-8", errors="replace")
    return str(body)


def load_json_arg(value: str | None) -> Any | None:
    """Parse a CLI ``--data``/``--params`` value: JSON, ``@file``, or None."""
    if value is None or value == "":
        return None
    value = value.strip()
    if value.startswith("@"):
        path = value[1:]
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise WunValidationError(f"cannot load JSON from {path!r}: {exc}", cause=exc) from exc
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise WunValidationError(f"invalid JSON: {value[:80]!r}", cause=exc) from exc


def normalize_url(url: str, base: str) -> str:
    """Resolve ``url`` against ``base`` when it is not already absolute."""
    if url.startswith(("http://", "https://")):
        return url
    if not url.startswith("/"):
        url = "/" + url
    return f"{base}{url}"


__all__ = ["encode_query", "append_query", "serialize_body", "load_json_arg", "normalize_url"]
