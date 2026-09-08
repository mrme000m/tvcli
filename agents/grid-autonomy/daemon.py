#!/usr/bin/env python3
"""Grid-fleet daemon — autonomous scan → deliberate → guard → deploy → watch → rotate.

Schedules (from ../config.yaml, merged over built-in defaults):
  health/positions poll   60s        (KILL check, observe_all, stagnation eval,
                                      out-of-channel re-analysis, in-place adjust)
  optimizer               2–5m       (idle-slot detection → fast challenger
                                      hunt (tvcli 15m structure) → Mistral
                                      arbiter → swap via execute_rotation)
  rescreen                10m        (merge.py → swarm → guardrails → deploy;
                                      also refreshes state.screen_cache for
                                      the optimizer's challenger board)
  reliability cron        24h        (bot_trades → archetype_stats → save
                                    → reload → sizing/kill gates)
  heartbeat              15m        (8 fail-soft loop-health checks → score
                                    in state.heartbeat + journal; stale
                                    screen/optimizer feeds self-nudge)

Autonomy with guardrails: deployments are paper (demo-hype) until the
reliability gate passes (>=30 samples, PF>=1.3); live needs live_allow=true in
state.json (set only by an explicit operator action) AND guardrails.deploy().
--dry-run (default) plans everything without creating anything.
--once runs a single rescreen cycle plus one health pass then exits.
Missing allowlisted paper profiles (autonomy.paper_profiles) are self-healed:
created at boot in live-paper mode via wtclient's idempotent ensure, retried
on the rescreen/health profile refresh under autonomy.profile_bootstrap_cooldown_s.

State: state/state.json {slots, active_bots, cooldowns_until, reliability,
live_allow, committed, journal}. HTTP ctl (thread): GET /health /status
/reliability /observe, POST /rotate {"slot": n} /rescreen /kill.

Stdlib only (http.server, subprocess, json). Subprocess drivers call the
sibling modules in-process where possible (import) and via CLI where the
upstream scripts demand it (merge.py). Worker A/C modules (resolve, observe,
reliability_grid, reflect, spreads) are imported DEFENSIVELY so the daemon
runs today and picks the real modules up when they land.
"""
import argparse
import copy
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "llm"))
sys.path.insert(0, os.path.join(HERE, "screen"))
sys.path.insert(0, os.path.join(HERE, "policy"))
sys.path.insert(0, os.path.join(HERE, "agents"))
sys.path.insert(0, os.path.join(HERE, "execution"))
sys.path.insert(0, os.path.join(HERE, "watch"))
WUN_SCRIPTS = os.path.normpath(os.path.join(
    HERE, "..", "..", ".agents", "skills", "wundertrading", "scripts"))
sys.path.insert(0, WUN_SCRIPTS)

from stagnation import (  # noqa: E402
    is_stagnant, slot_plan, derive_policy,
    CARRY_AFTER_K, CARRY_MIN_H, CARRY_MAX_H,
    CARRY_BREAK_EVEN_BUFFER_USD)
from swarm import deliberate  # noqa: E402
from guardrails import deploy as guard_deploy  # noqa: E402
import grid_adapter  # noqa: E402
from spec import build_spec  # noqa: E402
from config_lite import deep_merge, load_yaml  # noqa: E402
from ctl_http import serve_ctl  # noqa: E402

# ── Worker A defensive imports ────────────────────────────────────────
try:
    from resolve import resolve_pair, market_map, pair_meta  # noqa: F401
    HAS_RESOLVE = True
except Exception:
    HAS_RESOLVE = False

    def resolve_pair(venue, symbol, market=None):
        return None

    def pair_meta(venue, symbol, market=None):
        return {}
    market_map = {}

try:
    from observe import (account_limits, observe_all,  # noqa: F401
                         grid_capacity, grid_profiles, grid_status)
    HAS_OBSERVE = True
except Exception:
    HAS_OBSERVE = False

    def observe_all(active_bots):
        return {}

    def account_limits():
        return {}

    def grid_capacity():
        return {}

    def grid_profiles():
        return []

    def grid_status():
        return []

try:
    from reliability_grid import (archetype_stats, bot_trades,  # noqa: F401
                                  archive_trades, archived_by_archetype,
                                  ledger_key, normalize_archive,
                                  load as load_reliability,
                                  save as save_reliability)
    HAS_RELIABILITY = True
except Exception:
    HAS_RELIABILITY = False

    def archetype_stats(bots_by_archetype):
        return {}

    def bot_trades(bot_code):
        return []

    def archive_trades(trades, archetype):
        return False

    def archived_by_archetype():
        return {}

    def ledger_key(archetype):
        # fallback stub: reliability_grid absent — pass through unchanged
        return str(archetype) if archetype else "unknown"

    def normalize_archive():
        return False

    def load_reliability():
        return {}

    def save_reliability(data):
        return False

# ── Worker C defensive import ─────────────────────────────────────────
try:
    from reflect import (record_decision, record_outcome,  # noqa: F401
                         memories_for, write_run_card)
    HAS_REFLECT = True
except Exception:
    HAS_REFLECT = False

    def record_decision(ticket, brief, action, payloads):
        return None

    def record_outcome(decision_id, final):
        return None

    def memories_for(brief, k=3):
        return []

    def write_run_card(cycle_report):
        return None

# ── Slot optimizer (fast 2–5 min capital reallocation, defensive) ─────
# optimizer.py runs between rescreens: idle-slot detection, fast challenger
# hunt (tvcli 15m structure), a Mistral-pinned arbiter, and swaps through
# execute_rotation. Fail-soft import so the daemon runs without it.
try:
    from optimizer import SlotOptimizer as _SlotOptimizer  # noqa: E402
    HAS_OPTIMIZER = True
except Exception:
    HAS_OPTIMIZER = False
    _SlotOptimizer = None

# ── Position optimizer (slow-loop position revaluation, defensive) ──────
# position_optimizer.py revalues open grid positions against live candles
# and proposes ADVISORY exit-profile / channel edits (apply stays False by
# default — nothing ever auto-edits WunderTrading unless configured to).
# Fail-soft import so the daemon runs without it.
try:
    from position_optimizer import PositionOptimizer as _PositionOptimizer  # noqa: E402
    HAS_POSITION_OPTIMIZER = True
except Exception:
    HAS_POSITION_OPTIMIZER = False
    _PositionOptimizer = None

# ── Paper-profile bootstrap layer (defensive) ───────────────────────────
# execution/profiles.py turns autonomy.paper_profiles (venue-keyed allowlist)
# into a wtclient ensure call — the daemon self-heals missing paper profiles
# instead of guard-vetoing every deploy forever. Fail-soft import so the
# daemon runs even when wtclient/wt_library are missing entirely.
try:
    from profiles import (ensure_paper_profiles as _profiles_ensure,  # noqa: E402
                          paper_profile_spec as _profiles_spec)
    HAS_PROFILES = True
except Exception:
    HAS_PROFILES = False

    def _profiles_ensure(cfg, execute=False):
        return {"ok": False, "executed": False, "spec": {}, "result": None,
                "error": "execution/profiles unavailable"}

    def _profiles_spec(cfg):
        return {}

# ── wtclient exit-edit seam (defensive) ────────────────────────────────
# execution/wt_library.py wraps wtclient.GridClient.set_exits — the
# exit-only live edit of an ACTIVE grid bot (no stop/restart, verified
# live 2026-09-07). The daemon injects a thin closure of it into the
# position optimizer as its opt-in exit-apply seam; imported defensively
# so the daemon runs even when wtclient/wt_library are missing entirely.
try:
    import wt_library  # noqa: E402
    HAS_WT_LIBRARY = True
except Exception:
    HAS_WT_LIBRARY = False

# ── wtclient grid-backtest engine (defensive) ──────────────────────────
# position_optimizer's OPT-IN backtest-validation stage (cfg
# position_optimizer.backtest_validate, default off) plays exit-add recs
# through the pure client-side grid backtest engine — the same engine
# behind the configurator's Backtest button / GridClient.backtest, minus
# the :2087 network fetch (the engine supplies its own candles through
# the daemon's geo-aware fetch chain). Fail-soft import so the daemon
# boots without it; the engine then skips validation (fail-open).
try:
    from wtclient import backtest as _wt_backtest_engine  # noqa: E402
    HAS_WT_BACKTEST = True
except Exception:
    HAS_WT_BACKTEST = False
    _wt_backtest_engine = None

# Paper-profile ensure retry cadence on the health cycle (the boot attempt
# is immediate; failures here back off). Configurable via
# autonomy.profile_bootstrap_cooldown_s.
PROFILE_BOOTSTRAP_COOLDOWN_S = 1800.0

# ── PocketBase write-through side channel (defensive) ─────────────────
# PocketBase is an optional, best-effort projection: the file layer stays the
# system of record, and this mirrors journal/decisions/reliability/bots/slots
# into a queryable + realtime (SSE) backend. Never fatal: if the client is
# missing or the server is down, writes are silently skipped.
try:
    from pbclient import PB as _PB  # noqa: F401
    HAS_PB = True
except Exception:
    HAS_PB = False
    _PB = None

_pb_cache = None


def _pb():
    """Lazy, non-fatal PocketBase client. None => write-through disabled.

    GRID_STATE_DIR set (test isolation) disables the mirror as well: tests
    redirect state into a temp dir, and a stray PB_URL/PB_TOKEN in the ambient
    env would otherwise write test fixtures into the live side channel.
    """
    global _pb_cache
    if os.environ.get("GRID_STATE_DIR"):
        return None
    if not HAS_PB or _PB is None:
        return None
    if _pb_cache is None:
        try:
            _pb_cache = _PB()
        except Exception:
            _pb_cache = False
    return _pb_cache or None


def _pb_journal(event):
    pb = _pb()
    if pb is not None:
        try:
            pb.journal(event)
        except Exception:
            pass


def _pb_mirror_state(state):
    """Mirror bots/slots from state.json into the PB side channel (upsert by
    slot so repeated saves PATCH one row instead of appending duplicates)."""
    pb = _pb()
    if pb is None:
        return
    try:
        for slot, bot in (state.get("active_bots") or {}).items():
            pb.upsert("bots", "slot", {
                "slot": str(slot),
                "spec": bot if isinstance(bot, dict) else {},
            })
        slots = state.get("slots") or {}
        # slots may be a list (slot_plan) or dict (hand-edited state)
        rows = slots.items() if isinstance(slots, dict) else \
            [(s.get("slot"), s) for s in slots if isinstance(s, dict)]
        for slot, plan in rows:
            if slot is None:
                continue
            pb.upsert("slots", "slot", {"slot": str(slot), "plan": plan})
    except Exception:
        pass

# Real-money profile hard denylist (refused even if allowlisted by mistake).
PROFILE_DENYLIST = {"c629f5ba3a643a82137e7864"}


# ── runtime env self-heal (launchd starts with a bare environment) ────
# The daemon needs CLOUDFLARE_* (LLM chain) and PB_* (side channel) that a
# shell start gets from start.sh. Under launchd both arrive via
# scripts/run_launchd.py — and if that import failed at boot (dsh web down,
# ps -Eww restricted), the daemon would silently run LLM-degraded and PB-less
# forever. These helpers re-import at runtime so the next cycle heals.
sys.path.insert(0, os.path.join(HERE, "scripts"))
try:
    from run_launchd import import_cf_env as _import_cf_env  # noqa: E402
    from run_launchd import load_pb_env as _load_pb_env  # noqa: E402
    from run_launchd import load_llm_env as _load_llm_env  # noqa: E402
except Exception:
    _import_cf_env = None
    _load_pb_env = None
    _load_llm_env = None


def self_heal_env(state=None):
    """Re-import CF/LLM + PocketBase env when missing. Returns healed: [str]."""
    healed = []
    if not os.environ.get("CLOUDFLARE_ACCOUNT_ID") and _import_cf_env:
        try:
            _import_cf_env()
        except Exception:
            pass
        if os.environ.get("CLOUDFLARE_ACCOUNT_ID"):
            healed.append("cf")
    if not (os.environ.get("PB_TOKEN")
            or os.environ.get("PB_ADMIN_EMAIL")) and _load_pb_env:
        try:
            _load_pb_env()
        except Exception:
            pass
        if os.environ.get("PB_TOKEN") or os.environ.get("PB_ADMIN_EMAIL"):
            healed.append("pb")
    _llm_keys = ("NVIDIA_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY")
    if not any(os.environ.get(k) for k in _llm_keys) and _load_llm_env:
        try:
            _load_llm_env()
        except Exception:
            pass
        if any(os.environ.get(k) for k in _llm_keys):
            healed.append("llm")
    if healed and state is not None:
        log(state, {"kind": "env-heal",
                    "msg": "re-imported " + "+".join(healed)
                           + " env (LLM chain / PB side channel restored)"})
    return healed


def cdp_alive(url, timeout=3.0):
    """True when the CloakBrowser CDP endpoint answers /json/version."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/json/version",
                                    timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def resolve_cmd(cmd):
    """First token of `cmd` resolved to an absolute path (launchd PATH lacks
    mise/homebrew). Unresolvable commands pass through and fail at run."""
    parts = shlex.split(cmd)
    if not parts:
        return None
    if "/" in parts[0]:
        return parts
    exe = shutil.which(parts[0])
    if exe is None:
        # launchd's minimal PATH hides homebrew/mise binaries, so the
        # watchdog's relaunch commands (node, …) would always fail — scan
        # the usual install roots an interactive shell would find them in
        for root in ("/opt/homebrew/bin", "/usr/local/bin",
                     "/opt/homebrew/sbin", "/usr/local/sbin"):
            if os.path.isfile(os.path.join(root, parts[0])):
                exe = os.path.join(root, parts[0])
                break
        if exe is None:
            import glob
            for base in glob.glob("/Volumes/*/mise/shims"):
                if os.path.isfile(os.path.join(base, parts[0])):
                    exe = os.path.join(base, parts[0])
                    break
    return ([exe] + parts[1:]) if exe else parts

# Minimum viable notional per grid line when :2087 market metadata is
# unavailable (live values are per-pair `limits.cost.min`: 10 USDC on
# Hyperliquid, 5-50 USDT on Binance markets).
MIN_USD_PER_GRID = 10.0

# Bot statuses accepted as "verified stopped" before a rotation may delete
# the incumbent. Anything else (active, stopping, …) keeps it alive.
# "stopped_with_unrealized" is the transient terminal state right after a
# stop_and_close_all while WT is still closing the open legs — the bot is
# definitively not running (verified live 2026-09-05: it settles to plain
# "stopped" within minutes; rejecting it vetoed an already-stopped bot).
STOPPED_STATES = {"stopped", "stopped_and_close_all",
                 "stopped_with_unrealized", "closed"}

STATE_PATH = os.path.join(HERE, "state", "state.json")


def _pidguard_ok():
    """Single-writer guard: refuse to run alongside a live daemon that holds
    state/daemon.pid. start.sh and run_launchd.py write that file with the
    daemon's OWN pid, so the normal launch paths always pass; a direct
    `daemon.py --once` (smoke) started while the launchd daemon is live
    would otherwise clobber state.json (observed 2026-09-05: a second
    dry-run process wrote a full cycle while the supervised daemon ran).
    Override with GRID_NO_PIDGUARD=1.
    """
    if os.environ.get("GRID_NO_PIDGUARD"):
        return True
    try:
        with open(os.path.join(os.path.dirname(STATE_PATH),
                               "daemon.pid")) as f:
            other = int(f.read().strip())
    except (OSError, ValueError):
        return True
    if other is None or other == os.getpid():
        return True
    try:
        os.kill(other, 0)
        alive = True
    except ProcessLookupError:
        alive = False  # no such pid — the guard file is stale
    except OSError:
        # EPERM probing a foreign-uid pid (e.g. pid 1 on macOS): we cannot
        # disprove liveness, so fail closed — a live daemon must never be
        # doubled, and a stale file is cheaper than a double-writer
        alive = True
    if alive:
        print(f"refusing to start: daemon pid {other} is live "
              f"(state/daemon.pid) — stop it first, or set "
              f"GRID_NO_PIDGUARD=1 to override", flush=True)
        return False
    return True


SPECS_DIR = os.path.join(HERE, "watch", "specs")  # patchable in tests
DEFAULT_STATE = {
    "live_allow": False,
    "slots": [],               # from slot_plan()
    "active_bots": {},         # slot -> {symbol, venue, bot_code, ticket, payloads, since, ...}
    "cooldowns_until": {},     # "venue:SYM" -> epoch
    "reliability": None,
    "committed": {},           # slot -> worst-case commitment
    "journal": [],             # recent decisions (capped)
    "last_cycle": None,        # utcnow() of last manage-loop pass
    "last_observe": {},        # latest observe_all() result (GET /observe)
    "last_adjust": {},         # slot -> epoch of last in-place grid_edit
    "profiles": [],            # last grid_profiles() snapshot
    # gap-report 2026-09-07: WT's "Demo Trading Grid Bots" cap is
    # PER-PAPER-PROFILE, not per-account. The old single scalar
    # `demo_bot_cap` over-counted (the fleet has 2 paper profiles,
    # each with its own 5-bot cap). `demo_bot_caps` is the per-
    # profile dict keyed by profile_code; the legacy scalar is kept
    # below for the one-shot migration at boot.
    "demo_bot_caps": {},
    "demo_bot_cap": None,
    "carry_pray": {},          # bot_code -> {bot_record, carry_since,
                               # target_tp_usd, source_slot, decision_id,
                               # take_profit_applied, ...} — see fix #6
}

# heartbeat defaults — config.yaml `heartbeat:` section overrides these
HEARTBEAT_DEFAULTS = {
    "enabled": True,
    "interval_s": 900.0,        # one heartbeat cycle per 15 min
    "screen_stale_s": 2400.0,   # screen_cache older → nudge a rescreen
    "error_rate_warn": 0.3,     # journal -error fraction (trailing hour)
}

DEFAULT_CONFIG = {
    "portfolio": {
        "total_usd": 600.0,
        "venues": {"hyperliquid": {"balance_usd": 400.0},
                   "binance": {"balance_usd": 200.0}},
        "slots_min": 3, "slots_max": 6, "slots_default": 4,
        # dynamic slot mode (default for hyperliquid, the premium exchange
        # tier with upsert-init capacity 200): slots open while a profitable
        # candidate waits (screen.open_slot_min_score) AND deployable capital
        # is spare — capital is the ceiling, not a slot count.
        # slots_hard_max is the fleet-wide ceiling for dynamic venues
        # (operator directive: only 6 slots watched & rotated profitably);
        # slots_max caps FIXED venues (everything not in dynamic_slot_venues).
        "slots_hard_max": 6, "dynamic_slot_venues": ["hyperliquid"],
        "min_slot_usd": 100.0,
        "max_alloc_per_slot": 0.5, "cash_buffer_pct": 0.15,
    },
    "screen": {"rescreen_minutes": 10, "confirm_interval": "4h",
               # scan breadth: all moderately significant tokens are screened
               # (top-N by 24h volume) so the EV + tvcli passes — not a
               # hand-picked list — decide what gets a slot
               "min_volume_usd": 2_000_000, "universe_max_symbols": 100,
               "top_per_preset_venue": 30, "confluence_top": 10,
               "confluence_skills": ["squeeze", "choppiness",
                                     "mtf-confluence", "dvi"],
               # a venue slot opens for a new candidate only at/above this
               # score (and only with spare deployable capital)
               "open_slot_min_score": 40.0},
    "watch": {"interval_s": 60, "adjust_steps_threshold": 2.0,
              # per-bot in-place grid-edit rate limit in hours (audit
              # 2026-09-06: the old hardcoded 6 h left a 0.85-confidence
              # recenter rec sitting ~4 h past its window). Shared by the
              # manual recenter path and the position-optimizer apply path.
              "adjust_cooldown_h": 2.0,
              # fleet PnL journal cadence in seconds (0 = off)
              "pnl_snapshot_interval_s": 300,
              # gone-bot reconciliation: a tracked bot missing from a
              # HEALTHY WT grid status list ("grid resource not found in
              # status list") warns once after this many ticks, and the
              # slot is freed after this many minutes of CONTINUOUS
              # missing observations (transport failures never count —
              # fail closed, a dead browser must never look like a gone
              # bot).
              "gone_warn_after": 3, "gone_clear_min": 30,
              # browser watchdog: every WunderTrading session-API call rides
              # the headful CloakBrowser on CDP — when it dies the daemon is
              # blind and deploy/rotate fail. Probed each health pass.
              "browser_cdp": "http://127.0.0.1:9222",
              "browser_restart_cooldown_s": 600},
    "autonomy": {"mode": "auto", "base_pct": 0.25, "probe_pct": 0.40, "full_pct": 0.50,
                 "live_profiles": [], "paper_profiles": ["demo-hype"],
                 # health-cycle retry cadence for the paper-profile ensure
                 # (boot is immediate; retries back off this long)
                 "profile_bootstrap_cooldown_s": 1800.0,
                 # tier caps grid DENSITY too, not just the worst-case
                 # target — at min-notional-dominated sizes the exchange
                 # floor otherwise raised every tier to the hard cap
                 "tier_max_grids": {"base": 12, "probe": 20, "full": 30}},
    "memory": {"k": 3},
    "adopt_existing": True,
    "policy": {"hysteresis_score": 5.0, "cooldown_min_h": 12.0,
               "cooldown_max_h": 72.0, "min_hold_h": 24},
    "reliability": {"min_samples": 30, "profit_factor_pass": 1.3,
                    # recent_pf kill-flag binds only with this many closed
                    # samples (1 losing trip must not ban a regime forever)
                    "kill_min_samples": 10},
    "server": {"daemon_port": 8799},
    # loop-health heartbeat (HEARTBEAT_DEFAULTS is the same contract in
    # module-level form; kept here so a config.yaml-less boot carries it)
    "heartbeat": dict(HEARTBEAT_DEFAULTS),
}

# ── config loading (YAML subset parser lives in config_lite.py) ───────


def load_config(path=None):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    p = path or os.path.join(HERE, "config.yaml")
    if os.path.isfile(p):
        try:
            with open(p) as f:
                cfg = deep_merge(cfg, load_yaml(f.read()) or {})
        except Exception as exc:
            print(f"config load failed ({exc}) — using defaults", flush=True)
    return cfg


# ── state + journal ────────────────────────────────────────────────────

def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(state, event):
    event = dict(event)
    event["at"] = utcnow()
    state.setdefault("journal", []).append(event)
    state["journal"] = state["journal"][-200:]
    _pb_journal(event)
    print(f"[{event['at']}] {event.get('kind')}: {event.get('msg')}", flush=True)


def _round_trip_fee_pct(venue):
    """Venue round-trip fee in % (execution.guardrails.ROUND_TRIP_FEE_PCT).

    Lazy import so the daemon still boots (and projects honestly with the
    0.15 fallback) even if the guardrails module is unavailable — this is
    an observability input, never a deploy gate."""
    try:
        from execution.guardrails import ROUND_TRIP_FEE_PCT
        return float((ROUND_TRIP_FEE_PCT or {}).get(venue, 0.15))
    except Exception:
        return 0.15


def _return_pct(num, den):
    """num/den*100 as a rounded percent, or None when the denominator is
    missing/<=0 — observability only, never raises."""
    try:
        den = float(den or 0)
        if den <= 0:
            return None
        return round(float(num or 0) / den * 100.0, 2)
    except (TypeError, ValueError):
        return None


def _double_days(annual_return_pct):
    """Time (whole days) for capital to double at an annualized return %,
    using the exact compound-doubling formula ln(2)/ln(1+r). None when the
    return is missing/<=0 — observability only, never raises."""
    try:
        r = float(annual_return_pct or 0)
        if r <= 0:
            return None
        return round(math.log(2.0) / math.log(1.0 + r / 100.0) * 365.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _fmt_double(days):
    """Humanized doubling time: '~214d' / '~11mo' / '~5.9y'."""
    try:
        d = float(days or 0)
    except (TypeError, ValueError):
        return "—"
    if d < 365:
        return f"~{round(d)}d"
    if d < 730:
        return f"~{round(d / 30.44)}mo"
    return f"~{d / 365.0:.1f}y"


def _tvcli_health(base_url, timeout=5.0):
    """GET {base}/health probe for the tvcli server → (ok, detail).

    Fail-soft: any transport/parse failure returns (False, short reason).
    Never raises — heartbeat check #1."""
    try:
        req = urllib.request.Request(
            str(base_url).rstrip("/") + "/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(2048) or b"{}"
            try:
                js = json.loads(body)
            except Exception:
                js = {}
            status = (js or {}).get("status") or "ok"
            return (r.status == 200), f"HTTP {r.status} · {status}"
    except Exception as exc:
        return False, f"unreachable: {str(exc)[:80]}"


def load_state():
    st = copy.deepcopy(DEFAULT_STATE)
    if os.path.isfile(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                st.update(json.load(f))
        except Exception as exc:
            print(f"state load failed ({exc}) — starting fresh", flush=True)
    for key in DEFAULT_STATE:
        st.setdefault(key, DEFAULT_STATE[key])
    return st


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)
    _pb_mirror_state(state)


def should_rotate(candidate, incumbent, policy, observed, now_epoch,
                  needs_reanalysis=False):
    """Pure rotation decision → (rotate: bool, reasons: [str]).

    `needs_reanalysis` (set by health_cycle when the incumbent went
    out-of-channel or was stopped) is a hard trigger: a stopped bot earns
    nothing, so the challenger hysteresis does not apply. The min-hold floor
    and per-symbol cooldowns are still enforced by the caller.
    """
    reasons = []
    # `or 0`: adopted bots carry score_final=None — must not break arithmetic
    inc_score = incumbent.get("score_final") or 0
    cand_score = candidate.get("score_final") or 0
    stag, s_reasons = is_stagnant(
        observed, policy,
        regime_now=candidate.get("regime"),
        score_drop=inc_score - cand_score,
        ladder_full=observed.get("ladder_full", False),
        dd_vs_atr_band=observed.get("dd_vs_atr_band", 0.0))
    if not stag and needs_reanalysis:
        stag = True
        s_reasons = list(s_reasons) + [
            "needs_reanalysis (out-of-channel/stopped)"]
    if not stag:
        return False, ["incumbent healthy"]
    reasons.extend(s_reasons)
    dscore = cand_score - inc_score
    if not needs_reanalysis and dscore < policy.get("hysteresis_score", 5.0):
        return False, reasons + [f"Δscore {dscore:.1f} < hysteresis"]
    return True, reasons + [f"Δscore {dscore:.1f}"]


def run_merge(top=30, confluence_top=10, no_confluence=False,
              min_volume=None, max_symbols=None):
    cmd = [sys.executable, os.path.join(HERE, "screen", "merge.py"),
           "--top", str(top), "--confluence-top", str(confluence_top), "--json"]
    if min_volume:
        cmd += ["--min-volume", str(min_volume)]
    if max_symbols:
        cmd += ["--max-symbols", str(max_symbols)]
    if no_confluence:
        cmd.append("--no-confluence")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if p.returncode != 0:
        raise RuntimeError(f"merge failed: {p.stderr[-500:]}")
    return json.loads(p.stdout)


# fields the fast optimizer needs from a screen candidate (challenger
# refresh + plan_candidate compatibility); keeps state.json lean
SCREEN_CACHE_FIELDS = (
    "venue", "symbol", "tv_symbol", "regime", "metrics", "evidence",
    "score", "score_final", "spread_pct", "step", "archetype", "vol_usd",
    "preset", "flags", "confluence", "confluence_notes", "confluence_bonus",
    "tvcli_fit",
    "expected_fills_per_24h", "harvest_net_pct_24h", "confirm_4h",
)


def screen_cache_entry(cand):
    return {k: cand.get(k) for k in SCREEN_CACHE_FIELDS}


def _confluence_ok(cand):
    """Count of tvcli confluence skills that returned a result for a
    candidate. Surfaces in the screen report top3 so operators can see at a
    glance whether the tvcli /hunt pass actually contributed (vs fail-soft
    score-only screening). 0 with confluence_bonus 0 means tvcli was down."""
    con = cand.get("confluence") if isinstance(cand, dict) else None
    if not isinstance(con, dict):
        return 0
    return sum(1 for k, v in con.items() if k != "errors" and v is True)


# ── Worker A/C safe wrappers ───────────────────────────────────────────

def resolve_pair_safe(venue, symbol, market=None):
    """(pair_code, error). error non-empty means the resolve module is absent.

    `market` overrides the venue default ("derivative" when the Binance
    sleeve runs on its BINANCE_FUTURES paper stand-in profile)."""
    if not HAS_RESOLVE:
        return None, "resolve module missing"
    try:
        info = resolve_pair(venue, symbol, market=market) \
            if market else resolve_pair(venue, symbol)
    except TypeError:
        try:
            info = resolve_pair(venue, symbol)
        except Exception as exc:
            return None, f"resolve failed: {exc}"
    except Exception as exc:
        return None, f"resolve failed: {exc}"
    if not info:
        return None, ""
    code = info.get("pairCode") if isinstance(info, dict) else None
    return code, ""


def observe_all_safe(active_bots):
    try:
        return observe_all(active_bots) or {}
    except Exception as exc:
        return {"_error": str(exc)[:160]}


def grid_profiles_safe():
    try:
        return grid_profiles() or []
    except Exception:
        return []


def grid_capacity_safe():
    """grid_capacity with error guard: {} on failure (never raises)."""
    try:
        return grid_capacity() or {}
    except Exception:
        return {}


def account_limits_safe():
    """account_limits with error guard: {} on failure (never raises)."""
    try:
        return account_limits() or {}
    except Exception:
        return {}


def _limits_signature(limits, capacity):
    """Compact signature of the observed subscription state (change detection)."""
    gb = (limits or {}).get("gridBots") or {}
    caps = (capacity or {}).get("max_active") or {}
    active = (capacity or {}).get("active") or {}
    premium = active.get("premium")
    premium_exchanges = sorted(premium.keys()) if isinstance(premium, dict) else []
    return json.dumps({
        "gridBots": {"active": gb.get("active"), "max": gb.get("max")},
        "tier_caps": caps,
        "premium_exchanges": premium_exchanges,
    }, sort_keys=True)


def grid_status_safe():
    """List of active bots from observe.grid_status (fallback grid_adapter)."""
    try:
        bots = grid_status() or []
        if bots:
            return bots
    except Exception:
        pass
    fn = getattr(grid_adapter, "grid_status", None)
    if fn is not None:
        try:
            return fn() or []
        except Exception:
            return []
    return []


# retry backoff after a FAILED grid edit (WT error), per slot — a failed
# edit must NOT burn the 2 h adjust cooldown nor mutate bot bookkeeping
# for geometry WT never accepted (live incident 2026-09-06: a WT HTTP 500
# was journaled as "position-optimizer-applied")
ADJUST_FAILED_RETRY_S = 600


def _edit_error_summary(res):
    """One-line summary of a failed grid_edit_safe result (journal msg).

    Pulls the status_code + first chunk of response_text out of the
    rich envelope ``wt_library._wun_error_envelope`` builds, so the
    operator can diagnose an HTTP 500 from the journal without digging
    through PocketBase. Falls back to the 120-char stdout head for any
    non-WT envelope.
    """
    r = res or {}
    parts = []
    if r.get("status_code") is not None:
        parts.append(f"HTTP {r['status_code']}")
    err = r.get("error")
    if not err:
        err = " ".join(str(r.get("stdout") or "").split())[:120]
        if err:
            parts.append(err)
    else:
        parts.append(str(err))
    rt = r.get("response_text")
    if rt and len(parts) < 2:
        parts.append((rt or "")[:200])
    return " — ".join(parts) if parts else "unknown error"


def grid_edit_safe(bot_code, upsert, dry_run=True):
    fn = getattr(grid_adapter, "grid_edit", None)
    if fn is None:
        return {"ok": False, "error": "grid_edit not implemented yet"}
    try:
        return fn(bot_code, upsert, dry_run=dry_run)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:160]}


