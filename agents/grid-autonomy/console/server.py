#!/usr/bin/env python3
"""console — observation + configuration + dev-control backend for grid-autonomy.

A separate, additive HTTP service (the daemon itself is untouched): it reads
the daemon's state artifacts (state.json, decisions.jsonl, reliability.json,
reports/, daemon.log), proxies the daemon's ctl plane (:8799), and adds the
operations the ctl plane deliberately lacks — whitelisted config.yaml edits
(comment-preserving), KILL-file management, and daemon lifecycle control
(launchd-aware start/stop/restart). Serves the static frontend from ./static.

Bindings: 127.0.0.1 only. Destructive calls require {"confirm": true}.
Stdlib only, like the rest of grid-autonomy.

Run:
    python3 console/server.py            # :8798
    CONSOLE_PORT=8800 python3 console/server.py

API (all JSON):
    GET  /api/overview        merged snapshot (daemon, ctl, state, bots,
                              reliability, screen, config digest, PB health)
    GET  /api/daemon          supervisor/lifecycle detail
    GET  /api/state           raw state.json
    GET  /api/journal?limit=  journal tail (newest last, as stored)
    GET  /api/decisions?limit=decisions.jsonl tail (newest first)
    GET  /api/decisions/<id>   one decision + cohort (same symbol+regime)
    GET  /api/reliability     archetype ledger + sizing-tier computation
    GET  /api/recommendations?limit=  position-optimizer recommendations
                              (PocketBase records, newest first)
    GET  /api/screen          latest rescreen run card extract
    GET  /api/optimizer        proxy of the fast slot-optimizer status
                              (ctl /optimizer; fail-soft:
                              {"optimizer": null, "error": ...} + 200
                              when down)
    GET  /api/optimizer/swap-log  swap_log + per-slot idle trackers +
                              last arbiter verdict from state.optimizer
                              (no ctl round-trip; fail-soft empty)
    GET  /api/reports         run-card index
    GET  /api/reports/<stem>  one run card {json, md}
    GET  /api/logs?lines=&grep=  daemon.log tail
    GET  /api/config          parsed config.yaml + editable whitelist
    GET  /api/observe         proxy of the daemon ctl /observe (5s cache,
                              fail-soft: {"error": ...} + 200 when down)
    GET  /api/status          proxy of the daemon ctl /status  (5s cache,
                              fail-soft: {"error": ...} + 200 when down)
    GET  /api/pnl             PnL history — PocketBase `journal` records of
                              kind "pnl-snapshot" (via the pbclient adapter
                              with .pocketbase/pb.env superuser re-auth,
                              then the raw HTTP read, falling back to
                              state.json's journal array)
                              → {points: [{at, fleet{…}}]} newest-first
    GET  /api/chart?venue=&symbol=&interval=&bars=
                              OHLCV window for a slot's market — proxy of
                              the tvcli server's POST /fetch (interval ∈
                              15m|1h|4h|1d, bars 8..500, 60s in-process
                              cache; fail-soft: {"error": …, "bars": []}
                              + 200 when tvcli is down)
                              → {at, venue, symbol, interval,
                                 bars: [{t, o, h, l, c}] oldest-first}
    GET  /api/position-sweeps position-optimizer sweep history — journal
                              ring entries of kind position-optimizer-sweep /
                              position-optimizer / position-optimizer-applied
                              from state.json (fail-soft when absent)
                              → {sweeps: [{…}]} newest-first, last 25
    GET  /api/meta            ports, paths, versions
    GET  /api/llm/health      live provider ping + role routing matrix
                              (async: cold/expired cache answers
                              immediately with pending:true while one
                              background thread refreshes, 60s TTL)
                              (60s in-process cache; keys never returned)
    POST /api/ctl/rescreen    queue an immediate rescreen     {confirm}
    POST /api/ctl/optimize    queue an immediate fast-optimizer
                              cycle                        {confirm}
    POST /api/ctl/reliability queue a reliability refresh     {confirm}
    POST /api/ctl/rotate      force-rotate a slot {slot}     {confirm}
    POST /api/ctl/kill        write the KILL file            {confirm}
    POST /api/ctl/unkill      remove the KILL file           {confirm}
    POST /api/config          apply whitelisted edits {edits:{path:value}}
    POST /api/daemon/stop     KILL + SIGTERM (+SIGKILL w/ force) {confirm}
    POST /api/daemon/start    scripts/start.sh [--live-paper]   {confirm,
                              live_paper, clear_kill}
    POST /api/daemon/restart  launchd kickstart or stop+start   {confirm,
                              clear_kill, live_paper}
    POST /api/dev/reset       run `dev reset` (detached; wipes runtime
                              state, stops the stack; --keep-decisions /
                              --wt / --start)   {confirm, keep_decisions,
                              wt, start}
    POST /api/dev/reset-wt    run `dev reset-wt` (detached; deletes all
                              WunderTrading PAPER grid bots) {confirm}
    POST /api/dev/clean       run `dev clean` (detached; clears logs +
                              runtime artifacts)            {confirm}
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
GRID_HOME = os.path.dirname(HERE)
sys.path.insert(0, GRID_HOME)          # config_lite
sys.path.insert(0, HERE)               # yaml_edit

import yaml_edit  # noqa: E402
from config_lite import load_yaml  # noqa: E402

CONSOLE_PORT = int(os.environ.get("CONSOLE_PORT", "8798"))
STATE_DIR = os.environ.get("GRID_STATE_DIR") or os.path.join(GRID_HOME, "state")
CONFIG_PATH = os.path.join(GRID_HOME, "config.yaml")
STATIC_DIR = os.path.join(HERE, "static")
KILL_FILE = os.path.join(GRID_HOME, "KILL")
LAUNCHD_LABEL = "com.tvcli.grid-autonomy"
# The launchd-supervised daemon's stdout/stderr go here (see
# launchd/com.tvcli.grid-autonomy.plist), NOT state/daemon.log — start.sh
# writes daemon.log only for manual/nohup launches. The console must read
# the right file depending on the supervisor, or the Logs view silently
# shows a stale/empty file in the normal (supervised) production case.
LAUNCHD_LOG = os.path.join(STATE_DIR, "logs", "daemon-launchd.log")
PB_URL = os.environ.get("PB_URL", "http://127.0.0.1:8090").rstrip("/")

# WT account label surfaced in the UI header + fleet summary. The VPS
# container uses the vault item; the Mac uses its own browser session.
WT_ACCOUNT_LABEL = os.environ.get("WT_ACCOUNT_LABEL") or (
    "vps (vault account)" if os.path.isfile("/.dockerenv") else "local (Mac account)")

# LLM provider sidecar (set/choose/validate from the console). Mirrors the
# .pocketbase/pb.env "export KEY=\"val\"" format; the daemon sources it via
# run_launchd.load_llm_env / start.sh / self_heal_env. Keys land here chmod
# 0600 and are NEVER returned by any /api/llm endpoint (presence boolean only).
LLM_ENV_PATH = os.path.join(STATE_DIR, "llm.env")

# Provider order + role keys, mirrored from llm/provider.py so the API can
# report them without importing the daemon module (kept in sync manually).
LLM_PROVIDERS = ["cf", "nvidia", "openrouter", "mistral"]
LLM_ROLE_KEYS = ["bull", "bear", "bull_rebuttal", "bear_rebuttal", "facilitator",
                 "risk_seeking", "risk_neutral", "risk_conservative"]

DEFAULT_CTL_PORT = 8799

# Sizing-ladder thresholds (mirror daemon.size_multiplier semantics; surfaced
# so the UI can tier archetypes without hardcoding them client-side).
LADDER = {"probe_samples": 10, "full_samples": 30, "pf_pass": 1.3, "pf_kill": 1.0}

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
        ".png": "image/png", ".ico": "image/x-icon", ".json": "application/json"}

# ── editable config whitelist (path -> rule) ───────────────────────────
# Safety-critical lists (autonomy.live_profiles, paper_profiles) are
# deliberately NOT editable through the console.
EDITABLE = {
    "portfolio.total_usd": dict(t="float", min=10, max=1_000_000, group="Portfolio",
                                label="Fund size", unit="USD"),
    "portfolio.slots_default": dict(t="int", min=1, max=10, group="Portfolio",
                                    label="Slots"),
    "portfolio.slots_max": dict(t="int", min=3, max=10, group="Portfolio",
                                label="Max slots (fixed venues)"),
    "portfolio.slots_hard_max": dict(t="int", min=3, max=64,
                                     group="Portfolio",
                                     label="Hard slot ceiling (dynamic venues)"),
    "portfolio.min_slot_usd": dict(t="float", min=20, max=10_000,
                                    group="Portfolio",
                                    label="Min slot budget", unit="USD"),
    "portfolio.max_alloc_per_slot": dict(t="float", min=0.05, max=1.0,
                                         group="Portfolio",
                                         label="Max allocation per slot",
                                         unit="fraction"),
    "portfolio.cash_buffer_pct": dict(t="float", min=0.0, max=0.9,
                                      group="Portfolio", label="Cash buffer",
                                      unit="fraction"),
    "portfolio.venues.hyperliquid.balance_usd": dict(
        t="float", min=0, max=1_000_000, group="Portfolio",
        label="Hyperliquid sleeve", unit="USD"),
    "portfolio.venues.binance.balance_usd": dict(
        t="float", min=0, max=1_000_000, group="Portfolio",
        label="Binance sleeve", unit="USD"),
    "screen.rescreen_minutes": dict(t="float", min=5, max=1440, group="Cadence",
                                    label="Rescreen cadence", unit="min"),
    "grid_defaults.take_profit_pct": dict(
        t="float", min=0, max=2, group="Exits",
        label="Profit-exit target", unit="× slot budget",
        help="Cumulative total PnL (realized + mark) at which a bot is "
             "stopped at profit and its slot recycled. 0 disables. "
             "Never closes a losing line."),
    "screen.min_volume_usd": dict(t="int", min=100_000, max=100_000_000,
                                  group="Screening",
                                  label="Min 24h quote volume", unit="USD"),
    "screen.universe_max_symbols": dict(t="int", min=10, max=300,
                                        group="Screening",
                                        label="Universe size per venue",
                                        unit="symbols"),
    "screen.open_slot_min_score": dict(t="float", min=0, max=200,
                                       group="Screening",
                                       label="New-slot score floor",
                                       unit="pts"),
    "watch.interval_s": dict(t="float", min=10, max=3600, group="Cadence",
                             label="Health poll", unit="s"),
    "watch.adjust_steps_threshold": dict(t="float", min=0.5, max=10,
                                         group="Cadence",
                                         label="Re-centre drift", unit="steps"),
    "watch.gone_warn_after": dict(t="int", min=1, max=30, group="Cadence",
                                  label="Gone-bot warn after",
                                  unit="ticks"),
    "watch.gone_clear_min": dict(t="float", min=1, max=720, group="Cadence",
                                  label="Gone-bot slot clear", unit="min"),
    "policy.hysteresis_score": dict(t="float", min=0, max=50, group="Policy",
                                    label="Rotation hysteresis", unit="pts"),
    "policy.min_hold_h": dict(t="float", min=0, max=720, group="Policy",
                              label="Min hold", unit="h"),
    "autonomy.base_pct": dict(t="float", min=0.01, max=1.0, group="Sizing ladder",
                              label="Base tier", unit="fraction"),
    "autonomy.probe_pct": dict(t="float", min=0.01, max=1.0, group="Sizing ladder",
                               label="Probe tier", unit="fraction"),
    "autonomy.full_pct": dict(t="float", min=0.01, max=1.0, group="Sizing ladder",
                              label="Full tier", unit="fraction"),
    "autonomy.tier_max_grids.base": dict(
        t="int", min=3, max=100, group="Sizing ladder",
        label="Base-tier grid cap", unit="lines"),
    "autonomy.tier_max_grids.probe": dict(
        t="int", min=3, max=100, group="Sizing ladder",
        label="Probe-tier grid cap", unit="lines"),
    "autonomy.tier_max_grids.full": dict(
        t="int", min=3, max=100, group="Sizing ladder",
        label="Full-tier grid cap", unit="lines"),
    "reliability.kill_min_samples": dict(
        t="int", min=1, max=100, group="Sizing ladder",
        label="Kill-flag min samples", unit="trips"),
    "memory.k": dict(t="int", min=1, max=10, group="Deliberation",
                     label="Memories per candidate"),
    "optimizer.enabled": dict(t="bool", group="Optimizer",
                              label="Fast loop enabled"),
    "optimizer.interval_min": dict(t="float", min=2, max=5, group="Optimizer",
                                   label="Hunt cadence", unit="min"),
    "optimizer.idle_minutes": dict(t="float", min=1, max=120,
                                   group="Optimizer",
                                   label="Idle floor", unit="min"),
    "optimizer.idle_k": dict(t="float", min=0.25, max=5, group="Optimizer",
                             label="Idle × expected interval"),
    "optimizer.min_hold_min": dict(t="float", min=0, max=240,
                                   group="Optimizer",
                                   label="Fast min-hold", unit="min"),
    "optimizer.upgrade_margin": dict(t="float", min=1, max=50,
                                     group="Optimizer",
                                     label="Upgrade margin", unit="pts"),
    "optimizer.arbiter_margin": dict(t="float", min=0, max=50,
                                     group="Optimizer",
                                     label="Arbiter margin", unit="pts"),
    "optimizer.min_swap_interval_min": dict(t="float", min=5, max=720,
                                            group="Optimizer",
                                            label="Swap rate limit / slot",
                                            unit="min"),
    "optimizer.max_swaps_per_hour": dict(t="int", min=1, max=20,
                                         group="Optimizer",
                                         label="Swaps / hour cap"),
    "optimizer.hunt_top": dict(t="int", min=1, max=20, group="Optimizer",
                               label="Challengers refreshed"),
    "optimizer.fail_cooldown_min": dict(t="int", min=5, max=720,
                                        group="Optimizer",
                                        label="Failed-challenger cooldown",
                                        unit="min"),
    "optimizer.screen_cache_fresh_min": dict(t="float", min=10, max=1440,
                                             group="Optimizer",
                                             label="Cache freshness",
                                             unit="min"),
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── small readers (never raise) ────────────────────────────────────────

def _read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _tail(path, max_bytes=512 * 1024):
    """Last chunk of a file as text (files here are modest; bounded read)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - max_bytes))
            return f.read().decode("utf-8", "replace")
    except Exception:
        return ""


