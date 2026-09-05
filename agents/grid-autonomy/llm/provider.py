#!/usr/bin/env python3
"""Grid-autonomy LLM provider chain — Cloudflare primary, Nvidia/OpenRouter/Mistral fallback.

Pluggable, stdlib-only (urllib). All roles talk to `chat()`; provider order
and models come from env so the harness stays extendable without code edits.

Env:
  CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_KEY (or CLOUDFLARE_AI_TOKEN)
    CF_MODEL (default @cf/zai-org/glm-5.3)
  NVIDIA_API_KEY, NVIDIA_MODEL (default meta/llama-3.3-70b-instruct)
  OPENROUTER_API_KEY, OPENROUTER_MODEL (default arcee-ai/trinity-large-preview:free)
  MISTRAL_API_KEY, MISTRAL_MODEL (default mistral-large-latest)
  GRID_LLM_CHAIN (default "cf,nvidia,openrouter,mistral" — comma order = fallback order)

Usage:
  provider.py --ping [--json]            # try chain in order, report latency
  provider.py --chat "summarize: ..."    # one-shot chat via first healthy provider
  from provider import chat, chat_json

Budget: callers should cap ~8 LLM calls/decision (TradingAgents budget).
Keys are read from env only — never logged, never written to disk.
"""
import json
import os
import sys
import time
import urllib.request

CF_MODEL_DEFAULT = "@cf/zai-org/glm-5.3"
NVIDIA_MODEL_DEFAULT = "meta/llama-3.3-70b-instruct"
OPENROUTER_MODEL_DEFAULT = "arcee-ai/trinity-large-preview:free"
MISTRAL_MODEL_DEFAULT = "mistral-large-latest"

# Stable role keys for per-agent routing (GRID_LLM_ROLES maps role -> provider).
# Order mirrors the swarm pipeline; unassigned roles follow the global chain.
ROLE_KEYS = ["bull", "bear", "bull_rebuttal", "bear_rebuttal", "facilitator",
             "risk_seeking", "risk_neutral", "risk_conservative"]

TIMEOUT_S = 60