def reliability_load_safe():
    if not HAS_RELIABILITY:
        return {}
    try:
        return load_reliability() or {}
    except Exception:
        return {}


def record_decision_safe(ticket, brief, action, payloads=None):
    """Adoption callers pass no payloads — mirror record_decision's default
    (a missing default here crashed adopt_existing live on 2026-09-05:
    "missing 1 required positional argument" aborted the whole adoption
    pass, orphaning running WT paper bots that then blocked redeploys)."""
    if not HAS_REFLECT:
        return None
    try:
        return record_decision(ticket, brief, action, payloads)
    except Exception:
        return None


def record_outcome_safe(decision_id, final):
    if not HAS_REFLECT or decision_id is None:
        return None
    try:
        return record_outcome(decision_id, final)
    except Exception:
        return None


def memories_for_safe(brief, k=3):
    if not HAS_REFLECT:
        return []
    try:
        return memories_for(brief, k=k) or []
    except Exception:
        return []


def write_run_card_safe(cycle_report):
    if not HAS_REFLECT:
        return None
    try:
        return write_run_card(cycle_report)
    except Exception:
        return None


def demo_cap_from_error(err):
    """WunderTrading's demo (paper) grid-bot cap from a create-400 message.

    The paper-bot limit ("You've reached the maximum number of Demo Trading
    Grid Bots! (Limit: 5)") is NOT part of the upsert init data or the
    account-limits dashboard — the only way to learn it is to hit it. Parse
    it from the rejection so the daemon adapts after ONE failure instead of
    re-failing every cycle."""
    m = re.search(r"Demo Trading Grid Bots.*?Limit:\s*(\d+)",
                  err or "", re.S)
    return int(m.group(1)) if m else None


def _migrate_demo_bot_caps(state):
    """One-shot migration: legacy single-scalar ``demo_bot_cap`` → per-profile
    ``demo_bot_caps``. Idempotent; safe to call on every boot.

    If the legacy scalar is set AND the per-profile dict is empty, the
    cap is shared with the per-profile dict for every paper profile that
    currently has ≥1 bot — a conservative seed that keeps the
    ``manage_cycle`` veto at the legacy effective level until the
    health-cycle relearn can lift each profile individually.
    """
    legacy = state.get("demo_bot_cap")
    per = state.get("demo_bot_caps") or {}
    if legacy is None or per:
        return
    seed = {}
    active = state.get("active_bots") or {}
    profiles = state.get("profiles") or []
    paper_codes = {p.get("code") for p in profiles if p.get("paperTrading")}
    for slot, bot in active.items():
        code = bot.get("profile_code")
        if not code or code not in paper_codes:
            continue
        seed.setdefault(code, legacy)
    state["demo_bot_caps"] = seed


def _demo_cap_for_profile(state, profile_code):
    """The learned per-profile demo (paper) cap, or None when unknown.

    Per-profile (keyed by ``profile_code``) is the WT-platform truth —
    the limit appears on each paper profile in the UI independently.
    Falls back to the legacy single scalar for backward compat.
    """
    if not profile_code:
        return None
    per = (state.get("demo_bot_caps") or {}).get(profile_code)
    if per is not None:
        try:
            return int(per)
        except (TypeError, ValueError):
            return None
    legacy = state.get("demo_bot_cap")
    if legacy is None:
        return None
    try:
        return int(legacy)
    except (TypeError, ValueError):
        return None


def _count_paper_bots(state, profile_code):
    """Number of active bots on a given paper profile, for the per-profile
    demo-cap gate. Falls back to exchange+name matching when the bot
    record has no profile_code field (older adoptions / WT-side changes
    that the daemon hasn't seen yet)."""
    if not profile_code:
        return 0
    profiles = state.get("profiles") or []
    match = next((p for p in profiles if p.get("code") == profile_code), None)
    if not match or not match.get("paperTrading"):
        return 0
    n = 0
    for bot in (state.get("active_bots") or {}).values():
        if not isinstance(bot, dict):
            continue
        code = bot.get("profile_code")
        if code == profile_code:
            n += 1
            continue
        # legacy adoption: match by exchange+symbol against the profile
        if code is None:
            ex_match = (bot.get("venue") == "hyperliquid" and
                        match.get("exchange") == "HYPERLIQUID_SWAP") or \
                       (bot.get("venue") == "binance" and
                        match.get("exchange") in ("BINANCE", "BINANCE_FUTURES"))
            if ex_match:
                # require a symbol match (the profile was selected FOR
                # that token via the venue sleeve)
                n += 1
    return n


def _set_demo_cap_for_profile(state, profile_code, cap):
    """Record a per-profile demo-cap (used by both the 400-learner and
    the upward relearn). Idempotent."""
    if cap is None or not profile_code:
        return
    state.setdefault("demo_bot_caps", {})[profile_code] = int(cap)
    # mirror to the legacy scalar for downstream readers (the
    # _demo_cap_for_profile fallback) — picks the maximum of all
    # profiles, so a one-profile fleet behaves identically to the
    # pre-migration state.
    legacy = max(int(cap),
                 max((int(v) for v in (state["demo_bot_caps"]).values()
                      if isinstance(v, (int, float))), default=0))
    state["demo_bot_cap"] = legacy


def retry_grid_call(fn, dry, *args, attempts=3, backoff=2.0, **kwargs):
    """Retry wt_browser subprocess failures twice with backoff (call-site retry).

    `dry` controls retries only; the wrapped fn receives *args/**kwargs
    untouched (e.g. its own dry_run=... flag).
    """
    last = None
    for i in range(attempts):
        try:
            res = fn(*args, **kwargs) or {"ok": False, "error": "empty result"}
        except Exception as exc:
            res = {"ok": False, "error": str(exc)[:160]}
        last = res
        if dry or res.get("ok"):
            return res
        if i < attempts - 1:
            time.sleep(backoff * (i + 1))
    return last


def extract_bot_code(res):
    if not isinstance(res, dict):
        return None
    code = res.get("gridBotCode") or res.get("bot_code") or res.get("code")
    if not code and res.get("stdout"):
        try:
            j = json.loads(res["stdout"])
            code = j.get("gridBotCode") or j.get("code")
        except Exception:
            pass
    return code


# ── profiles / venue helpers ───────────────────────────────────────────

def build_profiles_active(profiles):
    return {p.get("code"): p.get("balance", 0) or 0
            for p in profiles if p.get("code")}


# venue -> exchanges that may serve it. Binance SPOT is the live target;
# BINANCE_FUTURES is the paper stand-in (WunderTrading has no Binance spot
# paper mode) and keeps the sleeve's spot-like no-short rule.
VENUE_EXCHANGES = {
    "hyperliquid": {"HYPERLIQUID_SWAP"},
    "binance": {"BINANCE", "BINANCE_FUTURES"},
}


def _allowed_profile_names(cfg):
    """Flatten the venue-keyed paper_profiles map (or a legacy flat list)."""
    pp = cfg["autonomy"]["paper_profiles"]
    if isinstance(pp, dict):
        names = set()
        for v in pp.values():
            names.update(v if isinstance(v, (list, tuple)) else [v])
        return names
    return set(pp)


def _missing_paper_profiles(cfg, profiles):
    """venue -> [allowlisted paper-profile names missing from `profiles`].

    The mirror of select_profile's gate: a name counts as PRESENT only when
    a snapshot profile carries it on a venue-coherent exchange AND is a
    paperTrading account. A same-name non-paper or wrong-family profile
    stays "missing" — the wtclient ensure reports it as an error state and
    never mutates it. Fail-soft: {} when anything is unexpected.
    """
    try:
        spec = _profiles_spec(cfg) if HAS_PROFILES else {}
        if not spec:
            return {}
        present = set()
        for prof in profiles or []:
            if not isinstance(prof, dict):
                continue
            name = str(prof.get("name") or "").strip()
            venue = venue_from_exchange(prof.get("exchange"))
            if name and venue and prof.get("paperTrading"):
                present.add((venue, name))
        missing = {}
        for venue, names in spec.items():
            gaps = [n for n in names if (venue, n) not in present]
            if gaps:
                missing[venue] = gaps
        return missing
    except Exception:
        return {}


def _json_safe(value):
    """Deep JSON-safe copy of a result dict (str repr fallback per value)."""
    try:
        return json.loads(json.dumps(value))
    except Exception:
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(v) for v in value]
        return str(value)


def _ensure_paper_profiles_safe(cfg, execute):
    """profiles.ensure_paper_profiles with a fail-soft guard (never raises)."""
    try:
        return _profiles_ensure(cfg, execute=execute)
    except Exception as exc:
        return {"ok": False, "executed": bool(execute), "spec": {},
                "result": None, "error": str(exc)[:200]}


def select_profile(venue, profiles, cfg, paper=True):
    """(profile, violation). Venue-strict; paper deploys use paper only.

    A binance-venue profile must be on BINANCE (spot) or BINANCE_FUTURES
    (paper stand-in); a hyperliquid-venue profile on HYPERLIQUID_SWAP. No
    cross-venue fallback — a mismatched profile is a violation, never a
    silent misroute."""
    allowed = _allowed_profile_names(cfg)
    if not paper:
        allowed |= set(cfg["autonomy"]["live_profiles"])
    exchanges = VENUE_EXCHANGES.get(venue, set())
    candidates = [p for p in profiles
                  if p.get("code") and p.get("name") in allowed
                  and (p.get("exchange") or "").upper() in exchanges]
    if not candidates:
        have = [p.get("name") for p in profiles
                if (p.get("exchange") or "").upper() in exchanges]
        return None, (f"no {venue} profile allowlisted (allowlist={sorted(allowed)}, "
                      f"{venue} profiles present={have})")
    # prefer the venue's native market (BINANCE spot) over stand-ins
    candidates.sort(key=lambda p: 0 if (p.get("exchange") or "").upper()
                    == ("BINANCE" if venue == "binance" else "HYPERLIQUID_SWAP")
                    else 1)
    for p in candidates:
        if p.get("code") in PROFILE_DENYLIST:
            return None, f"profile {p.get('name')} denylisted"
        if paper and not p.get("paperTrading"):
            continue
        return p, None
    return None, f"no paper profile on {venue} (candidates exist but not paperTrading)"


def market_for_profile(profile):
    """'derivative' | 'spot' for the market/pairCode resolution."""
    e = (profile.get("exchange") or "").upper() if profile else ""
    if e == "BINANCE":
        return "spot"
    if e == "HYPERLIQUID" or e == "HYPERLIQUID_SWAP" or e.endswith("_FUTURES") \
            or e.endswith("_SWAP"):
        return "derivative"
    return "spot"


def fetch_symbol(venue, symbol):
    """Full ticker for public candle APIs (binance needs BTCUSDT, not BTC)."""
    s = (symbol or "").upper().replace("/", "")
    if venue == "binance" and not s.endswith(("USDT", "USDC", "BUSD")):
        return f"{s}USDT"
    return s


def venue_from_exchange(exchange):
    e = (exchange or "").upper()
    if "HYPERLIQUID" in e:
        return "hyperliquid"
    if "BINANCE" in e:
        return "binance"
    return None


def symbol_from_pair(pair, venue):
    p = (pair or "").upper()
    if not p:
        return None
    if venue == "hyperliquid":
        return p.split("-")[0] if "-" in p else p
    for suffix in ("USDT", "USDC"):
        if p.endswith(suffix) and len(p) > len(suffix):
            return p[:-len(suffix)]
    return p


def reclassify_regime(venue, symbol, interval="1h", limit=300):
    from market_regime import fetch_candles, compute_metrics, classify
    market = "futures" if venue == "hyperliquid" else "spot"
    cl = fetch_candles(venue, fetch_symbol(venue, symbol), interval, limit, market)
    m = compute_metrics(cl)
    regime, _ = classify(m)
    return regime


# ── reliability escalation ─────────────────────────────────────────────

def _reliability_stats_for(reliability, archetype):
    if not isinstance(reliability, dict):
        return {}
    stats = reliability.get(archetype)
    if isinstance(stats, dict):
        return stats
    # flat fallback: single-bot/archetype reliability dict
    if "samples" in reliability or "profit_factor" in reliability:
        return reliability
    return {}


def size_multiplier(reliability, archetype, cfg):
    """(multiplier, tier, stats) for the escalation ladder.

    Tiers are TARGET worst-case fractions of the slot — the exchange
    minimum floor may still raise them toward the cap (density-first
    sizing). Unproven archetypes start at base (25% target), scale to
    probe/full only with reliability samples."""
    full = float(cfg["autonomy"]["full_pct"])
    probe = float(cfg["autonomy"]["probe_pct"])
    base = float(cfg["autonomy"].get("base_pct", 0.25))
    stats = _reliability_stats_for(reliability, archetype)
    samples = stats.get("samples", 0) or 0
    pf = stats.get("profit_factor", 0) or 0
    if samples >= 30 and pf >= 1.3:
        return full, "full", stats
    if samples >= 10:
        return probe, "probe", stats
    return base, "base", stats


# The kill-flag only binds with this many closed samples: grid bots
# routinely close their first round-trip in drawdown, so a single losing
# trip on a fresh archetype (recent_pf=0.0 over 1 sample) must NOT
# permanently ban the regime — with no active bot of that archetype left,
# no new trades would ever enter the ledger and the refusal could never
# lift by itself (the paper-sampling loop would stall itself).
KILL_MIN_SAMPLES = 10


def refuse_new_archetype(reliability, archetype, min_samples=None):
    """True only when the archetype is measured AND recently unprofitable.

    recent_pf covers the last RECENT_WINDOW (20) closed round-trips; below
    `min_samples` total closed trips the signal is noise — treat it as
    no-signal instead of a kill.
    """
    stats = _reliability_stats_for(reliability, archetype)
    samples = stats.get("samples", 0) or 0
    if samples < (KILL_MIN_SAMPLES if min_samples is None else min_samples):
        return False
    recent_pf = stats.get("recent_pf")
    return recent_pf is not None and recent_pf < 1.0


# ── gone-bot reconciliation ──────────────────────────────────────────────
# execution/observe.py distinguishes two observe error classes: transport
# failure ("grid status list unavailable (browser/session down)") and the
# bot genuinely missing from a HEALTHY status list ("grid resource not
# found in status list"). The live az00 deployment had tracked bots that
# WunderTrading had deleted server-side, so health_cycle warned the
# missing-bot class on EVERY 60 s tick forever — the slots were never
# freed and the journal flooded. Reconciliation (Daemon._reconcile_gone_bot)
# now tracks a per-slot missing EPISODE and frees the slot after
# watch.gone_clear_min of continuous absence.
GONE_BOT_ERROR = "grid resource not found in status list"


def is_gone_bot_error(err):
    """True only for the missing-bot class. Transport failures are NEVER
    this class: they must not count toward removal (fail-closed — while
    the browser/session is down, a tracked bot's absence is unknown, not
    gone, and the state must stay exactly as it was)."""
    return bool(err) and GONE_BOT_ERROR in str(err)


# ── Daemon ─────────────────────────────────────────────────────────────

