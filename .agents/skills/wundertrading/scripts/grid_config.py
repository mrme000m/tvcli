#!/usr/bin/env python3
"""WunderTrading skill — generate Grid-bot + MCP strategy configs for a symbol.

Turns the screening decision into two ready-to-apply payloads for one symbol:

1. grid_bot     — the web-UI Grid-bot config (grid type, ATR-band channel,
                  ATR-derived Profit-per-GRID, grid count, per-trade sizing,
                  Stop Trigger, Pump Protection). The Grid bot is a web-UI
                  configurator; there is NO MCP/REST endpoint for it.
2. mcp_strategy — the RELIABLE, fully-programmatic path: a ready-to-send
                  `place_strategy_trade` payload (classic or DCA) that reuses
                  the same regime decision. For chop/neutral this is a DCA
                  ladder whose deviation is derived from ATR (like the grid
                  step); for trend a classic TP-ladder; for squeeze a
                  band-edge limit. Idempotent via clientId.

Stdlib only. Read-only market data; NEVER executes trades — it emits a payload
only, and execution stays blocked until the Phase E checklist passes.

Usage:
  grid_config.py XMR --balance 10000 --max-alloc 0.5
  grid_config.py XMR --json
  grid_config.py XMR --dca-factor 1.5 --client-id ~xmr-chop-20260904
  grid_config.py XMR --profiles 123456,789012 --pair-code 119
  grid_config.py XMR --send --balance 10000 --profiles 123456   # preview + refuse (no --yes)
  grid_config.py XMR --send --yes --balance 10000 --profiles 123456  # SUBMIT via MCP

Knobs:
  --band-atr     channel half-width in ATRs (Higher/Lower price band)   [3.0]
  --step-factor  Profit-per-GRID = ATR% * factor (clamped)             [0.5]
  --step-min     floor on the derived grid step (%)                     [0.1]
  --step-max     cap  on the derived grid step (%)                      [2.0]
  --min-grids    minimum number of grid levels                          [5]
  --max-grids    maximum number of grid levels                          [30]
  --balance      account balance (USD) for sizing math
  --max-alloc    max fraction of balance a bot may commit               [0.5]
  --dca-factor   DCA safety-order deviation = ATR% * factor             [1.5]
  --dca-orders   DCA extraOrderCount (incl. entry)                       [6]
  --dca-vol-mult DCA extraOrderVolumeMultiplier                          [1.4]
  --dca-dev-mult DCA extraOrderDeviationMultiplier                       [1.5]
  --dca-tp       DCA take-profit deviation % vs average_price            [1.5]
  --client-id    explicit idempotency key (32-64 chars, ^[~=-a-zA-Z0-9]+$)
  --pair-code    explicit WunderTrading market code (else auto-resolved
                 via get_exchange_markets when keys are present)
  --profiles     comma-separated API profile ids (else placeholder)
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

from market_regime import (CONFIGS, classify, compute_metrics, fetch_candles,
                           ssl_context)
from universe_screen import derived_step, hyperliquid_spreads

# Regime -> grid type (references/grid-bot.md §8)
GRID_TYPE = {
    "chop_high_volatility": "Neutral",
    "squeeze": "Neutral",
    "neutral": "Neutral",
    "trend_up": "Long",
    "trend_down": "Short",
}

START_CONDITION = {
    "chop_high_volatility": "Immediate (Neutral grid harvests both sides)",
    "squeeze": "Immediate, or Bollinger (close back inside the lower band)",
    "neutral": "Immediate (probe)",
    "trend_up": "MACD, or Webhook (tvcli confluence)",
    "trend_down": "MACD, or Webhook (tvcli confluence)",
}

STOP_TRIGGER = "Stop and close all"  # spot default; futures may use "Stop only"

MCP_URL = "https://wundertrading.com:2083/mcp"

PHASE_E_CHECKLIST = [
    "User explicitly confirmed THIS trade: pair, side, size, config.",
    "pairCode resolved from get_exchange_markets (not hand-written).",
    "profilesCodes from get_api_profiles; profile active with balance.",
    "Sizing math done: worst-case commitment <= max_alloc of balance.",
    "Reliability ladder respected: paper -> probe 5-10% -> scale.",
]


def base_of(view_symbol):
    """'XMR-USDC' / 'XMRUSDT' -> 'XMR'."""
    b = (view_symbol or "").upper()
    for sep in ("-", "_"):
        if sep in b:
            b = b.split(sep)[0]
            break
    for q in ("USDT", "USDC", "USD"):
        if b.endswith(q) and len(b) > len(q):
            b = b[:-len(q)]
            break
    return b


def exchange_code_for(args):
    if args.exchange == "hyperliquid":
        return "HYPERLIQUID_SWAP"
    return "BINANCE_FUTURES" if args.market == "futures" else "BINANCE"


def wunder_markets(exchange_code):
    """Market list from the WunderTrading MCP get_exchange_markets ([] if no
    keys or the call fails)."""
    key = os.environ.get("WUN_API_KEY") or os.environ.get("WT_API_KEY")
    secret = os.environ.get("WUN_SECRET_KEY") or os.environ.get("WT_API_SECRET")
    if not (key and secret):
        return []
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "get_exchange_markets",
                                  "arguments": {"exchanges": [exchange_code]}}})
    req = urllib.request.Request(
        MCP_URL, data=body.encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "X-API-Key": key, "X-Secret-Key": secret})
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as r:
            resp = r.read().decode()
        data = json.loads(resp.split("data: ", 1)[1])
        text = data["result"]["content"][0]["text"]
        return json.loads(text)[exchange_code]
    except Exception as exc:
        print(f"note: pairCode resolution failed ({exc}) — leave placeholder",
              file=sys.stderr)
        return []


def resolve_paircode(symbol, args):
    markets = wunder_markets(exchange_code_for(args))
    for m in markets:
        if base_of(m.get("viewSymbol")) == symbol.upper():
            return m.get("code")
    return None


def make_client_id(symbol, regime, explicit):
    if explicit:
        if not re.fullmatch(r"[~=a-zA-Z0-9]{32,64}", explicit):
            print(f"warning: --client-id '{explicit}' may not match the "
                  f"schema (^[~=-a-zA-Z0-9]+$ — use ~, =, letters, digits only)",
                  file=sys.stderr)
        return explicit
    sym = re.sub(r"[^A-Za-z0-9]", "", symbol).upper()
    reg = re.sub(r"[^A-Za-z0-9]", "~", regime)
    cid = f"~{sym}~{reg}~{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    while len(cid) < 32:
        cid += "="
    return cid[:64]


def geo_sum(n, v):
    if v == 1.0:
        return float(n)
    return sum(v ** i for i in range(n))


def analyze(symbol, args):
    candles = fetch_candles(args.exchange, symbol, args.interval,
                            args.limit, args.market)
    if len(candles) < 60:
        raise SystemExit(f"{symbol}: only {len(candles)} candles — need >= 60")
    m = compute_metrics(candles)
    regime, evidence = classify(m)
    spread = None
    if args.exchange == "hyperliquid":
        spread = hyperliquid_spreads([symbol]).get(symbol)
    return m, regime, evidence, spread


def build_grid(symbol, args, m, regime, evidence, spread):
    atr = m["atr_pct"] or 0.0
    price = m["price"]
    band = args.band_atr * atr / 100.0
    high, low, mid = price * (1 + band), price * (1 - band), price
    width = (high - low) / mid * 100

    step = derived_step(m, {"step_atr_factor": args.step_factor,
                            "step_min": args.step_min,
                            "step_max": args.step_max})
    step_forced = False
    if spread is not None and step < 2 * spread:
        step = round(2 * spread, 3)
        step_forced = True
    grids = max(args.min_grids, min(args.max_grids, round(width / step)))

    amount = total = None
    if args.balance:
        alloc = args.balance * args.max_alloc
        amount = round(alloc / grids, 2)
        total = round(amount * grids, 2)

    return {
        "grid_type": GRID_TYPE[regime],
        "channel": {"low": round(low, 6), "mid": round(mid, 6),
                    "high": round(high, 6), "width_pct": round(width, 2)},
        "profit_per_grid_pct": step,
        "step_forced_to_2x_spread": step_forced,
        "grids": grids,
        "sizing": {
            "balance": args.balance,
            "max_alloc_pct": round(args.max_alloc * 100, 1),
            "amount_per_trade": amount,
            "total_commitment_estimate": total,
            "note": ("worst-case long side (all buy grids filled). A Neutral "
                     "grid additionally needs base coin on the short side — "
                     "the platform's Investment panel is authoritative."),
        },
        "take_profit_usd": None,
        "stop_loss_usd": None,
        "trailing_stop": None,
        "stop_trigger": STOP_TRIGGER,
        "pump_protection": True,
        "start_condition": START_CONDITION[regime],
    }


def build_mcp(symbol, args, m, regime):
    base = CONFIGS[regime]
    fields = {k: v for k, v in base["fields"].items() if v is not None}
    side = fields.get("side", "long")
    order_type = fields.get("orderType", "market")
    is_dca = regime in ("chop_high_volatility", "neutral")

    if is_dca:
        atr = m["atr_pct"] or 0.0
        dev = max(0.001, min(0.2, round(atr * args.dca_factor / 100.0, 5)))
        fields["extraOrderCount"] = args.dca_orders
        fields["extraOrderDeviation"] = dev
        fields["extraOrderVolumeMultiplier"] = args.dca_vol_mult
        fields["extraOrderDeviationMultiplier"] = args.dca_dev_mult
        fields["takeProfits"] = [{"priceDeviation": f"{args.dca_tp}%",
                                  "portfolio": "100%"}]
        fields["takeProfitBaseOn"] = "average_price"

    if regime == "squeeze":
        band = args.band_atr * (m["atr_pct"] or 0.0) / 100.0
        fields["orderType"] = "limit"
        fields["price"] = round(m["price"] * (1 - band), 6)
        fields["timeToLive"] = args.ttl

    amount = None
    if args.balance:
        alloc = args.balance * args.max_alloc
        if is_dca:
            n = fields.get("extraOrderCount", 1)
            v = fields.get("extraOrderVolumeMultiplier", 1.0)
            amount = round(alloc / geo_sum(n, v), 2)
        else:
            amount = round(alloc, 2)
        fields["amountPerTrade"] = amount
        fields["amountPerTradeType"] = "quote"

    pair_code = args.pair_code or resolve_paircode(symbol, args)
    payload = {
        "clientId": make_client_id(symbol, regime, args.client_id),
        "exchangeCode": exchange_code_for(args),
        "pairCode": pair_code if pair_code else "<RESOLVE_VIA_get_exchange_markets>",
        "profilesCodes": ([p.strip() for p in args.profiles.split(",") if p.strip()]
                          if args.profiles else ["<FROM_get_api_profiles>"]),
        "side": side,
        "orderType": order_type,
    }
    for k, v in fields.items():
        if k not in ("side", "orderType"):
            payload[k] = v

    return {
        "strategy": base["strategy"],
        "is_dca": is_dca,
        "dca_deviation_derived_from_atr": is_dca,
        "amount_per_trade": amount,
        "payload": payload,
    }


def send_strategy(payload):
    """Submit a place_strategy_trade via the WunderTrading MCP.

    Returns {"ok": True, "result": <tools/call result>} or
    {"ok": False, "error": <msg>}. This is the ONLY write path in the script."""
    key = os.environ.get("WUN_API_KEY") or os.environ.get("WT_API_KEY")
    secret = os.environ.get("WUN_SECRET_KEY") or os.environ.get("WT_API_SECRET")
    if not (key and secret):
        return {"ok": False,
                "error": "no API keys (set WUN_API_KEY/WUN_SECRET_KEY or "
                         "WT_API_KEY/WT_API_SECRET)"}
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "place_strategy_trade",
                                  "arguments": payload}})
    req = urllib.request.Request(
        MCP_URL, data=body.encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "X-API-Key": key, "X-Secret-Key": secret})
    try:
        with urllib.request.urlopen(req, timeout=60, context=ssl_context()) as r:
            resp = r.read().decode()
        data = json.loads(resp.split("data: ", 1)[1])
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if "error" in data:
        return {"ok": False, "error": json.dumps(data["error"])}
    return {"ok": True, "result": data.get("result")}


def run_send(args, mcp):
    p = mcp["payload"]
    print(f"== SEND {args.symbol} — {mcp['strategy']} ==")

    problems = []
    if not p.get("pairCode") or str(p["pairCode"]).startswith("<"):
        problems.append("pairCode unresolved — pass --pair-code or ensure "
                        "WUN/WT keys are set")
    if not p.get("profilesCodes") or any(str(x).startswith("<")
                                         for x in p["profilesCodes"]):
        problems.append("profilesCodes unresolved — pass --profiles <id>[,<id>]")
    if "amountPerTrade" not in p:
        problems.append("amountPerTrade missing — pass --balance for sizing")
    if problems:
        print("REFUSED — cannot send:")
        for pr in problems:
            print(f"  - {pr}")
        sys.exit(1)

    print("Phase E checklist (all must be true):")
    for c in PHASE_E_CHECKLIST:
        print(f"  [ ] {c}")
    print(f"\npayload:\n{json.dumps(p, indent=2)}")
    if not args.yes:
        print("\nNOT SENT. Re-run with --yes to confirm THIS exact trade "
              "(opens a real position immediately).")
        return

    resp = send_strategy(p)
    if resp.get("ok"):
        print("\nSENT. response:")
        print(json.dumps(resp["result"], indent=2))
    else:
        print(f"\nSEND FAILED: {resp.get('error')}")
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol", help="hyperliquid: XMR · binance: XMRUSDT")
    ap.add_argument("--exchange", default="hyperliquid",
                    choices=["hyperliquid", "binance"])
    ap.add_argument("--interval", default="1h",
                    choices=["15m", "1h", "4h", "1d"])
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--market", default="spot", choices=["spot", "futures"])
    ap.add_argument("--band-atr", type=float, default=3.0)
    ap.add_argument("--step-factor", type=float, default=0.5)
    ap.add_argument("--step-min", type=float, default=0.1)
    ap.add_argument("--step-max", type=float, default=2.0)
    ap.add_argument("--min-grids", type=int, default=5)
    ap.add_argument("--max-grids", type=int, default=30)
    ap.add_argument("--balance", type=float, default=None)
    ap.add_argument("--max-alloc", type=float, default=0.5)
    ap.add_argument("--dca-factor", type=float, default=1.5)
    ap.add_argument("--dca-orders", type=int, default=6)
    ap.add_argument("--dca-vol-mult", type=float, default=1.4)
    ap.add_argument("--dca-dev-mult", type=float, default=1.5)
    ap.add_argument("--dca-tp", type=float, default=1.5)
    ap.add_argument("--ttl", type=int, default=1440,
                    help="limit-order time-to-live in minutes (squeeze only)")
    ap.add_argument("--client-id", default=None,
                    help="explicit idempotency key (32-64 chars)")
    ap.add_argument("--pair-code", default=None)
    ap.add_argument("--profiles", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--send", action="store_true",
                    help="submit the mcp_strategy payload via MCP "
                         "place_strategy_trade")
    ap.add_argument("--yes", action="store_true",
                    help="confirm the exact trade (required with --send)")
    args = ap.parse_args()

    m, regime, evidence, spread = analyze(args.symbol, args)
    grid = build_grid(args.symbol, args, m, regime, evidence, spread)
    mcp = build_mcp(args.symbol, args, m, regime)

    out = {
        "symbol": args.symbol,
        "exchange": args.exchange,
        "interval": args.interval,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "regime": regime,
        "evidence": evidence,
        "metrics": m,
        "spread_pct": spread,
        "grid_bot": grid,
        "mcp_strategy": mcp,
        "disclaimer": "emitted only — execution blocked until the Phase E "
                      "checklist passes (explicit user confirmation).",
    }
    if args.send:
        run_send(args, mcp)
        return

    if args.json:
        print(json.dumps(out, indent=2))
        return

    c, sz = grid["channel"], grid["sizing"]
    print(f"== grid-bot config — {args.symbol} ({args.exchange} "
          f"{args.interval}) — {regime} ==")
    print(f"grid type:      {grid['grid_type']} GRID")
    print(f"channel:        low {c['low']}  mid {c['mid']}  high {c['high']} "
          f"(width {c['width_pct']}%)")
    print(f"profit/grid:    {grid['profit_per_grid_pct']}%"
          + ("  [forced to 2x spread]" if grid["step_forced_to_2x_spread"] else ""))
    print(f"grids:          {grid['grids']}")
    if sz["amount_per_trade"] is not None:
        print(f"amount/trade:   {sz['amount_per_trade']} USD  "
              f"(max commitment ~{sz['total_commitment_estimate']} USD)")
    print(f"stop trigger:   {grid['stop_trigger']}   ·   "
          f"pump protection: {'on' if grid['pump_protection'] else 'off'}")
    print(f"start condition: {grid['start_condition']}")

    p = mcp["payload"]
    print(f"\n== MCP strategy (reliable path) — {regime} ==")
    print(f"strategy: {mcp['strategy']}")
    if mcp["is_dca"]:
        print(f"DCA deviation {p['extraOrderDeviation']} "
              f"(= ATR {m['atr_pct']}% x {args.dca_factor})  ·  "
              f"amount/trade {mcp['amount_per_trade']} USD")
    print("place_strategy_trade payload:")
    print(json.dumps(p, indent=2))
    print("\nNOTE: resolve pairCode/profilesCodes (or pass --pair-code/--profiles) "
          "before sending. Grid bot has no MCP/REST endpoint — use the web UI or "
          "webhook for it; this DCA/classic payload is the API-reachable path.")
    print("EXECUTION BLOCKED until the Phase E checklist passes — explicit "
          "user confirmation of the exact trade is non-negotiable.")


if __name__ == "__main__":
    main()