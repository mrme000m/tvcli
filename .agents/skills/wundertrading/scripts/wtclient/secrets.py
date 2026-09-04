"""Credential loading for all WunderTrading surfaces.

Reads the same files the prime-stack ``bw-provision`` tool writes and lets
environment variables win. This module never logs or prints secret values.

Supported sources, in precedence order (later wins):

1. ``browser-debug/secrets/runtime/wt.env``         (HMAC / MCP keys)
2. ``browser-debug/secrets/runtime/wt-session.env`` (PHPSESSID, cf_clearance)
3. ``browser-debug/.env``                            (legacy fallback)
4. Environment variables prefixed ``WUN_``/``WT_`` plus ``PHPSESSID``,
   ``cf_clearance`` and ``CLOUDFLARE_ACCOUNT_ID``.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .errors import WunConfigError

_ENV_PREFIXES = ("WUN_", "WT_")
_EXTRA_ENV_KEYS = ("PHPSESSID", "cf_clearance", "CLOUDFLARE_ACCOUNT_ID")


def _repo_root() -> Path:
    """Walk up from this file until a repository marker is found."""
    start = Path(__file__).resolve()
    for parent in start.parents:
        if (parent / "browser-debug").is_dir() or (parent / ".git").exists():
            return parent
    return start.parents[5]


REPO_ROOT = _repo_root()

DEFAULT_SECRET_FILES: tuple[Path, ...] = (
    REPO_ROOT / "browser-debug" / "secrets" / "runtime" / "wt.env",
    REPO_ROOT / "browser-debug" / "secrets" / "runtime" / "wt-session.env",
    REPO_ROOT / "browser-debug" / ".env",
)


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE file, ignoring comments/blank lines and quoting."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip('"').strip("'")
        if not value:
            continue
        out[key] = value
    return out


def _env_overrides(environ: Mapping[str, str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (environ or os.environ).items():
        if key.startswith(_ENV_PREFIXES) or key in _EXTRA_ENV_KEYS:
            out[key] = value
    return out


@dataclass(frozen=True)
class Secrets:
    """Immutable credential bag with typed accessors for each surface."""

    values: dict[str, str]
    source_files: tuple[Path, ...] = field(default_factory=tuple)

    def get(self, name: str, *alts: str, default: str | None = None) -> str | None:
        """Return the first non-empty value among ``name`` and ``alts``."""
        for key in (name, *alts):
            value = self.values.get(key)
            if value:
                return value
        return default

    @property
    def api_key(self) -> str | None:
        return self.get("WUN_API_KEY", "WT_API_KEY")

    @property
    def api_secret(self) -> str | None:
        return self.get("WUN_SECRET_KEY", "WT_API_SECRET")

    @property
    def phpsessid(self) -> str | None:
        return self.get("WT_PHPSESSID", "PHPSESSID")

    @property
    def cf_clearance(self) -> str | None:
        return self.get("WT_CF_CLEARANCE", "cf_clearance")

    @property
    def cookies_json(self) -> str | None:
        return self.get("WT_COOKIES_JSON")

    @property
    def cookies(self) -> dict[str, str]:
        """Merge WT_COOKIES_JSON, named cookie vars, and raw env names."""
        cookies: dict[str, str] = {}
        raw = self.cookies_json
        if raw:
            try:
                for item in json.loads(raw):
                    if isinstance(item, dict) and item.get("name") and item.get("value"):
                        cookies[str(item["name"])] = str(item["value"])
            except (json.JSONDecodeError, TypeError):
                pass
        named = (
            ("WT_PHPSESSID", "PHPSESSID"),
            ("WT_CF_CLEARANCE", "cf_clearance"),
        )
        for source, target in named:
            value = self.get(source, target)
            if value:
                cookies[target] = value
        for direct in ("PHPSESSID", "cf_clearance"):
            if direct not in cookies:
                value = self.get(direct)
                if value:
                    cookies[direct] = value
        return cookies

    def has_api_keys(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def has_session(self) -> bool:
        return bool(self.cookies.get("PHPSESSID"))

    def require_api_keys(self) -> tuple[str, str]:
        """Return ``(api_key, api_secret)`` or raise a helpful config error."""
        key, secret = self.api_key, self.api_secret
        if not key or not secret:
            raise WunConfigError(
                "missing WUN_API_KEY / WUN_SECRET_KEY (or WT_API_KEY / WT_API_SECRET)",
                remediation=(
                    "create keys in the WunderTrading cabinet API page and export "
                    "them as WUN_API_KEY / WUN_SECRET_KEY, or provision "
                    "browser-debug/secrets/runtime/wt.env"
                ),
            )
        return key, secret

    def require_session(self) -> dict[str, str]:
        """Return session cookies or raise a helpful config error."""
        cookies = self.cookies
        if not cookies.get("PHPSESSID"):
            raise WunConfigError(
                "missing PHPSESSID session cookie",
                remediation=(
                    "run the browser login flow (node browser-debug/wt.mjs) and "
                    "re-export wt-session.env"
                ),
            )
        return cookies


def load_secrets(
    paths: tuple[Path | str, ...] | list[Path | str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Secrets:
    """Load secrets from files (later wins) then environment (highest wins)."""
    merged: dict[str, str] = {}
    files = paths if paths is not None else DEFAULT_SECRET_FILES
    for raw in files:
        merged.update(parse_env_file(Path(raw)))
    merged.update(_env_overrides(environ))
    return Secrets(values=merged, source_files=tuple(Path(p) for p in files))


__all__ = [
    "Secrets",
    "load_secrets",
    "parse_env_file",
    "DEFAULT_SECRET_FILES",
    "REPO_ROOT",
]
