#!/usr/bin/env python3
"""wt-exchanges-live.py — live verification of the wtclient exchanges surface.

Runs ExchangesClient against the ISOLATED vault-account CloakBrowser on CDP
port 9223 (profile-vault). NEVER point this at port 9222 — that browser
belongs to the separate Mac WT account with its own live daemon.

Usage (from the repo root):
    python3 browser-debug/wt-exchanges-live.py            # read-only: list + limits
    python3 browser-debug/wt-exchanges-live.py --ensure  # also run ensure_paper_profiles
                                                          # (creates missing paper profiles)
Expected on the vault account (verified 2026-09-06):
    - demo-hype  present (HYPERLIQUID, paper, id 47ca341d5a01c1df3781e490)
    - demo-bn    missing until --ensure creates it (BINANCE -> BINANCE_FUTURES paper)
    - account-limits gridBots.max == 200 (premium tier unlocked by demo-hype)
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
WUN_SCRIPTS = os.path.join(ROOT, ".agents", "skills", "wundertrading", "scripts")
sys.path.insert(0, WUN_SCRIPTS)

CDP_BASE = os.environ.get("WT_TEST_CDP", "http://127.0.0.1:9223")
SPEC = {"hyperliquid": ["demo-hype"], "binance": ["demo-bn"]}


def main() -> int:
    from wtclient import WunderTrading  # late import: only after wtclient lands

    wun = WunderTrading(browser=True, cdp_base=CDP_BASE)
    try:
        profiles = wun.exchanges.list_profiles()
        print(f"profiles ({len(profiles)}):")
        for p in profiles:
            print(
                f"  - {p.name}: family={p.exchange_family} "
                f"paper={p.paper_trading} enabled={p.enabled} "
                f"id={p.id}"
            )

        limits = wun.exchanges.account_limits()
        grid_limits = (limits or {}).get("gridBots") or {}
        print(f"account-limits gridBots: active={grid_limits.get('active')} "
              f"max={grid_limits.get('max')}")

        if "--ensure" in sys.argv:
            report = wun.exchanges.ensure_paper_profiles(SPEC)
            print("ensure report:")
            print(json.dumps(report, indent=2, default=str))
            # Idempotency re-check: a second pass must be all-present.
            report2 = wun.exchanges.ensure_paper_profiles(SPEC)
            states = {
                name: entry.get("state")
                for venue in (report2.get("venues") or {}).values()
                for name, entry in venue.items()
            }
            print(f"second-pass states: {states}")
            if not all(s == "present" for s in states.values()):
                print("FAIL: second ensure pass not idempotent")
                return 1
        return 0
    finally:
        wun.close()


if __name__ == "__main__":
    raise SystemExit(main())
