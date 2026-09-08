#!/usr/bin/env python3
"""Worker A — venue/symbol -> WunderTrading pair resolution.

`resolve_pair(venue, symbol)` maps a venue + base symbol (or native pair
string) to the exact WunderTrading `pairCode` needed by `grid_bots/upsert`.

The market map is fetched through the browser transport from
`https://wundertrading.com:2087/all-markets` (needs the logged-in browser
session; the endpoint requires `marketExpiryGroup=infinite`), cached under
`state/market_map-{market}.json` for 24h, and read back from cache (even
stale) when the browser is unavailable.

Everything here is read-only and must never raise: failures return an
unresolved dict (resolve_pair) or an empty/partial map (market_map).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GRID_HOME = os.path.dirname(HERE)  # agents/grid-autonomy
STATE_DIR = os.environ.get("GRID_STATE_DIR", os.path.join(GRID_HOME, "state"))
WUN_SCRIPTS = os.path.normpath(os.path.join(
    GRID_HOME, "..", "..", ".agents", "skills", "wundertrading", "scripts"))
MARKET_ORIGIN = os.environ.get("MARKET_ORIGIN", "https://wundertrading.com:2087")

sys.path.insert(0, WUN_SCRIPTS)

VENUE_MARKET = {
    "hyperliquid": ("derivative", "HYPERLIQUID_SWAP"),
    "hl": ("derivative", "HYPERLIQUID_SWAP"),
    "hyperliquid_swap": ("derivative", "HYPERLIQUID_SWAP"),
    "binance": ("spot", "BINANCE"),
    "binance_spot": ("spot", "BINANCE"),
    "bn": ("spot", "BINANCE"),
}

_BN_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USDP", "DAI", "EUR", "GBP")


def _cache_path(market: str) -> str:
    return os.path.join(STATE_DIR, f"market_map-{market}.json")


def _load_cache(market: str) -> dict:
    try:
        with open(_cache_path(market), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _write_cache(market: str, items: dict) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        payload = {"fetched_at": time.time(), "market": market, "items": items}
        tmp = _cache_path(market) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True)
        os.replace(tmp, _cache_path(market))
    except Exception:
        pass


def _fetch_map(market: str):
    """Fetch + normalize the nested all-markets body. Returns (items, err)."""
    url = f"{MARKET_ORIGIN}/all-markets?market={market}&marketExpiryGroup=infinite"
    try:
        from wtclient.transport.browser import BrowserTransport
        transport = BrowserTransport()
        try:
            resp = transport.fetch_market(url)
            if not resp.ok:
                return {}, f"HTTP {resp.status_code}"
            raw = resp.json()
        finally:
            try:
                transport.close()
            except Exception:
                pass
        return _normalize(raw, market), ""
    except Exception as exc:  # noqa: BLE001 — resolve must never raise
        return {}, str(exc)[:200]


def _normalize(raw, market: str) -> dict:
    """Flatten `{EXCHANGE: {markets: {"EXCH:code": {unifiedCode, pair}}}}`.

    Output keys are the native exchange pair keys (`HYPERLIQUID_SWAP:159`,
    `BINANCE:BTCUSDT`); values carry the fields resolve_pair needs.
    """
    items: dict = {}
    if not isinstance(raw, dict):
        return items
    for exchange, exdata in raw.items():
        if not isinstance(exdata, dict):
            continue
        markets = exdata.get("markets") or {}
        if not isinstance(markets, dict):
            continue
        for key, ent in markets.items():
            if not isinstance(ent, dict):
                continue
            pair = ent.get("pair") or {}
            if not isinstance(pair, dict):
                continue
            view = pair.get("viewSymbol")
            code = pair.get("code")
            if not view and not code:
                continue
            items[str(key)] = {
                "exchange": exchange,
                "market": exdata.get("market", market),
                "unified": ent.get("unifiedCode"),
                "inverse": bool(ent.get("inverse")),
                "pair": view,
                "code": code,
                "ref": pair.get("ref"),
                "base": pair.get("base"),
            }
    return items


def market_map(market: str = "derivative", ttl_h: float = 24.0) -> dict:
    """Flat `{EXCH:code -> {...}}` map for one market, cached `ttl_h` hours.

    Live browser fetch first (and cache refresh on success); on any failure
    fall back to the cached map even when stale.
    """
    market = (market or "derivative").lower()
    if market not in ("derivative", "spot"):
        return {}
    cache = _load_cache(market)
    items = cache.get("items") if isinstance(cache, dict) else None
    fetched_at = cache.get("fetched_at", 0) if isinstance(cache, dict) else 0
    if isinstance(items, dict) and items and             (time.time() - float(fetched_at)) < float(ttl_h) * 3600:
        return items
    fresh, _err = _fetch_map(market)
    if fresh:
        _write_cache(market, fresh)
        return fresh
    return items if isinstance(items, dict) else {}


def _candidates(venue: str, symbol: str) -> list:
    s = (symbol or "").upper().replace("/", "")
    if venue in ("hyperliquid", "hl", "hyperliquid_swap"):
        out = [s]
        if "-" not in s:
            out.append(f"{s}-USDC")
        else:
            out.append(s)
        out.append(s.replace("-USDC", "-USDT"))
        # preserve exact input as well (e.g. HYPE-USDC already covered)
        if symbol and symbol not in out:
            out.insert(0, symbol)
    else:  # binance spot
        out = [s]
        if s.endswith(_BN_QUOTES):
            pass
        else:
            out.append(f"{s}USDT")
            out.append(f"{s}USDC")
    dedup = []
    for c in out:
        if c and c not in dedup:
            dedup.append(c)
    return dedup


def resolve_pair(venue: str, symbol: str, market: str | None = None) -> dict:
    """Map venue + symbol -> `{"pairCode", "unified", "pair"}`.

    hyperliquid HYPE -> {"pairCode": "159", "unified": "HYPE-USDC",
                         "pair": "HYPE-USDC"} (numeric derivative code).
    binance BTC -> {"pairCode": "BTCUSDT", "unified": "BTC/USDT",
                    "pair": "BTCUSDT"} (spot).

    `market` overrides the venue default ("derivative" for hyperliquid,
    "spot" for binance) — the daemon passes "derivative" when the Binance
    sleeve runs on a BINANCE_FUTURES paper profile (Binance spot has no
    paper mode on WunderTrading). Unresolved -> pairCode None.
    """
    venue = (venue or "").lower()
    symbol = (symbol or "").strip()
    unresolved = {"pairCode": None, "unified": None, "pair": None}
    if not symbol:
        return unresolved
    spec = VENUE_MARKET.get(venue)
    if not spec:
        return unresolved
    market = (market or spec[0]).lower()
    if market not in ("derivative", "spot"):
        market = spec[0]
    exchange = spec[1] if market == spec[0] else _exchange_for_market(market, venue)
    cands = _candidates(venue, symbol)
    mm = market_map(market)
    if not mm:
        return unresolved
    # index once, then match candidates IN PRIORITY ORDER (USDT before USDC:
    # both exist on BINANCE_FUTURES and USDT is the liquid one)
    by_pair, by_code = {}, {}
    for key, ent in mm.items():
        if not str(ent.get("exchange", "")).upper() == exchange:
            continue
        p = str(ent.get("pair") or "").upper()
        c = str(ent.get("code") or "").upper()
        by_pair.setdefault(p, (key, ent))
        by_code.setdefault(c, (key, ent))
    for cand in cands:
        hit = by_pair.get(cand.upper()) or by_code.get(cand.upper())
        if hit:
            key, ent = hit
            return {
                "pairCode": str(key).split(":", 1)[1],
                "unified": ent.get("unified"),
                "pair": ent.get("pair"),
                "exchange": str(ent.get("exchange") or "").upper(),
            }
    # cross-exchange fallback on viewSymbol (same market only)
    for cand in cands:
        for key, ent in mm.items():
            if str(ent.get("pair") or "").upper() == cand.upper():
                return {
                    "pairCode": str(key).split(":", 1)[1],
                    "unified": ent.get("unified"),
                    "pair": ent.get("pair"),
                    "exchange": str(ent.get("exchange") or "").upper(),
                }
    return unresolved


def _exchange_for_market(market: str, venue: str) -> str:
    """Exchange code for a non-default market on a venue.

    binance venue + derivative market -> BINANCE_FUTURES (paper sleeve:
    WunderTrading has no Binance spot paper mode). Everything else keeps
    the venue default.
    """
    if market == "derivative" and venue in ("binance", "binance_spot", "bn"):
        return "BINANCE_FUTURES"
    if market == "spot" and venue in ("hyperliquid", "hl", "hyperliquid_swap"):
        return "HYPERLIQUID"
    return VENUE_MARKET.get(venue, (market, ""))[1]


# ── per-pair market metadata (min cost / precision) ────────────────────

META_TTL_H = 24 * 7  # minimums rarely change; 7-day cache


def _meta_cache_path() -> str:
    return os.path.join(STATE_DIR, "market_meta.json")


def _load_meta_cache() -> dict:
    try:
        with open(_meta_cache_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"fetched": {}, "at": {}}


def _save_meta_cache(cache: dict) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = _meta_cache_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, sort_keys=True)
        os.replace(tmp, _meta_cache_path())
    except Exception:
        pass


def _fetch_market_meta(market_code: str) -> dict:
    """GET :2087/market?marketCode=EXCH:code via the browser (public)."""
    url = f"{MARKET_ORIGIN}/market?marketCode={market_code}"
    try:
        from wtclient.transport.browser import BrowserTransport
        transport = BrowserTransport()
        try:
            resp = transport.fetch_market(url)
            if not resp.ok:
                return {}
            raw = resp.json()
        finally:
            try:
                transport.close()
            except Exception:
                pass
        pair = (raw or {}).get("pair") or {}
        return {
            "market_code": market_code,
            "pair": pair.get("viewSymbol"),
            "amount_currency": (pair.get("currencies") or {}).get("base"),
            "min_cost": (((raw or {}).get("limits") or {}).get("cost") or {})
            .get("min"),
            "amount_precision": ((raw or {}).get("precision") or {}).get("amount"),
        }
    except Exception:
        return {}


def pair_meta(venue: str, symbol: str, market: str | None = None) -> dict:
    """Per-pair trading constraints for the resolved pair.

    Returns {"pairCode", "market_code", "amount_currency", "min_cost",
    "amount_precision"} — min_cost is the minimum notional per trade in the
    amount currency (always a USD-stable on WunderTrading), amount_precision
    the decimals accepted for amountPerTrade. Empty dict when unresolvable.
    """
    info = resolve_pair(venue, symbol, market=market)
    if not info or not info.get("pairCode"):
        return {}
    spec = VENUE_MARKET.get((venue or "").lower())
    if not spec:
        return {}
    exch = info.get("exchange") or _exchange_for_market(
        market or spec[0], venue)
    market_code = f"{exch}:{info['pairCode']}"
    cache = _load_meta_cache()
    now = time.time()
    entry = (cache.get("fetched") or {}).get(market_code)
    if isinstance(entry, dict) and entry.get("min_cost") is not None and \
            now - float((cache.get("at") or {}).get(market_code, 0)) < META_TTL_H * 3600:
        out = dict(entry)
        out["pairCode"] = info["pairCode"]
        return out
    fresh = _fetch_market_meta(market_code)
    if fresh and fresh.get("min_cost") is not None:
        cache.setdefault("fetched", {})[market_code] = fresh
        cache.setdefault("at", {})[market_code] = now
        _save_meta_cache(cache)
        out = dict(fresh)
        out["pairCode"] = info["pairCode"]
        return out
    if isinstance(entry, dict):  # stale but better than nothing
        out = dict(entry)
        out["pairCode"] = info["pairCode"]
        return out
    return {}


# ── Binance paper (demo) pair support ─────────────────────────────────
#
# WT-SIDE CONSTRAINT (az00 evidence, 2026-09-08 UTC): WunderTrading's
# Binance paper sleeve is BINANCE_FUTURES-only (docs/binance-paper-profile.md)
# and its demo engine can only run pairs that exist on the Binance FUTURES
# TESTNET — the venue the "Binance Demo" flow signs up against. A pair that
# is live on Binance spot AND on mainnet USDT-M futures (so :2087
# all-markets — and therefore resolve_pair — succeeds) but absent from the
# testnet gets create HTTP 400 "Please check the highlighted fields for
# errors and try again." with NO field detail, deterministically, on every
# retry (observed: binance:RAY 400 at 02:57 while binance:ZRO 200 at 03:23,
# same demo-bn profile, same compute_upsert path). Roughly 1/6 of the
# top-60 Binance spot universe is affected at any time (RAYUSDT, ICPUSDT,
# PEPEUSDT, BONKUSDT, XAUTUSDT, ... on 2026-09-08).
#
# The check below is the cheapest safe screen-side guard: filter candidates
# BEFORE deliberation/deploy on the public, unauthenticated, not-geo-gated
# testnet exchangeInfo. Fail-open by design — an unreachable testnet must
# never veto a deploy (returns None = "unknown, let WT decide").

BINANCE_FUTURES_TESTNET_INFO = (
    "https://testnet.binancefuture.com/fapi/v1/exchangeInfo")
PAPER_SYMBOLS_TTL_H = 24.0  # recheck daily; testnet listings move slowly


def _paper_cache_path() -> str:
    return os.path.join(STATE_DIR, "paper_futures_symbols.json")


def _fetch_testnet_symbols() -> set:
    """TRADING symbols on the Binance futures testnet ({}, never raises).

    Plain urllib: the testnet is public and NOT Cloudflare-gated (unlike
    wundertrading.com:2087), so no browser transport is needed."""
    import ssl
    import urllib.request
    try:
        ctx = ssl.create_default_context()
    except Exception:  # noqa: BLE001 — public data, fail-soft
        ctx = ssl._create_unverified_context()
    try:
        req = urllib.request.Request(
            BINANCE_FUTURES_TESTNET_INFO,
            headers={"User-Agent": "tvcli-grid-autonomy/1.0"})
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            raw = json.loads(resp.read())
        return {s.get("symbol") for s in raw.get("symbols") or []
                if s.get("symbol") and s.get("status") == "TRADING"}
    except Exception:  # noqa: BLE001 — guard must never raise
        return set()


def binance_paper_futures_symbols(ttl_h: float = PAPER_SYMBOLS_TTL_H) -> set:
    """Testnet futures symbols, cached `ttl_h` hours in the state dir.

    Fresh fetch first (cache refresh on success); on any failure fall back
    to the cached set even when stale (an empty set means "unknown", the
    caller fails open)."""
    cache = {}
    try:
        with open(_paper_cache_path(), "r", encoding="utf-8") as fh:
            cache = json.load(fh)
    except Exception:
        cache = {}
    symbols = cache.get("symbols")
    fetched_at = float(cache.get("fetched_at") or 0)
    if isinstance(symbols, list) and symbols and \
            (time.time() - fetched_at) < float(ttl_h) * 3600:
        return set(symbols)
    fresh = _fetch_testnet_symbols()
    if fresh:
        try:
            tmp = _paper_cache_path() + ".tmp"
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"fetched_at": time.time(),
                           "symbols": sorted(fresh)}, fh)
            os.replace(tmp, _paper_cache_path())
        except Exception:
            pass
        return fresh
    if isinstance(symbols, list):  # stale but better than nothing
        return set(symbols)
    return set()


def paper_pair_supported(venue: str, symbol: str,
                         market: str | None = None) -> bool | None:
    """Can a PAPER (demo) grid bot exist for this pair on this venue?

    True  — supported (or no testnet-style constraint applies).
    False — the pair is provably NOT runnable on the paper sleeve; screen
            it out before deliberation/deploy (saves the doomed create and
            its 3 retries every cycle).
    None  — unknown (testnet unreachable, no cache): FAIL OPEN, let WT's
            own validation decide.

    Only the Binance paper sleeve is constrained today: WunderTrading runs
    its own engine for HYPERLIQUID_SWAP paper (no testnet dependency), and
    Binance spot has no paper mode at all."""
    venue = (venue or "").lower()
    symbol = (symbol or "").strip().upper().replace("/", "")
    if not symbol:
        return None
    spec = VENUE_MARKET.get(venue)
    if not spec:
        return None  # unknown venue — not this guard's call
    market = (market or spec[0]).lower()
    if market not in ("derivative", "spot"):
        market = spec[0]
    if venue not in ("binance", "binance_spot", "bn") or market != "derivative":
        return True  # no testnet-style constraint applies
    pair = symbol
    if not symbol.endswith(_BN_QUOTES):
        pair = f"{symbol}USDT"
    symbols = binance_paper_futures_symbols()
    if not symbols:
        return None  # unknown — fail open
    return pair in symbols


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Resolve venue+symbol to a WunderTrading pairCode")
    ap.add_argument("--venue", help="hyperliquid | binance")
    ap.add_argument("--symbol", help="base symbol or native pair string")
    ap.add_argument("--market", help="dump market map: derivative|spot")
    ap.add_argument("--ttl", type=float, default=24.0)
    ap.add_argument("--paper-check", action="store_true",
                    help="check the pair against the Binance futures "
                         "testnet (paper-sleeve support)")
    args = ap.parse_args(argv)
    if args.paper_check and args.venue and args.symbol:
        out = {"paper_pair_supported": paper_pair_supported(
            args.venue, args.symbol, market=args.market)}
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["paper_pair_supported"] is not False else 1
    if args.venue and args.symbol:
        out = resolve_pair(args.venue, args.symbol)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out.get("pairCode") else 1
    if args.market:
        mm = market_map(args.market, ttl_h=args.ttl)
        sample = dict(list(mm.items())[:5])
        print(json.dumps({"market": args.market, "count": len(mm),
                          "sample": sample}, indent=2, sort_keys=True))
        return 0 if mm else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
