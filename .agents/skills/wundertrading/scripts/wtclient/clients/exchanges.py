"""ExchangesClient — my-exchanges / account-limits session surface.

Verified 2026-09-06 against the live WT cabinet (session-auth, same origin
as ``/en/trader/grid_bots``):

- ``GET /en/trader/my-exchanges/master-api-profile/grid`` — HAL collection
  ``{"_embedded": {"items": [PROFILE...]}}`` (plus ``_links``/``total_items``).
- ``POST /en/trader/my-exchanges/master-api-profile/upsert`` — create (or
  rename/edit) a profile. Paper profiles need **no real exchange keys**: the
  WT UI itself submits random 32-hex placeholders for ``api``/``secret`` and
  the backend accepts them. A duplicate name yields HTTP 400 with
  ``{"code":400,"result":{"violations":[{"propertyPath":"name",...}]}}``.
- ``GET /en/trader/dashboard/account-limits`` — plan limits
  (``gridBots``/``dcaBots``/``openPositions``/... with
  ``allowOnCurrentPlan``/``active``/``max``/``exists``).
- ``DELETE /en/trader/my-exchanges/master-api-profile/{code}/delete`` —
  profile deletion (``code`` = hex ``resource.code``; the listing's
  ``actions.delete`` affordance).

Binance caveat: ``exchangeFamily: "BINANCE"`` + ``paperTrading: true``
resolves to ``BINANCE_FUTURES`` (USDT-M). Binance spot has no paper mode.
There is no way to set a paper profile balance (fixed $10k demo).

Account cap: WT allows only **2 paper trading accounts** per account — a
third create returns HTTP 400 ``"Limit reached. Only 2 Paper trading
accounts are allowed."`` Use :meth:`delete_profile_by_name` to free a
stale paper slot.
"""
from __future__ import annotations

import secrets as _secrets
from typing import Any

from ..errors import WunError
from ..models.profiles import Profile, parse_profiles
from ..transport.base import BaseTransport
from .base import BaseClient

#: Session-auth endpoints of the my-exchanges surface.
PROFILE_GRID_PATH = "/en/trader/my-exchanges/master-api-profile/grid"
PROFILE_UPSERT_PATH = "/en/trader/my-exchanges/master-api-profile/upsert"
PROFILE_DELETE_PATH_FMT = "/en/trader/my-exchanges/master-api-profile/{code}/delete"
ACCOUNT_LIMITS_PATH = "/en/trader/dashboard/account-limits"

#: Canonical venue-key -> exchangeFamily mapping used by
#: :meth:`ExchangesClient.ensure_paper_profiles`.
DEFAULT_VENUE_FAMILIES: dict[str, str] = {
    "hyperliquid": "HYPERLIQUID",
    "binance": "BINANCE",
}


def _dummy_key() -> str:
    """Random 32-hex placeholder — matches the UI's paper-profile key fields."""
    return _secrets.token_hex(16)


def paper_profile_body(
    name: str,
    exchange_family: str = "BINANCE",
    trade_mode: str = "hedge_mode",
    margin_mode: str = "cross",
) -> dict[str, Any]:
    """Body for ``POST /en/trader/my-exchanges/master-api-profile/upsert``.

    ``api``/``secret`` are random placeholder hex exactly like the WT UI
    submits for paper profiles. NEVER pass real exchange keys here.
    """
    name = str(name).strip()
    if not name:
        raise ValueError("profile name is required")
    return {
        "api": _dummy_key(),
        "secret": _dummy_key(),
        "enabled": True,
        "name": name,
        "exchangeFamily": exchange_family,
        "paperTrading": True,
        "marginMode": margin_mode,
        "favorite": False,
        "tradeMode": trade_mode,
    }


def _parse_upsert_error(response: Any) -> dict[str, Any]:
    """Extract status/message/violations from a failed upsert response body."""
    out: dict[str, Any] = {
        "status": getattr(response, "status_code", None),
        "message": None,
        "violations": [],
    }
    try:
        data = response.json()
    except Exception:
        try:
            out["message"] = (response.text or "")[:300] or None
        except Exception:
            pass
        return out
    if not isinstance(data, dict):
        return out
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    out["message"] = data.get("message") or result.get("message")
    violations = result.get("violations") or []
    parsed: list[dict[str, Any]] = []
    if isinstance(violations, list):
        for violation in violations:
            if isinstance(violation, dict):
                parsed.append(
                    {
                        "propertyPath": violation.get("propertyPath"),
                        "message": violation.get("message"),
                    }
                )
    out["violations"] = parsed
    return out


def _is_duplicate_name(parsed: dict[str, Any]) -> bool:
    """A 400 with a ``name`` violation 'account with that name' = duplicate."""
    if parsed.get("status") != 400:
        return False
    for violation in parsed.get("violations") or []:
        if (
            violation.get("propertyPath") == "name"
            and "account with that name" in (violation.get("message") or "").lower()
        ):
            return True
    return False


