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

tvcli fitness bonus (numeric signals, added to preset score, then re-sorted):
  moves large (ATR% ≥ 1.5) / mtf volRatio ≥ 1.5              +1.0 each
  moves fast: squeeze released + momentum |m| ≥ 20           +1.0
              extended squeeze (≥ 6 bars coiled)             +1.5
  choppiness CHOP ≥ 61.8 with range regime (harvestable)     +1.5
  choppiness CHOP ≤ 38.2 with trend regime (clean trend)     +1.0
  mtf-confluence composite agrees with regime direction      +2.0 (range +1.0)
  dvi trend agrees with regime direction                     +1.0
  RSI overheated flag on direction side                      -3.0
  binance spot short regime (can't trade it)                 -25.0
Positive bonus is capped at +6.0 — confluence refines, never dominates.
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
CONFLUENCE_SKILLS = ("squeeze", "choppiness", "mtf-confluence", "dvi")
TVCLI_BONUS_CAP = 6.0

# global dead-tape floor (see main()); below this ATR a grid pays more fees
# than it harvests, whatever the preset weights say
MIN_ATR_PCT = 0.25

# how many top candidates get the fills simulation (they are the only ones
# that can reach a slot this cycle; the rest keep their heuristic score)
EV_TOP_N = 20

# default scan breadth: moderately significant tokens (≥ $2M 24h quote
# volume, up to 100 symbols per venue) so the EV + tvcli passes — not a
# narrow hand-picked list — decide what gets a slot.
DEFAULT_MIN_VOLUME_USD = 2_000_000
DEFAULT_MAX_SYMBOLS = 100

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


def retry_urlopen_json(req, tries=3, timeout=30, backoff_s=2.0):
    """GET a JSON body with retry + backoff.

    The widened universe leans on ONE Binance 24h-ticker call and ONE
    Hyperliquid meta call per run; a transient SSL handshake timeout or a
    rate-limit blip there used to crash the whole screen (daemon saw
    `screen-error`, zero candidates). Retry with linear backoff instead —
    the per-symbol candle loops already fail soft, the universe fetch must
    too."""
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout,
                                        context=ssl_context()) as r:
                return json.loads(r.read())
        except Exception as exc:
            last = exc
            if i + 1 < tries:
                time.sleep(backoff_s * (i + 1))
    raise last


def binance_spot_universe(min_quote_vol_usd=5_000_000, max_symbols=60):
    """[(symbol, quoteVol)] top Binance SPOT USDT pairs by 24h quote volume."""
    url = "https://api.binance.com/api/v3/ticker/24hr"
    req = urllib.request.Request(url, headers={"User-Agent": "tvcli-grid-autonomy/1.0"})
    tickers = retry_urlopen_json(req, timeout=30)
    rows = []
    for t in tickers:
        s = t.get("symbol", "")
        if not s.endswith("USDT"):
            continue
        base = s[:-4]
        # skip leveraged/bear-bull tokens + fiat stables; skip non-ASCII
        # tickers (e.g. 币安人生USDT) — the public candle API 400s on them
        # anyway, but the URL encode fails first with an ascii codec error
        # that shows up as a per-symbol skip line every run
        if not s.isascii() or base.endswith(("UP", "DOWN", "BULL", "BEAR")) \
                or base in (
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
                   min_volume_usd=DEFAULT_MIN_VOLUME_USD,
                   max_symbols=DEFAULT_MAX_SYMBOLS, cache=None):
    """Preset-scored Binance SPOT ranking (same weights/steps as HL leg).

    `cache` (optional dict keyed (symbol, interval, limit)) is shared across
    presets so the same 1h candles are fetched once per symbol, not once per
    preset — the widened universe (100 symbols × 2 presets) stays bounded."""
    universe = binance_spot_universe(min_volume_usd, max_symbols)
    symbols = [s for s, _ in universe]
    spreads = binance_spreads(symbols) if HAS_BINANCE_SPREADS else {}
    out = []
    for symbol, qv in universe:
        try:
            ck = (symbol, interval, limit)
            if cache is not None and ck in cache:
                cl = cache[ck]
            else:
                cl = fetch_candles("binance", symbol, interval, limit, "spot")
                if cache is not None:
                    cache[ck] = cl
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
                       min_volume_usd=DEFAULT_MIN_VOLUME_USD,
                       max_symbols=DEFAULT_MAX_SYMBOLS):
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


def _rnum(res, *path, default=None):
    """Numeric field deep inside a hunt result (result.structure.*)."""
    if not isinstance(res, dict):
        return default
    cur = res
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    try:
        v = float(cur)
        return v if v == v else default  # NaN guard
    except (TypeError, ValueError):
        return default


