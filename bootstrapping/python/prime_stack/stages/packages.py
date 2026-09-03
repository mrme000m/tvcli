"""packages — apt runtime dependencies (python fallback stage).

The Ansible playbook owns the real apt stage (ansible.builtin.apt with
become); this stage exists so `prime-stack all` works standalone without
Ansible. It is a best-effort fallback: no passwordless sudo → skip with a
warning (the devcontainer image bakes these packages in anyway).
"""

from __future__ import annotations

import shutil

from ..config import APT_PACKAGES, Config
from ..core import Context, StageResult

STAGE = "packages"


def run(cfg: Config, ctx: Context) -> StageResult:
    if not shutil.which("sudo"):
        return StageResult(
            STAGE, skipped=True,
            summary_line="packages: SKIPPED (no sudo; the playbook apt stage or the image provides them)",
            warnings=["sudo not found — relying on baked-in packages"],
        )
    proc = ctx.mutate(
        "sudo -n apt-get update -qq && "
        "sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "
        + " ".join(APT_PACKAGES)
    )
    if proc.returncode != 0:
        return StageResult(
            STAGE, failed=True, error=f"apt-get install failed (rc={proc.returncode})",
            summary_line="packages: FAILED (apt-get)",
        )
    return StageResult(
        STAGE, changed=proc.returncode == 0 and not ctx.dry_run,
        details={"packages": APT_PACKAGES},
        summary_line=f"packages: {', '.join(APT_PACKAGES)} present"
        + (" (dry-run)" if ctx.dry_run else ""),
    )
