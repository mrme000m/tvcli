# grid-autonomy — VPS deployment guide

This directory is the **deployment layer** for the grid-autonomy Docker
image: `docker-compose.yml` (service layout), `env.example` (environment
template → `grid.env`), `verify.sh` (host-side smoke check) and this guide.
The image itself is built by `docker/Dockerfile` + `docker/entrypoint.sh`
(+ `patch_config.py`, `healthcheck.sh`) — see §c.

## CI/CD deploy (GitHub Actions → az00)

`.github/workflows/grid-autonomy-deploy.yml` (push to main on build-context
paths — auto-deploys in dry-run — plus manual `workflow_dispatch` with a
`mode` input)
builds the image in CI, **streams** it to the VPS
(`docker save | gzip | ssh … | sudo docker load` — no tarball ever lands
on the host's small root disk), writes `/opt/grid-autonomy/.env` with the
`BW_*` vault machine credentials from repo secrets, and (re)starts the
container via `docker/vps-run.sh` with five named volumes (`grid-state`,
`grid-pb`, `grid-profile`, `grid-secrets`, `grid-bwcli`) so everything
survives redeploys; `--restart unless-stopped` survives host reboots.

SSH host config lives in repo secrets: `SSH_HOST`, `SSH_USER`, `SSH_PORT`,
`SSH_PRIVATE_KEY` (deploy key for a host user with passwordless sudo +
docker access). The workflow's `mode` input sets `GRID_MODE` (`dry-run`
default, `live-paper` deliberate).

Ports are published on **127.0.0.1 only** — the console (`:8798`) and ctl
(`:8799`) carry no built-in auth. Reach them through an SSH tunnel:

```sh
ssh -L 8798:localhost:8798 -L 8799:localhost:8799 <host>
# then http://localhost:8798 (console) / http://localhost:8799 (ctl API)
```

One WunderTrading account must not run two live instances: the Mac's live
daemon owns the account, so the VPS runs dry-run unless deliberately
switched to `live-paper`.

## Public hostnames (Cloudflare tunnel)

All four services are published on the indevs domain through the
remotely-managed `grid-autonomy` Cloudflare tunnel (managed with the
[`cf` skill](../.agents/skills/cf/SKILL.md) — also baked into the image with
its vault tokens, so agents can manage tunnels from inside the container):

| hostname | service |
|---|---|
| `https://grid.00m.indevs.in` | mission console (UI) :8798 |
| `https://grid-ctl.00m.indevs.in` | daemon ctl API :8799 |
| `https://grid-pb.00m.indevs.in` | PocketBase :8090 |
| `https://grid-api.00m.indevs.in` | tvcli serve :8765 |

The `grid-cloudflared` connector runs beside the stack on the `grid-net`
docker network (ingress targets `http://grid-autonomy:PORT` — no host port
publishing involved). `vps-run.sh` keeps it ensured from `GRID_TUNNEL_TOKEN`
in `/opt/grid-autonomy/.env` (GitHub repo secret, written by the deploy
workflow), so it survives redeploys and host reboots. Health:
`gh`-less check — `curl -fs https://grid-ctl.00m.indevs.in/health`.

