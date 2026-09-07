#!/usr/bin/env python3
"""ctl_http — the grid-autonomy HTTP control plane (extracted from daemon.py).

Endpoints (127.0.0.1:<port>, default 8799):
    GET  /health       liveness + KILL-file presence
    GET  /status       slots, active bots, capabilities, journal tail
    GET  /reliability  current reliability ledger
    GET  /observe      latest observation snapshot
    GET  /optimizer    fast-loop status: trackers, last cycle report
    POST /rescreen     queue an immediate rescreen cycle
    POST /reliability  queue an immediate reliability-ledger refresh
    POST /optimize     queue an immediate optimizer cycle
    POST /rotate       force-rotate a slot (body {"slot": n})
    POST /kill         write the KILL file (daemon halts on next tick)

No daemon import — the served daemon instance is injected via
`serve_ctl(daemon, port)`, so this module stays circular-import free.
"""
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


def _utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ctl-plane cache for the data-source observability block (5s — /status is
# polled by the console this often; the tails below are cheap but must
# never hammer the market-regime ring or disk on every call)
_DS_TTL = 5.0
_DS_CACHE = {"at": 0.0, "payload": None}


def _data_sources_payload(state=None):
    """Observability for the parallel data feeds, fail-soft on BOTH ends:

      fetch_events  market_regime.fetch_events_tail(50) — the candle-hop
                    ring (direct/vision/tvcli), IF the module + function
                    exist (parallel worker's market_regime.py)
      hunt_stats    the tvcli /hunt confluence counters. PRIMARY source is
                    the daemon journal's newest kind=="screen" entry (the
                    screen runs merge.py in a SUBPROCESS, so
                    merge.last_hunt_stats() in this process only ever sees
                    the in-process zero shape — kept as fallback for
                    in-process callers).

    Missing module/function/permission → the documented empty shape, so
    the console can render "not reported yet" instead of erroring."""
    import time as _time
    now = _time.time()
    if _DS_CACHE["payload"] is not None and now - _DS_CACHE["at"] < _DS_TTL:
        return _DS_CACHE["payload"]
    payload = {"fetch_events": [], "hunt_stats": {}}
    # primary for BOTH fields: the persisted snapshot of the last rescreen
    # SUBPROCESS (state.screen_data_sources) — that child fetches the bulk
    # of the candles (4h confirms + harvest EV) and runs the /hunt pass,
    # but its in-memory rings die with it, so the daemon journals the tail
    # per cycle. In-process rings below are the fallback (position
    # optimizer / stagnation fetches happen in the daemon process itself).
    sds = (state or {}).get("screen_data_sources")
    if isinstance(sds, dict):
        if isinstance(sds.get("fetch_events"), list) and sds["fetch_events"]:
            payload["fetch_events"] = sds["fetch_events"][-50:]
        hs = sds.get("hunt_stats")
        if isinstance(hs, dict) and hs.get("skills"):
            payload["hunt_stats"] = hs
    if not payload["fetch_events"]:
        try:
            import market_regime as _mr
            _fn = getattr(_mr, "fetch_events_tail", None)
            if callable(_fn):
                payload["fetch_events"] = _fn(50) or []
        except Exception:
            pass
    # primary: the newest screen journal entry carries the subprocess's
    # hunt_stats (per-skill hunted/ok, candidates boosted, errors)
    try:
        for ev in reversed((state or {}).get("journal") or []):
            if isinstance(ev, dict) and ev.get("kind") == "screen":
                hs = ev.get("hunt_stats")
                if isinstance(hs, dict) and hs.get("skills"):
                    payload["hunt_stats"] = hs
                break
    except Exception:
        pass
    if not payload["hunt_stats"].get("skills"):
        try:
            # merge.py lives in screen/ (daemon.py sys.paths it; a standalone
            # ctl import needs the path added here, fail-soft on duplicates)
            import merge as _merge
            _fn = getattr(_merge, "last_hunt_stats", None)
            if callable(_fn):
                payload["hunt_stats"] = _fn() or {}
        except ImportError:
            try:
                sys.path.insert(0, os.path.join(HERE, "screen"))
                import merge as _merge
                _fn = getattr(_merge, "last_hunt_stats", None)
                if callable(_fn):
                    payload["hunt_stats"] = _fn() or {}
            except Exception:
                pass
        except Exception:
            pass
    _DS_CACHE["at"], _DS_CACHE["payload"] = now, payload
    return payload


def _cache_age(st):
    """Age of the optimizer's candidate board in seconds (None = no cache)."""
    import time as _time
    at = (st.get("screen_cache") or {}).get("at")
    if not at:
        return None
    try:
        return round(_time.time() - float(at), 0)
    except (TypeError, ValueError):
        return None


def status_payload(daemon):
    """Assemble the GET /status body from daemon state.

    PnL and demo-cap observability blocks are computed here (fail-soft —
    observability must never break /status):
      pnl       {realized, unrealized, net, committed_usd, idle_usd} from
                the daemon's latest observe fold (daemon.pnl_snapshot)
      demo_cap  {cap, active, headroom} — the learned WT demo (paper)
                grid-bot cap vs the live fleet
    """
    st = daemon.state
    pnl = {}
    try:
        if hasattr(daemon, "pnl_snapshot"):
            pnl = (daemon.pnl_snapshot() or {}).get("fleet") or {}
    except Exception:
        pnl = {}
    cap = st.get("demo_bot_cap")
    active_n = len(st.get("active_bots") or {})
    try:
        headroom = int(cap) - active_n if cap is not None else None
    except (TypeError, ValueError):
        headroom = None
    return {
        "slots": st["slots"],
        "active_bots": st["active_bots"],
        "committed": st["committed"],
        "live_allow": st["live_allow"],
        "profiles": st.get("profiles", []),
        "capacity": st.get("capacity", {}),
        "account_limits": st.get("account_limits", {}),
        "capabilities": getattr(daemon, "capabilities", {}),
        # dependency readiness (presence booleans only)
        "env": getattr(daemon, "env_status", lambda: {})(),
        "last_cycle": st.get("last_cycle"),
        "journal_tail": st["journal"][-10:],
        "pnl": pnl,
        "demo_cap": {"cap": cap, "active": active_n,
                     "headroom": headroom},
        # loop-health heartbeat block (None until the first cycle) + the
        # data-feed observability tails (fail-soft empty shapes)
        "heartbeat": st.get("heartbeat"),
        "data_sources": _data_sources_payload(st),
        # latest LLM market brief (advisory intelligence lane; None before
        # the first successful call) — rendered in the console Fleet rail
        "market_brief": _brief_view(st.get("market_brief")),
    }


def _brief_view(brief):
    """Public copy of the persisted market brief (drops the internal
    at_epoch bookkeeping key; never raises)."""
    if not isinstance(brief, dict):
        return None
    return {k: v for k, v in brief.items() if k != "at_epoch"}


class Ctl(BaseHTTPRequestHandler):
    daemon = None

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # client hung up mid-response (curl -m, tab closed, etc.) —
            # not a server fault, just stop sending
            pass

    def do_GET(self):
        st = self.daemon.state
        if self.path == "/health":
            self._json(200, {"status": "ok", "at": _utcnow(),
                             "kill": os.path.exists(os.path.join(HERE, "KILL"))})
        elif self.path == "/status":
            self._json(200, status_payload(self.daemon))
        elif self.path == "/reliability":
            self._json(200, {"reliability": st["reliability"]})
        elif self.path == "/observe":
            self._json(200, {"observe": st.get("last_observe", {})})
        elif self.path == "/optimizer":
            self._json(200, {
                "optimizer": self.daemon.optimizer_status()
                if hasattr(self.daemon, "optimizer_status")
                else {"enabled": False, "available": False},
                "screen_cache_age_s": _cache_age(st),
            })
        else:
            self._json(404, {"error": "unknown path"})

    def do_POST(self):
        if self.path == "/kill":
            open(os.path.join(HERE, "KILL"), "w").write(_utcnow())
            self._json(200, {"killed": True})
        elif self.path == "/rescreen":
            # manual: always honored, even at the demo-bot cap
            self.daemon.queue_rescreen(force=True)
            self._json(200, {"queued": True})
        elif self.path == "/reliability":
            self.daemon.queue_reliability()
            self._json(200, {"queued": True})
        elif self.path == "/optimize":
            if not getattr(self.daemon, "optimizer", None):
                self._json(503, {"error": "optimizer unavailable "
                                         "(import failed — see journal)"})
                return
            self.daemon.queue_optimize()
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
            # manual: rotations are allowed at the demo-bot cap
            self.daemon.queue_rescreen(force=True)  # rotation evaluated on rescreen
            self._json(200, {"queued": True, "slot": slot,
                             "symbol": bot.get("symbol")})
        else:
            self._json(404, {"error": "unknown path"})

    def log_message(self, *a):
        pass

    def handle(self):
        # ponytail: swallow client-disconnect noise (curl -m, closed tabs)
        # so a hung-up browser poll doesn't pollute the daemon log
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass


def serve_ctl(daemon, port):
    Ctl.daemon = daemon
    # Bind host is env-overridable for containers (docker -p needs 0.0.0.0
    # inside the container; the local default stays loopback-only).
    _bind_host = os.environ.get("GRID_BIND_HOST", "127.0.0.1")
    try:
        HTTPServer((_bind_host, port), Ctl).serve_forever()
    except OSError as exc:
        # e.g. EADDRINUSE when a stray `daemon.py --once` holds the port —
        # this thread used to die silently, leaving the daemon trading with
        # no control plane. Surface it loudly (stdout + state journal).
        msg = f"ctl plane failed to bind {_bind_host}:{port}: {exc}"
        print(msg, flush=True)
        try:
            daemon.state.setdefault("journal", []).append(
                {"kind": "ctl-error", "msg": msg[:200], "at": _utcnow()})
        except Exception:
            pass