class Daemon:
    def __init__(self, port=None, live_paper=False):
        self.config = load_config()
        self.port = port or int(self.config["server"].get("daemon_port", 8799))
        self.state = load_state()
        # one-shot migration: legacy single-scalar demo_bot_cap →
        # per-profile demo_bot_caps dict. Idempotent; the helper is a
        # no-op when the per-profile dict is already populated or the
        # legacy scalar is absent.
        _migrate_demo_bot_caps(self.state)
        self_heal_env(self.state)
        # config llm.chain is the documented fallback order — export it as
        # the provider-chain env DEFAULT (an explicit GRID_LLM_CHAIN from
        # state/llm.env or the environment still wins, so console-side
        # chain edits keep their precedence). Without this the yaml key
        # was dead config: provider.py only ever read the env var.
        _chain = (self.config.get("llm") or {}).get("chain")
        if isinstance(_chain, list) and _chain \
                and not os.environ.get("GRID_LLM_CHAIN"):
            os.environ["GRID_LLM_CHAIN"] = ",".join(
                str(p).strip() for p in _chain if str(p).strip())
        # same treatment for the per-provider model ids (yaml = documented
        # default; state/llm.env + ambient env keep precedence)
        for _prov, _var in (("cf_model", "CF_MODEL"),
                            ("nvidia_model", "NVIDIA_MODEL"),
                            ("openrouter_model", "OPENROUTER_MODEL"),
                            ("mistral_model", "MISTRAL_MODEL")):
            _model = (self.config.get("llm") or {}).get(_prov)
            if _model and not os.environ.get(_var):
                os.environ[_var] = str(_model)
        self.profiles = grid_profiles_safe()
        self.reliability = reliability_load_safe()
        self.state["profiles"] = self.profiles
        self.state["reliability"] = self.reliability
        # paper-profile bootstrap state: the deploy guard vetoes forever
        # when an allowlisted paper profile is missing (e.g. demo-bn on a
        # fresh WT account), so the daemon ensures it exists itself.
        # Only EXECUTES in live-paper mode; a dry-run boot stays a silent
        # no-op (the health-cycle retry below journals its attempts).
        self._live_paper = bool(live_paper)
        self._profile_bootstrap_ts = 0.0
        if self._live_paper:
            self._bootstrap_paper_profiles()
        self.capabilities = {
            "resolve": HAS_RESOLVE, "observe": HAS_OBSERVE,
            "reliability": HAS_RELIABILITY, "reflect": HAS_REFLECT,
            "optimizer": HAS_OPTIMIZER,
            "position_optimizer": HAS_POSITION_OPTIMIZER,
        }
        self._lock = threading.Lock()
        self._rescreen_flag = False
        self._reliability_flag = False
        self._optimize_flag = False
        # fast-loop engine (None when optimizer.py failed to import)
        self.optimizer = _SlotOptimizer(self, journal_fn=log) \
            if _SlotOptimizer else None
        # position revaluation engine (None when position_optimizer.py
        # failed to import); journal_fn adapts the engine's 1-arg
        # journal_fn(event) contract to daemon's log(state, event)
        self.position_optimizer = _PositionOptimizer(
            self.config.get("position_optimizer"),
            journal_fn=lambda event: log(self.state, event),
            persist_fn=self._pb_recommendation_persist,
            hunt_fn=self._po_hunt_structure,
            # opt-in exit-edit seam: engine calls it (only when
            # position_optimizer.apply is on) with (code, exit_kwargs)
            # shaped for wt_library.grid_set_exits; the daemon-level
            # dry-run gate lives inside (see _po_apply_exit)
            apply_fn=self._po_apply_exit,
            # opt-in backtest-validation seam: engine calls it (only
            # when position_optimizer.backtest_validate is on) with
            # (grid_cfg, candles) — the pure wtclient grid-backtest
            # engine over candles the ENGINE fetched through its own
            # injected fetcher (see _po_backtest)
            backtest_fn=(self._po_backtest if HAS_WT_BACKTEST
                        else None)) \
            if _PositionOptimizer else None
        self._browser_down_since = None
        self._last_browser_restart = 0.0
        self._migrate_archetype_keys()

    def _migrate_archetype_keys(self):
        """Idempotent ledger-key normalization (split-key bug fix).

        Adopted bots used to key the reliability ledger by the raw regime
        name ("chop_high_volatility") while fresh deploys keyed it by the
        archetype label ("Neutral Grid (mean-reversion)") — the same market
        regime wrote its stats under two keys, halving the sample base that
        gates sizing escalation and kill-flags. This re-keys state, the
        ledger, and the trade archive to the canonical archetype label.
        """
        try:
            changed = False
            rekeyed = 0
            for bot in (self.state.get("active_bots") or {}).values():
                old = bot.get("archetype")
                if isinstance(old, str) and old and ledger_key(old) != old:
                    bot["archetype"] = ledger_key(old)
                    changed = True
                    rekeyed += 1
            if rekeyed:
                log(self.state, {"kind": "reliability-migrate",
                                 "msg": f"re-keyed {rekeyed} active bot(s) to "
                                        f"canonical archetype labels"})
            rel = self.reliability or {}
            for old in list(rel.keys()):
                new = ledger_key(old)
                if new == old:
                    continue
                # canonical key wins when both spellings held stats; the
                # 24h recompute rebuilds from the re-keyed trade archive
                # + active bots, so nothing measured is lost from source
                if new in rel:
                    log(self.state, {"kind": "reliability-migrate",
                                     "msg": f"ledger key {old!r} dropped — "
                                            f"canonical {new!r} wins"})
                else:
                    rel[new] = rel[old]
                    log(self.state, {"kind": "reliability-migrate",
                                     "msg": f"ledger key {old!r} → {new!r}"})
                del rel[old]
                changed = True
            self.reliability = rel
            self.state["reliability"] = rel
            if changed and HAS_RELIABILITY:
                save_reliability(rel)
            if HAS_RELIABILITY and normalize_archive():
                log(self.state, {"kind": "reliability-migrate",
                                 "msg": "re-keyed reliability_archive.json "
                                        "to canonical archetype labels"})
                changed = True
            if changed:
                save_state(self.state)
        except Exception as exc:  # migration must never block startup
            print(f"archetype-key migration failed: {exc}", flush=True)

    # ── browser watchdog (WT session-API dependency) ────────────────────
    def browser_watchdog(self):
        """Keep the CloakBrowser + WunderTrading page alive.

        Observe/deploy/rotate all call wt_browser.py, which drives a headful
        CloakBrowser over CDP. If the browser dies (crash, reboot, logout)
        the daemon goes blind. This probes the CDP endpoint every health
        pass and, past the restart cooldown, relaunches the browser and
        re-asserts the WT page. Journaled as browser-restart; never raises.
        """
        w = self.config.get("watch", {})
        cdp = w.get("browser_cdp", "http://127.0.0.1:9222")
        if cdp_alive(cdp):
            self._browser_down_since = None
            return True
        now = time.time()
        if self._browser_down_since is None:
            self._browser_down_since = now
        cooldown = float(w.get("browser_restart_cooldown_s", 600))
        if now - self._last_browser_restart < cooldown:
            return False
        self._last_browser_restart = now
        launch = w.get("browser_launch_cmd")
        restore = w.get("wt_restore_cmd")
        detail = ""
        for cmd in (launch, restore):
            if not cmd:
                continue
            try:
                argv = resolve_cmd(cmd)
                p = subprocess.run(argv, capture_output=True, text=True,
                                   timeout=300)
                detail = ((p.stdout or "") + (p.stderr or "")).strip()[-160:]
            except Exception as exc:
                detail = f"{cmd.split()[0]}: {str(exc)[:120]}"
        ok = cdp_alive(cdp)
        log(self.state, {"kind": "browser-restart",
                         "msg": (f"CDP {cdp} was down {int(now - (self._browser_down_since or now))}s — "
                                 f"relaunch {'ok' if ok else 'FAILED'}"
                                 + (f" | {detail}" if detail else ""))[:220]})
        return ok

    def env_status(self):
        """Readiness booleans for the ctl plane (presence only — never values)."""
        w = self.config.get("watch", {})
        cdp = w.get("browser_cdp", "http://127.0.0.1:9222")
        return {
            "llm_env": {
                "cf": bool(os.environ.get("CLOUDFLARE_ACCOUNT_ID") and
                           (os.environ.get("CLOUDFLARE_API_KEY") or
                            os.environ.get("CLOUDFLARE_AI_TOKEN"))),
                "nvidia": bool(os.environ.get("NVIDIA_API_KEY")),
                "openrouter": bool(os.environ.get("OPENROUTER_API_KEY")),
                "mistral": bool(os.environ.get("MISTRAL_API_KEY")),
            },
            "pb_env": bool(os.environ.get("PB_TOKEN") or
                           os.environ.get("PB_ADMIN_EMAIL")),
            "browser_cdp": cdp_alive(cdp),
        }

    # ctl hook
    def queue_rescreen(self, force=False):
        """Queue an out-of-band rescreen (ctl /rescreen, /rotate, or the
        optimizer's free-slot refill nudge).

        AUTO nudges (force=False — the optimizer's refill path) are skipped
        while the fleet is at the learned demo (paper) grid-bot cap: every
        refill deploy would be vetoed at the cap, so the nudge only burns a
        full screen (22 futile nudged rescreens in the 2026-09-06 audit
        window). The skip is journaled only on TRANSITION (cap reached /
        headroom back), never per cycle. Manual ctl requests pass
        force=True — a human rescreen is always honored.
        """
        if not force:
            cap = self.state.get("demo_bot_cap")
            active = self.state.get("active_bots") or {}
            if cap and len(active) >= int(cap):
                if not getattr(self, "_refill_skip_active", False):
                    self._refill_skip_active = True
                    log(self.state, {
                        "kind": "demo-cap-nudge-skip",
                        "msg": f"fleet at the demo (paper) grid-bot cap "
                               f"{len(active)}/{int(cap)} — refill rescreen "
                               f"nudges skipped until headroom returns"})
                return False
            if getattr(self, "_refill_skip_active", False):
                self._refill_skip_active = False
                log(self.state, {
                    "kind": "demo-cap-nudge-skip",
                    "msg": "demo-bot headroom back — refill rescreen "
                           "nudges re-enabled"})
        with self._lock:
            self._rescreen_flag = True
        # immediate feedback: a forced rescreen otherwise looks like nothing
        # happened for minutes (the loop consumes the flag within ~10 s and
        # the screen itself takes ~2-4 min before any slot-open/deploy line)
        log(self.state, {"kind": "rescreen-queued",
                         "msg": "manual rescreen requested — cycle starts "
                                "within ~10 s (screen + deploy decisions "
                                "take ~2-4 min)"})
        save_state(self.state)
        return True

    def consume_rescreen(self):
        with self._lock:
            v = self._rescreen_flag
            self._rescreen_flag = False
            return v

    def queue_reliability(self):
        with self._lock:
            self._reliability_flag = True

    def consume_reliability(self):
        with self._lock:
            v = self._reliability_flag
            self._reliability_flag = False
            return v

    def queue_optimize(self):
        """Request an immediate optimizer cycle (ctl POST /optimize)."""
        with self._lock:
            self._optimize_flag = True
        # immediate feedback: a forced cycle otherwise looks like nothing
        # happened for up to interval_min minutes
        log(self.state, {"kind": "optimize-queued",
                         "msg": "manual optimizer cycle requested — runs "
                                "within ~10 s (idle check + fast hunt)"})
        save_state(self.state)

    def consume_optimize(self):
        with self._lock:
            v = self._optimize_flag
            self._optimize_flag = False
            return v

    def optimizer_interval_s(self):
        """Fast-loop cadence in seconds — optimizer.interval_min clamped to
        the 2–5 min design band (2 = aggressive hunting, 5 = conservative)."""
        if not getattr(self, "optimizer", None):
            return None
        cfg = (self.config.get("optimizer") or {})
        if not cfg.get("enabled", True):
            return None
        try:
            minutes = float(cfg.get("interval_min", 3))
        except (TypeError, ValueError):
            minutes = 3.0
        return max(2.0, min(5.0, minutes)) * 60

    def position_optimizer_interval_s(self):
        """Position-revaluation cadence in seconds — position_optimizer.
        interval_min (0 = disabled: engine missing or enabled: false)."""
        if not getattr(self, "position_optimizer", None):
            return 0
        cfg = (self.config.get("position_optimizer") or {})
        if not cfg.get("enabled", True):
            return 0
        try:
            minutes = float(cfg.get("interval_min", 15))
        except (TypeError, ValueError):
            minutes = 15.0
        return max(1.0, minutes) * 60

    def _pb_recommendation_persist(self, rec):
        """Persist an applied position-optimizer recommendation into the
        PocketBase side channel. Non-fatal: None (id-less) when PB is off
        or the write fails — the rec then lives in the journal only."""
        pb = _pb()
        if pb is None:
            return None
        try:
            return (pb.recommendation(rec) or {}).get("id")
        except Exception:
            return None

    def _po_apply_exit(self, code, exit_kwargs):
        """Position-optimizer exit-apply seam → wt_library.grid_set_exits.

        Called by the engine (position_optimizer._apply_exit_rec) for
        add-take-profit / add-trailing / add-stop-loss recs ONLY, and
        only when position_optimizer.apply is on (the engine checks).
        The daemon-level dry-run gate lives HERE, mirroring how
        grid_stop/grid_create are gated: a dry-run daemon (the default)
        passes dry_run=True so wt_library returns the PLANNED envelope —
        journaled, never executed; a live-paper daemon executes the
        exit-only live edit. Never raises.
        """
        if not HAS_WT_LIBRARY:
            return {"ok": False,
                    "error": "execution/wt_library unavailable (wtclient "
                             "not importable) — exit edit not attempted"}
        try:
            return wt_library.grid_set_exits(
                code, dry_run=not self._live_paper, **(exit_kwargs or {}))
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:160]}

    def _po_backtest(self, grid_cfg, candles):
        """Position-optimizer backtest seam → wtclient grid-backtest engine.

        Called by the engine (position_optimizer._backtest_validate)
        only when position_optimizer.backtest_validate is on (default
        off) to validate an exit-add rec: runs the PURE client-side
        grid backtest engine (the engine behind the configurator's
        Backtest button / GridClient.backtest) on the GIVEN candles.
        Those candles came through the engine's own injected
        fetch_candles_fn — the daemon's geo-aware market_regime chain —
        NOT GridClient.backtest's :2087 network fetch, so free-tier /
        geo-block behavior stays consistent. Pure computation, zero
        network. Raises on missing engine/bad input (the caller fails
        open and keeps the rec advisory).
        """
        from position_optimizer import engine_candles  # pure helper
        if not HAS_WT_BACKTEST or _wt_backtest_engine is None:
            raise RuntimeError("wtclient.backtest not importable")
        bars = engine_candles(candles)
        if not bars:
            raise ValueError("no candles to backtest")
        engine_input = _wt_backtest_engine.build_input(grid_cfg, bars)
        return _wt_backtest_engine.run_backtest(engine_input, bars)

    def optimizer_status(self):
        """Snapshot for GET /optimizer (never raises)."""
        if not self.optimizer:
            return {"enabled": False, "available": False}
        try:
            return self.optimizer.status()
        except Exception:
            return {"enabled": False, "available": True, "error": "status"}

    def plan_slots(self):
        p = self.config["portfolio"]
        venues = {k: v.get("balance_usd", 0) for k, v in p["venues"].items()}
        return slot_plan(p["total_usd"], venues, p["slots_default"],
                         p["max_alloc_per_slot"], p["cash_buffer_pct"])

    def open_slot(self, venue):
        """Append a new slot for `venue` — the venue's slots are all occupied
        but a profitable candidate is waiting and deployable capital is spare.

        Two modes (portfolio.dynamic_slot_venues, default [hyperliquid]):

        dynamic  slots open as long as there is a profitable opportunity
                 (the caller already enforced screen.open_slot_min_score)
                 AND enough capital left. Capital is the ceiling, not a
                 slot count: the only count bound is the absolute runaway
                 guard portfolio.slots_hard_max. The new slot's budget is
                 max(sleeve/(n+1), portfolio.min_slot_usd) — never below the
                 exchange floor a viable grid needs ($10/line × ≥5 lines at
                 50% worst-case = $100). Existing slots KEEP their budgets:
                 shrinking a running slot below the exchange floor would
                 veto every future re-deploy into it. Opened-but-unfilled
                 slots RESERVE their worst-case so a burst of opens cannot
                 over-commit the fund.

        fixed     (every venue not listed) the sleeve is re-split equally
                 across the venue's slots (existing budgets re-normalized),
                 and the total slot count is capped by portfolio.slots_max.

        Gates (fail-closed — any refusal leaves the slot count unchanged):
          1. total slots < slots_hard_max (dynamic) / slots_max (fixed)
          2. venue is funded in the portfolio
          3. spare deployable capital ≥ the new slot's worst-case commitment
        Returns (slot, None) on open, (None, reason) on refusal.
        """
        return self._open_slot_plan(venue, commit=True)

    def _open_slot_plan(self, venue, commit=True):
        """open_slot's planning half: identical gates and slot construction,
        but the persisted mutation (slot append, fixed-venue budget
        re-normalization, the slot-open journal) only happens with
        commit=True. Dry-run mirrors call this with commit=False so their
        decision ledger shows what the planner WOULD open without growing
        the persisted slot plan."""
        p = self.config["portfolio"]
        dynamic = venue in (p.get("dynamic_slot_venues") or [])
        min_slot_usd = float(p.get("min_slot_usd", 100.0))
        cap = int(p.get("slots_hard_max", 6) if dynamic
                  else p.get("slots_max", 5))
        cur = list(self.state["slots"])
        if len(cur) >= cap:
            label = "slots_hard_max" if dynamic else "slots_max"
            return None, f"already at {label} {cap}"
        venues = {k: v.get("balance_usd", 0) or 0
                  for k, v in p["venues"].items()}
        sleeve = venues.get(venue, 0)
        if sleeve <= 0:
            return None, f"venue {venue} not funded"
        ceiling = self.plan_slots()["deployable_ceiling"]
        committed = sum(self.state["committed"].values())
        # opened-but-unfilled slots already hold a claim on the ceiling —
        # count their worst-case as reserved so a burst of dynamic opens
        # cannot promise the same capital twice
        used = {int(s) for s in self.state["active_bots"]}
        reserved = sum((s.get("max_commitment") or 0)
                       for s in cur if s["slot"] not in used)
        spare = ceiling - committed - reserved
        venue_slots = 1 + sum(1 for s in cur if s["venue"] == venue)
        max_alloc = float(p.get("max_alloc_per_slot", 0.5))
        if dynamic:
            balance = round(max(sleeve / venue_slots, min_slot_usd), 2)
        else:
            balance = round(sleeve / venue_slots, 2)
        max_commitment = round(balance * max_alloc, 2)
        if spare < max_commitment - 1e-9:
            return None, (f"spare ${spare:.2f} < new-slot worst-case "
                          f"${max_commitment:.2f}")
        new_slot = {"slot": max(s["slot"] for s in cur) + 1, "venue": venue,
                    "balance": balance, "max_commitment": max_commitment,
                    "venue_sleeve": sleeve, "venue_slots": venue_slots,
                    "dynamic": dynamic}
        if commit:
            if not dynamic:
                # re-normalize the venue's existing slot budgets to the new
                # split so a later deploy into a freed slot sizes like one
                # into the new
                for s in cur:
                    if s["venue"] == venue:
                        s["balance"] = balance
                        s["max_commitment"] = max_commitment
                        s["venue_slots"] = venue_slots
            self.state["slots"] = cur + [new_slot]
            log(self.state, {"kind": "slot-open", "slot": new_slot["slot"],
                             "msg": (f"{venue} slot {new_slot['slot']} opened "
                                     f"(${balance:.0f} budget, worst-case "
                                     f"${max_commitment:.0f}); spare "
                                     f"${spare:.0f} of ${ceiling:.0f} "
                                     f"deployable"
                                     + (" (dynamic)" if dynamic else ""))})
        return new_slot, None

    # ── plan + commit one candidate (shared by rescreen and rotation) ──
    def plan_candidate(self, cand, slot, dry_run, is_rotation=False,
                       incumbent=None, cooldown_ok=True):
        """resolve → deliberate → profile → reliability → payloads → guard.

        Returns (action, ticket, payloads, brief); action is None on any veto.
        Does NOT create a bot — callers commit separately so rotation can
        stop/verify/delete the incumbent before deploying the challenger.
        """
        key = f"{cand['venue']}:{cand['symbol']}"
        brief = dict(cand, slot=slot)
        brief["memories"] = memories_for_safe(
            brief, k=int(self.config["memory"].get("k", 3)))
        # stagnation policy for the swarm brief (best-effort, same as deploy)
        try:
            from market_regime import fetch_candles
            cl = fetch_candles(cand["venue"],
                               fetch_symbol(cand["venue"], cand["symbol"]),
                               "1h", 300,
                               "futures" if cand["venue"] == "hyperliquid" else "spot")
            brief["stagnation_policy"] = derive_policy(
                [c[3] for c in cl], "1h", cand.get("step") or 0.5, cand["regime"])
        except Exception as exc:
            brief["stagnation_policy"] = {"error": str(exc)[:120]}

        ticket = deliberate(brief)
        if ticket.get("decision") != "GO":
            log(self.state, {"kind": "veto",
                             "msg": f"{key}: {ticket.get('veto', '?')[:160]}"})
            # rejected candidates land in the ledger too (evidence block
            # included) — "what did the agents turn down and why" is as
            # operable as the deploys; memories_for ignores outcome-less
            # rows, so the recall lane is unaffected
            try:
                record_decision_safe(
                    ticket, brief,
                    {"kind": "NO-GO", "slot": slot["slot"],
                     "venue": cand["venue"], "symbol": cand["symbol"],
                     "msg": f"deliberation veto: "
                            f"{str(ticket.get('veto', '?'))[:160]}"})
            except Exception:
                pass
            return None, ticket, None, brief

        # live profile selection FIRST (venue-strict, allowlist + denylist):
        # the profile's exchange decides the market for pairCode resolution.
        profile, violation = select_profile(cand["venue"], self.profiles,
                                            self.config, paper=True)
        if violation:
            log(self.state, {"kind": "guard-veto", "msg": f"{key}: {violation}"})
            return None, ticket, None, brief
        profiles_active = build_profiles_active(self.profiles)
        exch = (profile.get("exchange") or "").upper()
        market = market_for_profile(profile)

        # pairCode resolved BEFORE guard, from the profile-coherent market
        pair_code, resolve_err = resolve_pair_safe(cand["venue"], cand["symbol"],
                                                   market=market)
        if resolve_err:
            log(self.state, {"kind": "guard-veto",
                             "msg": f"{key}: {resolve_err} — cannot resolve pairCode"})
            return None, ticket, None, brief
        if not pair_code:
            log(self.state, {"kind": "guard-veto",
                             "msg": f"{key}: pairCode unresolved for "
                                    f"{cand['venue']}:{cand['symbol']}"})
            return None, ticket, None, brief
        # per-pair trading constraints (min notional per trade, precision)
        try:
            meta = pair_meta(cand["venue"], cand["symbol"], market=market) or {}
        except Exception:
            meta = {}
        # WT grid engines enforce a per-line floor ($MIN_USD_PER_GRID) that is
        # HIGHER than the exchange's order min (limits.cost.min) on Binance
        # futures (5 USDT) — the exchange value must only raise it, never lower
        # it below the grid floor.
        min_cost = max(meta.get("min_cost") or 0, MIN_USD_PER_GRID)
        amount_precision = meta.get("amount_precision")

        # escalation ladder (canonical ledger key: adopted bots used to
        # key the ledger by raw regime name while fresh deploys keyed by
        # archetype label — same regime, two sample bases)
        archetype = ledger_key(cand.get("archetype")
                               or cand.get("regime", "neutral"))
        if refuse_new_archetype(self.reliability, archetype,
                                min_samples=self._kill_min_samples()):
            log(self.state, {"kind": "reliability-veto",
                             "msg": f"{key}: recent_pf < 1.0 over enough "
                                    f"samples for archetype "
                                    f"{archetype} — refuse new deployment"})
            return None, ticket, None, brief
        mult, tier, stats = size_multiplier(self.reliability, archetype, self.config)
        max_alloc_eff = min(mult, float(self.config["autonomy"]["full_pct"]),
                            float(self.config["portfolio"]["max_alloc_per_slot"]))

        payloads = grid_adapter.build_ticket_payloads(
            ticket, brief, slot["balance"], max_alloc_eff, profile["code"],
            pair_code, exchange_code=exch, amount_precision=amount_precision,
            min_cost=min_cost)

        # ── tier density cap ─────────────────────────────────────────
        # The escalation ladder must bind on more than the worst-case
        # TARGET: at min-notional-dominated sizes the exchange floor raised
        # every tier to the hard cap, making base/probe/full symbolic.
        # Each tier also caps the grid COUNT, so a base-tier deployment is
        # genuinely a small probe (fewer lines → wider step → less capital
        # at risk) until reliability samples accrue.
        tier_grids = int((self.config.get("autonomy", {}) or {}).get(
            "tier_max_grids", {}).get(tier, 0) or 0)
        if tier_grids and int(payloads["grid_bot"].get("grids") or 0) > tier_grids:
            payloads = grid_adapter.build_ticket_payloads(
                ticket, brief, slot["balance"], max_alloc_eff,
                profile["code"], pair_code, exchange_code=exch,
                amount_precision=amount_precision, min_cost=min_cost,
                max_affordable_grids=tier_grids,
                min_grids=int(self.config.get("grid_defaults", {})
                              .get("min_grids", 5)))
            log(self.state, {"kind": "tier-cap",
                             "msg": f"{key}: tier {tier} caps density at "
                                    f"{tier_grids} grids — step widened to "
                                    f"{payloads['grid_bot']['profit_per_grid_pct']}%"})

        # ── density-first sizing (user directive) ──────────────────────
        # Grid DENSITY (line count) drives profit (fills/day × profit/fill).
        # Funds are the risk budget, not a reason to degrade geometry:
        #   1. build_ticket_payloads already raises per-line USD to the
        #      exchange minimum (min_cost) when the tier allocation is
        #      smaller — using MORE funds, keeping all the lines.
        #   2. The risk cap applies to the WORST-CASE (one side of the
        #      channel ≈ grids/2 lines), not the distributed notional —
        #      if the min-funded worst case fits the cap, bump the tier to
        #      cover it and keep the full-density grid.
        #   3. Only when even half a channel at min_cost breaks the cap:
        #      widen the step (fewer lines) as the last resort, else veto.
        sizing = payloads["grid_bot"].get("sizing") or {}
        worst = sizing.get("total_commitment_estimate") or 0.0
        side_lines = int(sizing.get("side_lines")
                         or ((int(payloads["grid_bot"].get("grids") or 0) + 1) // 2)
                         or 1)
        cap = min(float(self.config["autonomy"]["full_pct"]),
                 float(self.config["portfolio"]["max_alloc_per_slot"]))
        cap_usd = cap * max(slot["balance"], 1e-9)
        if worst > cap_usd + 1e-9:
            min_grids = int(self.config.get("grid_defaults", {})
                            .get("min_grids", 5))
            # last resort: fit fewer lines at the exchange minimum
            fit_grids = int(2 * cap_usd / max(min_cost, 1e-9))
            if fit_grids >= min_grids:
                payloads = grid_adapter.build_ticket_payloads(
                    ticket, brief, slot["balance"], cap, profile["code"],
                    pair_code, exchange_code=exch,
                    amount_precision=amount_precision,
                    max_affordable_grids=fit_grids, min_cost=min_cost,
                    min_grids=min_grids)
                sizing = payloads["grid_bot"].get("sizing") or {}
                new_worst = sizing.get("total_commitment_estimate") or 0.0
                log(self.state, {"kind": "size-fit",
                                 "msg": f"{key}: worst-case ${worst:.0f} > "
                                        f"${cap_usd:.0f} cap — widened to "
                                        f"{payloads['grid_bot'].get('grids')} "
                                        f"grids (worst ${new_worst:.0f})"})
                if new_worst > cap_usd + 1e-9 or \
                        (sizing.get("usd_per_grid") or 0) < min_cost - 1e-9:
                    log(self.state, {"kind": "guard-veto",
                                     "msg": f"{key}: cannot fund ≥{min_grids} "
                                            f"lines at ${min_cost} within "
                                            f"{int(cap * 100)}% worst-case cap"})
                    return None, ticket, None, brief
                max_alloc_eff = cap
            else:
                log(self.state, {"kind": "guard-veto",
                                 "msg": f"{key}: slot ${slot['balance']:.0f} cannot "
                                        f"fund ≥{min_grids} lines at "
                                        f"${min_cost} within "
                                        f"{int(cap * 100)}% worst-case cap"})
                return None, ticket, None, brief
        elif worst > 1e-9:
            # min_cost funding needs a bigger tier than the ladder set —
            # raise the effective allocation to the honest worst case
            floor_alloc = worst / max(slot["balance"], 1e-9)
            if floor_alloc > max_alloc_eff + 1e-9:
                max_alloc_eff = floor_alloc
                # rebuild so guard_ctx.max_alloc matches the raised tier
                # (per-line is unchanged: min_cost dominates)
                payloads = grid_adapter.build_ticket_payloads(
                    ticket, brief, slot["balance"], max_alloc_eff,
                    profile["code"], pair_code, exchange_code=exch,
                    amount_precision=amount_precision, min_cost=min_cost)
                log(self.state, {"kind": "size-floor",
                                 "msg": f"{key}: funded "
                                        f"{payloads['grid_bot'].get('grids')} "
                                        f"grids at "
                                        f"${(sizing.get('usd_per_grid') or 0):.0f}"
                                        f"/line — worst-case "
                                        f"{max_alloc_eff:.0%} of slot"})

        ctx = dict(payloads["guard_ctx"], kill_file=os.path.join(HERE, "KILL"),
                   profiles_active=profiles_active,
                   profile_code=profile["code"],
                   deployable_ceiling=self.plan_slots()["deployable_ceiling"],
                   committed_now=sum(self.state["committed"].values()),
                   paper=True, is_rotation=is_rotation)
        if is_rotation:
            ctx.update({
                "cooldown_ok": cooldown_ok,
                # the optimizer's swap gate compared FRESH scores (both sides
                # re-scored this cycle); the stored incumbent score can be an
                # hour old and would falsely veto the swap here — trust the
                # fresh score the swap decision was actually made on
                "incumbent_score":
                    ((incumbent or {}).get("optimizer_swap") or {})
                    .get("inc_score_fresh")
                    or (incumbent or {}).get("score_final") or 0,
                "candidate_score": cand.get("score_final") or 0,
                # manual rotate (ctl /rotate) overrides the score hysteresis;
                # every other guard (sizing, spread, venue, reliability) stays
                "hysteresis": 0.0 if (incumbent or {}).get("force_rotate")
                else float(self.config["policy"].get("hysteresis_score", 5.0)),
            })
        ok, violations = guard_deploy(ticket, ctx)
        if not ok:
            log(self.state, {"kind": "guard-veto",
                             "msg": f"{key}: {'; '.join(violations)[:200]}"})
            return None, ticket, None, brief

        action = {"kind": "deploy-paper" if dry_run else "DEPLOY-PAPER",
                  "slot": slot["slot"], "venue": cand["venue"],
                  "symbol": cand["symbol"], "grid_type": ticket["grid_type"],
                  "msg": f"slot {slot['slot']} {key} {ticket['grid_type']} "
                         f"step {payloads['grid_bot']['profit_per_grid_pct']}% "
                         f"x{payloads['grid_bot']['grids']} (dry_run={dry_run})",
                  "size_multiplier": max_alloc_eff, "escalation_tier": tier,
                  "profile": profile["code"]}
        decision_id = record_decision_safe(ticket, brief, action, payloads)
        action["decision_id"] = decision_id
        return action, ticket, payloads, brief

    def commit_deploy(self, action, ticket, payloads, brief, cand, slot, dry_run):
        """Create the bot (or journal the plan) and record active state."""
        archetype = ledger_key(cand.get("archetype")
                               or cand.get("regime", "neutral"))
        if dry_run:
            action["upsert"] = payloads["upsert"]
            log(self.state, action)
            return action
        res = retry_grid_call(grid_adapter.grid_create, False,
                              payloads["upsert"], cand["venue"], dry_run=False)
        action["result"] = res
        if not res.get("ok"):
            # surface the real WT reason (e.g. "Maximum number of Grid Bots
            # reached") instead of a bare DEPLOY-PAPER journal line
            msg = None
            try:
                msg = json.loads(res.get("stdout") or "").get("message")
            except Exception:
                pass
            action["kind"] = "deploy-failed"
            action["error"] = (msg or (res.get("stderr") or "")[:200]
                                or "grid_create ok=false")
            # learn the demo (paper) grid-bot cap from the 400 — it is not
            # part of any plan/capacity API, so the rejection is the only
            # teacher; persist it so the rescreen gates stop trying.
            # The cap is per-paper-profile (WT UI surfaces it independently
            # on each allowlisted paper account). The candidate's profile
            # code is on the action dict.
            demo_cap = demo_cap_from_error(action["error"])
            profile_code = action.get("profile") or (action.get("ticket") or {}).get("profile_code")
            if demo_cap and profile_code and \
                    _demo_cap_for_profile(self.state, profile_code) != demo_cap:
                _set_demo_cap_for_profile(self.state, profile_code, demo_cap)
                log(self.state, {
                    "kind": "demo-cap",
                    "msg": f"WunderTrading caps demo (paper) grid bots at "
                           f"{demo_cap} on profile {profile_code} — fleet "
                           f"is at the cap; new deploys vetoed until one "
                           f"is stopped (rotations still work: stop+delete "
                           f"frees the slot first)",
                    "profile": profile_code,
                })
            elif demo_cap and not profile_code:
                # unknown profile (legacy path) — fall back to the legacy
                # single-scalar write so the gate still lifts correctly
                if self.state.get("demo_bot_cap") != demo_cap:
                    self.state["demo_bot_cap"] = demo_cap
                    log(self.state, {
                        "kind": "demo-cap",
                        "msg": f"WunderTrading caps demo (paper) grid bots at "
                               f"{demo_cap} — fleet is at the cap; new deploys "
                               f"vetoed until one is stopped (rotations still "
                               f"work: stop+delete frees the slot first)"})
            # close the decision record: outcomes only attach on rotation,
            # so a failed create used to leave an open decision line in the
            # journal/console ledger forever
            record_outcome_safe(action.get("decision_id"), {
                "reason": "deploy-failed",
                "error": action["error"],
                "realized_pnl": None, "fills": 0, "observed": {}})
        log(self.state, action)
        bot_code = extract_bot_code(res)
        if res.get("ok") and bot_code:
            channel = {
                "low": payloads["upsert"]["lowPrice"],
                "mid": payloads["upsert"]["midPrice"],
                "high": payloads["upsert"]["highPrice"],
                "step_pct": payloads["grid_bot"]["profit_per_grid_pct"],
                "atr_pct": brief.get("metrics", {}).get("atr_pct"),
                "grids": payloads["upsert"]["gridLevels"],
            }
            self.state["active_bots"][str(slot["slot"])] = {
                "symbol": cand["symbol"], "venue": cand["venue"],
                "since": utcnow(), "ticket": ticket,
                "score_final": cand["score_final"],
                "archetype": archetype,
                "stagnation_policy": payloads["stagnation_policy"],
                "bot_code": bot_code, "channel": channel,
                "profile_code": action.get("profile"), "pair_code": payloads["upsert"]["pairCode"],
                "upsert": payloads["upsert"],
                "decision_id": action.get("decision_id"),
                "size_multiplier": action.get("size_multiplier"),
                # cumulative-total-PnL target for the daemon-side profit
                # exit (WT DOES enforce server-side takeProfit/stopLoss/
                # trailingStop on cumulative Total PnL — see
                # execution/grid_adapter.py compute_upsert kwargs +
                # docs/position_optimizer.md; the daemon exit stays as the
                # all-lines-≥0 refinement on top)
                "take_profit_usd": self._default_take_profit(slot["slot"]),
            }
            # a fresh bot starts with a fresh idle clock: the per-slot
            # fill tracker in state["optimizer"]["trackers"] carries the
            # PREVIOUS occupant's last_increase_at, which flagged new
            # bots idle minutes after deploy (2026-09-05: XVG flagged at
            # age 21m, inheriting a 272m-old counter; same stale counter
            # drove the premature ROBO swap). Clearing it makes
            # update_tracker re-seed last_increase_at=now on the next
            # observe fold (last is None → bumped → now).
            self.state.setdefault("optimizer", {}) \
                .setdefault("trackers", {}).pop(str(slot["slot"]), None)
            # position revaluation on entry (advisory: apply stays False
            # unless configured otherwise; journaled even on keep)
            if self.position_optimizer:
                try:
                    _po_bot = self.state["active_bots"][str(slot["slot"])]
                    _po_rec = self.position_optimizer.post_deploy(
                        _po_bot, str(slot["slot"]), dry_run=dry_run)
                    if _po_rec and _po_rec.get("recommendation") != "keep":
                        _po_bot["position_optimizer"] = \
                            _po_bot.get("position_optimizer") or {}
                        _po_bot["position_optimizer"]["last_recommendation"] = \
                            _po_rec["recommendation"]
                except Exception as _po_exc:
                    log(self.state, {
                        "kind": "position-optimizer-error",
                        "msg": f"post-deploy analysis failed: "
                               f"{str(_po_exc)[:160]}"})
            self.state["committed"][str(slot["slot"])] =                 payloads["guard_ctx"]["total_commitment"]
            spec = build_spec(cand["symbol"], cand["tv_symbol"],
                              payloads["upsert"]["midPrice"],
                              payloads["upsert"]["gridPercentStep"] * 100,
                              payloads["upsert"]["gridLevels"], slot["slot"],
                              regime=cand["regime"])
            spec_path = os.path.join(
                SPECS_DIR, f"{cand['symbol'].lower()}-s{slot['slot']}.json")
            try:
                os.makedirs(os.path.dirname(spec_path), exist_ok=True)
                with open(spec_path, "w") as f:
                    json.dump(spec, f, indent=2)
            except Exception as exc:
                log(self.state, {"kind": "warn", "msg": f"spec write failed: {exc}"})
        return action

    def _capacity_note_deploy(self, capacity, cand, payloads):
        """Locally adjust the tier-cap snapshot after THIS daemon created a
        bot in the current rescreen cycle.

        The snapshot is fetched once per cycle; without this adjustment the
        pre-check for candidate N ignores the bot the daemon itself created
        for candidate N−1 and retries the create into the server-side 400
        ("Maximum number of Grid Bots reached") instead of skipping with a
        clean capacity-veto. Best-effort: never raises.
        """
        if not capacity:
            return
        try:
            profiles = self.profiles or self.state.get("profiles") or []
            prof, _violation = select_profile(cand["venue"], profiles,
                                              self.config, paper=True)
            if not prof:
                return
            exch = (prof.get("exchange") or "").upper()
            active = capacity.setdefault("active", {})
            premium = active.get("premium")
            if isinstance(premium, dict) and exch in premium:
                premium[exch] = (premium.get(exch) or 0) + 1
            else:
                active["other"] = (active.get("other") or 0) + 1
            pair = (payloads.get("upsert") or {}).get("pairCode")
            if pair:
                used = (capacity.get("used_pairs") or {}).get(exch)
                if isinstance(used, dict):
                    used.setdefault(prof.get("code") or "?", []).append(pair)
        except Exception:
            pass

    def _kill_min_samples(self):
        try:
            return int(self.config.get("reliability", {}).get(
                "kill_min_samples", KILL_MIN_SAMPLES) or KILL_MIN_SAMPLES)
        except (TypeError, ValueError):
            return KILL_MIN_SAMPLES

    def venue_capacity_block(self, cand, capacity):
        """(blocked_reason | None) — plan-level grid-bot capacity for a venue.

        From the upsert init data (maxActiveGridBots/activeGridBots/
        exchangesUsedPairs). Two rules:
          1. active-grid-bot cap per exchange tier — non-premium exchanges
             (free plan: everything except HYPERLIQUID_SWAP) share a single
             active-bot cap; premium exchanges have their own large cap.
             A blocked venue is rotation-only: stop+delete first frees it.
          2. one bot per pair per profile (server-validated pair exclusivity).
        Returns None when capacity data is unavailable — the guard-profile
        gate and the server-side 400 stay the backstops.
        """
        if not capacity:
            return None
        profiles = self.profiles or self.state.get("profiles") or []
        prof, _violation = select_profile(cand["venue"], profiles,
                                          self.config, paper=True)
        if not prof:
            return None  # profile gate handles it
        exch = (prof.get("exchange") or "").upper()
        active = capacity.get("active") or {}
        max_active = capacity.get("max_active") or {}
        premium_active = active.get("premium")
        if isinstance(premium_active, dict) and exch in premium_active:
            act, cap = premium_active.get(exch, 0), max_active.get("premium")
        else:
            act, cap = active.get("other"), max_active.get("other")
        if cap is not None and act is not None and act >= cap:
            return (f"plan cap: {act} active grid bot(s) on {exch} already "
                    f"at the max {cap} for its tier — venue is "
                    f"rotation-only until capacity frees")
        used = (capacity.get("used_pairs") or {}).get(exch) or {}
        if isinstance(used, dict):
            try:
                pair, _m = resolve_pair_safe(cand["venue"], cand["symbol"])
            except Exception:
                pair = None
            if pair and pair in (used.get(prof.get("code")) or []):
                return (f"pair {cand['venue']}:{cand['symbol']} already "
                        f"has a bot on profile {prof.get('name')}")
        return None

    # ── paper-profile bootstrap (self-heal) ─────────────────────────────
    def _profile_bootstrap_cooldown_s(self):
        """Retry cadence for the paper-profile ensure (autonomy tunable)."""
        try:
            v = float(self.config.get("autonomy", {}).get(
                "profile_bootstrap_cooldown_s",
                PROFILE_BOOTSTRAP_COOLDOWN_S) or PROFILE_BOOTSTRAP_COOLDOWN_S)
        except (TypeError, ValueError):
            v = PROFILE_BOOTSTRAP_COOLDOWN_S
        return max(0.0, v)

    def _bootstrap_paper_profiles(self):
        """Boot-time ensure of the allowlisted paper profiles (no-op when
        nothing is missing). Live-paper mode only — the caller gates it."""
        missing = _missing_paper_profiles(self.config, self.profiles)
        if not missing:
            return None
        return self._run_paper_profile_ensure(missing, execute=True)

    def _run_paper_profile_ensure(self, missing, execute):
        """One ensure attempt: journals a "profile-bootstrap" event with the
        report and refreshes the profile snapshot after a successful
        execute (an empty refresh is ignored — a browser hiccup must not
        wipe the last good snapshot)."""
        self._profile_bootstrap_ts = time.time()
        report = _ensure_paper_profiles_safe(self.config, execute=execute)
        if execute and report.get("ok"):
            fresh = grid_profiles_safe()
            if fresh:
                self.profiles = fresh
                self.state["profiles"] = fresh
        msg = ("paper-profile ensure executed: missing="
               f"{missing} ok={report.get('ok')}" if execute else
               f"paper-profile ensure PLANNED (dry-run): missing={missing}")
        log(self.state, {"kind": "profile-bootstrap",
                         "msg": msg + (f" error={report.get('error')}"
                                       if report.get("error") else ""),
                         "executed": bool(execute),
                         "missing": missing,
                         # JSON-safe copy: state.json must never fail on a
                         # non-serializable wtclient payload
                         "report": _json_safe(report)})
        return report

    def _retry_paper_profile_bootstrap(self):
        """Health-cycle paper-profile ensure retry, cooldown-gated.

        Runs only in live-paper mode (dry-run cycles never mutate
        WunderTrading); an empty/missing profile snapshot counts as
        everything missing, so a wiped WT account self-heals too."""
        now = time.time()
        if now - self._profile_bootstrap_ts < self._profile_bootstrap_cooldown_s():
            return
        missing = _missing_paper_profiles(self.config, self.profiles)
        if not missing:
            return
        self._run_paper_profile_ensure(missing, execute=True)

    # ── rescreen cycle ─────────────────────────────────────────────────
    def _maybe_market_brief(self, cands, hunt_stats):
        """One LLM call per brief interval — the always-on intelligence lane.

        The swarm debate only runs for DEPLOY candidates; with the fleet at
        the WT demo cap or all slots healthy, plan_candidate never fires and
        the provider chain (Mistral first once cf is keyless) sits idle
        between rare arbiter calls. This lane puts the subscription to work
        on every rescreen (rate-limited by llm.brief_interval_min, 0=off):
        a compact, strictly-JSON market brief over the fresh screen board +
        the live fleet's fills/PnL + the tvcli hunt counters — the same
        evidence the deploy agents would see. Journaled as `market-brief`,
        persisted to state.market_brief for the console Fleet rail.
        ADVISORY ONLY: the brief never gates, deploys, rotates or edits —
        it is intelligence, not an actuator. Fail-soft by construction."""
        try:
            cfg = self.config.get("llm") or {}
            interval_min = float(cfg.get("brief_interval_min", 30) or 0)
            if interval_min <= 0:
                return
            now = time.time()
            last = (self.state.get("market_brief") or {}).get("at_epoch")
            if last and now - float(last) < interval_min * 60:
                return
            from provider import chat_json, named_chain  # llm/ on sys.path

            # pin the brief lane to the subscription provider (default
            # mistral — the always-on intelligence should ride the paid
            # subscription, not the free CF lane it would otherwise hit
            # first where a CF key exists). named_chain returns [] when
            # the provider has no credentials, in which case chat_json
            # falls back to the global chain — the lane never hard-fails
            # on a missing key.
            brief_chain = named_chain(
                str(cfg.get("brief_provider") or "mistral"))

            fleet = {}
            try:
                fleet = (self.pnl_snapshot() or {}).get("fleet") or {}
            except Exception:
                pass
            incumbents = []
            for slot_key, bot in (self.state.get("active_bots") or {}).items():
                obs = bot.get("observed") or {}
                incumbents.append({
                    "slot": slot_key, "symbol": bot.get("symbol"),
                    "venue": bot.get("venue"), "regime": bot.get("regime")
                    or (bot.get("ticket") or {}).get("regime"),
                    "score": bot.get("score_final"),
                    "fills_24h": obs.get("fills_24h"),
                    "realized_pnl": obs.get("realized_pnl"),
                    "unrealized_pnl": obs.get("unrealized_pnl"),
                    "open_lines": obs.get("open_lines"),
                    "dd_vs_atr_band": obs.get("dd_vs_atr_band"),
                    "structure": (bot.get("observed") or {}).get(
                        "structure_notes") or [],
                })
            challengers = [{
                "venue": c.get("venue"), "symbol": c.get("symbol"),
                "regime": c.get("regime"),
                "score_final": c.get("score_final"),
                "harvest_net_pct_24h": c.get("harvest_net_pct_24h"),
                "expected_fills_24h": c.get("expected_fills_per_24h"),
                "confluence": c.get("confluence_notes") or [],
            } for c in (cands or [])[:5]]
            skills = {}
            for skill, s in ((hunt_stats or {}).get("skills") or {}).items():
                if isinstance(s, dict):
                    skills[skill] = f"{s.get('ok', 0)}/{s.get('hunted', 0)}"
            evidence = json.dumps({
                "fleet_pnl": fleet, "incumbents": incumbents,
                "top_challengers": challengers, "tvcli_hunt": skills,
            }, default=str)
            sys_prompt = ("You are the market intelligence officer of a "
                          "crypto grid-trading fleet. Reply with STRICT "
                          "JSON only, no markdown fences, no commentary. "
                          "Be terse: summary at most 45 words, arrays at "
                          "most 3 items of at most 15 words each.")
            user_prompt = (
                f"Assess the fleet's market position from this evidence and "
                f"what the fleet should watch next. Schema: "
                f'{{"bias":"risk-on|risk-off|mixed","summary":str,'
                f'"watch":[str],"risks":[str]}}. Evidence: {evidence}')
            name, obj = chat_json([
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt}],
                _chain=brief_chain or None)
            brief = {
                "at": utcnow(), "at_epoch": now, "provider": name,
                "bias": str(obj.get("bias") or "mixed")[:24],
                "summary": str(obj.get("summary") or "")[:600],
                "watch": [str(w)[:160] for w in (obj.get("watch") or [])[:3]],
                "risks": [str(r)[:160] for r in (obj.get("risks") or [])[:3]],
            }
            self.state["market_brief"] = brief
            log(self.state, {"kind": "market-brief",
                             "msg": f"[{name}] {brief['summary'][:200]}",
                             "brief": brief})
        except Exception:
            # fail-soft AND quiet: a dead chain would otherwise journal an
            # error every rescreen; the heartbeat's llm checks already
            # surface provider health
            pass

    def rescreen_cycle(self, dry_run=True, no_confluence=False, max_new=2,
                       top=None):
        actions = []
        screen_cfg = self.config.get("screen", {}) or {}
        top = top or int(screen_cfg.get("top_per_preset_venue", 30))
        # LLM env can heal at any time (dsh web restarted with keys, etc.) —
        # cheap no-op when the env is already complete
        self_heal_env(self.state)
        # self-heal: refresh the profile snapshot every cycle (survives
        # browser restarts / Cloudflare blips that emptied the cache)
        try:
            fresh_profiles = grid_profiles_safe()
            if fresh_profiles:
                self.profiles = fresh_profiles
                self.state["profiles"] = fresh_profiles
            elif not self.profiles:
                log(self.state, {"kind": "health-warn",
                                  "msg": "grid_profiles() empty — browser/CF "
                                         "may be challenged; deployments will "
                                         "veto until it recovers"})
        except Exception as exc:
            log(self.state, {"kind": "health-warn",
                              "msg": f"profile refresh failed: {str(exc)[:120]}"})
        # paper-profile self-heal retry: an allowlisted profile that is
        # still missing (or a wiped snapshot — everything missing) retries
        # the ensure on a cooldown instead of vetoing deploys forever.
        # Live-paper only: dry-run cycles never mutate WunderTrading.
        if not dry_run:
            try:
                self._retry_paper_profile_bootstrap()
            except Exception as exc:
                log(self.state, {"kind": "health-warn",
                                 "msg": f"paper-profile bootstrap retry "
                                        f"failed: {str(exc)[:120]}"})
        # subscription observation: enforced tier caps (upsert init) + the
        # dashboard plan view (account-limits). Journaled on any change so
        # plan upgrades/downgrades and the Hyperliquid premium tier are
        # noticed automatically.
        capacity = grid_capacity_safe()
        if capacity:
            self.state["capacity"] = capacity
        limits = account_limits_safe()
        if limits:
            self.state["account_limits"] = limits
        sig = _limits_signature(limits, capacity)
        if limits and sig != self.state.get("limits_signature"):
            self.state["limits_signature"] = sig
            gb = (limits.get("gridBots") or {})
            caps = (capacity or {}).get("max_active") or {}
            active = (capacity or {}).get("active") or {}
            premium = active.get("premium")
            premium_ex = sorted(premium.keys()) if isinstance(premium, dict) else []
            log(self.state, {"kind": "subscription",
                             "msg": f"gridBots {gb.get('active')}/"
                                    f"{gb.get('max')} (dashboard); tier caps "
                                    f"other={caps.get('other')} "
                                    f"premium={caps.get('premium')} "
                                    f"[premium: {','.join(premium_ex) or 'none'}]"})
        capacity_noted = set()  # one capacity-veto log per venue per cycle
        slot_open_noted = set()  # one slot-open-veto log per venue per cycle
        # the demo (paper) grid-bot cap is learned from a create-400 (not
        # part of any capacity API): once at the cap, no NEW bot can exist —
        # opening slots or deploying candidates is pointless until one is
        # stopped (rotations are fine: stop+delete frees the slot first)
        # Per-profile demo-cap veto (gap-report 2026-09-07): the cap is
        # per-paper-profile, not per-account. The deploy loop below knows
        # the candidate's profile_code (via select_profile), so the
        # per-profile check happens inside the loop, not here. The
        # legacy single-scalar check stays as a belt-and-braces guard for
        # any caller that bypasses the deploy loop (manual `POST /ctl/...`).
        demo_cap_legacy = self.state.get("demo_bot_cap")
        # legacy alias — the deploy loop and downstream readers still
        # reference `demo_cap` (the per-profile gate above is a
        # refinement, not a replacement)
        demo_cap = demo_cap_legacy
        if demo_cap_legacy and len(self.state["active_bots"]) >= demo_cap_legacy:
            # journal the cap-veto on TRANSITION only (entering the capped
            # state) — 44 hourly demo-cap-veto lines in one audit window
            # buried real events while carrying no new information
            if not getattr(self, "_demo_cap_veto_active", False):
                self._demo_cap_veto_active = True
                log(self.state, {
                    "kind": "demo-cap-veto",
                    "msg": f"fleet at the legacy demo (paper) grid-bot cap "
                           f"{len(self.state['active_bots'])}/{demo_cap_legacy} "
                           f"— new deploys skipped, rotations still allowed"})
        else:
            self._demo_cap_veto_active = False
        plan = self.plan_slots()
        if not self.state["slots"]:
            self.state["slots"] = plan["slots"]
        try:
            report = run_merge(
                top=top, no_confluence=no_confluence,
                confluence_top=int(screen_cfg.get("confluence_top", 10)),
                min_volume=int(screen_cfg.get(
                    "min_volume_usd", 2_000_000)),
                max_symbols=int(screen_cfg.get(
                    "universe_max_symbols", 100)))
        except Exception as exc:
            log(self.state, {"kind": "screen-error", "msg": str(exc)[:200]})
            save_state(self.state)
            return actions
        cands = report.get("results", [])
        hunt_stats = report.get("hunt_stats") or {}
        # data-source observability: persist the SUBPROCESS screen's candle-
        # hop tail + hunt counters for the ctl /status data_sources block
        # (the child's in-memory ring dies with it — this is the only way
        # the console sees which hop served the 4h-confirm + harvest EV
        # fetches). Fail-soft by construction.
        try:
            self.state["screen_data_sources"] = {
                "at": time.time(),
                "fetch_events": (report.get("fetch_events") or [])[-50:],
                "hunt_stats": hunt_stats if isinstance(hunt_stats, dict) else {},
            }
        except Exception:
            pass
        log(self.state, {"kind": "screen",
                         "msg": f"{len(cands)} candidates, top=" +
                                (f"{cands[0]['venue']}:{cands[0]['symbol']} "
                                 f"{cands[0]['score_final']}" if cands else "none"),
                         "hunt_stats": hunt_stats})
        # candidate board for the fast optimizer (2–5 min cadence): the top
        # entries with every field a challenger refresh + plan needs. The
        # optimizer re-scores them on live candles between rescreens.
        if cands:
            self.state["screen_cache"] = {
                "at": time.time(),
                "candidates": [screen_cache_entry(c) for c in cands[:12]],
            }
        used_slots = {int(s) for s in self.state["active_bots"]}
        active_keys = {f"{b.get('venue')}:{b.get('symbol')}"
                       for b in self.state["active_bots"].values()}
        # persisted slots (possibly grown by open_slot) are the source of
        # truth — plan_slots() only seeds the initial set
        free = [s for s in self.state["slots"] if s["slot"] not in used_slots]
        deployed = 0
        deliberations, guards, deployments = [], [], []
        for cand in cands:
            if deployed >= max_new:
                break
            if not dry_run and demo_cap \
                    and len(self.state["active_bots"]) >= demo_cap:
                # live creates would 400 at the demo cap — stop deliberating.
                # Dry-run plans carry no create, so the mirror keeps planning
                # and recording: a dry-run deployment's decision ledger must
                # reflect what the planner is doing, not freeze at the cap.
                break
            # Per-profile demo-cap check (gap-report 2026-09-07): the cap is
            # per-paper-profile, not per-account. Resolving the profile for
            # the candidate now lets the loop veto a single profile while
            # letting the other paper profile (with headroom) deploy.
            _cap_prof, _cap_violation = select_profile(
                cand["venue"], self.profiles, self.config, paper=True)
            _cap_code = _cap_prof.get("code") if _cap_prof else None
            if _cap_code and not dry_run:
                _cap = _demo_cap_for_profile(self.state, _cap_code)
                _active = _count_paper_bots(self.state, _cap_code)
                if _cap is not None and _active >= _cap:
                    key = f"{cand['venue']}:{cand['symbol']}"
                    log(self.state, {
                        "kind": "demo-cap-veto",
                        "profile": _cap_code,
                        "msg": f"paper profile {_cap_code} at its "
                               f"per-profile demo cap {_active}/{_cap} — "
                               f"skipping {key} (another paper profile "
                               f"may still have headroom)",
                    })
                    continue
            key = f"{cand['venue']}:{cand['symbol']}"
            if key in active_keys:
                continue  # already running in another slot — no duplicate deploy
            if key in self.state["cooldowns_until"] and \
                    time.time() < self.state["cooldowns_until"][key]:
                continue
            blocked = self.venue_capacity_block(cand, capacity)
            if blocked:
                if cand["venue"] not in capacity_noted:
                    capacity_noted.add(cand["venue"])
                    log(self.state, {"kind": "capacity-veto",
                                     "msg": f"{key}: {blocked}"})
                continue
            slot = next((s for s in free if s["venue"] == cand["venue"]), None)
            if slot is None:
                # every slot on this venue is occupied — open another one
                # when the token is strong enough and deployable capital is
                # spare (open_slot is fail-closed; a refusal costs nothing)
                floor = float(screen_cfg.get("open_slot_min_score", 40.0))
                if (cand.get("score_final") or 0) < floor:
                    continue  # below the open-slot floor
                if dry_run:
                    # dry-run slot-open simulation: same gates as a live
                    # open, no persisted mutation — the mirror records what
                    # the planner WOULD open and deploy instead of silently
                    # skipping every candidate when all slots are occupied.
                    # No slot-open journal here: the recorded decision row
                    # itself carries the virtual slot (tests assert dry-run
                    # never journals slot-open).
                    slot, open_err = self._open_slot_plan(cand["venue"],
                                                          commit=False)
                else:
                    slot, open_err = self.open_slot(cand["venue"])
                if slot is None:
                    if cand["venue"] not in slot_open_noted:
                        slot_open_noted.add(cand["venue"])
                        log(self.state, {"kind": "slot-open-veto",
                                         "msg": f"{key}: {open_err}"})
                    continue
                # the freshly opened slot is usable THIS cycle: a guard-veto
                # on the opener must not orphan it for every later candidate
                # (free was computed before the open)
                free.append(slot)
            action, ticket, payloads, brief = self.plan_candidate(cand, slot, dry_run)
            deliberations.append({
                "symbol": cand["symbol"], "venue": cand["venue"],
                "decision": ticket.get("decision", "NO_GO"),
                "confidence": ticket.get("confidence"),
                "llm_degraded": ticket.get("llm_degraded", False),
                "veto": ticket.get("veto"),
            })
            if action is None:
                continue
            guards.append({
                "symbol": cand["symbol"], "venue": cand["venue"],
                "ok": True, "violations": [],
            })
            self.commit_deploy(action, ticket, payloads, brief, cand, slot, dry_run)
            actions.append(action)
            # a LIVE create that failed (commit_deploy journals deploy-failed
            # and never adds the slot to active_bots) must NOT consume the
            # slot: keep it in `free` so the NEXT same-venue candidate in
            # this cycle deploys into the SAME slot instead of tripping
            # open_slot (az00 2026-09-08: a 400 on binance slot 4 re-split
            # the fixed $120 sleeve to 2×$60 and starved every later
            # candidate; a 400 on freshly-opened slot 7 then met
            # slots_hard_max on the next HL candidate). Dry-run never
            # mutates active_bots, so dry-run plans always count as
            # successful.
            deploy_ok = dry_run or str(slot["slot"]) in self.state["active_bots"]
            if deploy_ok:
                if not dry_run:
                    # keep the tier-cap snapshot honest for the NEXT candidate
                    # in this same cycle (the snapshot predates this deploy)
                    self._capacity_note_deploy(capacity, cand, payloads)
                deployments.append({
                    "slot": slot["slot"], "symbol": cand["symbol"],
                    "venue": cand["venue"], "grid_type": ticket.get("grid_type"),
                    "step_pct": payloads["grid_bot"]["profit_per_grid_pct"],
                    "amount": payloads["upsert"].get("amountPerTrade"),
                    "multiplier": action.get("size_multiplier"),
                    "paper": dry_run,
                })
                if slot in free:
                    free.remove(slot)
                deployed += 1
            active_keys.add(key)  # a later duplicate candidate must not re-deploy

        # ── rotation pass: stagnant incumbents vs better challengers ──
        rotations = []
        active_keys = {f"{b.get('venue')}:{b.get('symbol')}"
                       for b in self.state["active_bots"].values()}
        for slot_key, bot in list(self.state["active_bots"].items()):
            obs = bot.get("observed") or {}
            policy = bot.get("stagnation_policy") or {}
            if obs.get("error"):
                # observation outage (browser/session down): never rotate on
                # blindness — missing fills default to 0 and would fake
                # stagnation, churning the fleet during the outage instead
                # of waiting it out (fail-closed)
                continue
            fresh = next(
                (c for c in cands if c.get("venue") == bot.get("venue")
                 and c.get("symbol") == bot.get("symbol")), None)
            manual = bool(bot.get("force_rotate"))
            reasons = []
            if manual:
                reasons = ["optimizer swap (fast lane)"
                           if bot.get("optimizer_swap")
                           else "manual rotate (ctl /rotate)"]
                stag = True
            else:
                # min-hold floor: never rotate a bot younger than min_hold_h
                # (fresh bots always look fill-less until the first expected
                # oscillation — rotating them is churn, not adaptation)
                try:
                    since = datetime.fromisoformat(bot["since"]) \
                        if bot.get("since") else None
                    age_h = (datetime.now(timezone.utc) - since
                             ).total_seconds() / 3600 if since else 0.0
                except Exception:
                    age_h = 0.0
                min_hold_h = float(self.config.get("policy", {})
                                   .get("min_hold_h", 24))
                if age_h < min_hold_h:
                    continue
            needs_re = bool(bot.get("needs_reanalysis"))
            if not manual and not policy.get("stagnant_if") and not needs_re:
                # adopted bots / policy-derivation failures: no thresholds yet
                continue
            if not manual:
                regime_now = fresh.get("regime") if fresh else None
                inc_score = bot.get("score_final") or 0
                fresh_score = (fresh.get("score_final") or inc_score) if fresh \
                    else inc_score
                score_drop = inc_score - fresh_score
                stag, reasons = is_stagnant(
                    obs, policy, regime_now=regime_now, score_drop=score_drop,
                    ladder_full=obs.get("ladder_full", False),
                    dd_vs_atr_band=obs.get("dd_vs_atr_band", 0.0))
                if not stag and needs_re:
                    # out-of-channel/stopped incumbent flagged by
                    # health_cycle: rotate even without fill stagnation
                    stag = True
                    reasons = list(reasons) + [
                        "needs_reanalysis (out-of-channel/stopped)"]
            if not stag:
                continue
            # challenger: best fresh candidate on the SAME venue as the slot,
            # not already active, cooldown-clean, different symbol
            slot_venue = bot.get("venue")
            challenger = next(
                (c for c in cands if c.get("venue") == slot_venue
                 and f"{c.get('venue')}:{c.get('symbol')}" not in active_keys
                 and c.get("symbol") != bot.get("symbol")
                 and not (f"{c.get('venue')}:{c.get('symbol')}"
                          in self.state["cooldowns_until"]
                          and time.time() < self.state["cooldowns_until"][
                              f"{c.get('venue')}:{c.get('symbol')}"])), None)
            if challenger is None:
                log(self.state, {"kind": "rotation-skip", "slot": slot_key,
                                 "msg": f"{bot.get('venue')}:{bot.get('symbol')} "
                                        f"stagnant ({'; '.join(reasons)[:100]}) "
                                        f"but no eligible challenger"})
                continue
            if self.execute_rotation(slot_key, challenger, dry_run):
                rotations.append({
                    "slot": int(slot_key), "from": f"{bot.get('venue')}:{bot.get('symbol')}",
                    "to": f"{challenger.get('venue')}:{challenger.get('symbol')}",
                    "reasons": reasons,
                })
                # refresh: a second stagnant slot in the SAME cycle must not
                # pick this just-committed challenger again (the stale set
                # pre-dates the rotation and would allow the duplicate
                # create → pair-exclusivity 400 with the incumbent already
                # stopped and deleted, leaving the slot empty)
                active_keys = {f"{b.get('venue')}:{b.get('symbol')}"
                               for b in self.state["active_bots"].values()}
        save_state(self.state)
        cycle_report = {
            "at": utcnow(), "cycle_kind": "rescreen",
            "dry_run": dry_run, "paper": dry_run,
            "screen": {"n_candidates": len(cands),
                       "hunt_stats": hunt_stats,
                       "top3": [{"venue": c.get("venue"), "symbol": c.get("symbol"),
                                 "regime": c.get("regime"),
                                 "score_final": c.get("score_final"),
                                 "score": c.get("score"),
                                 "confluence_bonus": c.get("confluence_bonus"),
                                 "tvcli_fit": c.get("tvcli_fit"),
                                 "confluence_ok": _confluence_ok(c),
                                 "step": c.get("step", c.get("step_pct")),
                                 "spread_pct": c.get("spread_pct"),
                                 "expected_fills_per_24h":
                                     c.get("expected_fills_per_24h"),
                                 "harvest_net_pct_24h":
                                     c.get("harvest_net_pct_24h")}
                                for c in cands[:3]]},
            "deliberations": deliberations,
            "guard": guards,
            "deployments": deployments,
            "rotations": rotations,
            "observed": self.state.get("last_observe", {}),
            "reliability": self.reliability,
            "caveats": ["dry-run: zero WunderTrading mutations"] if dry_run else [],
            "actions": actions,
            "active_slots": sorted(self.state["active_bots"]),
        }
        write_run_card_safe(cycle_report)
        # always-on intelligence lane (Mistral-first chain): one brief per
        # interval over this fresh evidence; advisory-only, fail-soft
        self._maybe_market_brief(cands, hunt_stats)
        try:
            save_state(self.state)
        except Exception:
            pass
        return actions

    # ── gone-bot reconciliation (see is_gone_bot_error) ──────────────────
    # The missing-episode state lives on the bot dict itself
    # (gone_missing_since / gone_ticks / gone_warned), so an episode
    # survives daemon restarts via state.json.
    @staticmethod
    def _clear_gone_episode(bot):
        """End a missing-bot episode (bot reappeared, or a transport
        error made the observation inconclusive — fail closed)."""
        for k in ("gone_missing_since", "gone_ticks", "gone_warned"):
            bot.pop(k, None)

    def _reconcile_gone_bot(self, slot_key, bot, now=None):
        """Record one more missing-bot observation for slot_key.

        Warns at most ONCE per episode (after watch.gone_warn_after
        ticks), then — after watch.gone_clear_min of CONTINUOUS missing
        observations — journals one loud `bot-gone` entry, removes the
        bot from active_bots (slot freed; the existing refill logic —
        rescreen/optimizer nudge — repopulates it), drops its committed
        worst-case claim, and persists. Returns True when the bot was
        removed (caller `continue`s)."""
        now = time.time() if now is None else now
        if bot.get("gone_missing_since") is None:
            bot["gone_missing_since"] = now
        bot["gone_ticks"] = int(bot.get("gone_ticks") or 0) + 1
        watch = self.config.get("watch") or {}
        warn_after = max(1, int(watch.get("gone_warn_after", 3)))
        clear_min = max(0.1, float(watch.get("gone_clear_min", 30)))
        if bot["gone_ticks"] >= warn_after and not bot.get("gone_warned"):
            bot["gone_warned"] = True
            log(self.state, {"kind": "health-warn", "slot": slot_key,
                             "msg": f"observe error: {GONE_BOT_ERROR} — "
                                    f"bot missing on WunderTrading; slot "
                                    f"frees after {clear_min:g} min "
                                    f"continuous"})
        if now - float(bot["gone_missing_since"]) >= clear_min * 60.0:
            mins = (now - float(bot["gone_missing_since"])) / 60.0
            log(self.state, {
                "kind": "bot-gone", "slot": slot_key,
                "symbol": bot.get("symbol"), "venue": bot.get("venue"),
                "minutes_missing": round(mins, 1),
                "msg": f"{bot.get('venue')}:{bot.get('symbol')} absent "
                       f"from the WT grid status list for {mins:.0f} min — "
                       f"removed from state.active_bots, slot freed "
                       f"for the existing refill logic"})
            # same slot-clear the rotation path performs: per-slot
            # optimizer/position-optimizer trackers live on the bot dict
            # itself and are dropped with it (there are no separate
            # per-slot tracker structures to clean up)
            self.state["active_bots"].pop(slot_key, None)
            self.state.setdefault("committed", {}).pop(slot_key, None)
            save_state(self.state)
            return True
        return False

    # ── carry-and-pray (gap-report 2026-09-07) ───────────────────────
    # When a bot's underwater book has been stuck longer than the
    # token's natural profitable-close time, the slot is freed and the
    # bot is parked in state["carry_pray"] with a server-side takeProfit
    # at break-even. The bot keeps running on WT; if it recovers, the
    # TP locks the recovery. This is the "I believe in this token's
    # intrinsic value" trader behavior — keep the position, free the
    # capital for a more profitable challenger.
    def _carry_pray_cfg(self):
        cfg = (self.config.get("carry_pray") or {}) if self.config else {}
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "carry_after_k": float(cfg.get("carry_after_k",
                                           CARRY_AFTER_K)),
            "min_carry_h": float(cfg.get("min_carry_h", CARRY_MIN_H)),
            "max_carry_h": float(cfg.get("max_carry_h", CARRY_MAX_H)),
            "auto_apply_tp": bool(cfg.get("auto_apply_tp", True)),
            "tp_break_even_buffer_usd": float(
                cfg.get("tp_break_even_buffer_usd",
                        CARRY_BREAK_EVEN_BUFFER_USD)),
        }

    def _check_carry_pray_transition(self, slot_key, bot, obs, now):
        """(transition_now: bool, reasons: [str]) for one active bot.

        Gated to bots holding ≥1 underwater line (open_losing > 0) and
        whose `since` is older than the per-bot carry_after_h (k_carry ×
        avg_holding_h, persisted in the stagnation_policy). The bot is
        parked; the slot is freed; a server-side TP is placed (opt-in).
        """
        cfg = self._carry_pray_cfg()
        if not cfg["enabled"]:
            return False, ["carry-pray disabled in config"]
        if not isinstance(bot, dict) or not bot.get("bot_code"):
            return False, ["no bot_code"]
        # already carried? (defensive — the slot should be free now)
        if bot["bot_code"] in (self.state.get("carry_pray") or {}):
            return False, ["already in carry_pray"]
        # already needs_reanalysis: the bot was stopped/out-of-channel
        # and the health cycle flagged it; the rotation path will
        # handle it. Carry is for bots that are still RUNNING on WT
        # with an underwater book — the slot would otherwise be tied
        # up while the grid drowns.
        if bot.get("needs_reanalysis"):
            return False, ["needs_reanalysis — rotation path handles"]
        # only when the book has at least one losing line
        losing = obs.get("open_losing")
        if not losing:
            return False, ["no open_losing lines"]
        policy = bot.get("stagnation_policy") or {}
        # per-bot carry_after_h; fall back to the global default
        carry_after_h = policy.get("carry_after_h")
        if carry_after_h is None:
            carry_after_h = cfg["max_carry_h"]  # conservative default
        try:
            since_str = bot.get("since")
            if not since_str:
                return False, ["no since timestamp"]
            since = datetime.fromisoformat(since_str)
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            elapsed_h = (now - since.timestamp()) / 3600.0
        except Exception as exc:
            return False, [f"since parse failed: {str(exc)[:80]}"]
        if elapsed_h < max(cfg["min_carry_h"], carry_after_h):
            return False, [f"elapsed {elapsed_h:.1f}h < carry_after {carry_after_h:.1f}h"]
        # build the TP target from the bot's current open-losing state
        unrealized = obs.get("unrealized_pnl")
        try:
            unrealized_f = float(unrealized) if unrealized is not None else 0.0
        except (TypeError, ValueError):
            unrealized_f = 0.0
        # TP at break-even + buffer: lock in the recovery once realized
        # + unrealized crosses zero. The buffer covers the round-trip
        # fee so the bot never books a wash.
        target_tp = max(0.0,
                        -unrealized_f + cfg["tp_break_even_buffer_usd"])
        # park the bot, free the slot
        self._enter_carry_pray(slot_key, bot, target_tp, elapsed_h, cfg,
                               dry_run=(not self._live_paper))
        return True, [
            f"open_losing={losing} for {elapsed_h:.1f}h ≥ "
            f"carry_after {carry_after_h:.1f}h",
            f"target TP=${target_tp:.2f} (break-even + "
            f"${cfg['tp_break_even_buffer_usd']:.2f})",
        ]

    def _enter_carry_pray(self, slot_key, bot, target_tp, elapsed_h, cfg,
                          dry_run):
        """Move the bot from active_bots → state['carry_pray']; place TP.

        The slot is freed (active_bots.pop, committed.pop, cooldowns_until
        for venue:symbol cleared, per-slot marks dropped). The bot record
        is preserved with new carry_* fields. The decision_id outcome is
        recorded so reflect.memories_for() can recall the carry.
        Never raises.
        """
        try:
            code = bot.get("bot_code")
            key = f"{bot.get('venue')}:{bot.get('symbol')}"
            cp = self.state.setdefault("carry_pray", {})
            # snapshot the bot record (don't alias; future edits to
            # active_bots would mutate the carried record otherwise)
            snap = dict(bot)
            # pop the slot
            self.state["active_bots"].pop(slot_key, None)
            self.state.setdefault("committed", {}).pop(str(slot_key), None)
            # clear per-slot marks so a future rescreen doesn't treat
            # this as a "manual" rotation; the carried bot is no
            # longer a slot concern
            for mark in ("force_rotate", "optimizer_swap", "needs_reanalysis"):
                snap.pop(mark, None)
            # the cooldowns_until entry for the carried venue:symbol is
            # what would block a future rotation; clear it so a
            # challenger can pick up the symbol if it returns to form
            self.state.setdefault("cooldowns_until", {}).pop(key, None)
            entry = {
                "bot_code": code,
                "bot": snap,
                "source_slot": str(slot_key),
                "carry_since": datetime.fromtimestamp(
                    time.time(), tz=timezone.utc).isoformat(
                        timespec="seconds"),
                "carry_target_tp_usd": round(target_tp, 4),
                "decision_id": bot.get("decision_id"),
                "take_profit_applied": False,
                "take_profit_envelope": None,
            }
            # opt-in auto-apply the TP through the existing
            # grid_set_exits seam (the daemon's `_po_apply_exit`
            # already wraps this with the live-paper gate, so
            # dry_run here is a no-op when the daemon is dry-run).
            if cfg.get("auto_apply_tp") and \
                    getattr(self, "position_optimizer", None) is not None and \
                    self.position_optimizer.apply_fn is not None:
                try:
                    res = self.position_optimizer.apply_fn(
                        code, {"take_profit": target_tp,
                               "pnl_compare_type": "total"})
                    entry["take_profit_applied"] = bool(
                        (res or {}).get("ok"))
                    entry["take_profit_envelope"] = res
                except Exception as exc:
                    entry["take_profit_envelope"] = {
                        "ok": False, "error": f"apply_fn raised: {exc}"}
            cp[code] = entry
            # journal the transition (one line per carry; the
            # completion journal happens when the bot stops)
            log(self.state, {
                "kind": "carry-pray-enter",
                "slot": str(slot_key),
                "symbol": bot.get("symbol"),
                "venue": bot.get("venue"),
                "bot_code": code,
                "elapsed_h": round(elapsed_h, 2),
                "target_tp_usd": round(target_tp, 4),
                "tp_applied": entry["take_profit_applied"],
                "msg": (f"{bot.get('venue')}:{bot.get('symbol')} carried "
                        f"& prayed after {elapsed_h:.1f}h (target TP "
                        f"${target_tp:.2f}, applied={entry['take_profit_applied']}) "
                        f"— slot {slot_key} freed"),
            })
            # record the carry as a decision outcome so the
            # reflect memory learns the pattern
            try:
                record_outcome_safe(bot.get("decision_id"), {
                    "reason": "carry-pray",
                    "realized_pnl": (bot.get("observed") or {}).get(
                        "realized_pnl"),
                    "unrealized_at_carry": (bot.get("observed") or {}).get(
                        "unrealized_pnl"),
                    "open_losing": (bot.get("observed") or {}).get(
                        "open_losing"),
                    "target_tp_usd": target_tp,
                    "holding_h": round(elapsed_h, 2),
                    "observed": bot.get("observed") or {},
                    "challenger": None,
                })
            except Exception:
                pass
            save_state(self.state)
        except Exception as exc:
            log(self.state, {
                "kind": "carry-pray-error",
                "slot": str(slot_key),
                "msg": f"enter failed: {str(exc)[:160]}"})

    def _check_carry_pray_completion(self, now):
        """Iterate state['carry_pray']: drop entries that have stopped
        on WT (TP fired, or any other stop path). Record the outcome
        with reason='carry-pray-exit' so reflect memory learns the
        final PnL of the carry.
        """
        cp = self.state.get("carry_pray") or {}
        if not cp:
            return []
        exits = []
        for code, entry in list(cp.items()):
            try:
                obs = self._observe_carry_pray_bot(code, entry)
            except Exception as exc:
                log(self.state, {"kind": "carry-pray-warn", "bot_code": code,
                                 "msg": f"observe failed: {str(exc)[:120]}"})
                continue
            if obs is None:
                continue  # still running, nothing to do
            # stopped (or in a terminal state): drop, journal, record outcome
            try:
                final_pnl = (obs.get("unrealized_pnl") or 0) + \
                            (obs.get("realized_pnl") or 0)
                log(self.state, {
                    "kind": "carry-pray-exit",
                    "bot_code": code,
                    "symbol": (entry.get("bot") or {}).get("symbol"),
                    "venue": (entry.get("bot") or {}).get("venue"),
                    "final_pnl_usd": round(final_pnl, 4),
                    "status": obs.get("status"),
                    "msg": f"{(entry.get('bot') or {}).get('venue')}:"
                           f"{(entry.get('bot') or {}).get('symbol')} "
                           f"carry-pray exit (status={obs.get('status')}, "
                           f"final_pnl=${final_pnl:.2f})",
                })
                try:
                    holding = 0.0
                    since_iso = (entry.get("bot") or {}).get("since")
                    carry_since = entry.get("carry_since")
                    since_dt = datetime.fromisoformat(
                        carry_since) if carry_since else None
                    if since_dt and since_dt.tzinfo is None:
                        since_dt = since_dt.replace(tzinfo=timezone.utc)
                    if since_dt:
                        holding = (now - since_dt.timestamp()) / 3600.0
                    record_outcome_safe(entry.get("decision_id"), {
                        "reason": "carry-pray-exit",
                        "realized_pnl": obs.get("realized_pnl"),
                        "unrealized_at_exit": obs.get("unrealized_pnl"),
                        "final_pnl_usd": final_pnl,
                        "fills": obs.get("fills_24h"),
                        "holding_h": round(holding, 2),
                        "observed": obs,
                    })
                except Exception:
                    pass
                cp.pop(code, None)
                exits.append({"bot_code": code, "final_pnl": final_pnl})
            except Exception as exc:
                log(self.state, {"kind": "carry-pray-error", "bot_code": code,
                                 "msg": f"exit failed: {str(exc)[:120]}"})
        if exits:
            save_state(self.state)
        return exits

    def _observe_carry_pray_bot(self, code, entry):
        """Single observation of a carried bot by bot_code (the bot is
        no longer in active_bots; the observe path keys by slot).

        Re-uses ``observe_all`` by temporarily inserting the carried
        bot under a synthetic slot, then stripping it. This is the
        safest way to share the existing per-line pnl + exits
        projection without duplicating the whole observe path.

        Returns the obs dict on success, None when the bot is still
        running, or {"error": ...} on transport failure.
        """
        try:
            bots = {f"cp_{code}": entry.get("bot") or {}}
            obs_all = observe_all_safe(bots) or {}
            obs = obs_all.get(f"cp_{code}") or {}
            if obs.get("error"):
                return None  # transport glitch — try again next cycle
            status = (obs.get("status") or "active").lower()
            if status not in STOPPED_STATES:
                return None
            return obs
        except Exception:
            return None

    # ── health poll ────────────────────────────────────────────────────
    def health_cycle(self, dry_run=True):
        self.browser_watchdog()
        # fleet-ceiling prune (pure state math, no network): open_slot
        # enforces the ceiling on growth, but state can already sit above
        # it (older cap, config edit) and the rescreen refill path
        # deploys into ANY free slot with no total-count check of its
        # own — without this an above-ceiling fleet only shrank on
        # restart. Journal + persist ONLY when something was pruned.
        try:
            pruned = self._prune_unfillable_slots(
                self.state.get("slots") or [],
                {str(k) for k in (self.state.get("active_bots") or {})})
            if pruned:
                log(self.state, {
                    "kind": "slots-reconciled",
                    "msg": "pruned empty slots above the fleet ceiling: "
                           + "; ".join(f"slot {sid} ({v}: {r})"
                                       for sid, v, r in pruned)[:300]})
                save_state(self.state)
        except Exception as exc:
            log(self.state, {"kind": "health-warn",
                             "msg": f"slot prune failed: {str(exc)[:120]}"})
        if not self.state["active_bots"]:
            return
        observed_all = observe_all_safe(self.state["active_bots"])
        self.state["last_observe"] = observed_all
        for slot_key, bot in list(self.state["active_bots"].items()):
            obs = observed_all.get(slot_key, observed_all.get(int(slot_key), {}))
            bot["observed"] = obs
            bot["last_observed"] = utcnow()
            # exit-profile projection (additive): the observe layer now
            # extracts the enriched grid_list exit fields (takeProfit /
            # stopLoss / trailing / positions exits — the fields
            # GridClient.set_exits edits) from the grid resource; mirror
            # them onto the bot record so the console fleet cards AND the
            # position optimizer's current_exits() see the CURRENT config.
            _exits = obs.get("exits")
            if isinstance(_exits, dict):
                bot["exits"] = _exits
            else:
                bot.pop("exits", None)
            # geometry projection (additive, gap-report 2026-09-07): the
            # observe layer also extracts the deployed channel/upsert
            # fields from the grid resource so ADOPTED bots (which land
            # with channel=None, upsert=None) gain their geometry after
            # one health cycle. Gated to adopted bots only — a non-
            # adopted bot's channel can be in-flight from a recent edit
            # (the 2h adjust_cooldown_h window), and we must not clobber
            # that. Adopted bots have no in-flight edit (they were
            # created on WT, not by this daemon) so overwriting is safe.
            if bot.get("adopted") and isinstance(obs.get("channel"), dict) \
                    and obs.get("channel"):
                bot["channel"] = obs["channel"]
            if bot.get("adopted") and isinstance(obs.get("upsert"), dict) \
                    and obs.get("upsert"):
                bot["upsert"] = obs["upsert"]
            if obs.get("error"):
                err = str(obs["error"])
                if is_gone_bot_error(err):
                    # missing-bot class: the status list itself loaded fine
                    # and the bot is not in it (deleted on the WT side).
                    # Warn once per episode, then free the slot after a
                    # continuous gone_clear_min — never per tick.
                    if self._reconcile_gone_bot(slot_key, bot):
                        continue
                else:
                    # transport class (browser/session down): fail closed —
                    # never counts toward removal, and any in-progress
                    # missing episode resets so only CONTINUOUS absence
                    # can clear a slot.
                    self._clear_gone_episode(bot)
                    log(self.state, {"kind": "health-warn", "slot": slot_key,
                                     "msg": f"observe error: {err[:160]}"})
                continue
            # observable again: an earlier missing-bot episode is over
            self._clear_gone_episode(bot)
            # carry-and-pray transition (gap-report 2026-09-07): when an
            # underwater book has been stuck longer than the token's
            # natural profitable-close time (k_carry × avg_holding_h,
            # from the stagnation_policy), free the slot and park the
            # bot in state["carry_pray"] with a server-side takeProfit
            # at break-even. The bot keeps running on WT; if it
            # recovers, the TP locks the recovery. Never raises.
            try:
                self._check_carry_pray_transition(
                    slot_key, bot, obs, time.time())
            except Exception as exc:
                log(self.state, {"kind": "carry-pray-error",
                                 "slot": slot_key,
                                 "msg": f"check failed: {str(exc)[:160]}"})
            policy = bot.get("stagnation_policy") or {}
            status = (obs.get("status") or "active").lower()
            price = obs.get("price")
            channel = bot.get("channel") or {}

            # reliability kill-flag review for existing bots
            archetype = ledger_key(bot.get("archetype")
                                   or bot.get("ticket", {}).get("regime"))
            flagged = refuse_new_archetype(
                self.reliability, archetype, min_samples=self._kill_min_samples())
            if flagged and not bot.get("reliability_flagged"):
                log(self.state, {"kind": "reliability-flag", "slot": slot_key,
                                 "msg": f"{bot.get('venue')}:{bot.get('symbol')} "
                                        f"archetype {archetype} recent_pf<1.0 — "
                                        f"flagged for rotation review"})
            bot["reliability_flagged"] = flagged

            # stagnation evaluation (drives rotation candidates) — with a
            # fresh-bot grace: a bot younger than one expected fill
            # interval has not had a fair chance to fill, so fill-count
            # stagnation is a false positive (a freshly swapped-in DOGE was
            # flagged "stagnant" 4 min after deploy). Rotation itself stays
            # guarded by policy.min_hold_h in the rescreen path; this keeps
            # the journal + transition sig honest.
            exp_fills = float(policy.get("expected_fills_per_24h") or 0)
            grace_h = min(12.0, max(1.0, 24.0 / exp_fills)) \
                if exp_fills > 0 else 1.0
            try:
                _since = datetime.fromisoformat(bot["since"]) \
                    if bot.get("since") else None
                age_h = (datetime.now(timezone.utc) - _since
                         ).total_seconds() / 3600 if _since else grace_h
            except Exception:
                age_h = grace_h
            if age_h < grace_h:
                stag, reasons = False, []
                bot.pop("stagnant_sig", None)
            else:
                stag, reasons = is_stagnant(
                    obs, policy, regime_now=obs.get("regime_now"),
                    score_drop=obs.get("score_drop", 0.0),
                    ladder_full=obs.get("ladder_full", False),
                    dd_vs_atr_band=obs.get("dd_vs_atr_band", 0.0))
            if stag:
                # log on TRANSITION only (first stagnant sweep per bot, or
                # when the reasons change): the 200-entry journal otherwise
                # fills with one line per stagnant bot every 60 s and all
                # screen/veto/deploy history ages out within the hour
                sig = "; ".join(reasons)[:160]
                if bot.get("stagnant_sig") != sig:
                    log(self.state, {"kind": "stagnant", "slot": slot_key,
                                     "msg": f"{bot.get('venue')}:{bot.get('symbol')}: "
                                            f"{sig}"})
                bot["stagnant_sig"] = sig
            else:
                bot.pop("stagnant_sig", None)

            # ── profit-side exit (the counterpart of the never-close-at-a-
            # loss rule): WunderTrading accepts takeProfit/stopLoss/trailing
            # fields on grid bots but does NOT enforce them server-side yet,
            # so the daemon owns the exit. When the bot's cumulative total
            # PnL (realized round-trips + mark PnL of open lines) reaches its
            # per-slot target AND every open line is at ≥ 0 (the stop below
            # closes all lines at market — no single line may realize a
            # loss), stop it at profit and free the slot for the next best
            # candidate. Fail-closed: unknown per-line state with open
            # positions means no exit.
            tp = bot.get("take_profit_usd")
            if tp is None:
                tp = self._default_take_profit(slot_key)
                if tp is not None:
                    bot["take_profit_usd"] = tp
            if tp and status not in STOPPED_STATES:
                try:
                    total_pnl = float(obs.get("realized_pnl") or 0.0)                         + float(obs.get("unrealized_pnl") or 0.0)
                except (TypeError, ValueError):
                    total_pnl = None
                open_lines = obs.get("open_lines")
                open_losing = obs.get("open_losing")
                if open_lines is None:
                    # per-line state unavailable with an open book → fail
                    # closed: a net-positive aggregate can hide one losing
                    # line whose close would realize a loss
                    safe_close = False
                elif open_lines == 0:  # flat book — nothing can lose
                    safe_close = True
                else:  # per-line state known → strict all-≥0 rule
                    safe_close = (open_losing or 0) == 0
                if total_pnl is not None and total_pnl >= float(tp) and safe_close:
                    stop_res = retry_grid_call(
                        grid_adapter.grid_stop, dry_run, bot["bot_code"],
                        "stop_and_close_all", dry_run=dry_run)
                    log(self.state, {
                        "kind": "profit-exit", "slot": slot_key,
                        "dry_run": dry_run,
                        "msg": f"{bot.get('venue')}:{bot.get('symbol')} "
                               f"cumulative PnL ${total_pnl:.2f} ≥ target "
                               f"${float(tp):.2f} — stopped at profit (all "
                               f"lines ≥ 0), slot recycled",
                        "result": stop_res})
                    record_outcome_safe(bot.get("decision_id"), {
                        "reason": "profit-exit",
                        "realized_pnl": round(total_pnl, 4),
                        "fills": obs.get("fills_24h") or 0,
                        "observed": {"total_pnl": round(total_pnl, 4)}})
                    bot["needs_reanalysis"] = True
                    continue

            out_of_channel = False
            if price is not None and channel.get("high") and channel.get("low"):
                out_of_channel = price > channel["high"] or price < channel["low"]
            if out_of_channel or status in STOPPED_STATES:
                # held under water: an out-of-channel bot with losing open
                # lines must keep TRADING instead of sitting dead outside
                # its grid — re-center the channel on the current price
                # (verified live 2026-09-06 on GRAM: the grid edit leaves
                # open positions and their entry prices untouched) so the
                # lines keep filling around the market and the position
                # can work its way back. 1 edit/6h rate limit inside
                # adjust_bot; this pre-check keeps the journal quiet.
                if out_of_channel and status not in STOPPED_STATES:
                    pnl = obs.get("unrealized_pnl")
                    losing = obs.get("open_losing")
                    if ((pnl is not None and float(pnl) < 0)
                            or (losing is not None and losing)):
                        last_adj = (self.state.get("last_adjust") or {}) \
                            .get(slot_key) or 0
                        if time.time() - float(last_adj) \
                                >= self._adjust_cooldown_s():
                            log(self.state, {
                                "kind": "recenter", "slot": slot_key,
                                "msg": f"{bot.get('venue')}:"
                                       f"{bot.get('symbol')} out-of-channel "
                                       f"with losing lines — re-centering "
                                       f"grid to keep trading (never close "
                                       f"at a loss)"})
                            self.adjust_bot(slot_key, dry_run)
                if not bot.get("needs_reanalysis"):
                    # transition only — same 60 s spam guard as `stagnant`
                    log(self.state, {"kind": "re-analysis", "slot": slot_key,
                                     "msg": f"{bot.get('venue')}:{bot.get('symbol')} "
                                            f"out-of-channel/stopped — mark for re-analysis"})
                bot["needs_reanalysis"] = True
                continue
            # back in channel and running: an earlier re-analysis mark is stale
            bot.pop("needs_reanalysis", None)

            # inside channel: drift → regime re-check → in-place adjust
            if price is not None and channel.get("mid") and channel.get("step_pct"):
                step = channel["step_pct"] / 100.0
                drift = abs(price - channel["mid"]) / (channel["mid"] * step) if step else 0
                if drift > float(self.config["watch"]["adjust_steps_threshold"]):
                    try:
                        regime_now = reclassify_regime(bot["venue"], bot["symbol"])
                    except Exception as exc:
                        log(self.state, {"kind": "health-warn", "slot": slot_key,
                                         "msg": f"regime recheck failed: {exc}"})
                        continue
                    if regime_now == policy.get("regime"):
                        self.adjust_bot(slot_key, dry_run)
                    else:
                        # transition-only logging: the classifier keeps
                        # answering the NEW regime every sweep while the
                        # policy still carries the old one, so the same
                        # one-direction flip used to re-journal every ~90 s
                        # and bury real events (same noise principle as
                        # `stagnant`/`re-analysis`). Log once per flip; a
                        # genuine flip BACK logs a new transition.
                        sig = f"{policy.get('regime')}→{regime_now}"
                        seen = getattr(self, "_regime_changed_sig", {})
                        if seen.get(slot_key) != sig:
                            seen[slot_key] = sig
                            self._regime_changed_sig = seen
                            log(self.state, {"kind": "regime-changed",
                                             "slot": slot_key,
                                             "msg": (f"{bot.get('venue')}:"
                                                     f"{bot.get('symbol')} "
                                                     f"{policy.get('regime')}"
                                                     f"→{regime_now} "
                                                     f"(no in-place "
                                                     f"adjust)")})
        # total-blindness escalation: when EVERY bot errors on EVERY sweep,
        # individual health-warn lines are too quiet — surface one loud
        # observe-outage event every ~30 blind minutes instead.
        bots = self.state["active_bots"] or {}
        if bots:
            # only TRANSPORT-class errors count as blindness here: a fleet
            # of gone bots (deleted on the WT side) is not an outage —
            # the status list itself loaded fine for them.
            errs = sum(1 for b in bots.values()
                       if (b.get("observed") or {}).get("error")
                       and not is_gone_bot_error(
                           (b.get("observed") or {}).get("error")))
            if errs and errs == len(bots):
                n = int(self.state.get("observe_error_sweeps", 0)) + 1
                self.state["observe_error_sweeps"] = n
                if n >= 30:
                    self.state["observe_error_sweeps"] = 0
                    log(self.state, {"kind": "observe-outage",
                                     "msg": f"all {len(bots)} bots unobservable for "
                                            f"~30 min — WT browser/session down "
                                            f"(watchdog should be restarting it)"})
            else:
                self.state["observe_error_sweeps"] = 0
        # demo-cap relearn (upward, per-paper-profile): the create-400
        # teacher only ratchets the learned demo (paper) grid-bot cap
        # DOWN. When live bots exceed it (manual UI deploys, plan change,
        # cap reset) the stale cap would veto every new deploy forever —
        # the platform demonstrably allows this many bots, so the cap
        # follows reality upward. The fleet's paper profiles can each
        # have a different cap; we group live bots by their
        # profile_code when available, else by exchange+paperTrading
        # against the profile snapshot.
        try:
            caps = self.state.get("demo_bot_caps") or {}
            if caps or self.state.get("demo_bot_cap"):
                live_by_code = {}
                profiles = self.state.get("profiles") or []
                paper_by_name = {p.get("name"): p for p in profiles
                                 if p.get("paperTrading")}
                for b in (grid_status_safe() or []):
                    code = b.get("code")
                    status = (b.get("status") or "").lower()
                    if status in STOPPED_STATES or not code:
                        continue
                    # best-effort: try profile_code directly, else resolve
                    # by name match
                    prof_code = code
                    if not any(p.get("code") == code for p in profiles):
                        nm = b.get("name")
                        if nm and nm in paper_by_name:
                            prof_code = paper_by_name[nm].get("code")
                    live_by_code[prof_code] = live_by_code.get(prof_code, 0) + 1
                for prof_code, live in live_by_code.items():
                    prev = (caps or {}).get(prof_code)
                    if prev is None:
                        continue
                    if live > prev:
                        caps[prof_code] = live
                        self.state["demo_bot_caps"] = caps
                        # mirror to legacy scalar
                        self.state["demo_bot_cap"] = max(
                            (int(v) for v in caps.values()
                             if isinstance(v, (int, float))), default=0)
                        log(self.state, {
                            "kind": "demo-cap-relearn",
                            "msg": f"{live} live demo (paper) grid bots on "
                                   f"profile {prof_code} above the learned "
                                   f"cap {prev} — cap raised to {live}, "
                                   f"deploy veto lifted for that profile",
                            "profile": prof_code,
                        })
        except Exception:
            pass
        save_state(self.state)

    def adjust_bot(self, slot_key, dry_run=True):
        bot = self.state["active_bots"].get(slot_key)
        if not bot or not bot.get("bot_code"):
            return
        last = self.state.get("last_adjust", {}).get(slot_key)
        now = time.time()
        cooldown_s = self._adjust_cooldown_s()
        if last and now - last < cooldown_s:
            # the fast optimizer re-proposes the same rate-limited
            # recenter every pass (~1 line/72s), which would evict the
            # whole 200-entry rolling journal within the cooldown window —
            # journal the skip at most once per hour per slot
            skips = getattr(self, "_adjust_skip_logged", {})
            if now - skips.get(slot_key, 0) >= 3600:
                skips[slot_key] = now
                self._adjust_skip_logged = skips
                log(self.state, {"kind": "adjust-skip", "slot": slot_key,
                                 "msg": f"rate limit (1 edit/"
                                        f"{cooldown_s / 3600:g}h) for "
                                        f"{bot.get('symbol')}"})
            return
        # failed-edit backoff — a WT-side edit error retries after the
        # short window, not the full cooldown; silent because the failure
        # was already journaled (don't spam, don't even fetch candles)
        if now - self.state.get("last_adjust_failed", {}).get(
                slot_key, 0) < ADJUST_FAILED_RETRY_S:
            return
        venue, symbol = bot["venue"], bot["symbol"]
        try:
            from market_regime import fetch_candles, compute_metrics
            cl = fetch_candles(venue, fetch_symbol(venue, symbol), "1h", 300,
                               "futures" if venue == "hyperliquid" else "spot")
            m = compute_metrics(cl)
        except Exception as exc:
            log(self.state, {"kind": "adjust-error", "slot": slot_key,
                             "msg": f"metrics fetch failed: {exc}"})
            return
        price, atr_pct = m["price"], m["atr_pct"]
        channel = bot.get("channel") or {}
        upsert_old = bot.get("upsert") or {}
        step_pct = channel.get("step_pct") or (upsert_old.get("gridPercentStep", 0) * 100)
        grids = channel.get("grids") or upsert_old.get("gridLevels", 0)
        amount = upsert_old.get("amountPerTrade", 0)
        grid_type = (bot.get("ticket") or {}).get("grid_type", "neutral")
        profile_code = bot.get("profile_code")
        pair_code = bot.get("pair_code") or upsert_old.get("pairCode")
        # the bot's stored deploy payload knows its REAL exchange (e.g.
        # BINANCE_FUTURES paper profile — gridMarket=derivative), which the
        # static venue default (binance→BINANCE→spot) gets wrong: a spot
        # market edit on a futures bot 400s on missing investmentRef/
        # investmentBase (live 2026-09-06 12:50:57Z, slot 3 GIGGLE)
        exchange_code = upsert_old.get("exchangeCode")
        try:
            new_upsert = grid_adapter.compute_upsert(
                symbol, venue, price, atr_pct, step_pct, grids, amount,
                grid_type, profile_code, pair_code,
                exchange_code=exchange_code)
        except Exception as exc:
            log(self.state, {"kind": "adjust-error", "slot": slot_key,
                             "msg": f"compute_upsert failed: {exc}"})
            return
        res = grid_edit_safe(bot["bot_code"], new_upsert, dry_run=dry_run)
        if not (res or {}).get("ok"):
            # WT rejected the edit (HTTP 500, validation, session loss) —
            # not applied: no cooldown burn, no bookkeeping mutation, no
            # "adjust" journal entry; retry allowed after the backoff above
            self.state.setdefault("last_adjust_failed", {})[slot_key] = now
            log(self.state, {
                "kind": "adjust-error", "slot": slot_key,
                "msg": "grid edit FAILED — not applied, retry allowed in "
                       f"<={ADJUST_FAILED_RETRY_S // 60} min: "
                       f"{_edit_error_summary(res)}",
                "result": res})
            return
        self.state.get("last_adjust_failed", {}).pop(slot_key, None)
        self.state.setdefault("last_adjust", {})[slot_key] = now
        bot["last_adjust"] = now
        bot["upsert"] = new_upsert
        bot["channel"] = {
            "low": new_upsert["lowPrice"], "mid": new_upsert["midPrice"],
            "high": new_upsert["highPrice"], "step_pct": step_pct,
            "atr_pct": atr_pct, "grids": grids,
        }
        log(self.state, {"kind": "adjust", "slot": slot_key, "dry_run": dry_run,
                         "msg": f"{venue}:{symbol} re-centered at {price} "
                                f"(step {step_pct}%, grids {grids})",
                         "result": res})

    def _default_take_profit(self, slot_key):
        """Take-profit target in USD for a bot record without one (adopted
        or pre-feature deploys): grid_defaults.take_profit_pct × the slot's
        budget. None when the slot or the budget is unknown — no target, no
        exit (fail-closed)."""
        slot = next((s for s in self.state["slots"]
                     if str(s["slot"]) == str(slot_key)), None)
        if not slot or not slot.get("balance"):
            return None
        pct = float((self.config.get("grid_defaults") or {})
                    .get("take_profit_pct") or 0.0)
        if pct <= 0:
            return None
        return round(float(slot["balance"]) * pct, 2)

    def _adjust_cooldown_s(self):
        """Per-bot in-place grid-edit rate limit in seconds —
        watch.adjust_cooldown_h (default 2 h; was a hardcoded 6 h — audit
        2026-09-06: a Δ+205.75% recenter rec sat ~4 h behind the window).
        One window covers BOTH the manual recenter path (adjust_bot /
        health_cycle) and the position-optimizer apply path: they share
        state["last_adjust"], so a manual edit throttles applies and vice
        versa."""
        try:
            h = float((self.config.get("watch") or {})
                      .get("adjust_cooldown_h", 2.0))
        except (TypeError, ValueError):
            h = 2.0
        return max(0.0, h) * 3600.0

    def _pnl_snapshot_interval_s(self):
        """Fleet PnL journal cadence in seconds (watch.pnl_snapshot_interval_s,
        default 300; 0 = off)."""
        try:
            s = float((self.config.get("watch") or {})
                      .get("pnl_snapshot_interval_s", 300))
        except (TypeError, ValueError):
            s = 300.0
        return max(0.0, s)

    def pnl_snapshot(self):
        """Fleet PnL block computed from the LATEST observe fold (pure
        state read — no network, never raises). Realized/unrealized are
        per-bot sums of the observe data; idle_usd = portfolio total minus
        the committed worst-case claims."""
        bots = {}
        realized = unrealized = fills = 0.0
        projected = 0.0
        for slot_key, bot in (self.state.get("active_bots") or {}).items():
            obs = (bot or {}).get("observed") or {}
            try:
                r = round(float(obs.get("realized_pnl") or 0.0), 4)
                u = round(float(obs.get("unrealized_pnl") or 0.0), 4)
                f = float(obs.get("fills_24h") or 0)
            except (TypeError, ValueError):
                r = u = 0.0
                f = 0.0
            proj = self._projected_24h_usd(bot)
            projected += proj
            try:
                slot_committed = float((self.state.get("committed")
                                       or {}).get(slot_key) or 0.0)
            except (TypeError, ValueError):
                slot_committed = 0.0
            bots[str(slot_key)] = {"symbol": (bot or {}).get("symbol"),
                                   "realized": r, "unrealized": u,
                                   "fills_24h": f,
                                   "projected_24h_usd": proj,
                                   "projected_annual_usd":
                                       round(proj * 365.0, 2),
                                   "projected_annual_return_pct":
                                       _return_pct(proj * 365.0,
                                                   slot_committed),
                                   "projected_double_days":
                                       _double_days(_return_pct(
                                           proj * 365.0, slot_committed))}
            realized += r
            unrealized += u
            fills += f
        committed = round(sum(float(v or 0)
                              for v in (self.state.get("committed")
                                        or {}).values()), 2)
        try:
            total = float((self.config.get("portfolio") or {})
                          .get("total_usd") or 0)
        except (TypeError, ValueError):
            total = 0.0
        realized = round(realized, 4)
        unrealized = round(unrealized, 4)
        return {"fleet": {
                    "realized": realized,
                    "unrealized": unrealized,
                    "net": round(realized + unrealized, 4),
                    "realized_net": round(realized + unrealized, 4),
                    "committed_usd": committed,
                    "idle_usd": round(max(total - committed, 0.0), 2),
                    "fills_24h": round(fills, 1),
                    "projected_24h_usd": round(projected, 2),
                    "projected_annual_usd": round(projected * 365.0, 2),
                    "projected_24h_return_pct":
                        _return_pct(projected, committed),
                    "projected_annual_return_pct":
                        _return_pct(projected * 365.0, committed),
                    "projected_annual_return_total_pct":
                        _return_pct(projected * 365.0, total),
                    "projected_double_days":
                        _double_days(_return_pct(projected * 365.0,
                                                 committed))},
                "bots": bots}

    def _projected_24h_usd(self, bot):
        """Model-based expected grid income per 24h for ONE bot, net of
        round-trip fees (observability only — never a gate).

        Fill semantics (verified live 2026-09-07):
          stagnation_policy.expected_fills_per_24h comes from
          policy/stagnation.simulate_grid_fills, which counts EVERY
          single-side grid-line crossing (one fill event each time
          consecutive closes cross any line). execution/observe.py's
          observed fills_24h instead counts CLOSED ROUND TRIPS — and one
          round trip = a buy-side fill matched by its sell-side fill,
          i.e. ~2 line crossings. Gross grid income accrues per ROUND
          TRIP (amountPerTrade × step spread), so the honest projection
          halves the crossing count:
              trips/24h ≈ expected_fills_per_24h / 2
              proj      = trips × amountPerTrade × (step% − rtfee%) / 100
        with the fee leg floored at 0 (a step thinner than the round-trip
        fee projects zero, not negative). Missing/NaN fields → 0.0, never
        a raise: pure fail-soft like the rest of the snapshot."""
        bot = bot if isinstance(bot, dict) else {}
        try:
            exp = float(((bot.get("stagnation_policy") or {})
                         .get("expected_fills_per_24h")) or 0)
        except (TypeError, ValueError):
            exp = 0.0
        if exp <= 0:
            return 0.0
        try:
            channel = bot.get("channel") or {}
            upsert = bot.get("upsert") or {}
            step = float(channel.get("step_pct")
                         or float(upsert.get("gridPercentStep") or 0) * 100
                         or 0)
            amt = float(upsert.get("amountPerTrade") or 0)
        except (TypeError, ValueError):
            step = amt = 0.0
        if step <= 0 or amt <= 0:
            return 0.0
        rt = _round_trip_fee_pct(bot.get("venue"))
        return round(exp * 0.5 * amt * max(step - rt, 0.0) / 100.0, 2)

    def _journal_pnl_snapshot(self):
        """One 'pnl-snapshot' journal event per watch.pnl_snapshot_interval_s
        in the manage loop — flows into the PocketBase journal
        write-through via log() and lands in state.json's ring. Pure
        observability: fail-soft, never crashes the loop."""
        snap = self.pnl_snapshot()
        f = snap["fleet"]
        log(self.state, {
            "kind": "pnl-snapshot",
            "msg": (f"fleet net ${f['net']:+.4f} (realized "
                    f"{f['realized']:+.4f}, unrealized "
                    f"{f['unrealized']:+.4f}) — committed "
                    f"${f['committed_usd']:.2f}, idle ${f['idle_usd']:.2f}, "
                    f"fills {f['fills_24h']:.0f}/24h, "
                    f"proj/24h ${f['projected_24h_usd']:.2f}"
                    + (f", proj/yr ${f['projected_annual_usd']:.2f} "
                       f"(~{f['projected_annual_return_pct']:.1f}%/yr on "
                       f"committed)"
                       if f.get("projected_annual_return_pct") is not None
                       else "")
                    + (f", double {_fmt_double(f['projected_double_days'])}"
                       if f.get("projected_double_days") is not None
                       else "")),
            **snap})

    # ── heartbeat: loop-health monitor + safe self-nudges ────────────

    def _heartbeat_cfg(self):
        """Merged heartbeat config (HEARTBEAT_DEFAULTS ← config.yaml)."""
        merged = dict(HEARTBEAT_DEFAULTS)
        try:
            cfg = self.config.get("heartbeat") or {}
            if isinstance(cfg, dict):
                merged.update({k: v for k, v in cfg.items() if v is not None})
        except Exception:
            pass
        return merged

    def _heartbeat_interval_s(self):
        try:
            return max(60.0, float(self._heartbeat_cfg().get("interval_s",
                                                            900)))
        except (TypeError, ValueError):
            return 900.0

    def _hb_check_tvcli(self):
        """(1) tvcli /health — the candle/confluence backbone."""
        base = (os.environ.get("TVCLI_SERVER")
                or (self.config.get("server") or {}).get("tvcli")
                or "http://127.0.0.1:8765")
        return _tvcli_health(base)

    def _hb_check_pocketbase(self):
        """(2) PocketBase sidecar (journal write-through)."""
        return (_pb() is not None), ("connected" if _pb() is not None
                                     else "client unavailable "
                                          "(PB_TOKEN/PB_ADMIN_EMAIL unset "
                                          "or .pocketbase down)")

    def _hb_check_wt_observe(self):
        """(3) WunderTrading observe fold — the last per-bot read."""
        lo = self.state.get("last_observe")
        if not isinstance(lo, dict) or not lo:
            return False, "no observe result yet"
        errs = [k for k, v in lo.items()
                if isinstance(v, dict) and v.get("error")]
        if errs:
            return False, f"observe errors: {','.join(sorted(errs)[:4])}"
        return True, f"{len(lo)} bot(s) observed clean"

    def _hb_check_screen_fresh(self, stale_s):
        """(4) screen cache age vs the staleness bound."""
        at = (self.state.get("screen_cache") or {}).get("at")
        try:
            age = time.time() - float(at)
        except (TypeError, ValueError):
            return False, "no screen cache"
        return (age <= stale_s), f"age {int(max(age, 0))}s (bound {int(stale_s)}s)"

    def _hb_check_optimizer_fresh(self):
        """(5) fast optimizer liveness — 3× its own interval.

        state["optimizer"]["last_at"] is an ISO timestamp string
        (optimizer.py stores report["at"]), so parse ISO first and fall
        back to a raw epoch float — float("2026-...T...") always raises,
        which used to make this check permanently fail (live 2026-09-07
        az00: every heartbeat nudged a healthy optimizer)."""
        if not getattr(self, "optimizer", None):
            return True, "optimizer unavailable (import failed) — skipped"
        opt_s = self.optimizer_interval_s() or 0
        if not opt_s:
            return True, "optimizer disabled — skipped"
        last = ((self.state.get("optimizer") or {}).get("last_at"))
        age = None
        try:
            age = time.time() - float(last)
        except (TypeError, ValueError):
            try:
                age = time.time() - datetime.fromisoformat(
                    str(last)).timestamp()
            except (ValueError, TypeError, OSError):
                age = None
        if age is None:
            return False, "no optimizer cycle yet"
        # 4× the interval (was 3×): the manage loop is sequential, so a
        # rescreen cycle (~every 15 min) holds the loop for 6–9 min while
        # the optimizer lane waits; at 3× (540s) the check flapped on
        # every post-rescreen heartbeat (live az00 2026-09-08: 542s vs
        # 540s) and the self-nudge it triggered was pure noise. 4× still
        # catches a genuinely dead lane within ~12 min.
        bound = 4 * opt_s
        return (age <= bound), f"last cycle {int(max(age, 0))}s ago (bound {int(bound)}s)"

    def _hb_check_po_fresh(self):
        """(6) position-optimizer per-bot recency — generous 2 h bound
        (2× the interval + cooldown design window)."""
        bots = self.state.get("active_bots") or {}
        if not bots:
            return True, "no active bots — skipped"
        stale = []
        for slot_key, bot in bots.items():
            po = (bot or {}).get("position_optimizer") or {}
            last = po.get("last_analyzed_at")
            try:
                age = time.time() - float(last)
            except (TypeError, ValueError):
                age = None
            if age is None or age > 7200:
                stale.append(str(slot_key))
        return (not stale), (f"not analyzed in 2h: {','.join(stale[:5])}"
                             if stale else "all bots analyzed in window")

    def _hb_check_journal_errors(self, warn_rate):
        """(7) journal -error fraction over the trailing hour."""
        now = time.time()
        in_hour = err = 0
        for ev in self.state.get("journal") or []:
            if not isinstance(ev, dict):
                continue
            try:
                age = now - datetime.fromisoformat(
                    str(ev.get("at"))).timestamp()
            except (ValueError, TypeError, OSError):
                continue  # unparseable/missing stamp → outside the window
            if age > 3600 or age < -300:
                continue
            in_hour += 1
            if str(ev.get("kind") or "").endswith("-error"):
                err += 1
        rate = (err / in_hour) if in_hour else 0.0
        return (rate < warn_rate), f"{err}/{in_hour} error entries ({rate:.0%})"

    def _hb_check_pnl_feed(self):
        """(8) pnl-snapshot feed — exists within 3× its interval (the extra
        slack absorbs one rescreen cycle blocking the sequential loop)."""
        pnl_s = self._pnl_snapshot_interval_s()
        if not pnl_s:
            return True, "pnl snapshots disabled — skipped"
        now = time.time()
        freshest = None
        for ev in reversed(self.state.get("journal") or []):
            if isinstance(ev, dict) and ev.get("kind") == "pnl-snapshot":
                try:
                    freshest = now - datetime.fromisoformat(
                        str(ev.get("at"))).timestamp()
                except (ValueError, TypeError, OSError):
                    freshest = None
                break
        if freshest is None:
            return False, "no pnl-snapshot in the journal ring"
        # 3× the interval (was 2×): same sequential-loop rationale as the
        # optimizer bound — a rescreen cycle blocks the snapshot lane for
        # 6–9 min, so 2× (600s) flapped chronically (live az00 2026-09-08:
        # 680s). 3× (900s) absorbs one blocked interval and still detects
        # a dead feed inside 15 min.
        return (freshest <= 3 * pnl_s), \
            f"last snapshot {int(max(freshest, 0))}s ago (bound {int(3 * pnl_s)}s)"

    def heartbeat_cycle(self, dry_run=True):
        """One loop-health pass: 8 fail-soft checks, a 0–100 score, and
        SAFE improving nudges (queue_rescreen/queue_optimize — the same
        auto paths the optimizer uses, demo-cap gated) when a feed goes
        stale. Never raises: a heartbeat failure must never crash the
        manage loop."""
        try:
            cfg = self._heartbeat_cfg()
            if not cfg.get("enabled", True):
                return None
            stale_s = 2400.0
            warn_rate = 0.3
            try:
                stale_s = float(cfg.get("screen_stale_s", 2400))
                warn_rate = float(cfg.get("error_rate_warn", 0.3))
            except (TypeError, ValueError):
                pass
            checks = {}
            for name, fn in (
                    ("tvcli_health", self._hb_check_tvcli),
                    ("pocketbase", self._hb_check_pocketbase),
                    ("wt_observe", self._hb_check_wt_observe),
                    ("screen_fresh", lambda: self._hb_check_screen_fresh(stale_s)),
                    ("optimizer_fresh", self._hb_check_optimizer_fresh),
                    ("po_fresh", self._hb_check_po_fresh),
                    ("journal_errors", lambda: self._hb_check_journal_errors(warn_rate)),
                    ("pnl_feed", self._hb_check_pnl_feed)):
                try:
                    ok, detail = fn()
                    checks[name] = {"ok": bool(ok),
                                    "detail": str(detail)[:120]}
                except Exception as exc:  # a broken check ≠ a broken loop
                    checks[name] = {"ok": False,
                                    "detail": f"check error: {str(exc)[:80]}"}
            passed = sum(1 for c in checks.values() if c["ok"])
            total = len(checks) or 1
            score = round(100 * passed / total)
            failed = [n for n, c in checks.items() if not c["ok"]]

            # improving actions — only when actionable and NOT dry-run
            nudges = []
            if not dry_run:
                try:
                    if not checks["screen_fresh"]["ok"] \
                            and self.queue_rescreen():
                        nudges.append("rescreen queued (screen cache stale)")
                except Exception:
                    pass
                try:
                    # set the flag directly (queue_optimize journals a
                    # "manual" message that would mislead here); the
                    # manage loop's fast-lane branch consumes it
                    if not checks["optimizer_fresh"]["ok"] \
                            and getattr(self, "optimizer", None):
                        with self._lock:
                            self._optimize_flag = True
                        nudges.append("optimize queued (optimizer stale)")
                except Exception:
                    pass
            for why in nudges:
                log(self.state, {"kind": "heartbeat-nudge", "msg": why})

            state_block = {"at": utcnow(), "score": score,
                           "checks": {k: dict(v) for k, v in checks.items()},
                           "nudges": list(nudges)}
            self.state["heartbeat"] = state_block
            msg = (f"heartbeat score {score}/100 · {passed}/{total} checks"
                   + (f" · {', '.join(failed)}" if failed else ""))
            log(self.state, {"kind": "heartbeat", "score": score,
                             "checks": {k: dict(v) for k, v in checks.items()},
                             "msg": msg})
            return state_block
        except Exception as exc:
            try:
                log(self.state, {"kind": "heartbeat-error",
                                "msg": str(exc)[:160]})
            except Exception:
                pass
            return None

    def _po_hunt_structure(self, bot):
        """Compact tvcli structure snapshot for ONE bot (PO hunt_fn).

        Feeds position_optimizer._analyze's rec["tvcli_structure"] with
        the 15m squeeze + choppiness read — the same tape the fast
        optimizer hunts — so slow-loop recommendations carry the current
        volatility context (is a recenter being proposed INTO a squeeze?).
        TV symbol convention mirrors screen/merge.py: BINANCE:<SYM>USDT
        for BOTH venues (HL perps mirror Binance symbols). Exactly 2
        skills, one /hunt batch each; the engine only calls this on
        re-analyzed (cooldown-gated) bots, so it stays cheap.
        Fail-soft: ANY import/hunt/extract failure returns None — a dead
        tvcli must never break the PO cycle."""
        try:
            symbol = str((bot or {}).get("symbol") or "").upper().strip()
            if not symbol:
                return None
            from merge import tv_hunt  # screen/merge.py (sys.path'd)
            tv_symbol = f"BINANCE:{symbol}USDT"
            out = {"at": time.time()}
            sq = (tv_hunt("squeeze", [tv_symbol],
                          timeframe="15m", bars=96).get(tv_symbol) or {})
            ch = (tv_hunt("choppiness", [tv_symbol],
                          timeframe="15m", bars=96).get(tv_symbol) or {})
            sqs = (sq.get("result") or {}).get("structure") or {}
            chs = (ch.get("result") or {}).get("structure") or {}
            out["squeeze"] = {"squeezeOn": bool(sqs.get("squeezeOn")),
                              "squeezeBars": sqs.get("squeezeBars"),
                              "momentumDir": sqs.get("momentumDir")}
            out["choppiness"] = {"chop": chs.get("chop"),
                                 "regime": chs.get("regime")}
            return out
        except Exception:
            return None

    # ── position-optimizer apply path (audit 2026-09-06 fix #2) ──────
    # Only edit-type GEOMETRY recs are ever eligible here: recenter /
    # widen / narrow / resize / revalue-grid (grid_edit path). Exit recs
    # (add-take-profit / add-trailing / add-stop-loss) have their OWN
    # opt-in apply path — the engine's set_exits seam (apply_fn, wired to
    # wt_library.grid_set_exits, exit-only live edit verified 2026-09-07)
    # — and never ride this geometry path; the exit payload keys are
    # stripped from applied geometry edits too. An edit leaves open
    # lines and their entry prices untouched (verified live 2026-09-06
    # on GRAM). an exit rides the
    # auto-apply path. An edit leaves open lines and their entry prices
    # untouched (verified live 2026-09-06 on GRAM).
    PO_GEOMETRY_RECS = ("recenter", "widen", "narrow", "resize",
                        "revalue-grid")
    PO_EXIT_PAYLOAD_KEYS = ("takeProfitUsd", "stopLossUsd",
                            "trailingActivationPct", "trailingExecutePct",
                            "positionsTrailing")
    PO_GEOMETRY_KEYS = ("lowPrice", "midPrice", "highPrice",
                        "gridPercentStep", "gridLevels", "amountPerTrade")

    def apply_position_optimizer_recs(self, recs, dry_run=True):
        """Daemon-side applier for the position-optimizer cycle output.

        With position_optimizer.apply: true, an edit-type GEOMETRY rec is
        applied through the existing grid-edit path when ALL of:
          * expected_delta_pct ≥ position_optimizer.min_improvement_pct
          * the bot is active and NOT stopped / in error
          * the shared per-bot adjust rate limit
            (watch.adjust_cooldown_h, default 2 h — last_adjust is written
            so the window covers manual recenters AND applies) allows
          * the daily apply cap (position_optimizer.max_apply_per_day)
            allows
        Every veto journals once (rate-limit skips share adjust_bot's
        1/hour throttle); a successful apply journals
        "position-optimizer-applied" and flips the rec's applied/applied_at
        (mirrored to the persisted PB recommendations record). Never
        raises — fail-soft by contract."""
        try:
            cfg = self.config.get("position_optimizer") or {}
            if not cfg.get("apply", False) or not recs:
                return []
            min_imp = float(cfg.get("min_improvement_pct", 2.0) or 0.0)
            max_day = int(cfg.get("max_apply_per_day", 4) or 0)
            cooldown_s = self._adjust_cooldown_s()
            now = time.time()
            book = self.state.setdefault("position_optimizer_applies", {})
            if book.get("day") != utcnow()[:10]:
                book.update({"day": utcnow()[:10], "count": 0,
                             "cap_logged": False})
            applied = []
            for rec in (recs or []):
                try:
                    if not isinstance(rec, dict):
                        continue
                    slot_key = str(rec.get("slot"))
                    rec_name = rec.get("recommendation")
                    # 1. edit-type GEOMETRY recs only — exit recs go
                    #    through the engine's own set_exits seam instead
                    #    (never this grid_edit path)
                    if rec_name not in self.PO_GEOMETRY_RECS:
                        continue
                    # 2. improvement gate (the same threshold the engine
                    #    journals/persists on)
                    try:
                        delta = float(rec.get("expected_delta_pct") or 0.0)
                    except (TypeError, ValueError):
                        delta = 0.0
                    if delta < min_imp:
                        continue
                    # 3. never apply on missing / rotated-out / stale recs
                    bot = (self.state.get("active_bots") or {}).get(slot_key)
                    if not bot or not bot.get("bot_code"):
                        continue
                    if rec.get("bot_code") and \
                            rec["bot_code"] != bot.get("bot_code"):
                        continue
                    # 4. never apply on error/stopped bots
                    status = ((bot.get("observed") or {})
                              .get("status") or "active").lower()
                    if status in STOPPED_STATES or status == "error":
                        log(self.state, {
                            "kind": "position-optimizer-skip",
                            "slot": slot_key,
                            "msg": f"{rec_name} {bot.get('symbol')} skipped "
                                   f"— bot status '{status}' (never edit a "
                                   f"stopped/errored bot)"})
                        continue
                    # 5. shared per-bot adjust rate limit
                    last = (self.state.get("last_adjust") or {}) \
                        .get(slot_key)
                    if last and now - float(last) < cooldown_s:
                        skips = getattr(self, "_adjust_skip_logged", {})
                        if now - skips.get(slot_key, 0) >= 3600:
                            skips[slot_key] = now
                            self._adjust_skip_logged = skips
                            log(self.state, {
                                "kind": "position-optimizer-skip",
                                "slot": slot_key,
                                "msg": f"{rec_name} {bot.get('symbol')} "
                                       f"skipped — adjust rate limit "
                                       f"(1 edit/{cooldown_s / 3600:g}h, "
                                       f"shared with manual recenters)"})
                        continue
                    # 5b. failed-edit backoff — a WT-side edit error is
                    #     retried at most once per 10 min (silent: the
                    #     failure was already journaled)
                    failed_at = (self.state.get("last_adjust_failed")
                                 or {}).get(slot_key, 0)
                    if now - failed_at < ADJUST_FAILED_RETRY_S:
                        continue
                    # 6. daily apply cap
                    if int(book.get("count") or 0) >= max_day:
                        if not book.get("cap_logged"):
                            book["cap_logged"] = True
                            log(self.state, {
                                "kind": "position-optimizer-skip",
                                "msg": f"daily position-optimizer apply cap "
                                       f"{max_day} reached — further recs "
                                       f"advisory until tomorrow"})
                        continue
                    # 7. apply through the existing edit path — GEOMETRY
                    #    ONLY: strip any exit keys the advisory payload
                    #    embedded (hard safety, belt + braces)
                    payload = dict(((rec.get("action") or {})
                                    .get("payload")) or {})
                    for k in self.PO_EXIT_PAYLOAD_KEYS:
                        payload.pop(k, None)
                    if not all(payload.get(k) is not None
                               for k in self.PO_GEOMETRY_KEYS):
                        continue  # incomplete geometry — nothing to edit
                    res = grid_edit_safe(bot["bot_code"], payload,
                                         dry_run=dry_run)
                    if not (res or {}).get("ok"):
                        # WT rejected the edit — NOT applied (live
                        # incident 2026-09-06: an HTTP 500 was journaled
                        # as applied): no cooldown burn, no count, no
                        # applied/applied_at flip, no PB update; retry
                        # allowed after the 5b backoff above
                        self.state.setdefault(
                            "last_adjust_failed", {})[slot_key] = now
                        log(self.state, {
                            "kind": "position-optimizer-error",
                            "slot": slot_key,
                            "symbol": bot.get("symbol"),
                            "recommendation": rec_name,
                            "msg": f"{rec_name} apply FAILED on WT — not "
                                   f"applied, retry allowed in "
                                   f"<={ADJUST_FAILED_RETRY_S // 60} min: "
                                   f"{_edit_error_summary(res)}",
                            "result": res})
                        continue
                    self.state.get("last_adjust_failed", {}).pop(slot_key,
                                                                 None)
                    self.state.setdefault("last_adjust", {})[slot_key] = now
                    bot["last_adjust"] = now
                    bot["upsert"] = {**(bot.get("upsert") or {}), **payload}
                    ch = bot.get("channel") or {}
                    bot["channel"] = {
                        "low": payload["lowPrice"],
                        "mid": payload["midPrice"],
                        "high": payload["highPrice"],
                        "step_pct": payload["gridPercentStep"] * 100.0,
                        "atr_pct": ch.get("atr_pct"),
                        "grids": payload["gridLevels"],
                    }
                    if not dry_run:
                        book["count"] = int(book.get("count") or 0) + 1
                    rec["applied"] = True
                    rec["applied_at"] = utcnow()
                    log(self.state, {
                        "kind": "position-optimizer-applied",
                        "slot": slot_key, "symbol": bot.get("symbol"),
                        "recommendation": rec_name,
                        "expected_delta_pct": delta,
                        "dry_run": bool(dry_run),
                        "msg": f"{rec_name} applied to "
                               f"{bot.get('venue')}:{bot.get('symbol')} "
                               f"(Δ{delta:+.2f}%, "
                               f"{int(book.get('count') or 0)}/{max_day} "
                               f"today)",
                        "result": res})
                    self._pb_recommendation_update(rec)
                    applied.append(rec)
                except Exception as exc:
                    log(self.state, {
                        "kind": "position-optimizer-error",
                        "msg": f"rec apply failed: {str(exc)[:160]}"})
            return applied
        except Exception as exc:
            log(self.state, {"kind": "position-optimizer-error",
                             "msg": f"apply pass failed: {str(exc)[:160]}"})
            return []

    def _pb_recommendation_update(self, rec):
        """Flip applied/applied_at on the persisted PB recommendations
        record (matched on the engine's recommendation uuid). Non-fatal:
        None when PB is off, the rec was never persisted, or the write
        fails — the apply already lives in the journal."""
        pb = _pb()
        if pb is None or not rec.get("id"):
            return None
        try:
            return pb.recommendation_update(
                rec["id"], {"applied": bool(rec.get("applied")),
                            "applied_at": rec.get("applied_at")})
        except Exception:
            return None

    # ── rotation ───────────────────────────────────────────────────────
    def execute_rotation(self, slot_key, challenger, dry_run=True):
        incumbent = self.state["active_bots"].get(slot_key)
        if not incumbent:
            return False
        policy = incumbent.get("stagnation_policy") or {}
        observed = incumbent.get("observed") or {}
        manual = bool(incumbent.get("force_rotate"))
        if manual:
            ok_rot, reasons = True, [
                "optimizer swap (fast lane)"
                if incumbent.get("optimizer_swap")
                else "manual rotate (ctl /rotate)"]
        else:
            try:
                ok_rot, reasons = should_rotate(
                    challenger, incumbent, policy, observed, time.time(),
                    needs_reanalysis=bool(incumbent.get("needs_reanalysis")))
            except Exception as exc:
                ok_rot, reasons = False, [f"rotation eval failed: {exc}"]
        if not ok_rot:
            log(self.state, {"kind": "rotation-veto", "slot": slot_key,
                             "msg": f"{'; '.join(reasons)[:160]}"})
            return False
        # NEVER realize a loss to reallocate capital: every rotation stops
        # the incumbent with stop_and_close_all, which closes its open grid
        # lines at market. An incumbent holding ANY under-water line keeps
        # running — its grid keeps working the position back toward
        # break-even. Per-LINE check first (observed.open_losing, from
        # observe._line_pnl): a net-positive aggregate can still hide one
        # losing line that the close would realize. Aggregate unrealized_pnl
        # is the backstop when per-line data is unavailable. This is the
        # hard rule for optimizer swaps AND rescreen rotations AND manual
        # /rotate alike.
        losing = observed.get("open_losing")
        pnl = observed.get("unrealized_pnl")
        if observed.get("error"):
            log(self.state, {"kind": "loss-veto", "slot": slot_key,
                             "msg": "observe error — position PnL unknown, "
                                    "not closing blind; incumbent kept"})
            return False
        if losing is not None and losing:
            log(self.state, {"kind": "loss-veto", "slot": slot_key,
                             "msg": f"{incumbent.get('venue')}:"
                                    f"{incumbent.get('symbol')}: {losing} open "
                                    f"line(s) under water "
                                    f"(${float(pnl or 0):.2f} unrealized) — "
                                    f"never close at a loss; incumbent kept, "
                                    f"challenger not deployed"})
            return False
        if losing is None and pnl is not None and float(pnl) < 0:
            log(self.state, {"kind": "loss-veto", "slot": slot_key,
                             "msg": f"{incumbent.get('venue')}:"
                                    f"{incumbent.get('symbol')}: open "
                                    f"position at loss "
                                    f"(${float(pnl):.2f} unrealized) — "
                                    f"never close at a loss; incumbent kept, "
                                    f"challenger not deployed"})
            return False
        key = f"{incumbent.get('venue')}:{incumbent.get('symbol')}"
        cooldown_ok = time.time() >= self.state["cooldowns_until"].get(key, 0)
        slot = next((s for s in self.state["slots"]
                     if s["slot"] == int(slot_key)), None)
        if slot is None:
            log(self.state, {"kind": "rotation-veto", "slot": slot_key,
                             "msg": "slot missing from plan"})
            return False
        action, ticket, payloads, brief = self.plan_candidate(
            challenger, slot, dry_run, is_rotation=True,
            incumbent=incumbent, cooldown_ok=cooldown_ok)
        if action is None:
            return False
        # stop → verify → delete → cooldown → clear → deploy challenger
        bot_code = incumbent.get("bot_code")
        # an already-stopped incumbent (health_cycle flags stopped bots for
        # re-analysis) cannot be stopped again — WT answers 400 "already
        # stopped", which used to veto the rotation forever. Treat a
        # verified-stopped bot as successfully stopped.
        already_stopped = False
        if not dry_run:
            try:
                for b in grid_status_safe() or []:
                    if b.get("code") == bot_code and                             (b.get("status") or "").lower() in STOPPED_STATES:
                        already_stopped = True
                        break
            except Exception:
                pass
        if already_stopped:
            stop_res = {"ok": True, "status": "already stopped", "skipped": True}
        else:
            stop_res = retry_grid_call(grid_adapter.grid_stop, dry_run, bot_code,
                                       "stop_and_close_all", dry_run=dry_run)
        log(self.state, {"kind": "rotation-stop", "slot": slot_key,
                         "msg": f"stop {bot_code}", "result": stop_res})
        if dry_run:
            status_res = {"ok": True, "dry_run": True, "status": "stopped"}
        else:
            status_res = None
            try:
                gs = grid_status_safe()
                for b in gs:
                    if b.get("code") == bot_code:
                        st = (b.get("status") or "").lower()
                        status_res = {"ok": st in STOPPED_STATES,
                                      "status": st}
                        break
            except Exception as exc:
                status_res = {"ok": False, "error": str(exc)[:160]}
        stop_failed = not (stop_res or {}).get("ok") and "already stopped"             not in json.dumps(stop_res or {}).lower()
        if not dry_run and stop_failed:
            log(self.state, {"kind": "rotation-veto", "slot": slot_key,
                             "msg": f"stop failed for {bot_code} — incumbent "
                                    f"kept, challenger not deployed"})
            return False
        if not dry_run and status_res is not None and \
                not status_res.get("ok"):
            # bot listed but still running — never delete a live bot
            log(self.state, {"kind": "rotation-veto", "slot": slot_key,
                             "msg": f"stop not verified for {bot_code} "
                                    f"(status={status_res.get('status')}) — "
                                    f"incumbent kept"})
            return False
        if status_res is None and not dry_run:
            # stop reported ok but the bot is not listed — deletion of an
            # already-gone bot is harmless, so proceed with a warning
            log(self.state, {"kind": "rotation-warn", "slot": slot_key,
                             "msg": f"could not verify stop for {bot_code} "
                                    f"(not listed) — proceeding"})
        # BEFORE deleting: export the incumbent's closed round-trips. Once
        # the bot is gone on WunderTrading its positions-history is
        # unreachable, so the reliability recompute (active bots only) would
        # drop every trip closed since the last 24h cron. Archive them under
        # the incumbent's archetype so the ledger keeps learning, and use the
        # realized sum as the decision outcome's true PnL (the old fallback
        # recorded a mis-scaled unrealized value — realized_pnl_24h never
        # existed in observed).
        realized_pnl = None
        if not dry_run:
            try:
                final_trades = bot_trades(bot_code) or []
                realized_pnl = round(sum(t.get("pnl_usd", 0.0)
                                         for t in final_trades), 4)
                if archive_trades(final_trades,
                                  ledger_key(incumbent.get("archetype"))):
                    log(self.state, {"kind": "reliability-archive", "slot": slot_key,
                                     "msg": f"archived {len(final_trades)} closed "
                                            f"round-trips for "
                                            f"{incumbent.get('archetype') or 'unknown'}"})
            except Exception as exc:
                log(self.state, {"kind": "reliability-archive-error",
                                 "slot": slot_key, "msg": str(exc)[:160]})
        del_res = retry_grid_call(grid_adapter.grid_delete, dry_run, bot_code,
                                  dry_run=dry_run)
        log(self.state, {"kind": "rotation-delete", "slot": slot_key,
                         "msg": f"delete {bot_code}", "result": del_res})
        self.state["cooldowns_until"][key] = time.time() + policy.get(
            "cooldown_h", 24.0) * 3600
        self.state["active_bots"].pop(slot_key, None)
        self.state["committed"].pop(slot_key, None)
        # reflect contract: {reason, realized_pnl, fills, holding_h, observed}
        obs_out = incumbent.get("observed") or {}
        try:
            since = datetime.fromisoformat(incumbent.get("since") or "") \
                if incumbent.get("since") else None
            holding_h = round((datetime.now(timezone.utc) - since).total_seconds()
                              / 3600, 1) if since else None
        except Exception:
            holding_h = None
        record_outcome_safe(incumbent.get("decision_id"), {
            "reason": "stagnant" if reasons and "fills" in reasons[0]
            else (reasons[0][:60] if reasons else "rotated"),
            "realized_pnl": realized_pnl if realized_pnl is not None
            else obs_out.get("unrealized_pnl"),
            "fills": obs_out.get("fills_24h"),
            "holding_h": holding_h,
            "observed": obs_out,
            "challenger": f"{challenger.get('venue')}:{challenger.get('symbol')}",
        })
        incumbent.pop("force_rotate", None)
        self.commit_deploy(action, ticket, payloads, brief, challenger, slot,
                           dry_run)
        if not dry_run and str(slot["slot"]) not in self.state["active_bots"]:
            # challenger create failed after the incumbent was removed:
            # the slot stays empty and the next rescreen refills it — make
            # the gap visible instead of logging a plain success
            log(self.state, {"kind": "rotation-error", "slot": slot_key,
                             "msg": f"challenger "
                                    f"{challenger.get('venue')}:"
                                    f"{challenger.get('symbol')} deploy "
                                    f"failed after incumbent removal — slot "
                                    f"left empty, next rescreen will refill"})
        log(self.state, {"kind": "rotate", "slot": slot_key,
                         "msg": f"{key} → {challenger.get('venue')}:"
                                f"{challenger.get('symbol')} "
                                f"({'; '.join(reasons)[:120]})"})
        save_state(self.state)
        return True

    # ── first-run adoption ─────────────────────────────────────────────
    def adopt_existing(self, dry_run=True):
        if not self.config.get("adopt_existing"):
            return
        bots = grid_status_safe()
        if not bots:
            return
        if not self.state.get("slots"):
            self.state["slots"] = self.plan_slots()["slots"]
        occupied = set(self.state["active_bots"])
        free = [s for s in self.state["slots"]
                if str(s["slot"]) not in occupied]
        # never double-track: skip bots already known to state (by code) and
        # symbols already active in another slot (duplicate deploy)
        tracked_codes = {bb.get("bot_code") for bb in
                         self.state["active_bots"].values()}
        tracked_keys = {f"{bb.get('venue')}:{bb.get('symbol')}" for bb in
                        self.state["active_bots"].values()}
        for b in bots:
            if not b.get("paperTrading"):
                continue
            if (b.get("status") or "active").lower() != "active":
                continue
            if b.get("code") in tracked_codes:
                continue  # already tracked in a slot
            venue = venue_from_exchange(b.get("exchange"))
            symbol = symbol_from_pair(b.get("pair") or b.get("pairCode"), venue)
            funded = self.config["portfolio"]["venues"]
            if venue not in funded or not symbol or not free:
                log(self.state, {"kind": "adopt-warn",
                                 "msg": f"paper bot {b.get('code')} "
                                        f"({b.get('pair')}) left running — "
                                        f"not adopted (no venue/slot match)"})
                continue
            if f"{venue}:{symbol}" in tracked_keys:
                log(self.state, {"kind": "adopt-warn",
                                 "msg": f"paper bot {b.get('code')} "
                                        f"({b.get('pair')}) left running — "
                                        f"{venue}:{symbol} already active in "
                                        f"another slot"})
                continue
            slot = next((s for s in free if s["venue"] == venue), None)
            if slot is None:
                log(self.state, {"kind": "adopt-warn",
                                 "msg": f"paper bot {b.get('code')} "
                                        f"({b.get('pair')}) left running — "
                                        f"no free {venue} slot"})
                continue
            free.remove(slot)
            # derive the stagnation policy from LIVE metrics: step from the
            # current ATR (grid_defaults factors, like every fresh deploy)
            # and the regime from the same candles — adopted bots used to
            # carry hardcoded step 0.5% / regime "neutral" thresholds until
            # their first re-analysis
            regime, step = "neutral", 0.5
            try:
                from market_regime import (fetch_candles, compute_metrics,
                                            classify)
                cl = fetch_candles(venue, fetch_symbol(venue, symbol), "1h", 300,
                                   "futures" if venue == "hyperliquid" else "spot")
                m = compute_metrics(cl)
                regime, _ev = classify(m)
                gd = self.config.get("grid_defaults", {}) or {}
                step = round(min(gd.get("step_max", 2.0),
                                max(gd.get("step_min", 0.1),
                                    (m.get("atr_pct") or 1.0)
                                    * gd.get("step_factor", 0.5))), 3)
            except Exception:
                cl = []
            try:
                policy = derive_policy([c[3] for c in cl], "1h", step, regime)
            except Exception as exc:
                policy = {"error": str(exc)[:120]}
            ticket = {"symbol": symbol, "venue": venue,
                      "decision": "GO", "grid_type": "neutral",
                      "regime": regime}
            # record the adoption as a decision so a later rotation can
            # attach an outcome (adopted bots used to have decision_id=None
            # and never fed the memory/reflection loop)
            decision_id = record_decision_safe(
                ticket,
                {"symbol": symbol, "venue": venue, "slot": slot,
                 "regime": regime, "stagnation_policy": policy},
                {"kind": "adopted", "slot": slot["slot"], "symbol": symbol,
                 "venue": venue,
                 "msg": f"adopted bot {b.get('code')}"})
            self.state["active_bots"][str(slot["slot"])] = {
                "symbol": symbol, "venue": venue,
                "since": utcnow(), "adopted": True,
                "bot_code": b.get("code"),
                "ticket": ticket,
                "score_final": None,
                "archetype": ledger_key(regime),
                "stagnation_policy": policy,
                "channel": None,
                "profile_code": None, "pair_code": b.get("pairCode"),
                "upsert": None, "decision_id": decision_id,
            }
            # adopted bots consume their slot's worst-case budget too —
            # without a committed entry open_slot's spare-capital math
            # over-deploys (CHIP 2026-09-05 was $50 invisible), and a
            # fresh occupancy must not inherit the previous occupant's
            # fill tracker (same staleness commit_deploy now clears)
            max_c = slot.get("max_commitment")
            if max_c:
                self.state.setdefault("committed", {})[str(slot["slot"])] = \
                    float(max_c)
            self.state.setdefault("optimizer", {}) \
                .setdefault("trackers", {}).pop(str(slot["slot"]), None)
            log(self.state, {"kind": "adopted", "slot": slot["slot"],
                             "msg": f"{venue}:{symbol} bot {b.get('code')} "
                                    f"adopted into slot {slot['slot']}"})
        save_state(self.state)

    # ── reliability cron (24h) ────────────────────────────────────────
    def reliability_cycle(self):
        """Refresh the reliability ledger, then reload it into the daemon.

        Measurement: export each active bot's closed round-trips
        (`bot_trades`), aggregate per archetype (`archetype_stats`), and
        merge into `state/reliability.json` — zero-sample archetypes never
        erase existing entries, so history from already-deleted bots
        survives. The reloaded ledger gates sizing escalation
        (base → probe → full) and archetype kill-flags. Never raises.
        """
        try:
            # seed with archived (rotated-out) bots' trades so deleted bots
            # keep feeding the ledger, then extend with the active bots
            by_archetype = {}
            for arch, rows in archived_by_archetype().items():
                by_archetype.setdefault(ledger_key(arch), []).extend(rows)
            for bot in (self.state.get("active_bots") or {}).values():
                code = bot.get("bot_code")
                if not code:
                    continue
                by_archetype.setdefault(
                    ledger_key(bot.get("archetype") or "unknown"),
                    []).extend(bot_trades(code))
            stats = archetype_stats(by_archetype) if by_archetype else {}
            merged = dict(self.reliability or {})
            fresh = 0
            for arch, st in (stats or {}).items():
                if st.get("samples"):
                    merged[arch] = st
                    fresh += 1
            if fresh:
                save_reliability(merged)
                log(self.state, {"kind": "reliability",
                                 "msg": f"computed {fresh} archetype(s) from "
                                        f"closed round-trips"})
        except Exception as exc:
            log(self.state, {"kind": "reliability-error",
                             "msg": f"compute failed: {exc}"})
        self.reliability = reliability_load_safe()
        self.state["reliability"] = self.reliability
        log(self.state, {"kind": "reliability",
                         "msg": f"reloaded ({len(self.reliability)} keys)"})
        save_state(self.state)

    def _prune_unfillable_slots(self, slots, active):
        """Drop EMPTY slots that can never host a bot. Mutates `slots`
        in place, returns [(slot, venue, reason), …]. Fail-closed: a slot
        is only dropped on a POSITIVE blocker —
          * the TOTAL slot count is above the fleet ceiling
            (portfolio.slots_hard_max when any dynamic venue is
            configured, else portfolio.slots_max): the highest-numbered
            EMPTY slots are dropped until total == ceiling — VENUE-AWARE:
            an EMPTY slot that is the last remaining slot (occupied or
            empty) of a FUNDED venue (portfolio.venues entry with
            balance_usd > 0) is never dropped while another empty slot
            whose venue keeps another slot is available (a venue stripped
            of its last slot can never host a deploy again — refill only
            fills same-venue free slots and open_slot refuses at the
            ceiling). If every droppable empty is protected (e.g. the
            ceiling sits below the number of funded venues), the plain
            highest-empty rule applies: the ceiling is a hard operator
            cap and never keeps the fleet above it, or
          * the fleet is at the learned demo (paper) grid-bot cap (no new
            bot can be created on ANY venue until one stops), or
          * the slot's venue is at its plan tier cap (e.g. binance free
            tier: 1 active grid bot — the venue is rotation-only).
        Occupied slots are never candidates; unknown capacity data prunes
        nothing; headroom on any path keeps the slot.
        """
        pruned = []
        try:
            # fleet ceiling: the operator's TOTAL-slot-count cap
            # (portfolio.slots_hard_max when any dynamic venue is
            # configured, else portfolio.slots_max — the same
            # total-count semantics open_slot enforces on growth). A
            # fleet already above it (state grew under an older cap, or a
            # config edit lowered the ceiling — the rescreen refill
            # path deploys into ANY free slot with no total-count check
            # of its own) otherwise only shrank on the next restart.
            # Fail-closed: prune the HIGHEST-numbered EMPTY slots until
            # total == ceiling; occupied slots are never touched, and at
            # or below the ceiling nothing is pruned.
            p = self.config.get("portfolio") or {}
            dyn = p.get("dynamic_slot_venues") or []
            active_set = {str(k) for k in (active or set())}
            try:
                fleet_cap = int(p.get("slots_hard_max", 16) if dyn
                                else p.get("slots_max", 5))
            except (TypeError, ValueError):
                fleet_cap = None

            def _slot_num(v):
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return -1

            if fleet_cap is not None and len(slots) > fleet_cap:
                over = len(slots) - fleet_cap
                empties = sorted(
                    (s for s in slots
                     if str(s.get("slot")) not in active_set),
                    key=lambda s: _slot_num(s.get("slot")), reverse=True)
                # venue-aware protection: the LAST remaining slot of a
                # FUNDED venue (portfolio.venues with balance_usd > 0) is
                # never the drop of choice while another empty exists —
                # dropping it would strand the venue's sleeve (refill
                # only fills same-venue free slots; open_slot refuses at
                # the ceiling), so binance-screened candidates could
                # never deploy again. Protected empties are only
                # considered AFTER every unprotected one (and only then
                # when the hard ceiling still demands a drop) — the
                # operator's cap always wins over venue protection.
                funded_venues = set()
                try:
                    venues_cfg = p.get("venues") or {}
                    for name, vc in venues_cfg.items():
                        try:
                            if float((vc or {}).get("balance_usd")
                                    or 0) > 0:
                                funded_venues.add(str(name))
                        except (TypeError, ValueError):
                            continue
                except Exception:
                    pass
                venue_counts = {}
                for s in slots:
                    v = str(s.get("venue"))
                    venue_counts[v] = venue_counts.get(v, 0) + 1

                def _venue_protected(s):
                    v = str(s.get("venue"))
                    return v in funded_venues and venue_counts.get(v, 0) <= 1

                ordered = ([s for s in empties if not _venue_protected(s)]
                           + [s for s in empties if _venue_protected(s)])
                for s in ordered[:over]:
                    total = len(slots)
                    slots.remove(s)
                    pruned.append(
                        (s.get("slot"), s.get("venue"),
                         f"fleet ceiling {total}/{fleet_cap}"))
            cap = self.state.get("demo_bot_cap")
            at_demo_cap = bool(cap) and len(active or {}) >= int(cap)
            for s in list(slots):
                if str(s.get("slot")) in (active or set()):
                    continue  # live bot — never touched
                reason = None
                if at_demo_cap:
                    reason = (f"demo-bot cap {len(active)}/{int(cap)}")
                else:
                    try:
                        blocked = self.venue_capacity_block(
                            {"venue": s.get("venue"), "symbol": ""},
                            self.state.get("capacity"))
                    except Exception:
                        blocked = None
                    if blocked and str(blocked).startswith("plan cap"):
                        reason = str(blocked)
                if reason:
                    slots.remove(s)
                    pruned.append((s.get("slot"), s.get("venue"), reason))
        except Exception:
            pass
        return pruned

    def reconcile_slots(self):
        """Re-normalize persisted slot budgets to the CURRENT config.

        Slots persist in state.json (open_slot grows the plan, rotations
        free them) — but nothing ever re-read portfolio.venues into them,
        so a config edit + daemon restart left the fleet sizing from the
        OLD sleeves while the console config editor showed the new ones
        (the console-vs-backend drift a restart was supposed to close).
        Keep the persisted slot COUNT and venue assignment (that is the
        runtime truth — which slots exist), only recompute each venue's
        per-slot budget: scaled sleeve / venue slot count (dynamic venues
        floor at portfolio.min_slot_usd — a dynamic slot opened beyond the
        sleeve must not be shrunk back below the exchange floor on restart).
        Never raises.

        Two 2026-09-06 audit heals ride along:
          * config-consolidation prune — EMPTY slots that can never host a
            bot (venue at its plan tier cap, or the fleet at the learned
            demo-bot cap) are dropped: they reserve worst-case capital
            against the deployable ceiling and keep the optimizer nudging
            futile refill rescreens ($200 of the $600 fund sat in phantom
            slots 4/6/8/9 for 27+ slot-hours). Occupied slots are NEVER
            touched — live bots never lose their slot id — and open_slot
            can always recreate capacity later (ids continue from the max).
          * stranded-venue rebalance (2026-09-07) — a venue FUNDED in
            portfolio.venues (balance_usd > 0) that holds ZERO slots can
            never host a deploy again: the refill path only fills
            same-venue free slots and open_slot refuses at the fleet
            ceiling (live: the ceiling prune had dropped binance's only
            slot, stranding the $120 sleeve). The reconcile converts the
            highest-numbered EMPTY slot of a venue that keeps another
            empty one to the stranded venue — never an occupied slot,
            never a source venue's last empty slot, never growing the
            total slot count — and the budget loop re-normalizes it
            through the same per-venue math below.
          * committed clamp — an ACTIVE bot's worst-case claim is clamped
            to its slot's new max_commitment when a config SHRINK lowers
            the cap (otherwise a phantom claim eats the deployable
            ceiling); it is never raised on growth — the deployed grid
            still runs at its old sizing until the next deploy into the
            slot re-commits the honest worst case.
        """
        try:
            p = self.config["portfolio"]
            total = float(p.get("total_usd", 0) or 0)
            venues = {k: float(v.get("balance_usd", 0) or 0)
                      for k, v in (p.get("venues") or {}).items()}
            vsum = sum(venues.values())
            slots = self.state.get("slots") or []
            if not slots or total <= 0 or vsum <= 0:
                return
            active = {str(k) for k in (self.state.get("active_bots")
                                      or {})}
            pruned = self._prune_unfillable_slots(slots, active)
            scale = total / vsum
            dynamic = set(p.get("dynamic_slot_venues") or [])
            min_slot_usd = float(p.get("min_slot_usd", 100.0))
            # stranded-venue rebalance: a venue FUNDED in config
            # (balance_usd > 0) with ZERO slots is dead capital — the
            # refill path only fills same-venue free slots and open_slot
            # refuses at the fleet ceiling (live 2026-09-07: the ceiling
            # prune had dropped binance's only slot, so the $120 sleeve
            # could never rotate). Convert the highest-numbered EMPTY
            # slot of a venue that keeps another empty one; the budget
            # loop below re-normalizes balance / max_commitment /
            # venue_sleeve / venue_slots through the same per-venue math
            # (dynamic venues floor at min_slot_usd). Fail-closed:
            # occupied slots are never converted, a source venue never
            # loses its last empty slot, the total count never grows.
            rebalanced = []

            def _num(v):
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return -1

            for vname, vbal in venues.items():
                if vbal <= 0 or any(s.get("venue") == vname for s in slots):
                    continue
                empty_counts = {}
                for s in slots:
                    if str(s.get("slot")) not in active:
                        ev = str(s.get("venue"))
                        empty_counts[ev] = empty_counts.get(ev, 0) + 1
                candidates = [
                    s for s in slots
                    if str(s.get("slot")) not in active
                    and empty_counts.get(str(s.get("venue")), 0) > 1]
                if not candidates:
                    continue
                src = max(candidates, key=lambda s: _num(s.get("slot")))
                src_venue = str(src.get("venue"))
                src["venue"] = vname
                rebalanced.append(
                    f"slot {src.get('slot')} {src_venue}→{vname} "
                    f"(${vbal * scale:.0f} sleeve had no slot)")
            if rebalanced:
                log(self.state, {"kind": "slots-reconciled",
                                 "msg": "rebalanced empty slot to stranded "
                                        "funded venue: "
                                        + "; ".join(rebalanced)[:300]})
            counts = {}
            for s in slots:
                counts[s["venue"]] = counts.get(s["venue"], 0) + 1
            max_alloc = float(p.get("max_alloc_per_slot", 0.5))
            changed = []
            for s in slots:
                n = counts.get(s["venue"], 0)
                if n <= 0:
                    continue
                sleeve = round(venues.get(s["venue"], 0) * scale, 2)
                if s["venue"] in dynamic:
                    balance = round(max(sleeve / n, min_slot_usd), 2)
                else:
                    balance = round(sleeve / n, 2)
                commitment = round(balance * max_alloc, 2)
                if s.get("balance") != balance or \
                        s.get("max_commitment") != commitment:
                    changed.append(f"slot {s['slot']} {s['venue']} "
                                   f"${s.get('balance')}→${balance}")
                s["venue_sleeve"] = sleeve
                s["venue_slots"] = n
                s["balance"] = balance
                s["max_commitment"] = commitment
            # committed-capital heal: an ACTIVE bot must hold a worst-case
            # claim in state["committed"] or open_slot's spare math
            # over-deploys — adopted bots never wrote one (CHIP, adopted
            # 2026-09-05, was $50 invisible to the deploy ceiling).
            # Conservative fallback: the slot's max_commitment (fail-closed:
            # spare shrinks, never grows).
            healed = []
            clamped = []
            for s in slots:
                sk = str(s["slot"])
                committed = self.state.setdefault("committed", {})
                if sk in active and sk not in committed \
                        and s.get("max_commitment"):
                    committed[sk] = float(s["max_commitment"])
                    healed.append(f"slot {sk} ${s['max_commitment']}")
                elif sk in active and committed.get(sk) is not None \
                        and s.get("max_commitment") is not None \
                        and float(committed[sk]) > float(s["max_commitment"]) + 1e-9:
                    clamped.append(f"slot {sk} ${committed[sk]}→"
                                   f"${s['max_commitment']}")
                    committed[sk] = float(s["max_commitment"])
            if healed:
                log(self.state, {"kind": "slots-reconciled",
                                 "msg": "committed-capital heal (adopted "
                                        "bots): " + "; ".join(healed)[:200]})
            if clamped:
                log(self.state, {"kind": "slots-reconciled",
                                 "msg": "committed claims clamped to the "
                                        "shrunk slot caps: "
                                        + "; ".join(clamped)[:200]})
            # fill-tracker heal: a tracker whose last_increase_at predates
            # the occupying bot's deploy is inherited staleness — it flags
            # fresh bots idle minutes after deploy (XVG 2026-09-05: flagged
            # at age 21m off a 272m-old counter). Clear it; the next observe
            # fold re-seeds the idle clock at now (fail-closed vs churn).
            # Trackers newer than the bot's deploy are genuine and stay.
            trackers = self.state.setdefault("optimizer", {}) \
                .setdefault("trackers", {})
            thealed = []
            for slot_key, bot in (self.state.get("active_bots")
                                  or {}).items():
                tr = trackers.get(slot_key)
                if not tr or not tr.get("last_increase_at"):
                    continue
                try:
                    since = datetime.fromisoformat(
                        bot.get("since") or "").timestamp()
                except (ValueError, TypeError):
                    continue
                if since and float(tr["last_increase_at"]) < since:
                    trackers.pop(slot_key, None)
                    thealed.append(str(slot_key))
            if thealed:
                log(self.state, {"kind": "slots-reconciled",
                                 "msg": "fill-tracker heal (stale "
                                        "inheritance): slots "
                                        + ", ".join(thealed)})
            if pruned:
                log(self.state, {"kind": "slots-reconciled",
                                 "msg": "pruned unfillable empty slots "
                                        "(cap-blocked, capital reserved for "
                                        "nothing): "
                                        + "; ".join(f"slot {sid} ({v}: {r})"
                                                    for sid, v, r in pruned
                                                    )[:300]})
            if changed or pruned or clamped or rebalanced:
                log(self.state, {"kind": "slots-reconciled",
                                 "msg": f"slot budgets re-normalized to "
                                        f"config (total ${total:.0f}): "
                                        + "; ".join(changed)[:300]})
                save_state(self.state)
        except Exception as exc:
            log(self.state, {"kind": "health-warn",
                             "msg": f"slot reconcile failed: {str(exc)[:120]}"})

    # ── manage loop ────────────────────────────────────────────────────
    def run(self, once=False, dry_run=True, no_confluence=False, top=None):
        self.top = top
        if not _pidguard_ok():
            return []
        t = threading.Thread(target=serve_ctl, args=(self, self.port), daemon=True)
        t.start()
        print(f"ctl on 127.0.0.1:{self.port} (dry_run={dry_run})", flush=True)
        if os.path.exists(os.path.join(HERE, "KILL")):
            print("KILL present — refusing to run", flush=True)
            return []
        # apply config.yaml venue/total edits to the persisted slot budgets
        # BEFORE the first adopt/rescreen seeds or uses them
        self.reconcile_slots()
        try:
            self.adopt_existing(dry_run)
        except Exception as exc:
            log(self.state, {"kind": "adopt-error", "msg": str(exc)[:160]})
        # carry-and-pray completion (gap-report 2026-09-07): drop
        # entries whose bot has stopped on WT (TP fired, or any other
        # stop path) so the reflect memory learns the final PnL and
        # the state doesn't accumulate ghost entries.
        try:
            self._check_carry_pray_completion(time.time())
        except Exception as exc:
            log(self.state, {"kind": "carry-pray-error",
                             "msg": f"completion failed: {str(exc)[:160]}"})
        actions = []
        try:
            actions = self.rescreen_cycle(dry_run=dry_run,
                                          no_confluence=no_confluence,
                                          top=self.top)
        except Exception as exc:
            log(self.state, {"kind": "rescreen-error", "msg": str(exc)[:200],
                             "tb": traceback.format_exc(limit=6)[-1200:]})
        if once:
            try:
                self.health_cycle(dry_run)
            except Exception as exc:
                log(self.state, {"kind": "health-error", "msg": str(exc)[:200],
                             "tb": traceback.format_exc(limit=6)[-1200:]})
            # one position-revaluation pass (geometry recs auto-applied
            # via grid_edit when position_optimizer.apply is on; exit-add
            # recs via the engine's wtclient set_exits seam — both
            # opt-in, fail-soft, and inert when apply is off)
            if self.position_optimizer:
                try:
                    _recs = self.position_optimizer.cycle(
                        self.state["active_bots"], dry_run=dry_run)
                    self.apply_position_optimizer_recs(_recs, dry_run=dry_run)
                except Exception as exc:
                    log(self.state, {"kind": "position-optimizer-error",
                                     "msg": str(exc)[:200]})
            self.state["last_cycle"] = utcnow()
            save_state(self.state)
            return actions

        interval_s = float(self.config["watch"]["interval_s"])
        rescreen_s = float(self.config["screen"]["rescreen_minutes"]) * 60
        reliability_s = 24 * 3600
        optimize_s = self.optimizer_interval_s()
        next_health = time.time() + interval_s
        next_rescreen = time.time() + rescreen_s
        # first reliability pass shortly after startup (same early-first-pass
        # pattern as the heartbeat below): without it a fresh deploy's ledger
        # stays missing for a full 24h, leaving the console Reliability tab
        # empty even after paper bots have closed grid trips. The manual
        # ctl /reliability refresh does the same work on demand.
        next_reliability = time.time() + min(300.0, reliability_s)
        next_optimize = time.time() + (optimize_s or interval_s)
        po_s = self.position_optimizer_interval_s()
        next_po = time.time() + po_s
        pnl_s = self._pnl_snapshot_interval_s()
        next_pnl = time.time() + (pnl_s or 0)
        # heartbeat: first pass shortly after startup (~60s) so the
        # console shows a score before the first full interval elapses,
        # then on its own cadence (fail-soft, never blocks the loop)
        hb_s = self._heartbeat_interval_s()
        next_heartbeat = time.time() + min(60.0, hb_s)
        while True:
            if os.path.exists(os.path.join(HERE, "KILL")):
                log(self.state, {"kind": "kill", "msg": "KILL file — halting"})
                save_state(self.state)
                break
            now = time.time()
            if now >= next_reliability:
                try:
                    self.reliability_cycle()
                except Exception as exc:
                    log(self.state, {"kind": "reliability-error", "msg": str(exc)[:160]})
                next_reliability = now + reliability_s
            if self.consume_reliability():
                try:
                    self.reliability_cycle()
                except Exception as exc:
                    log(self.state, {"kind": "reliability-error",
                                     "msg": str(exc)[:160]})
            if self.consume_rescreen():
                try:
                    self.rescreen_cycle(dry_run=dry_run,
                                        no_confluence=no_confluence,
                                        top=self.top)
                except Exception as exc:
                    log(self.state, {"kind": "rescreen-error", "msg": str(exc)[:200],
                             "tb": traceback.format_exc(limit=6)[-1200:]})
                next_rescreen = time.time() + rescreen_s
            if now >= next_health:
                try:
                    self.health_cycle(dry_run)
                except Exception as exc:
                    log(self.state, {"kind": "health-error", "msg": str(exc)[:200],
                             "tb": traceback.format_exc(limit=6)[-1200:]})
                next_health = time.time() + interval_s
            # fast loop: idle-slot swaps + challenger hunt between rescreens
            if optimize_s and (now >= next_optimize or self.consume_optimize()):
                try:
                    self.optimizer.run_cycle(dry_run=dry_run)
                except Exception as exc:
                    log(self.state, {"kind": "optimizer-error",
                                     "msg": str(exc)[:200],
                                     "tb": traceback.format_exc(limit=6)[-1200:]})
                next_optimize = time.time() + optimize_s
            elif not optimize_s:
                self.consume_optimize()  # drain stale requests when disabled
            # slow loop: position revaluation — geometry recs are
            # auto-applied via grid_edit when position_optimizer.apply is
            # on, exit-add recs via the engine's set_exits seam; the
            # whole path is fail-soft
            if po_s and now >= next_po:
                try:
                    _recs = self.position_optimizer.cycle(
                        self.state["active_bots"], dry_run=dry_run)
                    self.apply_position_optimizer_recs(_recs, dry_run=dry_run)
                except Exception as exc:
                    log(self.state, {"kind": "position-optimizer-error",
                                     "msg": str(exc)[:200],
                                     "tb": traceback.format_exc(limit=6)[-800:]})
                next_po = now + po_s
            # observability: loop-health heartbeat on its own cadence
            # (enabled=false turns it off); fail-soft — never blocks the loop
            if hb_s and now >= next_heartbeat:
                try:
                    self.heartbeat_cycle(dry_run=dry_run)
                except Exception as exc:
                    log(self.state, {"kind": "heartbeat-error",
                                    "msg": str(exc)[:160]})
                next_heartbeat = time.time() + hb_s
            # observability: fleet PnL snapshot on its own cadence
            # (0 = off); fail-soft — never blocks the manage loop
            if pnl_s and now >= next_pnl:
                try:
                    self._journal_pnl_snapshot()
                except Exception as exc:
                    log(self.state, {"kind": "health-warn",
                                     "msg": f"pnl snapshot failed: "
                                            f"{str(exc)[:120]}"})
                next_pnl = now + pnl_s
            if now >= next_rescreen:
                try:
                    self.rescreen_cycle(dry_run=dry_run,
                                        no_confluence=no_confluence,
                                        top=self.top)
                except Exception as exc:
                    log(self.state, {"kind": "rescreen-error", "msg": str(exc)[:200],
                             "tb": traceback.format_exc(limit=6)[-1200:]})
                next_rescreen = time.time() + rescreen_s
            self.state["last_cycle"] = utcnow()
            save_state(self.state)
            nxt = min(next_health, next_rescreen, next_reliability,
                      next_optimize if optimize_s else next_health,
                      next_po if po_s else next_health,
                      next_pnl if pnl_s else next_health,
                      next_heartbeat if hb_s else next_health)
            time.sleep(max(1.0, min(10.0, nxt - time.time())))



def main():
    # Graceful SIGTERM: exit 0 so a supervisor (launchd KeepAlive with
    # SuccessfulExit=false) restarts only on real crashes, not on stops.
    import signal

    def _term(_signum, _frame):
        raise SystemExit(0)

    try:
        signal.signal(signal.SIGTERM, _term)
    except (ValueError, OSError):
        pass  # non-main thread (tests) — fine
    # refuse a second daemon BEFORE any state write (the Daemon constructor
    # heals env + logs into the shared state file)
    if not _pidguard_ok():
        return 1
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="plan only (default; --live-paper turns this off)")
    ap.add_argument("--live-paper", action="store_true",
                    help="actually create paper bots (default: plan only)")
    ap.add_argument("--no-confluence", action="store_true")
    ap.add_argument("--top", type=int, default=30, help="merge --top passthrough")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    d = Daemon(port=args.port, live_paper=args.live_paper)
    try:
        d.run(once=args.once, dry_run=not args.live_paper,
              no_confluence=args.no_confluence, top=args.top)
    finally:
        try:
            save_state(d.state)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
