#!/usr/bin/env python3
"""WunderTrading skill — market regime classifier.

Fetches OHLCV from a public exchange API (no auth), computes trend/volatility
metrics, classifies the market regime, and emits a recommended strategy
skeleton for WunderTrading `place_strategy_trade` / `edit_trade_strategy`.

Stdlib only. Data sources:
  hyperliquid  POST https://api.hyperliquid.xyz/info  candleSnapshot
  binance      GET  https://api.binance.com/api/v3/klines  (spot)
               GET  https://fapi.binance.com/fapi/v1/klines (futures)

Usage:
  market_regime.py hyperliquid BTC --interval 1h
  market_regime.py binance BTCUSDT --interval 1h --market futures
  market_regime.py hyperliquid ETH --interval 4h --json

Output: human summary, or --json for {metrics, regime, recommended_config}.
The recommendation is a STARTING POINT — verify against the playbook matrix
in references/strategy-playbook.md and user constraints before executing.
"""
import argparse
import json
import math
import ssl
import sys
import urllib.request
from datetime import datetime, timezone

HL_INTERVALS = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
BINANCE_INTERVALS = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}

_SSL_CTX = None


def ssl_context():
    """CA-bundle discovery — python.org macOS builds ship with no default CA.

    Preference: default context → certifi → system bundles → unverified
    (warned; acceptable only because this fetches public OHLCV, never keys).
    """
    global _SSL_CTX
    if _SSL_CTX is not None:
        return _SSL_CTX
    try:
        _SSL_CTX = ssl.create_default_context()
        # sanity-check the default context can actually load a CA
        if not _SSL_CTX.get_ca_certs():
            raise ssl.SSLError("empty default CA store")
        return _SSL_CTX
    except Exception:
        pass
    for candidate in _ca_candidates():
        try:
            ctx = ssl.create_default_context(cafile=candidate)
            if ctx.get_ca_certs():
                _SSL_CTX = ctx
                return _SSL_CTX
        except Exception:
            continue
    print("warning: no CA bundle found — using UNVERIFIED TLS for public "
          "market data", file=sys.stderr)
    _SSL_CTX = ssl._create_unverified_context()
    return _SSL_CTX


def _ca_candidates():
    try:
        import certifi
        yield certifi.where()
    except ImportError:
        pass
    yield "/etc/ssl/cert.pem"                     # macOS
    yield "/etc/ssl/certs/ca-certificates.crt"    # Debian/Ubuntu
    yield "/etc/pki/tls/certs/ca-bundle.crt"      # RHEL/Fedora


def _interval_ms(interval):
    return {"15m": 15 * 60_000, "1h": 3_600_000,
            "4h": 4 * 3_600_000, "1d": 86_400_000}[interval]


def args_window_ms(limit, interval):
    """Lookback window (ms) for `limit` bars + 20% headroom (Hyperliquid
    candleSnapshot requires explicit startTime/endTime)."""
    return int(limit * _interval_ms(interval) * 1.2)


def fetch_candles(exchange, symbol, interval, limit, market="spot"):
    if exchange == "hyperliquid":
        interval_map = HL_INTERVALS
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = end_ms - args_window_ms(limit, interval)
        req = json.dumps({
            "type": "candleSnapshot",
            "req": {"coin": symbol, "interval": interval_map[interval],
                    "startTime": start_ms, "endTime": end_ms},
        }).encode()
        r = urllib.request.Request(
            "https://api.hyperliquid.xyz/info", data=req,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=30, context=ssl_context()) as resp:
            data = json.loads(resp.read())
        # [{"t","T","s","i","o","h","l","c","v","n"}, ...] string OHLC
        return [(float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"]))
                for c in data]
    if exchange == "binance":
        interval_map = BINANCE_INTERVALS
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}" \
              f"&interval={interval_map[interval]}&limit={limit}"
        if market == "futures":
            # fapi.binance.com handshakes are flaky from some networks; spot
            # klines are within bps of futures for majors and regime metrics
            # are shape-based, so fall back rather than fail.
            fapi = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}" \
                   f"&interval={interval_map[interval]}&limit={limit}"
            try:
                return _binance_klines(fapi)
            except Exception as exc:
                print(f"note: futures klines failed ({exc}) — "
                      f"falling back to spot klines", file=sys.stderr)
        return _binance_klines(url)
    raise SystemExit(f"unknown exchange: {exchange}")


def _binance_klines(url):
    with urllib.request.urlopen(url, timeout=30, context=ssl_context()) as resp:
        data = json.loads(resp.read())
    return [(float(k[1]), float(k[2]), float(k[3]), float(k[4])) for k in data]


