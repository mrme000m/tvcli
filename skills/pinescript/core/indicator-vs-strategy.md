# Layer 4: Indicators vs Strategies — Architecture Decision

> **Prerequisite:** Layer 3 (Execution Model). Choose the right tool for your use case.

---

## Quick Decision Matrix

| Goal | Use Case | Declaration |
|------|----------|-------------|
| **Visual analysis, signals, levels, alerts** | Show price levels, crossovers, dynamic support/resistance | `indicator("Name", overlay=true)` |
| **Backtest with simulated trades** | Performance metrics, equity curve, trade analysis | `strategy("Name", overlay=true, initial_capital=10000)` |
| **Paper trading / forward test** | Simulated orders without persistency | `strategy("Name", overlay=true, process_orders_on_close=true)` |
| **Both visuals + backtest** | Chart visuals + simulated trade lifecycle | `strategy("Name", overlay=true, ...)` (can plot) |
| **Indicator + Strategy pair** | Visual signals + separate backtest | Two scripts: `indicator(...)` + `strategy(...)` |

---

## Strategy Declaration — Full Parameter Reference

```pine
//@version=6
strategy("My Strategy", overlay=true, 
    initial_capital=10000,          // Starting cash (float/int)
    default_qty_type=strategy.percent_of_balance,  // or strategy.fixed
    default_qty_value=10,           // 10% or 10.0 units
    commission_type=strategy.commission.percent,   // or strategy.commission.fixed
    commission_value=0.04,          // 4% or 0.04 per trade
    currency=currency.USD,          // Account currency
    currency_align=currency.AUTO,   // Align price scale
    currency_cross=currency.AUTO,   // Cross pair handling
    process_orders_on_close=false,  // Trade execution timing
    pyramiding=0,                   // 0=disallow, >0=allow re-entries
    entry_exits_per_bar_alert=false // Alert per entry/exit combo
)
```

### Key Parameters Explained

| Parameter | Purpose | Typical Values |
|-----------|---------|----------------|
| `initial_capital` | Starting cash for backtest | `10000`, `100000` |
| `default_qty_type` | Position sizing method | `strategy.percent_of_balance`, `strategy.fixed` |
| `default_qty_value` | Size per trade | Percent (0-100) or fixed quantity |
| `commission_type` | Fee model | `strategy.commission.percent`, `strategy.commission.fixed` |
| `commission_value` | Fee rate | `0.04` = 4%, `5.0` = $5 flat |
| `process_orders_on_close` | Execute at bar close vs intrabar | `true` = faster, `false` = per-tick |
| `pyramiding` | Allow multiple simultaneous entries | `0` = no, `1+` = allow N entries |
| `currency` | Account currency | `currency.USD`, `currency.BTC` |

---

## Order Flow & Trade Lifecycle

### Entry Signals

```pine
// Long entry on EMA crossover
longCondition = ta.crossover(ta.sma(close, 9), ta.sma(close, 21))

// Enter long — strategy.entry syntax
strategy.entry("Long Entry", strategy.long)

// Or with limit/market execution
strategy.entry("Long Limit", strategy.long, limit=close * 0.99)
```

### Exit Signals

```pine
// Take profit / stop loss as separate exits
strategy.exit("Take Profit", "Long Entry", limit=high * 1.02)
strategy.exit("Stop Loss", "Long Entry", stop=low * 0.98)

// Trail stop — moves with price
strategy.exit("Trail Stop", "Long Entry", trail_price=2.0, trail_points=true)

// Multiple exits from one entry
strategy.exit("TP1", "Long Entry", limit=high * 1.01)  // First TP
strategy.exit("TP2", "Long Entry", trail_price=1.5)   // Trail on remainder
```

### Close Signals

```pine
// Close partial position
strategy.close("Long Entry", comment="Partial close")

// Close entire position
strategy.close("Long Entry", comment="Full close")
```

### Order Types Supported

| Order Type | Pine Syntax | When Used |
|------------|-------------|-----------|
| Market | `strategy.entry("Name", strategy.long)` | Instant execution at next bar open |
| Limit | `strategy.entry("Name", strategy.long, limit=price)` | Execute at better price or better |
| Stop | `strategy.entry("Name", strategy.long, stop=price)` | Trigger at price, then market |
| Market If Touched | `strategy.order()` with `ordertype.strategy.order.moc` | Opening auction |
| Fill or Kill | Via `strategy.order()` with `time_in_force` | Immediate or cancellation |

---

## `strategy.*` — Performance & Metrics

Available strategy-report fields (accessible via `tvcli run --signals --json` or WebSocket `strategyReport`):