def _load_state():
    st = _read_json(os.path.join(STATE_DIR, "state.json"), {}) or {}
    st.setdefault("slots", [])
    st.setdefault("active_bots", {})
    st.setdefault("committed", {})
    st.setdefault("journal", [])
    st.setdefault("cooldowns_until", {})
    return st


def _ctl_port() -> int:
    port = os.environ.get("GRID_DAEMON_PORT")
    if port:
        try:
            return int(port)
        except ValueError:
            pass
    cfg = load_yaml(open(CONFIG_PATH).read()) if os.path.isfile(CONFIG_PATH) else {}
    try:
        return int((cfg.get("server") or {}).get("daemon_port", DEFAULT_CTL_PORT))
    except Exception:
        return DEFAULT_CTL_PORT


def _http_json(url, timeout=3.0, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return False, json.loads(exc.read() or b"{}")
        except Exception:
            return False, {"error": f"HTTP {exc.code}"}
    except Exception as exc:
        # transport-level failure (connection refused, timeout, DNS): the
        # exception must NOT sit under "error" — _ctl_err() trusts that key
        # for daemon-ANSWERED error bodies and would otherwise mask a dead
        # daemon as a daemon message. The raw message still surfaces via
        # each caller's "detail" field.
        return False, {"transport": str(exc)[:200]}


def _ctl(path, method="GET", body=None):
    return _http_json(f"http://127.0.0.1:{_ctl_port()}{path}", 3.0, method, body)


def _ctl_err(resp):
    """Error copy for a failed ctl call: the daemon's own message when it
    ANSWERED with a JSON error body (e.g. POST /optimize 503 "optimizer
    unavailable (import failed)"), "ctl unreachable" only when nothing
    answered. Masking daemon errors as unreachable sends the operator
    debugging the connection instead of the daemon."""
    return (resp.get("error") if isinstance(resp, dict) else None) \
        or "ctl unreachable"


# ── ctl-plane proxy cache (≤5s: several panels share one /status) ──────

_CTL_TTL = 5.0
_CTL_CACHE: dict = {}


def _ctl_cached(path):
    """GET a ctl resource with a ≤5s cache so parallel console panels
    (overview, fleet header, status proxy) reuse one daemon round-trip."""
    now = time.time()
    hit = _CTL_CACHE.get(path)
    if hit and hit[0] > now:
        return hit[1], hit[2]
    ok, body = _ctl(path)
    _CTL_CACHE[path] = (now + _CTL_TTL, ok, body)
    return ok, body


# ── PocketBase side-channel access (journal `pnl-snapshot` history) ────

PB_ENV_PATH = os.path.join(GRID_HOME, ".pocketbase", "pb.env")


def _pb_env() -> dict:
    """Parse the local PB sidecar env file into {KEY: value} ({} if absent).

    Values are used for Authorization only and are never returned by any
    console endpoint."""
    env = {}
    try:
        with open(PB_ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.removeprefix("export ").partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key:
                    env[key] = val
    except OSError:
        pass
    return env


_PB_CLIENT = None


def _pb_client():
    """Lazily-built shared pbclient.PB() for journal reads (never raises).

    Seeds os.environ with the PB_* keys from the local .pocketbase/pb.env
    sidecar — ONLY keys not already present — so PB() picks up the
    superuser credentials and transparently re-auths when the stored
    PB_TOKEN JWT is stale (the journal collection blocks public reads).
    Returns None on any failure; callers fall through to the raw HTTP /
    state.json paths. Credentials are used for Authorization only and are
    never logged or returned by any console endpoint."""
    global _PB_CLIENT
    if _PB_CLIENT is not None:
        return _PB_CLIENT
    try:
        env = _pb_env()
        for key, val in env.items():
            if key.startswith("PB_") and os.environ.get(key) is None:
                os.environ[key] = val
        import pbclient  # GRID_HOME is already on sys.path
        _PB_CLIENT = pbclient.PB(url=(os.environ.get("PB_URL") or PB_URL))
    except Exception:
        return None
    return _PB_CLIENT


def _pb_get(url, timeout=2.5, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, json.loads(resp.read() or b"{}")
    except Exception as exc:
        return False, {"error": str(exc)[:200]}


def _pnl_points(items) -> list:
    """Normalize journal records/events of kind pnl-snapshot into
    [{at, fleet}] — tolerant of the payload living at the top level, in
    `extra` (the PB journal collection's free field), or being absent
    entirely (pre-restart daemon)."""
    pts = []
    for r in items or []:
        if not isinstance(r, dict):
            continue
        fleet = r.get("fleet")
        bots = r.get("bots")
        extra = r.get("extra")
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = None
        if not isinstance(fleet, dict) and isinstance(extra, dict):
            fleet = extra.get("fleet")
            bots = bots or extra.get("bots")
        pts.append({"at": r.get("at"),
                    "fleet": fleet if isinstance(fleet, dict) else None,
                    "bots": bots if isinstance(bots, dict) else None})
    pts.sort(key=lambda p: p.get("at") or "", reverse=True)
    return pts[:200]


def pnl_payload() -> dict:
    """PnL history for the timeline: newest-first pnl-snapshot points.

    Primary source: the PB `journal` collection (filter kind='pnl-snapshot',
    sort=-at, perPage=200) via the pbclient adapter first (superuser
    re-auth from the local .pocketbase/pb.env sidecar), then the raw HTTP
    read (public, then the sidecar's stored token).
    Fallback: state.json's journal array (the daemon keeps the last 200
    events in-process). Missing kind entirely → empty points list, which is
    a valid response (daemon pre-restart)."""
    pb = _pb_client()
    if pb is not None:
        try:
            records = pb.list("journal", filter="(kind='pnl-snapshot')",
                              sort="-at", per_page=200)
        except Exception:
            records = []
        if records:
            return {"points": _pnl_points(records), "source": "pocketbase",
                    "total": len(records)}
    from urllib.parse import quote
    flt = quote("(kind='pnl-snapshot')")
    url = (f"{PB_URL}/api/collections/journal/records"
           f"?perPage=200&sort=-at&filter={flt}")
    ok, body = _pb_get(url)
    items = (body or {}).get("items") if ok and isinstance(body, dict) else None
    if not items:
        token = _pb_env().get("PB_TOKEN")
        if token:
            ok, body = _pb_get(url, token=token)
            items = (body or {}).get("items") if ok and isinstance(body, dict) else None
    if isinstance(items, list) and items:
        return {"points": _pnl_points(items), "source": "pocketbase",
                "total": (body or {}).get("totalItems")}
    st = _load_state()
    evs = [e for e in (st.get("journal") or [])
           if isinstance(e, dict) and e.get("kind") == "pnl-snapshot"]
    return {"points": _pnl_points(evs), "source": "state",
                "total": len(evs)}


# ── market OHLCV proxy (tvcli /fetch) for slot sparklines ──────────────

# Base URL of the tvcli serve daemon; read at import like PB_URL, and
# referenced through the module global so tests can point it at a stub.
TVCLI_BASE = os.environ.get("TVCLI_SERVER", "http://127.0.0.1:8765")
CHART_INTERVALS = ("15m", "1h", "4h", "1d")
CHART_TTL = 60.0            # seconds a fetched window stays fresh
CHART_CACHE_MAX = 32        # bounded in-process cache (keys are 4-tuples)

_CHART_CACHE: dict = {}     # key -> (expiry_ts, payload)


def _tv_symbol(symbol: str) -> str:
    """TradingView symbol for a console venue/base pair (mirrors
    market_regime._tv_symbol): uppercase, no slash, USDT/USDC/BUSD quote
    kept, else USDT appended; BOTH venues ride the Binance USDT pair
    (hyperliquid perps have no TV feed of their own)."""
    s = (symbol or "").upper().replace("/", "")
    if not s.endswith(("USDT", "USDC", "BUSD")):
        s += "USDT"
    return f"BINANCE:{s}"


def _chart_bars(venue: str, symbol: str, interval: str, bars) -> tuple[int, dict]:
    """OHLCV window for the fleet sparklines, proxied from tvcli /fetch.

    Returns (status_code, payload): 400 for a bad venue/interval/symbol,
    otherwise 200. tvcli returns periods newest-first; we sort ascending
    and emit slim {t, o, h, l, c} bars. A 60s in-process cache (bounded
    to 32 keys, oldest-expiry evicted) keeps the 5s console poll from
    hammering tvcli. tvcli outages degrade to 200 + {"error": …,
    "bars": []} — fail-soft, like every other proxy here."""
    venue = (venue or "").strip().lower()
    if venue not in ("binance", "hyperliquid"):
        return 400, {"error": "unknown venue (binance|hyperliquid)"}
    interval = (interval or "1h").strip()
    if interval not in CHART_INTERVALS:
        return 400, {"error": f"bad interval ({'|'.join(CHART_INTERVALS)})"}
    symbol = (symbol or "").strip()
    if not symbol:
        return 400, {"error": "missing symbol"}
    try:
        n = int(bars)
    except (TypeError, ValueError):
        n = 96
    n = max(8, min(500, n))

    key = (venue, symbol, interval, n)
    now = time.time()
    hit = _CHART_CACHE.get(key)
    if hit and hit[0] > now:
        return 200, hit[1]

    ok, body = _http_json(f"{TVCLI_BASE}/fetch", 30.0, "POST",
                          {"symbol": _tv_symbol(symbol),
                           "timeframe": interval, "bars": n})
    periods = body.get("periods") if ok and isinstance(body, dict) else None
    base = {"at": utcnow(), "venue": venue, "symbol": symbol,
            "interval": interval}
    if not isinstance(periods, list):
        err = body.get("error") if isinstance(body, dict) else None
        return 200, {**base, "bars": [], "count": 0,
                     "error": err or "tvcli unreachable"}
    out = []
    for per in sorted(periods, key=lambda p: (p.get("time") or 0)
                      if isinstance(p, dict) else 0):
        try:
            out.append({"t": int(per["time"]), "o": float(per["open"]),
                        "h": float(per["high"]), "l": float(per["low"]),
                        "c": float(per["close"])})
        except (KeyError, TypeError, ValueError):
            continue
    payload = {**base, "bars": out, "count": len(out)}
    _CHART_CACHE[key] = (now + CHART_TTL, payload)
    while len(_CHART_CACHE) > CHART_CACHE_MAX:
        oldest = min(_CHART_CACHE, key=lambda k: _CHART_CACHE[k][0])
        del _CHART_CACHE[oldest]
    return 200, payload


# ── daemon lifecycle helpers ───────────────────────────────────────────

def _pid() -> int | None:
    try:
        with open(os.path.join(STATE_DIR, "daemon.pid")) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return None


def _ps(pid, field) -> str | None:
    try:
        out = subprocess.run(["ps", "-o", f"{field}=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


def _launchd_managed() -> bool:
    try:
        out = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
                             capture_output=True, timeout=5)
        return out.returncode == 0
    except Exception:
        return False


def _log_source() -> dict:
    """Resolve which file actually holds the daemon's stdout/stderr.

    The supervisor decides the sink: the launchd daemon redirects its stdio
    in-process to LAUNCHD_LOG (launchd itself cannot open files on this
    removable volume, so the plist sends early stdio to /dev/null), while
    start.sh/manual runs append to state/daemon.log. Prefer the supervised file when launchd manages the
    daemon (the normal production case) but fall back to the state log when
    the supervised file is absent/empty — e.g. a manual run while the agent
    is loaded-but-not-running, or a fresh install before first supervised
    boot. Report both the chosen path and which source is authoritative so
    the UI can label the view honestly instead of showing a silently wrong
    file.
    """
    state_log = os.path.join(STATE_DIR, "daemon.log")
    managed = _launchd_managed()

    def _nonempty(path):
        try:
            return os.path.getsize(path) > 0
        except OSError:
            return False

    if managed and _nonempty(LAUNCHD_LOG):
        return {"path": LAUNCHD_LOG, "source": "launchd",
                "managed": True, "state_log": state_log}
    # Not supervised, or supervised file missing/empty — state log is the
    # best available (and correct for manual/start.sh runs).
    return {"path": state_log, "source": "state",
            "managed": managed, "state_log": state_log}


def _mode(pid) -> str:
    cmd = (_ps(pid, "command") or "") if pid else ""
    if "run_launchd.py" in cmd:
        return "live-paper"  # the launcher hardcodes --live-paper
    if "--live-paper" in cmd:
        return "live-paper"
    if cmd and "daemon.py" in cmd:
        return "dry-run"
    m = re.findall(r"dry_run=(True|False)", _tail(_log_source()["path"],
                                                  64 * 1024))
    if m:
        return "live-paper" if m[-1] == "False" else "dry-run"
    return "unknown"


def daemon_info() -> dict:
    pid = _pid()
    kill = os.path.exists(KILL_FILE)
    ok, health = _ctl("/health")
    managed = _launchd_managed()
    info = {
        "running": pid is not None,
        "pid": pid,
        "supervisor": "launchd" if managed else ("manual" if pid else "none"),
        "mode": _mode(pid) if pid else None,
        "cmdline": _ps(pid, "command") if pid else None,
        "started_at": _ps(pid, "lstart") if pid else None,
        "kill_file": kill,
        "ctl_reachable": ok and health.get("status") == "ok",
    }
    if pid:
        try:
            info["uptime_s"] = int(time.time() - os.stat(
                os.path.join(STATE_DIR, "daemon.pid")).st_mtime)
        except Exception:
            pass
    return info


# ── domain shaping ─────────────────────────────────────────────────────

def _tier(stats: dict) -> str:
    samples = stats.get("samples") or 0
    pf = stats.get("profit_factor") or 0.0
    recent = stats.get("recent_pf") or 0.0
    if samples and recent < LADDER["pf_kill"]:
        return "killed"
    if samples >= LADDER["full_samples"] and pf >= LADDER["pf_pass"]:
        return "full"
    if samples >= LADDER["probe_samples"]:
        return "probe"
    return "base"


POSITION_OPTIMIZER_JOURNAL_KINDS = {
    "position-optimizer-sweep", "position-optimizer", "position-optimizer-applied"}


def position_sweeps_payload(limit=25):
    """Last position-optimizer journal events from state.json's ring
    (newest first, capped at `limit`). Fail-soft: a missing/corrupt state
    file yields [] — the UI shows its empty state, never a 500."""
    try:
        journal = _load_state().get("journal") or []
    except Exception:
        return []
    sweeps = [e for e in journal
              if isinstance(e, dict)
              and e.get("kind") in POSITION_OPTIMIZER_JOURNAL_KINDS]
    return sweeps[-limit:][::-1]


def reliability_archive_payload(limit_per_archetype=20) -> dict:
    """Recent closed round-trips per archetype, sourced from
    `state/reliability_archive.json` (rotated-out bots' last legs). Drives
    the expandable row under each archetype on the Reliability tab — the
    operator wants to see *which* trades produced the PF/win rate, not
    just the aggregates.

    Each row is a slim {ts, symbol, realized, hold_s, is_panic,
    is_synthetic, strategy_id} dict; the daemon's archived shape is
    richer (gross / fee / hold_s / strategy_id) but the UI only needs
    the operator-facing columns. Synthetic rows are flagged so the UI
    can mark them separately (the aggregates above already flag the
    pollution in bulk via the `synthetic_samples` count)."""
    try:
        sys.path.insert(0, os.path.join(GRID_HOME, "execution"))
        import reliability_grid as _rg
        arch = _rg.archived_by_archetype() or {}
    except Exception:
        arch = {}
    out = {}
    for name, rows in arch.items():
        slim = []
        for t in rows or []:
            if not isinstance(t, dict):
                continue
            slim.append({
                "ts": t.get("close_ts") or t.get("ts") or t.get("at_epoch"),
                "symbol": t.get("symbol") or "",
                "venue": t.get("venue") or "",
                "realized": t.get("realized_usd") or t.get("pnl") or t.get("realized"),
                "hold_s": t.get("hold_s"),
                "is_panic": bool(t.get("is_panic") or t.get("panic")),
                "is_synthetic": bool(t.get("synthetic")),
                "strategy_id": t.get("strategy_id") or t.get("bot_code"),
            })
        slim.sort(key=lambda r: r.get("ts") or 0, reverse=True)
        out[name] = slim[:max(1, min(limit_per_archetype, 100))]
    return {"archetypes": out, "source": "reliability_archive.json"}


def reliability_payload() -> dict:
    path = os.path.join(STATE_DIR, "reliability.json")
    ledger = _read_json(path, {}) or {}
    missing = not os.path.isfile(path)
    age_h = None
    try:
        age_h = round((time.time() - os.path.getmtime(path)) / 3600.0, 1)
    except OSError:
        missing = True
    archs = {}
    for arch, st in ledger.items():
        if isinstance(st, dict):
            st = dict(st)
            st["tier"] = _tier(st)
            # real (lived) samples vs synthetic/seeded backfill — the
            # aggregates still include both, so the UI flags pollution.
            synth = st.get("synthetic_samples")
            st["real_samples"] = (max(0, (st.get("samples") or 0)
                                      - synth) if isinstance(synth, (int, float))
                                  else st.get("samples"))
            # ladder progression: which rung is the archetype on, and how
            # many more samples before the NEXT rung. Consumed by the UI
            # to render progress arrows + a kill-flag ladder status line.
            samples = st.get("samples") or 0
            probe = LADDER["probe_samples"]
            full = LADDER["full_samples"]
            if st["tier"] == "base":
                st["ladder_next"] = "probe"
                st["ladder_next_at"] = probe
                st["ladder_progress_pct"] = round(min(100, (samples / probe) * 100), 1)
            elif st["tier"] == "probe":
                st["ladder_next"] = "full"
                st["ladder_next_at"] = full
                st["ladder_progress_pct"] = round(min(100, (samples / full) * 100), 1)
            elif st["tier"] == "full":
                st["ladder_next"] = None
                st["ladder_next_at"] = None
                st["ladder_progress_pct"] = 100.0
            elif st["tier"] == "killed":
                st["ladder_next"] = None
                st["ladder_next_at"] = None
                st["ladder_progress_pct"] = 0.0
            archs[arch] = st
    # the ledger is a snapshot refreshed by the daemon's health cycle;
    # past the 24h refresh cadence (+grace) it is stale evidence.
    stale = bool(age_h is not None and age_h > 26)
    if missing:
        note = ("no closed round-trips yet — archetypes populate once paper "
                "bots close their first grid trip (first refresh after a "
                "fresh deploy)")
    elif not archs:
        note = ("ledger computed but empty — no closed grid round-trips yet; "
                "archetypes appear after the first completed trip")
    elif stale:
        note = (f"ledger snapshot is {age_h}h old (stale past the 24h "
                f"refresh cadence)")
    else:
        note = ""
    # kill-flag ladder thresholds (mirrors reliability_grid.py constants —
    # the daemon binds them on its own, the console just surfaces them so
    # an operator can see WHY a particular archetype was refused).
    return {
        "ladder": LADDER,
        "kill_thresholds": {
            "kill_min_samples": 10,
            "recent_window": 20,
            "live_min_samples": 30,
        },
        "archetypes": archs,
        "ledger_age_h": age_h, "stale": stale, "missing": missing,
        "note": note, "refresh_cadence_h": 24,
    }


def _decision_index() -> dict:
    """decisions.jsonl by id → record; cheap full scan, fail-soft empty.

    Powers the slot-card "decision evidence" lookup and the
    /api/decisions/<id> endpoint. ~1k decisions × few hundred bytes
    each = well under a millisecond on the stdlib JSON parser."""
    idx = {}
    try:
        for line in _tail(os.path.join(STATE_DIR, "decisions.jsonl")).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            did = r.get("id")
            if did:
                idx[did] = r
    except OSError:
        pass
    return idx


def _screen_fit_index() -> dict:
    """screen_cache candidates keyed by (venue, symbol) → candidate dict.

    The latest screen's tvcli_fit + confluence_bonus is what the active
    bot was selected ON, so the Fleet slot cards should show it. When
    the cache is older than `optimizer.screen_cache_fresh_min` (default
    120m), the index still serves the last-known fitness with an
    `at_age_min` field so the UI can flag a stale read."""
    idx = {}
    sc = _read_json(os.path.join(STATE_DIR, "state.json"), {}) or {}
    cache = sc.get("screen_cache") or {}
    age_min = None
    at = cache.get("at")
    if at:
        try:
            age_min = round((time.time() - float(at)) / 60.0, 1)
        except (TypeError, ValueError):
            age_min = None
    for c in (cache.get("candidates") or []):
        if not isinstance(c, dict):
            continue
        key = f"{c.get('venue')}:{c.get('symbol')}"
        # tvcli_fit is the per-skill hunt read (squeeze/chop/mtf/vp/sr/dvi
        # metrics, in a dict). Pass it through verbatim so the slot card
        # and decision evidence panel show the same data — without it the
        # slot card's TVCLI confluence strip renders empty chips even
        # though the screen cache carries the values.
        fit_obj = c.get("tvcli_fit")
        idx[key] = {
            "score_final": c.get("score_final"),
            "score": c.get("score"),
            "regime": c.get("regime"),
            "archetype": c.get("archetype"),
            "tvcli_fit": fit_obj if isinstance(fit_obj, dict) else None,
            "confluence_bonus": c.get("confluence_bonus"),
            "confluence_ok": c.get("confluence_ok"),
            "confluence_notes": c.get("confluence_notes"),
            "spread_pct": c.get("spread_pct"),
            "step_pct": c.get("step"),
            "expected_fills_per_24h": c.get("expected_fills_per_24h"),
            "harvest_net_pct_24h": c.get("harvest_net_pct_24h"),
            "confirm_4h": c.get("confirm_4h"),
            "flags": c.get("flags"),
            "at_age_min": age_min,
        }
    idx["__at_age_min__"] = age_min
    return idx


def _enriched_bots(st: dict) -> list[dict]:
    observe = st.get("last_observe") or {}
    dec_idx = _decision_index()
    fit_idx = _screen_fit_index()
    out = []
    for slot_key, bot in (st.get("active_bots") or {}).items():
        bot = dict(bot or {})
        obs = bot.get("observed") or observe.get(str(slot_key)) or {}
        pol = (bot.get("stagnation_policy") or {})
        stag_if = pol.get("stagnant_if") or {}
        fills, ratio = obs.get("fills_24h"), obs.get("realized_ratio")
        stagnant = None
        if isinstance(fills, (int, float)) and isinstance(ratio, (int, float)):
            min_fills = stag_if.get("min_fills_24h")
            min_ratio = stag_if.get("min_realized_ratio")
            if min_fills is not None and min_ratio is not None:
                stagnant = fills < min_fills and ratio < min_ratio
        # tvcli_fit: the screen-time confluence read the bot was selected on.
        # Joined from the latest screen cache by venue+symbol — when absent
        # (bot not in the latest cache, e.g. deployed on a prior rescreen
        # that's since rolled off), the keys are just null and the UI
        # degrades to "—".
        fit = fit_idx.get(f"{bot.get('venue')}:{bot.get('symbol')}") or {}
        # decision evidence: full record so the Fleet rail can render the
        # debate + risk-team + confluence without a second round-trip.
        dec_id = bot.get("decision_id")
        dec = dec_idx.get(dec_id) if dec_id else None
        # position-optimizer summary: the slow lane's last analysis.
        po = bot.get("position_optimizer") or {}
        out.append({
            "slot": int(slot_key) if str(slot_key).isdigit() else slot_key,
            "symbol": bot.get("symbol"), "venue": bot.get("venue"),
            "grid_type": (bot.get("ticket") or {}).get("grid_type"),
            "since": bot.get("since"), "adopted": bool(bot.get("adopted")),
            "bot_code": bot.get("bot_code"), "channel": bot.get("channel"),
            "archetype": bot.get("archetype"),
            "score_final": bot.get("score_final"),
            "decision_id": dec_id,
            "decision": dec,
            "tvcli_fit": fit.get("tvcli_fit"),
            "tvcli_bonus": fit.get("confluence_bonus"),
            "tvcli_ok": fit.get("confluence_ok"),
            "tvcli_notes": fit.get("confluence_notes"),
            "screen_score": fit.get("score_final"),
            "screen_age_min": fit.get("at_age_min"),
            "expected_fills_24h": fit.get("expected_fills_per_24h"),
            "harvest_net_pct_24h": fit.get("harvest_net_pct_24h"),
            "force_rotate": bool(bot.get("force_rotate")),
            "needs_reanalysis": bool(bot.get("needs_reanalysis")),
            "committed": (st.get("committed") or {}).get(str(slot_key)),
            "stagnation_policy": pol,
            "observed": obs,
            "stagnant": stagnant,
            "position_optimizer": po,
            "optimizer_tracker": bot.get("optimizer"),
            # current exit profile (enriched grid_list fields projected
            # by the daemon health cycle / observe layer) — renders the
            # exit badge on the fleet card when present
            "exits": (bot.get("exits") if isinstance(bot.get("exits"), dict)
                      else (obs.get("exits")
                            if isinstance(obs.get("exits"), dict) else None)),
            "take_profit_usd": bot.get("take_profit_usd"),
            "loss_veto": obs.get("loss_veto") if isinstance(obs, dict) else None,
        })
    out.sort(key=lambda b: (not str(b["slot"]).isdigit(), str(b["slot"])))
    return out


def _latest_report_meta(kind: str):
    """(report, stem) of the most recent run card of this kind, or
    (None, None). The stem is what the console hands to /api/reports/<stem>
    for the rail-card deep-link, so the operator can jump from a screen
    rank to the deliberation that produced it without re-typing the ts."""
    rdir = os.path.join(STATE_DIR, "reports")
    try:
        names = sorted((n for n in os.listdir(rdir)
                        if n.endswith(".json") and f"-{kind}." in n), reverse=True)
    except OSError:
        return None, None
    for name in names:
        rep = _read_json(os.path.join(rdir, name))
        if rep is not None:
            return rep, os.path.splitext(name)[0]
    return None, None


def _latest_report(kind: str):
    rep, _ = _latest_report_meta(kind)
    return rep


def _screen_score_history(limit: int) -> list[dict]:
    """Top-of-screen `score_final` over the last N rescreen cards, oldest
    first. Powers the "last screen" rail's trend sparkline. Returns
    [{at, score, n_candidates}] for each card; [] when fewer than two
    rescreens exist (a one-point sparkline is just a dot)."""
    rdir = os.path.join(STATE_DIR, "reports")
    try:
        names = sorted((n for n in os.listdir(rdir)
                        if n.endswith(".json") and "-rescreen." in n),
                       reverse=True)[:max(1, limit)]
    except OSError:
        return []
    out = []
    for n in names:
        rep = _read_json(os.path.join(rdir, n))
        if not isinstance(rep, dict):
            continue
        scr = rep.get("screen") or {}
        top = (scr.get("top3") or [])
        score = top[0].get("score_final") if top and isinstance(top[0], dict) else None
        out.append({"at": rep.get("at"),
                    "score": score,
                    "n_candidates": scr.get("n_candidates")})
    out.reverse()
    return [r for r in out if r.get("at") and _is_num(r.get("score"))]


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def screen_payload() -> dict | None:
    rep, stem = _latest_report_meta("rescreen")
    if not rep:
        return None
    scr = rep.get("screen") or {}
    return {
        "at": rep.get("at"), "cycle_kind": rep.get("cycle_kind"),
        "n_candidates": scr.get("n_candidates"),
        "top": scr.get("top3") or [],
        "hunt_stats": scr.get("hunt_stats") or {},
        "data_sources": rep.get("data_sources") or {},
        "run_card_stem": stem,
        # 12-point score history for the rail's "is screening improving?"
        # sparkline: top-of-screen score_final across the last 12 rescreen
        # run cards. Cheap (file scan + JSON parse) and powers a glanceable
        # trend. Empty list = fewer than 2 rescreen cards on disk.
        "score_history": _screen_score_history(12),
        # the run-card JSON keys are deliberate/guard (singular, as the
        # daemon writes them): pass them through verbatim so the UI can
        # show the per-cycle deliberation + guard verdict
        "deliberations": rep.get("deliberations") or [],
        "guard": rep.get("guard") or [],
        "deployments": rep.get("deployments") or [],
        "rotations": rep.get("rotations") or [],
        "actions": rep.get("actions") or [],
        "caveats": rep.get("caveats") or [],
        "dry_run": rep.get("dry_run"),
    }


def decision_payload_by_id(decision_id: str) -> dict | None:
    """One full decision record by id, plus context.

    Powers the Fleet slot-card "View decision evidence" deep-link and the
    /api/decisions/<id> endpoint. Fails soft with None when missing —
    the UI shows a stale-by-id note instead of a 500."""
    if not decision_id:
        return None
    idx = _decision_index()
    row = idx.get(decision_id)
    if row is None:
        return None
    # Sibling decisions for the same symbol + venue + regime — the cohort
    # the k=3 memory recall drew from, surfaced so the UI can answer
    # "what happened last time we ran this archetype here" without
    # scanning the ledger. Identity filter is by `id` (each call to
    # _decision_index returns fresh objects, so `is` would always match).
    sib = [r for r in idx.values()
           if r.get("id") != decision_id
           and r.get("symbol") == row.get("symbol")
           and r.get("venue") == row.get("venue")
           and r.get("regime") == row.get("regime")]
    sib.sort(key=lambda r: r.get("at") or "", reverse=True)
    return {"decision": row,
            "cohort_size": len(sib),
            "cohort_realized": sum(
                float(((r.get("outcome") or {}).get("realized_pnl")) or 0)
                for r in sib if isinstance(r.get("outcome"), dict))}


def optimizer_swap_log() -> dict:
    """The fast slot-optimizer's swap_log + per-slot trackers + last
    arbiter verdict — surfaced in the console so an operator can answer:

      * which slots have been swapped and when
      * how long each slot has been idle (last_fills / last_increase_at)
      * what the Mistral arbiter last concluded per idle slot
      * how many cycles the optimizer has run + swap totals

    All from state.optimizer (already in-memory, no extra I/O); the
    arbiter verdict comes from the last cycle report's `arbiter` block.
    """
    st = _read_json(os.path.join(STATE_DIR, "state.json"), {}) or {}
    opt = st.get("optimizer") or {}
    trackers = opt.get("trackers") or {}
    swap_log = opt.get("swap_log") or []
    last = opt.get("last_report") or {}
    now = time.time()
    tracker_rows = []
    for slot, tr in trackers.items():
        if not isinstance(tr, dict):
            continue
        last_inc = tr.get("last_increase_at")
        idle_min = None
        if isinstance(last_inc, (int, float)) and last_inc > 0:
            idle_min = round((now - float(last_inc)) / 60.0, 1)
        tracker_rows.append({
            "slot": slot,
            "last_fills": tr.get("last_fills"),
            "last_increase_at": last_inc,
            "idle_min": idle_min,
        })
    tracker_rows.sort(key=lambda r: (r["idle_min"] is None,
                                      r["idle_min"] if r["idle_min"] is not None else 0),
                      reverse=True)
    swaps = []
    for e in swap_log:
        if not isinstance(e, dict):
            continue
        swaps.append({
            "slot": e.get("slot"),
            "at": e.get("at"),
            "ok": bool(e.get("ok")),
            "at_iso": (datetime.fromtimestamp(float(e["at"]), tz=timezone.utc).isoformat(timespec="seconds")
                       if isinstance(e.get("at"), (int, float)) and e["at"] > 0 else None),
        })
    swaps.sort(key=lambda s: s.get("at") or 0, reverse=True)
    arbiter = last.get("arbiter") if isinstance(last, dict) else None
    return {
        "cycles": opt.get("cycles") or 0,
        "swaps_total": opt.get("swaps_total") or 0,
        "cycles_since_card": opt.get("cycles_since_card") or 0,
        "last_at": opt.get("last_at"),
        "trackers": tracker_rows,
        "swaps": swaps[:60],
        "last_arbiter": arbiter,
        "caveats": (last.get("caveats") or []) if isinstance(last, dict) else [],
    }


# ── LLM provider health (async ping — never blocks the request) ────────
# /api/llm/health used to run the provider ping subprocess synchronously
# inside the request handler: a cold cache blocked the HTTP response for
# as long as the slowest provider in the chain answered (measured ~70s).
# The refresh now runs in ONE background daemon thread; the handler
# answers sub-second with a pending marker + last-known-good data.

_LLM_HEALTH_TTL = 60.0
_LLM_HEALTH_CACHE_KEY = "llm_health"
_LLM_HEALTH_LOCK = threading.Lock()   # guards the cache entry + refresh flag
_LLM_HEALTH_REFRESHING = False        # True while one refresh thread runs
_LLM_HEALTH_LAST: dict | None = None  # last-known-good payload — never
                                      # evicted on read, only replaced by a
                                      # newer SUCCESSFUL refresh (stale
                                      # serving keeps working after the TTL
                                      # lapses while a refresh is in flight)

_LLM_HEALTH_PENDING_NOTE = (
    "Ping runs in the background (async, never blocks the response); "
    "showing pending/last-known data. Keys never returned. Arbiter default "
    "= mistral (override via config.optimizer.llm_provider).")


def _llm_health_assemble(ping_results: list, chain: list, *,
                         pending: bool = False, stale: bool = False,
                         error: str | None = None,
                         note: str | None = None) -> dict:
    """Build the /api/llm/health wire payload: today's schema (at, chain,
    results, roles, role_keys, arbiter_provider, note) plus the additive
    pending / stale / error markers. Keys are never included — presence
    booleans only."""
    side = _llm_sidecar()
    raw_roles = side.get("GRID_LLM_ROLES")
    roles = {}
    if raw_roles:
        try:
            parsed = json.loads(raw_roles)
            if isinstance(parsed, dict):
                roles = parsed
        except Exception:
            roles = {}

    # active LLM provider for the fast-lane arbiter, mirrored from
    # config.optimizer.llm_provider (with sensible default to "mistral")
    cfg = (config_payload().get("config") or {})
    arbiter_provider = ((cfg.get("optimizer") or {}).get("llm_provider")
                        or "mistral")

    out = {
        "at": utcnow(),
        "chain": chain,
        "results": ping_results,
        "roles": roles,
        "role_keys": LLM_ROLE_KEYS,
        "arbiter_provider": arbiter_provider,
        "pending": pending,
        "note": note or (
            "Live ping cached 60s, refreshed in a background thread (never "
            "blocks the response); keys never returned. Arbiter default = "
            "mistral (override via config.optimizer.llm_provider)."),
    }
    if stale:
        out["stale"] = True
    if error:
        out["error"] = error
    return out


def _llm_health_probe() -> tuple[dict, str | None]:
    """Run the provider ping subprocess and build the full health payload.

    BLOCKING — up to the 180s subprocess timeout; only ever called from
    the background refresh thread (_llm_health_refresh). Returns
    (payload, error_note): error_note is None on a successful ping and a
    short human note when the ping failed / timed out / produced no JSON
    (the payload then carries the existing fallback results). Never
    raises. Keys are read from the sidecar into the subprocess env but
    never surfaced."""
    provider_script = os.path.join(GRID_HOME, "llm", "provider.py")
    ping_results: list = []
    chain: list = []
    error_note: str | None = None
    if os.path.isfile(provider_script):
        env = dict(os.environ)
        for key, val in _llm_sidecar().items():
            env[key] = val
        try:
            proc = subprocess.run(
                [sys.executable, provider_script, "--ping", "--json"],
                capture_output=True, text=True, timeout=180, env=env,
                cwd=GRID_HOME)
            if proc.returncode == 0:
                try:
                    data = json.loads(proc.stdout)
                    chain = data.get("chain") or []
                    for r in (data.get("results") or []):
                        ping_results.append({
                            "provider": r.get("provider"),
                            "ok": bool(r.get("ok")),
                            "latency_ms": r.get("latency_ms"),
                            "error": (str(r.get("error", ""))[:160]
                                      if not r.get("ok") else None),
                        })
                except Exception:
                    error_note = "ping output not JSON"
            else:
                error_note = f"ping subprocess rc={proc.returncode}"
        except subprocess.TimeoutExpired:
            error_note = "ping timeout (180s)"
            ping_results = [{"provider": p, "ok": False, "error": "ping timeout (180s)"}
                            for p in ("cf", "nvidia", "openrouter", "mistral")]
        except Exception as exc:
            error_note = f"ping failed: {str(exc)[:120]}"
            ping_results = [{"provider": "?", "ok": False,
                             "error": f"ping failed: {str(exc)[:120]}"}]

    # fall back to presence-only when the ping subprocess failed
    if not ping_results:
        side = _llm_sidecar()
        for name in LLM_PROVIDERS:
            kenv = {"cf": ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_KEY", "CLOUDFLARE_AI_TOKEN"),
                    "nvidia": ("NVIDIA_API_KEY",),
                    "openrouter": ("OPENROUTER_API_KEY",),
                    "mistral": ("MISTRAL_API_KEY",)}[name]
            present = any(side.get(k) or os.environ.get(k) for k in kenv)
            ping_results.append({"provider": name, "ok": present,
                                 "error": None if present else "no key"})

    return _llm_health_assemble(ping_results, chain,
                               error=error_note), error_note


def _llm_health_refresh() -> None:
    """Background daemon-thread refresh: run the blocking probe and
    publish its result into the 60s cache. At most one instance runs at
    a time — llm_health() sets _LLM_HEALTH_REFRESHING (under the lock)
    before spawning this thread; the finally below clears it. Failure
    semantics: a failed probe + an existing last-known-good payload merge
    into a stale:true + error-note response; otherwise the presence-only
    fallback payload is cached for the TTL. Never raises."""
    global _LLM_HEALTH_LAST, _LLM_HEALTH_REFRESHING
    try:
        try:
            payload, err = _llm_health_probe()
        except Exception as exc:               # defensive: probe never raises
            payload = _llm_health_assemble([], [])
            err = f"probe crashed: {str(exc)[:120]}"
        with _LLM_HEALTH_LOCK:
            if err and _LLM_HEALTH_LAST:
                # failed refresh, previous good data exists → serve stale
                out = dict(_LLM_HEALTH_LAST)
                out["stale"] = True
                out["error"] = err
            else:
                out = dict(payload)
                if err:
                    out["error"] = err
                else:
                    _LLM_HEALTH_LAST = payload   # only success replaces it
            _CTL_CACHE[_LLM_HEALTH_CACHE_KEY] = (
                time.time() + _LLM_HEALTH_TTL, True, out)
    finally:
        with _LLM_HEALTH_LOCK:
            _LLM_HEALTH_REFRESHING = False


def llm_health() -> dict:
    """Live LLM provider reachability + the role-pinning matrix, served
    without exposing any keys — and WITHOUT ever blocking the request
    thread on the (up to 180s) provider ping.

    Async contract:
    * cache fresh (≤60s) → served synchronously, exactly as before.
    * cache cold/expired → immediate 200 (sub-second) with pending:true
      and the last-known-good results ([] when none), while ONE daemon
      thread runs the existing `llm/provider.py --ping --json` subprocess
      and writes the result back into the 60s cache. Additional cold
      requests while a refresh is in flight get the same pending/stale
      answer without spawning more threads.
    * failed/timed-out refresh → presence-only fallback (or the previous
      payload with stale:true + the error note), cached for the TTL.
      The last-known-good payload is never evicted on read — only
      replaced by a newer successful refresh."""
    global _LLM_HEALTH_REFRESHING
    now = time.time()
    with _LLM_HEALTH_LOCK:
        hit = _CTL_CACHE.get(_LLM_HEALTH_CACHE_KEY)
        if hit and hit[0] > now:
            return hit[2]
        last = _LLM_HEALTH_LAST
        spawn = not _LLM_HEALTH_REFRESHING
        if spawn:
            _LLM_HEALTH_REFRESHING = True
    if spawn:
        threading.Thread(target=_llm_health_refresh, daemon=True,
                         name="llm-health-refresh").start()
    if last:
        out = dict(last)
        out["pending"] = True
        out["stale"] = True
        out["note"] = _LLM_HEALTH_PENDING_NOTE
        return out
    return _llm_health_assemble([], [], pending=True,
                                 note=_LLM_HEALTH_PENDING_NOTE)


def decisions_payload(limit: int) -> list[dict]:
    text = _tail(os.path.join(STATE_DIR, "decisions.jsonl"))
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    rows.sort(key=lambda r: r.get("at") or "", reverse=True)
    return rows[:max(1, min(limit, 1000))]


def recommendations_payload(limit: int) -> dict:
    """Position-optimizer recommendations from the PocketBase side channel
    (newest first). Non-fatal: an empty list when PB is down or the
    collection does not exist yet. Sorted by the engine's ISO `at` field —
    PB 0.40 has no auto `created` system field, so `sort=-created` 400s.

    Auth: the collection rules block public reads, so this goes through the
    same ladder as pnl_payload — pbclient (pb.env-seeded superuser re-auth),
    then raw HTTP with the sidecar's stored PB_TOKEN. A bare unauthenticated
    read used to 401/404 here, so the view always looked empty even when
    records existed."""
    limit = max(1, min(limit, 500))
    items = None
    pb = _pb_client()
    if pb is not None:
        try:
            items = pb.list("recommendations", sort="-at",
                            per_page=limit)
        except Exception:
            items = None
    if not items:
        url = (f"{PB_URL}/api/collections/recommendations/records"
               f"?perPage={limit}&sort=-at")
        ok, body = _pb_get(url)
        items = (body or {}).get("items") if ok and isinstance(body, dict) else None
        if not items:
            token = _pb_env().get("PB_TOKEN")
            if token:
                ok, body = _pb_get(url, token=token)
                items = (body or {}).get("items") \
                    if ok and isinstance(body, dict) else None
    items = [dict(r) for r in items if isinstance(r, dict)] \
        if isinstance(items, list) else []
    source = "pocketbase"
    if not items:
        # Journal fallback: a dry-run mirror never persists (persist is
        # gated on not-dry_run) but DOES journal every recommendation —
        # without this the Optimizer view looked permanently empty on the
        # az00 mirror even though the engine emits recs every sweep.
        # Derived rows carry journal_only + a source marker; they are NOT
        # applyable (no PB record to flip applied on).
        evs = [e for e in (_load_state().get("journal") or [])
               if e.get("kind") == "position-optimizer"
               and e.get("recommendation")]
        evs.sort(key=lambda e: e.get("at") or "", reverse=True)
        items = []
        for e in evs[:limit]:
            venue = ""
            parts = (e.get("msg") or "").split(" ", 1)
            if len(parts) == 2 and ":" in parts[1]:
                venue = parts[1].split(":")[0]
            items.append({
                "at": e.get("at"), "slot": e.get("slot"),
                "venue": venue, "symbol": e.get("symbol"),
                "recommendation": e.get("recommendation"),
                "expected_delta_pct": e.get("expected_delta_pct"),
                "trigger": e.get("trigger"),
                "dry_run": bool(e.get("dry_run")),
                "applied": False, "applied_at": None,
                "blocked_by": "journal-only",
                "journal_only": True,
            })
        if items:
            source = "journal"

    # Enrich each record with the apply-gate verdict so the UI can say WHY
    # a recommendation is sitting unapplied: config `apply: false` (advisory
    # mode), the persisted-per-day cap, or already applied.
    cfg = (config_payload().get("config") or {}).get("position_optimizer") or {}
    apply_enabled = bool(cfg.get("apply"))
    max_day = cfg.get("max_apply_per_day") or 4
    today = utcnow()[:10]
    # keeps are baseline records, not applies: they don't consume the
    # engine's per-day persist budget (position_optimizer._persist skips
    # them since 2026-09-08) and must not count toward the cap here either.
    persisted_today = sum(1 for r in items
                          if str(r.get("at") or "").startswith(today)
                          and not r.get("journal_only")
                          and r.get("recommendation") != "keep")
    for r in items:
        r.setdefault("applied", False)
        r.setdefault("applied_at", None)
        if r.get("journal_only"):
            continue
        if r.get("recommendation") == "keep":
            # a keep is a no-op verdict — there is nothing to apply, so no
            # apply-gate reason applies (it used to show "rate limit" once
            # the daily cap filled, implying the keep was waiting on a
            # gate it would never pass)
            r["blocked_by"] = ""
            continue
        if r.get("applied"):
            r["blocked_by"] = "applied"
        elif not apply_enabled:
            r["blocked_by"] = "apply disabled"
        elif persisted_today >= max_day:
            r["blocked_by"] = "rate limit"
        else:
            r["blocked_by"] = ""
    return {"recommendations": items, "apply": apply_enabled,
            "max_apply_per_day": max_day, "persisted_today": persisted_today,
            "source": source}


def reports_index() -> list[dict]:
    rdir = os.path.join(STATE_DIR, "reports")
    try:
        names = os.listdir(rdir)
    except Exception:
        return []
    stems = {}
    for n in names:
        base, ext = os.path.splitext(n)
        if ext in (".json", ".md"):
            stems.setdefault(base, {})[ext[1:]] = True
    out = []
    for stem, has in sorted(stems.items(), reverse=True):
        m = re.match(r"^(\d{8}T\d{6}Z)-(.+)$", stem)
        at = None
        if m:
            try:
                at = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ") \
                    .replace(tzinfo=timezone.utc).isoformat()
            except Exception:
                pass
        out.append({"stem": stem, "kind": m.group(2) if m else stem,
                    "at": at, "json": bool(has.get("json")),
                    "md": bool(has.get("md"))})
    return out[:300]


def logs_payload(lines: int, grep: str | None) -> dict:
    # the supervisor decides the sink (launchd in-process redirect vs the
    # manual start.sh log) — read whichever is authoritative, and report it
    log = _log_source()
    text = _tail(log["path"], 1024 * 1024)
    rows = text.splitlines()
    if grep:
        pat = re.compile(grep, re.IGNORECASE)
        rows = [r for r in rows if pat.search(r)]
    return {"lines": rows[-max(1, min(lines, 2000)):], "total": len(rows),
            "path": log["path"], "source": log["source"]}


def config_payload() -> dict:
    cfg = {}
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                cfg = load_yaml(f.read()) or {}
        except Exception:
            cfg = {}
    editable = {}
    with open(CONFIG_PATH) as f:
        text = f.read()
    for path, rule in EDITABLE.items():
        val, ok = yaml_edit.get_value(text, path)
        editable[path] = {**rule, "value": val if ok else None, "present": ok}
    return {"config": cfg, "editable": editable,
            "note": "The daemon reads config.yaml at startup — applied edits "
                    "need a daemon restart to take effect."}


def apply_config_edits(edits: dict) -> tuple[int, dict]:
    if not isinstance(edits, dict) or not edits:
        return 400, {"error": "body must be {edits: {path: value}}"}
    if not os.path.isfile(CONFIG_PATH):
        return 500, {"error": f"config.yaml not found at {CONFIG_PATH}"}
    with open(CONFIG_PATH) as f:
        text = f.read()
    applied, rejected = [], []
    for path, value in edits.items():
        rule = EDITABLE.get(path)
        if rule is None:
            rejected.append({"path": path, "reason": "not editable via console"})
            continue
        try:
            if rule["t"] == "int":
                value = int(value)
            else:
                value = float(value)
        except (TypeError, ValueError):
            rejected.append({"path": path, "reason": f"expected {rule['t']}"})
            continue
        if not (rule["min"] <= value <= rule["max"]):
            rejected.append({"path": path,
                             "reason": f"out of range [{rule['min']}, {rule['max']}]"})
            continue
        new_text = yaml_edit.set_value(text, path, value)
        if new_text is None:
            rejected.append({"path": path, "reason": "path not found in config.yaml"})
            continue
        text = new_text
        applied.append({"path": path, "value": value})
    if not applied:
        return 400, {"applied": [], "rejected": rejected}
    # round-trip guard: the edited file must still parse and carry the values
    parsed = load_yaml(text)
    for a in applied:
        node = parsed
        for part in a["path"].split("."):
            node = (node or {}).get(part)
        if node != a["value"]:
            return 500, {"error": f"round-trip check failed for {a['path']}"}
    backup = CONFIG_PATH + ".bak"
    try:
        shutil.copy2(CONFIG_PATH, backup)
    except Exception:
        pass
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, CONFIG_PATH)
    return 200, {"applied": applied, "rejected": rejected,
                 "backup": backup, "restart_required": True}


# ── LLM provider sidecar (set / choose / validate) ─────────────────────

def _llm_sidecar() -> dict:
    """Parse state/llm.env into {KEY: value}; {} when absent/unparseable.

    Values (incl. keys) are read here but only ever used internally — the
    API surfaces presence booleans and models, never a key's value.
    """
    if not os.path.isfile(LLM_ENV_PATH):
        return {}
    out = {}
    try:
        with open(LLM_ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.removeprefix("export ").partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key:
                    out[key] = val
    except OSError:
        pass
    return out


def _llm_env_value(key: str, default: str) -> str:
    """Sidecar first, live env fallback, then the module default."""
    side = _llm_sidecar()
    if key in side:
        return side[key]
    if os.environ.get(key):
        return os.environ[key]
    return default


def llm_payload() -> dict:
    side = _llm_sidecar()
    # Models: sidecar wins, then live env, then provider.py defaults.
    model_defaults = {
        "cf": os.environ.get("CF_MODEL", "@cf/zai-org/glm-5.3"),
        "nvidia": os.environ.get("NVIDIA_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b"),
        "openrouter": os.environ.get("OPENROUTER_MODEL",
                                     "nvidia/nemotron-3.5-lightning:free"),
        "mistral": os.environ.get("MISTRAL_MODEL", "mistral-large-latest"),
    }
    key_env = {
        "cf": ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_KEY", "CLOUDFLARE_AI_TOKEN"),
        "nvidia": ("NVIDIA_API_KEY",),
        "openrouter": ("OPENROUTER_API_KEY",),
        "mistral": ("MISTRAL_API_KEY",),
    }
    chain = side.get("GRID_LLM_CHAIN") or os.environ.get(
        "GRID_LLM_CHAIN", "cf,nvidia,openrouter,mistral")
    chain_list = [p.strip() for p in chain.split(",") if p.strip()]

    def _present(name):
        for key in key_env[name]:
            if side.get(key) or os.environ.get(key):
                return True
        return False

    providers = {}
    for name in LLM_PROVIDERS:
        model_var = {"cf": "CF_MODEL", "nvidia": "NVIDIA_MODEL",
                     "openrouter": "OPENROUTER_MODEL", "mistral": "MISTRAL_MODEL"}[name]
        providers[name] = {
            "key_present": _present(name),
            "model": side.get(model_var) or model_defaults[name],
            "model_env": model_var,
            "chain_position": (chain_list.index(name)
                               if name in chain_list else None),
            "enabled": name in chain_list,
        }
    roles = {}
    raw_roles = side.get("GRID_LLM_ROLES")
    if raw_roles:
        try:
            parsed = json.loads(raw_roles)
            if isinstance(parsed, dict):
                roles = parsed
        except Exception:
            roles = {}
    return {
        "providers": providers,
        "chain": chain_list,
        "roles": roles,
        "role_keys": LLM_ROLE_KEYS,
        "llm_env": {"cf": _present("cf"), "nvidia": _present("nvidia"),
                    "openrouter": _present("openrouter"),
                    "mistral": _present("mistral")},
        "sidecar": os.path.isfile(LLM_ENV_PATH),
        "note": "Keys are stored in state/llm.env (0600) and never returned. "
                "Models/chain/roles are read by the daemon at the next LLM "
                "call via self-heal — no full restart required.",
    }


def apply_llm(updates: dict) -> tuple[int, dict]:
    """Persist provider models, chain order, keys, and role routing to the sidecar.

    Body: {"providers": {name: {model?, key?}}, "chain": [..], "roles": {..}}.
    A `key` value of "" or "__KEEP__" leaves the stored key untouched; any
    other non-empty string replaces it. Models/chain/roles are written verbatim
    (model strings are free-form; chain entries must be known providers).
    """
    side = _llm_sidecar()

    providers = updates.get("providers") or {}
    if not isinstance(providers, dict):
        return 400, {"error": "providers must be an object"}
    chain = updates.get("chain")
    roles = updates.get("roles")

    # Merge models + keys.
    for name, spec in providers.items():
        if name not in LLM_PROVIDERS or not isinstance(spec, dict):
            continue
        model_var = {"cf": "CF_MODEL", "nvidia": "NVIDIA_MODEL",
                     "openrouter": "OPENROUTER_MODEL", "mistral": "MISTRAL_MODEL"}[name]
        if "model" in spec:
            model = str(spec["model"]).strip()
            if model:
                side[model_var] = model
        if "key" in spec:
            key_val = str(spec["key"])
            key_var = {"cf": "CLOUDFLARE_API_KEY", "nvidia": "NVIDIA_API_KEY",
                       "openrouter": "OPENROUTER_API_KEY",
                       "mistral": "MISTRAL_API_KEY"}[name]
            if key_val == "__CLEAR__":
                # explicit delete: drop the key from the sidecar so the
                # provider falls out of the chain on the next self-heal.
                side.pop(key_var, None)
            elif key_val and key_val != "__KEEP__":
                side[key_var] = key_val
            # "" / "__KEEP__" → leave existing key (or none) untouched.

    # Chain order: validate names, dedupe, append any omitted enabled providers.
    if chain is not None:
        if not isinstance(chain, list):
            return 400, {"error": "chain must be a list"}
        clean = []
        for p in chain:
            if p in LLM_PROVIDERS and p not in clean:
                clean.append(p)
        for p in LLM_PROVIDERS:
            if p not in clean:
                clean.append(p)
        side["GRID_LLM_CHAIN"] = ",".join(clean)

    # Roles: validate keys + provider values; drop unknown.
    if roles is not None:
        if not isinstance(roles, dict):
            return 400, {"error": "roles must be an object"}
        clean_roles = {}
        for role, provider in roles.items():
            if role in LLM_ROLE_KEYS and provider in LLM_PROVIDERS:
                clean_roles[role] = provider
        side["GRID_LLM_ROLES"] = json.dumps(clean_roles)

    # Serialize back to "export K=\"v\"" lines, atomic + backup + 0600.
    lines = []
    for key, val in side.items():
        lines.append(f'export {key}="{val}"')
    text = "\n".join(lines) + "\n"

    try:
        os.makedirs(os.path.dirname(LLM_ENV_PATH), exist_ok=True)
        if os.path.isfile(LLM_ENV_PATH):
            shutil.copy2(LLM_ENV_PATH, LLM_ENV_PATH + ".bak")
    except OSError:
        pass
    tmp = LLM_ENV_PATH + ".tmp"
    try:
        with open(tmp, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, LLM_ENV_PATH)
    except OSError as exc:
        return 500, {"error": f"write failed: {exc}"}

    return 200, {"applied": True, "providers": list(providers),
                 "chain": side.get("GRID_LLM_CHAIN", "").split(","),
                 "roles": json.loads(side.get("GRID_LLM_ROLES", "{}")),
                 "note": "Saved to state/llm.env. The daemon loads it at the "
                         "next LLM call (self-heal) — no restart required."}


def validate_llm() -> tuple[int, dict]:
    """Live ping each provider via `llm/provider.py --ping --json`, sourcing
    the sidecar into the child env so validation matches runtime exactly.
    Never returns key values — only ok/latency/error.
    """
    provider_script = os.path.join(GRID_HOME, "llm", "provider.py")
    if not os.path.isfile(provider_script):
        return 500, {"error": "llm/provider.py not found"}
    env = dict(os.environ)
    for key, val in _llm_sidecar().items():
        env[key] = val
    try:
        proc = subprocess.run(
            [sys.executable, provider_script, "--ping", "--json"],
            capture_output=True, text=True, timeout=90, env=env,
            cwd=GRID_HOME)
    except subprocess.TimeoutExpired:
        return 504, {"error": "ping timed out after 90s"}
    except Exception as exc:
        return 500, {"error": f"ping failed: {exc}"}
    if proc.returncode != 0:
        return 502, {"error": "provider.py --ping exited "
                              f"{proc.returncode}: {(proc.stderr or '')[:200]}"}
    try:
        data = json.loads(proc.stdout)
    except Exception:
        return 500, {"error": "unparseable ping output"}
    results = data.get("results") or []
    # Strip any key material defensively — ping() already emits none, but the
    # "error" field can echo a URL/response; truncate to be safe.
    safe = []
    for r in results:
        safe.append({
            "provider": r.get("provider"),
            "ok": bool(r.get("ok")),
            "latency_ms": r.get("latency_ms"),
            "error": str(r.get("error", ""))[:160] if not r.get("ok") else None,
        })
    return 200, {"chain": data.get("chain", [p for p, _ in
                  [(r.get("provider"), r) for r in safe]]),
                 "results": safe}


def overview_payload() -> dict:
    st = _load_state()
    ok_status, ctl_status = _ctl_cached("/status")
    daemon = daemon_info()
    pb_ok, _pb_body = _http_json(f"{PB_URL}/api/health", 1.5)
    committed = st.get("committed") or {}
    total_committed = sum(v for v in committed.values()
                          if isinstance(v, (int, float)))
    cfg = config_payload()["config"]
    portfolio = cfg.get("portfolio") or {}
    # Last arbiter verdict from the optimizer — surfaced so the Fleet rail
    # can show "Mistral said: keep NEAR (conf 0.78)" without waiting for the
    # next /optimizer poll. Always fail-soft (None when the loop hasn't run).
    opt = st.get("optimizer") or {}
    last_report = opt.get("last_report") or {}
    last_arbiter_verdict = (last_report.get("arbiter")
                            if isinstance(last_report, dict) else None)
    last_arbiter_at = (last_report.get("at")
                       if isinstance(last_report, dict) else None)
    return {
        "at": utcnow(),
        "daemon": daemon,
        "ctl": {"reachable": ok_status, "status": ctl_status if ok_status else None},
        "bots": _enriched_bots(st),
        "slots": st.get("slots") or [],
        "committed_usd": round(total_committed, 2),
        "journal_tail": (st.get("journal") or [])[-40:],
        "live_allow": st.get("live_allow"),
        "reliability": reliability_payload(),
        "screen": screen_payload(),
        "pocketbase": {"up": pb_ok},
        "readiness": _readiness(ctl_status),
        # the screen cache age comes from state["screen_cache"]["at"]
        # (epoch float written by rescreen_cycle at daemon.py:2327-2330),
        # NOT from state["optimizer"] which the original line read — the
        # old key was never written, so this always returned None.
        "screen_cache_age_s": (round(time.time() - float(
            st.get("screen_cache", {}).get("at", 0)), 1)
            if isinstance((st.get("screen_cache") or {}).get("at"),
                          (int, float)) else None),
        "last_arbiter_verdict": last_arbiter_verdict,
        "last_arbiter_at": last_arbiter_at,
        "config_digest": {
            "total_usd": (portfolio.get("total_usd")),
            "slots_default": portfolio.get("slots_default"),
            "rescreen_minutes": (cfg.get("screen") or {}).get("rescreen_minutes"),
            "watch_interval_s": (cfg.get("watch") or {}).get("interval_s"),
            "take_profit_pct": (cfg.get("grid_defaults") or {}).get("take_profit_pct"),
        },
    }


def _readiness(ctl_status: dict | None) -> dict:
    """Derived dependency-readiness + capacity facts for the console.

    Surfaces the daemon's own `/status` diagnostics — LLM-provider env,
    browser CDP, PocketBase, venue capacity, connected profiles, bot-type
    limits — in one flat, safety-conscious shape the frontend can render
    without re-deriving. Returns an empty dict when the ctl plane is down.
    """
    if not isinstance(ctl_status, dict):
        return {}
    env = ctl_status.get("env") or {}
    llm_env = env.get("llm_env") or {}
    capacity = ctl_status.get("capacity") or {}
    max_active = capacity.get("max_active") or {}
    active = capacity.get("active") or {}

    # Actual enforced caps (per exchange tier) vs the dashboard's own
    # `account_limits.gridBots`. `active.other` is the non-premium count;
    # premium exchanges are keyed by name under `active.premium`.
    other_max = _num(max_active.get("other"))
    other_active = _num(active.get("other"))
    premium_max = _num(max_active.get("premium"))
    premium_active = 0
    if isinstance(active.get("premium"), dict):
        premium_active = sum(_num(v) for v in active["premium"].values())

    # One real-money (paperTrading=False) profile in the mix is the single
    # safety fact worth surfacing in red — the daemon must never route a
    # paper decision to a live account.
    profiles = ctl_status.get("profiles") or []
    real_profiles = [p for p in profiles if isinstance(p, dict)
                     and p.get("paperTrading") is False]

    return {
        "reachable": True,
        "llm_env": {k: bool(v) for k, v in llm_env.items()} if isinstance(llm_env, dict) else {},
        "browser_cdp": bool(env.get("browser_cdp")),
        "pb_env": bool(env.get("pb_env")),
        "capabilities": ctl_status.get("capabilities") or {},
        "capacity": {
            "other": {"active": other_active, "max": other_max},
            "premium": {"active": premium_active, "max": premium_max},
        },
        "profiles": profiles,
        "real_profiles": real_profiles,
        "account_limits": ctl_status.get("account_limits") or {},
    }


def _num(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ── daemon ops ─────────────────────────────────────────────────────────

def _wait_gone(pid, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.4)
    return False


def daemon_stop(force=False) -> tuple[int, dict]:
    pid = _pid()
    if pid is None and os.path.exists(KILL_FILE):
        return 200, {"stopped": True, "note": "daemon not running; KILL file present"}
    if pid is None:
        return 409, {"error": "daemon not running"}
    _ctl("/kill")  # preferred path; falls back to writing the file directly
    if not os.path.exists(KILL_FILE):
        try:
            open(KILL_FILE, "w").write(utcnow())
        except Exception:
            pass
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    gone = _wait_gone(pid)
    if not gone and force:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        gone = _wait_gone(pid, 3.0)
    return 200, {"stopped": gone, "pid": pid, "kill_present": True,
                 "killed_forcefully": bool(force and gone)}


def daemon_start(live_paper=False, clear_kill=False) -> tuple[int, dict]:
    if os.path.exists(KILL_FILE):
        if not clear_kill:
            return 409, {"error": "KILL file present — pass clear_kill to remove it",
                         "kill_present": True}
        try:
            os.remove(KILL_FILE)
        except OSError as exc:
            return 500, {"error": f"could not remove KILL: {exc}"}
    if _pid() is not None:
        return 409, {"error": "daemon already running", "running": True}
    script = os.path.join(GRID_HOME, "scripts", "start.sh")
    if not os.path.isfile(script):
        return 500, {"error": f"start script not found: {script}"}
    cmd = ["bash", script] + (["--live-paper"] if live_paper else [])
    log = open(os.path.join(STATE_DIR, "daemon.log"), "ab")
    try:
        subprocess.Popen(cmd, cwd=GRID_HOME, stdout=log, stderr=log,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception as exc:
        return 500, {"error": f"spawn failed: {exc}"}
    deadline = time.time() + 15
    while time.time() < deadline:
        time.sleep(1.0)
        if _pid() is not None:
            return 200, {"started": True, "pid": _pid(),
                         "mode": "live-paper" if live_paper else "dry-run"}
    return 504, {"error": "start script ran but the daemon did not come up "
                          "within 15s (check state/daemon.log — e.g. missing "
                          "CLOUDFLARE_* keys from `dsh web`)"}


def daemon_restart(clear_kill=False, live_paper=None) -> tuple[int, dict]:
    if os.path.exists(KILL_FILE) and not clear_kill:
        return 409, {"error": "KILL file present — pass clear_kill to remove it",
                     "kill_present": True}
    if os.path.exists(KILL_FILE):
        try:
            os.remove(KILL_FILE)
        except OSError as exc:
            return 500, {"error": f"could not remove KILL: {exc}"}
    if _launchd_managed():
        try:
            subprocess.run(["launchctl", "kickstart", "-k",
                            f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
                           capture_output=True, timeout=10)
        except Exception as exc:
            return 500, {"error": f"launchctl kickstart failed: {exc}"}
        # launchd ThrottleInterval is 30s on crash; a clean kickstart is faster
        deadline = time.time() + 40
        _wait_gone(_pid() or -1, 0.1)
        while time.time() < deadline:
            time.sleep(1.0)
            pid = _pid()
            if pid is not None:
                return 200, {"restarted": True, "pid": pid, "supervisor": "launchd",
                             "mode": "live-paper",
                             "note": "launchd supervisor always starts --live-paper"}
        return 504, {"error": "kickstart issued but daemon not up after 40s"}
    # Manual/supervised restart: honor requested mode, or preserve the current
    # mode when the caller does not specify one. This lets the console switch
    # a running dry-run daemon to live-paper (or vice versa) with one restart.
    current_mode = None
    pid = _pid()
    if pid is not None:
        current_mode = _mode(pid)
    if pid is None:
        # Already stopped (e.g. an operator stop just armed KILL): there is
        # nothing to stop — a restart degrades to a start. Fall back to the
        # supervisor's configured posture (GRID_MODE) so a live-paper
        # container never silently downgrades to dry-run.
        if live_paper is None:
            env_mode = os.environ.get("GRID_MODE")
            live_paper = (env_mode == "live-paper" if env_mode else False)
        return daemon_start(live_paper=live_paper, clear_kill=True)
    code, body = daemon_stop()
    if code != 200:
        return code, body
    time.sleep(1.0)
    if live_paper is None:
        live_paper = current_mode == "live-paper"
    # daemon_stop() deliberately arms the KILL file (that is what keeps a
    # supervisor from racing the restart); starting again right after means
    # clearing exactly that marker — otherwise the manual path (the VPS
    # container, where launchd is absent) 409s on its own stop every time.
    return daemon_start(live_paper=live_paper, clear_kill=True)


# ── HTTP handler ───────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "grid-console/1.0"
    protocol_version = "HTTP/1.1"

    # -- plumbing --
    def _json(self, code, obj):
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True  # non-browser client (curl, tests)
        host = self.headers.get("Host", "")
        try:
            from urllib.parse import urlparse
            o = urlparse(origin)
            return o.netloc == host or o.hostname in ("127.0.0.1", "localhost")
        except Exception:
            return False

    def log_message(self, *a):
        pass

    # -- static --
    def _static(self, path):
        if path == "/":
            path = "/index.html"
        rel = os.path.normpath(path.lstrip("/"))
        if rel.startswith("..") or os.path.isabs(rel):
            self._json(404, {"error": "not found"})
            return
        full = os.path.join(STATIC_DIR, rel)
        if not os.path.isfile(full):
            self._json(404, {"error": "not found"})
            return
        ext = os.path.splitext(full)[1]
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # -- routing --
    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        q = parse_qs(u.query)
        route = u.path

        def q1(name, default):
            return (q.get(name) or [default])[0]

        if route == "/" or not route.startswith("/api/"):
            self._static(route)
        elif route == "/api/overview":
            self._json(200, overview_payload())
        elif route == "/api/daemon":
            self._json(200, daemon_info())
        elif route == "/api/state":
            self._json(200, _load_state())
        elif route == "/api/journal":
            st = _load_state()
            limit = int(q1("limit", 80))
            self._json(200, {"journal": (st.get("journal") or [])[-limit:]})
        elif route == "/api/decisions":
            self._json(200, {"decisions":
                             decisions_payload(int(q1("limit", 100)))})
        elif route.startswith("/api/decisions/"):
            did = os.path.basename(route[len("/api/decisions/"):])
            payload = decision_payload_by_id(did)
            if payload is None:
                self._json(404, {"error": f"no decision {did}"})
            else:
                self._json(200, payload)
        elif route == "/api/reliability":
            self._json(200, reliability_payload())
        elif route == "/api/reliability/archive":
            self._json(200, reliability_archive_payload(
                int(q1("limit", 20))))
        elif route == "/api/recommendations":
            self._json(200, recommendations_payload(
                int(q1("limit", 100))))
        elif route == "/api/screen":
            self._json(200, {"screen": screen_payload()})
        elif route == "/api/optimizer":
            ok, body = _ctl("/optimizer")
            # fail-soft: degrade with a 200 + {"optimizer": null, ...} so
            # the UI keeps the last-known panel when the daemon is down
            self._json(200, body if ok else
                       {"optimizer": None, "error": "ctl unreachable",
                        "detail": body})
        elif route == "/api/optimizer/swap-log":
            # swap_log + per-slot idle trackers + last arbiter verdict
            # from state.json's optimizer block (the daemon already keeps
            # the live copy — no extra ctl call needed)
            self._json(200, optimizer_swap_log())
        elif route == "/api/llm/health":
            # live provider ping + role routing matrix; 60s in-process cache
            self._json(200, llm_health())
        elif route == "/api/observe":
            ok, body = _ctl_cached("/observe")
            # fail-soft: degrade with a 200 + {"error": ...} so the UI can
            # keep rendering last-persisted state when the daemon is down
            self._json(200, body if ok else
                       {"error": "ctl unreachable", "detail": body})
        elif route == "/api/status":
            ok, body = _ctl_cached("/status")
            self._json(200, body if ok else
                       {"error": "ctl unreachable", "detail": body})
        elif route == "/api/pnl":
            self._json(200, pnl_payload())
        elif route == "/api/chart":
            code, payload = _chart_bars(q1("venue", ""), q1("symbol", ""),
                                        q1("interval", "1h"),
                                        q1("bars", "96"))
            self._json(code, payload)
        elif route == "/api/position-sweeps":
            self._json(200, {"sweeps": position_sweeps_payload(
                int(q1("limit", 25)))})
        elif route == "/api/reports":
            self._json(200, {"reports": reports_index()})
        elif route.startswith("/api/reports/"):
            stem = os.path.basename(route[len("/api/reports/"):])
            rdir = os.path.join(STATE_DIR, "reports")
            jpath, mpath = os.path.join(rdir, stem + ".json"), \
                os.path.join(rdir, stem + ".md")
            if not (os.path.isfile(jpath) or os.path.isfile(mpath)):
                self._json(404, {"error": "no such run card"})
                return
            md = None
            if os.path.isfile(mpath):
                with open(mpath, encoding="utf-8", errors="replace") as f:
                    md = f.read()
            self._json(200, {"stem": stem, "json": _read_json(jpath), "md": md})
        elif route == "/api/logs":
            self._json(200, logs_payload(int(q1("lines", 300)), q1("grep", None)))
        elif route == "/api/config":
            self._json(200, config_payload())
        elif route == "/api/llm":
            self._json(200, llm_payload())
        elif route == "/api/meta":
            self._json(200, {
                "console_port": CONSOLE_PORT, "ctl_port": _ctl_port(),
                "pocketbase": PB_URL, "state_dir": STATE_DIR,
                "grid_home": GRID_HOME, "launchd_label": LAUNCHD_LABEL,
                "pid": os.getpid(), "started": getattr(SERVER, "started", None),
                "wt_account": WT_ACCOUNT_LABEL,
            })
        else:
            self._json(404, {"error": "unknown path"})

    def do_POST(self):
        from urllib.parse import urlparse
        route = urlparse(self.path).path
        if not self._same_origin():
            self._json(403, {"error": "cross-origin refused"})
            return
        if not route.startswith("/api/"):
            self._json(404, {"error": "unknown path"})
            return
        body = self._body()
        confirmed = bool(body.get("confirm"))

        if route == "/api/ctl/rescreen":
            ok, resp = _ctl("/rescreen", "POST")
            self._json(200 if ok else 502, resp if ok else
                       {"error": _ctl_err(resp), "detail": resp})
        elif route == "/api/ctl/optimize":
            ok, resp = _ctl("/optimize", "POST")
            self._json(200 if ok else 502, resp if ok else
                       {"error": _ctl_err(resp), "detail": resp})
        elif route == "/api/ctl/reliability":
            ok, resp = _ctl("/reliability", "POST")
            self._json(200 if ok else 502, resp if ok else
                       {"error": _ctl_err(resp), "detail": resp})
        elif route == "/api/ctl/rotate":
            slot = body.get("slot")
            if slot is None:
                self._json(400, {"error": "missing slot"})
                return
            ok, resp = _ctl("/rotate", "POST", {"slot": slot})
            self._json(200 if ok else 502, resp if ok else
                       {"error": _ctl_err(resp), "detail": resp})
        elif route == "/api/ctl/kill":
            if not confirmed:
                self._json(400, {"error": 'pass {"confirm": true}'})
                return
            ok, resp = _ctl("/kill", "POST")
            if not ok:
                try:
                    open(KILL_FILE, "w").write(utcnow())
                    resp = {"killed": True, "via": "direct"}
                except Exception as exc:
                    self._json(502, {"error": str(exc)})
                    return
            self._json(200, resp)
        elif route == "/api/ctl/unkill":
            if not confirmed:
                self._json(400, {"error": 'pass {"confirm": true}'})
                return
            if os.path.exists(KILL_FILE):
                try:
                    os.remove(KILL_FILE)
                except OSError as exc:
                    self._json(500, {"error": str(exc)})
                    return
            self._json(200, {"kill_file": False})
        elif route == "/api/config":
            code, resp = apply_config_edits(body.get("edits"))
            self._json(code, resp)
        elif route == "/api/llm":
            code, resp = apply_llm(body)
            self._json(code, resp)
        elif route == "/api/llm/validate":
            code, resp = validate_llm()
            self._json(code, resp)
        elif route == "/api/daemon/stop":
            if not confirmed:
                self._json(400, {"error": 'pass {"confirm": true}'})
                return
            self._json(*daemon_stop(force=bool(body.get("force"))))
        elif route == "/api/daemon/start":
            if not confirmed:
                self._json(400, {"error": 'pass {"confirm": true}'})
                return
            self._json(*daemon_start(live_paper=bool(body.get("live_paper")),
                                     clear_kill=bool(body.get("clear_kill"))))
        elif route == "/api/daemon/restart":
            if not confirmed:
                self._json(400, {"error": 'pass {"confirm": true}'})
                return
            # live_paper: True/False forces the mode; null/omitted preserves
            # the current mode on manual restarts (launchd always uses
            # --live-paper regardless of this flag).
            self._json(*daemon_restart(
                clear_kill=bool(body.get("clear_kill")),
                live_paper=body.get("live_paper")))
        elif route in ("/api/dev/reset", "/api/dev/reset-wt", "/api/dev/clean"):
            if not confirmed:
                self._json(400, {"error": 'pass {"confirm": true}'})
                return
            self._json(*dev_action(route.rsplit("/", 1)[1], body))
        else:
            self._json(404, {"error": "unknown path"})


# ── dev-script actions ─────────────────────────────────────────────────

DEV_SCRIPT = os.path.join(GRID_HOME, "dev")
DEV_LOG = os.path.join(STATE_DIR, "logs", "dev.log")


def dev_action(action: str, body: dict) -> tuple[int, dict]:
    """Run a `dev <action>` through the single dev script, detached.

    reset/reset-wt stop the daemon AND this console (the dev script
    supervises the whole stack), so the command must run detached —
    its output lands in state/logs/dev.log and the frontend reloads
    once the console is back. `clean` does not stop the console, but
    uses the same path for uniformity.
    """
    if not os.path.isfile(DEV_SCRIPT):
        return 500, {"error": f"dev script not found: {DEV_SCRIPT}"}
    args = [DEV_SCRIPT, action, "--yes"]
    if action == "reset":
        if body.get("keep_decisions"):
            args.append("--keep-decisions")
        if body.get("start"):
            args.append("--start")
        # the WT reset is explicit, never defaulted: a local-only reset
        # leaves the paper bots running (they get re-adopted or block
        # redeploys), a --wt reset deletes them outright
        args.append("--wt" if body.get("wt") else "--no-wt")
    os.makedirs(os.path.dirname(DEV_LOG), exist_ok=True)
    try:
        with open(DEV_LOG, "ab") as log:
            subprocess.Popen(args, cwd=GRID_HOME, stdout=log, stderr=log,
                             stdin=subprocess.DEVNULL,
                             start_new_session=True)
    except Exception as exc:  # noqa: BLE001
        return 500, {"error": f"spawn failed: {exc}"}
    return 200, {"started": True, "action": action, "args": args,
                 "log": "state/logs/dev.log"}


SERVER = None


def main():
    global SERVER
    # Graceful SIGTERM: exit 0 so a supervisor (launchd KeepAlive with
    # SuccessfulExit=false) does not treat a stop as a crash and restart it.
    import signal
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    # Bind host is env-overridable for containers (docker -p needs 0.0.0.0
    # inside the container; the local default stays loopback-only).
    _bind_host = os.environ.get("GRID_BIND_HOST", "127.0.0.1")
    srv = ThreadingHTTPServer((_bind_host, CONSOLE_PORT), Handler)
    SERVER = srv
    srv.started = utcnow()
    print(f"grid-autonomy console on http://{_bind_host}:{CONSOLE_PORT} "
          f"(ctl :{_ctl_port()}, state {STATE_DIR})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
