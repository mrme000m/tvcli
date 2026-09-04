# Grid fleet — the autonomous grid-trading loop

How the bootstrapped dsh specialist fleet researches, screens, configures and
manages a grid-trading system that stays responsive to the market. The
machinery (presets, MCP rows, autoserve markers, vault items) is installed by
`bootstrapping/ansible/prime-stack.yml` under the `fleet` tag; this doc is the
operating blueprint the agents follow.

## The loop

```
            ┌──────────────────────────────────────────────────────────────┐
            │                   prime-orchestrator (coordinate)            │
            │   goals, delegation to prime-agent workers, fleet column      │
            └───────┬───────────────────────────────────────────▲──────────┘
                    │ delegate                                   │ report/verify
      ┌─────────────▼──────────┐        ┌────────────────────────┴─────────┐
      │ research + screen      │        │ manage                            │
      │  tv-investigator:      │        │  wt-investigator: live strategy   │
      │  /hunt fan-out over    │        │  edits (TP/SL/DCA/trailing),      │
      │  the accounts.json     │        │  close/cancel/swing, grid re-arm  │
      │  pool, token_screen.py │        └────────────────────────▲─────────┘
      │  regime ranking,       │                                 │ adaptation triggers
      │  tvcli indicator skills│        ┌────────────────────────┴─────────┐
      └─────────────┬──────────┘        │ configure                         │
                    │                   │  wt-investigator: grid/DCA/classic│
                    │                   │  config on WunderTrading via      │
                    │                   │  wt_api / wt_mcp / headful UI     │
                    │                   │  (bdg); pair codes resolved from  │
                    │                   │  get_exchange_markets             │
                    │                   └───────────┬───────────────────────┘
                    │ candidates + regime           │
                    └──────────► tv-scout: visual confluence on the live
                                  chart before anything goes live
```

**Responsiveness comes from `tvcli watch`.** A watch spec (`--spec`, the
watchtower Go mirror) polls price/levels/indicators every N seconds, fires
one-shot triggers per episode and appends a journal; the prime-orchestrator
turns fired triggers into re-analysis and adaptation orders for
wt-investigator. Regime decay, full DCA ladders, squeeze breakouts and
funding extremes are the standard trigger set (see the wundertrading skill's
playbook §5).

## Fleet roster

| Preset | Role in the loop | Tool surface |
|---|---|---|
| `prime-orchestrator` | Coordinate: tracked goals, delegation to prime-agent workers, fleet column, workflow engine | prime_agent tool, workflow/subagent tools (plugin) |
| `tv-investigator` | Research + screening at scale: multi-session TradingView network API, protocol capture, tvcli extension, regime ranking | bdg `tv` command group, tvcli `/hunt`, skill-filesystem |
| `wt-investigator` | Configure + manage WunderTrading bots: grid/DCA/signal config, exchange setup, API keys, backtesting UI | wt_* tools (wt CLI: session/login/browse/api/apikey/mcp), mcp-wundertrading row, bdg |
| `tv-scout` | Confirm: render the thesis on the live chart, screenshot, vision confluence | tvvisual (CloakBrowser + CDP), tvcli, skill-filesystem |
| `web-discovery` | Reverse-engineer: investigate any platform's UX + network API (bdg+cloak), codify verified findings, forge platform CLIs/skills/plugin rows/presets, improve them on drift | launch.mjs + bdg daemon, `record`-capable platform CLIs (wt.mjs), web-discovery skill |

All presets discover the workspace skills (tvcli, pine2tool, tv-scout,
tv-usecases, tv-watch, wundertrading, web-discovery, wt-network from the
plugin) through `skill-filesystem` local roots when working in the repo.

## Screening: parallel TV sessions with cookies

The tvcli multi-account pool is the TradingView screening engine:

1. `accounts.json` (provisioned from vault item `tvcli-accounts-pool`) holds
   the account registry — each account carries its own `SESSION`/`SIGNATURE`
   cookies; the pool fans work round-robin with failover.
2. `POST /hunt` on the tvcli server (`./tvcli serve --daemon` — autostarted
   via the `.tvcli-autoserve` marker the fleet tag writes) runs one skill
   across many symbols in parallel over the pool, capped per account tier
   (free = 2 studies/chart), e.g. `mtf-confluence` or `xau-scalp` over
   10 symbols in one call.
3. `scripts/token_screen.py` (wundertrading skill) then classifies every
   candidate's regime from public OHLCV and ranks them; spread data joins
   from `get_exchange_markets` via MCP when the keys are set.
4. Watch specs keep the shortlist under continuous observation.

## Regime → grid configuration

