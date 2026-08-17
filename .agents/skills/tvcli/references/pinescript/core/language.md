# Layer 1: Language Core — Variables, Types, Control Flow

> **Prerequisite:** Layer 0 (Essentials). This covers the Pine Script v5 language fundamentals.

---

## Execution Model: Bar-by-Bar

Pine Script executes **once per bar** (historical + realtime). On each bar:
1. Inputs read
2. Variables updated (series grow by one element)
3. All expressions evaluated
4. Plots/alerts emitted

```pine
//@version=6
indicator("Bar Counter")
var int count = 0       // 'var' = initialize ONCE, then persists
count := count + 1      // ':=' reassignment (required for var)
plot(count, "Bars Processed")
```

**Key insight:** `var` variables keep state across bars. Regular variables (`=`) reset each bar.

---

## Type System (v5/v6)

### Primitive Types
| Type | Literal Example | Notes |
|------|-----------------|-------|
| `int` | `42`, `-1` | 64-bit signed |
| `float` | `3.14`, `-0.5` | 64-bit float |
| `bool` | `true`, `false` | |
| `string` | `"hello"`, `'world'` | Double or single quotes |
| `color` | `color.red`, `#FF0000`, `color.new(255,0,0,50)` | Transparency 0-100 |

### Series Types (Time-Series)
Every variable is a **series** — an array indexed by bar.
```pine
float x = close          // series float (one value per bar)
int   y = bar_index      // series int
bool  z = close > open   // series bool
```
**Series ≠ Array.** Series auto-grow; arrays (`array.new_float()`) are fixed-size containers you manage.

### Special Types
| Type | Use Case |
|------|----------|
| `label`, `line`, `box`, `polyline` | Drawings (managed via `label.new()`, `line.new()`) |
| `array<int>`, `array<float>`, `array<color>`, etc. | Dynamic arrays |
| `matrix<float>` | 2D numerical data |
| `map<string, float>` | Key-value stores |
| `table` | Dashboard-style displays |

---

## Variable Declaration Syntax

```pine
// Type inference (most common)
myVar = 42              // int
myVar = 3.14            // float
myVar = "text"          // string

// Explicit type (when inference fails or for clarity)
int myVar = 42
float myVar = 3.14
string myVar = "text"

// 'var' = persistent across bars (initialized once)
var int persistentCounter = 0
var float[] myArray = array.new_float(0)

// 'varip' = persistent + updates on realtime ticks (intrabar)
varip float intrabarHigh = high
```

---

## Reassignment: `:=` vs `=`

| Operator | Meaning | Use With |
|----------|---------|----------|
| `=` | Declaration + assignment | First occurrence only |
| `:=` | Reassignment | `var` variables, or after declaration |

```pine
x = 1           // declare
x := x + 1      // reassign (OK)
y := 2          // ERROR: y not declared with '=' first
var z = 0
z := z + 1      // OK: var declared with =
```

---

## Control Flow

### If-Else (Expression Form — Returns Value)
```pine
// Returns a value — use for assignment
trend = close > open ? "bull" : "bear"
color = close > open ? color.green : color.red

// Block form (no return)
if close > open
    label.new(bar_index, high, "Up")
else
    label.new(bar_index, low, "Down")
```

### Switch (v5+)
```pine
signal = switch
    close > ta.sma(close, 200) => "Long"
    close < ta.sma(close, 200) => "Short"
    => "Neutral"
```

### Loops
```pine
// For loop (range)
sum = 0.0
for i = 1 to 10
    sum := sum + close[i]

// For-in (array iteration)
arr = array.from(1, 2, 3, 4, 5)
sum = 0.0
for value in arr
    sum := sum + value

// While (rare, use carefully — infinite loop = script timeout)
i = 0
while i < 10
    i := i + 1
```

**Loop limits:** Loops are bounded by execution time — 500ms per loop per bar plus a total script budget. A tight loop can time out well before any fixed iteration count, so keep loops modest and prefer built-ins (`ta.sma`, etc.).

---

## Functions

### User-Defined Functions
```pine
// Simple function
f_add(a, b) => a + b

// Multi-statement (block body)
f_atr_custom(len) =>
    tr = ta.tr(true)
    ta.rma(tr, len)

// With type annotations (v5+)
f_weighted_avg(float src, int len, float weight) => float
    sum = 0.0
    wSum = 0.0
    for i = 0 to len - 1
        w = weight ^ i
        sum := sum + src[i] * w
        wSum := wSum + w
    sum / wSum

// Call
myAtr = f_atr_custom(14)
avg = f_weighted_avg(close, 10, 0.9)
```

**Functions are pure** — no side effects (no `plot`, `label.new`, `alertcondition` inside). Use for calculations only.

### Built-in Namespaces (v5+)
| Namespace | Contents |
|-----------|----------|
| `ta.*` | Technical analysis (all indicators) |
| `math.*` | Math functions |
| `str.*` | String manipulation |
| `array.*` | Array operations |
| `matrix.*` | Matrix operations |
| `map.*` | Map operations |
| `table.*` | Table display |
| `request.*` | External data (security, currency, earnings) |
| `session.*` | Trading sessions |
| `timeframe.*` | Timeframe utilities |
| `chart.*` | Chart properties |
| `syminfo.*` | Symbol info |

---

## Historical Referencing `[]`

Access past bar values:
```pine
close[1]        // Previous bar close
close[10]       // 10 bars ago
high[bar_index] // First bar (invalid if bar_index > length)
```

**Bounds:** `close[bar_index]` on bar 0 = runtime error. Always check:
```pine
// Safe pattern
myVal = bar_index >= 10 ? close[10] : na
```

---

## `na` (Not Available) Handling

```pine
// Check for na
if na(myVar)
    // handle

// Coalesce: first non-na value
clean = nz(myVar, 0)        // Replace na with 0
clean = nz(myVar, close)    // Replace with close

// Force float from int series
floatVal = float(intSeries)
```

---

## Next Layer

→ **Layer 2: Type System Deep Dive** → `core/types.md`

Covers: series vs arrays, user-defined types, tuples, type casting, and generic functions.