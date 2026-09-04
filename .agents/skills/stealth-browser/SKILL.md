---
name: stealth-browser
description: Stealth browser automation for MCP-compatible agents — bypass Cloudflare/anti-bot with real Chrome via nodriver/CDP + FastMCP. 97 tools across 11 sections for navigation, element interaction, element cloning, network debugging, CDP execution, dynamic hooks, tabs and cookies. Use when driving a headful stealth browser, inspecting network traffic, cloning UIs pixel-perfect, or automating sites that block Playwright.
license: MIT
compatibility: Requires Chrome/Chromium/Edge, Python 3.10+, and the stealth-browser-mcp MCP server (bootstrapped by bootstrapping/python/prime_stack/stages/stealth_browser.py)
metadata:
  author: tvcli-workspace
  version: "1.0"
---

# stealth-browser — stealth browser automation (anti-bot, CDP, network hooks)

The workspace's stealth capability is [`vibheksoni/stealth-browser-mcp`](https://github.com/vibheksoni/stealth-browser-mcp) running as a **stdio MCP** exposed to **dsh** and **prime agents** via the `@deepseek-ai/dsh-mcp-client` row `mcp-stealth-browser` in `~/.dsh/profiles/web/cordis.patch.yml`. The install is bootstrapped by the `stealth-browser` stage and verified below. Agents speak to 97 tools, not to the browser directly.

## Install & configure (what the bootstrap does)

```bash
# standalone (no ansible)
bootstrapping/python/bin/prime-stack stealth-browser
# or full stack
bootstrapping/python/bin/prime-stack --dry-run all   # preview
ansible-playbook bootstrapping/ansible/prime-stack.yml -i localhost, -e ansible_connection=local -e ansible_python_interpreter=/usr/bin/python3 -e tv_workspace="$PWD" --tags stealth-browser
```

What the stage does (idempotent):
1. `git clone --depth 1 https://github.com/vibheksoni/stealth-browser-mcp.git tools/stealth-browser-mcp` (or fast-forward pull on re-run)
2. `python3 -m venv tools/stealth-browser-mcp/venv && venv/bin/pip install -r requirements.txt` (marker `.stealth-deps-installed` avoids reinstalls)
3. sanity check `venv/bin/python src/server.py --help` / `--list-sections`
4. upsert `mcp-stealth-browser` stdio row into `~/.dsh/profiles/web/cordis.patch.yml` (remove-then-append, 0600, placeholder `[]` stripped):

```yaml
- id: mcp-stealth-browser
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: stealth-browser
    transport: stdio
    command: /workspaces/tvcli/tools/stealth-browser-mcp/venv/bin/python
    args: [/workspaces/tvcli/tools/stealth-browser-mcp/src/server.py]
    cwd: /workspaces/tvcli/tools/stealth-browser-mcp/src
    toolCallTimeoutMs: 120000
    failOnStartupError: false
```

Re-running changes nothing; `git rev-parse HEAD` drift triggers a `reset --hard origin/master|main` + pip reinstall when the marker mismatches.

Dependencies: `git`, `python3-venv`, `Chrome/Chromium/Edge` (auto-detected, `validate_browser_environment_tool` diagnoses), Node `dsh` with `dsh-mcp-client` already installed by the `extras-plugins` stage.

## Verify (dsh + prime agents)

```bash
# checkout + venv + deps + MCP row
bootstrapping/python/bin/prime-stack stealth-browser

# binary sanity (no MCP)
tools/stealth-browser-mcp/venv/bin/python tools/stealth-browser-mcp/src/server.py --help
tools/stealth-browser-mcp/venv/bin/python tools/stealth-browser-mcp/src/server.py --list-sections
tools/stealth-browser-mcp/venv/bin/python tools/stealth-browser-mcp/src/server.py --debug --minimal --help

# dsh wiring
grep -A6 mcp-stealth-browser ~/.dsh/profiles/web/cordis.patch.yml
dsh --version  # 0.1.1-rc.2 — profile rebuild needed after first wire: dsh plugin --profile web rebuild

# prime-agent host still uses Cloudflare Workers AI — stealth-browser is a dsh tool surface
prime-agent --version
```

In the **dsh Web GUI** (port 3081): open a `prime-orchestrator` session, list tools — `spawn_browser`, `navigate`, `take_screenshot`, etc. appear under the `stealth-browser` server. Prime sub-agents spawned via `prime-orchestrator` inherit the same MCP surface because they run inside the dsh web profile.

**Expected stage envelope:**
`{"stage":"stealth-browser","changed":true|false,"failed":false,"details":{"checkout":"cloned|present","venv":"created|present","deps":"installed|present","mcp":"written|current"}}`

## Use the MCP (correct tool order)

The skill is intentionally **MCP-tool-order opinionated** — LLMs that skip state checks or forget cleanup fail silently on hard sites.

