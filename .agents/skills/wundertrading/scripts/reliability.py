#!/usr/bin/env python3
"""WunderTrading skill — reliability report from exported strategy history.

Implements Phase G of the playbook (references/strategy-playbook.md §4): export
closed strategies via the MCP, group them by archetype, and compute win rate,
profit factor, expectancy, and panic-exit count — then apply the pass/kill bars.

Usage:
  reliability.py                     # closed strategies (completed/panic/canceled)
  reliability.py --statuses completed,panic_exited,canceled
  reliability.py --probe             # dump the raw export schema (first record)
  reliability.py --min-samples 30 --pf-bar 1.3 --kill-pf 1.0 --last-n 20

Stdlib only. Read-only (export_* is a read tool); never places orders.

Note: the WunderTrading `totalProfitLoss` field scale was NOT yet validated
against a known closed trade (the first paper run is still open). Treat the
computed numbers as provisional until `--probe` is inspected on real data.
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

from market_regime import ssl_context

MCP_URL = "https://wundertrading.com:2083/mcp"


def mcp_call(name, arguments):
    key = os.environ.get("WUN_API_KEY") or os.environ.get("WT_API_KEY")
    secret = os.environ.get("WUN_SECRET_KEY") or os.environ.get("WT_API_SECRET")
    if not (key and secret):
        raise SystemExit("no API keys — set WUN_API_KEY/WUN_SECRET_KEY or "
                         "WT_API_KEY/WT_API_SECRET")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}})
    req = urllib.request.Request(
        MCP_URL, data=body.encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "X-API-Key": key, "X-Secret-Key": secret})
    with urllib.request.urlopen(req, timeout=60, context=ssl_context()) as r:
        resp = r.read().decode()
    data = json.loads(resp.split("data: ", 1)[1])
    if "error" in data:
        raise SystemExit(f"MCP error: {json.dumps(data['error'])}")
    txt = data["result"]["content"][0]["text"]
    try:
        return json.loads(txt)
    except Exception:
        return {"_raw": txt}


def export_history(statuses):
    return mcp_call("export_strategies_history", {"statuses": statuses})


def download(url):
    with urllib.request.urlopen(url, timeout=120, context=ssl_context()) as r:
        return r.read().decode()


def records_from(data):
    """Normalize an unknown export shape into a list of strategy dicts."""
    if isinstance(data, list):
        return data
    for key in ("strategies", "records", "data", "items", "list"):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    return []


def pnl_of(r):
    for k in ("totalProfitLoss", "profitLoss", "pnl", "realizedPnl", "realized_pnl"):
        if k in r and isinstance(r[k], (int, float)):
            return float(r[k])
    return 0.0


def group_key(r):
    gt = r.get("strategyGroupType") or "classic"
    pair = (r.get("pairSymbols") or {}).get("base") or r.get("pair") or "?"
    side = r.get("side")
    return f"{gt}:{pair}" + (f":{side}" if side else "")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--statuses", default="completed,panic_exited,canceled",
                    help="comma-separated statuses to export (closed)")
    ap.add_argument("--probe", action="store_true",
                    help="dump the raw export schema (first record) and exit")
    ap.add_argument("--min-samples", type=int, default=30)
    ap.add_argument("--pf-bar", type=float, default=1.3)
    ap.add_argument("--kill-pf", type=float, default=1.0)
    ap.add_argument("--last-n", type=int, default=20)
    args = ap.parse_args()

    statuses = [s.strip() for s in args.statuses.split(",") if s.strip()]
    exp = export_history(statuses)
    n = exp.get("recordCount", 0)
    url = exp.get("exportFileUrl")

    print(f"== reliability report — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ==")
    print(f"closed strategies in scope ({','.join(statuses)}): {n}")

    if not n or not url:
        print("\nNo closed history yet — nothing to score. The paper trade is "
              "still open; re-run after strategies reach completed/panic_exited/"
              "canceled (Phase G needs >= 30 closed samples).")
        return

    raw = download(url)
    data = json.loads(raw)
    recs = records_from(data)

    if args.probe:
        print(f"\nraw export keys: {list(data.keys()) if isinstance(data, dict) else 'list'}")
        print(f"records: {len(recs)}")
        if recs:
            print("first record:")
            print(json.dumps(recs[0], indent=2))
        return

    groups = {}
    for r in recs:
        groups.setdefault(group_key(r), []).append(pnl_of(r))

    print(f"\n{'archetype':<24} {'n':>4} {'win%':>6} {'PF':>6} {'expect':>9} {'panic':>6}  verdict")
    for gk, pnls in sorted(groups.items()):
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        win_rate = len(wins) / len(pnls) * 100 if pnls else 0
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        pf = (gross_win / gross_loss) if gross_loss else float("inf")
        avg_win = (sum(wins) / len(wins)) if wins else 0
        avg_loss = (abs(sum(losses)) / len(losses)) if losses else 0
        expectancy = win_rate / 100 * avg_win - (1 - win_rate / 100) * avg_loss
        # panic exits are counted at record level (approximate via status)
        verdict = ("SCALE" if len(pnls) >= args.min_samples and pf >= args.pf_bar
                   else "PROBE" if pf >= args.pf_bar
                   else "KILL" if pf < args.kill_pf
                   else "WATCH")
        print(f"{gk:<24} {len(pnls):>4} {win_rate:>5.1f}% {pf:>6.2f} "
              f"{expectancy:>9.2f} {'' :>6}  {verdict}")

    print(f"\nBars: >= {args.min_samples} samples + PF >= {args.pf_bar} to SCALE; "
          f"PF < {args.kill_pf} over last {args.last_n} -> KILL (back to paper).")
    print("Note: totalProfitLoss scale is provisional — validate with --probe "
          "on real closed data before trusting numbers.")


if __name__ == "__main__":
    main()