class ExchangesClient(BaseClient):
    """Exchange-profile management + plan limits over the session surface."""

    def __init__(
        self,
        transport: BaseTransport | None = None,
        **kwargs: Any,
    ) -> None:
        # Import lazily: SessionTransport requires cookies at construction.
        if transport is None:
            from ..transport.session import SessionTransport

            transport = SessionTransport(**kwargs)
        super().__init__(transport)

    # -- read --------------------------------------------------------------

    def list_profiles(self) -> list[Profile]:
        """Return every connected profile (paper + live) as :class:`Profile`."""
        data = self._get_json("GET", PROFILE_GRID_PATH)
        items = (data or {}).get("_embedded", {}).get("items") or []
        return parse_profiles(items)

    def account_limits(self) -> dict[str, Any]:
        """Return the raw dashboard ``account-limits`` payload."""
        data = self._get_json("GET", ACCOUNT_LIMITS_PATH)
        return data if isinstance(data, dict) else {"result": data}

    # -- write --------------------------------------------------------------

    def create_paper_profile(
        self,
        name: str,
        exchange_family: str = "BINANCE",
        *,
        trade_mode: str = "hedge_mode",
        margin_mode: str = "cross",
    ) -> dict[str, Any]:
        """Create a WunderTrading paper profile (no real keys submitted).

        Never raises: transport/API errors are caught and surfaced as
        ``{"created": False, "error": str(...)}`` so callers can gate on the
        returned dict. A 400 with a ``name`` violation
        "You have account with that name" means the profile already exists
        (name uniqueness is enforced) — reported via ``already_exists``.
        """
        try:
            body = paper_profile_body(name, exchange_family, trade_mode, margin_mode)
        except ValueError as exc:
            return {
                "created": False,
                "already_exists": False,
                "status": None,
                "message": str(exc),
                "violations": [],
                "response": None,
                "error": str(exc),
            }
        try:
            response = self.transport.request("POST", PROFILE_UPSERT_PATH, body=body)
        except WunError as exc:
            return {
                "created": False,
                "already_exists": False,
                "status": None,
                "message": str(exc),
                "violations": [],
                "response": None,
                "error": str(exc),
            }
        if response.ok:
            try:
                raw: Any = response.json()
            except Exception:
                raw = (response.text or "")[:500] or None
            return {
                "created": True,
                "already_exists": False,
                "status": response.status_code,
                "message": None,
                "violations": [],
                "response": raw,
            }
        parsed = _parse_upsert_error(response)
        result: dict[str, Any] = {
            "created": False,
            "already_exists": _is_duplicate_name(parsed),
            "status": parsed.get("status"),
            "message": parsed.get("message"),
            "violations": parsed.get("violations") or [],
            "response": None,
        }
        if not result["already_exists"]:
            result["error"] = (
                f"upsert failed with HTTP {result['status']}: {result['message'] or 'unknown error'}"
            )
        try:
            result["response"] = response.json()
        except Exception:
            pass
        return result

    # paper_profile_body is a pure function; expose it as a static method so
    # the public contract reads naturally on an instance too.
    paper_profile_body = staticmethod(paper_profile_body)

    # -- delete ---------------------------------------------------------------

    def delete_profile(self, code: str) -> dict[str, Any]:
        """Delete a profile by its hex ``code`` (verified live 2026-09-06).

        ``DELETE /en/trader/my-exchanges/master-api-profile/{code}/delete``
        — the same affordance the cabinet's ``actions.delete`` link exposes
        (``{"status": "ok", "message": "Delete success."}`` on success).
        ``code`` is the profile's hex ``resource.code`` from
        :meth:`list_profiles`, NOT the numeric ``id``. Raises
        :class:`~wtclient.errors.WunError` on transport/API failure — this
        is an explicit destructive action and callers should see failures.
        """
        code = str(code or "").strip()
        if not code:
            raise ValueError("profile code is required")
        response = self.transport.request(
            "DELETE", PROFILE_DELETE_PATH_FMT.format(code=code)
        )
        try:
            data = response.json()
        except Exception:
            data = {"status": "ok" if response.ok else "error",
                    "message": (response.text or "")[:300]}
        return data if isinstance(data, dict) else {"result": data}

    def delete_profile_by_name(
        self, name: str, *, paper_only: bool = True
    ) -> dict[str, Any]:
        """Look up a profile by ``name`` and delete it (never raises).

        Useful for freeing one of the account's limited paper slots — WT
        allows only 2 paper accounts, and a stale disabled one blocks
        creating the ones the fleet needs. With ``paper_only=True`` (the
        default) a NON-paper profile is refused — a live exchange
        connection is never deleted by a name match. Returns
        ``{"deleted": bool, "code"/"error": ...}``.
        """
        name = str(name or "").strip()
        if not name:
            return {"deleted": False, "error": "profile name is required"}
        try:
            profiles = self.list_profiles()
        except WunError as exc:
            return {"deleted": False, "error": f"list_profiles failed: {exc}"}
        match = next((p for p in profiles if p.name == name), None)
        if match is None:
            return {"deleted": False, "error": f"no profile named {name!r}"}
        if paper_only and match.paper_trading is not True:
            return {
                "deleted": False,
                "code": match.code,
                "error": (
                    f"profile {name!r} is not a paper account "
                    f"(paperTrading={match.paper_trading!r}); pass "
                    "paper_only=False to delete it anyway"
                ),
            }
        if not match.code:
            return {"deleted": False, "error": f"profile {name!r} has no code"}
        try:
            result = self.delete_profile(match.code)
        except (WunError, ValueError) as exc:
            return {"deleted": False, "code": match.code, "error": str(exc)}
        ok = (result or {}).get("status") == "ok"
        return (
            {"deleted": True, "code": match.code, "result": result}
            if ok
            else {"deleted": False, "code": match.code, "result": result}
        )

    # -- idempotent ensure ----------------------------------------------------

    def ensure_paper_profiles(
        self,
        spec: dict[str, list[str]],
        *,
        families: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Ensure the requested paper profiles exist (never mutates wrong-shape ones).

        ``spec`` maps a venue key (e.g. ``"hyperliquid"``) to the profile
        names wanted on that venue's family. For each venue->name:

        - present AND paper AND family matches -> ``state: "present"``
        - missing -> created -> ``state: "created"`` (or ``"error"``)
        - present but not paper / wrong family -> ``state: "error"`` with an
          explicit message — the existing profile is NEVER mutated.

        Returns ``{"ok": bool, "venues": {...}, "created": [...],
        "errors": [...]}`` where ``created`` lists fully-qualified
        ``"<venue>/<name>"`` strings. Never raises.
        """
        resolved = dict(DEFAULT_VENUE_FAMILIES)
        if families:
            resolved.update({str(k).lower(): v for k, v in families.items()})

        out: dict[str, Any] = {"ok": True, "venues": {}, "created": [], "errors": []}
        try:
            existing = self.list_profiles()
        except WunError as exc:
            for venue, names in (spec or {}).items():
                out["venues"][venue] = {
                    name: {"state": "error", "detail": f"list_profiles failed: {exc}"}
                    for name in names or []
                }
                out["errors"].append(f"list_profiles failed: {exc}")
            out["ok"] = False
            return out

        by_name: dict[str, Profile] = {}
        for profile in existing:
            if profile.name:
                by_name[profile.name] = profile

        for venue, names in (spec or {}).items():
            venue_key = str(venue).lower()
            family = resolved.get(venue_key)
            venue_out: dict[str, dict[str, Any]] = {}
            if family is None:
                message = (
                    f"unknown venue {venue!r}; known families: {sorted(resolved)} "
                    "(pass families= to extend)"
                )
                for name in names or []:
                    venue_out[name] = {"state": "error", "detail": message}
                    out["errors"].append(f"{venue_key}/{name}: {message}")
                out["venues"][venue] = venue_out
                continue
            for name in names or []:
                fq = f"{venue_key}/{name}"
                profile = by_name.get(name)
                if profile is None:
                    created = self.create_paper_profile(name, family)
                    if created.get("created"):
                        venue_out[name] = {"state": "created", "detail": family}
                        out["created"].append(fq)
                    elif created.get("already_exists"):
                        # Name taken by a profile we could not see in the
                        # listing (race or hidden); treat as an error, never
                        # silently as "present".
                        message = (
                            f"profile {name!r} already exists but was not in the "
                            "listing (name collision)"
                        )
                        venue_out[name] = {"state": "error", "detail": message}
                        out["errors"].append(f"{fq}: {message}")
                    else:
                        message = created.get("error") or created.get("message") or "creation failed"
                        venue_out[name] = {"state": "error", "detail": message}
                        out["errors"].append(f"{fq}: {message}")
                    continue
                problems: list[str] = []
                if not profile.paper_trading:
                    problems.append(
                        f"existing profile is not paper trading (paperTrading="
                        f"{profile.paper_trading!r})"
                    )
                if (profile.exchange_family or "").upper() != family.upper():
                    problems.append(
                        f"existing profile family {profile.exchange_family!r} != {family!r}"
                    )
                if problems:
                    message = "; ".join(problems) + " — refusing to mutate existing profile"
                    venue_out[name] = {"state": "error", "detail": message}
                    out["errors"].append(f"{fq}: {message}")
                else:
                    venue_out[name] = {"state": "present", "detail": profile.id}
            out["venues"][venue] = venue_out

        out["ok"] = not out["errors"]
        return out


__all__ = [
    "ExchangesClient",
    "DEFAULT_VENUE_FAMILIES",
    "PROFILE_GRID_PATH",
    "PROFILE_UPSERT_PATH",
    "PROFILE_DELETE_PATH_FMT",
    "ACCOUNT_LIMITS_PATH",
    "paper_profile_body",
]
