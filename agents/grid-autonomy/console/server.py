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
    GET  /api/reliability     archetype ledger + sizing-tier computation
    GET  /api/screen          latest rescreen run card extract
    GET  /api/reports         run-card index
    GET  /api/reports/<stem>  one run card {json, md}
    GET  /api/logs?lines=&grep=  daemon.log tail
    GET  /api/config          parsed config.yaml + editable whitelist
    GET  /api/observe         proxy of the daemon ctl /observe
    GET  /api/meta            ports, paths, versions
    POST /api/ctl/rescreen    queue an immediate rescreen     {confirm}
    POST /api/ctl/reliability queue a reliability refresh     {confirm}
    POST /api/ctl/rotate      force-rotate a slot {slot}     {confirm}
    POST /api/ctl/kill        write the KILL file            {confirm}
    POST /api/ctl/unkill      remove the KILL file           {confirm}
    POST /api/config          apply whitelisted edits {edits:{path:value}}
    POST /api/daemon/stop     KILL + SIGTERM (+SIGKILL w/ force) {confirm}
    POST /api/daemon/start    scripts/start.sh [--live-paper]   {confirm,
                              live_paper, clear_kill}
    POST /api/daemon/restart  launchd kickstart or stop+start   {confirm,
                              clear_kill}
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
    "watch.interval_s": dict(t="float", min=10, max=3600, group="Cadence",
                             label="Health poll", unit="s"),
    "watch.adjust_steps_threshold": dict(t="float", min=0.5, max=10,
                                         group="Cadence",
                                         label="Re-centre drift", unit="steps"),
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
    "memory.k": dict(t="int", min=1, max=10, group="Deliberation",
                     label="Memories per candidate"),
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
        return False, {"error": str(exc)[:200]}


def _ctl(path, method="GET", body=None):
    return _http_json(f"http://127.0.0.1:{_ctl_port()}{path}", 3.0, method, body)


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


def reliability_payload() -> dict:
    ledger = _read_json(os.path.join(STATE_DIR, "reliability.json"), {}) or {}
    archs = {}
    for arch, st in ledger.items():
        if isinstance(st, dict):
            st = dict(st)
            st["tier"] = _tier(st)
            archs[arch] = st
    return {"ladder": LADDER, "archetypes": archs}


def _enriched_bots(st: dict) -> list[dict]:
    observe = st.get("last_observe") or {}
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
        out.append({
            "slot": int(slot_key) if str(slot_key).isdigit() else slot_key,
            "symbol": bot.get("symbol"), "venue": bot.get("venue"),
            "grid_type": (bot.get("ticket") or {}).get("grid_type"),
            "since": bot.get("since"), "adopted": bool(bot.get("adopted")),
            "bot_code": bot.get("bot_code"), "channel": bot.get("channel"),
            "archetype": bot.get("archetype"),
            "score_final": bot.get("score_final"),
            "decision_id": bot.get("decision_id"),
            "force_rotate": bool(bot.get("force_rotate")),
            "needs_reanalysis": bool(bot.get("needs_reanalysis")),
            "committed": (st.get("committed") or {}).get(str(slot_key)),
            "stagnation_policy": pol,
            "observed": obs,
            "stagnant": stagnant,
        })
    out.sort(key=lambda b: (not str(b["slot"]).isdigit(), str(b["slot"])))
    return out


def _latest_report(kind: str):
    rdir = os.path.join(STATE_DIR, "reports")
    try:
        names = sorted((n for n in os.listdir(rdir)
                        if n.endswith(".json") and f"-{kind}." in n), reverse=True)
    except Exception:
        return None
    for name in names:
        rep = _read_json(os.path.join(rdir, name))
        if rep is not None:
            return rep
    return None


