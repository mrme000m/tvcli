"""serve mode — expose one long-lived headful Chart over a localhost HTTP/JSON API.

Agents drive construct/visualize/validate (plus Pine, replay, panes, alerts,
streaming entry points) without relaunching a browser per call. Mirrors the
repo's tvcli `serve --daemon :8765` convention; tvvisual uses :8766.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from .creds import load_creds
from .session import Chart


class _Handler(BaseHTTPRequestHandler):
    chart: Chart | None = None

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            return {}

    def _q(self):
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    # ------------------------------------------------------------------ GET

    def do_GET(self):
        path = urlparse(self.path).path
        q = self._q()
        c = self.chart
        try:
            if path == "/health":
                st = c.state() or {}
                return self._json({"ok": True, "symbol": st.get("symbol")})
            if path == "/whoami":
                creds = load_creds()
                st = c.state() or {}
                return self._json({"user": creds.get("user"), "tier": creds.get("tier"),
                                   "symbol": st.get("symbol"), "resolution": st.get("resolution"),
                                   "state": st})
            if path == "/state":
                return self._json(c.state())
            if path == "/studies":
                return self._json(c.studies())
            if path == "/values":
                return self._json(c.study_values())
            if path == "/ohlcv":
                return self._json(c.ohlcv(int(q.get("count", 100)), q.get("summary") == "1"))
            if path == "/quote":
                return self._json(c.quote(q.get("symbol")))
            if path == "/depth":
                return self._json(c.depth())
            if path == "/strategy":
                return self._json(c.strategy_results())
            if path == "/trades":
                return self._json(c.trades(int(q.get("max", 20))))
            if path == "/equity":
                return self._json(c.equity())
            if path == "/shapes":
                return self._json(c.read_shapes())
            if path == "/drawings":
                return self._json(c.list_drawings())
            if path == "/lines":
                return self._json(c.lines(q.get("filter", "")))
            if path == "/labels":
                return self._json(c.labels(q.get("filter", "")))
            if path == "/boxes":
                return self._json(c.boxes(q.get("filter", "")))
            if path == "/tables":
                return self._json(c.tables(q.get("filter", "")))
            if path == "/panes":
                return self._json(c.pane_list())
            if path == "/tabs":
                return self._json(c.tab_list())
            if path == "/alerts":
                return self._json(c.alert_list())
            if path == "/layouts":
                return self._json(c.layout_list())
            if path == "/layout_export":
                return self._json(c.layout_export(q.get("path")))
            if path == "/watchlist":
                return self._json(c.watchlist())
            if path == "/hotkeys":
                return self._json(c.hotkeys(scrape=q.get("scrape") == "1", mac=q.get("mac") == "1"))
            if path == "/screenshot":
                return self._json(c.screenshot(q.get("path"), q.get("region", "chart")))
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    # ------------------------------------------------------------------ POST

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body()
        c = self.chart
        try:
            if path == "/open":
                c.open(body.get("symbol"), body.get("timeframe"))
                return self._json({"ok": True, "state": c.state()})
            if path == "/chart_type":
                return self._json(c.set_chart_type(body.get("type")))
            if path == "/visible_range":
                return self._json(c.set_visible_range(body.get("from"), body.get("to")))
            if path == "/scroll_to_date":
                return self._json(c.scroll_to_date(body.get("date")))
            if path == "/symbol_search":
                return self._json(c.symbol_search(body.get("query"), body.get("type", "")))
            if path == "/indicator":
                if body.get("action") == "remove":
                    return self._json(c.remove_indicator(body.get("entity_id")))
                return self._json(c.add_indicator(body.get("name"), body.get("inputs")))
            if path == "/indicator_search":
                return self._json(c.search_indicators(body.get("query"), body.get("limit", 25)))
            if path == "/indicator_toggle":
                return self._json(c.toggle_indicator(body.get("entity_id"), body.get("visible", True)))
            if path == "/draw":
                return self._json(c.draw(body))
            if path == "/draw_trend":
                return self._json(c.draw_trend_line(body.get("p1"), body.get("p2"), body.get("label"), body.get("color", "#2962ff")))
            if path == "/remove_drawing":
                return self._json(c.remove_drawing(body.get("entity_id")))
            if path == "/clear":
                return self._json(c.clear_drawings())
            if path == "/validate":
                return self._json(c.validate(body.get("checks", [])))
            if path == "/show":
                return self._json(c.show(body))
            if path == "/chrome":
                if body.get("action") == "show":
                    return self._json(c.show_chrome())
                return self._json(c.hide_chrome())
            if path == "/zoom":
                return self._json(c.zoom_bars(body.get("bars", 150)))
            if path == "/alert_create":
                return self._json(c.alert_create(body.get("condition", "crossing"), body.get("price"), body.get("message")))
            if path == "/alert_delete":
                return self._json(c.alert_delete(body.get("alert_id"), body.get("alert_ids"), body.get("delete_all", False)))
            if path == "/replay":
                action = body.get("action")
                if action == "start":
                    return self._json(c.replay_start(body.get("date")))
                if action == "step":
                    return self._json(c.replay_step())
                if action == "autoplay":
                    return self._json(c.replay_autoplay(body.get("speed")))
                if action == "stop":
                    return self._json(c.replay_stop())
                if action == "status":
                    return self._json(c.replay_status())
                if action == "trade":
                    return self._json(c.replay_trade(body.get("trade_action")))
                return self._json({"error": "unknown replay action"}, 400)
            if path == "/pane":
                action = body.get("action")
                if action == "layout":
                    return self._json(c.pane_set_layout(body.get("layout")))
                if action == "focus":
                    return self._json(c.pane_focus(body.get("index")))
                if action == "symbol":
                    return self._json(c.pane_set_symbol(body.get("index"), body.get("symbol")))
                return self._json({"error": "unknown pane action"}, 400)
            if path == "/pine":
                action = body.get("action")
                if action == "get":
                    return self._json(c.pine_get_source())
                if action == "set":
                    return self._json(c.pine_set_source(body.get("source", "")))
                if action == "compile":
                    return self._json(c.pine_compile())
                if action == "smart_compile":
                    return self._json(c.pine_smart_compile())
                if action == "errors":
                    return self._json(c.pine_get_errors())
                if action == "console":
                    return self._json(c.pine_get_console())
                if action == "save":
                    return self._json(c.pine_save())
                if action == "new":
                    return self._json(c.pine_new(body.get("type", "indicator")))
                if action == "open":
                    return self._json(c.pine_open_script(body.get("name")))
                if action == "list":
                    return self._json(c.pine_list_scripts())
                return self._json({"error": "unknown pine action"}, 400)
            if path == "/ui":
                action = body.get("action")
                if action == "key":
                    return self._json(c.key_press(body.get("key"), body.get("modifiers")))
                if action == "click":
                    return self._json(c.click(body.get("by", "text"), body.get("value")))
                if action == "panel":
                    return self._json(c.open_panel(body.get("panel"), body.get("panel_action", "toggle")))
                if action == "fullscreen":
                    return self._json(c.fullscreen())
                if action == "layout_switch":
                    return self._json(c.layout_switch(body.get("name")))
                if action == "eval":
                    return self._json({"result": c.eval(body.get("expression"), await_promise=body.get("await_promise", False))})
                return self._json({"error": "unknown ui action"}, 400)
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def log_message(self, *args):  # keep the agent's stderr clean
        pass


def serve(host="127.0.0.1", port=8766, env_path=None, profile_dir=None, headless=False):
    _Handler.chart = Chart(env_path=env_path, profile_dir=profile_dir, headless=headless)
    _Handler.chart._ensure_open()
    srv = HTTPServer((host, port), _Handler)
    print(f"tvvisual serving on http://{host}:{port} (Ctrl+C to stop)", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        _Handler.chart.close()
