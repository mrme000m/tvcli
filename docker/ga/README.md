# grid-ga — the standalone GA-agent container

The GA agent (the `ga` dsh preset — DeepSeek Harness + prime-agent, served
over `dsh web`) runs in its **own container**, split out of the
grid-autonomy trading container:

```
                 ┌────────────────────────────────┐
  dsh.00m.indevs.in  (CF tunnel → grid-net)      │
   └─ http://grid-ga:3081 ──────────────┐        │
                                        ▼        │
   ┌─────────────────────┐        ┌──────────────────────────┐
   │ grid-ga (this)      │ docker │ grid-autonomy (trading)  │
   │ ├ dsh web  :3081    │  DNS   │ ├ console :8798          │
   │ ├ prime-agent CLI   │───────▶│ ├ ctl     :8799          │
   │ └ vault_loader (cf) │        │ ├ PocketBase :8090       │
   │ /srv/tvcli = git    │        │ └ daemon + browser       │
   └─────────────────────┘        └──────────────────────────┘
        host publish 127.0.0.1:3082:3081
```

**Git is the hand-off between the two containers.** The GA agent edits and
tests grid-autonomy code in `/srv/tvcli` (a persistent git clone of this
repo, host-mounted) and pushes to `main`; the grid-autonomy deploy workflow
picks the push up and redeploys the trading container. In the other
direction, nothing the trading stack does touches grid-ga:

- **grid deploys never touch grid-ga** — the grid-autonomy workflow
  (`grid-autonomy-deploy.yml`) builds/pulls only the grid-autonomy image and
  restarts only the `grid-autonomy` container. Different image
  (`ghcr.io/mrme000m/tvcli/grid-autonomy` vs `.../grid-ga`), different
  concurrency group, different paths filters. A trading redeploy cannot kill
  the agent's web sessions.
- **grid-ga rebuilds happen ONLY on `docker/ga/**` changes** — the GA
  workflow (`ga-deploy.yml`) triggers on `docker/ga/**` and
  `.github/workflows/ga-deploy.yml`. A daemon/skill/Go change redeploys the
  trading container but never rebuilds the agent's image (the agent picks the
  new code up via `git pull` in `/srv/tvcli` at its next boot, or on demand).
- the one shared piece of state is the **grid-dsh volume**: grid-ga mounts it
  at `/data/dsh` (the dsh home — presets, settings, web profile, sessions).
  It holds the GA home seeded by the original in-grid deployment; the
  grid-ga entrypoint's revision-refresh logic adopts it (first boot with a
  new `.baked-rev` refreshes baked preset files per-file, preserving
  operator-edited ones).

## The /srv/tvcli workbench + the push guardrail

`/opt/tvcli` on the host (bind-mounted `/srv/tvcli` in the container) is a
persistent git clone of **github.com/mrme000m/tvcli** (public repo — anonymous
read). `ga-run.sh` creates it as the invoking user on first deploy; the
container entrypoint keeps it current (`git pull --ff-only`, warn on
divergence — never reset) and, when `GH_TOKEN` is present (bridged from the
`GH_PUSH_TOKEN` repo secret), runs `gh auth setup-git` so pushes work.

**Guardrail: a push to `main` is a production auto-deploy of the
grid-autonomy container.** The GA persona hard-codes it: *never push to main
without explicit human confirmation in the web UI*. Pushes auto-deploy
**grid-autonomy only** — grid-ga itself rebuilds exclusively on
`docker/ga/**` / `.github/workflows/ga-deploy.yml` changes, so the agent
cannot take itself (or its own image) down with a code push.

## Ports / volumes / network

