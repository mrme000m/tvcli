#!/usr/bin/env python3
"""kimi-keys.py — programmatic Kimi Code API-key management.

Authenticates to Kimi using the `kimi-session` vault item (Google-OAuth
tokens), refreshes the access token when it is near expiry, then drives the
Kimi Code console's API-key service
(`kimi.gateway.credentials.v1.APIKeyService` on `www.kimi.ai/apiv2`):

  list         ListAPIKeys            — id/name/status/created (masked key)
  create       CreateAPIKey --name N  — returns the full key exactly once
  delete       DeleteAPIKey           — by --id or --name (lookup by name)
  reset        ResetAPIKey --key K    — rotate a key you already hold
  get-config   GetConfig              — whether the account can create more
  refresh      AuthService/RefreshToken — renew access token (--persist writes
                                          the fresh tokens back to kimi-session)

Session resolution is env-first (KIMI_ACCESS_TOKEN / KIMI_REFRESH_TOKEN /
KIMI_DEVICE_ID / KIMI_SSID), vault-fallback via `bw` (BW_SESSION, else
BW_PASSWORD unlock). Key values are NEVER logged — output carries redacted
metadata only (prefix, length, source); the one exception is `create`, which
prints the new key because that is the only moment it is retrievable.

Why two hosts: auth (auth.kimi.ai/api) issues web tokens; the console API-key
service (www.kimi.ai/apiv2) is a separate ConnectRPC surface gated by
`Authorization: Bearer <access_token>` plus x-msh-* device headers.

Usage:
  kimi-keys.py list
  kimi-keys.py create --name tvcli-agent --update-vault
  kimi-keys.py delete --name tvcli-agent
  kimi-keys.py get-config
  kimi-keys.py refresh --persist
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

# --- endpoints (reverse-engineered from kimi-web-seo JS, 2026-09-04) ---------
AUTH_HOST = "https://auth.kimi.ai/api"
APIV2_HOST = "https://www.kimi.ai/apiv2"
SERVICE = "kimi.gateway.credentials.v1.APIKeyService"

# enum wire values (JSON request bodies send enum NUMBERS; responses come back
# as the string names, e.g. "FEATURE_CODING" / "STATUS_ACTIVE")
FEATURE_CODING = 4
STATUS_ACTIVE = 1

SESSION_ITEM = "kimi-session"
PROVIDER_ITEM = "provider-keys"
KEY_FIELD = "KIMI_CODE_API_KEY"

UA = "tvcli-kimi-keys/1.0"

_bw_session_cache: str | None = None


# --- vault helpers (env-first, vault-fallback) -------------------------------
def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _bw_session() -> str:
    global _bw_session_cache
    if _bw_session_cache:
        return _bw_session_cache
    if os.environ.get("BW_SESSION"):
        _bw_session_cache = os.environ["BW_SESSION"]
        return _bw_session_cache
    if not os.environ.get("BW_PASSWORD"):
        raise SystemExit("no BW_SESSION and no BW_PASSWORD (vault locked)")
    p = _run(["bw", "unlock", "--passwordenv", "BW_PASSWORD", "--raw"])
    sess = (p.stdout or "").strip()
    if p.returncode != 0 or not sess:
        raise SystemExit("bw unlock failed")
    _bw_session_cache = sess
    return sess


def _bw_get(item: str) -> dict:
    sess = _bw_session()
    p = _run(["bw", "get", "item", item, "--session", sess])
    if p.returncode != 0:
        raise SystemExit(f"vault item {item} not found")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"bw get item {item} returned non-JSON")


def _bw_edit(item_id: str, payload: dict) -> None:
    sess = _bw_session()
    enc = base64.b64encode(json.dumps(payload).encode()).decode()
    p = _run(["bw", "edit", "item", item_id, enc, "--session", sess])
    if p.returncode != 0:
        raise SystemExit(f"bw edit item {item_id} failed: {p.stderr[:200]}")


def resolve_session() -> dict[str, str]:
    """Return {access_token, refresh_token, device_id, ssid} from env or vault."""
    env_map = {
        "access_token": "KIMI_ACCESS_TOKEN",
        "refresh_token": "KIMI_REFRESH_TOKEN",
        "device_id": "KIMI_DEVICE_ID",
        "ssid": "KIMI_SSID",
    }
    out: dict[str, str] = {}
    for k, env in env_map.items():
        if os.environ.get(env):
            out[k] = os.environ[env]
    if len(out) == len(env_map):
        return out

    item = _bw_get(SESSION_ITEM)
    notes = item.get("notes", "") or ""
    for line in notes.splitlines():
        if "=" in line:
            key, _, val = line.partition("=")
            if key in ("KIMI_ACCESS_TOKEN", "KIMI_REFRESH_TOKEN", "KIMI_DEVICE_ID", "KIMI_SSID"):
                out.setdefault(_SESSION_NOTE_TO_KEY[key], val.strip())
    if "access_token" not in out or "refresh_token" not in out:
        raise SystemExit("no KIMI_ACCESS_TOKEN/KIMI_REFRESH_TOKEN in env or kimi-session vault item")
    return out


_SESSION_NOTE_TO_KEY = {
    "KIMI_ACCESS_TOKEN": "access_token",
    "KIMI_REFRESH_TOKEN": "refresh_token",
    "KIMI_DEVICE_ID": "device_id",
    "KIMI_SSID": "ssid",
}


# --- JWT helpers -------------------------------------------------------------
def _b64url_decode(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def _jwt_payload(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        return json.loads(_b64url_decode(parts[1]).decode())
    except Exception:
        return {}


def _token_expiry(token: str) -> int:
    return int(_jwt_payload(token).get("exp", 0) or 0)


# --- HTTP --------------------------------------------------------------------
def _post(url: str, body: dict, token: str | None, device_id: str, ssid: str,
          timeout: int) -> tuple[int, dict]:
    headers = {
        "Content-Type": "application/json",
        "Origin": "https://www.kimi.ai",
        "User-Agent": UA,
        "x-msh-device-id": device_id,
        "X-Traffic-Id": device_id,
        "x-msh-session-id": ssid,
        "x-msh-platform": "web",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            try:
                return r.status, json.loads(r.read().decode())
            except json.JSONDecodeError:
                return r.status, {}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        raise SystemExit(f"request failed: {e}")


def _redact(v: str) -> str:
    return f"{v[:10]}…(len {len(v)})" if v else "missing"


# --- session refresh ---------------------------------------------------------
def ensure_fresh(sess: dict, ttl: int = 120, persist: bool = False) -> dict:
    """Refresh the access token if it is within `ttl` seconds of expiry."""
    exp = _token_expiry(sess["access_token"])
    if exp and exp - int(time.time()) > ttl:
        return sess
    status, body = _post(
        f"{AUTH_HOST}/account.gateway.v1.AuthService/RefreshToken",
        {"refresh_token": sess["refresh_token"]},
        None, sess.get("device_id", ""), sess.get("ssid", ""), 30,
    )
    if status != 200 or not body.get("accessToken"):
        raise SystemExit(f"token refresh failed (HTTP {status}): {_redact(json.dumps(body))}")
    sess["access_token"] = body["accessToken"]
    sess["refresh_token"] = body.get("refreshToken", sess["refresh_token"])
    if persist:
        _persist_session(sess)
    return sess


def _persist_session(sess: dict) -> None:
    item = _bw_get(SESSION_ITEM)
    notes = [
        f"KIMI_ACCESS_TOKEN={sess['access_token']}",
        f"KIMI_REFRESH_TOKEN={sess['refresh_token']}",
        f"KIMI_USER_ID={_jwt_payload(sess['access_token']).get('sub', '')}",
        f"KIMI_DEVICE_ID={sess.get('device_id', '')}",
        f"KIMI_SSID={sess.get('ssid', '')}",
        "KIMI_EMAIL=vb.mrme00@gmail.com",
        "KIMI_CONSOLE_URL=https://www.kimi.ai/code/console",
    ]
    item["notes"] = "\n".join(notes)
    _bw_edit(item["id"], item)
    print(json.dumps({"refreshed": True, "persisted": "kimi-session"}))


# --- key service calls -------------------------------------------------------
def _svc(method: str, body: dict, sess: dict) -> dict:
    status, resp = _post(f"{APIV2_HOST}/{SERVICE}/{method}", body,
                         sess["access_token"], sess.get("device_id", ""),
                         sess.get("ssid", ""), 30)
    if status != 200:
        reason = ""
        for d in resp.get("details", []):
            reason = d.get("debug", {}).get("localizedMessage", {}).get("message", "") or reason
        raise SystemExit(f"{method} failed (HTTP {status}): {reason or json.dumps(resp)[:300]}")
    return resp


def _list(sess: dict) -> list[dict]:
    resp = _svc("ListAPIKeys", {"page_size": 100, "page_token": "", "scope": [FEATURE_CODING]}, sess)
    return resp.get("apiKeys", [])


def _mask_key(k: str) -> str:
    if not k:
        return "-"
    if "..." in k:  # already masked by the API
        return k
    return f"{k[:10]}…{k[-4:]}"


def cmd_list(args, sess: dict) -> None:
    keys = _list(sess)
    out = [{
        "name": k.get("name"), "id": k.get("id"), "status": k.get("status"),
        "created": (k.get("createTime") or "")[:10], "key": _mask_key(k.get("key", "")),
    } for k in keys]
    print(json.dumps({"count": len(out), "api_keys": out}, indent=2))


def cmd_create(args, sess: dict) -> None:
    if not args.name:
        raise SystemExit("create needs --name")
    resp = _svc("CreateAPIKey", {
        "api_key": {"name": args.name, "scope": [FEATURE_CODING],
                    "status": STATUS_ACTIVE, "key": "", "id": ""},
    }, sess)
    k = resp.get("apiKey", {})
    key = k.get("key", "")
    if args.update_vault:
        _set_provider_key(key)
        print(json.dumps({
            "created": True, "name": k.get("name"), "id": k.get("id"),
            "key": _redact(key), "vault_updated": "provider-keys/KIMI_CODE_API_KEY",
        }, indent=2))
    else:
        print(json.dumps({
            "created": True, "name": k.get("name"), "id": k.get("id"),
            "key": key,  # full key — only shown once, on creation
            "warning": "store this key now; it is not retrievable later",
        }, indent=2))


def cmd_delete(args, sess: dict) -> None:
    kid = args.id
    if not kid and args.name:
        match = next((k for k in _list(sess) if k.get("name") == args.name), None)
        if not match:
            raise SystemExit(f"no key named '{args.name}'")
        kid = match["id"]
    if not kid:
        raise SystemExit("delete needs --id or --name")
    _svc("DeleteAPIKey", {"id": kid}, sess)
    print(json.dumps({"deleted": True, "id": kid}))


def cmd_reset(args, sess: dict) -> None:
    if not args.key:
        raise SystemExit("reset needs --key (the full sk-kimi-… value)")
    resp = _svc("ResetAPIKey", {"api_key": args.key}, sess)
    k = resp.get("apiKey", {})
    key = k.get("key", "")
    if args.update_vault:
        _set_provider_key(key)
    print(json.dumps({"reset": True, "name": k.get("name"), "id": k.get("id"),
                      "key": key if not args.update_vault else _redact(key)}))


def cmd_get_config(args, sess: dict) -> None:
    resp = _svc("GetConfig", {"scope": [FEATURE_CODING]}, sess)
    print(json.dumps({"allow_create": resp.get("allowCreate", False)}))


def _set_provider_key(key: str) -> None:
    item = _bw_get(PROVIDER_ITEM)
    f = next((x for x in item.get("fields", []) if x.get("name") == KEY_FIELD), None)
    if f is None:
        item.setdefault("fields", []).append({"name": KEY_FIELD, "value": key, "type": 1})
    else:
        f["value"] = key
    _bw_edit(item["id"], item)


def main() -> None:
    p = argparse.ArgumentParser(description="Kimi Code API-key management")
    p.add_argument("command", choices=["list", "create", "delete", "reset", "get-config", "refresh"])
    p.add_argument("--name", help="key name (create / delete-by-name)")
    p.add_argument("--id", help="key id (delete)")
    p.add_argument("--key", help="full key value (reset)")
    p.add_argument("--update-vault", action="store_true",
                   help="create/reset: write the key to provider-keys/KIMI_CODE_API_KEY")
    p.add_argument("--persist", action="store_true", help="refresh: write fresh tokens back to kimi-session")
    p.add_argument("--timeout", type=int, default=30)
    args = p.parse_args()

    sess = resolve_session()
    if args.command != "refresh":
        sess = ensure_fresh(sess, persist=False)

    if args.command == "list":
        cmd_list(args, sess)
    elif args.command == "create":
        cmd_create(args, sess)
    elif args.command == "delete":
        cmd_delete(args, sess)
    elif args.command == "reset":
        cmd_reset(args, sess)
    elif args.command == "get-config":
        cmd_get_config(args, sess)
    elif args.command == "refresh":
        sess = ensure_fresh(sess, ttl=10 ** 9, persist=args.persist)
        print(json.dumps({"access_token": _redact(sess["access_token"]),
                          "exp": _token_expiry(sess["access_token"]),
                          "now": int(time.time())}))


if __name__ == "__main__":
    main()