---
name: grid-bot
description: Create and configure a WunderTrading Grid bot for a specific token — screen the universe for mean-reversion/trend regimes, derive a ready-to-apply Grid-bot config (grid type, ATR-band channel, ATR-derived Profit-per-GRID, grid count, per-trade sizing, Stop Trigger, Pump Protection) from live OHLCV with the repeatable scripts, then apply it via the web UI (headful) or the webhook start condition and verify against the reliability ladder. Use when asked to set up, configure, or tune a Grid / Multi-Pair Grid / Signal bot on WunderTrading, or to turn a screening result into an exact grid configuration.
---

# grid-bot — create & configure a WunderTrading Grid bot

The Grid bot is a **web-UI configurator** (not the classic/DCA MCP/REST
surface — `place_strategy_trade`/`edit_trade_strategy` do NOT reach it). You
are the strategy brain: decide the token, grid type, channel, step and size
from market conditions; WunderTrading executes the grid. Full mechanics live
in the `wundertrading` skill's
[references/grid-bot.md](../wundertrading/references/grid-bot.md); the regime
→ grid-type matrix is in [strategy-playbook.md](../wundertrading/references/strategy-playbook.md).

The scripts are in `../wundertrading/scripts/` (this skill's sibling under
`.agents/skills/wundertrading/scripts/`). Run them with the repo's Python 3
(stdlib only).

## Workflow

### 1. Screen — find the candidate

```bash
cd .agents/skills/wundertrading/scripts
python3 universe_screen.py --preset grid-neutral          # mean-reversion (chop/squeeze)
python3 universe_screen.py --preset grid-directional      # trend -> Long/Short grid
python3 universe_screen.py --preset squeeze               # breakout-pending range
```

The ranked output gives symbol, regime, ADX/ATR, real spread (venue L2 book),
and the ATR-derived step. Pick the token whose regime you have a working grid
archetype for — a `chop_high_volatility` name beats a choppy favorite.

### 1b. Backtest & optimize before creating (programmatic, UI-parity)

`wt-backtest.mjs` ports the platform's own client-side backtest engine —
numbers match the configurator's Backtest panel digit-for-digit (verified
2026-09-04). Run it on candidate configs **before** creating anything:

```bash
cd browser-debug
# one run (grid): config = the same JSON you would POST to upsert
node wt-backtest.mjs backtest ../wt-grid-cfg.json
# platform-parity optimize (step 1.2-3.0%) + agent sweeps:
node wt-backtest.mjs optimize ../wt-grid-cfg.json
node wt-backtest.mjs sweep   ../wt-grid-cfg.json --step 0.3:5:0.25 --widths 0.15,0.3 --rank-by pnl
# DCA archetypes:
node wt-backtest.mjs dca       ../wt-dca-cfg.json
node wt-backtest.mjs dca-sweep ../wt-dca-cfg.json --dev 1:4:1 --tp 1:3:1
```

`--rank-by pnl` ranks by realized profit only (avoids the unrealized-heavy
directional bias that `totalResult` favors). The browser must be up
(`node wt.mjs`) — OHLC is fetched through the logged-in page.

### 2. Configure — derive the exact config

```bash
python3 grid_config.py XMR --balance 10000 --max-alloc 0.5
python3 grid_config.py XMR --json | jq
```

This emits, from live OHLCV, **two payloads**:

`grid_bot` (web UI):
- **grid type** from the regime (`chop/squeeze/neutral → Neutral`, `trend_up →
  Long`, `trend_down → Short`).
- **channel** = symmetric ATR band around the mid (High/Low/Mid), width
  `2 × band_atr × ATR%`.
- **Profit per GRID** = `ATR% × step_factor`, clamped, and forced to clear
  `2 × spread` (flag `step_forced_to_2x_spread`).
