#!/usr/bin/env python3
"""WunderTrading skill — full-universe screener with configurable scan presets.

Fetches the whole Hyperliquid perp universe (or an explicit symbol list on
hyperliquid/binance), filters by liquidity, classifies each candidate's market
regime (via market_regime.py), and ranks by a named scan preset. The preset
decides which regimes are in-scope and how they score — e.g. `grid-neutral`
only wants mean-reversion regimes (chop/squeeze/neutral) and scores trending
names 0, while `trend-classic` ranks trend_up/down for the MCP strategy
surface.

Presets live in scan_presets.json (same directory, editable) and can be
overridden with --presets-file. Stdlib only (urllib). Read-only market data;
never executes trades.

Usage:
  universe_screen.py                       # default preset (grid-neutral), hyperliquid 1h
  universe_screen.py --preset trend-classic
  universe_screen.py --preset grid-neutral --top 20 --min-volume 10000000
  universe_screen.py --symbols BTC,ETH,SOL --preset all
  universe_screen.py --preset grid-neutral --wun-exchange BINANCE_FUTURES
  universe_screen.py --list-presets
  universe_screen.py --preset squeeze --json

Spread source: on hyperliquid the venue's own L2 book is used (public, real
bid/ask). Pass --wun-exchange for a WunderTrading MCP spread lookup instead
(needs WUN_API_KEY/WUN_SECRET_KEY or WT_API_KEY/WT_API_SECRET); note the MCP
get_exchange_markets returns bid==ask==last on Hyperliquid, so it is only
meaningful on non-HL venues. The scan never depends on spread data.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

from market_regime import fetch_candles, compute_metrics, classify, ssl_context

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PRESETS_FILE = os.path.join(HERE, "scan_presets.json")

# Fallback used only when scan_presets.json is missing/empty; the JSON is the
# editable source of truth.
DEFAULT_PRESETS = {
    "grid-neutral": {
        "regime_weights": {"chop_high_volatility": 100, "squeeze": 82,
                           "neutral": 45, "trend_up": 0, "trend_down": 0},
        "min_volume_usd": 5_000_000, "max_symbols": 60,
        "interval": "1h", "limit": 300, "exclude": [],
        "atr_min": 0.8, "atr_max": 4.0,
        "step_atr_factor": 0.5, "step_min": 0.1, "step_max": 2.0,
    },
    "all": {
        "regime_weights": {"chop_high_volatility": 100, "squeeze": 82,
                           "neutral": 45, "trend_up": 30, "trend_down": 25},
        "min_volume_usd": 5_000_000, "max_symbols": 100,
        "interval": "1h", "limit": 300, "exclude": [],
        "atr_min": 0.0, "atr_max": 6.0,
        "step_atr_factor": 0.5, "step_min": 0.1, "step_max": 2.0,
    },
}

# Regime -> archetype label for the report (see references/grid-bot.md and
# strategy-playbook.md §3).
ARCHETYPE = {
    "chop_high_volatility": "Neutral Grid (mean-reversion)",
    "squeeze": "Neutral Grid tight + Stop Trigger",
    "neutral": "probe Neutral/Infinite grid",
    "trend_up": "Long Grid / classic LONG",
    "trend_down": "Short Grid (futures) / flat (spot)",
}


def load_presets(path):
    presets = json.loads(json.dumps(DEFAULT_PRESETS))  # deep copy
    p = path or DEFAULT_PRESETS_FILE
    if os.path.isfile(p):
        try:
            data = json.load(open(p))
        except Exception as exc:
            print(f"warning: cannot parse presets file {p}: {exc} — "
                  f"using built-ins", file=sys.stderr)
            return presets
        for name, body in data.items():
            if name.startswith("_"):
                continue
            if not isinstance(body, dict):
                continue
            cur = presets.setdefault(name, {})
            for k, v in body.items():
                if k == "regime_weights" and isinstance(v, dict):
                    cur.setdefault("regime_weights", {}).update(v)
                else:
                    cur[k] = v
    return presets


def hyperliquid_universe():
    """[(name, dayNtlVlm_usd, oi_notional_usd, markPx, maxLev)] sorted by volume.

    openInterest is in BASE units (not USD) — convert via markPx.
    dayNtlVlm is already USD notional."""
    req = urllib.request.Request(
        "https://api.hyperliquid.xyz/info",
        data=json.dumps({"type": "metaAndAssetCtxs"}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as r:
        d = json.loads(r.read())
    meta, ctxs = d[0], d[1]
    rows = []
    for u, c in zip(meta["universe"], ctxs):
        mark = float(c.get("markPx", 0) or 0)
        vol = float(c.get("dayNtlVlm", 0) or 0)
        if mark <= 0 or vol <= 0:
            continue
        oi = float(c.get("openInterest", 0) or 0) * mark
        rows.append((u["name"], vol, oi, mark, u.get("maxLeverage")))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def wunder_spreads(exchange_code):
    """{symbol: spread_pct} from the WunderTrading MCP (optional).

    Keys read from WUN_API_KEY/WUN_SECRET_KEY or the fleet-provisioned
    WT_API_KEY/WT_API_SECRET (browser-debug/secrets/runtime/wt.env)."""
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
    except Exception as exc:
        print(f"note: spread lookup failed ({exc}) — skipping", file=sys.stderr)
        return {}
    out = {}
    for m in markets:
        if m.get("last"):
            out[m["viewSymbol"]] = (m["ask"] - m["bid"]) / m["last"] * 100
    return out


def hyperliquid_spreads(names):
    """{symbol: spread_pct} from the venue's own L2 book (public, real bid/ask).

    The WunderTrading MCP get_exchange_markets returns bid == ask == last on
    Hyperliquid, so its (ask-bid)/last spread is always 0.0 — this endpoint is
    the accurate source. levels[0] = bids (best first), levels[1] = asks."""
    out = {}
    for name in names:
        body = json.dumps({"type": "l2Book", "coin": name}).encode()
        req = urllib.request.Request(
            "https://api.hyperliquid.xyz/info", data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20,
                                        context=ssl_context()) as r:
                d = json.loads(r.read())
            lv = d.get("levels") or []
            if len(lv) >= 2 and lv[0] and lv[1]:
                bid = float(lv[0][0]["px"])
                ask = float(lv[1][0]["px"])
                mid = (bid + ask) / 2
                if mid:
                    out[name] = (ask - bid) / mid * 100
        except Exception:
            continue
    return out


def derived_step(m, preset):
    """Grid step (%) derived from volatility: clamp(ATR% * factor, min, max).

    Returns None when the preset defines no step_atr_factor (e.g. classic
    strategies, which have no grid step)."""
    factor = preset.get("step_atr_factor")
    if not factor:
        return None
    atr = m["atr_pct"] or 0.0
    return round(max(preset.get("step_min", 0.1),
                     min(preset.get("step_max", 3.0), atr * factor)), 3)


def score(regime, m, preset, spread_pct=None):
    """Preset-aware score, or None when the regime is out of scope (weight 0).

    Spread penalty relative to the ATR-derived step: > 0.1% costs 2;
    > step/2 costs 4 (half-step guard); >= step costs 20 (unharvestable)."""
    w = preset.get("regime_weights", {}).get(regime, 0)
    if w <= 0:
        return None
    s = float(w)
    adx = m["adx14"] or 20.0
    atr = m["atr_pct"] or 0.0
    rsi = m["rsi14"]
    if regime in ("chop_high_volatility", "neutral"):
        s += min(atr, 4.0) * 4.0            # range to harvest
    elif regime == "squeeze":
        s += (100 - (m["bb_width_pctile"] or 50)) * 0.15  # tighter = stronger
    elif regime in ("trend_up", "trend_down"):
        s += min(adx, 40.0) / 40.0 * 2.0     # trend strength
    if rsi is not None and (rsi > 78 or rsi < 22):
        s -= 1.5                             # overheated / oversold
    if atr > 4.0:
        s -= 4.0                             # wild tape
    if spread_pct is not None:
        if spread_pct > 0.1:
            s -= 2.0
        step = derived_step(m, preset)
        if step:
            if spread_pct > step / 2:
                s -= 4.0
            if spread_pct >= step:
                s -= 20.0
    return round(s, 2)


def flags_of(regime, m, preset, vol_usd, oi_usd, spread_pct=None):
    fl = []
    atr = m["atr_pct"] or 0.0
    rsi = m["rsi14"]
    if preset.get("atr_min") and atr < preset["atr_min"]:
        fl.append("low_range")
    if preset.get("atr_max") and atr > preset["atr_max"]:
        fl.append("wild_tape")
    if rsi is not None and (rsi > 78 or rsi < 22):
        fl.append("overheated" if rsi > 78 else "oversold")
    if spread_pct is not None:
        step = derived_step(m, preset)
        if spread_pct > 0.1:
            fl.append("spread>0.1%")
        if step and spread_pct > step / 2:
            fl.append("spread>half_step")
        if step and spread_pct >= step:
            fl.append("unharvestable")
    if vol_usd < 1e7:
        fl.append("thin_vol")
    if oi_usd < 5e6:
        fl.append("thin_oi")
    return fl


def screen(cfg, symbols=None, spreads=None):
    """Returns a ranked list of result dicts (out-of-scope regimes dropped)."""
    preset = cfg["preset"]
    rows = []

    if symbols is not None:
        universe = None
        for s in symbols:
            rows.append({"name": s, "vol_usd": None, "oi_usd": None,
                         "mark_px": None, "max_lev": None})
    else:
        universe = hyperliquid_universe()
        for name, vol, oi, mark, lev in universe:
            if vol < cfg["min_volume_usd"]:
                continue
            rows.append({"name": name, "vol_usd": vol, "oi_usd": oi,
                         "mark_px": mark, "max_lev": lev})
            if len(rows) >= cfg["max_symbols"]:
                break

    if spreads is None and cfg["exchange"] == "hyperliquid":
        spreads = hyperliquid_spreads([r["name"] for r in rows])
    spreads = spreads or {}

    exclude = [e.lower() for e in preset.get("exclude", [])]
    out = []
    for r in rows:
        name = r["name"]
        if any(e in name.lower() for e in exclude):
            continue
        try:
            cl = fetch_candles(cfg["exchange"], name, cfg["interval"],
                               cfg["limit"], cfg["market"])
            if len(cl) < 60:
                continue
            m = compute_metrics(cl)
            regime, ev = classify(m)
            sp = None
            if spreads:
                for vs, v in spreads.items():
                    if vs == name or vs.split("-")[0] == name:
                        sp = v
                        break
            sc = score(regime, m, preset, sp)
            if sc is None:
                continue
            out.append({
                "symbol": name, "regime": regime, "metrics": m,
                "evidence": ev, "score": sc, "spread_pct": sp,
                "step": derived_step(m, preset),
                "archetype": ARCHETYPE.get(regime, regime),
                "vol_usd": r.get("vol_usd"), "oi_usd": r.get("oi_usd"),
                "mark_px": r.get("mark_px"),
                "flags": flags_of(regime, m, preset,
                                  r.get("vol_usd") or 0, r.get("oi_usd") or 0,
                                  sp),
            })
        except Exception as exc:
            print(f"skip {name}: {exc}", file=sys.stderr)
        time.sleep(0.08)

    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", default="grid-neutral",
                    help="scan preset from scan_presets.json")
    ap.add_argument("--presets-file", default=None,
                    help="override presets JSON path")
    ap.add_argument("--list-presets", action="store_true")
    ap.add_argument("--exchange", default="hyperliquid",
                    choices=["hyperliquid", "binance"])
    ap.add_argument("--symbols", help="explicit comma-separated list; skips "
                    "universe fetch (works on both exchanges)")
    ap.add_argument("--interval", help="override preset interval "
                    "(15m|1h|4h|1d)")
    ap.add_argument("--limit", type=int, help="override preset candle limit")
    ap.add_argument("--min-volume", type=int, help="override preset liquidity "
                    "floor (USD)")
    ap.add_argument("--top", type=int, default=None,
                    help="override preset max_symbols")
    ap.add_argument("--market", default="spot", choices=["spot", "futures"],
                    help="binance only")
    ap.add_argument("--wun-exchange", default=None,
                    help="WunderTrading exchange code for spread lookup")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    presets = load_presets(args.presets_file)
    if args.list_presets:
        print("== scan presets ==")
        for name, body in sorted(presets.items()):
            print(f"{name}: {body.get('description', '(no description)')}")
        return

    if args.preset not in presets:
        sys.exit(f"unknown preset '{args.preset}'. available: "
                 f"{', '.join(sorted(presets))}")

    preset = presets[args.preset]
    cfg = {
        "preset": preset,
        "exchange": args.exchange,
        "market": args.market,
        "interval": args.interval or preset.get("interval", "1h"),
        "limit": args.limit or preset.get("limit", 300),
        "min_volume_usd": args.min_volume if args.min_volume is not None
                          else preset.get("min_volume_usd", 0),
        "max_symbols": args.top if args.top is not None
                       else preset.get("max_symbols", 60),
    }

    symbols = ([s.strip() for s in args.symbols.split(",") if s.strip()]
               if args.symbols else None)
    spreads = wunder_spreads(args.wun_exchange) if args.wun_exchange else None
    results = screen(cfg, symbols=symbols, spreads=spreads)

    if args.json:
        print(json.dumps({
            "preset": args.preset, "exchange": cfg["exchange"],
            "interval": cfg["interval"],
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "min_volume_usd": cfg["min_volume_usd"],
            "results": results,
        }, indent=2))
        return

    header = (f"== universe screen — {args.preset} ({cfg['exchange']} "
              f"{cfg['interval']}), {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ==")
    print(header)
    print(f"{'#':>2} {'sym':<9} {'regime':<21} {'ADX':>5} {'ATR%':>6} {'RSI':>5} "
          f"{'BBpct':>6} {'d7%':>7} {'spread%':>7} {'step%':>6} {'vol$M':>7} "
          f"{'score':>6}  flags")
    for i, r in enumerate(results, 1):
        m = r["metrics"]
        vol = f"{r['vol_usd']/1e6:.1f}" if r["vol_usd"] is not None else "n/a"
        sp = f"{r['spread_pct']:.3f}" if r["spread_pct"] is not None else "n/a"
        st = f"{r['step']:.3f}" if r.get("step") is not None else "n/a"
        flags = ",".join(r["flags"]) if r["flags"] else "-"
        print(f"{i:>2} {r['symbol']:<9} {r['regime']:<21} {m['adx14']:>5} "
              f"{m['atr_pct']:>6} {m['rsi14']:>5} {m['bb_width_pctile']:>6} "
              f"{m['change_pct']['7d']:>7} {sp:>7} {st:>6} {vol:>7} "
              f"{r['score']:>6}  {flags}")

    if not results:
        print("\n(no in-scope candidates — the market regime may not fit this "
              "preset; try `--preset all` or a different interval)")
        return
    top = results[0]
    print(f"\nTOP: {top['symbol']} — {top['regime']} "
          f"(score {top['score']})")
    print(f"archetype: {top['archetype']}")
    if top.get("step") is not None:
        print(f"derived grid step: {top['step']}% "
              f"(= ATR {top['metrics']['atr_pct']}% x "
              f"{preset.get('step_atr_factor')})")
    print(f"evidence: {json.dumps(top['evidence'])}")
    print("EXECUTION BLOCKED until the SKILL.md Phase E checklist passes — "
          "explicit user confirmation of the exact trade is non-negotiable.")


if __name__ == "__main__":
    main()