#!/usr/bin/env python3
"""shot_console.py — one-off console screenshot for verification.

Opens the mission console (:8798) in a NEW tab on the existing CDP :9222
browser, waits for the app to render its data, captures a PNG, then closes
ONLY that tab. The daemon's own WT tab is never touched.

Usage: python3 scripts/shot_console.py [output.png]
"""
import base64
import json
import sys
import time
import urllib.parse
import urllib.request

import websocket

CDP_HTTP = "http://127.0.0.1:9222"
CONSOLE_URL = "http://127.0.0.1:8798/"
OUT = sys.argv[1] if len(sys.argv) > 1 else \
    "state/reports/audit-20260906-console-live.png"


def http_json(path, method="GET"):
    req = urllib.request.Request(CDP_HTTP + path, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def main():
    tab = http_json("/json/new?" + urllib.parse.quote(CONSOLE_URL, safe=""),
                    method="PUT")
    ws_url = tab["webSocketDebuggerUrl"]
    print("new tab:", tab["id"])
    ws = websocket.create_connection(ws_url, timeout=30)
    mid = 0

    def send(method, params=None):
        nonlocal mid
        mid += 1
        ws.send(json.dumps({"id": mid, "method": method,
                            "params": params or {}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))
                return msg.get("result", {})

    try:
        send("Page.enable")
        send("Runtime.enable")
        expr = ("!!document.getElementById('pnl-header') &&"
                " (document.getElementById('pnl-header').innerText||'').length > 10")
        for _ in range(40):
            time.sleep(0.5)
            res = send("Runtime.evaluate",
                       {"expression": expr, "returnByValue": True})
            if res.get("result", {}).get("value") is True:
                break
        time.sleep(1.5)  # let charts settle
        shot = send("Page.captureScreenshot", {"format": "png"})
        with open(OUT, "wb") as f:
            f.write(base64.b64decode(shot["data"]))
        print("screenshot written:", OUT)
    finally:
        ws.close()
        http_json("/json/close/" + tab["id"], method="PUT")
        print("tab closed")


if __name__ == "__main__":
    main()