⚠️ The ctl API and PocketBase are **unauthenticated** on those public
hostnames (the daemon's kill/config surface!). Consider a Cloudflare Access
policy on `grid-ctl`/`grid-pb` if the deployment goes live-paper.

## Updating the deployed code with GitHub

Everything the image is built from lives in the repo, so a code update is:

```sh
git commit -m "grid-autonomy: <change>" && git push          # auto-deploys
# — or —
gh workflow run grid-autonomy-deploy.yml --ref main -f mode=dry-run   # manual
gh run watch     # live progress (build ~4 min + stream + boot ≈ 10-12 min)
```

Pushes to `main` that touch the build context (`docker/`, `agents/grid-autonomy/`,
`.agents/skills/`, `browser-debug/wt-login.mjs` + driver deps, Go sources)
trigger the deploy automatically (dry-run). The workflow is idempotent:
named volumes keep state across redeploys; the container is replaced with a
graceful SIGTERM stop; the tunnel connector is untouched. To change the
deployment mode deliberately, dispatch with `-f mode=live-paper`.


grid-autonomy is an autonomous grid-trading daemon that runs the whole loop
— **screen → deliberate → guard → deploy → watch → optimize → rotate** — on
WunderTrading **paper profiles**. All 8 guardrails fail closed; real-money
profiles stay refused while `autonomy.live_profiles: []` (the shipped
config). Full operating manual: `agents/grid-autonomy/README.md`. Operating
semantics (control plane, journal kinds, safety rails): the
`grid-autonomy` skill (`.agents/skills/grid-autonomy/SKILL.md`).

> **`GRID_MODE=live-paper` is the intended VPS production mode.** It
> creates real bots on WunderTrading *paper* profiles only. `dry-run` (the
> image default) is planning-only: screens, deliberates and journals
> everything, creates nothing.

---

## (a) What runs in the container

One container, one supervisor (`docker/entrypoint.sh`), six components
selected by `GRID_COMPONENTS=xvfb,pb,serve,browser,daemon,console`:

| Component | What it is | Port | Published? |
|---|---|---|---|
| `xvfb` | Virtual display for the headful browser | — | never |
| `pb` | PocketBase — event-driven persistence side channel (data in the `grid-pb` volume) | 127.0.0.1:8090 | no (optional debug profile — §f) |
| `serve` | `tvcli serve` — TradingView confluence backend (`/hunt` fitness). Needs the bind-mounted `/app/.env` TV auth | 127.0.0.1:8765 | **never** |
| `browser` | CloakBrowser — headful Chromium on CDP, inside Xvfb, profile in the `grid-browser-profile` volume. It is the **WunderTrading session-API transport** | 127.0.0.1:9222 | **never** |
| `daemon` | `agents/grid-autonomy/daemon.py` — the whole autonomous loop (screen/deliberate/guard/deploy/watch/optimize/rotate/reflect) + its control plane | 8799 | **yes** (compose) |
| `console` | Mission console — web UI + JSON API over the daemon's state | 8798 | **yes** (compose) |

Ports 8765 (tvcli) and 9222 (CDP) are hard dependencies that must stay
container-internal: 9222 is a full remote-control surface for a logged-in
browser, 8765 exposes the TV-authenticated backend. The compose file
publishes only **8798** and **8799** — and they only answer because the
image sets `GRID_BIND_HOST=0.0.0.0` (both HTTP servers default to 127.0.0.1
locally); that binding is container-scoped, so the host-side publish
binding and firewall still control exposure.

The healthcheck (`grid-healthcheck`, defined in the image) curls
`http://127.0.0.1:8799/health` — the daemon control plane is the liveness
signal for the whole stack.

Control-plane endpoints (no auth — see §g):

| Method | Path | Effect |
|---|---|---|
| GET | `:8799/health` | Liveness + KILL-file presence |
| GET | `:8799/status` | Slots, active bots, committed capital, `live_allow`, journal tail |
| GET | `:8799/reliability`, `:8799/observe`, `:8799/optimizer` | Ledgers / snapshots |
| POST | `:8799/rescreen` | Queue an immediate rescreen |
| POST | `:8799/optimize` | Queue an immediate optimizer cycle |
| POST | `:8799/rotate` | Force-rotate a slot — body `{"slot": n}` |
| POST | `:8799/kill` | Write the KILL file (halt at next loop tick) |

## (b) Prerequisites

- Linux VPS, **Docker Engine 24+** with the compose plugin
  (`docker compose version`).
- **2 vCPU / 4 GB RAM minimum** — headful Chromium + PocketBase + tvcli +
  daemon in one container; the browser is the heavy part.
- Outbound internet: WunderTrading, TradingView, Binance/Hyperliquid public
  APIs, Cloudflare Workers AI, GitHub (PocketBase download), Docker Hub
  (alpine for the optional debug profile).
- **amd64 or arm64** — the image is stdlib-Python + Chromium and builds/runs
  on both. `uname -m` on the VPS tells you which.
- Firewall or SSH-tunnel discipline for 8798/8799 (they have **no auth**).

## (c) Build

### On the VPS, from the repo

```sh
git clone <your-repo-url> && cd go        # repo root (this directory's parent)
docker build -t grid-autonomy:local -f docker/Dockerfile .
```

Always build **from the repo root** (the Dockerfile mirrors the repo into
`/app`, whitelist-pruned by `.dockerignore` — secrets and runtime state are
never baked; they arrive only via the runtime bind mounts in §d).

The build itself needs internet + time: it compiles the tvcli Go binary,
installs Node 22 + Chromium/Xvfb libraries, and downloads the pinned
PocketBase binary and a ~350 MB CloakBrowser Chromium build — expect the
first build to take several minutes and a few GB of disk.

### Cross-build from Apple Silicon (for an amd64 VPS)

Build the amd64 image locally, ship it as a tarball, load it on the VPS:

```sh
# on the Mac, from the repo root:
docker buildx build --platform linux/amd64 -t grid-autonomy:v1 -f docker/Dockerfile --load .
docker save grid-autonomy:v1 | gzip > grid-autonomy-v1.tar.gz

# transfer (any of scp/rsync):
scp grid-autonomy-v1.tar.gz user@vps:/tmp/

# on the VPS:
docker load < /tmp/grid-autonomy-v1.tar.gz
docker tag grid-autonomy:v1 grid-autonomy:local    # compose references :local
rm /tmp/grid-autonomy-v1.tar.gz
```

(`docker load` also accepts the gzip stream directly, as above. If you
prefer, edit `image:` in `docker-compose.yml` to `grid-autonomy:v1`
instead of retagging. Local tags follow `grid-autonomy:local`;
versioned tags like `grid-autonomy:v1` are what you ship.)

For an **arm64 VPS**, drop `--platform linux/amd64` (or set it to
`linux/arm64`) and everything else is identical.

## (d) Secrets materialization

There are two routes: **(0) the Bitwarden vault** (recommended — everything
below is fetched automatically at container boot) or the manual files in
**1–3**. They compose freely: the vault fills whatever the manual files
leave empty. **Nothing below ever goes into the image** — secrets arrive as
env (`grid.env`) or runtime files, never `COPY`/`ARG`. Lock every file to
`chmod 600`.

### 0. Bitwarden vault (recommended: fully automated)

Set `BW_URL`, `BW_CLIENTID`, `BW_CLIENTSECRET`, `BW_PASSWORD` in
`docker/grid.env` (template: `env.example`). At boot the entrypoint runs
`docker/vault_loader.sh`, which machine-authenticates the bw CLI against the
self-hosted vault and materializes:

| Vault item | Becomes |
|---|---|
| `wundertrading` (folder `grid-autonomy`) | `WT_EMAIL` / `WT_PASSWORD` |
| `provider-keys` (fields) | `NVIDIA_API_KEY`, `OPENROUTER_API_KEY`, `MISTRAL_API_KEY` (from `MISTRAL_VIBE_API_KEY`), optional `NVIDIA_BASE_URL` |
| `opencode-cloudflare` (notes) | `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_KEY` |
| `tvcli-primary-env` (notes) | `/app/.env` — **only when no `.env` is bind-mounted** |
| `wundertrading-session` (notes) | `wt-session.env` in the `grid-secrets` volume — only when absent |

Precedence: **mounted files win** (a bind-mounted `.env` or an existing
`wt-session.env` is never overwritten), and vault values override matching
keys in `grid.env`. Resolved values land in `/data/secrets/grid-vault.env`
(mode 600) which the entrypoint sources; secret values are never printed.

With `WT_EMAIL`/`WT_PASSWORD` present the deployment is **self-healing on
WunderTrading auth**: at boot (and every `WT_KEEPER_INTERVAL` seconds,
default 1800) the session is probed; when it is stale, `wt-login.mjs` logs
in with the credentials in the headful CloakBrowser page and re-exports
fresh cookies. The login itself is driven by the official `cloakbrowser`
npm stealth driver (Bézier-curve mouse, per-character typing, isTrusted
CDP key events) attached to the baked CloakBrowser binary — the WT login
form is gated by an invisible Google reCAPTCHA that scores the
interaction, so plain CDP form-filling fails. Manual cookie renewal
(§d.2 below) becomes unnecessary.

The `bw` CLI is baked into the image (`@bitwarden/cli`); its state lives in
the `/data/bw-cli` directory. CI validation of this path (no 2GB image
build) runs via the manual GitHub workflow
`.github/workflows/grid-autonomy-vault-smoke.yml` — the `BW_*` values are
available as GitHub repo secrets of **mrme000m/tvcli**.

### 1. TradingView auth → `docker/.env` (bind-mounted to `/app/.env`)

Used by `tvcli serve` (the confluence backend). Extract the cookies from a
logged-in `tradingview.com/chart/` page (dev tools → Application → Cookies):

```sh
cd docker/
cat > .env <<'EOF'
SESSION=<sessionid cookie>
SIGNATURE=<sessionid_sign cookie>
DEVICE_T=<device_t cookie>
TV_USER=<your tradingview username>
EOF
chmod 600 .env
```

Optional multi-account pool: an `accounts.json` sidecar (`{"default": ...,
"accounts": {"name": {"sessionId": ..., "signature": ..., "deviceToken": ...,
"userName": ..., "tier": ...}}}`) can be placed next to the compose file and
bind-mounted to `/app/accounts.json` the same way (`TV_ACCOUNTS_FILE`
overrides the path). Single-account `.env` is the default and enough.

### 2. WunderTrading session → `grid-secrets` volume (optional)

Used by the browser watchdog's `wt_restore_cmd` to re-assert the WT cookie
session after a browser relaunch. Lives in the `grid-secrets` named volume
(`/app/browser-debug/secrets/runtime/wt-session.env`) — written by the
vault loader and refreshed automatically by `wt-login.mjs` (see §d.0). To
seed it manually:

```sh
docker cp wt-session.env grid-autonomy:/app/browser-debug/secrets/runtime/
```

Two formats (first wins):

```sh
# preferred — full cookie jar as JSON:
WT_COOKIES_JSON='[{"name":"PHPSESSID","value":"...","domain":"wundertrading.com", ...}, ...]'

# or the two cookies that matter:
WT_PHPSESSID=<php session id>
WT_CF_CLEARANCE=<cloudflare clearance cookie>
```

**This file is optional.** Compose cannot conditionally mount, so the mount
line is always present — create the file even if you do not have the
cookies yet (`touch wt-session.env`); an empty file simply disables cookie
re-assertion and renewal becomes manual.

**PHPSESSID expires roughly weekly.** The browser profile volume
(`grid-browser-profile`) keeps the logged-in session across restarts, so
renewal is only needed after a real expiry. Symptoms: `browser-restart …
relaunch ok` in the journal followed by persisting
`grid status list unavailable` observe errors.

**With the vault (§d.0) or any `WT_EMAIL`/`WT_PASSWORD` configured this is
AUTOMATIC**: the entrypoint's auth probe and the background WT session
keeper detect the stale session, run `wt-login.mjs` (credential login in
the headful browser, cookies re-exported to `wt-session.env`) and re-assert
the page via `wt.mjs`. Check `[wt-keeper]` lines in `docker compose logs`.

Manual fallback (no credentials available):

1. Log in to wundertrading.com on **any** machine (fresh login).
2. Export the cookies (dev tools → Application → Cookies for
   wundertrading.com) and push them into the volume:
   `docker cp wt-session.env grid-autonomy:/app/browser-debug/secrets/runtime/`
3. `docker compose restart` — the entrypoint re-asserts the session via
   `wt.mjs` at boot, and the watchdog re-asserts on later browser relaunches.

### 3. LLM keys → `docker/grid.env`

```sh
cd docker/
cp env.example grid.env
chmod 600 grid.env
$EDITOR grid.env      # fill CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_KEY
                      # (or CLOUDFLARE_AI_TOKEN); optionally the others
```

- **Cloudflare Workers AI** (account id + API key, or account-scoped
  `CLOUDFLARE_AI_TOKEN`) — primary of the LLM chain; required before
  `GRID_MODE=live-paper`, strongly recommended for dry-run (without it the
  swarm degrades to rule fallback and decisions are tagged `llm_degraded`).
- **NVIDIA / OpenRouter** keys — optional fallback providers.
- **MISTRAL_API_KEY** — optional; the slot optimizer's arbiter is pinned to
  Mistral, so without it fast-lane swap verdicts fall back to the numeric
  margin rule.
- **PB_ADMIN_EMAIL / PB_ADMIN_PASS** — set both; if the password is empty,
  first boot generates one and prints it **once** in the container log.

## (e) First boot

The image default is **dry-run**: plan-only, zero WunderTrading mutations.
Start there even though `env.example` ships `GRID_MODE=live-paper` (the
intended production value) — set `GRID_MODE=dry-run` in your fresh
`grid.env` for the first boot:

```sh
cd docker/
# grid.env: GRID_MODE=dry-run (image default — unset works too)
docker compose up -d
docker compose ps           # wait for (healthy) — image healthcheck curls :8799/health
docker compose logs -f     # Ctrl-C to detach; logs stay in the container/volumes
```

What to watch in `docker compose logs -f`:

- Xvfb + Chromium up (CDP on :9222), PocketBase started,
  `tvcli serve` listening on :8765;
- daemon boot lines: slot plan reconciliation, `screen` cycle start;
- the first rescreen finishing (a run card lands in `state/reports/`);
- no repeated `browser-restart` / `observe error` lines.

Verification checklist:

```sh
curl -s http://localhost:8799/health | head    # {"status": ...}
# console — open http://<vps>:8798 in a browser (tunnel if firewalled — §g)
# PocketBase is internal; check it through the container:
docker compose exec grid-autonomy curl -s http://127.0.0.1:8090/api/health
# offline unit tests inside the container:
docker compose exec grid-autonomy sh -c   "cd /app/agents/grid-autonomy && python3 -m unittest discover -s tests -t ."
# or run the host-side smoke script:
./verify.sh
```

Then switch to paper deploys — edit `grid.env`:

```
GRID_MODE=live-paper
```

and apply it:

```sh
docker compose up -d --force-recreate
```

(Use `--force-recreate`, not `restart`: compose only reads `env_file`
(`grid.env`) when it (re)creates the container.) From now on the daemon
creates real bots on the allowlisted WunderTrading **paper** profiles
(`demo-hype`, `demo-bn`). Real money stays refused:
`autonomy.live_profiles: []`.

## (f) Operations runbook

All commands from `docker/` unless noted.

**Logs.**
`docker compose logs -f` (all components), `docker compose logs -f
grid-autonomy` (daemon). The entrypoint also writes per-component logs into
the `grid-state` volume — `docker compose exec grid-autonomy ls
/app/agents/grid-autonomy/state/`:
`daemon.log`, `console.log`, `tvcli-serve.log`, `browser-launch.log`,
`wt-restore.log` (PocketBase: `.pocketbase/pb.log` and
`.pocketbase/setup.log`). Run cards and the decision journal are in the
same volume (`state/reports/`, `state/decisions.jsonl`) — the console's UI
reads them; see also `agents/grid-autonomy/README.md` "State artifacts".

**Journal & status.**
`curl -s http://localhost:8799/status | jq` — slots, active bots, committed
capital, `live_allow`, plan capacity, and the last journal entries (kinds:
`screen`, `veto`, `deploy-paper`, `optimizer-swap`, `browser-restart`,
`observe-outage`, …). The console at `:8798` renders the same state.

**Config edits.**
Edit the host-side `docker/config.yaml` (bind-mounted to
`/app/agents/grid-autonomy/config.yaml`), then `docker compose restart` —
the daemon reads config at startup and re-normalizes slot budgets
(`slots-reconciled`). (Edits to `grid.env` need `docker compose up -d
--force-recreate` instead — env vars are only read at container creation.)
The
entrypoint auto-patches the two `watch.browser_*` commands
(`browser_launch_cmd`, `wt_restore_cmd`) to container paths on every boot;
the patch writes through the bind mount to your host file, backing it up
**once** to `config.yaml.bak` next to it. The console's config editor
(comment-preserving, whitelisted keys) applies whitelisted edits without a
restart for some knobs — but venue/portfolio changes need the restart
path.

**Forced actions** (control plane, no restart):

```sh
curl -s -X POST http://localhost:8799/rescreen     # immediate rescreen
curl -s -X POST http://localhost:8799/optimize     # immediate optimizer cycle
curl -s -X POST http://localhost:8799/rotate -d '{"slot": 2}'   # force-rotate slot 2
```

**Stopping / starting — two distinct flows.**
- *Normal stop* — `docker compose stop` (or `docker stop grid-autonomy`):
  graceful SIGTERM. The entrypoint stops the daemon cleanly (state save),
  tears down the browser, and the container restarts cleanly later with
  `docker compose start` / `up -d`. **No KILL file is written.** This is
  the flow for config edits, host maintenance, reboots.
- *Operator halt ("stop and keep stopped")* —
  `curl -X POST http://localhost:8799/kill` (or `docker compose exec
  grid-autonomy curl -X POST http://127.0.0.1:8799/kill`). This writes
  `agents/grid-autonomy/KILL`, halts the loop at the next tick, and — by
  design — survives container restarts: the daemon refuses to start while
  the file exists, and the entrypoint logs a loud boot-time warning
  ("KILL file present — the daemon will refuse to start!"). Use it when
  the fleet must stay down. Clear it to run again:

```sh
docker compose exec grid-autonomy rm -f /app/agents/grid-autonomy/KILL
docker compose restart
```

- A stale `state/daemon.pid` is auto-cleared at boot; only the KILL file
  needs manual attention.
- `docker compose down` removes the container entirely — the next
  `docker compose up -d` starts clean (all state is in the named
  volumes). Remember: grid.env edits are picked up only on container
  (re)creation, not on `restart`.

**Cookie renewal.** See §d.2 — rewrite `wt-session.env`, then
`docker compose restart`. The `grid-browser-profile` volume keeps the
session warm across restarts; renewal is only needed after real expiry
(~weekly for PHPSESSID).

**PocketBase dashboard.** Zero-config:

```sh
docker compose exec grid-autonomy curl -s http://127.0.0.1:8090/api/health
# UI not reachable this way — for the admin UI use a temporary publish:
```

Temporary publish: set `PB_HOST=0.0.0.0` in `grid.env`, then either
uncomment the `127.0.0.1:8090:8090` port line in `docker-compose.yml`, or
run the compose debug profile (a socat forwarder — no image rebuild needed):

```sh
docker compose --profile debug up -d        # publishes 8090 (requires PB_HOST=0.0.0.0)
# ... admin UI at http://<vps>:8090/_/  (log in with PB_ADMIN_EMAIL/PB_ADMIN_PASS)
docker compose --profile debug stop pb-forward   # when done
```

Revert `PB_HOST=127.0.0.1` afterwards.

**Backups.** `config.yaml` is host-side — copy it. The three named volumes
hold everything else:

```sh
cd docker/
docker run --rm -v grid-state:/data -v "$PWD:/backup" alpine tar czf /backup/grid-state.tgz /data
docker run --rm -v grid-pb:/data -v "$PWD:/backup" alpine tar czf /backup/grid-pb.tgz /data
docker run --rm -v grid-browser-profile:/data -v "$PWD:/backup" alpine tar czf /backup/grid-browser-profile.tgz /data
```

(Optionally stop the stack first for a consistent snapshot:
`docker compose stop` → back up → `docker compose start`.)

**Restore** (fresh VPS or disaster recovery): bring the stack up once so
the volumes exist (or `docker volume create grid-state` …), then unpack:

```sh
docker compose stop
docker run --rm -v grid-state:/data -v "$PWD:/backup" alpine sh -c "cd /data && tar xzf /backup/grid-state.tgz --strip-components=1"
# same pattern for grid-pb and grid-browser-profile
docker compose start
```

**Updates.** The named volumes persist everything that matters:

```sh
cd <repo-root>
git pull
docker build -t grid-autonomy:local -f docker/Dockerfile .   # or rebuild via compose
cd docker/ && docker compose up -d --force-recreate   # run the new image, keep volumes
```

For cross-built images repeat §c. Run `./verify.sh` after every update.

## (g) Security notes

- **8798 / 8799 have no authentication.** The console can edit config and
  trigger KILL/rotate; the ctl plane can too. Both bind `0.0.0.0` inside the
  container (image env `GRID_BIND_HOST=0.0.0.0`, required for the compose
  port mappings to be reachable — keep it while publishing ports; set
  `127.0.0.1` in grid.env only to restore loopback-only binding inside the
  container, which makes the published ports unreachable). The binding is
  **container-scoped**: exposure is still fully controlled host-side by the
  publish binding and firewall. Restrict access: firewall to your IP or keep
  them off the internet entirely — never expose them raw:

```yaml
# docker-compose.yml — localhost-only alternative:
    ports:
      - "127.0.0.1:8798:8798"
      - "127.0.0.1:8799:8799"
```

```sh
ssh -L 8798:localhost:8798 -L 8799:localhost:8799 user@vps   # then use localhost locally
```

- **Never publish 9222** (Chromium CDP = full remote control of a
  logged-in session) or **8765** (TV-authenticated confluence backend).
  They stay container-internal by design; the compose file does not map
  them — keep it that way.
- **Secret file permissions**: `grid.env`, `.env`, `wt-session.env` and any
  `accounts.json` → `chmod 600`. Never commit them; never `COPY` them into
  an image (`docker history` bakes layers forever).
- **Paper-only guardrails**: 8 fail-closed gates, empty
  `autonomy.live_profiles`, a hard profile denylist — the shipped posture
  refuses real money by construction. Going live is a deliberate, explicit
  operator act (see `agents/grid-autonomy/README.md` "Paper → live
  escalation"); the Docker layer does not change any of it.
- **VPS datacenter IP + Cloudflare risk**: the WunderTrading browser
  session originates from a datacenter IP and Cloudflare may challenge it
  harder than a residential one. Mitigations: keep the
  `grid-browser-profile` volume warm (never delete it casually — the
  account/session reputation lives there); if you route through a proxy,
  a `--proxy-server=…` launch flag is the supported hook (browser
  relaunch command in config) — keep the exit IP stable and, ideally, in
  the same country as the original login.

## (h) Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `observe error: grid status list unavailable (browser/session down)` on every bot, plus `observe-outage` journal entries (one per ~30 min of total blindness) | CloakBrowser/CDP down or the WT login lapsed. The watchdog relaunches the browser (journal `browser-restart`, ≤1 try / 10 min). If relaunch is `ok` but errors persist → session cookie expired → renewal procedure §d.2. |
| `llm_degraded: true` on decisions | LLM keys missing/expired. Not fatal — rule-map fallback. Check `CLOUDFLARE_*` in `grid.env`, restart. |
| PocketBase gone / collections stale | PB is a side channel — the file layer (`state/`) is the system of record, the daemon keeps running. Check `docker compose exec grid-autonomy curl -s http://127.0.0.1:8090/api/health`; if dead, `docker compose restart` re-runs the (idempotent) PB setup. |
| tvcli serve down (confluence disabled) | Screens still work — `/hunt` fitness fails soft and candidates lose the confluence bonus. Check the bind-mounted `/app/.env` TV auth (expired cookies → re-extract per §d.1) and `docker compose logs -f serve`. |
| Daemon refuses to start; logs show "KILL present — refusing to run" + the entrypoint's boot warning | A previous intentional `POST :8799/kill` left `agents/grid-autonomy/KILL` (it survives restarts by design). Clear it: `docker compose exec grid-autonomy rm -f /app/agents/grid-autonomy/KILL`, then `docker compose restart`. |
| "daemon already running (PID …)" | Stale `state/daemon.pid` — auto-cleared at boot; if it ever persists, `docker compose restart`. (Override knob `GRID_NO_PIDGUARD=1` exists but should not be needed in Docker, where exactly one daemon runs per container.) |
| `capacity-veto` / `demo-cap-veto` journal entries | WunderTrading plan caps (1 active grid bot on non-Hyperliquid exchanges, 5 demo bots on the free plan) — capacity, not an error. See the operating manual's troubleshooting table. |
| Healthcheck never green | `curl -s http://localhost:8799/health` from the host; if it answers but Docker still shows unhealthy, check the image's `grid-healthcheck` interval/start-period. If it does not answer: `docker compose logs -f` and walk §h top-down. |

Quick diagnostic one-liners:

```sh
docker compose ps                                   # health + uptime
curl -s http://localhost:8799/health; echo          # daemon liveness
curl -s http://localhost:8799/status | jq '.journal_tail[:10]'
docker compose exec grid-autonomy curl -s http://127.0.0.1:9222/json/version   # CDP up?
docker compose exec grid-autonomy curl -s http://127.0.0.1:8765/health         # tvcli up?
docker compose exec grid-autonomy curl -s http://127.0.0.1:8090/api/health     # PB up?
```

---

*Deployment layer files: `docker-compose.yml`, `env.example` (→ `grid.env`),
`verify.sh`, this guide. Image-side files (owned separately):
`Dockerfile`, `entrypoint.sh`, `patch_config.py`, `healthcheck.sh`.*
