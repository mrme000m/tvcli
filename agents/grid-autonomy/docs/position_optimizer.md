# Position optimizer — TP / SL / trailing-stop research (WunderTrading grid bots)

Grounding: every field-level fact below comes from the repo's own verified
docs (`browser-debug/docs/wt/grid-bot-api.md` — §"Create / edit" payload
block, lines 78-92, and the coverage matrix §9, lines 191-200: audited
live 2026-09-04 by driving the headful configurator while recording the CDP
network layer) and from the daemon's own profit-exit code
(`daemon.py::_default_take_profit`, `config.yaml::grid_defaults.take_profit_pct`,
`policy/stagnation.py::derive_policy`).

## 1. What WunderTrading supports server-side

The `POST /en/trader/grid_bots/upsert` payload accepts these optional
fields "only when the UI toggles are on" — they are part of the upsert
contract and, once set, are enforced by the **server** on the bot's live
PnL:

| Field(s) | Meaning (verified semantics) |
|---|---|
| `takeProfit` | $ threshold on the bot's **cumulative Total PnL**; when reached, the bot exits (stop per `stopCondition`) |
| `stopLoss` | $ threshold on the same **cumulative Total PnL** (negative direction) |
| `stopLossPnlCompareType: "total"` | the compare-type for both $ fields — "total" = cumulative Total PnL (realized + unrealized), not realized-only |
| `trailingStopActivation` | % of cumulative Total PnL at which a trailing stop arms (e.g. 5 = arms once +5% total PnL) |
| `trailingStopExecute` | % give-back from the cumulative-PnL high that triggers the exit (e.g. 2 = exit when PnL falls 2% off its peak) |
| `trailingStopPnlCompareType: "total"` | trailing measured on cumulative Total PnL |
| `strategyProfitCondition: "trailing_stop"` | **positions** trailing stop: each open grid line closes after a pullback of **30% of the grid step** from that line's local high (platform-fixed, not configurable) |
| `strategyStopLossFixedPercentRatio` | **positions** stop loss: each line closes at a fixed adverse ratio (e.g. 0.05 = 5% below entry) |

Key distinction: the `takeProfit`/`stopLoss`/`trailingStop*` family is
**bot-level** (whole-book exits against cumulative PnL), while
`strategyProfitCondition`/`strategyStopLossFixedPercentRatio` is
**line-level** (each grid position manages its own exit, freeing capital
back into the grid cycle).

