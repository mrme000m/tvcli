#!/usr/bin/env python3
"""wundertrading httpx client — HMAC /open_api + session /en/trader without CloakBrowser.

Ports the two WunderTrading surfaces documented in the skill to pure httpx
so scripts and agents can run without a headful browser:

  1. /open_api  HMAC-SHA256 (same keys as MCP, no browser, no Cloudflare issue)
     — classic/DCA strategies, exchanges/markets/profiles/history. Verified
     recipe from references/rest-api.md.
  2. /en/trader  session + Cloudflare-fingerprinted XHR surface (the grid-bot
     configurator that wt.mjs/wt-grid.mjs/wt-bots.mjs previously drove via
     fetch-in-page). Best-effort httpx replay with curl-equivalent headers;
     raw HTTP without the right fingerprint/cookies still gets a 403
     "Just a moment…" — this module replays the exact browser headers and
     cookies so the same curl succeeds without launching Chromium. When
     Cloudflare still challenges, the error is surfaced with a remediation hint.

Stdlib + httpx only. No nodriver, no CDP, no Xvfb.

Secrets: reads the same files the prime-stack/bw-provision writes
  - browser-debug/secrets/runtime/wt.env               → WT_API_KEY / WT_API_SECRET  (HMAC + MCP)
  - browser-debug/secrets/runtime/wt-session.env       → WT_PHPSESSID / WT_CF_CLEARANCE / WT_COOKIES_JSON
  plus env vars WUN_API_KEY / WUN_SECRET_KEY / WT_*.

Usage:
  wt_httpx.py open_api GET /open_api/exchanges
  wt_httpx.py open_api GET "/open_api/api_profiles?limit=5"
  wt_httpx.py open_api GET /open_api/markets --params '{"exchanges":["HYPERLIQUID_SWAP"]}'
  wt_httpx.py session GET /en/trader/grid_bots/grid?page=1\\&limit=5
  wt_httpx.py session POST /en/trader/grid_bots/upsert?gridMarket=derivative --data @cfg.json
  wt_httpx.py session GET /en/trader/grid_bots/upsert --pretty
  wt_httpx.py mcp get_exchange_markets --params '{"exchanges":["HYPERLIQUID_SWAP"]}'
  wt_httpx.py curl GET /open_api/exchanges              # prints curl equivalent
  wt_httpx.py curl GET /en/trader/grid_bots/grid        # prints session curl

Each command prints JSON (or raw text) and its curl equivalent on stderr when
--curl is set.  Mirrors the wt.mjs / wt-bots.mjs / wt-grid.mjs command shapes
but over httpx.

Curl equivalents (what httpx imitates):
  # HMAC (references/rest-api.md)
  TS=$(python3 -c 'import time;print(int(time.time()*1000))'); RW=60000
  SIG=$(printf '%s\\n%s\\n%s\\n%s\\n%s' "$METHOD" "$PATH" "$TS" "$RW" "$BODY" | openssl dgst -sha256 -hmac "$WUN_SECRET_KEY" -binary | base64)
  curl -sS -X "$METHOD" "https://wundertrading.com$PATH" -H "X-API-Key: $WUN_API_KEY" -H "X-Signature: $SIG" -H "X-Timestamp: $TS" -H "X-Recv-Window: $RW" ${BODY:+-H Content-Type:application/json -d "$BODY"}

  # Session (grid-bot web surface)
  curl -sS "https://wundertrading.com/en/trader/grid_bots/grid?page=1&limit=5" \\
    -H "Accept: application/json" -H "X-Requested-With: XMLHttpRequest" \\
    -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.0.0" \\
    -H "X-W-CSRF-Token: $CSRF" -H "Referer: https://wundertrading.com/en/trader/grid_bots" \\
    -b "PHPSESSID=$PHPSESSID; cf_clearance=$CF"

  # MCP (streamable HTTP, sessionless)
  curl -sS -X POST https://wundertrading.com:2083/mcp -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "X-API-Key: $K" -H "X-Secret-Key: $S" -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_supported_exchanges","arguments":{}}}'
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

try:
    import httpx  # type: ignore
except ImportError as exc:
    print("httpx not installed — pip install httpx", file=sys.stderr)
    raise SystemExit(1) from exc

# ---------------------------------------------------------------------------
# secret loading (same files as bw-provision + wt.mjs)
# ---------------------------------------------------------------------------
WORKSPACE = Path(__file__).resolve().parents[4]  # repo root from scripts/
WT_ENV = WORKSPACE / "browser-debug" / "secrets" / "runtime" / "wt.env"
WT_SESSION_ENV = WORKSPACE / "browser-debug" / "secrets" / "runtime" / "wt-session.env"
FALLBACK_ENV = WORKSPACE / "browser-debug" / ".env"

def _parse_env_file(p: Path) -> dict:
    out: dict[str, str] = {}
    if not p.is_file():
        return out
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out

def load_secrets() -> dict:
    env: dict[str, str] = {}
    for p in (WT_ENV, WT_SESSION_ENV, FALLBACK_ENV):
        env.update(_parse_env_file(p))
    # env vars win
    for k in list(os.environ.keys()):
        if k.startswith("WUN_") or k.startswith("WT_") or k in ("CLOUDFLARE_ACCOUNT_ID",):
            env[k] = os.environ[k]
    return env

SECRETS = load_secrets()

def secret(name: str, *alts: str) -> str | None:
    for k in (name, *alts):
        if SECRETS.get(k):
            return SECRETS[k]
    return None

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------
WT_ORIGIN = "https://wundertrading.com"
MCP_URL = "https://wundertrading.com:2083/mcp"
MARKET_ORIGIN = "https://wundertrading.com:2087"

# CloakBrowser fingerprint the cabinet expects (Windows Chrome 146, verified
# via browser-debug/launch.mjs fallbacks). Using the same UA as the headful
# session dramatically improves the success rate of raw httpx against
# Cloudflare's fingerprint check (still not 100% — cf_clearance may lag).
CLOAK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.7680.177.5 Safari/537.36"
)

# ---------------------------------------------------------------------------
# HMAC /open_api client (no browser)
# ---------------------------------------------------------------------------

def hmac_signature(secret: str, method: str, path: str, ts: str, rw: str, body: str) -> str:
    payload = "\n".join([method.upper(), path, ts, rw, body])
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.b64encode(sig).decode()


def open_api_request(
    method: str,
    path: str,
    body: dict | str | None = None,
    recv_window: str = "60000",
    api_key: str | None = None,
    api_secret: str | None = None,
    verbose_curl: bool = False,
) -> httpx.Response:
    """HMAC-signed /open_api request via httpx (curl-equivalent).

    Mirrors the bash recipe in references/rest-api.md. PATH must include
    query string exactly as sent. BODY is serialized JSON or empty string.
    """
    key = api_key or secret("WUN_API_KEY", "WT_API_KEY", "WT_API_KEY") or ""
    sec = api_secret or secret("WUN_SECRET_KEY", "WT_API_SECRET") or ""
    if not key or not sec:
        raise SystemExit("open_api: missing WUN_API_KEY/WUN_SECRET_KEY (or WT_API_KEY/WT_API_SECRET)")
    ts = str(int(time.time() * 1000))
    body_str = ""
    if body is not None:
        if isinstance(body, (dict, list)):
            body_str = json.dumps(body, separators=(",", ":"))
        else:
            body_str = str(body)
    sig = hmac_signature(sec, method.upper(), path, ts, recv_window, body_str)
    headers = {
        "X-API-Key": key,
        "X-Signature": sig,
        "X-Timestamp": ts,
        "X-Recv-Window": recv_window,
        "Accept": "application/json",
        "User-Agent": CLOAK_UA,
    }
    if body_str:
        headers["Content-Type"] = "application/json"
    url = f"{WT_ORIGIN}{path}"
    if verbose_curl:
        _print_curl(method.upper(), url, headers, body_str, file=sys.stderr)
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.request(method.upper(), url, headers=headers, content=body_str.encode() if body_str else None)
    return resp


def _print_curl(method: str, url: str, headers: dict, body: str, file=sys.stderr):
    parts = ["curl", "-sS", "-X", method, f'"{url}"']
    for k, v in headers.items():
        # redact signatures/keys in the printed curl
        if k in ("X-API-Key", "X-Signature"):
            v = v[:6] + "…REDACTED"
        parts.extend(["-H", f'"{k}: {v}"'])
    if body:
        parts.extend(["-d", f"'{body[:800]}'"])
    print("# curl equivalent:", file=file)
    print(" ".join(parts), file=file)

# ---------------------------------------------------------------------------
# Session /en/trader client (grid bots etc.) via httpx
# ---------------------------------------------------------------------------

def _session_cookies() -> dict[str, str]:
    """PHPSESSID + cf_clearance from wt-session.env (or WT_COOKIES_JSON)."""
    cookies: dict[str, str] = {}
    j = secret("WT_COOKIES_JSON")
    if j:
        try:
            for c in json.loads(j):
                if c.get("name") and c.get("value"):
                    cookies[c["name"]] = c["value"]
        except Exception:
            pass
    for k, ck in (("WT_PHPSESSID", "PHPSESSID"), ("WT_CF_CLEARANCE", "cf_clearance")):
        v = secret(k)
        if v:
            cookies[ck] = v
    # also allow direct PHPSESSID / cf_clearance env names
    for ck in ("PHPSESSID", "cf_clearance"):
        if ck not in cookies and os.environ.get(ck):
            cookies[ck] = os.environ[ck]
    return cookies

SESSION_CSRF_CACHE: dict[str, str] = {}

def fetch_csrf_token(client: httpx.Client) -> str:
    """Extract window.baseServerConfig.appCsrfToken from the grid_bots page.

    GET https://wundertrading.com/en/trader/grid_bots with session cookies,
    parse the inline JS config. Cached per process.
    """
    if "csrf" in SESSION_CSRF_CACHE:
        return SESSION_CSRF_CACHE["csrf"]
    # try a few cabinet pages — the token is on every /en/trader/* page
    for path in ("/en/trader/grid_bots", "/en/trader/dashboard", "/en/trader/grid_bots/upsert"):
        try:
            resp = client.get(f"{WT_ORIGIN}{path}", headers={"Accept": "text/html,application/xhtml+xml"})
        except Exception:
            continue
        if resp.status_code != 200:
            continue
        text = resp.text
        # window.baseServerConfig = {..., appCsrfToken: "...", ...}
        m = re.search(r'appCsrfToken\s*[:=]\s*["\']([^"\']{20,})["\']', text)
        if m:
            tok = m.group(1)
            SESSION_CSRF_CACHE["csrf"] = tok
            return tok
        # fallback: meta tag
        m = re.search(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']', text, re.I)
        if m:
            tok = m.group(1)
            SESSION_CSRF_CACHE["csrf"] = tok
            return tok
    raise RuntimeError("could not extract CSRF token — session cookies stale or Cloudflare challenge active (re-login via wt.mjs and re-export wt-session.env)")


def session_client() -> httpx.Client:
    """httpx.Client with WunderTrading session cookies + browser headers.

    Imitates the fetch-in-page headers from wt.mjs: Accept, X-Requested-With,
    Referer, User-Agent, and the cookie jar.
    """
    cookies = _session_cookies()
    if not cookies.get("PHPSESSID"):
        raise SystemExit("session: missing PHPSESSID — run: node browser-debug/wt.mjs  (then bw-provision.sh --export wundertrading-session)")
    headers = {
        "User-Agent": CLOAK_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{WT_ORIGIN}/en/trader/grid_bots",
        "Origin": WT_ORIGIN,
        "Cache-Control": "no-cache",
    }
    # httpx cookie handling: pass dict, let client manage
    client = httpx.Client(
        headers=headers,
        cookies=cookies,  # type: ignore[arg-type]
        timeout=30.0,
        follow_redirects=True,
        http2=False,
    )
    return client


def session_request(
    method: str,
    path: str,
    body: dict | None = None,
    verbose_curl: bool = False,
) -> httpx.Response:
    """Session-auth /en/trader request via httpx (curl-equivalent).

    Adds X-W-CSRF-Token for non-safe methods by fetching the token first.
    Mirrors the fetch-in-page pattern from browser-debug/wt-grid.mjs:
      headers: {Accept: application/json, Content-Type: application/json,
                X-W-CSRF-Token: window.baseServerConfig.appCsrfToken}
    """
    client = session_client()
    # normalize path: must be absolute with leading /
    if not path.startswith("/"):
        path = "/" + path
    url = f"{WT_ORIGIN}{path}" if path.startswith("/en/") or path.startswith("/cdn-cgi/") else path
    headers: dict[str, str] = {}
    if method.upper() not in ("GET", "HEAD"):
        try:
            tok = fetch_csrf_token(client)
            headers["X-W-CSRF-Token"] = tok
        except Exception as exc:
            client.close()
            raise SystemExit(str(exc)) from exc
    if body is not None:
        headers["Content-Type"] = "application/json"
    body_bytes = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    if verbose_curl:
        _print_session_curl(method.upper(), url, client, headers, body, file=sys.stderr)
    resp = client.request(method.upper(), url, headers=headers, content=body_bytes)
    client.close()
    # surface Cloudflare challenge explicitly
    if resp.status_code == 403 and "Just a moment" in resp.text:
        raise SystemExit(
            "session: Cloudflare challenge (403 Just a moment) — raw httpx was "
            "fingerprinted. Remediation: refresh cf_clearance via `node browser-debug/wt.mjs` "
            "(re-exports wt-session.env) and retry, or keep using wt.mjs fetch-in-page "
            "which uses the real browser TLS fingerprint. The curl/httpx replay succeeds "
            "once cf_clearance is fresh (typically 30m–2h)."
        )
    if resp.status_code == 403 and "Invalid CSRF" in resp.text:
        SESSION_CSRF_CACHE.pop("csrf", None)
        raise SystemExit("session: Invalid CSRF token — token rotated; retry (will refetch)")
    return resp


def _print_session_curl(method: str, url: str, client: httpx.Client, extra_headers: dict, body: dict | None, file=sys.stderr):
    parts = ["curl", "-sS", "-X", method, f'"{url}"']
    # merge client headers
    for k, v in {**client.headers, **extra_headers}.items():
        if k.lower() in ("cookie", "x-w-csrf-token"):
            v = v[:12] + "…REDACTED" if len(v) > 12 else v
        parts.extend(["-H", f'"{k}: {v}"'])
    # cookies
    cookie_header = "; ".join(f"{k}={v[:8]}…REDACTED" for k, v in client.cookies.items())
    if cookie_header:
        parts.extend(["-b", f'"{cookie_header}"'])
    if body is not None:
        parts.extend(["-d", f"'{json.dumps(body, separators=(',', ':'))[:800]}'"])
    print("# curl equivalent (session):", file=file)
    print(" ".join(parts), file=file)

# ---------------------------------------------------------------------------
# MCP streamable HTTP client (same as token_screen.py / grid_config.py, but httpx)
# ---------------------------------------------------------------------------

def mcp_call(tool: str, arguments: dict, verbose_curl: bool = False) -> dict:
    key = secret("WUN_API_KEY", "WT_API_KEY") or ""
    sec = secret("WUN_SECRET_KEY", "WT_API_SECRET") or ""
    if not key or not sec:
        raise SystemExit("mcp: missing WUN_API_KEY/WUN_SECRET_KEY")
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    body = json.dumps(payload, separators=(",", ":"))
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-API-Key": key,
        "X-Secret-Key": sec,
        "User-Agent": CLOAK_UA,
    }
    if verbose_curl:
        print("# curl equivalent (mcp):", file=sys.stderr)
        print(f'curl -sS -X POST "{MCP_URL}" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -H "X-API-Key: {key[:6]}…REDACTED" -H "X-Secret-Key: …REDACTED" -d \'{body[:1000]}\'', file=sys.stderr)
    with httpx.Client(timeout=40.0) as client:
        resp = client.post(MCP_URL, headers=headers, content=body.encode())
    # MCP returns text/event-stream with one data: line
    text = resp.text
    # strip SSE framing
    if "data:" in text:
        text = text.split("data:", 1)[1].strip().split("\n", 1)[0]
    try:
        data = json.loads(text)
    except Exception:
        raise SystemExit(f"mcp: unparseable response {resp.status_code}: {text[:800]}")
    if "error" in data:
        raise SystemExit(f"mcp error {data['error']}")
    # result.content[0].text is JSON string for most tools
    try:
        content = data["result"]["content"][0]["text"]
        return json.loads(content)
    except Exception:
        return data["result"]

# ---------------------------------------------------------------------------
# Public market data on :2087 (no auth, no CSRF, no HMAC) — plain httpx
# ---------------------------------------------------------------------------

def market_get(path: str, verbose_curl: bool = False) -> dict:
    """GET https://wundertrading.com:2087/... — public market data origin.

    Note (2026-09-04 live): this origin is now also Cloudflare-managed;
    raw httpx/curl gets 403 Just a moment without a fresh cf_clearance.
    wt-grid.mjs fetches it via fetch-in-page (browser TLS fingerprint) for
    this reason. This helper tries httpx first; on 403 it raises with a
    hint to use exchange public APIs (market_regime.py) or the browser path.
    """
    url = f"{MARKET_ORIGIN}{path}" if path.startswith("/") else path
    if verbose_curl:
        print(f'# curl -sS "{url}"', file=sys.stderr)
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        resp = client.get(url, headers={"Accept": "application/json", "User-Agent": CLOAK_UA})
    if resp.status_code == 403 and "Just a moment" in resp.text:
        raise SystemExit(
            "market :2087 is Cloudflare-gated (403 Just a moment) — use exchange "
            "public APIs (scripts/market_regime.py → api.binance.com / api.hyperliquid.xyz) "
            "or fetch via browser: node browser-debug/wt.mjs api GET /en/trader/... or "
            "wt_httpx.py session. The :2087 parity is kept for browser-in-page fetches."
        )
    resp.raise_for_status()
    return resp.json()

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_params(s: str | None) -> dict | None:
    if s is None or s == "":
        return None
    if s.startswith("@"):
        return json.loads(Path(s[1:]).read_text())
    return json.loads(s)

def _maybe_json(text: str):
    try:
        return json.loads(text)
    except Exception:
        return text

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="surface", required=True)

    # open_api
    p_open = sub.add_parser("open_api", help="HMAC /open_api (no browser)")
    p_open.add_argument("method", help="GET|POST|PATCH|PUT|DELETE")
    p_open.add_argument("path", help="/open_api/... including query string")
    p_open.add_argument("--data", "--body", dest="data", help="JSON body or @file")
    p_open.add_argument("--params", help="JSON dict appended as query string (alternative to path)")
    p_open.add_argument("--recv-window", default="60000")
    p_open.add_argument("--curl", action="store_true", help="print curl equivalent to stderr")
    p_open.add_argument("--pretty", action="store_true", default=True)

    # session
    p_sess = sub.add_parser("session", help="session /en/trader (grid bots, CSRF, httpx without browser)")
    p_sess.add_argument("method")
    p_sess.add_argument("path")
    p_sess.add_argument("--data", dest="data", help="JSON body or @file")
    p_sess.add_argument("--curl", action="store_true")
    p_sess.add_argument("--pretty", action="store_true", default=True)

    # mcp
    p_mcp = sub.add_parser("mcp", help="MCP streamable HTTP (no browser)")
    p_mcp.add_argument("tool", help="e.g. get_supported_exchanges, get_exchange_markets")
    p_mcp.add_argument("--params", dest="params", default="{}", help="JSON arguments or @file")
    p_mcp.add_argument("--curl", action="store_true")

    # market
    p_mkt = sub.add_parser("market", help="public :2087 market data (no auth)")
    p_mkt.add_argument("path", help="/all-markets, /market?marketCode=..., /ohlc?... ")
    p_mkt.add_argument("--curl", action="store_true")

    # curl printer
    p_curl = sub.add_parser("curl", help="print curl equivalent without executing")
    p_curl.add_argument("method")
    p_curl.add_argument("path")
    p_curl.add_argument("--data", dest="data")

    args = ap.parse_args()

    if args.surface == "open_api":
        path = args.path
        if args.params:
            qs = urlencode(json.loads(args.params) if isinstance(args.params, str) and args.params.strip().startswith("{") else _load_params(args.params) or {})
            path = f"{path}?{qs}" if "?" not in path else f"{path}&{qs}"
        body = _load_params(args.data) if args.data else None
        resp = open_api_request(args.method, path, body, recv_window=args.recv_window, verbose_curl=args.curl)
        print(resp.text if not args.curl else "")
        if not args.curl:
            try:
                print(json.dumps(resp.json(), indent=2))
            except Exception:
                print(resp.text)
        sys.exit(0 if resp.is_success else 1)

    if args.surface == "session":
        body = _load_params(args.data) if args.data else None
        resp = session_request(args.method, args.path, body, verbose_curl=args.curl)
        if args.curl:
            # already printed on stderr
            pass
        try:
            print(json.dumps(resp.json(), indent=2))
        except Exception:
            print(resp.text)
        sys.exit(0 if resp.is_success else 1)

    if args.surface == "mcp":
        params = _load_params(args.params) or {}
        if isinstance(params, str):
            params = json.loads(params)
        result = mcp_call(args.tool, params, verbose_curl=args.curl)
        print(json.dumps(result, indent=2))
        return

    if args.surface == "market":
        data = market_get(args.path, verbose_curl=args.curl)
        print(json.dumps(data, indent=2))
        return

    if args.surface == "curl":
        # just print curl for open_api vs session heuristic
        if args.path.startswith("/open_api"):
            body = _load_params(args.data) if args.data else None
            body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
            extra = "-H Content-Type:application/json -d '" + body_str + "'" if body_str else ""
            print(f'TS=$(python3 -c \'import time;print(int(time.time()*1000))\'); RW=60000; SIG=$(printf \'%s\\n%s\\n%s\\n%s\\n%s\' "{args.method.upper()}" "{args.path}" "$TS" "$RW" \'{body_str}\' | openssl dgst -sha256 -hmac "$WUN_SECRET_KEY" -binary | base64)')
            print(f'curl -sS -X {args.method.upper()} "https://wundertrading.com{args.path}" -H "X-API-Key: $WUN_API_KEY" -H "X-Signature: $SIG" -H "X-Timestamp: $TS" -H "X-Recv-Window: $RW" {extra}')
        else:
            cookies = _session_cookies()
            print(f'curl -sS -X {args.method.upper()} "https://wundertrading.com{args.path}" -H "Accept: application/json" -H "X-W-CSRF-Token: $CSRF" -H "User-Agent: {CLOAK_UA[:40]}..." -b "PHPSESSID={cookies.get("PHPSESSID","")[:8]}…; cf_clearance=…"')

if __name__ == "__main__":
    main()
