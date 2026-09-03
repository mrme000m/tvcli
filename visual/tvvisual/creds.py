"""Load TradingView session credentials from env vars and/or a .env file."""

from __future__ import annotations

import os
from pathlib import Path


def parse_env(path) -> dict:
    out = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def load_creds(env_path=None) -> dict:
    data = {}
    if env_path:
        data.update(parse_env(env_path))
    data.update({k: v for k, v in os.environ.items() if v})
    return {
        "session": data.get("SESSION", ""),
        "signature": data.get("SIGNATURE", ""),
        "device_t": data.get("DEVICE_T", ""),
        "user": data.get("TV_USER", ""),
        "tier": data.get("TV_TIER", ""),
    }