def screen_payload() -> dict | None:
    rep = _latest_report("rescreen")
    if not rep:
        return None
    scr = rep.get("screen") or {}
    return {
        "at": rep.get("at"), "cycle_kind": rep.get("cycle_kind"),
        "n_candidates": scr.get("n_candidates"),
        "top": scr.get("top3") or [],
        "dry_run": rep.get("dry_run"),
    }


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
        "nvidia": os.environ.get("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct"),
        "openrouter": os.environ.get("OPENROUTER_MODEL",
                                     "arcee-ai/trinity-large-preview:free"),
    }
    key_env = {
        "cf": ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_KEY", "CLOUDFLARE_AI_TOKEN"),
        "nvidia": ("NVIDIA_API_KEY",),
        "openrouter": ("OPENROUTER_API_KEY",),
    }
    chain = side.get("GRID_LLM_CHAIN") or os.environ.get(
        "GRID_LLM_CHAIN", "cf,nvidia,openrouter")
    chain_list = [p.strip() for p in chain.split(",") if p.strip()]

    def _present(name):
        for key in key_env[name]:
            if side.get(key) or os.environ.get(key):
                return True
        return False

    providers = {}
    for name in LLM_PROVIDERS:
        model_var = {"cf": "CF_MODEL", "nvidia": "NVIDIA_MODEL",
                     "openrouter": "OPENROUTER_MODEL"}[name]
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
                    "openrouter": _present("openrouter")},
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
                     "openrouter": "OPENROUTER_MODEL"}[name]
        if "model" in spec:
            model = str(spec["model"]).strip()
            if model:
                side[model_var] = model
        if "key" in spec:
            key_val = str(spec["key"])
            key_var = {"cf": "CLOUDFLARE_API_KEY", "nvidia": "NVIDIA_API_KEY",
                       "openrouter": "OPENROUTER_API_KEY"}[name]
            if key_val and key_val != "__KEEP__":
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
    ok_status, ctl_status = _ctl("/status")
    daemon = daemon_info()
    pb_ok, _pb_body = _http_json(f"{PB_URL}/api/health", 1.5)
    committed = st.get("committed") or {}
    total_committed = sum(v for v in committed.values()
                          if isinstance(v, (int, float)))
    cfg = config_payload()["config"]
    portfolio = cfg.get("portfolio") or {}
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
        "config_digest": {
            "total_usd": (portfolio.get("total_usd")),
            "slots_default": portfolio.get("slots_default"),
            "rescreen_minutes": (cfg.get("screen") or {}).get("rescreen_minutes"),
            "watch_interval_s": (cfg.get("watch") or {}).get("interval_s"),
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


def daemon_restart(clear_kill=False) -> tuple[int, dict]:
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
                return 200, {"restarted": True, "pid": pid, "supervisor": "launchd"}
        return 504, {"error": "kickstart issued but daemon not up after 40s"}
    code, body = daemon_stop()
    if code != 200:
        return code, body
    time.sleep(1.0)
    return daemon_start(live_paper=True)


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
        elif route == "/api/reliability":
            self._json(200, reliability_payload())
        elif route == "/api/screen":
            self._json(200, {"screen": screen_payload()})
        elif route == "/api/observe":
            ok, body = _ctl("/observe")
            self._json(200 if ok else 502, body if ok else
                       {"error": "ctl unreachable", "detail": body})
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
                       {"error": "ctl unreachable", "detail": resp})
        elif route == "/api/ctl/reliability":
            ok, resp = _ctl("/reliability", "POST")
            self._json(200 if ok else 502, resp if ok else
                       {"error": "ctl unreachable", "detail": resp})
        elif route == "/api/ctl/rotate":
            slot = body.get("slot")
            if slot is None:
                self._json(400, {"error": "missing slot"})
                return
            ok, resp = _ctl("/rotate", "POST", {"slot": slot})
            self._json(200 if ok else (502 if not ok else 502), resp if ok else
                       {"error": "ctl unreachable or slot unknown",
                        "detail": resp})
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
            self._json(*daemon_restart(clear_kill=bool(body.get("clear_kill"))))
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
    srv = ThreadingHTTPServer(("127.0.0.1", CONSOLE_PORT), Handler)
    SERVER = srv
    srv.started = utcnow()
    print(f"grid-autonomy console on http://127.0.0.1:{CONSOLE_PORT} "
          f"(ctl :{_ctl_port()}, state {STATE_DIR})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
