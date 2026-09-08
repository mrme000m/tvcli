#!/usr/bin/env python3
"""Render the prime-agent runtime config — GA agent stack (docker layer).

Mirrors bootstrapping/python/prime_stack/stages/prime_config.py (the
verified Cloudflare Workers AI provider fragment), containerized: the
account id and API key arrive ONLY via the runtime environment
(vault_loader / grid.env — never baked), and every write is a KEYED MERGE:
any other provider or key already present in the documents is preserved
verbatim. The API key is written straight from the process environment to
mode-0600 files and never appears in stdout, stderr, or logs.

Providers:
  cloudflare-workers-ai  REQUIRED (both CF_ACCOUNT_ID + CLOUDFLARE_AI_TOKEN
                        must be set, else exit 3). Stays defaultProvider /
                        defaultModel — GA's own model per explicit user
                        directive (@cf/zai-org/glm-5.3).
  nvidia                OPTIONAL — merged only when NVIDIA_API_KEY is set
                        (baseUrl from NVIDIA_BASE_URL when present, else
                        https://integrate.api.nvidia.com/v1).
  openrouter            OPTIONAL — merged only when OPENROUTER_API_KEY is set
                        (https://openrouter.ai/api/v1, free-tier ids).
  mistral               OPTIONAL — merged only when MISTRAL_API_KEY is set
                        (https://api.mistral.ai/v1).
The three optional providers mirror the grid-autonomy daemon's LLM fallback
chain (CF → Nvidia → OpenRouter, plus Mistral for arbiters) and are skipped
SILENTLY when their key is absent — never fatal, never partial. Their
prefixed ids land in settings.json enabledModels ONLY for providers actually
merged in that run; foreign ids already present are never removed.

Env:
  CF_ACCOUNT_ID        Cloudflare account id (bridged from
                       CLOUDFLARE_ACCOUNT_ID by the entrypoint)
  CLOUDFLARE_AI_TOKEN  Workers AI API token (bridged from
                       CLOUDFLARE_API_KEY by the entrypoint)
  PRIME_AGENT_CODING_AGENT_DIR  config dir override (the entrypoint points
                       it into the persistent grid-dsh volume; default
                       ~/.prime/agent)

Usage: python3 prime_agent_config.py [--force]
  Writes <dir>/models.json, <dir>/auth.json, <dir>/settings.json.
  Exits 3 when required env is missing (warning path), 1 on I/O failure.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROVIDER = "cloudflare-workers-ai"
DEFAULT_MODEL_ID = "@cf/zai-org/glm-5.3"
DEFAULT_THINKING = "high"

# The verified Cloudflare Workers AI model catalog (single source:
# bootstrapping/python/prime_stack/config.py :: MODEL_CATALOG).
MODELS = [{'id': '@cf/zai-org/glm-5.2', 'name': 'CF Workers AI / GLM-5.2', 'contextWindow': 262144, 'maxTokens': 16384, 'reasoning': True}, {'id': '@cf/zai-org/glm-5.3', 'name': 'CF Workers AI / GLM-5.3', 'contextWindow': 1310720, 'maxTokens': 16384, 'reasoning': True}, {'id': '@cf/zai-org/glm-5.3-flash', 'name': 'CF Workers AI / GLM-5.3 Flash', 'contextWindow': 1310720, 'maxTokens': 16384, 'reasoning': True}, {'id': '@cf/deepseek-ai/deepseek-v4-flash-0731', 'name': 'CF Workers AI / DeepSeek V4 Flash 0731', 'contextWindow': 1310720, 'maxTokens': 16384}, {'id': '@cf/deepseek-ai/deepseek-v4-pro-0813', 'name': 'CF Workers AI / DeepSeek V4 Pro 0813', 'contextWindow': 1048576, 'maxTokens': 16384}, {'id': '@cf/qwen/qwen3.8-27b', 'name': 'CF Workers AI / Qwen3.8 27B', 'contextWindow': 262144, 'maxTokens': 16384}, {'id': '@cf/moonshotai/kimi-k2.6', 'name': 'CF Workers AI / Kimi K2.6', 'contextWindow': 262144, 'maxTokens': 16384}, {'id': '@cf/moonshotai/kimi-k2.7-code', 'name': 'CF Workers AI / Kimi K2.7 Code', 'contextWindow': 262144, 'maxTokens': 16384, 'reasoning': True}]

# Prefixed ids for settings.json (config.py :: enabled_model_ids — prefix,
# not backreference, so layering is escape-proof).
ENABLED_MODELS = ['cloudflare-workers-ai/@cf/zai-org/glm-5.2', 'cloudflare-workers-ai/@cf/zai-org/glm-5.3', 'cloudflare-workers-ai/@cf/zai-org/glm-5.3-flash', 'cloudflare-workers-ai/@cf/deepseek-ai/deepseek-v4-flash-0731', 'cloudflare-workers-ai/@cf/deepseek-ai/deepseek-v4-pro-0813', 'cloudflare-workers-ai/@cf/qwen/qwen3.8-27b', 'cloudflare-workers-ai/@cf/moonshotai/kimi-k2.6', 'cloudflare-workers-ai/@cf/moonshotai/kimi-k2.7-code']

# The optional worker-fallback providers (the grid-autonomy daemon's LLM
# chain order: CF → Nvidia → OpenRouter, plus Mistral for arbiters; ids and
# endpoints live-proven by agents/grid-autonomy/llm/provider.py and the
# Mac's prime-agent models.json). Each merges ONLY when its apiKeyEnv is
# set in the process environment — absent → skipped silently.
EXTRA_PROVIDERS = {
    "nvidia": {
        "apiKeyEnv": "NVIDIA_API_KEY",
        "baseUrlEnv": "NVIDIA_BASE_URL",
        "defaultBaseUrl": "https://integrate.api.nvidia.com/v1",
        "models": [
            {"id": "meta/llama-3.3-70b-instruct",
             "name": "NVIDIA / Llama 3.3 70B Instruct",
             "contextWindow": 131072, "maxTokens": 32768},
        ],
    },
    "openrouter": {
        "apiKeyEnv": "OPENROUTER_API_KEY",
        "defaultBaseUrl": "https://openrouter.ai/api/v1",
        "models": [
            {"id": "arcee-ai/trinity-large-preview:free",
             "name": "OpenRouter / Trinity Large Preview (free)",
             "reasoning": True,
             "contextWindow": 131072, "maxTokens": 32768},
            {"id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
             "name": "NVIDIA / Nemotron 3 Nano Omni (free)",
             "reasoning": True,
             "contextWindow": 131072, "maxTokens": 32768},
            {"id": "poolside/laguna-s-2.1:free",
             "name": "Poolside / Laguna S 2.1 (free)",
             "reasoning": True,
             "contextWindow": 131072, "maxTokens": 32768},
            {"id": "nvidia/nemotron-3.5-lightning:free",
             "name": "NVIDIA / Nemotron 3.5 Lightning (free)",
             "reasoning": True,
             "contextWindow": 131072, "maxTokens": 32768},
        ],
    },
    "mistral": {
        "apiKeyEnv": "MISTRAL_API_KEY",
        "defaultBaseUrl": "https://api.mistral.ai/v1",
        "models": [
            {"id": "mistral-large-latest",
             "name": "Mistral / Large Latest",
             "contextWindow": 131072, "maxTokens": 32768},
        ],
    },
}

FILES = {"models": "models.json", "auth": "auth.json", "settings": "settings.json"}


def load_doc(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_doc(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    tmp.chmod(0o600)
    tmp.replace(path)


def render(account_id: str, api_key: str, config_dir: Path) -> list:
    changed = []

    models = load_doc(config_dir / FILES["models"])
    before_models = json.dumps(models, sort_keys=True)
    models.setdefault("providers", {})[PROVIDER] = {
        "type": "api_key",
        "baseUrl": f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        "api": "openai-completions",
        "models": MODELS,
    }

    auth = load_doc(config_dir / FILES["auth"])
    before_auth = json.dumps(auth, sort_keys=True)
    auth[PROVIDER] = {"type": "api_key", "key": api_key}

    # optional worker-fallback providers — keyed merges, present-key-only
    # (absent env key → provider skipped silently, never fatal)
    enabled_extra = []
    for name, spec in EXTRA_PROVIDERS.items():
        provider_key = (os.environ.get(spec["apiKeyEnv"]) or "").strip()
        if not provider_key:
            continue
        base_url = ((os.environ.get(spec.get("baseUrlEnv", "")) or "").strip()
                    or spec["defaultBaseUrl"])
        models.setdefault("providers", {})[name] = {
            "type": "api_key",
            "baseUrl": base_url,
            "api": "openai-completions",
            "models": spec["models"],
        }
        auth[name] = {"type": "api_key", "key": provider_key}
        enabled_extra.extend("%s/%s" % (name, m["id"]) for m in spec["models"])

    if json.dumps(models, sort_keys=True) != before_models:
        write_doc(config_dir / FILES["models"], models)
        changed.append(FILES["models"])

    if json.dumps(auth, sort_keys=True) != before_auth:
        write_doc(config_dir / FILES["auth"], auth)
        changed.append(FILES["auth"])

    settings = load_doc(config_dir / FILES["settings"])
    before_settings = json.dumps(settings, sort_keys=True)
    settings["defaultProvider"] = PROVIDER
    settings["defaultModel"] = DEFAULT_MODEL_ID
    settings["defaultThinkingLevel"] = DEFAULT_THINKING
    merged = list(settings.get("enabledModels") or [])
    for m in ENABLED_MODELS + enabled_extra:
        if m not in merged:
            merged.append(m)
    settings["enabledModels"] = merged
    if json.dumps(settings, sort_keys=True) != before_settings:
        write_doc(config_dir / FILES["settings"], settings)
        changed.append(FILES["settings"])

    return changed


def main() -> int:
    account_id = (os.environ.get("CF_ACCOUNT_ID") or "").strip()
    api_key = (os.environ.get("CLOUDFLARE_AI_TOKEN") or "").strip()
    if not account_id or not api_key:
        print("prime_agent_config: CF_ACCOUNT_ID / CLOUDFLARE_AI_TOKEN not "
              "set — prime-agent config left alone", file=sys.stderr)
        return 3

    config_dir = Path(os.environ.get("PRIME_AGENT_CODING_AGENT_DIR")
                      or Path.home() / ".prime" / "agent")
    changed = render(account_id, api_key, config_dir)
    # presence-only reporting — the key values never leave the process
    present = [n for n, s in EXTRA_PROVIDERS.items()
               if (os.environ.get(s["apiKeyEnv"]) or "").strip()]
    print("prime_agent_config: config_dir=%s changed=%s extra_providers=%s"
          % (config_dir, changed, present))
    return 0


if __name__ == "__main__":
    sys.exit(main())
