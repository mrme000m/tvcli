#!/usr/bin/env python3
"""cf — Cloudflare tunnel/connector manager for this container.

Auth (never printed, never committed):
  1. Env: CF_ACCOUNT_ID + CF_API_TOKEN_READ / CF_API_TOKEN_WRITE
     (CF_API_TOKEN works as fallback for both).
  2. Bitwarden vault fallback — folder ``cloudflare``, item
     ``cloudflare-tunnels`` (naming: browser-debug/secrets/VAULT_CONVENTIONS.md):
       field ``account-id``   → Cloudflare account ID
       field ``read-all``     → read-only API token (GET commands)
       field ``write-all``    → read-write API token (create/update/delete, DNS)
     The ``*-storage`` fields on that item are opaque/reserved and ignored.
     Unlock uses BW_SESSION if set, else BW_PASSWORD (+ BW_EMAIL or
     BW_CLIENTID/BW_CLIENTSECRET) via ``bw unlock --passwordenv``.

Read commands default to the read token, write commands to the write token.
Override with --token read|write. ``auth-status`` only shows redacted
metadata (id prefix, token lengths, source) — never values.

Requires: python3 + httpx (present in the codespace), bw CLI, jq-less.
cloudflared is installed on demand by ``cloudflared-ensure``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request

API = "https://api.cloudflare.com/client/v4"
VAULT_FOLDER = "cloudflare"
VAULT_ITEM = "cloudflare-tunnels"
VAULT_ITEM_LEGACY = "72d6e3279eb70c619d8a0ea4b908475f"  # pre-convention name (the raw account ID)

# ---------------------------------------------------------------- auth

_bw_session_cache: str | None = None


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _bw_session() -> str:
    global _bw_session_cache
    if _bw_session_cache:
        return _bw_session_cache
    if os.environ.get("BW_SESSION"):
        _bw_session_cache = os.environ["BW_SESSION"]
        return _bw_session_cache
    if not os.environ.get("BW_PASSWORD"):
        raise SystemExit("no BW_SESSION and no BW_PASSWORD (vault locked, cannot resolve CF creds)")
    p = _run(["bw", "unlock", "--passwordenv", "BW_PASSWORD", "--raw"])
    sess = (p.stdout or "").strip()
    if p.returncode != 0 or not sess:
        raise SystemExit(f"bw unlock failed: {(p.stderr or p.stdout).strip()[:200]}")
    _bw_session_cache = sess
    return sess


def _vault_fields() -> dict[str, str]:
    sess = _bw_session()
    item = None
    for name in (VAULT_ITEM, VAULT_ITEM_LEGACY):
        p = _run(["bw", "get", "item", name, "--session", sess])
        if p.returncode == 0:
            try:
                item = json.loads(p.stdout)
            except json.JSONDecodeError:
                item = None
        if item and item.get("name") in (VAULT_ITEM, VAULT_ITEM_LEGACY):
            break
        item = None
    if item is None:
        raise SystemExit(f"vault item {VAULT_ITEM} not found (legacy {VAULT_ITEM_LEGACY} either)")
    return {f.get("name", ""): f.get("value", "") for f in item.get("fields", [])}


def resolve_account_id() -> tuple[str, str]:
    """Return (account_id, source)."""
    if os.environ.get("CF_ACCOUNT_ID"):
        return os.environ["CF_ACCOUNT_ID"].strip(), "env:CF_ACCOUNT_ID"
    fields = _vault_fields()
    if fields.get("account-id", "").strip():
        return fields["account-id"].strip(), f"vault:{VAULT_ITEM}/account-id"
    return VAULT_ITEM_LEGACY, "vault:legacy-item-name"


def resolve_token(kind: str) -> tuple[str, str]:
    """kind in (read, write). Return (token, source). Never log the token."""
    env_map = {
        "read": ["CF_API_TOKEN_READ", "CF_API_TOKEN"],
        "write": ["CF_API_TOKEN_WRITE", "CF_API_TOKEN"],
    }
    for var in env_map[kind]:
        if os.environ.get(var):
            return os.environ[var], f"env:{var}"
    fields = _vault_fields()
    key = "read-all" if kind == "read" else "write-all"
    tok = (fields.get(key) or "").strip()
    if not tok:
        raise SystemExit(f"vault field {key!r} empty on item {VAULT_ITEM}")
    return tok, f"vault:{VAULT_ITEM}/{key}"


def redact(s: str, keep: int = 8) -> str:
    s = s or ""
    return f"{s[:keep]}…(len {len(s)})" if len(s) > keep else f"…(len {len(s)})"


# ---------------------------------------------------------------- http

def api(method: str, path: str, token: str, body: dict | None = None) -> dict:
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method.upper(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # urllib raises HTTPError (a subclass) on 4xx/5xx
        payload = ""
        try:
            payload = e.read().decode()[:500]  # type: ignore[attr-defined]
        except Exception:
            payload = str(e)[:500]
        raise SystemExit(f"CF API {method} {path} failed: {payload}")


def check(resp: dict, action: str) -> list | dict:
    if not resp.get("success"):
        raise SystemExit(f"{action} failed: {json.dumps(resp.get('errors', resp))[:500]}")
    return resp.get("result")


# ---------------------------------------------------------------- commands

def cmd_auth_status(_a) -> int:
    acct, acct_src = resolve_account_id()
    out = {"account_id": redact(acct, 8), "account_source": acct_src}
    for kind in ("read", "write"):
        try:
            tok, src = resolve_token(kind)
            out[kind] = {"token": redact(tok, 3), "source": src}
        except SystemExit as e:
            out[kind] = {"error": str(e)}
    print(json.dumps(out, indent=2))
    return 0


def _tok(a, default: str) -> str:
    kind = a.token or default
    tok, _ = resolve_token(kind)
    return tok


def cmd_zones(a) -> int:
    tok = _tok(a, "read")
    q = f"?name={a.name}" if a.name else "?per_page=50"
    res = check(api("GET", f"/zones{q}", tok), "zones")
    rows = [{"id": z["id"], "name": z["name"], "status": z.get("status")} for z in res]
    print(json.dumps(rows, indent=2))
    return 0


def cmd_tunnel_list(a) -> int:
    acct, _ = resolve_account_id()
    tok = _tok(a, "read")
    res = check(api("GET", f"/accounts/{acct}/cfd_tunnel?per_page=50", tok), "tunnel-list")
    rows = [{"id": t["id"], "name": t.get("name"), "status": t.get("status"),
             "created_at": t.get("created_at")} for t in res]
    print(json.dumps(rows, indent=2))
    return 0


def cmd_tunnel_get(a) -> int:
    acct, _ = resolve_account_id()
    tok = _tok(a, "read")
    res = check(api("GET", f"/accounts/{acct}/cfd_tunnel/{a.tunnel_id}", tok), "tunnel-get")
    print(json.dumps(res, indent=2))
    return 0


def cmd_tunnel_create(a) -> int:
    acct, _ = resolve_account_id()
    tok = _tok(a, "write")
    res = check(api("POST", f"/accounts/{acct}/cfd_tunnel",
                    tok, {"name": a.name, "tunnel_secret": a.secret} if a.secret else {"name": a.name}),
                "tunnel-create")
    print(json.dumps(res, indent=2))
    return 0


def cmd_tunnel_delete(a) -> int:
    acct, _ = resolve_account_id()
    tok = _tok(a, "write")
    res = check(api("DELETE", f"/accounts/{acct}/cfd_tunnel/{a.tunnel_id}?cascade={str(a.cascade).lower()}", tok),
                "tunnel-delete")
    print(json.dumps({"deleted": True, "result": res}, indent=2))
    return 0


def cmd_connectors(a) -> int:
    # Connection health rides on the tunnel object itself (verified live);
    # there is no separate .../connectors endpoint on this API surface.
    acct, _ = resolve_account_id()
    tok = _tok(a, "read")
    if a.tunnel_id:
        tun = check(api("GET", f"/accounts/{acct}/cfd_tunnel/{a.tunnel_id}", tok), "tunnel-get")
        print(json.dumps({"id": tun["id"], "name": tun.get("name"), "status": tun.get("status"),
                          "connections": tun.get("connections", [])}, indent=2))
        return 0
    tunnels = check(api("GET", f"/accounts/{acct}/cfd_tunnel?per_page=50", tok), "tunnel-list")
    out = [{"tunnel": t.get("name"), "id": t["id"], "status": t.get("status"),
            "connections": t.get("connections", [])} for t in tunnels]
    print(json.dumps(out, indent=2))
    return 0


def cmd_tunnel_config_get(a) -> int:
    acct, _ = resolve_account_id()
    tok = _tok(a, "read")
    res = check(api("GET", f"/accounts/{acct}/cfd_tunnel/{a.tunnel_id}/configurations", tok), "config-get")
    print(json.dumps(res, indent=2))
    return 0


def cmd_tunnel_config_put(a) -> int:
    """--ingress 'hostname=app.00m.indevs.in,service=http://localhost:3081;service=http_status:404'"""
    acct, _ = resolve_account_id()
    tok = _tok(a, "write")
    ingress: list[dict] = []
    for rule in a.ingress.split(";"):
        rule = rule.strip()
        if not rule:
            continue
        entry: dict = {}
        for kv in rule.split(","):
            k, _, v = kv.partition("=")
            entry[k.strip()] = v.strip()
        ingress.append(entry)
    if not ingress or ingress[-1].get("service", "").startswith("http") and "hostname" in ingress[-1]:
        ingress.append({"service": "http_status:404"})
    res = check(api("PUT", f"/accounts/{acct}/cfd_tunnel/{a.tunnel_id}/configurations",
                    tok, {"config": {"ingress": ingress}}), "config-put")
    print(json.dumps(res, indent=2))
    return 0


def cmd_dns_route(a) -> int:
    tok = _tok(a, "write")
    target = f"{a.tunnel_id}.cfargotunnel.com"
    res = check(api("POST", f"/zones/{a.zone_id}/dns_records", tok,
                    {"type": "CNAME", "name": a.hostname, "content": target,
                     "proxied": True, "ttl": 1}), "dns-route")
    print(json.dumps(res, indent=2))
    return 0


def cmd_expose(a) -> int:
    """Full flow: ensure tunnel NAME exists, set ingress, CNAME hostname → tunnel."""
    acct, _ = resolve_account_id()
    rtok, _ = resolve_token("read")
    wtok, _ = resolve_token("write")
    tunnels = check(api("GET", f"/accounts/{acct}/cfd_tunnel?per_page=50", rtok), "tunnel-list")
    tun = next((t for t in tunnels if t.get("name") == a.tunnel), None)
    if not tun:
        tun = check(api("POST", f"/accounts/{acct}/cfd_tunnel", wtok, {"name": a.tunnel}), "tunnel-create")
    tid = tun["id"]
    ingress = [{"hostname": a.hostname, "service": a.service}, {"service": "http_status:404"}]
    check(api("PUT", f"/accounts/{acct}/cfd_tunnel/{tid}/configurations",
              wtok, {"config": {"ingress": ingress}}), "config-put")
    zones = check(api("GET", "/zones?per_page=50", rtok), "zones")
    zone = next((z for z in zones if a.hostname == z["name"] or a.hostname.endswith("." + z["name"])), None)
    dns = None
    if zone and not a.skip_dns:
        dns = check(api("POST", f"/zones/{zone['id']}/dns_records", wtok,
                        {"type": "CNAME", "name": a.hostname,
                         "content": f"{tid}.cfargotunnel.com", "proxied": True, "ttl": 1}),
                    "dns-route")
    print(json.dumps({"tunnel": {"id": tid, "name": a.tunnel},
                      "ingress": ingress,
                      "zone": zone["name"] if zone else None,
                      "dns": {"id": dns.get("id")} if dns else "skipped",
                      "run": f"cloudflared tunnel run {a.tunnel}  # token/config mode, see SKILL.md"},
                     indent=2))
    return 0


def cmd_cloudflared_ensure(_a) -> int:
    p = subprocess.run(["which", "cloudflared"], capture_output=True, text=True)
    if p.returncode == 0:
        r = _run(["cloudflared", "--version"])
        print((r.stdout or r.stderr).strip() or "cloudflared present")
        return 0
    url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb"
    print(f"installing cloudflared from {url} ...")
    r = subprocess.run(["sh", "-c", f"curl -fsSL -o /tmp/cloudflared.deb {url} && sudo dpkg -i /tmp/cloudflared.deb"],
                       text=True)
    return r.returncode


# ---------------------------------------------------------------- cli

def build() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cf.py", description="Cloudflare tunnel/connector manager (vault-backed)")
    p.add_argument("--token", choices=["read", "write"], default=None,
                   help="token selector (default: read for GET, write for mutating)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("auth-status", help="redacted credential check (no values)")
    z = sub.add_parser("zones", help="list zones (find the indevs domain)")
    z.add_argument("--name", default=None)
    sub.add_parser("tunnel-list", help="list cfd tunnels")
    g = sub.add_parser("tunnel-get", help="one tunnel")
    g.add_argument("tunnel_id")
    c = sub.add_parser("tunnel-create", help="create tunnel (write)")
    c.add_argument("name")
    c.add_argument("--secret", default=None, help="optional 32-byte base64 tunnel secret")
    d = sub.add_parser("tunnel-delete", help="delete tunnel (write)")
    d.add_argument("tunnel_id")
    d.add_argument("--cascade", action="store_true", help="also delete connections/config")
    k = sub.add_parser("connectors", help="connector status (all tunnels or one)")
    k.add_argument("tunnel_id", nargs="?")
    cg = sub.add_parser("tunnel-config-get", help="tunnel ingress config")
    cg.add_argument("tunnel_id")
    cp = sub.add_parser("tunnel-config-put", help="set ingress (write)")
    cp.add_argument("tunnel_id")
    cp.add_argument("--ingress", required=True)
    r = sub.add_parser("dns-route", help="CNAME hostname → tunnel (write)")
    r.add_argument("zone_id")
    r.add_argument("hostname")
    r.add_argument("tunnel_id")
    e = sub.add_parser("expose", help="ensure tunnel + ingress + DNS in one step (write)")
    e.add_argument("--tunnel", required=True)
    e.add_argument("--hostname", required=True, help="e.g. app.00m.indevs.in")
    e.add_argument("--service", required=True, help="e.g. http://localhost:3081")
    e.add_argument("--skip-dns", action="store_true")
    sub.add_parser("cloudflared-ensure", help="install cloudflared in container if missing")
    return p


def main() -> int:
    a = build().parse_args()
    fn = {"auth-status": cmd_auth_status, "zones": cmd_zones, "tunnel-list": cmd_tunnel_list,
          "tunnel-get": cmd_tunnel_get, "tunnel-create": cmd_tunnel_create,
          "tunnel-delete": cmd_tunnel_delete, "connectors": cmd_connectors,
          "tunnel-config-get": cmd_tunnel_config_get, "tunnel-config-put": cmd_tunnel_config_put,
          "dns-route": cmd_dns_route, "expose": cmd_expose,
          "cloudflared-ensure": cmd_cloudflared_ensure}[a.cmd]
    return fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
