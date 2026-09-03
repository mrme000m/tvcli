"""extras — web-profile parity with the production Mac.

Four independent sub-stages (one CLI stage each, one playbook tag each):

  extras-plugins  dsh-mnemon, pi2dsh, dsh-mobile, @deepseek-ai/dsh-mcp-client,
                  pi-agent-memory + the vendored dsh-restart. Each install
                  reuses the allowBuilds remedy from stages/plugin.py.
  extras-mnemon   mnemon settings + profile patch row (memory in the web UI)
  extras-mobile   dsh-mobile gateway setup (mobile access in the web UI;
                  codespace: HTTP gateway behind the forwarded https origin)
  extras-mcp      home-level cordis patch mounting the code-review-graph MCP
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ..config import Config
from ..core import Context, StageResult, StageFailure, pem_sha256_fingerprint
from .plugin import PLUGIN_ADD_LOG, merge_allowbuilds_lines, parse_allowbuilds_keys

PLUGIN_REBUILD_LOG = "/tmp/prime-stack-plugin-rebuild.log"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

MNEMON_ROW = '''# dsh-mnemon tuning (managed by the prime-stack bootstrap)
- id: mnemon
  config:
    routingGuidance: true
    displayMode: sidebar
    lifecycleEnabled: true
    recallMode: guided
    writebackMode: guided
    idleReviewMs: 30000
    tabEnabled: true
    writeEnabled: true
    remoteAccess: trusted-host
    timeoutMs: 10000
    defaultRecallLimit: 10
    embedding:
      enabled: false
      endpoint: http://localhost:11434
      model: nomic-embed-text
    recallQuality:
      policy: strict-v1
      lowScoreThreshold: 0.25
      highScoreThreshold: 0.6
      candidateMultiplier: 3
      maxMediumResults: 4
      maxUnknownResults: 2'''

MNEMON_SETTINGS_SECTION = '''mnemon:
  taskAgentModel:
    mode: fixed
    provider: cloudflare-workers-ai
    model: "@cf/zai-org/glm-5.2"'''


# ---------------------------------------------------------------- plugins ---

def _profile_lists_package(cfg: Config, package: str) -> bool:
    try:
        return f'"{package}"' in cfg.profile_package_json.read_text()
    except OSError:
        return False


def _install_plugin_with_remedy(cfg: Config, ctx: Context, package: str, spec: str) -> str:
    """Returns 'present' | 'installed' | 'installed-after-remedy' | raises."""
    add_cmd = f'dsh plugin --profile {cfg.dsh_profile} add "{spec}"'
    proc = ctx.mutate(add_cmd, logfile=PLUGIN_ADD_LOG)
    if proc.returncode == 0:
        return "installed"
    log_text = Path(PLUGIN_ADD_LOG).read_text() if Path(PLUGIN_ADD_LOG).exists() else ""
    existing = cfg.pnpm_workspace.read_text() if cfg.pnpm_workspace.exists() else None
    new_text, _ = merge_allowbuilds_lines(existing, parse_allowbuilds_keys(log_text))
    if new_text is not None and new_text != existing:
        ctx.write_text(cfg.pnpm_workspace, new_text)
    proc = ctx.mutate(add_cmd, logfile=PLUGIN_ADD_LOG)
    if proc.returncode == 0:
        return "installed-after-remedy"
    raise StageFailure(
        f"parity plugin install failed — inspect {PLUGIN_ADD_LOG} and the pnpm "
        f"output above (package: {package}, spec: {spec})",
        details={"package": package, "spec": spec, "log": PLUGIN_ADD_LOG},
    )


def run_plugins(cfg: Config, ctx: Context) -> StageResult:
    # Pre-seed allowBuilds for known native build scripts: pnpm >= 10 skips
    # the postinstall builds of registry packages silently unless
    # allowlisted — node-pty/ssh2/cloudflared/cpu-features (dsh-mobile deps)
    # NEED their builds to function. Plain-name keys work for registry
    # packages (verified on the production Mac profile).
    existing_ws = cfg.pnpm_workspace.read_text() if cfg.pnpm_workspace.exists() else None
    ws_text, seed_changed = merge_allowbuilds_lines(existing_ws, cfg.native_build_keys)
    if seed_changed:
        ctx.write_text(cfg.pnpm_workspace, ws_text)

    statuses = {}
    for item in cfg.parity_plugins:
        if _profile_lists_package(cfg, item["package"]):
            statuses[item["package"]] = "present"
            continue
        statuses[item["package"]] = _install_plugin_with_remedy(
            cfg, ctx, item["package"], item["spec"]
        )

    changed = any(s != "present" for s in statuses.values()) or seed_changed
    if changed:
        # `dsh plugin add` is a pnpm forwarder; rebuild re-runs the
        # postinstall builds allowBuilds permits (node-pty, ssh2, …).
        # Cheap when everything is already built.
        ctx.mutate(
            f"dsh plugin --profile {cfg.dsh_profile} rebuild >{PLUGIN_REBUILD_LOG} 2>&1"
        )

    return StageResult(
        STAGE_PLUGINS, changed=changed,
        details={"plugins": statuses, "allowbuilds_seeded": seed_changed},
        summary_line="parity plugins (mnemon, pi2dsh, mobile, mcp-client, "
                     "agent-memory, restart): "
                     + ("installed this run" if changed else "checked (already present)"),
    )


# ----------------------------------------------------------------- mnemon ---

def _strip_placeholder_lists(text: str) -> str:
    """A dsh-seeded patch layer may carry a comment header + a bare `[]`
    placeholder — rows must REPLACE that placeholder, not follow it (a list
    after [] is invalid YAML — end-of-stream expected)."""
    import re
    return re.sub(r"(?m)^\[\]\s*$", "", text)


def run_mnemon(cfg: Config, ctx: Context) -> StageResult:
    changed = False

    patch = cfg.web_profile_patch
    text = patch.read_text() if patch.exists() else "[]\n"
    if "id: mnemon" not in text:
        new_text = _strip_placeholder_lists(text)
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        new_text += MNEMON_ROW + "\n"
        if ctx.write_text(patch, new_text):
            changed = True

    settings = cfg.dsh_settings
    settings_text = settings.read_text() if settings.exists() else ""
    has_mnemon = any(l.startswith("mnemon:") for l in settings_text.splitlines())
    if not has_mnemon:
        # settings.yaml may be absent when CF secrets were missing at first
        # boot (dsh-config skips then) — create it so mnemon still lands;
        # chmod 600 matches the dsh-config writer.
        if settings_text and not settings_text.endswith("\n"):
            settings_text += "\n"
        new_text = settings_text + MNEMON_SETTINGS_SECTION + "\n"
        if ctx.write_text(settings, new_text, mode=0o600):
            changed = True

    return StageResult(
        STAGE_MNEMON, changed=changed,
        summary_line="mnemon (memory in the web UI): "
        + ("configured this run" if changed else "configured"),
    )


# ----------------------------------------------------------------- mobile ---

def _env_with_login_shell_fallback(name: str) -> str:
    value = os.environ.get(name, "")
    if value:
        return value
    proc = subprocess.run(
        ["bash", "-lc", f"printenv {name}"], capture_output=True, text=True
    )
    return (proc.stdout or "").strip()


def build_mobile_setup_doc(doc: dict, cs_name: str, cs_domain: str,
                           port: int, home: Path) -> dict:
    """The codespace-forwarded gateway config.

    The plugin enforces "publicOrigin requires TLS" (parseGatewayConfig
    demands tls.mode == "provided"), so the gateway serves the self-signed
    cert the setup CLI generated — on loopback — and is published at the
    forwarded https origin. `instanceId` is NOT a free token: the gateway
    verifies it equals the fingerprint256 of the pairing CA (sha256 of the
    cert DER, hex, lowercase, no colons) at boot — derive it from the
    actual CA so a drifted value self-heals.
    """
    tls = doc.get("tls") or {}
    instance_id = doc.get("instanceId")
    try:
        pem = (home / "tls" / "ca.pem").read_text()
        fingerprint = pem_sha256_fingerprint(pem)
        if fingerprint:
            instance_id = fingerprint
    except OSError:
        pass  # no readable CA yet — keep whatever the setup CLI wrote
    return {
        "version": 1,
        "publicOrigin": f"https://{cs_name}-{port}.{cs_domain}",
        "listenHost": "127.0.0.1",
        "upstreamOrigin": "http://127.0.0.1:3081",
        "instanceId": instance_id,
        "pairingCaFile": str(home / "tls" / "ca.pem"),
        "tls": {
            "mode": "provided",
            "certFile": tls.get("certFile") or str(home / "tls" / "server-cert.pem"),
            "keyFile": tls.get("keyFile") or str(home / "tls" / "server-key.pem"),
        },
        "allowedCidrs": ["127.0.0.0/8", "::1/128"],
    }


def run_mobile(cfg: Config, ctx: Context) -> StageResult:
    changed = False
    details = {}
    home = cfg.dsh_home / "mobile-access"

    # Official CLI setup: creates $DSH_HOME/mobile-access/{tls CA, mobile.css,
    # mobile.js, extensions}, enables the gateway (control.json). The CLI's
    # setup is natively non-interactive (no --yes flag exists). Idempotency
    # guard: the CA is the expensive artifact.
    if not (home / "tls" / "ca.pem").is_file():
        proc = ctx.mutate(
            f"dsh plugin --profile {cfg.dsh_profile} exec dsh-mobile setup "
            f"--port {cfg.mobile_gateway_port} --dsh-port 3081 </dev/null"
        )
        if proc.returncode != 0:
            return StageResult(
                STAGE_MOBILE, failed=True,
                error=f"dsh-mobile setup failed (rc={proc.returncode})",
                summary_line="mobile access: FAILED (dsh-mobile setup)",
            )
        changed = True
        details["setup"] = "ran this run"

    setup = home / "setup.json"
    cs_name = _env_with_login_shell_fallback("CODESPACE_NAME")
    cs_domain = _env_with_login_shell_fallback("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    if cs_name and cs_domain:
        doc = {}
        if setup.exists():
            try:
                doc = json.loads(setup.read_text())
            except Exception:
                doc = {}
        out = build_mobile_setup_doc(doc if isinstance(doc, dict) else {},
                                     cs_name, cs_domain, cfg.mobile_gateway_port, home)
        if ctx.write_text(setup, json.dumps(out, indent=2) + "\n", mode=0o600):
            changed = True
        details["origin"] = "codespace"
    else:
        details["origin"] = "managed LAN (not a codespace)"

    # Create-only: a control.json the user turned OFF from the web UI is
    # never force-re-enabled.
    control = home / "control.json"
    if not control.exists():
        if ctx.write_text(control, '{"version":1,"enabled":true}\n', mode=0o600):
            changed = True

    return StageResult(
        STAGE_MOBILE, changed=changed, details=details,
        summary_line=f"mobile access (gateway :{cfg.mobile_gateway_port}): "
        + ("configured this run" if changed else "configured"),
    )


# -------------------------------------------------------------------- mcp ---

def run_mcp(cfg: Config, ctx: Context) -> StageResult:
    proc = ctx.exec("command -v code-review-graph >/dev/null 2>&1")
    if proc.returncode != 0:
        return StageResult(
            STAGE_MCP, skipped=True,
            warnings=["code-review-graph not installed — home-level patch skipped"],
            summary_line="code-review-graph MCP (home-level patch): SKIPPED (CLI not installed)",
        )
    patch = cfg.dsh_home / "cordis.patch.yml"
    if patch.exists():
        return StageResult(
            STAGE_MCP,
            summary_line="code-review-graph MCP (home-level patch): present",
        )
    template = (TEMPLATES_DIR / "cordis-home-mcp.yml").read_text()
    changed = ctx.write_text(patch, template, mode=0o600)
    return StageResult(
        STAGE_MCP, changed=changed,
        summary_line="code-review-graph MCP (home-level patch): "
        + ("patched this run" if changed else "present"),
    )


STAGE_PLUGINS = "extras-plugins"
STAGE_MNEMON = "extras-mnemon"
STAGE_MOBILE = "extras-mobile"
STAGE_MCP = "extras-mcp"
