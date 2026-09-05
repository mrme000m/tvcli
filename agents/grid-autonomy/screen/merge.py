#!/usr/bin/env python3
"""Grid-autonomy screen merger — HL perps + Binance spot, ranked, tvcli-confluenced.

Runs the wundertrading `universe_screen` presets on BOTH venues, merges the
rankings, then enriches the top-N with tvcli multi-account `/hunt` confluence
(squeeze + choppiness + mtf-confluence) before emitting candidate_report.json.

Venues (matches the funded portfolio):
  hyperliquid  perps — Long/Short/Neutral grids (300 USD sleeve)
  binance      SPOT  — Long/flat only, Short rejected downstream (200 USD sleeve)

Stdlib only. Read-only market data; never executes trades.

Usage:
  merge.py --json                                   # full report on stdout
  merge.py --top 15 --confluence-top 10             # tune shortlist sizes
  merge.py --no-confluence                          # skip tvcli /hunt (fast)
  merge.py --out agents/grid-autonomy/state/candidate_report.json
  merge.py --presets grid-neutral,grid-directional

Confluence bonus (added to preset score, then re-sorted):
  mtf-confluence HTF alignment agrees with regime direction  +2.0
  squeeze fires in harvest direction (chop/squeeze regimes)  +1.5
  choppiness confirms chop (CI > 61.8) for mean-reversion    +1.0
  RSI overheated flag on direction side                      -3.0
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
WUN_SCRIPTS = os.path.normpath(os.path.join(
    HERE, "..", "..", "..", ".agents", "skills", "wundertrading", "scripts"))
sys.path.insert(0, WUN_SCRIPTS)

from market_regime import fetch_candles, compute_metrics, classify, ssl_context  # noqa: E402
from universe_screen import (load_presets, screen, hyperliquid_spreads,  # noqa: E402
                             derived_step, ARCHETYPE, flags_of, score)

# Worker A's execution/spreads.py — import defensively; stub returns {} so the
# merge still works before that module lands.
try:
    sys.path.insert(0, os.path.join(HERE, "..", "execution"))
    from spreads import binance_spreads  # noqa: E402
    HAS_BINANCE_SPREADS = True
except Exception:  # pragma: no cover - exercised when spreads.py is absent
    HAS_BINANCE_SPREADS = False

    def binance_spreads(symbols):
        return {}

TVCLI_SERVER = os.environ.get("TVCLI_SERVER", "http://127.0.0.1:8765")
CONFLUENCE_SKILLS = ("squeeze", "choppiness", "mtf-confluence")

# global dead-tape floor (see main()); below this ATR a grid pays more fees
# than it harvests, whatever the preset weights say
MIN_ATR_PCT = 0.25

# how many top candidates get the fills simulation (they are the only ones
# that can reach a slot this cycle; the rest keep their heuristic score)
EV_TOP_N = 20

# round-trip cost per venue (%, both legs): exchange maker fees + the
# WunderTrading builder fee where it applies. Imported from
# execution/guardrails so the screen, the sizing, and the deploy gate all
# charge the same fee model.
try:
    from guardrails import ROUND_TRIP_FEE_PCT  # noqa: E402  (path set above)
except Exception:  # standalone/screen-only runs without the package
    ROUND_TRIP_FEE_PCT = {"hyperliquid": 0.10, "binance": 0.20}


def apply_harvest_ev(cands, interval="1h", limit=300, top_n=EV_TOP_N):
    """Simulate each top candidate's own grid over its recent candles.

    Adds expected_fills_per_24h / harvest_gross_pct_24h /
    harvest_net_pct_24h and adjusts the heuristic score by expected daily
    NET harvest (bonus capped at +3; dead tape < 0.1%/day net costs −10).
    Fail-open: a fetch failure leaves the heuristic score untouched."""
    try:
        sys.path.insert(0, os.path.join(HERE, "..", "policy"))
        from stagnation import INTERVAL_HOURS, simulate_grid_fills
    except Exception:
        return cands
    interval_h = INTERVAL_HOURS.get(interval, 1.0)
    for c in cands[:top_n]:
        try:
            market = "futures" if c["venue"] == "hyperliquid" else "spot"
            cl = fetch_candles(c["venue"], fetch_symbol(c["venue"], c["symbol"]),
                               interval, limit, market)
            closes = [row[3] for row in cl]
            if len(closes) < 60:
                continue
            step = c.get("step") or 0.5
            fills, _ = simulate_grid_fills(closes, step)
            window_h = len(closes) * interval_h
            per24 = fills / window_h * 24 if window_h else 0.0
            rt_fee = ROUND_TRIP_FEE_PCT.get(c["venue"], 0.15)
            c["expected_fills_per_24h"] = round(per24, 2)
            c["harvest_gross_pct_24h"] = round(per24 * step, 3)
            c["harvest_net_pct_24h"] = round(per24 * max(step - rt_fee, 0.0), 3)
            if c["harvest_net_pct_24h"] < 0.1:
                # below fee noise: whatever the regime says, this tape
                # cannot pay for its own round trips
                c["score"] = round(c["score"] - 10.0, 2)
            else:
                c["score"] = round(c["score"] + min(c["harvest_net_pct_24h"], 3.0), 2)
            c["ev_adjusted"] = True
        except Exception as exc:
            print(f"ev {c.get('venue')}:{c.get('symbol')} failed: {exc}",
                  file=sys.stderr)
            continue
    cands.sort(key=lambda x: x["score"], reverse=True)
    return cands


def binance_spot_universe(min_quote_vol_usd=5_000_000, max_symbols=60):
    """[(symbol, quoteVol)] top Binance SPOT USDT pairs by 24h quote volume."""
    url = "https://api.binance.com/api/v3/ticker/24hr"
    req = urllib.request.Request(url, headers={"User-Agent": "tvcli-grid-autonomy/1.0"})
    with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as r:
        tickers = json.loads(r.read())
    rows = []
    for t in tickers:
        s = t.get("symbol", "")
        if not s.endswith("USDT"):
            continue
        base = s[:-4]
        # skip leveraged/bear-bull tokens + fiat stables
        if base.endswith(("UP", "DOWN", "BULL", "BEAR")) or base in (
                "USDC", "DAI", "FDUSD", "TUSD", "USDP", "AEUR", "XUSD",
                "RLUSD", "USD1", "USDE", "EURI", "PYUSD", "EUR", "USTC",
                "FRAX", "USDF", "USDG", "USDM", "BBUSD", "BUSD"):
            continue
        try:
            qv = float(t.get("quoteVolume", 0))
        except (TypeError, ValueError):
            continue
        if qv >= min_quote_vol_usd:
            rows.append((s, qv))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows[:max_symbols]


def screen_binance(preset_name, preset, interval="1h", limit=300,
                   min_volume_usd=5_000_000, max_symbols=60):
    """Preset-scored Binance SPOT ranking (same weights/steps as HL leg)."""
    universe = binance_spot_universe(min_volume_usd, max_symbols)
    symbols = [s for s, _ in universe]
    spreads = binance_spreads(symbols) if HAS_BINANCE_SPREADS else {}
    out = []
    for symbol, qv in universe:
        try:
            cl = fetch_candles("binance", symbol, interval, limit, "spot")
            if len(cl) < 60:
                continue
            m = compute_metrics(cl)
            regime, ev = classify(m)
            spread = spreads.get(symbol, spreads.get(symbol[:-4]))
            sc = score(regime, m, preset, spread)
            if sc is None:
                continue
            base = symbol[:-4]
            out.append({
                "symbol": base, "venue": "binance", "tv_symbol": f"BINANCE:{symbol}",
                "regime": regime, "metrics": m, "evidence": ev, "score": sc,
                "spread_pct": spread, "step": derived_step(m, preset),
                "archetype": ARCHETYPE.get(regime, regime),
                "vol_usd": qv, "oi_usd": None, "mark_px": m["price"],
                "flags": flags_of(regime, m, preset, qv, 0, None),
                "preset": preset_name,
            })
        except Exception as exc:
            print(f"skip binance:{symbol}: {exc}", file=sys.stderr)
        time.sleep(0.05)
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def screen_hyperliquid(preset_name, preset, interval="1h", limit=300,
                       min_volume_usd=5_000_000, max_symbols=60):
    cfg = {"preset": preset, "exchange": "hyperliquid", "market": "futures",
           "interval": interval, "limit": limit,
           "min_volume_usd": min_volume_usd, "max_symbols": max_symbols}
    results = screen(cfg)
    for r in results:
        r["venue"] = "hyperliquid"
        r["tv_symbol"] = f"BINANCE:{r['symbol']}USDT"
        r["preset"] = preset_name
    return results


def tv_hunt(skill, tv_symbols, timeframe="1H", bars=180):
    """One tvcli /hunt batch → {tv_symbol: result|error}."""
    body = json.dumps({"skill": skill, "timeframe": timeframe, "bars": bars,
                       "symbols": tv_symbols}).encode()
    req = urllib.request.Request(
        f"{TVCLI_SERVER}/hunt", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.loads(r.read())
    return resp.get("symbols", {})


def apply_confluence(cands, timeframe="1H", bars=180):
    """Enrich top candidates with tvcli /hunt signals; returns bonus map."""
    tv_syms = list({c["tv_symbol"] for c in cands})
    hunts = {}
    for skill in CONFLUENCE_SKILLS:
        try:
            hunts[skill] = tv_hunt(skill, tv_syms, timeframe, bars)
        except Exception as exc:
            print(f"confluence {skill} failed: {exc} — continuing without it",
                  file=sys.stderr)
            hunts[skill] = {}
    for c in cands:
        bonus, notes = 0.0, []
        tv = c["tv_symbol"]
        mtf = (hunts.get("mtf-confluence", {}).get(tv) or {})
        sq = (hunts.get("squeeze", {}).get(tv) or {})
        ch = (hunts.get("choppiness", {}).get(tv) or {})
        c["confluence"] = {
            "mtf-confluence": (mtf.get("result") is not None),
            "squeeze": (sq.get("result") is not None),
            "choppiness": (ch.get("result") is not None),
            "errors": {k: (v.get("error") if isinstance(v, dict) else None)
                       for k, v in (("mtf", mtf), ("sq", sq), ("ch", ch))},
        }
        # Direction agreement: only reward when HTF read exists and matches.
        try:
            res = mtf.get("result") or {}
            narrative = json.dumps(res.get("narrative", res))[:2000].lower()
            regime = c["regime"]
            if regime == "trend_up" and ("bull" in narrative or "long" in narrative):
                bonus += 2.0
                notes.append("mtf-aligned-long")
            elif regime == "trend_down" and ("bear" in narrative or "short" in narrative):
                bonus += 2.0
                notes.append("mtf-aligned-short")
            elif regime in ("chop_high_volatility", "squeeze", "neutral") and \
                    ("range" in narrative or "chop" in narrative or "neutral" in narrative):
                bonus += 2.0
                notes.append("mtf-range-agree")
        except Exception:
            pass
        if sq.get("result") and c["regime"] in ("chop_high_volatility", "squeeze"):
            bonus += 1.5
            notes.append("squeeze-fires")
        if ch.get("result") and c["regime"] in ("chop_high_volatility", "neutral"):
            bonus += 1.0
            notes.append("choppy-confirmed")
        m = c.get("metrics") or {}
        rsi = m.get("rsi14")
        if rsi is not None and (rsi > 78 or rsi < 22):
            bonus -= 3.0
            notes.append("rsi-overheated")
        # Venue guardrail: Binance SPOT cannot short — directional short is flat.
        if c["venue"] == "binance" and c["regime"] == "trend_down":
            bonus -= 25.0
            notes.append("spot-no-short")
        c["confluence_bonus"] = round(bonus, 2)
        c["confluence_notes"] = notes
        c["score_final"] = round(c["score"] + bonus, 2)
    cands.sort(key=lambda x: x["score_final"], reverse=True)
    return cands


def config_confirm_interval(default="4h"):
    """Read screen.confirm_interval from ../config.yaml (stdlib line scan)."""
    try:
        path = os.path.join(HERE, "..", "config.yaml")
        in_screen = False
        with open(path) as fh:
            for line in fh:
                s = line.rstrip("\n")
                if s.startswith("screen:"):
                    in_screen = True
                    continue
                if not s.startswith(" ") or not s.strip():
                    in_screen = False
                    continue
                if in_screen and s.lstrip().startswith("confirm_interval:"):
                    val = s.split(":", 1)[1].strip().split("#")[0].strip()
                    return val.strip("'\"") or default
    except Exception:
        pass
    return default


def fetch_symbol(venue, symbol):
    """Full ticker for the public candle APIs (binance needs BTCUSDT, not BTC).

    Candidates carry the BASE symbol; the candle endpoints need the full
    pair. Without this, every binance 4h confirmation 400s (bad symbol) and
    the HTF gate silently fail-opens (verified live 2026-09-05)."""
    s = (symbol or "").upper().replace("/", "")
    if venue == "binance" and not s.endswith(("USDT", "USDC", "BUSD")):
        return f"{s}USDT"
    return s


def drop_dead_tape(cands, min_atr_pct=MIN_ATR_PCT):
    """(alive, dropped) — candidates below the global ATR floor cannot pay
    their own round-trip fees, whatever the preset weights say."""
    alive, dropped = [], []
    for c in cands:
        atr = (c.get("metrics") or {}).get("atr_pct", 0.0)
        if atr < min_atr_pct:
            dropped.append({"venue": c.get("venue"), "symbol": c.get("symbol"),
                            "atr_pct": atr})
            continue
        alive.append(c)
    return alive, dropped


def confirm_directional(cands, interval="4h"):
    """Drop trend candidates whose HTF regime disagrees with the 1h regime.

    trend_up on 1h is kept only if 4h is trend_up or neutral; trend_down only
    if 4h is trend_down or neutral. Mean-reversion regimes are untouched.
    Each kept candidate gets a confirm_4h annotation; failures keep the
    candidate (fail-open here — the guardrail layer is the fail-closed gate).
    """
    out = []
    for c in cands:
        if c.get("regime") not in ("trend_up", "trend_down"):
            c.setdefault("confirm_4h", {"skipped": "non-trend"})
            out.append(c)
            continue
        try:
            market = "futures" if c["venue"] == "hyperliquid" else "spot"
            cl = fetch_candles(c["venue"], fetch_symbol(c["venue"], c["symbol"]),
                               interval, 200, market)
            m = compute_metrics(cl)
            regime4h, ev4h = classify(m)
            c["confirm_4h"] = {"interval": interval, "regime": regime4h,
                               "evidence": ev4h}
            if c["regime"] == "trend_up" and regime4h not in ("trend_up", "neutral"):
                c["dropped_confirm"] = f"4h {regime4h} disagrees with 1h trend_up"
                continue
            if c["regime"] == "trend_down" and regime4h not in ("trend_down", "neutral"):
                c["dropped_confirm"] = f"4h {regime4h} disagrees with 1h trend_down"
                continue
            out.append(c)
        except Exception as exc:
            print(f"confirm {interval} failed for {c['venue']}:{c['symbol']}: {exc}",
                  file=sys.stderr)
            c["confirm_4h"] = {"interval": interval, "error": str(exc)[:120]}
            out.append(c)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--presets", default="grid-neutral,grid-directional")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--top", type=int, default=15,
                    help="shortlist per preset per venue before merge")
    ap.add_argument("--confluence-top", type=int, default=10)
    ap.add_argument("--min-volume", type=int, default=5_000_000)
    ap.add_argument("--no-confluence", action="store_true")
    ap.add_argument("--confirm", default=None,
                    help="HTF interval for directional confirmation (default: config)")
    ap.add_argument("--no-confirm", action="store_true",
                    help="skip directional 4h re-confirmation")
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    confirm_interval = args.confirm or config_confirm_interval()

    presets = load_presets(None)
    wanted = [p.strip() for p in args.presets.split(",") if p.strip()]
    for p in wanted:
        if p not in presets:
            sys.exit(f"unknown preset '{p}'. available: {', '.join(sorted(presets))}")

    hl_all, bn_all = [], []
    for pname in wanted:
        preset = presets[pname]
        hl_all.extend(screen_hyperliquid(
            pname, preset, args.interval, args.limit, args.min_volume, args.top))
        bn_all.extend(screen_binance(
            pname, preset, args.interval, args.limit, args.min_volume, args.top))

    # de-dupe by (venue, symbol), keep best score
    merged = {}
    for r in hl_all + bn_all:
        key = (r["venue"], r["symbol"])
        if key not in merged or r["score"] > merged[key]["score"]:
            merged[key] = r
    cands = sorted(merged.values(), key=lambda x: x["score"], reverse=True)

    # global dead-tape floor: no matter the preset, a candidate whose price
    # barely moves cannot pay for its own fees (USD-peg wobble, flat stables
    # that slip past the universe filter, pinned/illiquid tapes). 0.25% ATR
    # is below every preset's harvestable range yet above peg noise.
    cands, dropped_floor = drop_dead_tape(cands)

    if not args.no_confirm:
        cands = confirm_directional(cands, confirm_interval)
    else:
        for c in cands:
            c["confirm_4h"] = {"skipped": "--no-confirm"}

    # ── expected-value pass ────────────────────────────────────────────
    # The regime-weighted score rewards volatility, but profit = fills/day ×
    # net step. A high-ATR token that never recrosses its lines scores high
    # and harvests nothing. Simulate the candidate's OWN grid over the same
    # candles the screen fetched, then adjust the ranking by expected daily
    # harvest so the fleet deploys tokens that actually oscillate.
    cands = apply_harvest_ev(cands, args.interval, args.limit, top_n=EV_TOP_N)

    shortlist = cands[: args.confluence_top]
    if not args.no_confluence and shortlist:
        apply_confluence(shortlist)
        # re-merge: confluenced shortlist re-sorted, tail keeps raw score
        for c in cands:
            c.setdefault("score_final", c["score"])
            c.setdefault("confluence_bonus", 0.0)
            c.setdefault("confluence_notes", [])
        cands.sort(key=lambda x: x["score_final"], reverse=True)
    else:
        for c in cands:
            c["score_final"] = c["score"]
            c["confluence_bonus"] = 0.0
            c["confluence_notes"] = ["confluence-skipped"]

    report = {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "presets": wanted, "interval": args.interval,
        "venues": {"hyperliquid": "perps (Long/Short/Neutral)",
                   "binance": "SPOT (Long/flat only — Short rejected)"},
        "top": cands[0] if cands else None,
        "results": cands,
        "dropped_dead_tape": dropped_floor,
        "disclaimer": "screening only — execution blocked until guardrails pass.",
    }
    text = json.dumps(report, indent=2)
    if args.out:
        open(args.out, "w").write(text)
        print(f"wrote {args.out} ({len(cands)} candidates)")
    if args.json or not args.out:
        print(text)
        return
    print(f"== merge screen ({args.interval}) ==")
    for i, r in enumerate(cands[: args.top], 1):
        print(f"{i:>2} {r['venue'][:4]}:{r['symbol']:<9} {r['regime']:<21} "
              f"score {r['score_final']:>6} (base {r['score']})  "
              f"{','.join(r['confluence_notes']) or '-'}")
    if cands:
        print(f"\nTOP: {cands[0]['venue']}:{cands[0]['symbol']} — "
              f"{cands[0]['regime']} (final {cands[0]['score_final']})")


if __name__ == "__main__":
    main()
