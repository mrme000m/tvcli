"""fleet — the specialist agent fleet + grid-trading wiring.

Implements the autonomous grid-trading loop blueprint
(bootstrapping/docs/grid-fleet.md):

  tv-investigator screen/research (tvcli /hunt fan-out across the
                  accounts.json multi-account cookie pool + network-API
                  investigation; regime ranking via token_screen.py)
  wt-investigator configure (WunderTrading bots — Grid/DCA/Signal/
                  Multi-Pair Grid — via wt CLI, REST, MCP, headful UI)
  tv-scout        confirm (visual confluence on the live chart)
  prime-orchestrator  coordinate/manage (fleet + prime-agent workers)

Sub-stages (one CLI stage / playbook tag each):

  fleet-presets   vendored presets from bootstrapping/presets/, installed
                  with marker semantics (user-owned dirs are preserved)
  fleet-patch     the wundertrading MCP row in the web profile (keys read
                  from the vault at provision time — never committed, never
                  logged) + the wt-tools cloak-dir override
  fleet-autoserve tvcli multi-account HTTP server (POST /hunt fan-out over
                  the account pool) autostart marker

Mac paths in the vendored preset copies are @TV_WORKSPACE@ / @CLOAK_DIR@
placeholders, resolved at install time.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..config import FLEET_PRESET_FILES, WT_MCP_URL, Config
from ..core import Context, StageResult, sha256_text
from .extras import _strip_placeholder_lists

STAGE_PRESETS = "fleet-presets"
STAGE_PATCH = "fleet-patch"
STAGE_AUTOSERVE = "fleet-autoserve"


# ---------------------------------------------------------------- presets ---

def render_preset_text(text: str, workspace: str, cloak_dir: str) -> str:
    return text.replace("@TV_WORKSPACE@", workspace).replace("@CLOAK_DIR@", cloak_dir)


def preset_plan(src_dir: Path, files: list, workspace: str, cloak_dir: str) -> dict:
    """Rendered file contents + their sha256 hashes (the marker payload)."""
    rendered = {f: render_preset_text((src_dir / f).read_text(), workspace, cloak_dir)
                for f in files}
    return {"files": {f: sha256_text(t) for f, t in rendered.items()}, "rendered": rendered}


def preset_marker_status(marker_doc: dict, hashes: dict, managed_by: str) -> str:
    """Decision for one preset dir: 'update' | 'unchanged' | 'user-owned'.

    Marker semantics (matches the dsh plugin's own):
      - marker, managedBy matches, hashes identical  → unchanged
      - marker, managedBy matches, hashes differ      → update
      - marker with a different managedBy             → user-owned
      - dir without OUR marker                        → user-owned
    """
    if marker_doc.get("managedBy") == managed_by:
        return "unchanged" if marker_doc.get("files") == hashes else "update"
    return "user-owned"


def run_presets(cfg: Config, ctx: Context) -> StageResult:
    statuses = {}
    warnings = []
    changed = False

    for name in cfg.fleet_presets:
        src_dir = cfg.fleet_preset_src / name
        dst_dir = cfg.preset_root / name
        marker = dst_dir / cfg.fleet_preset_marker
        if not src_dir.is_dir():
            warnings.append(f"vendored preset missing: {src_dir}")
            statuses[name] = "missing-source"
            continue

        plan = preset_plan(src_dir, list(FLEET_PRESET_FILES),
                           str(cfg.workspace), str(cfg.wt_cloak_dir))
        marker_doc = {}
        if marker.exists():
            try:
                marker_doc = json.loads(marker.read_text() or "{}")
            except Exception:
                marker_doc = {}
        elif dst_dir.exists():
            statuses[name] = "preserved: user-owned (no prime-stack marker)"
            warnings.append(f"{name}: user-owned (no marker) — preserved untouched")
            continue

        status = preset_marker_status(marker_doc, plan["files"], cfg.fleet_managed_by)
        if status == "unchanged":
            statuses[name] = "unchanged"
            continue
        if status == "user-owned":
            statuses[name] = "preserved: user-owned (marker managedBy differs)"
            warnings.append(f"{name}: user-owned (marker managedBy differs) — preserved untouched")
            continue

        was_new = not marker.exists()
        ctx.mkdir(dst_dir)
        for fname, text in plan["rendered"].items():
            ctx.write_text(dst_dir / fname, text)
        ctx.write_text(marker, json.dumps(
            {"managedBy": cfg.fleet_managed_by, "version": 1,
             "files": plan["files"]}, indent=2) + "\n")
        statuses[name] = "installed" if was_new else "updated"
        changed = True

    return StageResult(
        STAGE_PRESETS, changed=changed, warnings=warnings,
        details={"presets": statuses},
        summary_line="fleet presets (tv-scout, tv-investigator, wt-investigator): "
        + ("installed/updated this run" if changed else "present or user-preserved"),
    )


# ------------------------------------------------------------------ patch ---

def load_env_file(text: str) -> dict:
    keys = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            keys[k.strip()] = v.strip()
    return keys


def wt_keys(cfg: Config) -> dict:
    """WT_API_KEY / WT_API_SECRET from the bw-provisioned runtime wt.env,
    falling back to the process env (standalone runs without wt.env yet).
    Values never leave this function."""
    keys = load_env_file(cfg.wt_runtime_env.read_text()) if cfg.wt_runtime_env.exists() else {}
    return {k: keys.get(k) or os.environ.get(k, "")
            for k in ("WT_API_KEY", "WT_API_SECRET")}


def upsert_grid_rows(doc: list, keys: dict, cloak_dir: str, mcp_url: str) -> tuple:
    """Idempotent upsert of the two grid-fleet rows into a cordis patch list.

    Returns (new_doc, rows_written). Rows are removed by id first, then
    appended in a deterministic order, so re-runs converge. Existing user
    rows are preserved verbatim.
    """
    doc = [row for row in doc
           if not (isinstance(row, dict) and row.get("id") in ("mcp-wundertrading", "wt-tools"))]
    rows_written = []
    if all(keys.values()):
        doc.append({
            "id": "mcp-wundertrading",
            "name": "@deepseek-ai/dsh-mcp-client",
            "config": {
                "serverName": "wundertrading",
                "transport": "streamable-http",
                "url": mcp_url,
                "toolCallTimeoutMs": 60000,
                "headers": {
                    "X-API-Key": keys["WT_API_KEY"],
                    "X-Secret-Key": keys["WT_API_SECRET"],
                },
            },
        })
        rows_written.append("mcp-wundertrading")
    doc.append({"id": "wt-tools", "config": {"cloakDir": cloak_dir}})
    rows_written.append("wt-tools")
    return doc, rows_written


def run_patch(cfg: Config, ctx: Context) -> StageResult:
    import yaml

    patch_file = cfg.web_profile_patch
    text = patch_file.read_text() if patch_file.exists() else ""
    text = _strip_placeholder_lists(text)
    try:
        doc = yaml.safe_load(text) or [] if text.strip() else []
        if not isinstance(doc, list):
            doc = []
    except yaml.YAMLError as exc:
        return StageResult(
            STAGE_PATCH, failed=True,
            error=f"cannot parse {patch_file}: {exc}",
            summary_line="grid-fleet patch rows: FAILED (cordis.patch.yml unparseable)",
        )

    keys = wt_keys(cfg)
    warnings = []
    if not all(keys.values()):
        warnings.append("wundertrading keys missing (wt.env not provisioned / "
                        "env unset) — MCP row skipped; the wt_* plugin tools "
                        "keep working (they read the vault through the credential seam)")

    before = json.dumps(doc, sort_keys=True)
    doc, rows_written = upsert_grid_rows(doc, keys, str(cfg.wt_cloak_dir), WT_MCP_URL)
    after = json.dumps(doc, sort_keys=True)
    changed = after != before

    if changed or not patch_file.exists():
        ctx.mkdir(patch_file.parent)
        ctx.write_text(patch_file, yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
                       mode=0o600)

    return StageResult(
        STAGE_PATCH, changed=changed, warnings=warnings,
        details={"rows": rows_written},
        summary_line="wundertrading MCP row + wt-tools cloakDir (web profile patch): "
        + ("written this run" if changed else "checked (rows current)"),
    )


# -------------------------------------------------------------- autoserve ---

def run_autoserve(cfg: Config, ctx: Context) -> StageResult:
    if not cfg.fleet_enable_tvcli_autoserve:
        return StageResult(
            STAGE_AUTOSERVE, skipped=True,
            summary_line="tvcli multi-account autoserve: disabled by configuration",
        )
    if not (cfg.workspace / "accounts.json").is_file():
        return StageResult(
            STAGE_AUTOSERVE, skipped=True,
            warnings=["accounts.json not provisioned yet (bw-provision) — tvcli autoserve stays off"],
            summary_line="tvcli multi-account autoserve: SKIPPED (accounts.json not provisioned)",
        )
    marker = cfg.workspace / ".tvcli-autoserve"
    if marker.exists():
        return StageResult(
            STAGE_AUTOSERVE,
            summary_line="tvcli multi-account autoserve (.tvcli-autoserve): enabled",
        )
    ctx.touch(marker)
    return StageResult(
        STAGE_AUTOSERVE, changed=True,
        summary_line="tvcli multi-account autoserve (.tvcli-autoserve): enabled this run",
    )
