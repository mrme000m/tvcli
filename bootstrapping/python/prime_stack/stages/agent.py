"""agent — install the prime-agent CLI (official installer).

Gotcha (empirically verified): the installer's prompt helper reads
/dev/tty whenever one is attached — and the codespace lifecycle runs
commands under a pty, so a plain invocation hangs forever waiting for a
keypress. Fix: download the script, run it under `setsid --wait` with
stdin closed — setsid detaches the controlling terminal so the helper
falls back to stdin, `</dev/null` makes every prompt take its default,
and `--wait` keeps the caller blocked until the exit code is available.
"""

from __future__ import annotations

from ..config import PRIME_AGENT_INSTALLER, Config
from ..core import Context, StageResult

STAGE = "agent"


def prime_agent_present(cfg: Config, ctx: Context) -> bool:
    proc = ctx.exec(f'command -v prime-agent >/dev/null 2>&1 || [ -x "{cfg.prime_agent_bin}" ]')
    return proc.returncode == 0


def run(cfg: Config, ctx: Context) -> StageResult:
    if prime_agent_present(cfg, ctx):
        return StageResult(
            STAGE,
            details={"bin": str(cfg.prime_agent_bin)},
            summary_line="prime-agent CLI: installed",
        )
    proc = ctx.mutate(
        f"curl -fsSL {PRIME_AGENT_INSTALLER} -o /tmp/prime-agent-install.sh "
        "&& setsid --wait sh /tmp/prime-agent-install.sh </dev/null"
    )
    if proc.returncode != 0:
        return StageResult(
            STAGE, failed=True,
            error=f"prime-agent installer failed (rc={proc.returncode}); "
                  "it needs network access to app.primeintellect.ai",
            summary_line="prime-agent CLI: FAILED to install",
        )
    return StageResult(
        STAGE, changed=True,
        details={"bin": str(cfg.prime_agent_bin)},
        summary_line="prime-agent CLI: installed this run",
    )