- **grids** = channel width / step (capped `--min-grids`/`--max-grids`).
- **sizing** = `balance × max_alloc / grids` per trade; worst-case commitment
  is stated (Neutral/hedge also need base coin on the short side — the
  platform's Investment panel is authoritative).
- **risk controls**: Stop Trigger (`stop and close all` spot default) and Pump
  Protection on.

`mcp_strategy` (the **reliable, API-reachable** path — see the note below):
- A ready-to-send `place_strategy_trade` payload (classic or DCA) reusing the
  same regime decision. For `chop`/`neutral` it's a **DCA ladder** with
  `extraOrderDeviation` derived from ATR (`--dca-factor`); for `trend` a
  classic TP-ladder; for `squeeze` a band-edge limit.
- Sizing: `amountPerTrade` divided by the DCA geometric sum so worst-case
  commitment ≤ `--max-alloc`; idempotent `clientId` (auto-generated, or pass
  `--client-id`); `pairCode` auto-resolved from `get_exchange_markets` when
  keys are present (or pass `--pair-code`).

Tune `--band-atr`, `--step-factor`, `--step-min/max` per regime — see
`scan_presets.json` for the preset equivalents.

### 3. Apply — API (reliable), MCP for classic/DCA, or webhook

- **Grid-bot web API (preferred for grids — verified 2026-09-04)** — the
  configurator's XHR surface is fully replayable from the logged-in
  CloakBrowser page:
  ```bash
  node browser-debug/wt-grid.mjs profiles                  # pick profile code (paper: paperTrading=true)
  node browser-debug/wt-grid.mjs analyze HYPERLIQUID_SWAP:159   # market meta + last candle + 30d hi/lo
  node browser-debug/wt-grid.mjs create grid-bot.json      # POST /en/trader/grid_bots/upsert
  node browser-debug/wt-grid.mjs list [--all]              # bots + per-bot action links
  node browser-debug/wt-grid.mjs stop|restart|close-all|delete <botCode>
  # Python parity (httpx with browser, no Node — same fetch-in-page):
  python .agents/skills/wundertrading/scripts/wt_browser.py grid profiles
  python .agents/skills/wundertrading/scripts/wt_browser.py grid create grid-bot.json  # same POST
  python .agents/skills/wundertrading/scripts/wt_browser.py grid list
  # Pure httpx without browser (best-effort, needs fresh cf_clearance):
  python .agents/skills/wundertrading/scripts/wt_httpx.py session GET /en/trader/grid_bots/grid?page=1\&limit=5
  ```
  The payload schema (all fields + enums + grid-line geometry) is in
  [../wundertrading/references/grid-bot.md](../wundertrading/references/grid-bot.md) §9
  and `browser-debug/docs/wt/grid-bot-api.md`. Requires the headful browser up
  (`node browser-debug/wt.mjs` or `wt_browser.py` via CDP) — calls run as fetch-in-page (session +
  CSRF + Cloudflare fingerprint). Paper profiles (`demo-hype` on
  HYPERLIQUID_SWAP) are the safe test target. `wt_browser.py` is the `httpx`+`websockets` Python port of `wt-grid.mjs`.
- **MCP `place_strategy_trade` (preferred when the regime maps to classic/DCA)**
  — submit `mcp_strategy.payload` with `grid_config.py XMR --send --yes
  --balance <n> --profiles <id>[,<id>]`. `--send` alone prints the Phase E
  checklist + payload and **refuses**; `--yes` is the explicit confirmation
  gate. This is the deterministic, idempotent path (`clientId`), with no
  browser/session drift. **There is no Grid-bot MCP tool** — Grid bots go
  through the web API above (or the UI).
- **Web UI** (fallback / human verification): hand `grid_bot` to the headful
  `wt-investigator` agent (wt CLI + bdg with vault-persisted cookies) to fill
  the Grid-bot tab, or enter it manually. Resolve `pairCode` from
  `get_exchange_markets` (never hand-write).
- **Webhook start condition**: the Grid bot can be started/stopped by a
  TradingView-compatible alert (`Entry`/`Exit` alert messages). The unique
  entry code is generated by the UI **at creation time** — it cannot be
  pre-generated. After creating the bot, wire the webhook URL + entry message
  into a tvcli/Pine alert (the tvcli skill's consolidated engine can emit it).

### 4. Verify — Phase E checklist (all must pass before anything goes live)

1. User explicitly confirmed THIS bot: pair, grid type, channel, step, size.
2. `pairCode` resolved from `get_exchange_markets`.
3. Profile active with balance (`get_api_profiles`).
4. Sizing math done: worst-case commitment ≤ 50% of balance.
5. Step clears spread: `profit_per_grid ≥ 2 × spread`.
6. Reliability ladder: paper (demo) → probe 5–10% → scale only after
   `export_strategies_history` passes the bar (≥ 30 samples, PF ≥ 1.3).

### 5. Monitor & adapt

`get_live_strategies` to watch; use the platform's built-in **Backtest /
Optimize** (30-day Profit-per-GRID sweep) and **Profit-Optimized Pairs** (ROI
on a $500/$50 benchmark) to sanity-check the step. Re-run `universe_screen.py`
on regime decay / squeeze breakout / funding extremes and re-derive the config
when the regime changes.

**Price/level watch** (`tvcli watch` — poll triggers on TP / DCA-ladder depth):
a ready spec for the XMR demo is at
[../wundertrading/watch/xmr-demo-dca.json](../wundertrading/watch/xmr-demo-dca.json).
Run it with `./tvcli watch --spec <path> --interval 60` (add `--once --dry-run`
to test; it fires `L1`/`L2` actions the prime-orchestrator can consume).

**Reliability (Phase G):** `python3 reliability.py` exports closed history and
scores each archetype (win rate / PF / expectancy) against the pass/kill bars.

## Safety rails

- Never execute without explicit user confirmation of the exact trade.
- The scripts are read-only (market data + config generation) — they never
  place orders.
- DCA/classic archetypes go through the MCP/REST path; Grid bots go through
  the web UI or webhook. Don't mix them up.

## References

| File | Contents |
|---|---|
| [../wundertrading/references/grid-bot.md](../wundertrading/references/grid-bot.md) | Grid-bot mechanics, grid types, sizing, risk controls, start conditions, webhook path, regime→grid mapping |
| [../wundertrading/references/strategy-playbook.md](../wundertrading/references/strategy-playbook.md) | Regime classification, config matrix, reliability ladder |
| [../wundertrading/scripts/universe_screen.py](../wundertrading/scripts/universe_screen.py) | Full-universe screener with `scan_presets.json` presets |
| [../wundertrading/scripts/grid_config.py](../wundertrading/scripts/grid_config.py) | Per-symbol config generator — web-UI Grid-bot config + API-reachable `place_strategy_trade` DCA/classic payload |
| [../wundertrading/scripts/market_regime.py](../wundertrading/scripts/market_regime.py) | Regime classifier (EMA/ATR/ADX/RSI/BB) |
| [../wundertrading/scripts/reliability.py](../wundertrading/scripts/reliability.py) | Phase G reliability report — export closed history, score archetypes (win rate/PF/expectancy) |
| [../wundertrading/scripts/wt_httpx.py](../wundertrading/scripts/wt_httpx.py) | Pure `httpx` client: HMAC `/open_api` + MCP without browser, `session` best-effort httpx |
| [../wundertrading/scripts/wt_browser.py](../wundertrading/scripts/wt_browser.py) | `httpx` with CloakBrowser (CDP `fetch()`): Python port of `wt-grid.mjs`/`wt-bots.mjs` — reliable grid-bot `create/list/stop` |
| [../wundertrading/watch/xmr-demo-dca.json](../wundertrading/watch/xmr-demo-dca.json) | `tvcli watch` spec for the XMR demo DCA (TP + DCA-ladder triggers) |