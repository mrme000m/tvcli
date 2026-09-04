"""Stage registry — CLI names map to stage entry points.

Groups (`all`, `extras`, `fleet`) expand to their member stages in
deployment order. The order matches the original playbook's task order
(secrets runs BEFORE fleet so a standalone full run reads a freshly
provisioned wt.env — the post-create path provisions it even earlier in
bw-provision.sh).
"""

from . import (agent, dsh, dsh_config, env_bridge, extras, fleet, packages,
               plugin, preset, prime_config, secrets, stealth_browser)

STAGES = {
    "packages": packages.run,
    "dsh": dsh.run,
    "plugin": plugin.run,
    "agent": agent.run,
    "preset": preset.run,
    "env": env_bridge.run,
    "dsh-config": dsh_config.run,
    "prime-config": prime_config.run,
    "extras-plugins": extras.run_plugins,
    "extras-mnemon": extras.run_mnemon,
    "extras-mobile": extras.run_mobile,
    "extras-mcp": extras.run_mcp,
    "secrets": secrets.run,
    "fleet-presets": fleet.run_presets,
    "fleet-patch": fleet.run_patch,
    "fleet-autoserve": fleet.run_autoserve,
    "stealth-browser": stealth_browser.run,
}

GROUPS = {
    "extras": ["extras-plugins", "extras-mnemon", "extras-mobile", "extras-mcp"],
    "fleet": ["fleet-presets", "fleet-patch", "fleet-autoserve"],
}

GROUPS["all"] = [
    "packages",
    "dsh",
    "plugin",
    "agent",
    "preset",
    "env",
    "dsh-config",
    "prime-config",
    *GROUPS["extras"],
    "secrets",
    *GROUPS["fleet"],
    "stealth-browser",
]
