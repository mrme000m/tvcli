#!/usr/bin/env python3
"""wt_reset.py — reset the WunderTrading paper accounts (grid bots only).

Deletes every grid bot whose resource is paperTrading=true on
WunderTrading: active bots are stopped (stop_and_close_all), verified
stopped, then deleted; already-stopped/historic paper bots are deleted
outright. This clears the plan-capacity view (`used_pairs`, the free-tier
"one active non-premium bot" slot) and all open/closed positions, so the
daemon starts from a clean exchange state.

SAFETY: bots on real-money profiles (`paperTrading: false`) are NEVER
touched — they are listed, warned about, and skipped. The daemon's real
Hyperliquid profile is in daemon.PROFILE_DENYLIST for the same reason.

Usage:
    python3 scripts/wt_reset.py            # dry-run: show what would happen
    python3 scripts/wt_reset.py --yes      # actually stop + delete
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GRID_HOME = os.path.dirname(HERE)
for p in (GRID_HOME, os.path.join(GRID_HOME, "execution")):
    if p not in sys.path:
        sys.path.insert(0, p)

STOPPED_STATES = {"stopped", "stopped_and_close_all",
                 "stopped_with_unrealized", "closed"}
VERIFY_TIMEOUT_S = 120
VERIFY_POLL_S = 5


def _import_safe(mod_name):
    """Import a worker module without letting import errors raise."""
    try:
        return __import__(mod_name)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: could not import {mod_name}: {exc}")
        return None


def list_paper_bots():
    observe = _import_safe("observe")
    if observe is None:
        return None, None
    try:
        bots = observe._grid_resources() or []
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: grid status list unavailable: {exc}")
        print("       (is the CloakBrowser/WT session up? see README "
              "troubleshooting)")
        return None, None
    paper = [b for b in bots if b.get("paperTrading")]
    real = [b for b in bots if not b.get("paperTrading")]
    return paper, real


def _fmt(bot):
    pair = (bot.get("pair") or {}).get("viewSymbol") or "?"
    return f"{bot.get('code')} {pair} [{bot.get('status')}]"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--yes", action="store_true",
                    help="actually stop + delete (default: dry-run)")
    args = ap.parse_args(argv)

    paper, real = list_paper_bots()
    if paper is None:
        return 1

    if real:
        print(f"REAL-MONEY bots present (NEVER touched):")
        for b in real:
            print(f"  - {_fmt(b)}")

    if not paper:
        print("No paper grid bots found — WT paper accounts already clean.")
        return 0

    print(f"Paper grid bots to {'DELETE' if args.yes else 'delete (dry-run)'}:")
    for b in paper:
        print(f"  - {_fmt(b)}")
    if not args.yes:
        print("\nDry-run only. Re-run with --yes to stop + delete them.")
        return 0

    grid_adapter = _import_safe("grid_adapter")
    if grid_adapter is None:
        return 1

    deleted, skipped = [], []
    for bot in paper:
        code = bot.get("code")
        status = (bot.get("status") or "").lower()
        try:
            if status not in STOPPED_STATES:
                print(f"stopping  {code} …")
                res = grid_adapter.grid_stop(code, "stop_and_close_all",
                                             dry_run=False)
                if not (res or {}).get("ok"):
                    print(f"  stop FAILED: {json.dumps(res)[:160]} — skipped")
                    skipped.append(code)
                    continue
                # verify stopped (positions close asynchronously)
                deadline = time.time() + VERIFY_TIMEOUT_S
                status = None
                while time.time() < deadline:
                    time.sleep(VERIFY_POLL_S)
                    observe = _import_safe("observe")
                    fresh = next(
                        (b for b in (observe._grid_resources() or [])
                         if b.get("code") == code), None)
                    status = ((fresh or {}).get("status") or "").lower()
                    if status in STOPPED_STATES:
                        break
                if status not in STOPPED_STATES:
                    print(f"  stop NOT verified (status={status}) — skipped")
                    skipped.append(code)
                    continue
            print(f"deleting  {code} …")
            # WT can answer 403 Forbidden for a short window after the stop
            # while it is still closing the legs (verified live 2026-09-05:
            # the same code deletes fine ~30-60s later) — retry with backoff.
            deleted_ok = False
            for attempt in range(6):
                res = grid_adapter.grid_delete(code, dry_run=False)
                if (res or {}).get("ok"):
                    deleted_ok = True
                    break
                if attempt < 5:
                    print(f"  delete not ready yet "
                          f"({json.dumps(res)[:100]}) — retrying in 15s")
                    time.sleep(15)
            if deleted_ok:
                deleted.append(code)
            else:
                print(f"  delete FAILED after retries: {json.dumps(res)[:160]}")
                skipped.append(code)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR on {code}: {exc}")
            skipped.append(code)

    print(f"\ndone: {len(deleted)} deleted, {len(skipped)} skipped")
    if skipped:
        print("skipped:", ", ".join(skipped))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
