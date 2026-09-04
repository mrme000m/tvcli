#!/usr/bin/env python3
"""Worker A — public Binance spot bookTicker spreads (no auth).

`binance_spreads(symbols) -> {symbol: spread_pct}` where spread_pct is
(ask-bid)/mid*100 in percent, matching the convention of
`universe_screen.hyperliquid_spreads` so merge.py can score both venues on
the same scale.

Public market data only; no keys or session cookies involved. Never raises —
returns a partial/empty dict on any failure.
"""
from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request

_BOOK_TICKER = "https://api.binance.com/api/v3/ticker/bookTicker"
_UA = {"User-Agent": "tvcli-grid-autonomy/1.0"}


def _ctx():
    try:
        return ssl.create_default_context()
    except Exception:  # noqa: BLE001
        return ssl._create_unverified_context()


def _spread_pct(ticker):
    try:
        bid = float(ticker.get("bidPrice") or 0.0)
        ask = float(ticker.get("askPrice") or 0.0)
    except (TypeError, ValueError):
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return round((ask - bid) / mid * 100.0, 6)


def _fetch_all(symbols):
    url = _BOOK_TICKER + "?symbols=" + urllib.parse.quote(json.dumps(symbols))
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=20, context=_ctx()) as resp:
        return json.loads(resp.read())


def binance_spreads(symbols):
    """`{symbol: spread_pct}` from the public bookTicker endpoint."""
    out = {}
    syms = []
    for s in symbols or []:
        v = str(s).strip().upper().replace("/", "")
        if v and v not in syms:
            syms.append(v)
    if not syms:
        return out
    try:
        rows = _fetch_all(syms)
        if not isinstance(rows, list):
            rows = []
        for t in rows:
            if not isinstance(t, dict) or not t.get("symbol"):
                continue
            spread = _spread_pct(t)
            if spread is not None:
                out[t["symbol"]] = spread
        return out
    except Exception:  # noqa: BLE001 — fall back to per-symbol calls
        pass
    # per-symbol fallback (public endpoint; a failed bulk query shouldn't
    # abort the whole binance screening leg)
    for sym in syms:
        try:
            req = urllib.request.Request(
                _BOOK_TICKER + "?symbol=" + urllib.parse.quote(sym), headers=_UA)
            with urllib.request.urlopen(req, timeout=10, context=_ctx()) as resp:
                t = json.loads(resp.read())
            spread = _spread_pct(t)
            if spread is not None:
                out[sym] = spread
        except Exception:
            continue
    return out


if __name__ == "__main__":
    import sys
    symbols = sys.argv[1:] or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    print(json.dumps(binance_spreads(symbols), indent=2, sort_keys=True))
