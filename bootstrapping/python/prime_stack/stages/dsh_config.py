"""dsh-config — ~/.dsh/settings.yaml (CF Workers AI as the default LLM).

Failure semantics preserved from the original contract: missing CF secrets
→ skip with a warning (fatal when strict); an existing settings.yaml is
left untouched (backed up first) unless --force-settings is set. The
Cloudflare account id is always templated at runtime from the env var —
nothing is hardcoded, and the id never appears in logs or the envelope.
"""

from __future__ import annotations

from ..config import (DEFAULT_MODEL_ID, DEFAULT_PROVIDER, Config, dsh_catalog)
from ..core import Context, StageResult, StageFailure

STAGE = "dsh-config"


def render_dsh_settings(cf_account_id: str) -> str:
    """Render ~/.dsh/settings.yaml — model catalog + order come from
    config.dsh_catalog() (the single source with explicit surface deltas)."""
    lines = [
        "# Managed by the prime-stack bootstrap (bootstrapping/python/prime_stack",
        "# — stages/dsh_config.py). The Cloudflare account id is templated from",
        "# the CLOUDFLARE_ACCOUNT_ID env var at provision time.",
        "agent-default-model:",
        f"  provider: {DEFAULT_PROVIDER}",
        f'  model: "{DEFAULT_MODEL_ID}"',
        "agent-presets:",
        "  default: prime-orchestrator",
        "locale:",
        "  preference: en",
        "permission:",
        "  defaultPreset: danger-full-access",
        "llm-pi-ai:",
        "  providers:",
        f"    {DEFAULT_PROVIDER}:",
        f"      displayName: {DEFAULT_PROVIDER}",
        "      apiKeyEnv: CLOUDFLARE_AI_TOKEN",
        "      api: openai-completions",
        f"      baseURL: https://api.cloudflare.com/client/v4/accounts/{cf_account_id}/ai/v1",
        "      models:",
    ]
    for m in dsh_catalog():
        lines.append(f"        - id: \"{m['id']}\"")
        lines.append(f"          name: \"{m['name']}\"")
        lines.append(f"          contextWindow: {m['contextWindow']}")
        lines.append(f"          maxTokens: {m['maxTokens']}")
    return "\n".join(lines) + "\n"


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
                      "settings.yaml left alone; re-run once they are provided"],
            summary_line="dsh settings.yaml: SKIPPED (CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_KEY not set)",
        )

    settings = cfg.dsh_settings
    exists = settings.is_file()

    if exists:
        ctx.write_text(
            settings.with_name(settings.name + ".pre-prime-stack.bak"),
            settings.read_text(),
            mode=0o600,
        )

    if exists and not cfg.force_settings:
        return StageResult(
            STAGE,
            warnings=[f"{settings} already exists — left untouched (backup at "
                      f"{settings}.pre-prime-stack.bak). Re-run with "
                      "--force-settings to replace it with the template."],
            summary_line="dsh settings.yaml: exists — left untouched",
        )

    changed = ctx.write_text(settings, render_dsh_settings(cfg.cf_account_id), mode=0o600)
    return StageResult(
        STAGE, changed=changed,
        details={"settings": str(settings), "models": len(dsh_catalog())},
        summary_line="dsh settings.yaml: "
        + ("written this run" if changed else "current"),
    )
