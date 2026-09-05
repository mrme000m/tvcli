#!/usr/bin/env python3
"""ctl_http — the grid-autonomy HTTP control plane (extracted from daemon.py).

Endpoints (127.0.0.1:<port>, default 8799):
    GET  /health       liveness + KILL-file presence
    GET  /status       slots, active bots, capabilities, journal tail
    GET  /reliability  current reliability ledger
    GET  /observe      latest observation snapshot
    POST /rescreen     queue an immediate rescreen cycle
    POST /reliability  queue an immediate reliability-ledger refresh
    POST /rotate       force-rotate a slot (body {"slot": n})
    POST /kill         write the KILL file (daemon halts on next tick)

No daemon import — the served daemon instance is injected via
`serve_ctl(daemon, port)`, so this module stays circular-import free.
"""
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


def _utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ctl(BaseHTTPRequestHandler):
    daemon = None

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        st = self.daemon.state
        if self.path == "/health":
            self._json(200, {"status": "ok", "at": _utcnow(),
                             "kill": os.path.exists(os.path.join(HERE, "KILL"))})
        elif self.path == "/status":
            self._json(200, {"slots": st["slots"], "active_bots": st["active_bots"],
                             "committed": st["committed"],
                             "live_allow": st["live_allow"],
                             "profiles": st.get("profiles", []),
                             "capacity": st.get("capacity", {}),
                             "account_limits": st.get("account_limits", {}),
                             "capabilities": getattr(
                                 self.daemon, "capabilities", {}),
                             # dependency readiness (presence booleans only)
                             "env": getattr(self.daemon, "env_status",
                                            lambda: {})(),
                             "last_cycle": st.get("last_cycle"),
                             "journal_tail": st["journal"][-10:]})
        elif self.path == "/reliability":
            self._json(200, {"reliability": st["reliability"]})
        elif self.path == "/observe":
            self._json(200, {"observe": st.get("last_observe", {})})
        else:
            self._json(404, {"error": "unknown path"})

    def do_POST(self):
        if self.path == "/kill":
            open(os.path.join(HERE, "KILL"), "w").write(_utcnow())
            self._json(200, {"killed": True})
        elif self.path == "/rescreen":
            self.daemon.queue_rescreen()
            self._json(200, {"queued": True})
        elif self.path == "/reliability":
            self.daemon.queue_reliability()
            self._json(200, {"queued": True})
        elif self.path == "/rotate":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            slot = body.get("slot")
            if slot is None:
                self._json(400, {"error": "missing slot"})
                return
            sk = str(slot)
            bot = self.daemon.state["active_bots"].get(sk)
            if bot is None:
                self._json(404, {"error": f"no active bot in slot {slot}"})
                return
            bot["force_rotate"] = True
            self.daemon.queue_rescreen()  # rotation is evaluated on rescreen
            self._json(200, {"queued": True, "slot": slot,
                             "symbol": bot.get("symbol")})
        else:
            self._json(404, {"error": "unknown path"})

    def log_message(self, *a):
        pass


def serve_ctl(daemon, port):
    Ctl.daemon = daemon
    try:
        HTTPServer(("127.0.0.1", port), Ctl).serve_forever()
    except OSError as exc:
        # e.g. EADDRINUSE when a stray `daemon.py --once` holds the port —
        # this thread used to die silently, leaving the daemon trading with
        # no control plane. Surface it loudly (stdout + state journal).
        msg = f"ctl plane failed to bind 127.0.0.1:{port}: {exc}"
        print(msg, flush=True)
        try:
            daemon.state.setdefault("journal", []).append(
                {"kind": "ctl-error", "msg": msg[:200], "at": _utcnow()})
        except Exception:
            pass