def ema(values, period):
    k = 2.0 / (period + 1)
    out, prev = [], None
    for v in values:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def wilder(values, period):
    """Wilder smoothing (ATR/ADX/RSI style): seed = SMA of first period."""
    out, prev = [], None
    for i, v in enumerate(values):
        if i < period - 1:
            out.append(None)
            continue
        if i == period - 1:
            prev = sum(values[:period]) / period
        else:
            prev = (prev * (period - 1) + v) / period
        out.append(prev)
    return out


def compute_metrics(candles):
    """candles: list of (open, high, low, close), oldest first."""
    highs = [c[1] for c in candles]
    lows = [c[2] for c in candles]
    closes = [c[3] for c in candles]
    n = len(closes)
    price = closes[-1]

    ema20 = ema(closes, 20)[-1]
    ema50 = ema(closes, 50)[-1]
    ema200 = ema(closes, 200)[-1] if n >= 200 else None

    # ATR(14) as % of price
    trs = []
    for i in range(1, n):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    atr = wilder(trs, 14)[-1] if len(trs) >= 14 else None
    atr_pct = (atr / price * 100) if atr else None

    # Bollinger width(20, 2) and its percentile over the window
    width_series = []
    for i in range(19, n):
        window = closes[i - 19:i + 1]
        mean = sum(window) / 20
        var = sum((x - mean) ** 2 for x in window) / 20
        sd = math.sqrt(var)
        width_series.append((4 * sd / mean * 100) if mean else 0.0)
    bb_width = width_series[-1] if width_series else None
    bb_width_pctile = None
    if width_series:
        bb_width_pctile = sum(1 for w in width_series if w <= bb_width) / len(width_series) * 100

    # RSI(14) Wilder
    gains, losses = [], []
    for i in range(1, n):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    rsi = None
    if len(gains) >= 14:
        ag = wilder(gains, 14)[-1]
        al = wilder(losses, 14)[-1]
        if al and al > 0:
            rsi = 100 - 100 / (1 + ag / al)
        elif al == 0:
            rsi = 100.0

    # ADX(14) Wilder
    plus_dm, minus_dm = [], []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
    adx = None
    if n > 28:
        atr14 = wilder(trs, 14)
        pdi14 = wilder(plus_dm, 14)
        mdi14 = wilder(minus_dm, 14)
        dxs = []
        for a, p, m in zip(atr14, pdi14, mdi14):
            if a is None or p is None or m is None or not a:
                continue
            pdi, mdi = 100 * p / a, 100 * m / a
            if pdi + mdi:
                dxs.append(100 * abs(pdi - mdi) / (pdi + mdi))
        if len(dxs) >= 14:
            adx = wilder(dxs, 14)[-1]

    return {
        "bars": n,
        "price": price,
        "ema20": ema20, "ema50": ema50, "ema200": ema200,
        "atr_pct": round(atr_pct, 3) if atr_pct else None,
        "bb_width_pct": round(bb_width, 3) if bb_width else None,
        "bb_width_pctile": round(bb_width_pctile, 1) if bb_width_pctile else None,
        "rsi14": round(rsi, 1) if rsi is not None else None,
        "adx14": round(adx, 1) if adx else None,
        "change_pct": {
            "24h": round((closes[-1] / closes[-min(n, 25)] - 1) * 100, 2),
            "7d": round((closes[-1] / closes[-min(n, 169)] - 1) * 100, 2) if n > 25 else None,
        },
    }


def classify(m):
    """Return (regime, evidence dict). Mirrors references/strategy-playbook.md."""
    has_200 = m["ema200"] is not None
    adx = m["adx14"] or 20.0  # missing ADX (short window) → neutral assumption
    atr = m["atr_pct"] or 0.0
    bw_pctile = m["bb_width_pctile"] or 50.0
    ema_stack_up = m["ema20"] > m["ema50"] and (not has_200 or m["ema50"] > m["ema200"])
    ema_stack_down = m["ema20"] < m["ema50"] and (not has_200 or m["ema50"] < m["ema200"])

    if bw_pctile <= 20 and adx < 20:
        return "squeeze", {"bb_width_pctile": bw_pctile, "adx": adx}
    if adx >= 20 and ema_stack_up:
        return "trend_up", {"ema_stack": "20>50>200", "adx": adx, "atr_pct": atr}
    if adx >= 20 and ema_stack_down:
        return "trend_down", {"ema_stack": "20<50<200", "adx": adx, "atr_pct": atr}
    if adx < 20 and atr >= 1.5:
        return "chop_high_volatility", {"adx": adx, "atr_pct": atr}
    return "neutral", {"adx": adx, "atr_pct": atr, "bb_width_pctile": bw_pctile}