The wundertrading skill's playbook (§3) and
[grid-bot.md](../../.agents/skills/wundertrading/references/grid-bot.md) map
the classified regime to the bot archetype. On WunderTrading the
**classic/DCA strategy surface is fully API/MCP-reachable**
(`place_strategy_trade`, `edit_trade_strategy`); the multi-level
**Grid / Multi-Pair Grid / Signal bot configurators are web-UI flows** —
wt-investigator drives them headful via bdg with vault-persisted cookies (the
wt-network skill carries the verified selectors), and uses REST/MCP for
everything they expose.

The Grid bot also carries a **webhook start condition**: a
TradingView-compatible `Entry` / `Exit` alert message starts or stops the bot,
so the consolidated grid-candidate tvcli skill can **arm/trigger a grid
headlessly** — no browser, no MCP call. That is the preferred tvcli→grid
bridge and closes the loop between analysis and execution. For the grid step,
prefer the platform's built-in **Optimize** (a 30-day Profit-per-GRID sweep)
and **Profit-Optimized Pairs** (ROI-ranked on a `$500`/`$50` benchmark) over a
hand-rolled parameter search; keep the regime/grid-type decision in
`market_regime.py`.

| Regime | Bot archetype | Core config guidance |
|---|---|---|
| `trend_up` / `trend_down` | Directional DCA (slanted grid) or classic strategy via API | laddered TPs, trailing stop, break-even move; short side only on futures |
| `chop_high_volatility` | Futures/Spot **Grid** centered at the mid (or DCA strategy via API) | tight grid step (0.5–2%), DCA safety orders below the lower bound, TP on the averaged price |
| `squeeze` | Grid paused / range limits at the band edges | narrow bounds, small TP, tight structure stop; re-arm on breakout |
| `neutral` | Flat, or a small probe DCA | half size, wide bounds |
| `multi-pair` (portfolio view) | **Multi-Pair Grid** across the token_screen top-N | per-pair weight by rank score; only pairs in the same regime family |

Cross-cutting rules (from the wundertrading skill):

- `pairCode` is the market `code` from `get_exchange_markets` — numeric on
  Hyperliquid (`BTC-USDC` → `"0"`), tickers on Binance. Never hand-write it.
- DCA sizing math is mandatory: an N-step ladder with volume multiplier v
  commits up to `A × (1 + v + … + v^(N−1))`; cap the total at ≤ 50% of the
  profile balance.
- Every live order requires explicit confirmation (the tools enforce it);
  paper-trade on a `demo` profile first, probe 5–10%, scale only after the
  exported history passes the reliability bars (≥ 30 samples, PF ≥ 1.3).

## Manage: keep the system responsive

- `tvcli watch --spec fleet.json --interval 60` (or per-token specs) is the
  heartbeat; fired triggers land in the journal the prime-orchestrator
  consumes.
- Regime change → wt-investigator re-configures: `edit_trade_strategy`
  (classic: no DCA fields; DCA: `placeConditionalOrdersOnExchange` ignored),
  `place_strategy_swing` to flip futures positions, `close_strategy_market`
  to exit, `cancel_strategy` to abort unentered bots.
- `get_live_strategies` + `export_strategies_history` feed the reliability
  loop (win rate / profit factor / expectancy / drawdown per archetype);
  PF < 1.0 over the last 20 samples kills the archetype.
- Key rotation: recreate the key pair (`wt apikey create`), re-run the
  playbook's fleet tag — the mcp-wundertrading row picks up the new pair
  from the vault on the next run.

## Provisioning contract (what the fleet tag guarantees)

- 4 specialist presets installed into `~/.dsh/.agent-presets/` with marker
  semantics (user edits are preserved; updates follow the vendored files in
  `bootstrapping/presets/`).
- `mcp-wundertrading` + `wt-tools cloakDir` rows merged into
  `~/.dsh/profiles/web/cordis.patch.yml` (0600; keys templated at runtime
  from vault item `wundertrading-api` → `browser-debug/secrets/runtime/wt.env`,
  never committed, never logged).
- `.tvcli-autoserve` marker (tvcli multi-account server on :8765) when
  `accounts.json` is provisioned.
- Secrets stay in the Bitwarden vault (`keys.00m.indevs.in`):
  `wundertrading-login`, `wundertrading-session`, `wundertrading-api`,
  `tvcli-accounts-pool`, `tvcli-primary-env`, `browser-debug-env`.

## Verification checklist (inside the codespace)

```sh
ansible-playbook bootstrapping/ansible/prime-stack.yml -i localhost, \
  -e ansible_connection=local -e ansible_python_interpreter=/usr/bin/python3 \
  -e tv_workspace="$PWD" --tags fleet
ls ~/.dsh/.agent-presets/                          # prime-orchestrator + 4 fleet presets
grep -c "mcp-wundertrading\|wt-tools" ~/.dsh/profiles/web/cordis.patch.yml   # 2 rows
ls -l .tvcli-autoserve                             # present when accounts.json exists
curl -sf http://127.0.0.1:8765/health              # tvcli server (after restart)
dsh web --port 3081 --host 127.0.0.1 --no-open     # fleet column; pick any preset
```
