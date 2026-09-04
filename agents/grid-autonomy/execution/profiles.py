#!/usr/bin/env python3
"""WunderTrading exchange-profile management (paper/demo) via wt_browser.py.

Verified 2026-09-04 against the live my-exchanges UI and captured network:

    POST /en/trader/my-exchanges/master-api-profile/upsert
    body: {"api": "<32-hex dummy>", "secret": "<32-hex dummy>",
           "enabled": true, "name": "<profile name>",
           "exchangeFamily": "BINANCE", "paperTrading": true,
           "marginMode": "cross", "favorite": false,
           "tradeMode": "hedge_mode"}

Paper profiles need no real exchange API keys. The UI sends random 32-hex
placeholder values for api/secret and the backend accepts them; this module
generates the same placeholders locally. NEVER pass real exchange keys here.

Binance caveat: on WunderTrading, Binance paper trading is FUTURES-ONLY.
`exchangeFamily: "BINANCE"` + `paperTrading: true` resolves to exchange code
`BINANCE_FUTURES` (USDT-M). Binance spot has no paper mode (the exchange
family list exposes spot as type ["spot"] and only the futures child as
["usdtm","paper"]).

Nothing here executes on import. Live creation requires explicit
dry_run=False.
"""
import json
import os
import secrets
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
WUN_SCRIPTS = os.path.join(ROOT, ".agents", "skills", "wundertrading", "scripts")

PROFILE_UPSERT = "/en/trader/my-exchanges/master-api-profile/upsert"


def _dummy_secret():
    """Random 32-hex placeholder — matches the UI's paper-profile key fields."""
    return secrets.token_hex(16)


def paper_profile_body(name, exchange_family="BINANCE",
                       trade_mode="hedge_mode", margin_mode="cross"):
    """Body for POST /en/trader/my-exchanges/master-api-profile/upsert."""
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


def _run(cmd, dry_run=True):
    if dry_run:
        return {"ok": True, "dry_run": True, "cmd": cmd}
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return {"ok": p.returncode == 0, "stdout": p.stdout[-2000:],
                "stderr": p.stderr[-1000:], "cmd": cmd}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "cmd": cmd}


def _parse_response(res):
    """Extract status/code/message/violations from an api-POST stdout blob."""
    out = res.get("stdout") or ""
    try:
        data = json.loads(out)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    violations = data.get("result", {}).get("violations") or []
    return {
        "status": data.get("code"),
        "message": data.get("result", {}).get("message"),
        "violations": [
            {"propertyPath": v.get("propertyPath"), "message": v.get("message")}
            for v in violations if isinstance(v, dict)
        ],
    }


def create_paper_profile(name, exchange_family="BINANCE", dry_run=True,
                         trade_mode="hedge_mode", margin_mode="cross"):
    """Create a WunderTrading paper profile (no real keys submitted).

    Binance resolves to BINANCE_FUTURES (USDT-M) paper. Returns the
    subprocess result envelope; on a live call `stdout` carries the
    response JSON and `parsed` carries {status, message, violations}.
    A 400 with a `name` violation "You have account with that name" means
    the profile already exists (name uniqueness is enforced).
    """
    body = paper_profile_body(name, exchange_family, trade_mode, margin_mode)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(body, f)
        path = f.name
    cmd = [sys.executable, os.path.join(WUN_SCRIPTS, "wt_browser.py"),
           "api", "POST", PROFILE_UPSERT, "--data", f"@{path}"]
    res = _run(cmd, dry_run)
    try:
        os.unlink(path)
    except OSError:
        pass
    if not dry_run:
        parsed = _parse_response(res)
        res["parsed"] = parsed
        res["already_exists"] = (
            parsed.get("status") == 400
            and any(v.get("propertyPath") == "name"
                    and "account with that name" in (v.get("message") or "")
                    for v in parsed.get("violations", []))
        )
        res["created"] = bool(res.get("ok")) and parsed.get("status") in (200, 201)
    return res


def list_profiles():
    """Best-effort list of connected profiles (code/name/exchange/paper/balance)."""
    try:
        from observe import grid_profiles
        return grid_profiles() or []
    except Exception:
        return []
