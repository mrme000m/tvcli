"""stealth-browser — install + configure vibheksoni/stealth-browser-mcp.

Installs the stealth-browser-mcp checkout (venv + pip install) and wires it
as a stdio MCP row (via @deepseek-ai/dsh-mcp-client) in the dsh web profile
patch so dsh + prime agents see 97 stealth browser tools.

Idempotent: clone/pull, venv create, pip install only when needed; MCP row
upsert is deterministic (remove by id then append).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..config import Config
from ..core import Context, StageResult

STAGE = "stealth-browser"
REPO_URL = "https://github.com/vibheksoni/stealth-browser-mcp.git"
MCP_ID = "mcp-stealth-browser"


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").is_dir()


def _venv_python(cfg: Config) -> Path:
    return cfg.stealth_browser_dir / "venv" / "bin" / "python"


def _server_py(cfg: Config) -> Path:
    return cfg.stealth_browser_dir / "src" / "server.py"


def _ensure_checkout(cfg: Config, ctx: Context) -> tuple[str, bool]:
    """Ensure checkout exists and is up-to-date. Returns (status, changed)."""
    dest = cfg.stealth_browser_dir
    if _is_git_repo(dest):
        # fetch + fast-forward if behind; cheap and idempotent
        proc = ctx.exec(f'git -C "{dest}" fetch --depth 1 origin 2>&1', timeout=60)
        # best-effort fast-forward; ignore failure (offline)
        if proc.returncode == 0:
            before = ctx.exec(f'git -C "{dest}" rev-parse HEAD').stdout.strip()
            ctx.mutate(f'git -C "{dest}" reset --hard origin/master 2>&1 || git -C "{dest}" reset --hard origin/main 2>&1 || true', timeout=30)
            after = ctx.exec(f'git -C "{dest}" rev-parse HEAD').stdout.strip()
            changed = before != after and bool(after)
            return ("updated" if changed else "present"), changed
        return "present", False
    if dest.exists():
        # non-git directory at target — treat as present but warn
        return "present", False
    ctx.mkdir(dest.parent)
    proc = ctx.mutate(f'git clone --depth 1 "{REPO_URL}" "{dest}" 2>&1', timeout=90)
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed (rc={proc.returncode}): {(proc.stdout+proc.stderr)[-800:]}")
    return "cloned", True


def _ensure_venv(cfg: Config, ctx: Context) -> tuple[str, bool]:
    py = _venv_python(cfg)
    if py.is_file():
        return "present", False
    # python3 -m venv venv (uses system python)
    proc = ctx.mutate(f'python3 -m venv "{cfg.stealth_browser_dir / "venv"}" 2>&1', timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"venv creation failed: {(proc.stdout+proc.stderr)[-800:]}")
    return "created", True


def _ensure_deps(cfg: Config, ctx: Context) -> tuple[str, bool]:
    """pip install -r requirements.txt when marker is stale."""
    server = _server_py(cfg)
    venv_py = _venv_python(cfg)
    req = cfg.stealth_browser_dir / "requirements.txt"
    marker = cfg.stealth_browser_dir / "venv" / ".stealth-deps-installed"
    if not server.is_file():
        return "skipped (no server.py)", False
    if not req.is_file():
        return "skipped (no requirements.txt)", False
    # if marker exists and requirements haven't changed, skip
    import hashlib
    want = hashlib.sha256(req.read_bytes()).hexdigest()[:12]
    have = marker.read_text().strip() if marker.exists() and not ctx.dry_run else ""
    if have == want:
        # also verify fastmcp importable
        proc = ctx.exec(f'"{venv_py}" -c "import fastmcp" 2>&1', timeout=10)
        if proc.returncode == 0:
            return "present", False
    if ctx.dry_run:
        return "would-install", True
    proc = ctx.mutate(f'"{venv_py}" -m pip install --upgrade pip -q 2>&1', timeout=60)
    proc = ctx.mutate(f'"{venv_py}" -m pip install -r "{req}" -q 2>&1', timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"pip install failed: {(proc.stdout+proc.stderr)[-1200:]}")
    # verify server imports
    proc = ctx.exec(f'"{venv_py}" "{server}" --help 2>&1 | head -n 20', timeout=15)
    if proc.returncode != 0 and "usage" not in (proc.stdout+proc.stderr).lower():
        # help may exit non-zero in some versions; check list-sections instead
        proc2 = ctx.exec(f'"{venv_py}" "{server}" --list-sections 2>&1', timeout=15)
        if proc2.returncode != 0 and not proc2.stdout:
            raise RuntimeError(f"server sanity check failed: {(proc.stdout+proc.stderr)[-800:]}")
    ctx.write_text(marker, want)
    return "installed", True


def _mcp_row(cfg: Config) -> dict:
    venv_py = _venv_python(cfg)
    server = _server_py(cfg)
    # Use absolute paths so dsh workers resolve them regardless of cwd
    return {
        "id": MCP_ID,
        "name": "@deepseek-ai/dsh-mcp-client",
        "config": {
            "serverName": "stealth-browser",
            "transport": "stdio",
            "command": str(venv_py),
            "args": [str(server)],
            "cwd": str(cfg.stealth_browser_dir / "src"),
            "toolCallTimeoutMs": 120000,
            "failOnStartupError": False,
        },
    }


def _upsert_row(doc: list, row: dict) -> tuple[list, bool]:
    before = json.dumps(doc, sort_keys=True)
    doc = [r for r in doc if not (isinstance(r, dict) and r.get("id") == row["id"])]
    doc.append(row)
    after = json.dumps(doc, sort_keys=True)
    return doc, before != after


def _ensure_browser_symlink(cfg: Config, ctx: Context) -> tuple[str, bool]:
    """Ensure a Chrome binary is discoverable via PATH for stealth-browser.

    Codespaces have no system chrome — the stealth CloakBrowser checkout
    (browser-debug/cloakbrowser/chromium-*/chrome) is the only binary.
    platform_utils.check_browser_executable searches /usr/bin + `which`.
    Idempotent: create symlinks to the cloak binary when no executable is
    found, so spawn_browser(headless=False) lands on DISPLAY=:99 and is
    visible via x11vnc:5900 / noVNC:6080 (shared Xvfb).
    """
    import shutil, os
    # fast path: already resolvable
    venv_py = _venv_python(cfg)
    if venv_py.is_file():
        proc = ctx.exec(f'"{venv_py}" -c \'import sys; sys.path.insert(0, "{cfg.stealth_browser_dir}/src"); from platform_utils import check_browser_executable; print(check_browser_executable() or "")\' 2>&1', timeout=10)
        if proc.stdout.strip():
            return "present", False
    # also check host which
    for name in ("google-chrome", "google-chrome-stable", "chrome", "chromium"):
        if shutil.which(name):
            return "present", False
    # find cloak binary
    cloak_candidates = sorted(cfg.workspace.glob("browser-debug/cloakbrowser/chromium-*/chrome"))
    cloak_bin = cloak_candidates[-1] if cloak_candidates else None
    if not cloak_bin or not cloak_bin.is_file():
        return "skipped (no cloak binary)", False
    # symlink into /usr/local/bin + /usr/bin (best-effort, needs sudo)
    # Use ctx.mutate so --dry-run is faithful and failures are non-fatal.
    link_targets = ["/usr/local/bin/chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"]
    changed = False
    for link in link_targets:
        proc = ctx.mutate(f'sudo ln -sf "{cloak_bin}" "{link}" 2>&1 || ln -sf "{cloak_bin}" "{link}" 2>&1; test -x "{link}" && echo ok', timeout=10)
        if "ok" in (proc.stdout or ""):
            # first successful link is enough to unblock platform_utils.which
            changed = True
            break
    if not changed:
        # fallback: prepend cloak dir to PATH via env bridge? for now warn
        return "skipped (symlink failed)", False
    # verify
    proc = ctx.exec(f'"{venv_py}" -c \'import sys; sys.path.insert(0, "{cfg.stealth_browser_dir}/src"); from platform_utils import check_browser_executable; print(check_browser_executable() or "")\' 2>&1', timeout=10)
    if proc.stdout.strip():
        return "symlinked", True
    return "symlinked (unverified)", True


def _wire_mcp(cfg: Config, ctx: Context) -> tuple[str, bool, list]:
    warnings: list[str] = []
    server = _server_py(cfg)
    venv_py = _venv_python(cfg)
    if not server.is_file():
        warnings.append(f"{server} not found — MCP row skipped (run install first)")
        return "skipped (no checkout)", False, warnings
    if not venv_py.is_file():
        warnings.append(f"{venv_py} not found — MCP row skipped")
        return "skipped (no venv)", False, warnings

    patch_file = cfg.web_profile_patch
    text = patch_file.read_text() if patch_file.exists() else ""
    # reuse helper that strips placeholder []
    from .extras import _strip_placeholder_lists
    text = _strip_placeholder_lists(text)
    try:
        import yaml
        doc = yaml.safe_load(text) or [] if text.strip() else []
        if not isinstance(doc, list):
            doc = []
    except Exception as exc:
        return f"FAILED (unparseable {patch_file}: {exc})", False, warnings

    row = _mcp_row(cfg)
    doc2, changed = _upsert_row(doc, row)
    if changed or not patch_file.exists():
        import yaml
        ctx.mkdir(patch_file.parent)
        ctx.write_text(patch_file, yaml.safe_dump(doc2, sort_keys=False, allow_unicode=True), mode=0o600)
    return ("written" if changed else "current"), changed, warnings


def run(cfg: Config, ctx: Context) -> StageResult:
    warnings: list[str] = []
    details: dict = {}
    changed_any = False

    # 1. checkout
    try:
        status, changed = _ensure_checkout(cfg, ctx)
        details["checkout"] = status
        changed_any |= changed
    except Exception as exc:
        return StageResult(STAGE, failed=True, error=str(exc), summary_line=f"stealth-browser: FAILED (checkout: {exc})")

    # 2. venv
    try:
        status, changed = _ensure_venv(cfg, ctx)
        details["venv"] = status
        changed_any |= changed
    except Exception as exc:
        return StageResult(STAGE, failed=True, error=str(exc), summary_line=f"stealth-browser: FAILED (venv: {exc})")

    # 3. deps
    try:
        status, changed = _ensure_deps(cfg, ctx)
        details["deps"] = status
        changed_any |= changed
    except Exception as exc:
        return StageResult(STAGE, failed=True, error=str(exc), warnings=warnings, summary_line=f"stealth-browser: FAILED (pip install: {exc})")

    # 4. ensure browser binary is discoverable (cloak → /usr/bin) for headful VNC
    try:
        b_status, b_changed = _ensure_browser_symlink(cfg, ctx)
        details["browser_bin"] = b_status
        changed_any |= b_changed
        if b_status.startswith("skipped"):
            warnings.append(f"browser symlink {b_status} — headless stealth still works, headful needs a Chrome binary on PATH")
    except Exception as exc:  # non-fatal
        details["browser_bin"] = f"error: {exc}"
        warnings.append(f"browser symlink failed: {exc}")

    # 5. wire MCP row
    mcp_status, mcp_changed, mcp_warnings = _wire_mcp(cfg, ctx)
    details["mcp"] = mcp_status
    warnings.extend(mcp_warnings)
    changed_any |= mcp_changed

    # 6. also ensure runtime file-upload allow dirs include workspace (for file_upload tool)
    # non-fatal hint only

    summary = "stealth-browser-mcp: " + ("installed/configured this run" if changed_any else "present (checkout, venv, deps, MCP row current + headful via VNC)")
    if warnings:
        summary += f" ({len(warnings)} warning(s))"
    return StageResult(STAGE, changed=changed_any, warnings=warnings, details=details, summary_line=summary)
