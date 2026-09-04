#!/usr/bin/env python3
"""provider-limits.py — subscription type + limits/rates for every key in the
`provider-keys` vault item (see browser-debug/secrets/VAULT_CONVENTIONS.md).

Resolution: env-first (NVIDIA_API_KEY, OPENROUTER_API_KEY, KIMI_CODE_API_KEY,
CLINEPASS_API_KEY, OPENCODE_GO_API_KEY, NVIDIA_BASE_URL), vault-fallback via
bw (BW_SESSION, else BW_PASSWORD unlock). Key values are NEVER printed —
output carries only redacted metadata (prefix, length, source).

Read-only GETs only:

  nvidia      GET {base}/models ................. validity + model count
              (no public key-info endpoint; tier is static free-prototyping)
  openrouter  GET https://openrouter.ai/api/v1/key ... limit/remaining/reset,
              usage day/week/month, free-tier flag
  kimi        GET https://api.kimi.com/coding/v1/models ... validity + models
              (sk-kimi- keys are Kimi Code API keys; no public balance endpoint —
              quota lives in the code console, not api.moonshot.ai)
  clinepass   GET https://api.cline.bot/api/v1/users/me -> id, then
              GET /api/v1/users/{id}/balance ... credit balance
  opencode_go GET https://opencode.ai/zen/go/v1/usage ... usage vs the fixed
              Go plan budgets ($60/mo, $30/wk, $12 per rolling 5h)

Rate-window headers (x-ratelimit-*/ratelimit-*) are echoed generically when
present. Usage: provider-limits.py [--provider nvidia|openrouter|kimi|
clinepass|opencode_go] [--timeout SEC]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request

VAULT_ITEM = "provider-keys"
FIELDS = ["NVIDIA_BASE_URL", "NVIDIA_API_KEY", "OPENROUTER_API_KEY",
          "KIMI_CODE_API_KEY", "CLINEPASS_API_KEY", "OPENCODE_GO_API_KEY"]

_bw_session_cache: str | None = None


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _bw_session() -> str:
    global _bw_session_cache
    if _bw_session_cache:
        return _bw_session_cache
    if os.environ.get("BW_SESSION"):
        _bw_session_cache = os.environ["BW_SESSION"]
        return _bw_session_cache
    if not os.environ.get("BW_PASSWORD"):
        raise SystemExit("no BW_SESSION and no BW_PASSWORD (vault locked)")
    p = _run(["bw", "unlock", "--passwordenv", "BW_PASSWORD", "--raw"])
    sess = (p.stdout or "").strip()
    if p.returncode != 0 or not sess:
        raise SystemExit("bw unlock failed")
    _bw_session_cache = sess
    return sess


def _vault_fields() -> dict[str, str]:
    sess = _bw_session()
    p = _run(["bw", "get", "item", VAULT_ITEM, "--session", sess])
    if p.returncode != 0:
        raise SystemExit(f"vault item {VAULT_ITEM} not found")
    try:
        item = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise SystemExit("bw get item returned non-JSON")
    return {f.get("name", ""): (f.get("value", "") or "") for f in item.get("fields", [])}


def resolve(name: str, vault: dict) -> tuple[str, str]:
    """Return (value, source). Env wins; empty string when absent."""
    if os.environ.get(name):
        return os.environ[name], f"env:{name}"
    if vault.get(name):
        return vault[name], f"vault:{VAULT_ITEM}/{name}"
    return "", "missing"


def redact_key(value: str, keep: int = 8) -> str:
    return f"{value[:keep]}…(len {len(value)})" if value else "missing"


def get(url: str, key: str, timeout: int, extra: dict | None = None) -> tuple[int, dict, dict]:
    """GET with Bearer auth. Returns (status, json-or-{}, ratelimit-headers)."""
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json",
               # urllib's default UA is challenged by some WAFs (opencode.ai
               # 403s Python-urllib); identify as a script instead.
               "User-Agent": "tvcli-provider-limits/1.0"}
    headers.update(extra or {})
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode()
            rate = {k: v for k, v in r.headers.items() if "ratelimit" in k.lower()}
            try:
                doc = json.loads(body)
            except json.JSONDecodeError:
                doc = {}
            return r.status, doc, rate
    except Exception as e:  # HTTPError carries .status/.headers/.read()
        status = getattr(e, "status", 0) or 0
        rate = {}
        try:
            rate = {k: v for k, v in e.headers.items() if "ratelimit" in k.lower()}
        except Exception:
            pass
        return status, {}, rate
    return 0, {}, {}


def check_nvidia(key: str, base: str, t: int) -> dict:
    base = (base or "https://integrate.api.nvidia.com/v1").rstrip("/")
    status, body, rate = get(f"{base}/models", key, t)
    models = body.get("data", []) if isinstance(body, dict) else []
    return {
        "subscription": "free prototyping (NVIDIA Developer Program; credit grants phased out 2025)" if status == 200 else "unknown",
        "valid": status == 200,
        "models_visible": len(models) if status == 200 else 0,
        "limits": {"requests_per_minute": "~40 (free hosted ceiling; ~200 on request)",
                   "credits": "n/a — rate-limit governed, no balance endpoint"},
        "rates": rate,
        "http_status": status,
    }


def check_openrouter(key: str, t: int) -> dict:
    status, body, rate = get("https://openrouter.ai/api/v1/key", key, t)
    d = body.get("data", {}) if isinstance(body, dict) else {}
    if status != 200 or not d:
        return {"subscription": "unknown", "valid": False, "http_status": status}
    free = d.get("is_free_tier")
    return {
        "subscription": ("free tier" if free else "paid/credits") if free is not None else "key-scoped (see limits)",
        "valid": True,
        "limits": {"credit_limit": d.get("limit"), "credit_remaining": d.get("limit_remaining"),
                   "limit_reset": d.get("limit_reset"),
                   "free_tier": free},
        "usage_credits": {"all_time": d.get("usage"), "daily": d.get("usage_daily"),
                          "weekly": d.get("usage_weekly"), "monthly": d.get("usage_monthly")},
        "rates": rate,
        "http_status": status,
    }


def check_kimi(key: str, t: int) -> dict:
    # sk-kimi-* keys are Kimi Code API keys (api.kimi.com/coding/v1), not the
    # legacy Moonshot sk-* keys (api.moonshot.ai). There is no public balance
    # endpoint on the coding API; validity == a 200 on /models.
    base = "https://api.kimi.com/coding/v1"
    status, body, rate = get(f"{base}/models", key, t)
    models = [m.get("id") for m in (body.get("data", []) if isinstance(body, dict) else [])]
    if status != 200:
        return {"subscription": "unknown", "valid": False, "http_status": status,
                "note": "sk-kimi- keys are region-bound (api.kimi.com coding / platform.kimi.com china)"}
    return {
        "subscription": "Kimi Code (K2.7/K3 coding) — quota governed by the code console, not a balance API",
        "valid": True,
        "models_visible": len(models),
        "models": models[:8],
        "limits": {"note": "no public balance endpoint; usage/quota tracked in the Kimi Code console"},
        "rates": rate,
        "http_status": status,
    }


def check_clinepass(key: str, t: int) -> dict:
    base = "https://api.cline.bot"
    s_me, me, _ = get(f"{base}/api/v1/users/me", key, t)
    me = me.get("data", me) if isinstance(me, dict) else me  # {success, data} envelope
    if s_me != 200 or not isinstance(me, dict) or not me.get("id"):
        return {"subscription": "unknown", "valid": False, "http_status": s_me}
    uid = me["id"]
    s_bal, bal, rate = get(f"{base}/api/v1/users/{uid}/balance", key, t)
    out: dict = {
        "subscription": "ClinePass $9.99/mo (2-5x standard rate on cline-pass/* models) or usage-billing credits — see balance",
        "valid": True,
        "account": {"id": uid, "email": me.get("email")},
        "http_status": s_bal,
        "rates": rate,
    }
    out["balance"] = bal if s_bal == 200 else {"error": f"balance lookup http {s_bal}"}
    out["balance_unit"] = "provider credits (raw; dashboard at app.cline.bot)"
    return out


def check_opencode_go(key: str, t: int) -> dict:
    status, body, rate = get("https://opencode.ai/zen/go/v1/usage", key, t)
    if status != 200 or not isinstance(body, dict):
        return {"subscription": "unknown", "valid": False, "http_status": status}
    return {
        "subscription": "OpenCode Go $10/mo (13 curated open models; Zen pay-as-you-go separate)",
        "valid": True,
        "plan_budgets": {"monthly": 60, "weekly": 30, "rolling_5h": 12, "currency": "USD"},
        "usage": body,
        "rates": rate,
        "http_status": status,
    }


CHECKS = {"nvidia": check_nvidia, "openrouter": check_openrouter, "kimi": check_kimi,
          "clinepass": check_clinepass, "opencode_go": check_opencode_go}
KEY_FIELD = {"nvidia": "NVIDIA_API_KEY", "openrouter": "OPENROUTER_API_KEY", "kimi": "KIMI_CODE_API_KEY",
             "clinepass": "CLINEPASS_API_KEY", "opencode_go": "OPENCODE_GO_API_KEY"}


def main() -> int:
    ap = argparse.ArgumentParser(prog="provider-limits.py", description="subscription + limits/rates per provider-keys key (redacted)")
    ap.add_argument("--provider", choices=sorted(CHECKS), default=None)
    ap.add_argument("--timeout", type=int, default=20)
    a = ap.parse_args()

    try:
        vault = _vault_fields()
    except SystemExit as e:
        vault = {}
        vault_error = str(e)
    else:
        vault_error = ""

    base, base_src = resolve("NVIDIA_BASE_URL", vault)
    names = [a.provider] if a.provider else sorted(CHECKS)
    report = []
    for name in names:
        key, src = resolve(KEY_FIELD[name], vault)
        entry: dict = {"provider": name, "key": {"prefix": redact_key(key), "source": src}}
        if not key:
            entry.update({"subscription": "unknown", "valid": False,
                          "error": vault_error or f"{KEY_FIELD[name]} not in env or vault"})
        elif name == "nvidia":
            entry.update(check_nvidia(key, base, a.timeout))
            entry["base_url_source"] = base_src
        else:
            try:
                entry.update(CHECKS[name](key, a.timeout))
            except Exception as e:  # network etc. — never leak the key
                entry.update({"subscription": "unknown", "valid": False, "error": f"{type(e).__name__}"})
        report.append(entry)
    print(json.dumps({"providers": report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
