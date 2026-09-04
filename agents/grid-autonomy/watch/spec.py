#!/usr/bin/env python3
"""Per-bot tvcli watch spec generator — TP / ladder / recheck triggers.

Derives level triggers from the deployed grid channel (mid ± step multiples)
in the shape of watch/xmr-demo-dca.json, so `tvcli watch --spec` polls them
and the daemon consumes fired triggers from the journal.

Usage:
  spec.py --symbol PUMP --tv-symbol BINANCE:PUMPUSDT --mid 3.5 --step-pct 1.087 \
      --grids 12 --slot 1 --out watch/specs/pump-s1.json
"""
import argparse
import json
from datetime import datetime, timezone


def build_spec(symbol, tv_symbol, mid, step_pct, grids, slot,
               tf="1h", regime="chop_high_volatility"):
    now = datetime.now(timezone.utc)
    step = step_pct / 100.0
    # TP zone: one grid step above avg entry; ladder depths below mid
    tp = mid * (1 + step)
    dca1 = mid * (1 - step)
    dca3 = mid * (1 - 3 * step)
    full = mid * (1 - grids * step / 2)
    return {
        "episode": slot,
        "id": f"{symbol.lower()}-grid-s{slot}",
        "mission": f"{symbol} {regime} grid slot {slot}: TP/ladder/recheck watch.",
        "status": "active",
        "created": now.isoformat(timespec="seconds"),
        "symbol": tv_symbol,
        "tf": "5m",
        "baseline": {"time": int(now.timestamp()), "price": round(mid, 6),
                     "at": now.isoformat(timespec="seconds")},
        "triggers": [
            {"id": "tp-hit", "type": "level", "dir": "up",
             "level": round(tp, 6),
             "label": f"TP zone (+{step_pct}% grid step)", "action": "L1"},
            {"id": "dca1", "type": "level", "dir": "down",
             "level": round(dca1, 6),
             "label": f"First grid below mid (-{step_pct}%)", "action": "L1"},
            {"id": "dca3", "type": "level", "dir": "down",
             "level": round(dca3, 6),
             "label": "Deep deployment (-3 steps) — review", "action": "L2"},
            {"id": "dca-full", "type": "level", "dir": "down",
             "level": round(full, 6),
             "label": "Channel floor — regime likely flipped", "action": "L2"},
            {"id": "recheck", "type": "time", "afterMin": 60,
             "label": "Hourly regime recheck reminder", "action": "L1"},
        ],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tv-symbol", required=True)
    ap.add_argument("--mid", type=float, required=True)
    ap.add_argument("--step-pct", type=float, required=True)
    ap.add_argument("--grids", type=int, default=12)
    ap.add_argument("--slot", type=int, default=1)
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--regime", default="chop_high_volatility")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    spec = build_spec(args.symbol, args.tv_symbol, args.mid, args.step_pct,
                      args.grids, args.slot, args.tf, args.regime)
    text = json.dumps(spec, indent=2)
    if args.out:
        open(args.out, "w").write(text)
        print(f"wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