| What | Value | Notes |
|------|-------|-------|
| Container name | `grid-ga` | image `ghcr.io/mrme000m/tvcli/grid-ga` |
| dsh web (in-container) | `0.0.0.0:3081` | EXPOSE 3081; HEALTHCHECK curls it |
| Host publish | `127.0.0.1:3082:3081` | az00's caddy owns :3081; reach via `ssh -L 3082:localhost:3082 <host>` |
| Public path | `dsh.00m.indevs.in` | CF tunnel (grid-net connector) → `http://grid-ga:3081`; ingress reconciled by `ga-deploy.yml` |
| Network | `grid-net` | shared with grid-autonomy + cloudflared; GA reaches `http://grid-autonomy:8798` (console), `:8799` (ctl), `:8090` (PocketBase) via docker DNS |
| Volume `grid-dsh` | `/data/dsh` | **reused from the grid deployment** — the seeded GA home (`/opt/dsh-home` seed + revision refresh) |
| Volume `grid-ga-secrets` | `/data/secrets` | vault-resolved env (`grid-vault.env`) |
| Volume `grid-ga-bwcli` | `/data/bw-cli` | bw CLI login state |
| Host bind `/opt/tvcli` | `/srv/tvcli` (rw) | the git workbench |
| Env file | `/opt/grid-ga/.env` | `BW_*` + `GH_PUSH_TOKEN` (written by the workflow) |

## Local-run quick start

```sh
# 1. build (context = repo root; the image carries only docker/ga payloads)
docker build -t grid-ga:local -f docker/ga/Dockerfile .

# 2. env (BW_* are OPTIONAL — the vault load fails soft without them)
cp docker/ga/env.example ga.env && chmod 600 ga.env && $EDITOR ga.env

# 3. run (creates the volumes/network/workbench, replaces an old container)
IMAGE=grid-ga:local ENV_FILE="$PWD/ga.env" bash docker/ga/ga-run.sh
#    → http://127.0.0.1:3082  (dsh web, the GA agent)

# 4. follow the boot: vault → dsh-home seed → settings render → workbench pull
docker logs -f grid-ga
```

On the VPS the same `ga-run.sh` is invoked over SSH by
`.github/workflows/ga-deploy.yml` with the SHA-tagged GHCR image.

## Secrets

The container loads **only Cloudflare items** from the vault
(`BW_VAULT_ONLY=cf` — no trading secrets in this image):

| Secret | Where | Used for |
|--------|-------|---------|
| `BW_URL` / `BW_CLIENTID` / `BW_CLIENTSECRET` / `BW_PASSWORD` | repo secrets → `/opt/grid-ga/.env` | vault_loader machine-auth (same Vaultwarden + creds as the grid deployment) |
| vault item `opencode-cloudflare` | vault | `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_KEY` → the dsh/prime-agent Cloudflare Workers AI provider (bridged to `CF_ACCOUNT_ID` / `CLOUDFLARE_AI_TOKEN`) |
| vault item `cloudflare-tunnels` (folder `cloudflare`) | vault | `CF_ACCOUNT_ID` + `CF_API_TOKEN_READ/WRITE` → the `cf` skill inside the agent |
| `GH_PUSH_TOKEN` | repo secret → `.env` → container `GH_TOKEN` | `gh auth setup-git` — authenticated pushes from `/srv/tvcli` |

Values are NEVER baked into the image (the settings template carries an
`@CF_ACCOUNT_ID@` placeholder rendered at boot); `settings.yaml` is mode
600 and the API key is only ever read from runtime env.

## Deploying

Push to `main` touching `docker/ga/**` or `.github/workflows/ga-deploy.yml`
auto-deploys (or dispatch `ga-deploy.yml` manually; `transport` input:
`ghcr` default with `ssh-stream` fallback — see the workflow header for the
required repo secrets). GA has no trading mode — nothing to preserve or set.

Required repo secrets: `SSH_HOST`, `SSH_USER`, `SSH_PORT`, `SSH_PRIVATE_KEY`,
`GHCR_PULL_TOKEN` (ghcr transport), `BW_URL`, `BW_USERNAME`, `BW_PASSWORD`,
`BW_CLIENTID`, `BW_CLIENTSECRET`, `GH_PUSH_TOKEN`, and optionally
`CF_API_TOKEN` / `CF_ACCOUNT_ID` / `CF_TUNNEL_ID` for the tunnel ingress
step.
