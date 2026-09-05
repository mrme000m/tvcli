#!/usr/bin/env python3
"""Grid-fleet daemon — autonomous scan → deliberate → guard → deploy → watch → rotate.

Schedules (from ../config.yaml, merged over built-in defaults):
  health/positions poll   60s        (KILL check, observe_all, stagnation eval,
                                      out-of-channel re-analysis, in-place adjust)
  rescreen                60m        (merge.py → swarm → guardrails → deploy)
  reliability cron        24h        (bot_trades → archetype_stats → save
                                    → reload → sizing/kill gates)

Autonomy with guardrails: deployments are paper (demo-hype) until the
reliability gate passes (>=30 samples, PF>=1.3); live needs live_allow=true in
state.json (set only by an explicit operator action) AND guardrails.deploy().
--dry-run (default) plans everything without creating anything.
--once runs a single rescreen cycle plus one health pass then exits.

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

from stagnation import is_stagnant, slot_plan, derive_policy  # noqa: E402
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
}

DEFAULT_CONFIG = {
    "portfolio": {
        "total_usd": 600.0,
        "venues": {"hyperliquid": {"balance_usd": 400.0},
                   "binance": {"balance_usd": 200.0}},
        "slots_min": 3, "slots_max": 6, "slots_default": 4,
        "max_alloc_per_slot": 0.5, "cash_buffer_pct": 0.15,
    },
    "screen": {"rescreen_minutes": 60, "confirm_interval": "4h",
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
              # browser watchdog: every WunderTrading session-API call rides
              # the headful CloakBrowser on CDP — when it dies the daemon is
              # blind and deploy/rotate fail. Probed each health pass.
              "browser_cdp": "http://127.0.0.1:9222",
              "browser_restart_cooldown_s": 600},
    "autonomy": {"mode": "auto", "base_pct": 0.25, "probe_pct": 0.40, "full_pct": 0.50,
                 "live_profiles": [], "paper_profiles": ["demo-hype"],
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


# ── Daemon ─────────────────────────────────────────────────────────────

class Daemon:
    def __init__(self, port=None):
        self.config = load_config()
        self.port = port or int(self.config["server"].get("daemon_port", 8799))
        self.state = load_state()
        self_heal_env(self.state)
        self.profiles = grid_profiles_safe()
        self.reliability = reliability_load_safe()
        self.state["profiles"] = self.profiles
        self.state["reliability"] = self.reliability
        self.capabilities = {
            "resolve": HAS_RESOLVE, "observe": HAS_OBSERVE,
            "reliability": HAS_RELIABILITY, "reflect": HAS_REFLECT,
        }
        self._lock = threading.Lock()
        self._rescreen_flag = False
        self._reliability_flag = False
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
    def queue_rescreen(self):
        with self._lock:
            self._rescreen_flag = True

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

    def plan_slots(self):
        p = self.config["portfolio"]
        venues = {k: v.get("balance_usd", 0) for k, v in p["venues"].items()}
        return slot_plan(p["total_usd"], venues, p["slots_default"],
                         p["max_alloc_per_slot"], p["cash_buffer_pct"])

    def open_slot(self, venue):
        """Append a new slot for `venue` — the venue's slots are all occupied
        but a profitable candidate is waiting and deployable capital is spare.

        Gates (fail-closed — any refusal leaves the slot count unchanged):
          1. total slots < portfolio.slots_max (config, default 6)
          2. venue is funded in the portfolio
          3. spare deployable capital ≥ the new slot's worst-case commitment
        Returns (slot, None) on open, (None, reason) on refusal. Existing
        same-venue slot budgets are re-normalized to the new split so every
        future deploy into that venue sizes from one per-slot budget.
        """
        p = self.config["portfolio"]
        slots_max = int(p.get("slots_max", 5))
        cur = list(self.state["slots"])
        if len(cur) >= slots_max:
            return None, f"already at slots_max {slots_max}"
        venues = {k: v.get("balance_usd", 0) or 0
                  for k, v in p["venues"].items()}
        sleeve = venues.get(venue, 0)
        if sleeve <= 0:
            return None, f"venue {venue} not funded"
        ceiling = self.plan_slots()["deployable_ceiling"]
        committed = sum(self.state["committed"].values())
        venue_slots = 1 + sum(1 for s in cur if s["venue"] == venue)
        balance = round(sleeve / venue_slots, 2)
        max_commitment = round(balance * float(p["max_alloc_per_slot"]), 2)
        spare = ceiling - committed
        if spare < max_commitment - 1e-9:
            return None, (f"spare ${spare:.2f} < new-slot worst-case "
                          f"${max_commitment:.2f}")
        new_slot = {"slot": max(s["slot"] for s in cur) + 1, "venue": venue,
                    "balance": balance, "max_commitment": max_commitment,
                    "venue_sleeve": sleeve, "venue_slots": venue_slots}
        # re-normalize the venue's existing slot budgets to the new split so
        # a later deploy into a freed slot sizes like one into the new slot
        for s in cur:
            if s["venue"] == venue:
                s["balance"] = balance
                s["max_commitment"] = max_commitment
                s["venue_slots"] = venue_slots
        self.state["slots"] = cur + [new_slot]
        log(self.state, {"kind": "slot-open", "slot": new_slot["slot"],
                         "msg": (f"{venue} slot {new_slot['slot']} opened "
                                 f"(${balance:.0f} budget, worst-case "
                                 f"${max_commitment:.0f}); spare ${spare:.0f} "
                                 f"of ${ceiling:.0f} deployable")})
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
                "incumbent_score": (incumbent or {}).get("score_final") or 0,
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
            }
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

    # ── rescreen cycle ─────────────────────────────────────────────────
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
        log(self.state, {"kind": "screen",
                         "msg": f"{len(cands)} candidates, top=" +
                                (f"{cands[0]['venue']}:{cands[0]['symbol']} "
                                 f"{cands[0]['score_final']}" if cands else "none")})
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
                    continue  # dry runs never grow the persisted slot plan
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
            if not dry_run and str(slot["slot"]) in self.state["active_bots"]:
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
                reasons = ["manual rotate (ctl /rotate)"]
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
                       "top3": [{"venue": c.get("venue"), "symbol": c.get("symbol"),
                                 "regime": c.get("regime"),
                                 "score_final": c.get("score_final"),
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
        return actions

    # ── health poll ────────────────────────────────────────────────────
    def health_cycle(self, dry_run=True):
        self.browser_watchdog()
        if not self.state["active_bots"]:
            return
        observed_all = observe_all_safe(self.state["active_bots"])
        self.state["last_observe"] = observed_all
        for slot_key, bot in list(self.state["active_bots"].items()):
            obs = observed_all.get(slot_key, observed_all.get(int(slot_key), {}))
            bot["observed"] = obs
            bot["last_observed"] = utcnow()
            if obs.get("error"):
                log(self.state, {"kind": "health-warn", "slot": slot_key,
                                 "msg": f"observe error: {obs['error'][:160]}"})
                continue
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

            # stagnation evaluation (drives rotation candidates)
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

            out_of_channel = False
            if price is not None and channel.get("high") and channel.get("low"):
                out_of_channel = price > channel["high"] or price < channel["low"]
            if out_of_channel or status in STOPPED_STATES:
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
                        log(self.state, {"kind": "regime-changed", "slot": slot_key,
                                         "msg": f"{bot.get('venue')}:{bot.get('symbol')} "
                                                f"{policy.get('regime')}→{regime_now} "
                                                f"(no in-place adjust)"})
        # total-blindness escalation: when EVERY bot errors on EVERY sweep,
        # individual health-warn lines are too quiet — surface one loud
        # observe-outage event every ~30 blind minutes instead.
        bots = self.state["active_bots"] or {}
        if bots:
            errs = sum(1 for b in bots.values()
                       if (b.get("observed") or {}).get("error"))
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
        save_state(self.state)

    def adjust_bot(self, slot_key, dry_run=True):
        bot = self.state["active_bots"].get(slot_key)
        if not bot or not bot.get("bot_code"):
            return
        last = self.state.get("last_adjust", {}).get(slot_key)
        now = time.time()
        if last and now - last < 6 * 3600:
            log(self.state, {"kind": "adjust-skip", "slot": slot_key,
                             "msg": f"rate limit (1 edit/6h) for {bot.get('symbol')}"})
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
        try:
            new_upsert = grid_adapter.compute_upsert(
                symbol, venue, price, atr_pct, step_pct, grids, amount,
                grid_type, profile_code, pair_code)
        except Exception as exc:
            log(self.state, {"kind": "adjust-error", "slot": slot_key,
                             "msg": f"compute_upsert failed: {exc}"})
            return
        res = grid_edit_safe(bot["bot_code"], new_upsert, dry_run=dry_run)
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

    # ── rotation ───────────────────────────────────────────────────────
    def execute_rotation(self, slot_key, challenger, dry_run=True):
        incumbent = self.state["active_bots"].get(slot_key)
        if not incumbent:
            return False
        policy = incumbent.get("stagnation_policy") or {}
        observed = incumbent.get("observed") or {}
        manual = bool(incumbent.get("force_rotate"))
        if manual:
            ok_rot, reasons = True, ["manual rotate (ctl /rotate)"]
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

    def reconcile_slots(self):
        """Re-normalize persisted slot budgets to the CURRENT config.

        Slots persist in state.json (open_slot grows the plan, rotations
        free them) — but nothing ever re-read portfolio.venues into them,
        so a config edit + daemon restart left the fleet sizing from the
        OLD sleeves while the console config editor showed the new ones
        (the console-vs-backend drift a restart was supposed to close).
        Keep the persisted slot COUNT and venue assignment (that is the
        runtime truth — which slots exist), only recompute each venue's
        per-slot budget: scaled sleeve / venue slot count. Never raises.
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
            scale = total / vsum
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
            if changed:
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
            self.state["last_cycle"] = utcnow()
            save_state(self.state)
            return actions

        interval_s = float(self.config["watch"]["interval_s"])
        rescreen_s = float(self.config["screen"]["rescreen_minutes"]) * 60
        reliability_s = 24 * 3600
        next_health = time.time() + interval_s
        next_rescreen = time.time() + rescreen_s
        next_reliability = time.time() + reliability_s
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
            nxt = min(next_health, next_rescreen, next_reliability)
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
    d = Daemon(port=args.port)
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
