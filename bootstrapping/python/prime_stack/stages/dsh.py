"""dsh — install the dsh CLI at the exact supported version + pnpm >= 10.

Gotchas this stage encodes (empirically verified):

* dsh must be EXACTLY `0.1.1-rc.2` (plugin compatibility table:
  0.1.0-rc.7 / 0.1.1-rc.2 supported, 0.1.2-alpha NOT). A different installed
  version only warns — it never auto-upgrades.
* A crashed npm replace can leave a half-removed global tree (ENOTEMPTY
  dirs, no bin symlink). Installing over that fails and pushes koffi into
  a cmake source build the container lacks — so the leftover tree is
  removed first. This only runs when the dsh binary is MISSING, so the
  removal is always safe: the prebuilt koffi in the tarball loads fine
  without its (npm-blocked) install script. Do NOT pass --allow-scripts.
* pnpm >= 10 is the dsh plugin forwarder requirement; the devcontainer
  node feature ships npm only.
"""

from __future__ import annotations

from ..config import Config
from ..core import Context, StageResult, version_lt

STAGE = "dsh"


def detect_dsh_version(ctx: Context) -> str:
    proc = ctx.exec("dsh --version 2>/dev/null || true")
    out = (proc.stdout or "").strip()
    return out or "MISSING"


def detect_pnpm_version(ctx: Context) -> str:
    proc = ctx.exec("pnpm --version 2>/dev/null || true")
    out = (proc.stdout or "").strip().lstrip("v")
    return out or "0.0.0"


def run(cfg: Config, ctx: Context) -> StageResult:
    warnings = []
    changed = False
    details = {}

    dsh_version = detect_dsh_version(ctx)
    if dsh_version == "MISSING":
        proc = ctx.mutate(
            'groot="$(npm root -g)" && rm -rf "$groot/@deepseek-ai/dsh" '
            f"&& npm install -g @deepseek-ai/dsh@{cfg.dsh_version}"
        )
        if proc.returncode != 0:
            return StageResult(
                STAGE, failed=True,
                error=f"npm install -g @deepseek-ai/dsh@{cfg.dsh_version} failed (rc={proc.returncode})",
                summary_line="dsh CLI: FAILED to install",
            )
        changed = True
        details["installed"] = cfg.dsh_version
        summary = f"dsh CLI: installed {cfg.dsh_version} this run"
    else:
        details["installed"] = dsh_version
        if dsh_version != cfg.dsh_version:
            warnings.append(
                f"dsh {dsh_version} is installed but {cfg.dsh_version} is the supported "
                "version for dsh-prime-orchestrator (compatibility: 0.1.0-rc.7 / "
                "0.1.1-rc.2 only). Not auto-upgrading."
            )
        summary = f"dsh CLI: {dsh_version}"

    pnpm_version = detect_pnpm_version(ctx)
    details["pnpm"] = pnpm_version
    if version_lt(pnpm_version, "10.0.0"):
        proc = ctx.mutate("npm install -g pnpm@10")
        if proc.returncode != 0:
            return StageResult(
                STAGE, failed=True,
                error=f"npm install -g pnpm@10 failed (rc={proc.returncode})",
                summary_line="dsh CLI: installed, but pnpm >= 10 FAILED to install",
                warnings=warnings,
            )
        changed = True
        summary += " (+ pnpm 10 installed this run)"
    else:
        summary += f" (pnpm {pnpm_version})"

    return StageResult(
        STAGE, changed=changed, warnings=warnings, details=details,
        summary_line=summary,
    )
