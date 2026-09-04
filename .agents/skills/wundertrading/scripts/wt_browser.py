#!/usr/bin/env python3
"""wundertrading browser-backed httpx client — grid-bot configurator via CloakBrowser CDP.

Ports the Node mjs grid-bot surface (wt-grid.mjs, wt-bots.mjs, wt.mjs) to
Python with **httpx + websockets** but keeps the **headful browser** for the
fingerprint-gated `/en/trader/*` surface (the only way to reliably talk to the
grid-bot configurator — no HMAC `open_api` covers it, and raw httpx gets
Cloudflare 403 even with fresh cookies). This is the `httpx with the browser`
you asked for: Python drives the same `fetch()` *inside* the logged-in
CloakBrowser page over CDP, so Cloudflare/TLS + PHPSESSID + X-W-CSRF-Token
are satisfied, but the driver is Python instead of Node.

Architecture (mirrors wt-grid.mjs):
  1. httpx GET http://127.0.0.1:9222/json → pick wundertrading.com page
  2. websockets.connect(ws://.../devtools/page/...) → CDP WebSocket
  3. Runtime.evaluate `fetch(path,{method,headers:{X-W-CSRF-Token},credentials:include})`
     inside the page — exactly the headers wt-grid.mjs uses.

Requires:
  - httpx + websockets (pip install httpx websockets)
  - headful CloakBrowser up and logged in: `node browser-debug/launch.mjs`
    then `node browser-debug/wt.mjs` (or at least the browser on 9222 with a
    wundertrading.com page). Secrets come from browser-debug/secrets/runtime/
    wt-session.env via the profile's cookies (no keys needed for this path).

For the *official* surfaces that work **without** a browser, use:
  - wt_httpx.py open_api ...  (HMAC /open_api, classic/DCA)
  - wt_httpx.py mcp ...        (streamable HTTP :2083/mcp, same keys)

Grid-bot CRUD here is verified live 2026-09-04 on demo-hype paper profile
(docs: browser-debug/docs/wt/grid-bot-api.md, .agents/skills/wundertrading/
references/grid-bot.md).

Usage (mirrors wt-grid.mjs + wt-bots.mjs, all via httpx+CDP):
  wt_browser.py grid list [--all]
  wt_browser.py grid analyze HYPERLIQUID_SWAP:191
  wt_browser.py grid create cfg.json          # gridMarket=derivative
  wt_browser.py grid stop <code> [stop_only|stop_and_close_all|...]
  wt_browser.py grid restart <code>
  wt_browser.py grid close-all <code>
  wt_browser.py grid delete <code>
  wt_browser.py grid positions <code>
  wt_browser.py grid presets [limit]
  wt_browser.py grid profiles                 # exchangesProfiles
  wt_browser.py bots signal list [--all]      # wt-bots parity: signal|grid|dca|mn|mp
  wt_browser.py bots grid list
  wt_browser.py bots dca create cfg.json
  wt_browser.py api GET /en/trader/grid_bots/grid?page=1\\&limit=5
  wt_browser.py api POST /en/trader/grid_bots/upsert?gridMarket=derivative --data @cfg.json
  wt_browser.py raw --curl GET /en/trader/grid_bots/grid   # curl that the browser fetch imitates
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

try:
    import websockets  # type: ignore
except ImportError as e:
    print("websockets not installed — pip install websockets", file=sys.stderr)
    raise SystemExit(1) from e

CDP_BASE = "http://127.0.0.1:9222"
CLOAK_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.7680.177.5"


# ---------------------------------------------------------------------------
# CDP helpers (httpx for /json discovery, websockets for Runtime.evaluate)
# ---------------------------------------------------------------------------

async def find_page() -> dict:
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(f"{CDP_BASE}/json")
        resp.raise_for_status()
        targets = resp.json()
    # prefer wundertrading.com page, fallback to any page with ws
    for t in targets:
        if t.get("type") == "page" and "wundertrading.com" in (t.get("url") or "") and t.get("webSocketDebuggerUrl"):
            return t
    for t in targets:
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return t
    raise SystemExit("no WT page target — run: node browser-debug/wt.mjs (or node browser-debug/launch.mjs)")

async def cdp_call(ws_url: str, method: str, params: dict | None = None, timeout: float = 15) -> dict:
    params = params or {}
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"id": 1, "method": method, "params": params}))
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        if "error" in msg:
            raise RuntimeError(msg["error"].get("message", str(msg["error"])))
        return msg

async def fetch_in_page(ws_url: str, method: str, path: str, body: dict | None = None, _csrf_retry: bool = True) -> dict:
    """Browser fetch() via Runtime.evaluate — imitates wt-grid.mjs callApi.

    Headers: Accept: application/json, Content-Type when body, X-W-CSRF-Token
    for non-safe methods (from window.baseServerConfig.appCsrfToken).
    credentials: include so PHPSESSID/cf_clearance ride.

    CSRF rotation: appCsrfToken goes stale once the page has been open for
    a while (server: 400 "Invalid CSRF token ... Refresh the page"). On that
    exact failure for a state-changing method, reload the page once (fresh
    token) and retry.
    """
    # build JS that runs inside the page; body is JSON-stringified in JS
    body_js = "undefined" if body is None else json.dumps(body)
    expr = f"""(async () => {{
      const method = {json.dumps(method)};
      const path = {json.dumps(path)};
      const body = {body_js};
      const headers = {{ 'Accept': 'application/json' }};
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      if (!['GET','HEAD'].includes(method)) headers['X-W-CSRF-Token'] = window.baseServerConfig.appCsrfToken;
      try {{
        const r = await fetch(path, {{ method, headers, credentials: 'include', ...(body !== undefined ? {{ body: JSON.stringify(body) }} : {{}}) }});
        const text = await r.text();
        let j = null; try {{ j = JSON.parse(text); }} catch {{}}
        return {{ status: r.status, ok: r.ok, json: j ?? text.slice(0,1200), text: j ? undefined : text.slice(0,1200) }};
      }} catch (e) {{ return {{ status: 0, ok: false, error: String(e) }}; }}
    }})()"""
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expr, "awaitPromise": True, "returnByValue": True}}))
        raw = await asyncio.wait_for(ws.recv(), timeout=20)
        msg = json.loads(raw)
        if msg.get("error"):
            raise RuntimeError(msg["error"].get("message", str(msg)))
        val = msg.get("result", {}).get("result", {}).get("value")
        if val is None:
            exc = msg.get("result", {}).get("exceptionDetails")
            raise RuntimeError(f"no result: {json.dumps(exc)[:300] if exc else 'empty'}")
        # stale-CSRF retry: navigate to the canonical trader page (always
        # repopulates window.baseServerConfig.appCsrfToken), then re-run
        async def _csrf_reload():
            async with websockets.connect(ws_url, max_size=None) as wsr:
                await wsr.send(json.dumps({"id": 2, "method": "Runtime.evaluate",
                                           "params": {"expression":
                                               "location.href='https://wundertrading.com/en/trader/grid_bots'"}}))
            for _ in range(12):  # wait up to ~24s for the SPA + token
                await asyncio.sleep(2)
                async with websockets.connect(ws_url, max_size=None) as wst:
                    await wst.send(json.dumps({"id": 3, "method": "Runtime.evaluate",
                                               "params": {"expression":
                                                   "(window.baseServerConfig && window.baseServerConfig.appCsrfToken) ? 'ready' : 'waiting'",
                                                   "returnByValue": True}}))
                    rawt = await asyncio.wait_for(wst.recv(), timeout=10)
                    if json.loads(rawt).get("result", {}).get("result", {}).get("value") == "ready":
                        return True
            return False
        if _csrf_retry and method.upper() not in ("GET", "HEAD") and not val.get("ok") \
                and val.get("status") == 400 and "CSRF" in json.dumps(val.get("json") or val.get("text") or ""):
            try:
                if await _csrf_reload():
                    return await fetch_in_page(ws_url, method, path, body, _csrf_retry=False)
            except Exception:
                pass
        return val

async def fetch_market_in_page(ws_url: str, url: str) -> dict:
    """Public :2087 market data via browser fetch with credentials omit.

    Mirrors wt-grid.mjs callMarket — no cookies, no custom headers (CORS).
    This path works even when raw httpx gets Cloudflare 403, because it uses
    the browser's TLS fingerprint.
    """
    expr = f"""(async () => {{
      try {{
        const r = await fetch({json.dumps(url)}, {{ credentials: 'omit' }});
        return {{ status: r.status, ok: r.ok, json: await r.json() }};
      }} catch (e) {{ return {{ status: 0, ok: false, error: String(e) }}; }}
    }})()"""
    async with websockets.connect(ws_url, max_size=None) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expr, "awaitPromise": True, "returnByValue": True}}))
        raw = await asyncio.wait_for(ws.recv(), timeout=20)
        msg = json.loads(raw)
        val = msg.get("result", {}).get("result", {}).get("value")
        if val is None:
            raise RuntimeError("market fetch: no result")
        return val

def curl_equivalent(method: str, path: str, body: dict | None = None) -> str:
    """Curl that the browser fetch imitates (for docs, not for raw execution)."""
    # This is the fetch() inside the page — not a raw curl (raw hits CF 403).
    # Shown for parity with wt_httpx.py --curl.
    headers = ["-H 'Accept: application/json'"]
    if body is not None:
        headers.append("-H 'Content-Type: application/json'")
    headers.append("-H 'X-W-CSRF-Token: $CSRF'  # window.baseServerConfig.appCsrfToken")
    headers.append("-H 'X-Requested-With: XMLHttpRequest'")
    headers.append(f"-H 'User-Agent: {CLOAK_UA[:50]}...'")
    headers.append("-b 'PHPSESSID=$PHPSESSID; cf_clearance=$CF'")
    body_part = f" -d '{json.dumps(body)[:600]}'" if body is not None else ""
    return f"# browser fetch → curl that would be sent if Cloudflare allowed it (use this file instead):\ncurl -sS -X {method} \"https://wundertrading.com{path}\" {' '.join(headers)}{body_part}"

# ---------------------------------------------------------------------------
# grid-bot commands (parity with wt-grid.mjs)
# ---------------------------------------------------------------------------

async def cmd_grid_list(all_: bool):
    page = await find_page()
    path = "/en/trader/grid_bots/grid?page=1&limit=50" + ("" if all_ else "&criteria%5Bstatuses%5D%5Bvalue%5D%5B%5D=active")
    r = await fetch_in_page(page["webSocketDebuggerUrl"], "GET", path)
    if not r.get("ok"):
        raise SystemExit(f"list failed {r}")
    for it in (r.get("json", {}).get("_embedded", {}).get("items") or []):
        b = it.get("resource", {})
        links = {k: f"{v['data']['method']} {v['data']['link']}" for k, v in (it.get("actions") or {}).items() if isinstance(v.get("data"), dict) and v["data"].get("link")}
        print(json.dumps({"code": b.get("code"), "status": b.get("status"), "pair": b.get("pair", {}).get("unifiedCode"),
                          "exchange": b.get("exchange", {}).get("code"), "paperTrading": b.get("paperTrading"),
                          "gridTradingType": b.get("gridTradingType"), "gridType": b.get("gridType"),
                          "step": b.get("gridPercentStep"), "levels": b.get("gridLevels"),
                          "high": b.get("highPrice"), "low": b.get("lowPrice"), "actions": links}, indent=1))

async def cmd_grid_analyze(code: str):
    page = await find_page()
    # three parallel market fetches via browser (same as wt-grid.mjs Promise.all)
    ws = page["webSocketDebuggerUrl"]
    results = await asyncio.gather(
        fetch_market_in_page(ws, f"https://wundertrading.com:2087/market?marketCode={code}"),
        fetch_market_in_page(ws, f"https://wundertrading.com:2087/ohlc/last?code={code}&timeframe=15"),
        fetch_market_in_page(ws, f"https://wundertrading.com:2087/ohlc/low-high?code={code}&timeframe=15&limit=2976"),
    )
    print(json.dumps({"market": results[0].get("json"), "lastCandle": results[1].get("json"), "thirtyDayHighLow": results[2].get("json")}, indent=2))

async def cmd_grid_create(cfg_path: str):
    import json as _j
    body = _j.loads(Path(cfg_path).read_text())
    grid_market = body.pop("gridMarketHint", "derivative")
    page = await find_page()
    r = await fetch_in_page(page["webSocketDebuggerUrl"], "POST", f"/en/trader/grid_bots/upsert?gridMarket={grid_market}", body)
    print(json.dumps({"status": r.get("status"), "ok": r.get("ok"), "gridBotCode": r.get("json", {}).get("result", {}).get("gridBotCode"), "violations": r.get("json", {}).get("violations"), "message": r.get("json", {}).get("message") or r.get("json", {}).get("result", {}).get("message")}, indent=2))
    if not r.get("ok"):
        raise SystemExit(1)

async def cmd_grid_simple(method: str, path: str):
    page = await find_page()
    body = {} if method == "POST" else None
    r = await fetch_in_page(page["webSocketDebuggerUrl"], method, path, body)
    print(json.dumps({"status": r.get("status"), "ok": r.get("ok"), "json": r.get("json")}, indent=2))
    if not r.get("ok"):
        raise SystemExit(1)

async def cmd_api(method: str, path: str, data: str | None):
    body = json.loads(Path(data[1:]).read_text()) if data and data.startswith("@") else (json.loads(data) if data else None)
    page = await find_page()
    r = await fetch_in_page(page["webSocketDebuggerUrl"], method.upper(), path, body)
    if r.get("json") is not None:
        print(json.dumps(r["json"], indent=2))
    else:
        print(json.dumps(r, indent=2))
    if not r.get("ok"):
        raise SystemExit(1)

# bot types parity with wt-bots.mjs (signal/grid/dca/mn/mp)
BOTS = {
    "signal": {"list": lambda p: f"/en/trader/signal_bots/grid?{p}", "upsert": "/en/trader/signal_bots/upsert"},
    "grid":   {"list": lambda p: f"/en/trader/grid_bots/grid?{p}",   "upsert": "/en/trader/grid_bots/upsert"},
    "dca":    {"list": lambda p: f"/en/trader/dca_bots/grid?{p}",     "upsert": "/en/trader/dca_bots/upsert"},
    "mn":     {"list": lambda _: "/en/trader/market_neutral/grid",    "upsert": "/en/trader/market_neutral/upsert"},
    "mp":     {"list": lambda _: "/en/trader/multi_pair_grid_bot/grid?limit=200", "upsert": "/en/trader/multi_pair_grid_bot/upsert"},
}

async def cmd_bots(btype: str, cmd: str, rest: list[str]):
    page = await find_page()
    ws = page["webSocketDebuggerUrl"]
    if cmd == "list":
        all_ = "--all" in rest
        p = "" if btype in ("mn","mp") else ("page=1&limit=50" if all_ else "page=1&limit=50&criteria%5Bstatuses%5D%5Bvalue%5D%5B%5D=active")
        path = BOTS[btype]["list"](p)
        r = await fetch_in_page(ws, "GET", path)
        if not r.get("ok"):
            raise SystemExit(f"list failed {r}")
        for it in (r.get("json", {}).get("_embedded", {}).get("items") or []):
            b = it.get("resource") or it
            print(json.dumps({"code": b.get("code"), "id": b.get("id"), "status": b.get("status"), "pair": b.get("pair",{}).get("unifiedCode") or b.get("pairCode"), "type": b.get("gridTradingType") or b.get("dcaTradingType") or b.get("type")}, indent=1))
    elif cmd == "create":
        cfg = json.loads(Path(rest[0]).read_text())
        # wt-bots grid needs ?gridMarket= derivative/spot
        path = BOTS[btype]["upsert"]
        if btype == "grid":
            gm = cfg.pop("gridMarketHint", "derivative")
            path = f"{path}?gridMarket={gm}"
        r = await fetch_in_page(ws, "POST", path, cfg)
        print(json.dumps({"status": r.get("status"), "ok": r.get("ok"), "json": r.get("json")}, indent=2))
        if not r.get("ok"):
            raise SystemExit(1)
    else:
        print(f"bots {btype}: list/create only in this httpx port — use wt_bots.mjs for stop/start/delete parity or add here")
        raise SystemExit(2)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    # grid
    p_grid = sub.add_parser("grid")
    p_grid.add_argument("action", choices=["list","analyze","create","stop","restart","close-all","delete","positions","presets","profiles"])
    p_grid.add_argument("arg", nargs="*", help="code / cfg.json / limit")
    p_grid.add_argument("--all", action="store_true")

    # bots (wt-bots parity)
    p_bots = sub.add_parser("bots")
    p_bots.add_argument("btype", choices=list(BOTS.keys()))
    p_bots.add_argument("action", choices=["list","create"])
    p_bots.add_argument("arg", nargs="*")

    # raw api via browser (like wt.mjs api)
    p_api = sub.add_parser("api")
    p_api.add_argument("method")
    p_api.add_argument("path")
    p_api.add_argument("--data", help="JSON or @file")

    # raw curl print
    p_raw = sub.add_parser("raw")
    p_raw.add_argument("--curl", action="store_true", default=True)
    p_raw.add_argument("method")
    p_raw.add_argument("path")
    p_raw.add_argument("--data")

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help(); raise SystemExit(2)

    async def run():
        if args.cmd == "grid":
            a = args.action
            if a == "list":
                await cmd_grid_list(args.all)
            elif a == "analyze":
                if not args.arg: raise SystemExit("analyze <EXCH:code>")
                await cmd_grid_analyze(args.arg[0])
            elif a == "create":
                if not args.arg: raise SystemExit("create <cfg.json>")
                await cmd_grid_create(args.arg[0])
            elif a == "stop":
                code = args.arg[0]; sc = args.arg[1] if len(args.arg) > 1 else "stop_only"
                await cmd_grid_simple("POST", f"/en/trader/grid_bots/{code}/stop?stopCondition={sc}&awaitStartSignal=true")
            elif a == "restart":
                await cmd_grid_simple("POST", f"/en/trader/grid_bots/{args.arg[0]}/restart")
            elif a == "close-all":
                await cmd_grid_simple("POST", f"/en/trader/grid_bots/{args.arg[0]}/close-all")
            elif a == "delete":
                await cmd_grid_simple("DELETE", f"/en/trader/grid_bots/{args.arg[0]}/delete")
            elif a == "positions":
                page = await find_page()
                r = await fetch_in_page(page["webSocketDebuggerUrl"], "GET", f"/en/trader/grid_bots/{args.arg[0]}/positions/grid?page=1&limit=50")
                print(json.dumps(r.get("json"), indent=2))
                if not r.get("ok"): raise SystemExit(1)
            elif a == "presets":
                page = await find_page()
                r = await fetch_in_page(page["webSocketDebuggerUrl"], "GET", f"/en/trader/grid_bots/presets?page=1&limit={args.arg[0] if args.arg else '10'}")
                if not r.get("ok"): raise SystemExit(f"presets failed {r}")
                for it in (r.get("json", {}).get("_embedded", {}).get("items") or []):
                    print(json.dumps({"pair": it.get("code"), "exchange": it.get("markets",[{}])[0].get("exchange"), "levels": it.get("grid_levels"), "step": it.get("grid_percent_step"), "high": it.get("high_price"), "low": it.get("low_price"), "roi": it.get("roi")}, indent=1))
            elif a == "profiles":
                page = await find_page()
                r = await fetch_in_page(page["webSocketDebuggerUrl"], "GET", "/en/trader/grid_bots/upsert")
                if not r.get("ok"): raise SystemExit(f"profiles failed {r}")
                for exch, accs in (r.get("json", {}).get("data", {}).get("exchangesProfiles") or {}).items():
                    if not accs: continue
                    for cid, acc in accs.items():
                        print(json.dumps({"exchange": exch, "code": cid, "name": acc.get("name_of_account"), "paperTrading": acc.get("paperTrading")}, indent=1))
            else:
                raise SystemExit(f"unknown grid {a}")
        elif args.cmd == "bots":
            await cmd_bots(args.btype, args.action, args.arg)
        elif args.cmd == "api":
            await cmd_api(args.method, args.path, args.data)
        elif args.cmd == "raw":
            body = json.loads(Path(args.data[1:]).read_text()) if args.data and args.data.startswith("@") else (json.loads(args.data) if args.data else None)
            print(curl_equivalent(args.method, args.path, body))

    asyncio.run(run())

if __name__ == "__main__":
    main()
