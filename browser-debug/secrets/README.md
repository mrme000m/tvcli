# Secrets — bw CLI + Bitwarden vault

All devcontainer secrets live in the Bitwarden vault (self-hosted:
`keys.00m.indevs.in`). [`bw-provision.sh`](bw-provision.sh) materializes them
as **gitignored runtime files**; values are never echoed, logged, or inlined
into shell profiles — profiles only gain guarded `source` lines pointing at
`600`-mode files.

Naming rules for every item/field live in
[`VAULT_CONVENTIONS.md`](VAULT_CONVENTIONS.md) — read it before adding
anything. Item names are `<domain>-<purpose>` (never a raw ID); payloads are
either **notes-type** (provisioned below) or **fields-type** (read live by
skills, never provisioned).

## Vault schema

Provisioned (notes-type — declarative in [`manifest.json`](manifest.json)):

| Item (Secure Note) | Notes content | Runtime target |
|---|---|---|
| `tvcli-primary-env` | `SESSION=` `SIGNATURE=` `DEVICE_T=` `TV_USER=` `TV_TIER=` lines | `.env` |
| `tvcli-accounts-pool` | full `accounts.json` (multi-account WS registry, attachment) | `accounts.json` |
| `browser-debug-env` | TV cookies + `MISTRAL_API_KEY` (canonical browser-debug env) | `browser-debug/.env` |
| `opencode-cloudflare` | `CLOUDFLARE_ACCOUNT_ID=` `CLOUDFLARE_API_KEY=` | `secrets/runtime/opencode.env` |
| `tv-proxy` | SOCKS relay creds (`TV_PROXY=…` etc.; optional — absent from the vault, skips with a warning) | `secrets/runtime/tv-proxy.env` |
| `wundertrading-api` | `WT_API_KEY=` `WT_API_SECRET=` | `secrets/runtime/wt.env` |
| `wundertrading-session` | `WT_PHPSESSID=` `WT_CF_CLEARANCE=` `WT_COOKIES_JSON=` | `secrets/runtime/wt-session.env` |
| `kimi-session` | `KIMI_ACCESS_TOKEN=` `KIMI_REFRESH_TOKEN=` `KIMI_USER_ID=` `KIMI_DEVICE_ID=` `KIMI_SSID=` (Google-OAuth web session) | `secrets/runtime/kimi-session.env` |

Live-read (fields-type — consumed at runtime, NOT in `manifest.json`):

| Item | Folder | Fields | Consumer |
|---|---|---|---|
| `cloudflare-tunnels` | `cloudflare` | `account-id`, `read-all`, `write-all` (+ opaque `*-storage`) | `cf` skill |
| `provider-keys` | — | `NVIDIA_*`, `OPENROUTER_*`, `KIMI_CODE_API_KEY`, `CLINEPASS_*`, `OPENCODE_GO_*` | `provider-limits.py` (read) · `kimi-keys.py --update-vault` (write `KIMI_CODE_API_KEY`) |
| `kimi` | — | **login** (`vb.mrme00@gmail.com` + password + TOTP) | manual Google OAuth login; not provisioned |
| `wundertrading-login` | — | notes (manual/headful login) | docs only (`wt-investigator` preset) |

The mapping is declarative in [`manifest.json`](manifest.json) — **adding a
secret = add a vault item + one manifest entry** (format `env`/`json`/`raw`,
optional `jsonValidate` jq count expression, `sourceInto` rc-files). No code
changes.

## One-time setup (codespace / devcontainer)

1. Create a Bitwarden API key for your user (web vault → Settings → API Key).
2. Publish the credentials as codespace secrets for the repo — either in the
   GitHub UI (repo → Settings → Codespaces → Secrets) or with one command:
   ```bash
   BW_CLIENTID=… BW_CLIENTSECRET=… BW_PASSWORD=… \
     bash browser-debug/secrets/set-codespace-secrets.sh   # needs pynacl + gh
   # verify the API path without real values:
   bash browser-debug/secrets/set-codespace-secrets.sh --test
   ```
3. Populate the vault items once, from a machine that already has working
   secrets: `bash browser-debug/secrets/migrate-local.sh` (see below).
4. `post-create.sh` runs `bw-provision.sh` automatically on every rebuild
   (configures `bw config server`, `bw login --apikey`, `bw unlock`,
   `bw sync`, fetches all manifest items).

If the credentials are absent the provisioner exits `2` with a warning and the
container still builds — no secrets, no live analysis, nothing else breaks.

## Daily use

```bash
# fetch/rotate all secrets into runtime files (idempotent)
browser-debug/secrets/bw-provision.sh

# check what would happen without touching anything
browser-debug/secrets/bw-provision.sh --dry-run

# initial migration / rotation FROM the local side: push a file into a vault item
browser-debug/secrets/bw-provision.sh --export tvcli-accounts-pool accounts.json
browser-debug/secrets/bw-provision.sh --export tvcli-primary-env .env
```

`--export` create-or-updates the Secure Note (notes = file content) and never
prints the content. After rotating in the vault, re-run plain provisioning on
every machine/container.

## Provider tools

- **`provider-limits.py`** — read-only subscription/limits report for every
  `provider-keys` field (nvidia / openrouter / kimi / clinepass / opencode_go).
  `python3 browser-debug/secrets/provider-limits.py [--provider NAME]`.
- **`kimi-keys.py`** — programmatic Kimi Code API-key management (list /
  create / delete / reset / get-config / refresh). Auths via the `kimi-session`
  web tokens (refreshing the access token when near expiry) and drives the
  console's `kimi.gateway.credentials.v1.APIKeyService` ConnectRPC surface.

  ```bash
  python3 browser-debug/secrets/kimi-keys.py list
  python3 browser-debug/secrets/kimi-keys.py create --name tvcli-agent --update-vault   # writes KIMI_CODE_API_KEY
  python3 browser-debug/secrets/kimi-keys.py delete --name tvcli-agent
  python3 browser-debug/secrets/kimi-keys.py get-config                                 # key-limit (5) / can-create
  python3 browser-debug/secrets/kimi-keys.py refresh --persist                          # renew + write tokens back to kimi-session
  ```

  `create`/`reset` print the full key exactly once (that is the only moment it
  is retrievable); with `--update-vault` the value goes straight into the vault
  and only redacted metadata is printed. Key values are never logged otherwise.

## Safety properties

- Every target path is gitignored (`browser-debug/.env`, `.env`,
  `accounts.json`, `secrets/runtime/`); `manifest.json` itself is the only
  tracked file here and contains **no secret values** — only names + paths.
- `umask 077`, targets `chmod 600`, fetched content only ever flows
  `bw → variable → file`.
- `jsonValidate` refuses to install a broken/empty account pool (e.g.
  `.accounts | length` must be > 0).
- Shell rc files only ever receive `[ -f <path> ] && . <path>` lines — the
  old pattern of `printf`ing credential VALUES into `~/.profile`/`~/.bashrc`
  is gone.
