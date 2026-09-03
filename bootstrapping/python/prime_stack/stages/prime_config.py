"""prime-config — ~/.prime/agent/{models,auth,settings}.json.

All three files are KEYED MERGES: every other provider/key in an existing
document is preserved verbatim, and a no-op merge leaves the file byte-identical
(strictly better than the old task, which rewrote the files on every run).
The API key is read straight from the process environment and written with
mode 0600 — it never appears in stdout, stderr, logs, or the JSON envelope,
so no no_log is needed anywhere upstream.
"""

from __future__ import annotations

from ..config import (DEFAULT_MODEL_ID, DEFAULT_PROVIDER, Config,
                      enabled_model_ids, prime_provider)
from ..core import (Context, StageResult, StageFailure, json_text,
                    load_json_file)

STAGE = "prime-config"


def merge_provider_into_models(doc: dict, provider: dict) -> None:
    doc.setdefault("providers", {})[DEFAULT_PROVIDER] = provider


def merge_auth_entry(doc: dict, api_key: str) -> None:
    doc[DEFAULT_PROVIDER] = {"type": "api_key", "key": api_key}


def merge_settings_defaults(doc: dict, enabled_models: list) -> None:
    doc["defaultProvider"] = DEFAULT_PROVIDER
    doc["defaultModel"] = DEFAULT_MODEL_ID
    doc["defaultThinkingLevel"] = "high"
    merged = list(doc.get("enabledModels") or [])
    for m in enabled_models:
        if m not in merged:
            merged.append(m)
    doc["enabledModels"] = merged


def _merge_json_file(ctx: Context, path, mutate_doc, mode=0o600) -> bool:
    """Load → mutate in place → idempotent write. Returns changed."""
    doc = load_json_file(path)
    before = json_text(doc)
    mutate_doc(doc)
    after = json_text(doc)
    if after == before:
        # content identical on disk too — still enforce the mode
        return ctx.write_text(path, after, mode=mode) if path.exists() else False
    return ctx.write_text(path, after, mode=mode)


def run(cfg: Config, ctx: Context) -> StageResult:
    if not cfg.cf_secrets_present:
        if cfg.strict:
            raise StageFailure(
                "prime_stack_strict=true but CLOUDFLARE_ACCOUNT_ID / "
                "CLOUDFLARE_API_KEY are not set"
            )
        return StageResult(
            STAGE, skipped=True,
            warnings=["CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_KEY not set — "
                      "prime-agent config left alone"],
            summary_line="prime-agent config: SKIPPED (CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_KEY not set)",
        )

    changed_models = _merge_json_file(
        ctx, cfg.prime_agent_dir / "models.json",
        lambda doc: merge_provider_into_models(doc, prime_provider(cfg.cf_account_id)),
    )
    changed_auth = _merge_json_file(
        ctx, cfg.prime_agent_dir / "auth.json",
        lambda doc: merge_auth_entry(doc, cfg.cf_api_key),
    )
    changed_settings = _merge_json_file(
        ctx, cfg.prime_agent_dir / "settings.json",
        lambda doc: merge_settings_defaults(doc, enabled_model_ids()),
    )

    changed = changed_models or changed_auth or changed_settings
    return StageResult(
        STAGE, changed=changed,
        details={"models.json": changed_models, "auth.json": changed_auth,
                 "settings.json": changed_settings},
        summary_line="prime-agent config (models/auth/settings.json): "
        + ("merged this run" if changed else "current"),
    )