*Re-verified live in the UI 2026-09-06* (Edit-Grid-bot form, Exit section —
read-only toggling, nothing submitted): the Take Profit / Stop Loss inputs
render with `$` suffixes and the trailing activation/execute inputs also
render `$`-suffixed (consistent with the `*PnlCompareType: "total"` family
being thresholds on cumulative Total PnL in the profit currency — treat
the trailing values as $ amounts there, not the bare % reading of
grid-bot-api.md's example `5`/`2`); Positions stop loss renders `%`; the
bot-list `resource` echo confirms the field names, the
`stopLossPnlCompareType`/`trailingStopPnlCompareType` `"total"` defaults,
and `strategyProfitCondition: "take_profit"` as the OFF state of positions
trailing (clearing it = `"take_profit"`, not null).

## 2. What the daemon does TODAY

- **Profit exit is daemon-side only.** `_default_take_profit` (daemon.py
  ~:2130) computes `grid_defaults.take_profit_pct` (config default 0.10) ×
  slot balance as the per-bot USD target; the watch loop stops the bot at
  profit only when cumulative total PnL (realized + mark PnL of open
  lines) ≥ target **AND** every open line is ≥ 0 (never close at a loss,
  fail-closed when per-line state is unknown).
- The comment in `commit_deploy` (~:1490) saying WT's native takeProfit
  is "not enforced server-side" is **stale** — the fields ARE in the
  verified upsert contract (§1 above). `compute_upsert` in
  `execution/grid_adapter.py` now accepts them as optional kwargs and
  injects them into the payload only when provided.
- **adjust_bot is recenter-only** (one edit per 6h, geometry only) and
  there is **no loss-side exit at all** — the never-close-at-a-loss rule.

## 3. When each exit type increases profit

Expected-fill math (from `policy/stagnation.py::derive_policy`):
`expected_fills_per_24h` = naive grid-cross simulation over 300×1h
candles (a fill is counted when consecutive closes cross any grid line),
and the raw grid profit identity is
**profit ≈ fills × (step_pct − round-trip fee)** — where round-trip fee
is `2 × spread + exchange/builder fees` (the same `fee_floor` the
adapter floors `profit_per_grid_pct` at). Anything that raises
fills/day or per-fill capture, without lowering the other, raises profit.

### Fixed takeProfit (`takeProfit`, $ on cumulative Total PnL)
- **Effect on profit: ~neutral by construction, positive on reliability.**
  The daemon already exits at the same threshold via its own polling; a
  server-side fixed TP locks the exit in without depending on the
  daemon's watch cadence, the :8799 process being up, or observe
  failures. It removes **polling risk** (the gap between reaching target
  and the daemon noticing — during which PnL can round-trip), not profit.
- No downside vs the daemon exit **except** it removes the daemon's
  all-lines-≥0 refinement: the server will stop-and-close-all at the $ TP
  even if one line would realize a small loss. In practice reaching +10%
  cumulative total PnL almost always implies the book is green overall;
  the per-line rule only matters for the last few bps.

### Cumulative trailing stop (`trailingStopActivation` + `trailingStopExecute`, % on cumulative Total PnL)
- **Helps in steady trend regimes.** A fixed TP caps the harvest at
  exactly the target. A trailing stop arms at activation (e.g. +5%) and
  then rides the cumulative-PnL peak, exiting only after a give-back
  (e.g. 2%). When the token keeps trending the grid keeps filling and the
  trailing exit captures continuation **beyond** the fixed target.
- **Risks giving back profit in chop.** If the execute gap is too tight
  relative to normal grid-PnL noise, the stop triggers on every minor
  pullback and exits at "peak − gap" repeatedly below what a fixed TP
  would have banked. Rule of thumb: execute gap ≥ the grid step% (the
  PnL swings of a grid bot are of order one step), activation ≥ 2× gap.
- Net positive when the regime evidence says trend (the same
  archetype/regime signal the ticket already carries) and the expected
  give-back < the expected continuation.

### Positions trailing (`strategyProfitCondition: "trailing_stop"`, 30% of grid step per line)
- Each line closes after a **30%-of-step pullback from its local high**
  instead of waiting for the full-step crossing to the next line. Effects:
  - **More fills/day**: capital cycles back in faster — each early-closed
    line frees notional for the next crossing. In the expected-fill
    model this raises the effective fills coefficient.
  - **Smaller per-fill capture**: 30% of step instead of ~100% of step
    minus fee. Per-fill profit becomes ~0.3 × step − round-trip fee,
    which is only positive when **step ≥ ~2× the round-trip fee**
    (0.3·step > fee ⇒ step > fee/0.3).
- **Net positive in mean-reversion (chop/neutral) regimes** where price
  oscillates without completing full step crossings — the 30% pullback
  harvests moves a full-step exit would miss entirely.
- **Net negative in strong trends**: full-step capture (step − fee)
  pays more than 0.3·step − fee per cycle when the crossings actually
  complete; early exits also leave directional profit on the table.

### Stop loss (`stopLoss`, `strategyStopLossFixedPercentRatio`)
- Conflicts directly with the **never-close-at-a-loss rule** (the daemon
  stops at profit only, holds or recenters losers). A server-side SL
  realizes losses the daemon's design explicitly refuses to take.
- Only defensible as a **wide operator-opt-in risk cap** — e.g.
  ≥ 15% of slot balance — to bound tail risk (exchange incident, token
  collapse) for operators who value survival over the loss-avoidance
  rule. **Recommendation: grid bots keep SL off by default**
  (`stop_loss_enabled: false`).

## 4. Recommendation rules for the position-optimizer harness

The daemon's position optimizer should attach server-side exits as
follows (knobs live in the `position_optimizer:` config section — the
shipped `position_optimizer.py` engine reads them):

- **add-take-profit** when the bot's `realized_pnl ≥ 60%` of its target
  (`take_profit_usd`): most of the target is already banked, so lock the
  remaining run-up in server-side and drop the polling dependency.
- **add cumulative trailing** when cumulative PnL ≥ activation AND
  realized ratio ≥ 0.3 (i.e. the profit is materially realized, not just
  mark-to-market that a pullback would erase):
  `takeProfit = target`, `trailingStopActivation = trailing_activation_pct`,
  `trailingStopExecute = trailing_execute_pct`.
- **positions trailing** for chop/neutral regimes with healthy
  expected fills (`expected_fills_per_24h` at or above the stagnation
  floor) and step ≥ 2× round-trip fee — capital cycles faster at
  acceptable per-fill capture. Skip it in trend regimes.
- **stop loss** only when operator-enabled AND wide: `stopLoss` ≥ 15% of
  slot balance (or `strategyStopLossFixedPercentRatio` ≥ 0.15), else
  never.

Config knobs (defaults recommended):

```yaml
position_optimizer:            # as shipped in config.yaml
  take_profit_pct: 0.10            # × slot balance = USD profit-exit target
  trailing_activation_pct: 5.0     # cumulative-PnL % where trailing arms
  trailing_execute_pct: 2.0        # give-back % that executes the trailing exit
  positions_trailing: true         # strategyProfitCondition: trailing_stop
  stop_loss_enabled: false         # never by default (never-close-at-a-loss)
  backtest_validate: false         # OFF by default (see §4c)
  backtest_candles: 300            # 1h-candle lookback of the validation window
  backtest_min_edge_pct: 0.5       # required locked-PnL edge, % of slot balance
```

## 4b. Exit awareness + the opt-in set_exits apply path (2026-09-08)

`wtclient.GridClient.set_exits` (live-verified 2026-09-07) edits ONLY the
exit/risk fields of an ACTIVE grid bot through the upsert path — the bot
is **not** stopped or restarted; the edit applies live
(`wt_library.grid_set_exits` is the never-raise daemon wrapper, dry-run
by default). The position optimizer now uses it in two ways:

- **Exit awareness (always on, advisory-side):** `current_exits(bot)`
  extracts the bot's CURRENT exit config from the enriched grid_list
  fields the observe layer projects (`observed.exits` → `bot.exits`,
  mirrored into the console fleet cards). `evaluate_exits` /
  `make_recommendation` suppress an `add-take-profit` / `add-trailing` /
  `add-stop-loss` rec when the corresponding field is already set and
  materially matches the target (within `EXIT_MATCH_TOL` = 10%); a set
  but materially different value still escalates per the normal
  priority. No more "add take-profit" recs for bots that already have
  one.
- **Opt-in apply (apply: true only):** with
  `position_optimizer.apply: true` the daemon injects
  `wt_library.grid_set_exits` (dry-run gated by the daemon's own
  live-paper flag) as the engine's `apply_fn` seam. An exit-add rec —
  and ONLY an exit-add rec, geometry still goes through the grid-edit
  path with its own gates — is then executed live, the outcome recorded
  on the recommendation (`applied` / `apply_error` / `applied_at`) and
  journaled as `position-optimizer-applied` (success, carrying bot code
  + action + exit kwargs + outcome) or `position-optimizer-error`
  (failure). `max_apply_per_day` counts SUCCESSFUL applications that
  actually executed — a dry-run daemon's ok envelope is journaled as a
  rehearsal but does not burn the cap. The engine default stays
  `apply: false` (advisory only, zero WT mutation); the config comment
  documents that `apply: true` now covers both geometry AND exit edits.

Kwarg mapping (engine target → `set_exits`): `take_profit_usd →
take_profit`; `trailing_activation_pct`/`trailing_execute_pct →
trailing_activation`/`trailing_execute` (+ `positions_trailing_stop:
true` when the engine flagged per-position trailing and the bot is not
already in `strategyProfitCondition: "trailing_stop"`); `stop_loss_usd
→ stop_loss` as a POSITIVE magnitude with `pnl_compare_type: "total"`
(the engine models the risk cap as a negative USD level; WT stores the
threshold as a positive $ compared against cumulative PnL — same
convention as `grid_adapter.compute_upsert`).

## 4c. Opt-in backtest validation of exit-add recs (2026-09-08)

`wtclient.backtest` is the pure-Python port of the configurator's
client-side grid backtest engine (digit-for-digit parity with
`wt-backtest.mjs` — §5). With `position_optimizer.backtest_validate:
true` the engine runs an extra validation stage before an
`add-take-profit` / `add-trailing` / `add-stop-loss` rec is emitted —
and before the opt-in apply path could execute it:

1. **Same candles, same geometry.** The candidate exit config
   (`current exits + the recommended one`, built from the SAME
   `exit_edit_kwargs` the apply path would send) and the current-exit
   baseline (identical grid geometry, the bot's exits as configured
   today) are both replayed through the wtclient grid backtest engine
   over the same recent candles (`backtest_candles` 1h bars, default
   300 — reused from the analysis fetch when it already covers the
   window, else one extra pull through the same injected
   `fetch_candles_fn` chain; the daemon's `_po_backtest` seam runs the
   PURE engine on those candles and NEVER touches
   `GridClient.backtest`'s `:2087` network fetch, so free-tier /
   geo-block behavior stays consistent).
2. **Exit overlay.** The engine itself simulates the grid only (WT
   parity: the UI's Backtest button likewise ignores exit fields in
   the payload). `exit_overlay_pnl` models the exit profile ON TOP of
   the engine's trade ledger: the ordered trade list is a complete
   position book (opens record their level, closes the crossed
   level), so cumulative TOTAL PnL is available at every event — and
   WT compares its USD exit thresholds against exactly that
   (`$ thresholds compare against cumulative TOTAL PnL`).
   `takeProfitUsd` locks when total ≥ target; `stopLossUsd` (positive,
   "total" compare) closes everything near the cap;
   `trailingActivationPct`/`trailingExecutePct` (percent of slot
   balance, the same base as `evaluate_exits`) arm and execute on the
   post-arm give-back. Exits are evaluated at trade events, marked at
   the trade price — intra-candle extremes between trades can trip a
   real exit slightly earlier (documented approximation).
3. **Locked-in PnL comparison.** Both sides are compared on the PnL
   LOCKED IN by the end of the window: the total at the exit trigger
   when one fired (closing everything converts unrealized into
   realized), else the window wound down at the final mark (realized +
   marked unrealized ≈ the engine's `totalResultFiat`). That
   convention is what makes the gate meaningful: a take-profit that
   fires before the window reverses locks MORE than a baseline that
   rides the reversal holding underwater positions, and loses to a
   baseline that keeps banking grid steps — exactly the trade-off an
   exit-add rec must justify.
4. **Verdict.** The rec survives only when the candidate's locked-in
   PnL beats the baseline by `backtest_min_edge_pct`% of slot balance
   (default 0.5% → $0.50 on a $100 slot). On a veto the rec is
   downgraded to `keep` (`expected_delta_pct` zeroed, `exit_kwargs`
   stripped) and ONE `position-optimizer-backtest-veto` journal event
   carries BOTH backtest results (compact summaries + the overlay
   verdicts). A pass is journaled as `position-optimizer-backtest` and
   stamped on the rec as `rec["backtest"]` (`validated` / `passed` /
   `edge_usd` / `margin_usd` / both result compacts).

**Fail-open by design:** the stage is OFF by default
(`backtest_validate: false`); a missing backtest seam (wtclient not
importable → the daemon injects `backtest_fn=None`) or ANY engine
error skips validation and emits the advisory rec unvalidated — the
daemon always boots, and the gate never blocks recs on infrastructure
failure. Only a completed comparison with an insufficient edge vetoes.
`apply` semantics are unchanged and independent: a vetoed rec is never
applied (validation runs before the apply path), and an unvalidated
rec is exactly the pre-stage advisory behavior.

## 5. How to validate

`browser-debug/wt-backtest.mjs` is the verbatim Node port of the
configurator's client-side backtest engine (digit-for-digit parity
verified on HYPE-USDC). It takes the same JSON you would POST to
`…/grid_bots/upsert` as its config:

```bash
node browser-debug/wt-backtest.mjs backtest cfg_fixed_tp.json   # 30d history
node browser-debug/wt-backtest.mjs backtest cfg_trailing.json   # same history
```

- When the browser is up (the engine fetches `:2087/ohlc` history
  in-page; raw HTTP is Cloudflare-403), run the **same 30d history**
  with (a) a fixed-TP config vs (b) a trailing config
  (`trailingStopActivation`/`trailingStopExecute` set) and compare
  `totalResult` / realized `pnl`.
- Use `sweep --step 0.2:5:0.1` first to pin the step (the engine's
  fee model is 0.2% per closed position) — exit-type comparison is only
  meaningful at the same geometry.
- Also spot-check one choppy and one trending window manually: trailing
  should win on the trend window, fixed TP (or plain daemon exit) on the
  choppy one — that asymmetry is the regime gate in §4.
- In-process alternative: with `position_optimizer.backtest_validate:
  true` (§4c) the engine now runs exactly this comparison itself — the
  same pure engine (the `wtclient.backtest` port), the same candles
  from the daemon's own fetch chain, and a locked-in-PnL verdict per
  exit-add rec — no browser, no `:2087` fetch, vetoed recs downgraded
  to `keep` with both results journaled.
