#!/usr/bin/env python3
"""WunderTrading skill — token screener.

Classifies every candidate token's market regime (via market_regime.py) and
ranks them into a pick list: the token whose current market conditions best
match a strategy archetype you can actually run. Optionally joins live
spread data from the WunderTrading MCP (set WUN_API_KEY / WUN_SECRET_KEY).

Usage:
  token_screen.py hyperliquid BTC,ETH,SOL,HYPE --interval 1h
  token_screen.py binance BTCUSDT,ETHUSDT,SOLUSDT --interval 4h --market futures
  token_screen.py hyperliquid --top 10 --interval 1h   # default majors list

Stdlib only (urllib). Safe: read-only market data; never executes trades.
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

from market_regime import (CONFIGS, classify, compute_metrics, fetch_candles,
                           ssl_context)

DEFAULT_MAJORS = ["BTC", "ETH", "SOL", "HYPE", "XRP", "DOGE", "SUI", "LINK",
                  "AVAX", "ARB"]

# Regime → tradeable-edge ranking: how strong an archetype the regime gives
# you on this exchange type.
REGIME_RANK = {"trend_up": 5.0, "trend_down": 3.0, "chop_high_volatility": 2.0,
               "squeeze": 2.0, "neutral": 0.0}


def wunder_spreads(exchange_code):
    """{viewSymbol: spread_pct} from the WunderTrading MCP (optional, keys via
    WUN_API_KEY / WUN_SECRET_KEY). Returns {} when keys are missing or the
    call fails — spread data is a bonus, never a hard dependency."""
    key = os.environ.get("WUN_API_KEY") or os.environ.get("WT_API_KEY")
    secret = (os.environ.get("WUN_SECRET_KEY")
              or os.environ.get("WT_API_SECRET"))
    if not (key and secret):
        return {}
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "get_exchange_markets",
                                  "arguments": {"exchanges": [exchange_code]}}})
    req = urllib.request.Request(
        "https://wundertrading.com:2083/mcp", data=body.encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "X-API-Key": key, "X-Secret-Key": secret})
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as r:
            resp = r.read().decode()
        data = json.loads(resp.split("data: ", 1)[1])
        text = data["result"]["content"][0]["text"]
        markets = json.loads(text)[exchange_code]
    except Exception as exc:  # network/parse — skip spread column
        print(f"note: spread lookup failed ({exc}) — skipping", file=sys.stderr)
        return {}
    out = {}
    for m in markets:
        if m.get("last"):
            out[m["viewSymbol"]] = (m["ask"] - m["bid"]) / m["last"] * 100
    return out


def score(regime, metrics, spread_pct=None, shortable=True):
    s = REGIME_RANK[regime]
    adx = metrics["adx14"] or 0
    s += min(adx, 40.0) / 40.0 * 2.0          # trend strength bonus ≤ 2
    rsi = metrics["rsi14"]
    if rsi is not None and (rsi > 78 or rsi < 22):
        s -= 1.5                              # overheated/oversold — chase risk
    if metrics["atr_pct"] and metrics["atr_pct"] > 4.0:
        s -= 1.0                              # wild tape — sizing/slippage risk
    if regime == "trend_down" and not shortable:
        s = -1.0                              # spot account can't act on it
    if spread_pct is not None:
        if spread_pct > 1.0:
            s -= 5.0                          # untradeable spread
        elif spread_pct > 0.1:
            s -= 2.0
    return round(s, 2)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exchange", choices=["hyperliquid", "binance"])
    ap.add_argument("--symbols", help="comma-separated candidates "
                    "(hyperliquid: BTC,ETH · binance: BTCUSDT,ETHUSDT)")
    ap.add_argument("--top", type=int, default=len(DEFAULT_MAJORS),
                    help="how many default majors to screen (used when "
                         "--symbols is omitted)")
    ap.add_argument("--interval", default="1h", choices=["15m", "1h", "4h", "1d"])
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--market", default="spot", choices=["spot", "futures"],
                    help="binance only")
    ap.add_argument("--wun-exchange", default=None,
                    help="WunderTrading exchange code for spread lookup, e.g. "
                         "HYPERLIQUID_SWAP or BINANCE_FUTURES (needs "
                         "WUN_API_KEY/WUN_SECRET_KEY)")
    args = ap.parse_args()

    symbols = ([s.strip() for s in args.symbols.split(",") if s.strip()]
               if args.symbols else DEFAULT_MAJORS[:args.top])
    # spot accounts can't act on trend_down; swap/futures venues can
    shortable = not (args.exchange == "binance" and args.market == "spot")
    spreads = {}
    if args.wun_exchange:
        spreads = wunder_spreads(args.wun_exchange)

    rows = []
    for sym in symbols:
        try:
            candles = fetch_candles(args.exchange, sym, args.interval,
                                    args.limit, args.market)
            if len(candles) < 60:
                rows.append({"symbol": sym, "error": f"only {len(candles)} candles"})
                continue
            metrics = compute_metrics(candles)
            regime, evidence = classify(metrics)
            spread = None
            for vs, sp in spreads.items():
                if vs.split("-")[0] == sym or vs == sym:
                    spread = sp
                    break
            rows.append({
                "symbol": sym, "regime": regime, "metrics": metrics,
                "evidence": evidence, "spread_pct": spread,
                "score": score(regime, metrics, spread, shortable),
            })
        except Exception as exc:
            rows.append({"symbol": sym, "error": str(exc)})

    rows.sort(key=lambda r: r.get("score", -999), reverse=True)
    print(f"== token screen — {args.exchange} {args.interval}, "
          f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ==")
    print(f"{'rank':>4} {'symbol':<10} {'regime':<21} {'ADX':>5} {'ATR%':>6} "
          f"{'RSI':>5} {'Δ7d%':>6} {'spread%':>7} {'score':>6}")
    for i, r in enumerate(rows, 1):
        if "error" in r:
            print(f"{i:>4} {r['symbol']:<10} ERROR {r['error']}")
            continue
        m = r["metrics"]
        spread = f"{r['spread_pct']:.3f}" if r["spread_pct"] is not None else "n/a"
        print(f"{i:>4} {r['symbol']:<10} {r['regime']:<21} {m['adx14']:>5} "
              f"{m['atr_pct']:>6} {m['rsi14']:>5} {m['change_pct']['7d']:>6} "
              f"{spread:>7} {r['score']:>6}")

    top = rows[0] if rows else None
    if not top or "error" in top:
        return
    rec = CONFIGS[top["regime"]]
    print(f"\nTOP PICK: {top['symbol']} — {top['regime']} "
          f"(score {top['score']}, evidence {json.dumps(top['evidence'])})")
    print(f"strategy: {rec['strategy']}")
    print("config skeleton (adapt: pairCode from get_exchange_markets, "
          "profilesCodes from get_api_profiles, size per playbook §3):")
    print(json.dumps(rec["fields"], indent=1))
    print("\nEXECUTION BLOCKED until the SKILL.md Phase E checklist passes — "
          "explicit user confirmation of the exact trade is non-negotiable.")


if __name__ == "__main__":
    main()