def _post(url, payload, headers, timeout=TIMEOUT_S):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _cf_chat(messages, model, max_tokens=1024):
    acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    token = os.environ.get("CLOUDFLARE_API_KEY") or os.environ.get("CLOUDFLARE_AI_TOKEN")
    if not (acct and token):
        raise RuntimeError("cf: missing CLOUDFLARE_ACCOUNT_ID/API_KEY")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
    out = _post(url, {"messages": messages, "max_tokens": max_tokens},
                {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    # Workers AI chat shape: {"result": {"response": "..."}} (or OpenAI-like)
    res = out.get("result", out)
    if isinstance(res, dict) and "response" in res:
        return res["response"]
    try:
        return res["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError(f"cf: unexpected response shape: {str(out)[:200]}")


def _openai_compat_chat(base_url, key, model, messages, max_tokens=1024, extra_headers=None):
    if not key:
        raise RuntimeError(f"{base_url}: missing API key")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    out = _post(f"{base_url}/chat/completions",
                {"model": model, "messages": messages, "max_tokens": max_tokens},
                headers, timeout=TIMEOUT_S)
    try:
        return out["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError(f"{base_url}: unexpected response: {str(out)[:200]}")


def _providers():
    """[(name, fn)] in chain order — only providers with creds present."""
    chain = [p.strip() for p in os.environ.get(
        "GRID_LLM_CHAIN", "cf,nvidia,openrouter,mistral").split(",")]
    fns = {
        "cf": lambda msgs, mt: _cf_chat(
            msgs, os.environ.get("CF_MODEL", CF_MODEL_DEFAULT), mt),
        "nvidia": lambda msgs, mt: _openai_compat_chat(
            "https://integrate.api.nvidia.com/v1",
            os.environ.get("NVIDIA_API_KEY"),
            os.environ.get("NVIDIA_MODEL", NVIDIA_MODEL_DEFAULT), msgs, mt),
        "openrouter": lambda msgs, mt: _openai_compat_chat(
            "https://openrouter.ai/api/v1",
            os.environ.get("OPENROUTER_API_KEY"),
            os.environ.get("OPENROUTER_MODEL", OPENROUTER_MODEL_DEFAULT), msgs, mt,
            {"HTTP-Referer": "https://github.com/mrme000m/tvcli", "X-Title": "tvcli-grid-autonomy"}),
        "mistral": lambda msgs, mt: _openai_compat_chat(
            "https://api.mistral.ai/v1",
            os.environ.get("MISTRAL_API_KEY"),
            os.environ.get("MISTRAL_MODEL", MISTRAL_MODEL_DEFAULT), msgs, mt),
    }
    return [(p, fns[p]) for p in chain if p in fns]


def role_chain(role):
    """Single-provider chain for a role, or None when unassigned (follow global).

    GRID_LLM_ROLES is a JSON object mapping a role key (see ROLE_KEYS) to a
    provider name (cf|nvidia|openrouter). Returns [(name, fn)] for that one
    provider so a role can be pinned to a specific model independent of the
    fallback order; returns None when the role is absent or the provider has
    no credentials (so the caller degrades to the global chain).
    """
    if not role:
        return None
    try:
        roles = json.loads(os.environ.get("GRID_LLM_ROLES", "{}"))
    except Exception:
        return None
    provider = roles.get(role) if isinstance(roles, dict) else None
    if not provider:
        return None
    return [(name, fn) for name, fn in _providers() if name == provider]


def chat(messages, max_tokens=1024, _chain=None, role=None):
    """First healthy provider wins. Returns (provider_name, text).

    `role` (optional) pins the call to a single provider via GRID_LLM_ROLES;
    it is ignored when the role is unassigned or its provider lacks creds.
    """
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    if _chain is not None:
        chain = _chain
    else:
        chain = role_chain(role) or _providers()
    if not chain:
        raise RuntimeError("no LLM providers configured (set GRID_LLM_CHAIN + keys)")
    errors = {}
    for name, fn in chain:
        try:
            t0 = time.time()
            text = fn(messages, max_tokens)
            return name, text
        except Exception as exc:
            errors[name] = str(exc)[:160]
    raise RuntimeError(f"all LLM providers failed: {errors}")


def _extract_json(text):
    """Best-effort strict-JSON extraction: fences → first balanced {...} block.

    LLMs sometimes prepend prose ("Here is the JSON: {...}") or wrap in
    ```json fences; the balanced-block scan recovers both. Returns None
    when no complete object is present (e.g. truncated output).
    """
    body = (text or "").strip()
    if not body:
        return None
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else body[3:]
        if body.rstrip().endswith("```"):
            body = body.rstrip()[:-3]
        body = body.strip()
    try:
        return json.loads(body)
    except Exception:
        pass
    start = body.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(body)):
            ch = body[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(body[start:i + 1])
                    except Exception:
                        break
        start = body.find("{", start + 1)
    return None


def chat_json(messages, max_tokens=4096, _chain=None, attempts=3, role=None):
    """chat() + parse the reply as JSON.

    LLM JSON output is flaky (truncation, prose-wrapped replies), so retry
    the chain up to `attempts` times with a fresh completion and a
    balanced-block extraction before giving up. max_tokens is generous
    (4096): debate answers run long and truncation mid-string is the
    dominant failure mode.
    """
    errors = {}
    for attempt in range(max(1, attempts)):
        name, text = chat(messages, max_tokens, _chain, role=role)
        parsed = _extract_json(text)
        if isinstance(parsed, dict):
            return name, parsed
        errors[f"{name}#{attempt}"] = (
            f"unparseable JSON: {str(text)[:120]!r}")
    raise RuntimeError(f"chat_json: {len(errors)} attempt(s) failed: {errors}")


def ping():
    results = []
    for name, fn in _providers():
        t0 = time.time()
        try:
            fn([{"role": "user", "content": "reply with exactly: ok"}], 16)
            results.append({"provider": name, "ok": True,
                            "latency_ms": int((time.time() - t0) * 1000)})
        except Exception as exc:
            results.append({"provider": name, "ok": False,
                            "latency_ms": int((time.time() - t0) * 1000),
                            "error": str(exc)[:200]})
    return results


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ping", action="store_true")
    ap.add_argument("--chat", default=None, help="one-shot user message")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.chat:
        name, text = chat(args.chat)
        if args.json:
            print(json.dumps({"provider": name, "text": text}))
        else:
            print(f"[{name}] {text}")
        return
    results = ping()
    if args.json or True:
        print(json.dumps({"chain": [r["provider"] for r in results],
                          "results": results}, indent=2))
    for r in results:
        status = "ok" if r["ok"] else f"FAIL {r.get('error', '')}"
        print(f"{r['provider']}: {status} ({r['latency_ms']}ms)", file=sys.stderr)


if __name__ == "__main__":
    main()
