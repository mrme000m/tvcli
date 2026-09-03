"""secrets — provision runtime secrets from the Bitwarden vault (never fatal).

Same contract as .devcontainer/post-create.sh: bw-provision exit 2
(credentials not configured) is a warning; any other non-zero is a warning
too — secrets must never break the stack. bw-provision.sh itself never
echoes values.
"""

from __future__ import annotations

from ..config import Config
from ..core import Context, StageResult

STAGE = "secrets"


def run(cfg: Config, ctx: Context) -> StageResult:
    script = cfg.workspace / "browser-debug" / "secrets" / "bw-provision.sh"
    if not script.is_file():
        return StageResult(
            STAGE, skipped=True,
            warnings=[f"{script} not found — secrets not provisioned"],
            summary_line="secrets: SKIPPED (bw-provision.sh not found)",
        )
    proc = ctx.mutate(f'bash "{script}"')
    if proc.returncode != 0:
        hint = (
            "credentials not configured — set BW_EMAIL / BW_PASSWORD, or "
            "BW_CLIENTID / BW_CLIENTSECRET, as codespace secrets; see "
            "browser-debug/secrets/README.md"
            if proc.returncode == 2
            else f"bw-provision exit {proc.returncode}"
        )
        return StageResult(
            STAGE,
            warnings=[f"secrets not provisioned ({hint}). The stack installs "
                      "and runs without them."],
            details={"rc": proc.returncode},
            summary_line=f"secrets: not provisioned ({hint})",
        )
    return StageResult(
        STAGE, changed=not ctx.dry_run,
        details={"rc": 0},
        summary_line="secrets: provisioned this run" if not ctx.dry_run else "secrets: would provision (dry-run)",
    )