1. `spawn_browser` → keep `instance_id`. Optional `idle_timeout_seconds`, `proxy`, `headless` args.
2. `navigate(url)` → then **verify** with `get_instance_state` / `get_page_content` / `take_screenshot`.
3. **Inspect before acting**: `query_elements`, `get_page_content`, screenshots for stable selectors. Prefer role/visible-text over generated classes.
4. **Act narrow**: `click_element`, `type_text` (human keystrokes) vs `paste_text` (CDP instant), `scroll_page`, `wait_for_element`, `file_upload` (paths must be inside `BROWSER_FILE_UPLOAD_ALLOWED_DIRS`), `select_option`.
5. **Verify after**: re-query DOM / URL / title / screenshot. Treat every return (ids, selectors) as stateful.
6. `close_instance` when done (unless the user wants the session left open). Server reaps idle browsers (`BROWSER_IDLE_TIMEOUT`, default 600s); `spawn_browser(idle_timeout_seconds=0)` disables for one instance.

Pre-document spoofing: `add_script_to_evaluate_on_new_document` **before** `navigate` for WebGL/`navigator` patches that must run during the target's first inline scripts (`world_name=None`, `run_immediately=True` to also patch the current document).

Network debugging (after navigation): `list_network_requests` → `search_network_requests` → `get_request_details`/`get_response_details`/`get_response_content` (+ `modify_headers`, `set_network_capture_filters`).

Element cloning (pixel-perfect recreation): `clone_element_complete` / `extract_element_*` / `clone_element_progressive` → `expand_*` / file-backed `*_to_file` variants for large payloads.

Dynamic hooks: `create_dynamic_hook` / `create_simple_dynamic_hook` with restricted Python that can intercept/block/redirect/modify flows → `list_dynamic_hooks`/`get_dynamic_hook_details`/`validate_hook_function`.

## Modular surface (97 tools, 11 sections)

| Section | Tools | When |
|---|---|---|
| `browser-management` | 8 | spawn/list/close/navigate/back/forward/reload/state |
| `element-interaction` | 12 | query/click/type/paste/upload/select/state/wait/scroll/script/content/screenshot |
| `element-extraction` | 9 | styles/structure/events/animations/assets + CDP variants + complete clones |
| `file-extraction` | 9 | file-backed clones (`*_to_file`, `list_clone_files`, `cleanup_clone_files`) |
| `network-debugging` | 10 | capture/filter/search/export + header mutation |
| `cdp-functions` | 14 | `execute_cdp_command`, `list_cdp_commands`, JS discovery/binding, `execute_python_in_browser` |
| `progressive-cloning` | 10 | `clone_element_progressive` + `expand_*` + storage ops |
| `cookies-storage` | 3 | `get_cookies`/`set_cookie`/`clear_cookies` |
| `tabs` | 5 | `list_tabs`/`new_tab`/`switch_tab`/`close_tab`/`get_active_tab` |
| `debugging` | 7 | `get_debug_view`/`export_debug_logs`/`hot_reload`/`validate_browser_environment_tool` |
| `dynamic-hooks` | 10 | `create_dynamic_hook` + presets + docs/validation |

Trim with server flags: `--minimal` (20 core tools), `--xpool-safe` (83, disables `cdp-functions` section that triggers `Runtime.enable`), `--disable-<section>` or `--list-sections` / `--debug` (stderr verbose; never stdout — it corrupts stdio JSON-RPC).

## Environment

| Var | Default | Meaning |
|---|---|---|
| `STEALTH_BROWSER_MCP_AUTH_TOKEN` / `MCP_AUTH_TOKEN` | unset | Bearer for HTTP transport only; stdio needs no token |
| `BROWSER_IDLE_TIMEOUT` | 600 | Seconds before idle browser reaped; 0 = manual |
| `BROWSER_IDLE_REAPER_INTERVAL` | 60 | Reaper tick |
| `BROWSER_ORPHAN_PROFILE_MAX_AGE` | 21600 | Startup sweep for stale `uc_*` temp profiles |
| `BROWSER_FILE_UPLOAD_ALLOWED_DIRS` | repo root | `:`-separated allowlist for `file_upload` |
| `STEALTH_BROWSER_DEBUG` / `DEBUG` | 0 | Verbose to stderr; 1 = on |
| `XPOOL_SAFE_MODE` | 0 | Disables `cdp-functions` at startup |
| `PORT` | 8000 | HTTP `--port` default |

HTTP mode (`--transport http --host 127.0.0.1 --port 8000`) requires `STEALTH_BROWSER_MCP_AUTH_TOKEN` if exposed beyond loopback — the default stdio row does not.

## Gotchas (verified)

- **stdio stdout must stay clean** — debug logs go to stderr; keep `STEALTH_BROWSER_DEBUG=0` in normal MCP runs or tools hang with malformed JSON.
- **Chrome on Linux/CI** — root/container auto-adjusts sandbox args; use `--sandbox=false` only if required, and ensure `validate_browser_environment_tool` passes.
- **Orphan `uc_*` profiles** — server sweeps on startup + idle reap; set `BROWSER_IDLE_TIMEOUT=0` only for fully manual lifetimes.
- **Tool bloat** — prefer `--minimal` or selective `--disable-*` when 97 tools clutter the chat; `--list-sections` shows the current map.
- **Playbook tags** — `stealth-browser` is its own tag; it runs after `extras-plugins` (needs `dsh-mcp-client`) and independently of `secrets`/`fleet`.