# Config skeletons — field names/values match the WunderTrading MCP
# place_strategy_trade / edit_trade_strategy schemas. Deviations are strings
# like "2%" (accepts 0.02 / 2% / 2), portfolio portions sum to 1.
CONFIGS = {
    "trend_up": {
        "strategy": "classic LONG, no DCA — ride the trend with a trailing stop",
        "fields": {
            "side": "long", "orderType": "market",
            "takeProfits": [
                {"priceDeviation": "2%", "portfolio": "40%"},
                {"priceDeviation": "4%", "portfolio": "30%"},
                {"priceDeviation": "8%", "portfolio": "30%"},
            ],
            "takeProfitBaseOn": "entry_order",
            "stopLoss": "3%",
            "stopLossMove": "2%", "stopLossMoveExecute": "0%",
            "trailingStopActivation": "4%", "trailingStopExecute": "2%",
        },
    },
    "trend_down": {
        "strategy": "classic SHORT (futures only) — mirror of trend_up; on spot stay FLAT",
        "fields": {
            "side": "short", "orderType": "market",
            "takeProfits": [
                {"priceDeviation": "2%", "portfolio": "40%"},
                {"priceDeviation": "4%", "portfolio": "30%"},
                {"priceDeviation": "8%", "portfolio": "30%"},
            ],
            "takeProfitBaseOn": "entry_order",
            "stopLoss": "3%",
            "stopLossMove": "2%", "stopLossMoveExecute": "0%",
            "trailingStopActivation": "4%", "trailingStopExecute": "2%",
        },
    },
    "chop_high_volatility": {
        "strategy": "DCA LONG (futures) — average into adverse moves, exit on mean reversion",
        "fields": {
            "side": "long", "orderType": "market",
            "extraOrderCount": 6, "extraOrderDeviation": "2.5%",
            "extraOrderVolumeMultiplier": 1.4, "extraOrderDeviationMultiplier": 1.5,
            "takeProfits": [{"priceDeviation": "1.5%", "portfolio": "100%"}],
            "takeProfitBaseOn": "average_price",
            "stopLoss": None,  # wide or none — DCA handles adverse moves
            "trailingStopActivation": None, "trailingStopExecute": None,
        },
    },
    "squeeze": {
        "strategy": "range limit entries at the band edge, or WAIT for expansion — tight risk",
        "fields": {
            "side": "long", "orderType": "limit",
            "price": "<recent range low / BB lower band>",
            "takeProfits": [{"priceDeviation": "2%", "portfolio": "100%"}],
            "stopLoss": "3%",
        },
    },
    "neutral": {
        "strategy": "no clear edge — prefer flat, probe with small DCA only",
        "fields": {
            "side": "long", "orderType": "market",
            "extraOrderCount": 4, "extraOrderDeviation": "2%",
            "extraOrderVolumeMultiplier": 1.3, "extraOrderDeviationMultiplier": 1.4,
            "takeProfits": [{"priceDeviation": "1.5%", "portfolio": "100%"}],
            "takeProfitBaseOn": "average_price",
            "stopLoss": "8%",
        },
    },
}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exchange", choices=["hyperliquid", "binance"])
    ap.add_argument("symbol", help="hyperliquid: BTC · binance: BTCUSDT")
    ap.add_argument("--interval", default="1h", choices=list(HL_INTERVALS))
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--market", default="spot", choices=["spot", "futures"],
                    help="binance only")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.exchange == "hyperliquid" and "-" in args.symbol:
        args.symbol = args.symbol.split("-")[0]

    candles = fetch_candles(args.exchange, args.symbol, args.interval,
                            args.limit, args.market)
    if len(candles) < 60:
        sys.exit(f"only {len(candles)} candles — need >= 60 for meaningful metrics")
    m = compute_metrics(candles)
    regime, evidence = classify(m)
    rec = CONFIGS[regime]

    out = {
        "symbol": args.symbol, "exchange": args.exchange, "interval": args.interval,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metrics": m, "regime": regime, "evidence": evidence,
        "recommended_config": rec,
        "disclaimer": "starting point only — size to balance, confirm with user before executing",
    }
    if args.json:
        print(json.dumps(out, indent=2))
        return

    print(f"== {args.exchange}:{args.symbol} {args.interval} — {regime.upper()} ==")
    print(f"price {m['price']}  EMA20 {m['ema20']:.4g}  EMA50 {m['ema50']:.4g}"
          + (f"  EMA200 {m['ema200']:.4g}" if m['ema200'] else ""))
    print(f"ATR14 {m['atr_pct']}%  ADX14 {m['adx14']}  RSI14 {m['rsi14']}"
          f"  BBw {m['bb_width_pct']}% (pctile {m['bb_width_pctile']})")
    print(f"Δ24h {m['change_pct']['24h']}%  Δ7d {m['change_pct']['7d']}%")
    print(f"\nrecommended: {rec['strategy']}")
    print(json.dumps(rec["fields"], indent=2))
    print("\nevidence: " + json.dumps(evidence))
    print("starting point only — size to balance, confirm with user before executing")


if __name__ == "__main__":
    main()
