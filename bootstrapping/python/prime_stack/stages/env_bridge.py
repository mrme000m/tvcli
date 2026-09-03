"""env — bridge PATH and the Cloudflare env into login shells.

The plugin's llm-cf-provider + cf-tools read CLOUDFLARE_AI_TOKEN and
CF_ACCOUNT_ID. Codespace secrets only inject CLOUDFLARE_API_KEY /
CLOUDFLARE_ACCOUNT_ID, so resolve at SHELL time with :- fallbacks — the
bridge works no matter which pair is set.

Marker-bounded block (same markers ansible blockinfile used), so files
written by the previous playbook generation are recognized as present.
"""

from __future__ import annotations

from ..config import Config
from ..core import Context, StageResult, marker_block_text

STAGE = "env"

MARKER_BEGIN = "# >>> dsh-prime-stack bootstrap >>>"
MARKER_END = "# <<< dsh-prime-stack bootstrap <<<"
BLOCK = '''export PATH="$HOME/.local/bin:$PATH"
export CLOUDFLARE_AI_TOKEN="${CLOUDFLARE_AI_TOKEN:-${CLOUDFLARE_API_KEY:-}}"
export CF_ACCOUNT_ID="${CF_ACCOUNT_ID:-${CLOUDFLARE_ACCOUNT_ID:-}}"'''


def run(cfg: Config, ctx: Context) -> StageResult:
    changed = False
    for name in (".profile", ".bashrc"):
        rc_file = cfg.home / name
        existing = rc_file.read_text() if rc_file.exists() else ""
        new_text, file_changed = marker_block_text(existing, MARKER_BEGIN, MARKER_END, BLOCK)
        if file_changed:
            ctx.write_text(rc_file, new_text)
            changed = True
    return StageResult(
        STAGE, changed=changed,
        details={"files": ["~/.profile", "~/.bashrc"]},
        summary_line="env bridge (PATH + Cloudflare): "
        + ("written this run" if changed else "present"),
    )
