# Bitwarden vault naming conventions

All devcontainer/agent secrets live in the self-hosted vault
(`https://keys.00m.indevs.in`). `bw-provision.sh` + `manifest.json` move them
into gitignored runtime files; skills that need live credentials (see
`cf`) read the vault at runtime. Every new vault entry MUST follow this
file — it keeps item names greppable and every consumer in lockstep.

## 1. Folders — one lowercase token per service domain

- `cloudflare` (tunnel/API credentials). Renamed from `cloud flare` (spaces
  break shell recipes and `jq` filters — never use them).
- Legacy flat items (`tvcli-*`, `wundertrading-*`, …) stay in No Folder until
  their skill adopts folders. New service domains get a folder; never create
  multi-word folder names.

## 2. Item names — `<domain>-<purpose>`, lowercase kebab

Never a raw ID, token, or username. The name must be searchable (`bw list
items --search <domain>` returns the whole family).

| Item | Shape | Consumer |
|---|---|---|
| `tvcli-primary-env` | notes (`KEY=VALUE`) | `manifest.json` → `.env` |
| `tvcli-accounts-pool` | attachment `accounts.json` | `manifest.json` → `accounts.json` |
| `browser-debug-env` | notes (`KEY=VALUE`) | `manifest.json` → `browser-debug/.env` |
| `opencode-cloudflare` | notes (`KEY=VALUE`) | `manifest.json` → `secrets/runtime/opencode.env` |
| `tv-proxy` | notes (`KEY=VALUE`, optional — missing from vault skips with a warning) | `manifest.json` → `secrets/runtime/tv-proxy.env` |
| `wundertrading-api` | notes (`KEY=VALUE`) | `manifest.json` → `secrets/runtime/wt.env` |
| `wundertrading-session` | notes (`KEY=VALUE`) | `manifest.json` → `secrets/runtime/wt-session.env` |
| `kimi` | **login** (username/password/TOTP) | manual Google OAuth login (`vb.mrme00@gmail.com`); not provisioned |
| `kimi-session` | notes (`KEY=VALUE`) | `manifest.json` → `secrets/runtime/kimi-session.env` |
| `opencode-session` | notes (`KEY=VALUE`) | `manifest.json` → `secrets/runtime/opencode-session.env` |
| `cloudflare-tunnels` | **fields** (live-read, §3) | `cf` skill (`scripts/cf.py`) |
| `provider-keys` | **fields** (live-read, §3) | `provider-limits.py` (read) · `kimi-keys.py --update-vault` (write `KIMI_CODE_API_KEY`; do not provision) |
| `wundertrading-login` | notes (manual/headful login, not provisioned) | docs only (`wt-investigator` preset) |

Rename history (old → new, keep this log so stale references are traceable):

- `cloud flare/` → `cloudflare/` (folder, space removed)
- `72d6e3279eb70c619d8a0ea4b908475f` → `cloudflare-tunnels` (was the raw
  Cloudflare account ID; the ID now lives in the `account-id` field)
- field `ready-all-storage` → `read-all-storage` (typo)

## 3. Payload shapes — notes-type vs fields-type

- **notes-type** (provisioned): notes hold `KEY=VALUE` env lines (`env`),
  a JSON document (`json`), or raw text (`raw`); large blobs ride as an
  attachment. Adding a secret = vault item + one `manifest.json` entry —
  no code changes. The provisioner only reads notes/attachments.
- **fields-type** (live-read): custom fields, kebab-case names, opaque
  companions end in `-storage`. Read live with `bw get item <name>`
  (the `cf` skill's `resolve_token()` is the reference implementation:
  env-first, vault-fallback, values never logged). Never add fields-type
  items to `manifest.json` — the provisioner cannot read fields.

## 4. Env binding — UPPER_SNAKE, env-first

Runtime env names are UPPER_SNAKE and mirror the payload keys
(`CLOUDFLARE_ACCOUNT_ID`, `WT_API_KEY`, `SESSION`, …). Scripts resolve
env-first, vault-fallback, and only ever log redacted metadata
(id prefix, value length, source) — see `cf … auth-status`.

## 5. Forbidden

- Secret values in the repo, `manifest.json`, docs, or shell rc files
  (rc files get guarded `source` lines only).
- `$`-references as values (`opencode-cloudflare`'s `CLOUDFLARE_API_KEY`
  is `$CLOUDFLARE_API_TOKEN` — a pointer, not a token; fatal under `set -u`).
- New vault items without updating this file's table.
