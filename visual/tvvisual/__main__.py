"""tvvisual CLI — headful CloakBrowser channel for TradingView (CDP).

Every capability is available both as a Python method on `Chart` and as a
pipe-friendly JSON CLI command. Run `tvvisual <cmd> --help` for details.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from .session import Chart, install
from .tools import pine_analyze, pine_check
from . import scout as _scout


def _emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _chart(args):
    return Chart(env_path=getattr(args, "env", None),
                 profile_dir=getattr(args, "profile", None))


def _with_chart(args, fn):
    chart = _chart(args)
    try:
        _emit(fn(chart))
    finally:
        chart.close()


def _env(parser):
    parser.add_argument("--env", default=argparse.SUPPRESS, help=".env with SESSION/SIGNATURE/DEVICE_T")
    parser.add_argument("--profile", default=argparse.SUPPRESS, help="persistent profile dir")


# --------------------------------------------------------------------- install

def cmd_install(args):
    print(install())


# ----------------------------------------------------------------------- serve

def cmd_serve(args):
    from .server import serve
    serve(args.host, args.port, getattr(args, "env", None),
          getattr(args, "profile", None), args.headless)


# ------------------------------------------------------------------------ open

def cmd_open(args):
    chart = Chart(env_path=getattr(args, "env", None),
                  profile_dir=getattr(args, "profile", None), humanize=args.humanize)
    try:
        chart.open(args.symbol, args.tf)
        out = {"symbol": args.symbol, "timeframe": args.tf}
        if args.ind:
            out["indicators"] = [chart.add_indicator(name) for name in args.ind]
            time.sleep(2)
        levels = []
        for p in args.level or []:
            levels.append(chart.draw_level(p))
        for z in args.zone or []:
            hi, lo = (z.split(":") + [None])[:2]
            levels.append(chart.draw_zone(float(hi), float(lo)))
        if levels:
            out["drawings"] = levels
        if args.validate:
            out["validate"] = chart.validate(json.loads(args.validate))
        if args.state:
            out["state"] = chart.state()
        if args.shot:
            out["shot"] = chart.screenshot(args.shot, args.region)
        _emit(out)
        hold = args.hold
        if hold == -1:
            print("Chart open. Ctrl+C to close.", file=sys.stderr)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        elif hold > 0:
            time.sleep(hold)
    finally:
        chart.close()


# ------------------------------------------------------------------------ show

def cmd_show(args):
    cfg = {}
    if getattr(args, "config", None):
        with open(args.config) as f:
            cfg.update(json.load(f))
    if args.symbol:
        cfg["symbol"] = args.symbol
    if args.tf:
        cfg["timeframe"] = args.tf
    if args.width:
        cfg["width"] = args.width
    if args.height:
        cfg["height"] = args.height
    if args.bars:
        cfg["bars"] = args.bars
    if args.no_chrome:
        cfg["hide_chrome"] = True
    if args.shot:
        cfg["screenshot"] = args.shot
    if not cfg.get("symbol"):
        print("error: symbol required (--config or positional)", file=sys.stderr)
        sys.exit(2)
    w, h = cfg.get("width"), cfg.get("height")
    viewport = {"width": w, "height": h} if (w and h) else None
    chart = Chart(env_path=getattr(args, "env", None),
                  profile_dir=getattr(args, "profile", None), viewport=viewport)
    try:
        _emit(chart.show(cfg))
        hold = cfg.get("hold", 0) or args.hold
        if hold == -1:
            print("Chart open. Ctrl+C to close.", file=sys.stderr)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        elif hold > 0:
            time.sleep(hold)
    finally:
        chart.close()


# ------------------------------------------------------------- simple handlers

def _quote(args):
    _with_chart(args, lambda c: c.quote(getattr(args, "symbol", None)))

def _hotkeys(args):
    _with_chart(args, lambda c: c.hotkeys(scrape=args.scrape, mac=args.mac))


def _data_ohlcv(args):
    _with_chart(args, lambda c: c.ohlcv(args.count, args.summary))

def _data_values(args):
    _with_chart(args, lambda c: c.study_values())

def _data_strategy(args):
    _with_chart(args, lambda c: c.strategy_results())

def _data_trades(args):
    _with_chart(args, lambda c: c.trades(args.max))

def _data_equity(args):
    _with_chart(args, lambda c: c.equity())

def _data_depth(args):
    _with_chart(args, lambda c: c.depth())


def _draw_level(args):
    _with_chart(args, lambda c: c.draw_level(args.price, args.label, args.color))

def _draw_zone(args):
    _with_chart(args, lambda c: c.draw_zone(args.high, args.low, args.label, args.color))

def _draw_trend(args):
    _with_chart(args, lambda c: c.draw_trend_line((args.t1, args.p1), (args.t2, args.p2), args.label, args.color))

def _draw_label(args):
    _with_chart(args, lambda c: c.draw_label(args.text, args.price))

def _draw_list(args):
    _with_chart(args, lambda c: c.list_drawings())

def _draw_get(args):
    _with_chart(args, lambda c: c.get_drawing(args.entity_id))

def _draw_remove(args):
    _with_chart(args, lambda c: c.remove_drawing(args.entity_id))

def _draw_clear(args):
    _with_chart(args, lambda c: c.clear_drawings())


def _indicator_add(args):
    inputs = None
    if args.inputs:
        inputs = json.loads(args.inputs)
    _with_chart(args, lambda c: c.add_indicator(args.name, inputs))

def _indicator_remove(args):
    _with_chart(args, lambda c: c.remove_indicator(args.entity_id))

def _indicator_search(args):
    _with_chart(args, lambda c: c.search_indicators(args.query, args.limit))

def _indicator_toggle(args):
    _with_chart(args, lambda c: c.toggle_indicator(args.entity_id, not args.off))


def _pine_get(args):
    _with_chart(args, lambda c: c.pine_get_source())

def _pine_set(args):
    src = args.source
    if args.file:
        src = open(args.file).read()
    _with_chart(args, lambda c: c.pine_set_source(src))

def _pine_compile(args):
    _with_chart(args, lambda c: c.pine_compile())

def _pine_smart(args):
    _with_chart(args, lambda c: c.pine_smart_compile())

def _pine_errors(args):
    _with_chart(args, lambda c: c.pine_get_errors())

def _pine_console(args):
    _with_chart(args, lambda c: c.pine_get_console())

def _pine_save(args):
    _with_chart(args, lambda c: c.pine_save())

def _pine_new(args):
    _with_chart(args, lambda c: c.pine_new(args.type))

def _pine_open(args):
    _with_chart(args, lambda c: c.pine_open_script(args.name))

def _pine_list(args):
    _with_chart(args, lambda c: c.pine_list_scripts())

def _scout_surface(args):
    _with_chart(args, lambda c: _scout.scout_surface(c, kb=getattr(args, "kb", None)))

def _scout_dom(args):
    _with_chart(args, lambda c: _scout.scout_dom(c, kb=getattr(args, "kb", None)))

def _scout_probe(args):
    js = args.js
    if args.js_file:
        js = open(args.js_file).read()
    _with_chart(args, lambda c: _scout.scout_probe(
        c, args.name, js, kb=getattr(args, "kb", None),
        await_promise=args.await_promise))

def _scout_recipes(args):
    _emit(_scout.list_recipes(kb=getattr(args, "kb", None)))

def _scout_verify(args):
    _with_chart(args, lambda c: _scout.verify_recipes(
        c, kb=getattr(args, "kb", None), only=args.name))

def _scout_save(args):
    recipe = json.loads(open(args.file).read())
    _emit(_scout.save_recipe(recipe, kb=getattr(args, "kb", None)))


def _recipe_list(args):
    _emit(_scout.list_recipes(kb=getattr(args, "kb", None)))

def _recipe_run(args):
    params = json.loads(args.param) if args.param else None
    kb = getattr(args, "kb", None)
    recipe = _scout.load_recipe(args.name, kb=kb)
    chart = _chart(args)
    try:
        _emit(_scout.run_recipe(chart, recipe, params=params, kb=kb))
        if args.hold > 0:
            time.sleep(args.hold)
    finally:
        chart.close()

def _recipe_codegen(args):
    _emit(_scout.codegen_recipe(args.name, kb=getattr(args, "kb", None),
                                 out_dir=args.out))


def _confluence(args):
    from .confluence import run_confluence, skill_inputs
    if getattr(args, "list_inputs", False):
        _emit({"skill": args.skill, "inputs": skill_inputs(args.skill, args.tvcli)})
        return
    overrides = {}
    for kv in (getattr(args, "inputs", []) or []):
        k, _, v = kv.partition("=")
        k = k.strip()
        if k:
            overrides[k] = v
    chart = _chart(args)
    try:
        _emit(run_confluence(chart, skill=args.skill, symbol=args.symbol,
                             timeframe=args.tf, tvcli=args.tvcli,
                             kb=getattr(args, "kb", None),
                             input_overrides=overrides))
    finally:
        chart.close()


def _pine_analyze(args):
    _emit(pine_analyze(args.source))

def _pine_check(args):
    _emit(pine_check(args.source))


def _replay_start(args):
    _with_chart(args, lambda c: c.replay_start(args.date))

def _replay_step(args):
    _with_chart(args, lambda c: c.replay_step())

def _replay_autoplay(args):
    _with_chart(args, lambda c: c.replay_autoplay(args.speed))

def _replay_stop(args):
    _with_chart(args, lambda c: c.replay_stop())

def _replay_status(args):
    _with_chart(args, lambda c: c.replay_status())

def _replay_trade(args):
    _with_chart(args, lambda c: c.replay_trade(args.action))


def _pane_list(args):
    _with_chart(args, lambda c: c.pane_list())

def _pane_layout(args):
    _with_chart(args, lambda c: c.pane_set_layout(args.layout))

def _pane_focus(args):
    _with_chart(args, lambda c: c.pane_focus(args.index))

def _pane_symbol(args):
    _with_chart(args, lambda c: c.pane_set_symbol(args.index, args.symbol))


def _alert_list(args):
    _with_chart(args, lambda c: c.alert_list())

def _alert_create(args):
    _with_chart(args, lambda c: c.alert_create(args.condition, args.price, args.message))

def _alert_delete(args):
    _with_chart(args, lambda c: c.alert_delete(alert_id=args.alert_id, delete_all=args.all))


def _watchlist_get(args):
    _with_chart(args, lambda c: c.watchlist())

def _watchlist_add(args):
    _with_chart(args, lambda c: c.watchlist_add(args.symbol))


def _tab_list(args):
    _with_chart(args, lambda c: c.tab_list())

def _tab_new(args):
    _with_chart(args, lambda c: c.tab_new(args.url))

def _tab_switch(args):
    _with_chart(args, lambda c: c.tab_switch(args.index))

def _tab_close(args):
    _with_chart(args, lambda c: c.tab_close(args.index))


def _layout_list(args):
    _with_chart(args, lambda c: c.layout_list())

def _layout_switch(args):
    _with_chart(args, lambda c: c.layout_switch(args.name))

def _layout_export(args):
    _with_chart(args, lambda c: c.layout_export(args.out))


def _layout_restore(args):
    _with_chart(args, lambda c: c.chart_restore(args.path))


def _closed_bar(args):
    _with_chart(args, lambda c: c.closed_bar_values(getattr(args, "study", None)))


def _drawings_snapshot(args):
    _with_chart(args, lambda c: c.drawings_snapshot(getattr(args, "out", None)))


def _drawings_restore(args):
    _with_chart(args, lambda c: c.drawings_restore(args.path))


def _ui_click(args):
    _with_chart(args, lambda c: c.click(args.by, args.value))

def _ui_keyboard(args):
    mods = []
    if args.ctrl: mods.append("ctrl")
    if args.alt: mods.append("alt")
    if args.shift: mods.append("shift")
    if args.meta: mods.append("meta")
    _with_chart(args, lambda c: c.key_press(args.key, mods or None))

def _ui_hover(args):
    _with_chart(args, lambda c: c.hover(args.by, args.value))

def _ui_scroll(args):
    _with_chart(args, lambda c: c.scroll(args.direction, args.amount))

def _ui_find(args):
    _with_chart(args, lambda c: c.find_elements(args.query, args.strategy))

def _ui_eval(args):
    _with_chart(args, lambda c: c.eval(args.expression, await_promise=args.await_promise))

def _ui_type(args):
    _with_chart(args, lambda c: c.type_text(args.text))

def _ui_panel(args):
    _with_chart(args, lambda c: c.open_panel(args.panel, args.action))

def _ui_fullscreen(args):
    _with_chart(args, lambda c: c.fullscreen())

def _ui_mouse(args):
    _with_chart(args, lambda c: c.mouse_click(args.x, args.y, "right" if args.right else "left", args.double))


def _stream(args):
    chart = _chart(args)
    try:
        if args.kind == "quote":
            chart.stream_quote(args.interval)
        elif args.kind == "bars":
            chart.stream_bars(args.interval)
        elif args.kind == "values":
            chart.stream_values(args.interval)
        elif args.kind == "lines":
            chart.stream_lines(args.interval, args.filter or "")
        elif args.kind == "labels":
            chart.stream_labels(args.interval, args.filter or "")
        elif args.kind == "tables":
            chart.stream_tables(args.interval, args.filter or "")
        elif args.kind == "all":
            chart.stream_all_panes(args.interval)
    finally:
        chart.close()


# ------------------------------------------------------------------------ main

def main(argv=None):
    p = argparse.ArgumentParser(prog="tvvisual",
                                description="Headful CloakBrowser channel for TradingView (CDP)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(parser):
        _env(parser)
        return parser

    sp = common(sub.add_parser("install", help="download the stealth Chromium"))
    sp.set_defaults(func=cmd_install)

    sp = common(sub.add_parser("serve", help="run the long-lived HTTP/JSON API"))
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8766)
    sp.add_argument("--headless", action="store_true")
    sp.set_defaults(func=cmd_serve)

    sp = common(sub.add_parser("open", help="construct + visualize a chart"))
    sp.add_argument("symbol")
    sp.add_argument("--tf", default="15")
    sp.add_argument("--ind", action="append", help="indicator name (repeatable)")
    sp.add_argument("--level", action="append", type=float)
    sp.add_argument("--zone", action="append", help="high:low")
    sp.add_argument("--validate", help="JSON checks")
    sp.add_argument("--state", action="store_true")
    sp.add_argument("--shot", help="screenshot path")
    sp.add_argument("--region", default="chart", choices=["full", "chart"])
    sp.add_argument("--hold", type=int, default=0)
    sp.add_argument("--humanize", action="store_true")
    sp.set_defaults(func=cmd_open)

    sp = common(sub.add_parser("show", help="construct a clean, chrome-free chart view"))
    sp.add_argument("symbol", nargs="?")
    sp.add_argument("--config")
    sp.add_argument("--tf")
    sp.add_argument("--width", type=int)
    sp.add_argument("--height", type=int)
    sp.add_argument("--bars", type=int)
    sp.add_argument("--no-chrome", action="store_true")
    sp.add_argument("--shot")
    sp.add_argument("--hold", type=int, default=0)
    sp.set_defaults(func=cmd_show)

    sp = common(sub.add_parser("hotkeys", help="chart drawing + hotkey reference"))
    sp.add_argument("--scrape", action="store_true", help="also read TradingView's own shortcuts sheet")
    sp.add_argument("--mac", action="store_true", help="swap Ctrl for Cmd")
    sp.set_defaults(func=_hotkeys)

    sp = common(sub.add_parser("quote", help="current price snapshot"))
    sp.add_argument("symbol", nargs="?")
    sp.set_defaults(func=_quote)

    # data
    data = sub.add_parser("data", help="visual analysis reads")
    dsub = data.add_subparsers(dest="dcmd", required=True)
    d = common(dsub.add_parser("ohlcv")); d.add_argument("--count", type=int, default=100); d.add_argument("--summary", action="store_true"); d.set_defaults(func=_data_ohlcv)
    d = common(dsub.add_parser("values")); d.set_defaults(func=_data_values)
    d = common(dsub.add_parser("strategy")); d.set_defaults(func=_data_strategy)
    d = common(dsub.add_parser("trades")); d.add_argument("--max", type=int, default=20); d.set_defaults(func=_data_trades)
    d = common(dsub.add_parser("equity")); d.set_defaults(func=_data_equity)
    d = common(dsub.add_parser("depth")); d.set_defaults(func=_data_depth)

    # draw
    draw = sub.add_parser("draw", help="draw shapes on the chart")
    dr = draw.add_subparsers(dest="dcmd", required=True)
    d = common(dr.add_parser("level")); d.add_argument("price", type=float); d.add_argument("--label"); d.add_argument("--color", default="#ff9800"); d.set_defaults(func=_draw_level)
    d = common(dr.add_parser("zone")); d.add_argument("high", type=float); d.add_argument("low", type=float); d.add_argument("--label"); d.add_argument("--color", default="#2962ff"); d.set_defaults(func=_draw_zone)
    d = common(dr.add_parser("trend")); d.add_argument("t1", type=int); d.add_argument("p1", type=float); d.add_argument("t2", type=int); d.add_argument("p2", type=float); d.add_argument("--label"); d.add_argument("--color", default="#2962ff"); d.set_defaults(func=_draw_trend)
    d = common(dr.add_parser("label")); d.add_argument("text"); d.add_argument("price", type=float); d.set_defaults(func=_draw_label)
    d = common(dr.add_parser("list")); d.set_defaults(func=_draw_list)
    d = common(dr.add_parser("get")); d.add_argument("entity_id"); d.set_defaults(func=_draw_get)
    d = common(dr.add_parser("remove")); d.add_argument("entity_id"); d.set_defaults(func=_draw_remove)
    d = common(dr.add_parser("clear")); d.set_defaults(func=_draw_clear)

    # indicator
    ind = sub.add_parser("indicator", help="add/remove/search indicators")
    isub = ind.add_subparsers(dest="dcmd", required=True)
    d = common(isub.add_parser("add")); d.add_argument("name"); d.add_argument("--inputs", help="JSON of input overrides"); d.set_defaults(func=_indicator_add)
    d = common(isub.add_parser("remove")); d.add_argument("entity_id"); d.set_defaults(func=_indicator_remove)
    d = common(isub.add_parser("search")); d.add_argument("query"); d.add_argument("--limit", type=int, default=25); d.set_defaults(func=_indicator_search)
    d = common(isub.add_parser("toggle")); d.add_argument("entity_id"); d.add_argument("--off", action="store_true"); d.set_defaults(func=_indicator_toggle)

    # pine
    pine = sub.add_parser("pine", help="Pine Script development")
    ps = pine.add_subparsers(dest="dcmd", required=True)
    d = common(ps.add_parser("get")); d.set_defaults(func=_pine_get)
    d = common(ps.add_parser("set")); d.add_argument("source", nargs="?"); d.add_argument("--file"); d.set_defaults(func=_pine_set)
    d = common(ps.add_parser("compile")); d.set_defaults(func=_pine_compile)
    d = common(ps.add_parser("smart-compile")); d.set_defaults(func=_pine_smart)
    d = common(ps.add_parser("errors")); d.set_defaults(func=_pine_errors)
    d = common(ps.add_parser("console")); d.set_defaults(func=_pine_console)
    d = common(ps.add_parser("save")); d.set_defaults(func=_pine_save)
    d = common(ps.add_parser("new")); d.add_argument("--type", default="indicator", choices=["indicator", "strategy", "library"]); d.set_defaults(func=_pine_new)
    d = common(ps.add_parser("open")); d.add_argument("name"); d.set_defaults(func=_pine_open)
    d = common(ps.add_parser("list")); d.set_defaults(func=_pine_list)
    d = common(ps.add_parser("analyze")); d.add_argument("source"); d.set_defaults(func=_pine_analyze)
    d = common(ps.add_parser("check")); d.add_argument("source"); d.set_defaults(func=_pine_check)

    # replay
    rep = sub.add_parser("replay", help="bar-by-bar practice mode")
    rs = rep.add_subparsers(dest="dcmd", required=True)
    d = common(rs.add_parser("start")); d.add_argument("date", nargs="?"); d.set_defaults(func=_replay_start)
    d = common(rs.add_parser("step")); d.set_defaults(func=_replay_step)
    d = common(rs.add_parser("autoplay")); d.add_argument("--speed", type=int); d.set_defaults(func=_replay_autoplay)
    d = common(rs.add_parser("stop")); d.set_defaults(func=_replay_stop)
    d = common(rs.add_parser("status")); d.set_defaults(func=_replay_status)
    d = common(rs.add_parser("trade")); d.add_argument("action", choices=["buy", "sell", "close"]); d.set_defaults(func=_replay_trade)

    # pane
    pan = sub.add_parser("pane", help="multi-pane layouts")
    ps2 = pan.add_subparsers(dest="dcmd", required=True)
    d = common(ps2.add_parser("list")); d.set_defaults(func=_pane_list)
    d = common(ps2.add_parser("layout")); d.add_argument("layout"); d.set_defaults(func=_pane_layout)
    d = common(ps2.add_parser("focus")); d.add_argument("index", type=int); d.set_defaults(func=_pane_focus)
    d = common(ps2.add_parser("symbol")); d.add_argument("index", type=int); d.add_argument("symbol"); d.set_defaults(func=_pane_symbol)

    # alert
    al = sub.add_parser("alert", help="price alerts")
    asub = al.add_subparsers(dest="dcmd", required=True)
    d = common(asub.add_parser("list")); d.set_defaults(func=_alert_list)
    d = common(asub.add_parser("create")); d.add_argument("--price", type=float, required=True); d.add_argument("--condition", default="crossing"); d.add_argument("--message"); d.set_defaults(func=_alert_create)
    d = common(asub.add_parser("delete")); d.add_argument("--alert-id"); d.add_argument("--all", action="store_true"); d.set_defaults(func=_alert_delete)

    # watchlist
    wl = sub.add_parser("watchlist", help="watchlist")
    wsub = wl.add_subparsers(dest="dcmd", required=True)
    d = common(wsub.add_parser("get")); d.set_defaults(func=_watchlist_get)
    d = common(wsub.add_parser("add")); d.add_argument("symbol"); d.set_defaults(func=_watchlist_add)

    # tab
    tb = sub.add_parser("tab", help="browser-tab management")
    tsub = tb.add_subparsers(dest="dcmd", required=True)
    d = common(tsub.add_parser("list")); d.set_defaults(func=_tab_list)
    d = common(tsub.add_parser("new")); d.add_argument("url", nargs="?"); d.set_defaults(func=_tab_new)
    d = common(tsub.add_parser("switch")); d.add_argument("index", type=int); d.set_defaults(func=_tab_switch)
    d = common(tsub.add_parser("close")); d.add_argument("index", type=int); d.set_defaults(func=_tab_close)

    # layout
    ly = sub.add_parser("layout", help="saved layouts")
    lsub = ly.add_subparsers(dest="dcmd", required=True)
    d = common(lsub.add_parser("list")); d.set_defaults(func=_layout_list)
    d = common(lsub.add_parser("switch")); d.add_argument("name"); d.set_defaults(func=_layout_switch)
    d = common(lsub.add_parser("export")); d.add_argument("--out"); d.set_defaults(func=_layout_export)
    d = common(lsub.add_parser("restore")); d.add_argument("path", help="layout_export() file to reload (full chart replace)"); d.set_defaults(func=_layout_restore)

    # closed-bar
    cb = common(sub.add_parser("closed-bar", help="exact last-closed-bar study plot values"))
    cb.add_argument("--study", help="substring match on study name")
    cb.set_defaults(func=_closed_bar)

    # drawings (restorable line-tool DTO)
    dws = sub.add_parser("drawings", help="capture/restore all line tools")
    dwsub = dws.add_subparsers(dest="dcmd", required=True)
    d = common(dwsub.add_parser("snapshot")); d.add_argument("--out"); d.set_defaults(func=_drawings_snapshot)
    d = common(dwsub.add_parser("restore")); d.add_argument("path"); d.set_defaults(func=_drawings_restore)

    # ui
    ui = sub.add_parser("ui", help="UI automation (click, keys, hover, ...)")
    usub = ui.add_subparsers(dest="dcmd", required=True)
    d = common(usub.add_parser("click")); d.add_argument("--by", default="text"); d.add_argument("value"); d.set_defaults(func=_ui_click)
    d = common(usub.add_parser("keyboard")); d.add_argument("key"); d.add_argument("--ctrl", action="store_true"); d.add_argument("--alt", action="store_true"); d.add_argument("--shift", action="store_true"); d.add_argument("--meta", action="store_true"); d.set_defaults(func=_ui_keyboard)
    d = common(usub.add_parser("hover")); d.add_argument("--by", default="text"); d.add_argument("value"); d.set_defaults(func=_ui_hover)
    d = common(usub.add_parser("scroll")); d.add_argument("direction", choices=["up", "down", "left", "right"]); d.add_argument("--amount", type=int, default=300); d.set_defaults(func=_ui_scroll)
    d = common(usub.add_parser("find")); d.add_argument("query"); d.add_argument("--strategy", default="text", choices=["text", "aria-label", "css"]); d.set_defaults(func=_ui_find)
    d = common(usub.add_parser("eval")); d.add_argument("expression"); d.add_argument("--await", dest="await_promise", action="store_true"); d.set_defaults(func=_ui_eval)
    d = common(usub.add_parser("type")); d.add_argument("text"); d.set_defaults(func=_ui_type)
    d = common(usub.add_parser("panel")); d.add_argument("panel", choices=["pine-editor", "strategy-tester", "watchlist", "alerts", "trading"]); d.add_argument("action", nargs="?", default="toggle", choices=["open", "close", "toggle"]); d.set_defaults(func=_ui_panel)
    d = common(usub.add_parser("fullscreen")); d.set_defaults(func=_ui_fullscreen)
    d = common(usub.add_parser("mouse")); d.add_argument("x", type=int); d.add_argument("y", type=int); d.add_argument("--right", action="store_true"); d.add_argument("--double", action="store_true"); d.set_defaults(func=_ui_mouse)

    # confluence
    cf = common(sub.add_parser("confluence", help="tvcli compute -> chart represent -> confirm report"))
    cf.add_argument("skill", nargs="?", default="xau-scalp")
    cf.add_argument("--symbol", default="OANDA:XAUUSD")
    cf.add_argument("--tf", default="5")
    from .confluence import TVCLI_DEFAULT
    cf.add_argument("--tvcli", default=TVCLI_DEFAULT)
    cf.add_argument("--kb")
    cf.add_argument("--input", action="append", dest="inputs", default=[],
                    metavar="KEY=VALUE",
                    help="override an indicator input (repeatable); KEY = JS var name or in_N")
    cf.add_argument("--list-inputs", action="store_true",
                    help="list the skill's inputs (name/type/default) and exit")
    cf.set_defaults(func=_confluence)

    # scout
    sc = sub.add_parser("scout", help="progressive UI instrumentation + knowledge base")
    scs = sc.add_subparsers(dest="dcmd", required=True)
    d = common(scs.add_parser("surface")); d.add_argument("--kb"); d.set_defaults(func=_scout_surface)
    d = common(scs.add_parser("dom")); d.add_argument("--kb"); d.set_defaults(func=_scout_dom)
    d = common(scs.add_parser("probe")); d.add_argument("name"); d.add_argument("--js"); d.add_argument("--js-file"); d.add_argument("--await", dest="await_promise", action="store_true"); d.add_argument("--kb"); d.set_defaults(func=_scout_probe)
    d = scs.add_parser("recipes"); d.add_argument("--kb"); d.set_defaults(func=_scout_recipes)
    d = common(scs.add_parser("verify")); d.add_argument("name", nargs="?"); d.add_argument("--kb"); d.set_defaults(func=_scout_verify)
    d = scs.add_parser("save"); d.add_argument("file"); d.add_argument("--kb"); d.set_defaults(func=_scout_save)

    # recipe
    rc = sub.add_parser("recipe", help="run / generate verified repeatable chart recipes")
    rcs = rc.add_subparsers(dest="dcmd", required=True)
    d = rcs.add_parser("list"); d.add_argument("--kb"); d.set_defaults(func=_recipe_list)
    d = common(rcs.add_parser("run")); d.add_argument("name"); d.add_argument("--param", help="JSON param overrides"); d.add_argument("--kb"); d.add_argument("--hold", type=int, default=0); d.set_defaults(func=_recipe_run)
    d = rcs.add_parser("codegen"); d.add_argument("name"); d.add_argument("--kb"); d.add_argument("--out"); d.set_defaults(func=_recipe_codegen)

    # stream
    st = sub.add_parser("stream", help="JSONL monitoring")
    stsub = st.add_subparsers(dest="kind", required=True)
    d = common(stsub.add_parser("quote")); d.add_argument("--interval", type=float, default=0.3); d.set_defaults(func=_stream)
    d = common(stsub.add_parser("bars")); d.add_argument("--interval", type=float, default=0.5); d.set_defaults(func=_stream)
    d = common(stsub.add_parser("values")); d.add_argument("--interval", type=float, default=0.5); d.set_defaults(func=_stream)
    d = common(stsub.add_parser("lines")); d.add_argument("--filter"); d.add_argument("--interval", type=float, default=1.0); d.set_defaults(func=_stream)
    d = common(stsub.add_parser("labels")); d.add_argument("--filter"); d.add_argument("--interval", type=float, default=1.0); d.set_defaults(func=_stream)
    d = common(stsub.add_parser("tables")); d.add_argument("--filter"); d.add_argument("--interval", type=float, default=2.0); d.set_defaults(func=_stream)
    d = common(stsub.add_parser("all")); d.add_argument("--interval", type=float, default=0.5); d.set_defaults(func=_stream)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