def tvcli_fitness(c, mtf=None, sq=None, ch=None, dvi=None):
    """(bonus, notes, fit) — numeric tvcli fitness read for one candidate.

    Pure function: no network. `mtf`/`sq`/`ch`/`dvi` are the per-symbol hunt
    result dicts ({"result": {...}} or {}). A missing skill contributes 0 —
    the heuristic score stands alone when tvcli is down (fail-soft).

    The weighting answers "which tokens move large and fast":
      * large  — ATR% ≥ 1.5 and mtf volRatio ≥ 1.5 (volatility expansion)
      * fast   — squeeze released with momentum, or a ≥ 6-bar squeeze coil
                 about to fire, or DVI trend agreeing with the 1h regime
      * harvestable — CHOP ≥ 61.8 in a range regime (lines recross often)
    """
    regime = c.get("regime")
    venue = c.get("venue")
    m = c.get("metrics") or {}
    bonus, notes, fit = 0.0, [], {}

    sqr = (sq or {}).get("result") or {}
    squeeze_on = bool((sqr.get("structure") or {}).get("squeezeOn"))
    sq_mom = _rnum(sqr, "structure", "momentum")
    sq_bars = _rnum(sqr, "structure", "squeezeBars")
    if sqr:
        fit.update({"squeeze_on": squeeze_on,
                    "squeeze_momentum": sq_mom, "squeeze_bars": sq_bars})
        if squeeze_on and sq_bars is not None and sq_bars >= 6:
            bonus += 1.5
            notes.append("squeeze-coiled(breakout pending)")
        elif not squeeze_on and sq_mom is not None and abs(sq_mom) >= 20:
            bonus += 1.0
            notes.append("momentum-release")
        elif squeeze_on and regime in ("chop_high_volatility", "squeeze",
                                       "neutral"):
            bonus += 0.5
            notes.append("squeeze-active-range")

    chr_ = (ch or {}).get("result") or {}
    chop_val = _rnum(chr_, "structure", "chop")
    if chop_val is not None:
        fit["chop"] = chop_val
        if chop_val >= 61.8 and regime in ("chop_high_volatility", "neutral",
                                           "squeeze"):
            bonus += 1.5
            notes.append("high-chop-harvest")
        elif chop_val <= 38.2 and regime in ("trend_up", "trend_down"):
            bonus += 1.0
            notes.append("clean-trend")

    mtfr = (mtf or {}).get("result") or {}
    mtf_comp = _rnum(mtfr, "structure", "mtfComposite")
    mtf_aligned = _rnum(mtfr, "structure", "mtfAligned")
    vol_ratio = _rnum(mtfr, "structure", "volRatio")
    if mtf_comp is not None:
        fit.update({"mtf_composite": mtf_comp, "mtf_aligned": mtf_aligned})
        if regime == "trend_up" and mtf_comp > 50:
            bonus += 2.0
            notes.append("mtf-aligned-long")
        elif regime == "trend_down" and mtf_comp < -50:
            bonus += 2.0
            notes.append("mtf-aligned-short")
        elif regime in ("chop_high_volatility", "squeeze", "neutral") \
                and abs(mtf_comp) <= 50:
            bonus += 1.0
            notes.append("mtf-range-agree")
    if vol_ratio is not None and vol_ratio >= 1.5:
        fit["vol_ratio"] = vol_ratio
        bonus += 1.0
        notes.append("vol-expanding")

    dvir = (dvi or {}).get("result") or {}
    dvi_trend = _rnum(dvir, "structure", "trend")
    dvi_mom = _rnum(dvir, "structure", "momentum")
    if dvi_trend is not None:
        fit.update({"dvi_trend": dvi_trend, "dvi_momentum": dvi_mom})
        if dvi_trend > 0 and regime == "trend_up":
            bonus += 1.0
            notes.append("dvi-trend-agree-long")
        elif dvi_trend < 0 and regime == "trend_down":
            bonus += 1.0
            notes.append("dvi-trend-agree-short")

    atr = m.get("atr_pct")
    if atr is not None:
        fit["atr_pct"] = atr
        if atr >= 1.5:
            bonus += 1.0
            notes.append("moves-large")
    rsi = m.get("rsi14")
    if rsi is not None and (rsi > 78 or rsi < 22):
        bonus -= 3.0
        notes.append("rsi-overheated")
    # Venue guardrail: Binance SPOT cannot short — directional short is flat.
    if venue == "binance" and regime == "trend_down":
        bonus -= 25.0
        notes.append("spot-no-short")

    if bonus > TVCLI_BONUS_CAP:
        bonus = TVCLI_BONUS_CAP
    return round(bonus, 2), notes, fit


