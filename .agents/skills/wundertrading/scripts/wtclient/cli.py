"""Command-line interface for wtclient.

Backward-compatible with ``scripts/wt_httpx.py`` plus a ``grid`` command that
mirrors the useful subset of ``scripts/wt_browser.py``. Every surface supports
``--transport raw|browser`` where a browser fallback exists.

The ``discover`` and ``debug`` subcommands expose the discovery + debug
machinery without writing any Python::

    wtclient discover surfaces                # print the known endpoint index
    wtclient discover probe GET /open_api/api_profiles?limit=5
    wtclient debug enable --level DEBUG
    wtclient debug status
    wtclient debug list-dumps --limit 5
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from .clients.exchanges import DEFAULT_VENUE_FAMILIES, ExchangesClient
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


def cmd_exchanges(args: argparse.Namespace) -> int:
    """my-exchanges surface — profiles, plan limits, paper-profile creation."""
    from .clients.exchanges import paper_profile_body

    action = args.action

    if action == "create-paper" and not args.execute:
        # dry run: no credentials needed, just print the planned body
        body = paper_profile_body(
            args.name,
            args.family,
            trade_mode=args.trade_mode,
            margin_mode=args.margin_mode,
        )
        print("# dry run (pass --execute to POST the upsert)")
        _print_json(body)
        return 0
    if action == "ensure" and not args.execute:
        spec = load_json_arg(args.spec)
        if not isinstance(spec, dict):
            raise SystemExit("--spec must be a JSON object mapping venue -> [names]")
        print("# dry run (pass --execute to create missing paper profiles)")
        _print_json({"spec": spec, "families": DEFAULT_VENUE_FAMILIES})
        return 0

    secrets = load_secrets()
    client = ExchangesClient(_grid_transport(secrets, args.transport == "browser"))

    if action == "profiles":
        _print_json([p.as_dict() for p in client.list_profiles()])
    elif action == "limits":
        _print_json(client.account_limits())
    elif action == "create-paper":
        _print_json(
            client.create_paper_profile(
                args.name,
                args.family,
                trade_mode=args.trade_mode,
                margin_mode=args.margin_mode,
            )
        )
    elif action == "ensure":
        spec = load_json_arg(args.spec)
        if not isinstance(spec, dict):
            raise SystemExit("--spec must be a JSON object mapping venue -> [names]")
        _print_json(client.ensure_paper_profiles(spec))
    else:
        raise SystemExit(f"unknown exchanges action {action!r}")
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


def cmd_discover(args: argparse.Namespace) -> int:
    """Discovery helpers — surface catalog, probe an unknown endpoint."""
    from .discovery import Probe, surface_index

    if args.action == "surfaces":
        _print_json(surface_index())
        return 0
    if args.action == "probe":
        secrets = load_secrets()
        # build a temporary wun facade (mirror WunderTrading.__init__)
        from .clients.client import WunderTrading

        wun = WunderTrading(browser=args.browser, secrets=secrets)
        try:
            probe = Probe(wun)
            results = probe.try_method(args.method, args.path)
            out = [
                {
                    "surface": r.surface,
                    "ok": r.ok,
                    "status": r.status,
                    "elapsed_ms": round(r.elapsed_ms, 2),
                    "error": r.error,
                }
                for r in results
            ]
            _print_json({"method": args.method, "path": args.path, "attempts": out})
            return 0 if any(r.ok for r in results) else 2
        finally:
            wun.close()
    raise SystemExit(f"unknown discover action {args.action!r}")


def cmd_debug(args: argparse.Namespace) -> int:
    """Debug helpers — show state, dump dir, list captured dumps."""
    from .debug import STATE, default_dump_dir

    if args.action == "status":
        _print_json(
            {
                "enabled": STATE.enabled,
                "log_level": logging.getLevelName(STATE.log_level),
                "dump_dir": str(STATE.dump_dir),
                "dump_on_failure": STATE.dump_on_failure,
                "requests_logged": STATE.requests_logged,
                "captured_dumps": len(STATE.dumps),
            }
        )
        return 0
    if args.action == "enable":
        STATE.install(
            level=getattr(logging, args.level.upper(), logging.INFO),
            dump_on_failure=not args.no_dump,
            dump_dir=Path(args.dump_dir) if args.dump_dir else None,
        )
        print(
            f"wtclient debug enabled (level={args.level}, "
            f"dump_dir={STATE.dump_dir}, dump_on_failure={STATE.dump_on_failure})",
            file=sys.stderr,
        )
        return 0
    if args.action == "list-dumps":
        d = default_dump_dir()
        if not d.exists():
            print(f"# no dump dir at {d}", file=sys.stderr)
            return 0
        files = sorted(d.glob("*.json"))
        for f in files[-args.limit:]:
            _print_json({"path": str(f), "size": f.stat().st_size})
        return 0
    raise SystemExit(f"unknown debug action {args.action!r}")


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

    p = sub.add_parser(
        "exchanges",
        help="my-exchanges: paper profiles + account limits (raw session or browser)",
    )
    p.add_argument(
        "action",
        choices=["profiles", "limits", "create-paper", "ensure"],
    )
    p.add_argument(
        "name",
        nargs="?",
        default=None,
        help="for `create-paper`: profile name",
    )
    p.add_argument("--family", default="BINANCE", help="exchangeFamily (BINANCE|HYPERLIQUID)")
    p.add_argument("--trade-mode", dest="trade_mode", default="hedge_mode")
    p.add_argument("--margin-mode", dest="margin_mode", default="cross")
    p.add_argument("--spec", default=None, help="for `ensure`: JSON {venue: [names]}")
    p.add_argument(
        "--execute",
        action="store_true",
        help="actually write (without it, print the planned body only)",
    )
    p.add_argument("--transport", choices=["raw", "browser"], default="raw")
    p.set_defaults(func=cmd_exchanges)

    p = sub.add_parser("curl", help="print a redacted curl equivalent without executing")
    p.add_argument("method")
    p.add_argument("path")
    p.add_argument("--data", dest="data")
    p.set_defaults(func=cmd_curl)

    p = sub.add_parser(
        "discover",
        help="discovery machinery — surface catalog, endpoint probe",
    )
    p.add_argument("action", choices=["surfaces", "probe"])
    p.add_argument("method", nargs="?", help="for `probe`: HTTP method")
    p.add_argument("path", nargs="?", help="for `probe`: path or full URL")
    p.add_argument(
        "--browser",
        action="store_true",
        help="for `probe`: include the headful browser transport",
    )
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser(
        "debug",
        help="debug machinery — enable logging, dump-on-failure, list dumps",
    )
    p.add_argument("action", choices=["enable", "status", "list-dumps"])
    p.add_argument("--level", default="INFO", help="log level (DEBUG/INFO/WARN/ERROR)")
    p.add_argument("--dump-dir", default=None, help="override WT_DEBUG_DUMP_DIR")
    p.add_argument("--no-dump", action="store_true", help="disable dump-on-failure")
    p.add_argument("--limit", type=int, default=10, help="for list-dumps")
    p.set_defaults(func=cmd_debug)

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
