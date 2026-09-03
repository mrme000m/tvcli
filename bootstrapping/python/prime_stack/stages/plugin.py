"""plugin — install dsh-prime-orchestrator into the dsh `web` profile.

The pnpm >= 11 gotcha (empirically verified, both ways):

  pnpm >= 11 blocks build scripts of git-hosted dependencies with
  ERR_PNPM_GIT_DEP_PREPARE_NOT_ALLOWED and prints the exact `allowBuilds`
  key it wants — `name@<tarball-url-with-commit-sha>`. That key changes
  with every plugin release, so it is PARSED from the failed install
  output instead of being hardcoded (the playbook stays repeatable across
  plugin updates). pnpm 10 needs no remedy: git-dep prepare scripts run
  unconditionally.

All the remedy logic is pure and unit-tested: `parse_allowbuilds_keys`
(extract the demanded keys from a pnpm failure log) and
`merge_allowbuilds_lines` (merge them into the profile's
pnpm-workspace.yaml text). Quoted YAML keys are required — the key value
contains ':' and '/'.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import Config
from ..core import Context, StageResult, StageFailure

STAGE = "plugin"

PLUGIN_ADD_LOG = "/tmp/prime-stack-plugin-add.log"
WORKSPACE_BASE = "packages:\n  - .\nnodeLinker: hoisted\n"


def parse_allowbuilds_keys(log_text: str) -> list:
    """Extract the allowBuilds keys pnpm demands from a failure log.

    Mirrors: sed -n '/^allowBuilds:/,$p' | grep -E ': true$'
             | sed -e 's/^\\s*//' -e 's/\\s*: true$//'
    A matching surrounding quote pair is stripped (pnpm has printed both
    quoted and unquoted forms across versions; the merge always re-quotes).
    """
    lines = log_text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("allowBuilds:"))
    except StopIteration:
        return []
    keys = []
    for line in lines[start + 1:]:
        m = re.match(r"^\s*(.+?)\s*:\s*true\s*$", line)
        if not m:
            continue
        key = m.group(1).strip()
        if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
            key = key[1:-1]
        if key:
            keys.append(key)
    return keys


def _key_present(lines: list, key: str) -> bool:
    pattern = re.compile(r"^\s*[\"']?" + re.escape(key) + r"[\"']?\s*:\s*true\s*$")
    return any(pattern.match(l) for l in lines)


def merge_allowbuilds_lines(existing_text, keys) -> tuple:
    """Merge allowBuilds keys into pnpm-workspace.yaml text.

    `existing_text` may be None (file absent → base workspace scaffold is
    created). Returns (new_text, changed). Keys already present (in any
    quoting style) are left untouched.
    """
    keys = [k for k in dict.fromkeys(keys)]
    if not keys:
        return existing_text, False
    lines = (existing_text if existing_text is not None else WORKSPACE_BASE).splitlines()
    changed = False
    for key in keys:
        if _key_present(lines, key):
            continue
        idx = next((i for i, l in enumerate(lines) if l.startswith("allowBuilds:")), None)
        if idx is None:
            lines += ["allowBuilds:", f'  "{key}": true']
        else:
            lines.insert(idx + 1, f'  "{key}": true')
        changed = True
    if not changed:
        return existing_text, False
    return "\n".join(lines) + "\n", True


def plugin_installed_built(cfg: Config) -> bool:
    """package.json listing the dep is not enough — an interrupted install
    can leave it listed but unbuilt. Require lib/index.js too, so a broken
    half-state self-heals on the next run."""
    pkg_json = cfg.profile_package_json
    try:
        if cfg.dsh_plugin_package not in pkg_json.read_text():
            return False
    except OSError:
        return False
    return cfg.plugin_built_artifact.is_file()


def _add_cmd(cfg: Config) -> str:
    return f"dsh plugin --profile {cfg.dsh_profile} add {cfg.dsh_plugin_spec}"


def _plugin_add(cfg: Config, ctx: Context):
    return ctx.mutate(_add_cmd(cfg), logfile=PLUGIN_ADD_LOG)


def _remedy(cfg: Config, ctx: Context, log_text: str) -> bool:
    keys = parse_allowbuilds_keys(log_text)
    if not keys:
        return False
    existing = None
    if cfg.pnpm_workspace.exists():
        existing = cfg.pnpm_workspace.read_text()
    new_text, _ = merge_allowbuilds_lines(existing, keys)
    ctx.write_text(cfg.pnpm_workspace, new_text)
    return True


def run(cfg: Config, ctx: Context) -> StageResult:
    if plugin_installed_built(cfg):
        return StageResult(
            STAGE,
            details={"plugin": cfg.dsh_plugin_package, "profile": cfg.dsh_profile},
            summary_line=f"plugin {cfg.dsh_plugin_package}: present in profile {cfg.dsh_profile}",
        )

    first = _plugin_add(cfg, ctx)
    first_rc = first.returncode
    retry_rc = None
    if first_rc != 0:
        log_text = ""
        if Path(PLUGIN_ADD_LOG).exists():
            log_text = Path(PLUGIN_ADD_LOG).read_text()
        if _remedy(cfg, ctx, log_text):
            retry = _plugin_add(cfg, ctx)
            retry_rc = retry.returncode
            log_text = Path(PLUGIN_ADD_LOG).read_text() if Path(PLUGIN_ADD_LOG).exists() else log_text

    if not cfg.plugin_built_artifact.is_file():
        raise StageFailure(
            f"dsh plugin add did not produce {cfg.plugin_built_artifact} "
            f"(first rc={first_rc}, retry rc={retry_rc}). Inspect {PLUGIN_ADD_LOG} "
            "and the pnpm output above; the usual causes are a still-blocked "
            "build (allowBuilds) or missing network access to github.com.",
            details={"first_rc": first_rc, "retry_rc": retry_rc, "log": PLUGIN_ADD_LOG},
        )

    return StageResult(
        STAGE, changed=True,
        details={"plugin": cfg.dsh_plugin_package, "profile": cfg.dsh_profile,
                 "first_rc": first_rc, "retry_rc": retry_rc},
        summary_line=f"plugin {cfg.dsh_plugin_package}: installed into profile {cfg.dsh_profile}"
        + (f" (after allowBuilds remedy, retry rc={retry_rc})" if retry_rc == 0 else ""),
    )
