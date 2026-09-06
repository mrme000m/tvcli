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
bin/cf.sh tunnel-token TUNNEL_ID             # connector token for
                                              # `cloudflared tunnel run --token …`

# one-step expose: ensure tunnel + ingress + CNAME hostname → tunnel
bin/cf.sh expose --tunnel codespace-web \
  --hostname app.00m.indevs.in --service http://localhost:3081

# then run the connector in the container (token or config mode)
bin/cf.sh cloudflared-ensure                 # installs cloudflared if missing
cloudflared tunnel run codespace-web
```

## Worked example — grid-autonomy on the az00 VPS

Tunnel `grid-autonomy` publishes ALL four services through one connector
running next to the stack on the `grid-net` docker network (ingress targets
`http://grid-autonomy:PORT` — docker DNS, no host port publishing needed):

```bash
bin/cf.sh tunnel-create grid-autonomy
bin/cf.sh tunnel-token <TUNNEL_ID>            # → connector token
bin/cf.sh tunnel-config-put <TUNNEL_ID> --ingress \
  'hostname=grid.00m.indevs.in,service=http://grid-autonomy:8798;hostname=grid-ctl.00m.indevs.in,service=http://grid-autonomy:8799;hostname=grid-pb.00m.indevs.in,service=http://grid-autonomy:8090;hostname=grid-api.00m.indevs.in,service=http://grid-autonomy:8765'
for h in grid grid-ctl grid-pb grid-api; do
  bin/cf.sh dns-route <ZONE_ID> "$h.00m.indevs.in" <TUNNEL_ID>
done
# connector (kept ensured by docker/vps-run.sh from GRID_TUNNEL_TOKEN):
#   docker run -d --name grid-cloudflared --network grid-net \
#     cloudflare/cloudflared:latest tunnel --no-autoupdate run --token <TOKEN>
```

Hostnames: `grid` = mission console :8798, `grid-ctl` = daemon ctl API
:8799, `grid-pb` = PocketBase :8090, `grid-api` = tvcli serve :8765.

## Where the tokens come from

- **Mac / codespace:** vault fallback (`BW_SESSION` from
  `browser-debug/secrets/bw-provision.sh`) or the env vars above — dsh agent
  presets read repo skills from `.agents/skills/` and either path works.
- **grid-autonomy docker image:** `docker/vault_loader.sh` exports
  `CF_ACCOUNT_ID` + `CF_API_TOKEN_READ` / `CF_API_TOKEN_WRITE` from the
  vault item at every boot into `/data/secrets/grid-vault.env` (sourced by
  the entrypoint AND by interactive shells via the boot-time .bashrc hook),
  so any agent or shell inside that container runs this skill with pure env
  auth — in a `docker exec` script, run `. /data/secrets/grid-vault.env`
  first. The skill is baked into the image at `/app/.agents/skills/cf/`.

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
