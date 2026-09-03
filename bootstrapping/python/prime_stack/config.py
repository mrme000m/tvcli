"""Configuration: component identities, the CF model catalog, runtime paths.

`Config.from_env` is the single place stages learn about the world. The
Cloudflare model catalog lives ONLY here (the old design duplicated it
between an Ansible var and an inline settings.yaml template, and the two
had already drifted) — surface-specific deltas (dsh settings.yaml names
some models differently and caps maxTokens) are explicit override tables
instead of a second hand-maintained copy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --- fixed component identities -------------------------------------------

DSH_VERSION = "0.1.1-rc.2"  # EXACTLY this; plugin compat: 0.1.0-rc.7 / 0.1.1-rc.2
DSH_PROFILE = "web"
DSH_PLUGIN_SPEC = "github:mrme000m/dsh-prime-orchestrator"
DSH_PLUGIN_PACKAGE = "dsh-prime-orchestrator"
PRIME_AGENT_INSTALLER = "https://app.primeintellect.ai/prime-agent/install.sh"

# Web-profile parity with the production Mac (~/.dsh/profiles/web): dsh-mnemon
# (memory), pi2dsh (Pi extension bridge), dsh-mobile (mobile access),
# @deepseek-ai/dsh-mcp-client + pi-agent-memory (plain deps), dsh-restart
# (vendored at plugins/dsh-restart — /restart reloads plugins without a
# manual stop). Versions pinned to the Mac's working set.
PARITY_PLUGINS = [
    {"package": "dsh-mnemon", "spec": "dsh-mnemon@0.3.2"},
    {"package": "pi2dsh", "spec": "pi2dsh@0.19.0"},
    {"package": "dsh-mobile", "spec": "dsh-mobile@0.2.0"},
    {"package": "@deepseek-ai/dsh-mcp-client", "spec": "@deepseek-ai/dsh-mcp-client@0.0.1-rc.1"},
    {"package": "pi-agent-memory", "spec": "pi-agent-memory@0.3.4"},
    {"package": "dsh-restart", "spec": "./plugins/dsh-restart"},
]

# Registry packages whose native build scripts pnpm must be allowed to run
# (dsh-mobile deps; verified set from the production Mac profile). Plain-name
# keys work for registry packages (unlike pnpm>=11 git-dep keys, which are
# parsed from the failure output at install time — see stages/plugin.py).
NATIVE_BUILD_KEYS = ["node-pty", "ssh2", "cloudflared", "cpu-features"]

# dsh-mobile gateway: loopback HTTP behind the codespace https forward.
MOBILE_GATEWAY_PORT = 3443

# --- fleet ------------------------------------------------------------------

FLEET_PRESETS = ["tv-scout", "tv-investigator", "qd-analyst", "wt-investigator"]
FLEET_MANAGED_BY = "prime-stack-bootstrap"
FLEET_PRESET_MARKER = ".plugin-managed.json"
FLEET_PRESET_FILES = ["preset.yml", "agent.cordis.yml"]
WT_MCP_URL = "https://wundertrading.com:2083/mcp"

# --- apt packages (python fallback stage; the playbook uses the apt module) -

APT_PACKAGES = ["python3-yaml", "curl", "jq", "util-linux"]

# --- Cloudflare Workers AI model catalog ------------------------------------
# Single source of truth, verified against the production Mac config.
# Surface-specific deltas below (NOT a second catalog).

MODEL_CATALOG = [
    {"id": "@cf/zai-org/glm-5.2", "name": "CF Workers AI / GLM-5.2",
     "contextWindow": 262144, "maxTokens": 16384, "reasoning": True},
    {"id": "@cf/zai-org/glm-5.3", "name": "CF Workers AI / GLM-5.3",
     "contextWindow": 1048576, "maxTokens": 131072, "reasoning": True},
    {"id": "@cf/zai-org/glm-5.3-flash", "name": "CF Workers AI / GLM-5.3 Flash",
     "contextWindow": 1048576, "maxTokens": 131072, "reasoning": True},
    {"id": "@cf/deepseek-ai/deepseek-v4-flash-0731", "name": "CF Workers AI / DeepSeek V4 Flash 0731",
     "contextWindow": 131072, "maxTokens": 16384},
    {"id": "@cf/deepseek-ai/deepseek-v4-pro-0813", "name": "CF Workers AI / DeepSeek V4 Pro 0813",
     "contextWindow": 131072, "maxTokens": 16384},
    {"id": "@cf/qwen/qwen3.8-27b", "name": "CF Workers AI / Qwen3.8 27B",
     "contextWindow": 262144, "maxTokens": 16384},
    {"id": "@cf/moonshotai/kimi-k2.6", "name": "CF Workers AI / Kimi K2.6",
     "contextWindow": 262144, "maxTokens": 16384},
    {"id": "@cf/moonshotai/kimi-k2.7-code", "name": "CF Workers AI / Kimi K2.7 Code",
     "contextWindow": 262144, "maxTokens": 16384, "reasoning": True},
]

# The dsh settings.yaml surface: the production Mac lists models in a
# different order, uses short display names and different maxTokens caps for
# the GLM-5.3 pair. Kept as explicit overrides so the deltas are visible.
DSH_MODEL_ORDER = [
    "@cf/zai-org/glm-5.2",
    "@cf/deepseek-ai/deepseek-v4-flash-0731",
    "@cf/deepseek-ai/deepseek-v4-pro-0813",
    "@cf/qwen/qwen3.8-27b",
    "@cf/moonshotai/kimi-k2.6",
    "@cf/moonshotai/kimi-k2.7-code",
    "@cf/zai-org/glm-5.3",
    "@cf/zai-org/glm-5.3-flash",
]
DSH_MODEL_OVERRIDES = {
    "@cf/zai-org/glm-5.3": {"name": "glm53", "maxTokens": 256000},
    "@cf/zai-org/glm-5.3-flash": {"name": "glm53flash", "maxTokens": 128000},
}

DEFAULT_MODEL_ID = "@cf/zai-org/glm-5.3"
DEFAULT_PROVIDER = "cloudflare-workers-ai"


def _model_by_id(model_id: str) -> dict:
    for m in MODEL_CATALOG:
        if m["id"] == model_id:
            return m
    raise KeyError(model_id)


def dsh_catalog() -> list:
    """The model list as rendered into ~/.dsh/settings.yaml."""
    out = []
    for model_id in DSH_MODEL_ORDER:
        m = dict(_model_by_id(model_id))
        m.pop("reasoning", None)
        m.update(DSH_MODEL_OVERRIDES.get(model_id, {}))
        out.append(m)
    return out


def prime_provider(cf_account_id: str) -> dict:
    """The provider fragment merged into ~/.prime/agent/models.json."""
    return {
        "type": "api_key",
        "baseUrl": f"https://api.cloudflare.com/client/v4/accounts/{cf_account_id}/ai/v1",
        "api": "openai-completions",
        "models": [dict(m) for m in MODEL_CATALOG],
    }


def enabled_model_ids() -> list:
    """Prefixed ids for prime-agent settings.json (prefix, not backreference —
    re.sub('^', 'prefix', id) semantics, escape-proof across layers)."""
    return [f"{DEFAULT_PROVIDER}/{m['id']}" for m in MODEL_CATALOG]


# --- runtime configuration ---------------------------------------------------

def _flag(env: str, cli_value) -> bool:
    if cli_value is not None:
        return bool(cli_value)
    return os.environ.get(env, "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    """Everything the stages need to know about the machine and the knobs."""

    workspace: Path
    home: Path
    dsh_home: Path
    strict: bool = False
    force_settings: bool = False
    dry_run: bool = False

    # fixed identities (overridable for tests)
    dsh_version: str = DSH_VERSION
    dsh_profile: str = DSH_PROFILE
    dsh_plugin_spec: str = DSH_PLUGIN_SPEC
    dsh_plugin_package: str = DSH_PLUGIN_PACKAGE
    parity_plugins: list = field(default_factory=lambda: [dict(p) for p in PARITY_PLUGINS])
    native_build_keys: list = field(default_factory=lambda: list(NATIVE_BUILD_KEYS))
    mobile_gateway_port: int = MOBILE_GATEWAY_PORT
    fleet_presets: list = field(default_factory=lambda: list(FLEET_PRESETS))
    fleet_managed_by: str = FLEET_MANAGED_BY
    fleet_preset_marker: str = FLEET_PRESET_MARKER
    fleet_enable_tvcli_autoserve: bool = True

    # ---- derived paths ----------------------------------------------------

    @property
    def dsh_profile_dir(self) -> Path:
        return self.dsh_home / "profiles" / self.dsh_profile

    @property
    def dsh_settings(self) -> Path:
        return self.dsh_home / "settings.yaml"

    @property
    def preset_root(self) -> Path:
        return self.dsh_home / ".agent-presets"

    @property
    def preset_dir(self) -> Path:
        return self.preset_root / "prime-orchestrator"

    @property
    def web_profile_patch(self) -> Path:
        return self.dsh_profile_dir / "cordis.patch.yml"

    @property
    def profile_package_json(self) -> Path:
        return self.dsh_profile_dir / "package.json"

    @property
    def plugin_built_artifact(self) -> Path:
        return (self.dsh_profile_dir / "node_modules" / self.dsh_plugin_package
                / "lib" / "index.js")

    @property
    def pnpm_workspace(self) -> Path:
        return self.dsh_profile_dir / "pnpm-workspace.yaml"

    @property
    def prime_agent_bin(self) -> Path:
        return self.home / ".local" / "bin" / "prime-agent"

    @property
    def prime_agent_dir(self) -> Path:
        return self.home / ".prime" / "agent"

    @property
    def wt_runtime_env(self) -> Path:
        return self.workspace / "browser-debug" / "secrets" / "runtime" / "wt.env"

    @property
    def wt_cloak_dir(self) -> Path:
        return self.workspace / "browser-debug"

    @property
    def qd_runtime_env(self) -> Path:
        return self.workspace / "browser-debug" / "secrets" / "runtime" / "qd-agent.env"

    @property
    def fleet_preset_src(self) -> Path:
        return self.workspace / "bootstrapping" / "presets"

    # ---- secrets (presence only; values never leave the process) ----------

    @staticmethod
    def env(name: str) -> str:
        return os.environ.get(name, "").strip()

    @property
    def cf_account_id(self) -> str:
        return self.env("CLOUDFLARE_ACCOUNT_ID")

    @property
    def cf_api_key(self) -> str:
        return self.env("CLOUDFLARE_API_KEY")

    @property
    def cf_secrets_present(self) -> bool:
        return bool(self.cf_account_id) and bool(self.cf_api_key)

    # ---- construction ------------------------------------------------------

    @classmethod
    def from_env(cls, args=None, **overrides) -> "Config":
        args = args if args is not None else type("A", (), {})()
        workspace = getattr(args, "workspace", None) or os.environ.get("TV_WORKSPACE") or os.getcwd()
        home = Path(os.environ.get("HOME") or "/root")
        dsh_home = Path(os.environ.get("DSH_HOME") or home / ".dsh")
        cfg = cls(
            workspace=Path(workspace).resolve(),
            home=home,
            dsh_home=dsh_home,
            strict=_flag("PRIME_STACK_STRICT", getattr(args, "strict", None)),
            force_settings=_flag("PRIME_STACK_FORCE_SETTINGS", getattr(args, "force_settings", None)),
            dry_run=_flag("PRIME_STACK_DRY_RUN", getattr(args, "dry_run", None)),
        )
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg
