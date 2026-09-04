#!/usr/bin/env python3
"""wundertrading httpx client — thin CLI shim over the wtclient package.

The implementation now lives in the modular, validated, type-safe package in
``scripts/wtclient/``. This file keeps the historical CLI name and command
shapes working:

  wt_httpx.py open_api GET /open_api/exchanges
  wt_httpx.py open_api GET "/open_api/api_profiles?limit=5"
  wt_httpx.py session GET /en/trader/grid_bots/grid?page=1&limit=5
  wt_httpx.py session GET /en/trader/grid_bots/upsert --transport browser
  wt_httpx.py mcp get_exchange_markets --params '{"exchanges":["HYPERLIQUID_SWAP"]}'
  wt_httpx.py market /supported-markets
  wt_httpx.py curl GET /open_api/exchanges

For the package API, ``from wtclient import WunderTrading``.
"""
from __future__ import annotations

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from wtclient.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
