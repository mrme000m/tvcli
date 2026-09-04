"""cloudflare — wrangler CLI + upstream Cloudflare agent skills for every harness.

Upstream: https://github.com/cloudflare/skills (Apache-2.0). What the stage does:

  1. wrangler install — `npm install -g wrangler` when `wrangler --version`
     fails (npm globals reset on every codespace rebuild, so post-create
     re-runs this; idempotent via the version probe).
  2. auth bridge — marker block in ~/.profile + ~/.bashrc exporting
     CLOUDFLARE_API_TOKEN (falling back to CLOUDFLARE_API_KEY) so wrangler's
     native env auth works in every login shell without `wrangler login`
     (browser OAuth — unusable headless). Values are never written, only
     `${VAR:-fallback}` references. `wrangler whoami` verifies (warn-only;
     the Workers AI key may lack deploy scopes — then export
     CLOUDFLARE_API_TOKEN from vault item `cloudflare-tunnels` / `write-all`).
  3. skills vendor — shallow clone of cloudflare/skills to
     ~/.local/share/prime-stack/cloudflare-skills (fetch + fast-forward on
     re-run), then symlink CF_SKILL_NAMES (wrangler, cloudflare,
     cloudflare-one) into the opencode (~/.config/opencode/skills) and
     prime-agent (~/.prime/skills) skills dirs. dsh has no skills dir —
     dsh workers read the same skills from the stable vendor path, and the
     repo-local `.agents/skills/cf` skill stays the dsh-facing entry point.
  4. MCP — the upstream remote server (https://mcp.cloudflare.com/mcp) is
     wired as `mcp-cloudflare` (streamable-http, no secret headers) in the
     dsh web profile patch, and keyed-merged into opencode.jsonc. Docs
     retrieval works unauthenticated; API tools pick up the env auth above.

Idempotent throughout; auth problems warn (never fatal) unless strict, per
the secrets contract. See browser-debug/secrets/VAULT_CONVENTIONS.md.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..config import CF_MCP_ROW_ID, CF_MCP_URL, Config
from ..core import Context, StageResult, StageFailure, marker_block_text

STAGE = "cloudflare"

AUTH_MARKER_BEGIN = "# >>> prime-stack cloudflare auth >>>"
AUTH_MARKER_END = "# <<< prime-stack cloudflare auth <<<"
AUTH_BLOCK = '''export CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-${CLOUDFLARE_API_KEY:-}}"
export CLOUDFLARE_ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-${CF_ACCOUNT_ID:-}}"'''

OPENCODE_MCP_KEY = "cloudflare"


# ---------------------------------------------------------------- pure -------

def plan_skill_links(vendor: Path, dest_root: Path, names: list) -> list:
    """(src, dest) pairs for skills present upstream. Pure/testable."""
    pairs = []
    for name in names:
        src = vendor / "skills" / name
        pairs.append((src, dest_root / name))
    return pairs


def link_state(dest: Path, src: Path) -> str:
    """'current' | 'missing' | 'stale' — read-only, safe in dry-run."""
    if dest.is_symlink():
        try:
            if os.path.realpath(dest) == os.path.realpath(src):
                return "current"
        except OSError:
            pass
        return "stale"
    if dest.exists():
        return "stale"  # user-owned file/dir — never clobber
    return "missing"


def merge_opencode_mcp(doc: dict, url: str = CF_MCP_URL) -> None:
    """Keyed merge of the Cloudflare remote MCP server. Preserves the rest."""
    doc.setdefault("mcp", {})[OPENCODE_MCP_KEY] = {
        "type": "remote",
        "url": url,
        "enabled": True,
    }


def cloudflare_mcp_row(url: str = CF_MCP_URL) -> dict:
    """dsh web-profile row (same streamable-http shape as mcp-wundertrading,
    but no secret headers — docs retrieval is unauthenticated)."""
    return {
        "id": CF_MCP_ROW_ID,
        "name": "@deepseek-ai/dsh-mcp-client",
        "config": {
            "serverName": "cloudflare",
            "transport": "streamable-http",
            "url": url,
            "toolCallTimeoutMs": 60000,
        },
    }


def upsert_row(doc: list, row: dict) -> tuple:
    before = json.dumps(doc, sort_keys=True)
    doc = [r for r in doc if not (isinstance(r, dict) and r.get("id") == row["id"])]
    doc.append(row)
    after = json.dumps(doc, sort_keys=True)
    return doc, before != after


# ---------------------------------------------------------------- stage -------

def _wrangler_present(ctx: Context) -> bool:
    return ctx.exec("command -v wrangler >/dev/null 2>&1 && wrangler --version 2>&1").returncode == 0


def _ensure_wrangler(ctx: Context) -> tuple:
    if _wrangler_present(ctx):
        return "present", False
    proc = ctx.mutate("npm install -g wrangler 2>&1", timeout=180)
    if proc.returncode != 0:
        raise StageFailure(
            "npm install -g wrangler failed — check network access to registry.npmjs.org",
            details={"rc": proc.returncode},
        )
    if ctx.dry_run:
        return "would-install", True
    if not _wrangler_present(ctx):
        raise StageFailure("wrangler installed but `wrangler --version` still fails")
    return "installed", True


def _ensure_auth_bridge(cfg: Config, ctx: Context) -> bool:
    changed = False
    for name in (".profile", ".bashrc"):
        rc_file = cfg.home / name
        existing = rc_file.read_text() if rc_file.exists() else ""
        new_text, file_changed = marker_block_text(existing, AUTH_MARKER_BEGIN, AUTH_MARKER_END, AUTH_BLOCK)
        if file_changed:
            ctx.write_text(rc_file, new_text)
            changed = True
    return changed


def _ensure_vendor(cfg: Config, ctx: Context, repo: str) -> tuple:
    """Shallow clone / fast-forward of cloudflare/skills. Returns (status, changed)."""
    from .stealth_browser import _is_git_repo
    dest = cfg.cf_vendor_dir
    if _is_git_repo(dest):
        proc = ctx.exec(f'git -C "{dest}" fetch --depth 1 origin 2>&1', timeout=60)
        if proc.returncode == 0:
            before = ctx.exec(f'git -C "{dest}" rev-parse HEAD').stdout.strip()
            ctx.mutate(f'git -C "{dest}" reset --hard origin/main 2>&1 || git -C "{dest}" reset --hard origin/master 2>&1 || true', timeout=30)
            after = ctx.exec(f'git -C "{dest}" rev-parse HEAD').stdout.strip()
            changed = before != after and bool(after)
            return ("updated" if changed else "present"), changed
        return "present", False
    if dest.exists():
        return "present", False
    ctx.mkdir(dest.parent)
    proc = ctx.mutate(f'git clone --depth 1 "{repo}" "{dest}" 2>&1', timeout=90)
    if proc.returncode != 0:
        raise StageFailure(
            f"git clone of cloudflare/skills failed (rc={proc.returncode})",
            details={"repo": repo},
        )
    return "cloned", True


def _ensure_skill_links(cfg: Config, ctx: Context) -> tuple:
    """Symlink vendored skills into opencode + prime-agent skills dirs.

    Never clobbers user-owned files (link_state 'stale' on a non-symlink is
    left alone with a warning). Returns (details, changed, warnings).
    """
    details: dict = {}
    warnings: list[str] = []
    changed = False
    if not (cfg.cf_vendor_dir / "skills").is_dir():
        warnings.append(f"{cfg.cf_vendor_dir}/skills not found — skill links skipped (vendor first)")
        return {"links": "skipped (no vendor)"}, False, warnings
    for label, root in (("opencode", cfg.opencode_skills_dir), ("prime", cfg.prime_skills_dir)):
        states: dict = {}
        for src, dest in plan_skill_links(cfg.cf_vendor_dir, root, cfg.cf_skill_names):
            if not src.is_dir():
                states[src.name] = "missing-upstream"
                continue
            state = link_state(dest, src)
            states[dest.name] = state
            if state == "current":
                continue
            if state == "stale" and not dest.is_symlink():
                warnings.append(f"{label} skills/{dest.name} is user-owned — left alone")
                continue
            ctx.mkdir(root)
            ctx.mutate(f'ln -sfn "{src}" "{dest}"')
            states[dest.name] = "linked this run" if not ctx.dry_run else "would-link"
            changed = True
        details[label] = states
    return details, changed, warnings


def _ensure_opencode_mcp(cfg: Config, ctx: Context) -> tuple:
    """Keyed-merge the Cloudflare MCP server into opencode.jsonc.

    The file is JSONC — if it ever gains comments json parsing fails and we
    warn + skip rather than clobber them.
    """
    path = cfg.opencode_config
    if not path.exists():
        return "skipped (no opencode.jsonc)", False, []
    try:
        doc = json.loads(path.read_text())
        if not isinstance(doc, dict):
            raise ValueError("top-level JSON is not an object")
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        return "skipped", False, [f"opencode.jsonc not plain JSON ({exc}) — MCP merge skipped to protect comments"]
    before = json.dumps(doc, sort_keys=True)
    merge_opencode_mcp(doc)
    if json.dumps(doc, sort_keys=True) == before:
        return "current", False, []
    text = json.dumps(doc, indent=2) + "\n"
    ctx.write_text(path, text)
    return ("merged this run" if not ctx.dry_run else "would-merge"), True, []


def _ensure_dsh_mcp_row(cfg: Config, ctx: Context) -> tuple:
    from .extras import _strip_placeholder_lists
    patch_file = cfg.web_profile_patch
    text = patch_file.read_text() if patch_file.exists() else ""
    text = _strip_placeholder_lists(text)
    try:
        import yaml
        doc = yaml.safe_load(text) or [] if text.strip() else []
        if not isinstance(doc, list):
            doc = []
    except Exception as exc:
        return "FAILED", False, [f"unparseable {patch_file}: {exc}"]
    doc2, changed = upsert_row(doc, cloudflare_mcp_row())
    if changed or not patch_file.exists():
        import yaml
        ctx.mkdir(patch_file.parent)
        ctx.write_text(patch_file, yaml.safe_dump(doc2, sort_keys=False, allow_unicode=True), mode=0o600)
    return ("written" if changed else "current"), changed, []


def _verify_whoami(ctx: Context) -> tuple:
    """`wrangler whoami` — proves the token works. Warn-only (the Workers AI
    key often lacks deploy scopes; never break the stack over it)."""
    token = os.environ.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CLOUDFLARE_API_KEY")
    if not token:
        return "skipped", ["CLOUDFLARE_API_TOKEN / CLOUDFLARE_API_KEY not set — "
                           "wrangler installed but unverified (export a token from "
                           "vault item cloudflare-tunnels / write-all for deploys)"]
    proc = ctx.exec("wrangler whoami 2>&1", timeout=30)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        hint = out[-300:] if out else f"rc={proc.returncode}"
        return "unverified", [f"`wrangler whoami` failed ({hint}) — token may lack "
                              "account scopes; deploys need CLOUDFLARE_API_TOKEN from "
                              "the vault write-all field"]
    return "verified", []


def run(cfg: Config, ctx: Context) -> StageResult:
    from ..config import CF_SKILLS_REPO
    warnings: list[str] = []
    details: dict = {}
    changed_any = False

    # 1. wrangler CLI
    try:
        status, changed = _ensure_wrangler(ctx)
        details["wrangler"] = status
        changed_any |= changed
    except StageFailure as exc:
        return StageResult(STAGE, failed=True, error=str(exc),
                           details={"wrangler": "FAILED"},
                           summary_line=f"cloudflare: FAILED ({exc})")

    # 2. auth bridge (login shells)
    try:
        bridge_changed = _ensure_auth_bridge(cfg, ctx)
        details["auth_bridge"] = "written this run" if bridge_changed else "present"
        changed_any |= bridge_changed
    except OSError as exc:
        warnings.append(f"auth bridge failed: {exc}")

    # 3. vendor upstream skills
    try:
        status, changed = _ensure_vendor(cfg, ctx, CF_SKILLS_REPO)
        details["vendor"] = status
        changed_any |= changed
    except StageFailure as exc:
        warnings.append(str(exc))
        details["vendor"] = "FAILED (non-fatal)"
        details["links"] = "skipped (no vendor)"
        status_vendor_ok = False
    else:
        status_vendor_ok = True

    # 4. skill links (only when the vendor checkout exists on disk)
    if status_vendor_ok and (cfg.cf_vendor_dir / "skills").is_dir():
        try:
            link_details, link_changed, link_warnings = _ensure_skill_links(cfg, ctx)
            details["links"] = link_details
            warnings.extend(link_warnings)
            changed_any |= link_changed
        except OSError as exc:
            warnings.append(f"skill links failed: {exc}")
    elif status_vendor_ok:
        details["links"] = "skipped (vendor has no skills/ — offline clone?)"
        warnings.append("cloudflare-skills vendor has no skills/ dir — links skipped")

    # 5. opencode MCP merge
    oc_status, oc_changed, oc_warnings = _ensure_opencode_mcp(cfg, ctx)
    details["opencode_mcp"] = oc_status
    warnings.extend(oc_warnings)
    changed_any |= oc_changed

    # 6. dsh MCP row
    dsh_status, dsh_changed, dsh_warnings = _ensure_dsh_mcp_row(cfg, ctx)
    details["dsh_mcp_row"] = dsh_status
    warnings.extend(dsh_warnings)
    changed_any |= dsh_changed

    # 7. verify (warn-only; strict makes missing/unverifiable auth fatal)
    who_status, who_warnings = _verify_whoami(ctx)
    details["whoami"] = who_status
    if cfg.strict and who_status != "verified":
        raise StageFailure(
            "prime_stack_strict=true but wrangler auth is not verified — "
            "set CLOUDFLARE_API_TOKEN (vault cloudflare-tunnels / write-all)",
            details={"whoami": who_status},
        )
    warnings.extend(who_warnings)

    summary = "cloudflare (wrangler + skills + MCP for dsh/prime-agent/opencode): " + (
        "configured this run" if changed_any else "present")
    if warnings:
        summary += f" ({len(warnings)} warning(s))"
    return StageResult(STAGE, changed=changed_any, warnings=warnings,
                       details=details, summary_line=summary)
