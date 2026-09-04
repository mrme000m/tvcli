# grid-autonomy console — observe · configure · control

A self-contained web console for the autonomous grid-trading daemon:
a stdlib-only backend (`server.py`) that reads the daemon's state
artifacts, proxies its ctl plane, and adds safe config editing + daemon
lifecycle control — plus a vanilla JS/HTML/CSS frontend (`static/`).
The daemon itself is untouched; the console is purely additive and
fail-soft (it renders last-persisted state even when the daemon is down).

```
browser ── http://127.0.0.1:8798 ──> console/server.py
                                      ├─ reads  state/{state.json, decisions.jsonl,
                                      │         reliability.json, reports/, daemon.log}
                                      ├─ proxies daemon ctl :8799 (rescreen/rotate/kill…)
                                      ├─ edits  config.yaml (whitelist, comments preserved)
                                      └─ controls daemon lifecycle (launchd-aware)
```

## Run

```sh
cd agents/grid-autonomy
python3 console/server.py            # http://127.0.0.1:8798
CONSOLE_PORT=8800 python3 console/server.py   # override port
```

Stdlib only — no pip installs, no build step. Binds `127.0.0.1` only.

The console can run (and show last-persisted state) whether the daemon is
up or down; live values (status chips, ladder cursors, feed) update every
5s while the page is visible.

## The UI

| View | What it shows / does |
|------|----------------------|
| **Fleet** | One card per slot. Active bots render a **channel ladder** — the bot's actual ATR channel (`low/mid/high`) with its geometric grid rungs and a live price cursor (crimson flag when out of channel). Fills vs stagnation floor, unrealized PnL, commitment, hold time, stagnant/adopted/rotate-queued badges. Right rail: control actions, live journal feed, last screen shortlist, fleet summary. |
| **Decisions** | The full `decisions.jsonl` ledger — every deliberate → guard → deploy call with regime, score, step, slot, LLM-degraded flag, rationale, and the outcome (realized PnL) attached on close. Filter by text/state. |
| **Run cards** | Index of `state/reports/`; each card opens the rendered markdown (Route/Ground/Deliberate/Guard/Deploy/Observe/Reflect/Caveats) with the raw JSON behind a toggle. |
| **Reliability** | Per-archetype ledger with sizing-tier computation (base <10 samples → probe ≥10 → full ≥30 & PF ≥1.3; recent PF <1.0 kills) and a progress track to the 30-sample gate. |
| **Config** | Whitelisted knobs (portfolio, cadence, policy, sizing ladder, memory) edited in place — comments preserved, rolling `.bak` kept, round-trip verified before write. Everything else (incl. `autonomy.live_profiles`) is read-only by design; the daemon reads config at startup, so edits prompt a restart. |
| **Logs** | `daemon.log` tail with grep + follow. |

## API

Everything the UI does is a plain JSON endpoint (safe to curl):

| Method | Path | Effect |
|--------|------|--------|
| GET | `/api/overview` | Merged snapshot: daemon info, ctl status, enriched bots, slots, committed, journal tail, reliability, last screen, config digest, PB health. |
| GET | `/api/daemon` | Supervisor/lifecycle detail (pid, mode, launchd vs manual, uptime, KILL). |
| GET | `/api/state` | Raw `state.json`. |
| GET | `/api/journal?limit=` | Journal tail. |
| GET | `/api/decisions?limit=` | Decisions, newest first, outcomes included. |
| GET | `/api/reliability` | Ledger + tier computation + ladder thresholds. |
| GET | `/api/screen` | Latest rescreen run-card extract (top candidates). |
| GET | `/api/reports` · `/api/reports/<stem>` | Run-card index / one card `{json, md}`. |
| GET | `/api/logs?lines=&grep=` | `daemon.log` tail. |
| GET | `/api/config` | Parsed `config.yaml` + editable whitelist. |
| GET | `/api/observe` | Proxy of daemon ctl `/observe`. |
| GET | `/api/meta` | Ports, paths. |
| POST | `/api/ctl/rescreen` | Queue immediate rescreen. |
| POST | `/api/ctl/reliability` | Queue reliability refresh. |
| POST | `/api/ctl/rotate` `{"slot": n}` | Force-rotate a slot. |
| POST | `/api/ctl/kill` `{confirm}` | Write the KILL file. |
| POST | `/api/ctl/unkill` `{confirm}` | Remove the KILL file. |
| POST | `/api/config` `{"edits": {path: value}}` | Apply whitelisted edits (backup + round-trip check). |
| POST | `/api/daemon/stop` `{confirm, force}` | KILL + SIGTERM (+SIGKILL with force). |
| POST | `/api/daemon/start` `{confirm, live_paper, clear_kill}` | `scripts/start.sh`, optionally `--live-paper`. |
| POST | `/api/daemon/restart` `{confirm, clear_kill}` | launchd kickstart (supervised) or stop+start. |

### Safety model

- **127.0.0.1 only**; cross-origin POSTs are refused.
- Destructive calls (`kill`, `stop`, `restart`) require `{"confirm": true}`
  — the UI backs these with explicit confirm dialogs.
- Config edits are restricted to a **whitelisted, range-checked** set of
  numeric knobs; `autonomy.live_profiles` / `paper_profiles` are
  deliberately not editable from the console. Every write is
  comment-preserving (`yaml_edit.py`), round-trip verified against the
  YAML parser, and leaves `config.yaml.bak`.
- A KILL file is never cleared implicitly — `start`/`restart` refuse with
  `kill_present: true` unless the request explicitly passes `clear_kill`.
- The console never talks to WunderTrading directly; every trading action
  still flows through the daemon's guardrailed ctl plane.

## Files

| Path | Role |
|------|------|
| `server.py` | HTTP backend: static serving, read-only state APIs, ctl proxy, config editor, daemon lifecycle ops. |
| `yaml_edit.py` | Path-aware, comment-preserving YAML leaf editor (block + one flow level). |
| `static/index.html` · `static/app.js` · `static/styles.css` | The frontend — vanilla, no dependencies, no build step. |
| `../tests/test_console.py` | Offline unit + HTTP tests (part of the daemon suite). |

## Tests

```sh
cd agents/grid-autonomy
python3 -m unittest tests.test_console        # console only
python3 -m unittest discover -s tests -t .    # full suite (all offline)
```