def apply_confluence(cands, timeframe="1H", bars=180):
    """Enrich top candidates with tvcli /hunt numeric fitness (see docstring)."""
    tv_syms = list({c["tv_symbol"] for c in cands})
    hunts = {}
    for skill in config_confluence_skills():
        try:
            hunts[skill] = tv_hunt(skill, tv_syms, timeframe, bars)
        except Exception as exc:
            print(f"confluence {skill} failed: {exc} — continuing without it",
                  file=sys.stderr)
            hunts[skill] = {}
    for c in cands:
        tv = c["tv_symbol"]
        mtf = hunts.get("mtf-confluence", {}).get(tv) or {}
        sq = hunts.get("squeeze", {}).get(tv) or {}
        ch = hunts.get("choppiness", {}).get(tv) or {}
        dvi = hunts.get("dvi", {}).get(tv) or {}
        c["confluence"] = {
            "mtf-confluence": (mtf.get("result") is not None),
            "squeeze": (sq.get("result") is not None),
            "choppiness": (ch.get("result") is not None),
            "dvi": (dvi.get("result") is not None),
            "errors": {k: (v.get("error") if isinstance(v, dict) else None)
                       for k, v in (("mtf", mtf), ("sq", sq), ("ch", ch),
                                    ("dvi", dvi))},
        }
        bonus, notes, fit = tvcli_fitness(c, mtf=mtf, sq=sq, ch=ch, dvi=dvi)
        c["tvcli_fit"] = fit
        c["confluence_bonus"] = bonus
        c["confluence_notes"] = notes
        c["score_final"] = round(c["score"] + bonus, 2)
    cands.sort(key=lambda x: x["score_final"], reverse=True)
    return cands


def _config_screen(key, default=None):
    """Read a screen.* scalar/list from ../config.yaml (stdlib line scan)."""
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
                if in_screen and s.lstrip().startswith(key + ":"):
                    val = s.split(":", 1)[1].split("#")[0].strip()
                    if val.startswith("[") and val.endswith("]"):
                        inner = val[1:-1].strip()
                        return ([v.strip().strip("'\"") for v in inner.split(",")
                                 if v.strip()] or default)
                    return val.strip("'\"") or default
    except Exception:
        pass
    return default


def config_confirm_interval(default="4h"):
    return _config_screen("confirm_interval", default)


def config_confluence_skills():
    """screen.confluence_skills from config.yaml, else the built-in set."""
    skills = _config_screen("confluence_skills")
    if not skills:
        return list(CONFLUENCE_SKILLS)
    return [s for s in skills if s]


def config_screen_value(key, default=None):
    """Screen-tuning scalars (min_volume_usd, universe_max_symbols, ...)."""
    val = _config_screen(key)
    if val is None:
        return default
    try:
        return type(default)(val)
    except (TypeError, ValueError):
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
                    help="print shortlist size (cosmetic; the report holds all)")
    ap.add_argument("--confluence-top", type=int, default=10)
    ap.add_argument("--min-volume", type=int,
                    default=config_screen_value(
                        "min_volume_usd", DEFAULT_MIN_VOLUME_USD),
                    help="min 24h quote volume (USD) to be scanned at all")
    ap.add_argument("--max-symbols", type=int,
                    default=config_screen_value(
                        "universe_max_symbols", DEFAULT_MAX_SYMBOLS),
                    help="universe cap per venue (top-N by 24h volume)")
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

    hl_all, bn_all, candle_cache, screen_errors = [], [], {}, []
    for pname in wanted:
        preset = presets[pname]
        # fail-soft PER VENUE: a hard Hyperliquid/Binance outage (universe
        # fetch exhausted its retries) degrades that venue to zero
        # candidates and the other venue still screens — the daemon would
        # otherwise skip a whole cycle over one venue's blip
        for name, fn, kw in (
                ("hyperliquid", screen_hyperliquid, {}),
                ("binance", screen_binance, {"cache": candle_cache})):
            try:
                rows = fn(pname, preset, args.interval, args.limit,
                         args.min_volume, args.max_symbols, **kw)
            except Exception as exc:
                print(f"venue {name} screen failed: {exc}", file=sys.stderr)
                screen_errors.append({"venue": name, "error": str(exc)[:200]})
                continue
            (hl_all if name == "hyperliquid" else bn_all).extend(rows)

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
        "universe": {"min_volume_usd": args.min_volume,
                     "max_symbols": args.max_symbols,
                     "confluence_skills": config_confluence_skills()},
        "top": cands[0] if cands else None,
        "results": cands,
        "screen_errors": screen_errors,
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
