"""Command-line interface for wtclient.

Backward-compatible with ``scripts/wt_httpx.py`` plus a ``grid`` command that
mirrors the useful subset of ``scripts/wt_browser.py``. Every surface supports
``--transport raw|browser`` where a browser fallback exists.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .clients.grid import GridClient
from .clients.market import MarketDataClient
from .config import MARKET_ORIGIN
from .errors import WunError
from .query import append_query, load_json_arg
from .secrets import Secrets, load_secrets
from .transport.browser import BrowserTransport
from .transport.hmac import OpenApiTransport
from .transport.mcp import McpTransport
from .transport.session import SessionTransport


def _print_json(value: Any) -> None:
    try:
        print(json.dumps(value, indent=2, default=str))
    except (TypeError, ValueError):
        print(value)


def _grid_transport(secrets: Secrets, use_browser: bool):
    if use_browser:
        return BrowserTransport()
    return SessionTransport(secrets.require_session())


def _market_transport(use_browser: bool):
    if use_browser:
        return BrowserTransport()
    from .transport.market import MarketTransport

    return MarketTransport()


def cmd_open_api(args: argparse.Namespace) -> int:
    secrets = load_secrets()
    key, secret = secrets.require_api_keys()
    transport = OpenApiTransport(key, secret, recv_window=args.recv_window)
    path = args.path
    if args.params:
        params = load_json_arg(args.params) or {}
        path = append_query(path, params)
    body = load_json_arg(args.data)
    response = transport.request(args.method, path, body=body, curl=args.curl)
    if args.curl:
        return 0 if response.ok else 1
    _print_json(_parse_response(response))
    return 0 if response.ok else 1


def cmd_session(args: argparse.Namespace) -> int:
    secrets = load_secrets()
    transport = _grid_transport(secrets, args.transport == "browser")
    body = load_json_arg(args.data)
    if args.transport == "browser" and args.curl:
        print("# browser transport: no curl equivalent (fetch runs in-page)", file=sys.stderr)
    if args.transport == "browser":
        response = transport.request(args.method, args.path, body=body)
    else:
        response = transport.request(args.method, args.path, body=body, curl=args.curl)
    _print_json(_parse_response(response))
    return 0 if response.ok else 1


def cmd_mcp(args: argparse.Namespace) -> int:
    secrets = load_secrets()
    key, secret = secrets.require_api_keys()
    transport = McpTransport(key, secret)
    params = load_json_arg(args.params) or {}
    result = transport.call_tool(args.tool, params, curl=args.curl)
    if not args.curl:
        _print_json(result)
    return 0


def cmd_market(args: argparse.Namespace) -> int:
    if args.transport == "browser":
        transport = BrowserTransport()
        url = args.path if args.path.startswith(("http://", "https://")) else f"{MARKET_ORIGIN}{args.path}"
        response = transport.fetch_market(url)
    else:
        transport = _market_transport(False)
        response = transport.request("GET", args.path)
    response.raise_for_status()
    _print_json(_parse_response(response))
    return 0


def cmd_grid(args: argparse.Namespace) -> int:
    secrets = load_secrets()
    transport = _grid_transport(secrets, args.transport == "browser")
    market = BrowserTransport() if args.transport == "browser" else None
    client = GridClient(transport, market=market)
    action = args.action

    if action == "list":
        _print_json(client.list(active_only=not args.all))
    elif action == "list-bots":
        _print_json(client.list_bots(args.bot_type, active_only=not args.all))
    elif action == "analyze":
        _require(args.arg, "analyze <EXCH:code>")
        _print_json(client.analyze(args.arg[0]))
    elif action == "create":
        _require(args.arg, "create <cfg.json>")
        payload = json.loads(open(args.arg[0], encoding="utf-8").read())
        result = client.create(payload, grid_market=args.grid_market)
        _print_json(_grid_create_summary(result))
    elif action == "edit":
        _require(args.arg, "edit <code> <cfg.json>")
        payload = json.loads(open(args.arg[1], encoding="utf-8").read())
        _print_json(client.edit(args.arg[0], payload, grid_market=args.grid_market))
    elif action == "stop":
        _require(args.arg, "stop <code> [stopCondition]")
        condition = args.arg[1] if len(args.arg) > 1 else "stop_only"
        _print_json(client.stop(args.arg[0], condition))
    elif action == "restart":
        _require(args.arg, "restart <code>")
        _print_json(client.restart(args.arg[0]))
    elif action == "close-all":
        _require(args.arg, "close-all <code>")
        _print_json(client.close_all(args.arg[0]))
    elif action == "delete":
        _require(args.arg, "delete <code>")
        _print_json(client.delete(args.arg[0]))
    elif action == "positions":
        _require(args.arg, "positions <code>")
        _print_json(client.positions(args.arg[0]))
    elif action == "positions-history":
        _require(args.arg, "positions-history <code>")
        _print_json(client.positions_history(args.arg[0]))
    elif action == "presets":
        limit = int(args.arg[0]) if args.arg else 10
        _print_json(client.presets(limit))
    elif action == "profiles":
        _print_json(client.profiles())
    else:
        raise SystemExit(f"unknown grid action {action!r}")
    return 0


def cmd_curl(args: argparse.Namespace) -> int:
    """Render a redacted curl equivalent without sending anything."""
    from .curl import curl_command

    body = load_json_arg(args.data)
    if args.path.startswith("/open_api"):
        secrets = load_secrets()
        key, secret = secrets.require_api_keys()
        transport = OpenApiTransport(key, secret)
        url, request_headers, body_str = transport.prepare(args.method, args.path, body=body)
        print(
            "# curl equivalent (open_api):\n"
            + curl_command(args.method, url, headers=request_headers, body=body_str)
        )
    else:
        secrets = load_secrets()
        transport = SessionTransport(secrets.require_session())
        url, request_headers, body_str = transport.prepare(args.method, args.path, body=body)
        note = ""
        if args.method.upper() not in ("GET", "HEAD"):
            note = "# note: add X-W-CSRF-Token from the logged-in cabinet page\n"
        print(
            "# curl equivalent (session):\n"
            + note
            + curl_command(args.method, url, headers=request_headers, cookies=transport.cookies, body=body_str)
        )
    return 0


def _parse_response(response: Any) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text


def _grid_create_summary(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"result": result}
    inner = result.get("result") or {}
    return {
        "status": result.get("status"),
        "gridBotCode": inner.get("gridBotCode"),
        "violations": result.get("violations"),
        "message": result.get("message") or inner.get("message"),
    }


def _require(values: list[str], usage: str) -> None:
    if not values:
        raise SystemExit(usage)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wtclient",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="surface", required=True)

    p = sub.add_parser("open_api", help="HMAC /open_api (no browser)")
    p.add_argument("method")
    p.add_argument("path")
    p.add_argument("--data", "--body", dest="data", help="JSON body or @file")
    p.add_argument("--params", help="JSON dict appended as query string")
    p.add_argument("--recv-window", default="60000")
    p.add_argument("--curl", action="store_true")
    p.add_argument("--pretty", action="store_true", default=True)
    p.set_defaults(func=cmd_open_api)

    p = sub.add_parser("session", help="session /en/trader (raw httpx or browser)")
    p.add_argument("method")
    p.add_argument("path")
    p.add_argument("--data", dest="data", help="JSON body or @file")
    p.add_argument("--curl", action="store_true")
    p.add_argument("--transport", choices=["raw", "browser"], default="raw")
    p.add_argument("--pretty", action="store_true", default=True)
    p.set_defaults(func=cmd_session)

    p = sub.add_parser("mcp", help="MCP streamable HTTP (no browser)")
    p.add_argument("tool")
    p.add_argument("--params", dest="params", default="{}")
    p.add_argument("--curl", action="store_true")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("market", help="public :2087 market data (no auth)")
    p.add_argument("path")
    p.add_argument("--curl", action="store_true")
    p.add_argument("--transport", choices=["raw", "browser"], default="raw")
    p.set_defaults(func=cmd_market)

    p = sub.add_parser("grid", help="grid bots (raw session or browser)")
    p.add_argument("action", choices=[
        "list", "list-bots", "analyze", "create", "edit", "stop", "restart",
        "close-all", "delete", "positions", "positions-history", "presets",
        "profiles",
    ])
    p.add_argument("arg", nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--transport", choices=["raw", "browser"], default="raw")
    p.add_argument("--grid-market", choices=["spot", "derivative"], default=None)
    p.add_argument("--bot-type", dest="bot_type", default=None, help="for list-bots")
    p.set_defaults(func=cmd_grid)

    p = sub.add_parser("curl", help="print a redacted curl equivalent without executing")
    p.add_argument("method")
    p.add_argument("path")
    p.add_argument("--data", dest="data")
    p.set_defaults(func=cmd_curl)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except WunError as exc:
        print(f"wtclient: {exc}", file=sys.stderr)
        return 1
    except SystemExit:
        raise
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
