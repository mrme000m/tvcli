---
name: cf
description: Manage Cloudflare Zero Trust tunnels + connectors from this container using the vault-backed account (item cloudflare-tunnels in folder cloudflare; fields account-id/read-all/write-all per VAULT_CONVENTIONS.md) and expose local ports on the indevs domain. Use when asked to publish a container port publicly, check/create/delete tunnels, inspect connectors, set tunnel ingress, or route DNS for *.indevs.in.
---

# cf — Cloudflare tunnels from the container

Publishes container ports (dsh web :3081, tvcli :8765, anything else) on the
indevs domain via remotely-managed `cloudflared` tunnels. All API calls go to
`https://api.cloudflare.com/client/v4` with `Bearer` tokens.

## Auth (never printed, never committed)

Resolution order in `scripts/cf.py`:

1. Env: `CF_ACCOUNT_ID` + `CF_API_TOKEN_READ` / `CF_API_TOKEN_WRITE`
   (`CF_API_TOKEN` works as fallback for both).
2. Bitwarden fallback — folder `cloudflare`, item
   `cloudflare-tunnels` (convention: `browser-debug/secrets/VAULT_CONVENTIONS.md`).
   Field `account-id` = account ID, `read-all` = read-only token (GET),
   `write-all` = write token (create/update/delete/DNS). The `*-storage`
   fields are opaque/reserved and ignored. Unlock uses `BW_SESSION` if set,
   else `BW_PASSWORD` (+ `BW_EMAIL` or `BW_CLIENTID`/`BW_CLIENTSECRET`) via
   `bw unlock --passwordenv`.

```bash
bin/cf.sh auth-status        # redacted only: id prefix, token lengths, source
```

## Quick flows

```bash
bin/cf.sh zones                              # find the indevs zone
bin/cf.sh tunnel-list                        # existing tunnels
bin/cf.sh connectors [TUNNEL_ID]             # connector health (all or one)

# one-step expose: ensure tunnel + ingress + CNAME hostname → tunnel
bin/cf.sh expose --tunnel codespace-web \
  --hostname app.00m.indevs.in --service http://localhost:3081

# then run the connector in the container (token or config mode)
bin/cf.sh cloudflared-ensure                 # installs cloudflared if missing
cloudflared tunnel run codespace-web
```

Granular commands: `tunnel-get/get/create/delete`, `tunnel-config-get/put`,
`dns-route ZONE_ID HOSTNAME TUNNEL_ID`. Read commands use the read token,
mutating commands the write token (`--token read|write` overrides).
Curl equivalents: [references/api.md](references/api.md).

## Gotchas

- DNS target is always `<tunnel-id>.cfargotunnel.com` (CNAME, proxied, ttl 1).
- Ingress must end with a catch-all (`{"service":"http_status:404"}`) —
  `expose` and `tunnel-config-put` append it automatically.
- `tunnel-delete` without `--cascade` refuses while connections/config exist.
- The `opencode-cloudflare` vault item is a different credential (Workers AI,
  with a `$`-pointer value) — it is NOT used here.