```pine
// Equity curve & trade stats (automatically computed)
beginning_equity          // Starting capital
ending_equity             // Final equity after backtest
gross_profit              // Sum of all winning trades' profits
gross_loss                // Sum of all losing trades' losses
net_profit                // gross_profit - gross_loss - commissions - fees
profit_factor             // gross_profit / (gross_loss + commissions + fees)
expected_value            // Average profit per trade
trade_count               // Total number of trades
winning trades            // Count of profitable trades
losing trades             // Count of unprofitable trades
win_rate                  // winning trades / trade_count × 100%
average trade             // net_profit / trade_count
max drawdown              // Peak-to-trough decline in equity
max drawdown duration     // Bars of consecutive losses
sharpe_ratio              // Risk-adjusted return (internal calc)
sortino_ratio             // Downside-deviation-adjusted return
currency                // Account currency
initial_capital           // As declared
final_capital             // ending_equity after all adjustments
```

### Accessing Strategy Report Data

```pine
// These are built-in — no extra code needed
// Access via tvcli or WebSocket output

// Common pattern: log key metrics on last bar
if barstate.islast
    // tvcli extracts these from strategyReport automatically
    // WebSocket JSON includes: strategyReport.{fields}
```

---

## `process_orders_on_close` vs Default

| Mode | Behavior | When to Use |
|------|----------|-------------|
| **Default (`false`)** | Orders execute on bar open; intrabar `varip` updates occur | Most strategies — standard backtest behavior |
| **`process_orders_on_close=true`** | Orders execute after bar close; intrabar `varip` still updates | High-frequency strategies; need tick-level `varip` with end-of-bar execution |

**Critical:** `process_orders_on_close` only affects order execution timing. `varip` variables still update on every realtime tick regardless.

---

## Pyramiding & Multiple Entries

```pine
// Allow up to 3 simultaneous long entries
strategy("Pyramid Strategy", overlay=true, pyramiding=3)

// Entry only if we have fewer than 3 open positions
longCondition = ta.crossover(ta.sma(close, 9), ta.sma(close, 21))

// Check current position count
currentLongs = strategy.position_size

// Pyramid: only enter if below limit
if longCondition and strategy.position_size < 3
    strategy.entry("Pyramid Long", strategy.long)
    // Each entry adds to same position; PnL aggregates
```

**Position Sizing with Pyramiding:**
- Each `strategy.entry` adds to the aggregate position
- `strategy.position_size` reflects total units
- `strategy.position_avg_price` = weighted average of all entries

---

## Hybrid: Indicator + Strategy Pattern

**Best for:** Visual signals ON chart + separate backtest with trade metrics.

```pine
// ============ Script 1: INDICATOR (visuals only) ============
//@version=6
indicator("My Signals", overlay=true)
fast = ta.sma(close, 9)
slow = ta.sma(close, 21)
plot(fast, "Fast", color=color.green)
plot(slow, "Slow", color=color.red)

// Visual crossover arrows
plotshape(fast > slow ? ta.crossover(fast, slow) : false, 
    "Bullish Crossover", shape.triangleup, location.belowbar, color.green)

// ============ Script 2: STRATEGY (backtest only) ============
//@version=6
strategy("My Strategy", overlay=true, initial_capital=10000)
longCondition = ta.crossover(ta.sma(close, 9), ta.sma(close, 21))
strategy.entry("Long", strategy.long, when=longCondition)
// Full backtest metrics auto-generated
```

**Advantages:**
- No conflict between plot overload and trade execution
- Separate Pine IDs — run independently
- Strategy report from Script 2, charts from Script 1

---

## Common Strategy Anti-Patterns

| Anti-Pattern | Why It Fails | Fix |
|-------------|-------------|-----|
| `strategy.entry` inside `for` loop | O(n) orders per bar → exponential position growth | Gate with `strategy.position_size` check; use `once` pattern |
| `strategy.exit` with dynamic `limit`/`stop` via series | Backtest may compute different values per bar | Use `strategy.order` with `ordertype=strategy.order.limit` for per-trade precision |
| `pyramiding` without `strategy.position_size` guard | Unlimited position accumulation | Explicit `if strategy.position_size < N` guard |
| `calc_on_every_tick` + `strategy.exit` with series args | Exit prices may not match intended logic | Pre-compute exit levels before tick loop, or use `strategy.close` |
| Forgetting `commission_type`/`commission_value` | Trades look profitable but omit fees | Always declare commission params if trading |

---

## Next Layer

→ **Layer 5: Inputs & Settings** → `core/inputs.md`

Covers: all input functions, conditional visibility, YAML generation for automation, and best practices.