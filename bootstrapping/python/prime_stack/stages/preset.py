"""preset — materialize the plugin-managed `prime-orchestrator` agent preset.

The dsh-prime-orchestrator plugin materializes the preset into
$DSH_HOME/.agent-presets/prime-orchestrator/ at HOST BOOT (verified in the
plugin's src/index.ts: apply() -> materializePresetOnBoot(ctx), called for
every booted profile). `dsh plugin` is only a pnpm forwarder and never boots
the host, so one short-lived `dsh web` boot is the cheap trigger.

Never hand-copy preset files — that bypasses the plugin's sha256 marker
semantics (user edits are preserved by the marker, not by file presence).

Boot flags: --port 0 (the OS picks a free port), --no-open (dsh 0.1.1-rc.2
opens the Web UI in a browser by default — pointless headless), `timeout 90`
sends SIGTERM which dsh treats as an ordinary stop (exit 0).
"""

from __future__ import annotations

import time

from ..config import Config
from ..core import Context, StageResult, StageFailure

STAGE = "preset"

WARMBOOT_LOG = "/tmp/dsh-warmboot.log"
WAIT_SECONDS = 120


def run(cfg: Config, ctx: Context) -> StageResult:
    preset_file = cfg.preset_dir / "preset.yml"
    if preset_file.is_file():
        return StageResult(
            STAGE,
            details={"preset_dir": str(cfg.preset_dir)},
            summary_line="preset prime-orchestrator: materialized",
        )

    ctx.mutate(
        f"timeout 90 dsh web --port 0 --host 127.0.0.1 --no-open >{WARMBOOT_LOG} 2>&1 || true"
    )
    if ctx.dry_run:
        return StageResult(
            STAGE, changed=True,
            details={"preset_dir": str(cfg.preset_dir)},
            summary_line="preset prime-orchestrator: would warm-boot the profile to materialize it",
        )

    materialized = ctx.wait_until(lambda: preset_file.is_file(), WAIT_SECONDS)
    if not materialized:
        raise StageFailure(
            f"{preset_file} was not materialized within {WAIT_SECONDS}s of a warm "
            f"boot. Check {WARMBOOT_LOG} and that 'dsh web' boots cleanly by hand. "
            "Note: an existing preset the user EDITED is intentionally preserved "
            "(marker semantics) — this stage only fails when no preset exists at all.",
            details={"warmboot_log": WARMBOOT_LOG},
        )
    return StageResult(
        STAGE, changed=True,
        details={"preset_dir": str(cfg.preset_dir)},
        summary_line="preset prime-orchestrator: materialized this run",
    )
