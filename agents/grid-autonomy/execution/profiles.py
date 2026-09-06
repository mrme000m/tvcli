#!/usr/bin/env python3
"""WunderTrading paper-profile management — config-driven ensure on wtclient.

Supersedes the old wt_browser.py-subprocess implementation: profile
create/list now rides the in-process wtclient ExchangesClient through
``execution/wt_library.py`` (same session, no forks, same dry_run-first
semantics).

Verified 2026-09-04/06 against the live my-exchanges UI and captured
network (the knowledge wtclient now implements):

    POST /en/trader/my-exchanges/master-api-profile/upsert
    body: {"api": "<32-hex dummy>", "secret": "<32-hex dummy>",
           "enabled": true, "name": "<profile name>",
           "exchangeFamily": "BINANCE", "paperTrading": true,
           "marginMode": "cross", "favorite": false,
           "tradeMode": "hedge_mode"}

Paper profiles need no real exchange API keys. The UI sends random 32-hex
placeholder values for api/secret and the backend accepts them; wtclient
generates the same placeholders. NEVER pass real exchange keys here.

Binance caveat: on WunderTrading, Binance paper trading is FUTURES-ONLY.
`exchangeFamily: "BINANCE"` + `paperTrading: true` resolves to exchange code
`BINANCE_FUTURES` (USDT-M). Binance spot has no paper mode (the exchange
family list exposes spot as type ["spot"] and only the futures child as
["usdtm","paper"]).

Config shape (``autonomy.paper_profiles``, mirrors daemon._allowed_profile_names):

    paper_profiles:            # venue-keyed (current)
      hyperliquid: [demo-hype]
      binance: [demo-bn]
    paper_profiles: [demo-hype]  # legacy flat list -> hyperliquid venue

Nothing here executes on import. Live creation requires explicit
execute=True (passed only by the daemon in live-paper mode).
"""
from __future__ import annotations

import secrets

try:  # execution package import (tests) …
    from execution import wt_library
except Exception:  # … or top-level (daemon puts execution/ on sys.path)
    try:
        import wt_library  # type: ignore
    except Exception:
        wt_library = None  # type: ignore[assignment]

# Legacy reference: the session endpoint paper profiles are created at
# (wtclient posts here with its own placeholder keys).
PROFILE_UPSERT = "/en/trader/my-exchanges/master-api-profile/upsert"

# Legacy flat-list fallback venue: the pre-venue-map allowlist only ever
# carried Hyperliquid paper profiles (demo-hype), so a flat list maps to
# the hyperliquid venue key.
LEGACY_FLAT_VENUE = "hyperliquid"


def _clean_names(names):
    """Order-preserved de-dup of non-empty name strings."""
    out = []
    if names is None:
        return out
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, (list, tuple)):
        return out
    for n in names:
        n = str(n or "").strip()
        if n and n not in out:
            out.append(n)
    return out


def paper_profile_spec(cfg):
    """venue -> [profile names] from ``cfg["autonomy"]["paper_profiles"]``.

    Venue-keyed maps pass through (venue keys normalized to lowercase);
    a legacy flat list of names maps to the hyperliquid venue. Unknown /
    empty shapes yield {} — the caller treats that as "nothing to ensure".
    """
    autonomy = (cfg or {}).get("autonomy") or {}
    pp = autonomy.get("paper_profiles")
    spec: dict[str, list[str]] = {}
    if isinstance(pp, dict):
        for venue, names in pp.items():
            venue = str(venue or "").strip().lower()
            if not venue:
                continue
            clean = _clean_names(names)
            if clean:
                spec[venue] = clean
    elif isinstance(pp, (list, tuple)):
        clean = _clean_names(pp)
        if clean:
            spec[LEGACY_FLAT_VENUE] = clean
    return spec


def ensure_paper_profiles(cfg, *, execute: bool = False) -> dict:
    """Ensure the config's allowlisted paper profiles exist. NEVER raises.

    Builds the venue-keyed spec from ``autonomy.paper_profiles`` and calls
    ``wt_library.ensure_paper_profiles`` (wtclient idempotently creates only
    what is missing; a wrong-shape existing profile is reported as an
    error state and never mutated). ``execute=False`` (default) stays a
    planned dry-run report — no WunderTrading mutation.

    Returns ``{"ok": bool, "executed": bool, "spec": {...}, "result": ...,
    "error": str|None}``.
    """
    spec = paper_profile_spec(cfg)
    report = {"ok": False, "executed": bool(execute), "spec": spec,
              "result": None, "error": None}
    if wt_library is None:
        report["error"] = "wt_library unavailable (wtclient missing)"
        return report
    try:
        res = wt_library.ensure_paper_profiles(spec, dry_run=not execute)
    except Exception as exc:  # unexpected — still a report, never a raise
        report["error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
        return report
    report["result"] = res
    if isinstance(res, dict):
        report["ok"] = bool(res.get("ok"))
        if not report["ok"]:
            report["error"] = str(res.get("error") or "ensure failed")
    else:
        report["error"] = f"unexpected ensure result: {type(res).__name__}"
    return report


# ── legacy compatibility wrappers ─────────────────────────────────────
# No current importer, kept so the old public surface keeps working (thin
# wrappers over wt_library; the subprocess implementation is gone).


def _dummy_secret():
    """Random 32-hex placeholder — matches the UI's paper-profile key fields."""
    return secrets.token_hex(16)


def paper_profile_body(name, exchange_family="BINANCE",
                       trade_mode="hedge_mode", margin_mode="cross"):
    """Body for POST /en/trader/my-exchanges/master-api-profile/upsert.

    Pure/local (generates the same placeholder keys wtclient submits) —
    kept for reference and tests; the live path goes through wtclient now.
    """
    name = str(name).strip()
    if not name:
        raise ValueError("profile name is required")
    return {
        "api": _dummy_secret(),
        "secret": _dummy_secret(),
        "enabled": True,
        "name": name,
        "exchangeFamily": exchange_family,
        "paperTrading": True,
        "marginMode": margin_mode,
        "favorite": False,
        "tradeMode": trade_mode,
    }


def create_paper_profile(name, exchange_family="BINANCE", dry_run=True,
                         trade_mode="hedge_mode", margin_mode="cross"):
    """Create a WunderTrading paper profile (no real keys submitted).

    Thin wrapper over ``wt_library.create_paper_profile`` (wtclient picks
    the trade/margin-mode defaults; the kwargs are accepted for the old
    signature). Returns the wt_library result envelope.
    """
    if wt_library is None:
        return {"ok": False, "dry_run": bool(dry_run),
                "error": "wt_library unavailable (wtclient missing)"}
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "dry_run": bool(dry_run),
                "error": "profile name is required"}
    try:
        return wt_library.create_paper_profile(
            name, exchange_family, dry_run=dry_run)
    except Exception as exc:
        return {"ok": False, "dry_run": bool(dry_run),
                "error": str(exc)[:200]}


def list_profiles():
    """Best-effort list of connected profiles via wtclient (never raises).

    Shape is ``Profile.as_dict()``: id/name/exchangeFamily/paperTrading/
    enabled/marginMode/tradeMode/favorite. (The old observe.grid_profiles
    code/name/exchange/balance shape is superseded — the daemon keeps its
    own snapshot via grid_profiles_safe.)
    """
    if wt_library is None:
        return []
    try:
        return wt_library.exchanges_list_profiles() or []
    except Exception:
        return []
