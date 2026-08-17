# Layer 2: Type System Deep Dive — Series, Arrays, UDTs, Generics

> **Prerequisite:** Layer 1 (Language Core). Advanced type mechanics for complex scripts.

---

## Series vs Arrays — The Critical Distinction

| Aspect | Series (Implicit) | Array (Explicit) |
|--------|-------------------|------------------|
| **Growth** | Auto-appends each bar | Manual `array.push()` |
| **Indexing** | `var[i]` — `i` bars ago | `array.get(arr, i)` — absolute index |
| **Length** | `bar_index + 1` | `array.size(arr)` |
| **Persistence** | Automatic (all history) | Manual (you manage) |
| **Use For** | Price data, indicators, state | Lookup tables, fixed windows, buffers |

```pine
// Series: implicit history
sma20 = ta.sma(close, 20)  // Each bar: new value appended

// Array: explicit buffer
var float[] window = array.new_float(0)
array.push(window, close)
if array.size(window) > 20
    array.shift(window)  // Remove oldest
// window now holds last 20 closes manually
```

---

## User-Defined Types (UDTs) — `type` Keyword (v5+)

```pine
// Define a struct-like type
type OrderBlock
    float top
    float bottom
    float left_time
    float right_time
    string direction  // "bullish" or "bearish"
    bool  mitigated

// Create instance
ob = OrderBlock.new(top=high, bottom=low, left_time=time, right_time=time, direction="bullish", mitigated=false)

// Access fields
plot(ob.top)
ob.mitigated := true  // Mutate (requires var)
```

**UDTs are value types** — assignment copies. For reference semantics, use arrays/maps of UDTs.

---

## Arrays — Complete API

```pine
// Creation
arr = array.new_float(10)        // Pre-sized (filled with na)
arr = array.new_float(0)         // Empty, dynamic
arr = array.from(1.0, 2.0, 3.0)  // From literals
arr = array.copy(otherArr)       // Shallow copy (== deep for value-type elements)

// Access
first = array.get(arr, 0)
last  = array.get(arr, array.size(arr) - 1)
array.set(arr, 0, 99.0)          // Mutate

// Modification
array.push(arr, 4.0)             // Append
array.unshift(arr, 0.0)          // Prepend
array.insert(arr, 2, 1.5)        // At index
array.remove(arr, 0)             // Remove at index
array.shift(arr)                 // Remove first
array.pop(arr)                   // Remove last
array.clear(arr)                 // Empty

// Queries
len = array.size(arr)
idx = array.indexof(arr, 2.0)    // First index of value (-1 if not found)
contains = array.includes(arr, 2.0)

// Slicing
sub = array.slice(arr, 0, 5)     // [0, 5) — copy

// Iteration
for i = 0 to array.size(arr) - 1
    val = array.get(arr, i)
    
for val in arr                   // v5+ range-for
    // val is copy
```

**Performance:** `array.get/set` O(1). `array.push/pop` amortized O(1). `array.insert/remove/shift/unshift` O(n).

---

## Matrices (v5+)

2D numerical grids for linear algebra, multi-timeframe grids, etc.

```pine
// Creation
mat = matrix.new<float>(3, 3, 0.0)  // 3x3 zeros
mat = matrix.new<float>(2, 3, 0.0) // 2x3 zeros (initial_value must be a constant)

// Access
val = matrix.get(mat, 0, 1)
matrix.set(mat, 1, 1, 42.0)

// Operations
rows = matrix.rows(mat)
cols = matrix.columns(mat)
transposed = matrix.transpose(mat)
summed = matrix.sum(mat)           // Sum all elements
mult = matrix.mult(mat1, mat2)     // Matrix multiplication

// Row/col vectors
row = matrix.get_row(mat, 0)       // array<float>
col = matrix.get_column(mat, 1)    // array<float>
```

---

## Maps (v5+)

Key-value stores with string keys.

```pine
// Creation
m = map.new<string, float>()
// Populate (there is no map.from(); use map.put)
map.put(m, "key1", 1.0)
map.put(m, "key2", 2.0)

// Access
val = map.get(m, "key1")           // Returns na if missing
val = nz(map.get(m, "missing"), 0.0) // Coalesce missing to 0.0

// Modification
map.put(m, "key3", 3.0)
map.remove(m, "key1")
map.clear(m)

// Queries
size = map.size(m)
keys = map.keys(m)                 // array<string>
vals = map.values(m)               // array<float>
contains = map.contains(m, "key1")

// Iteration
for key in map.keys(m)
    val = map.get(m, key)
```

---

## Tuples (v5+)

Multiple return values from functions.

```pine
// Function returning tuple
f_ohlc() => [open, high, low, close]

// Destructuring
[o, h, l, c] = f_ohlc()

// Or ignore with _
[_, h, _, c] = f_ohlc()

// Tuple type annotation
f_complex() => [float, int, string]
    [1.0, 42, "hello"]
```

---

## Type Casting & Conversion

```pine
// Primitive conversions
i = int(3.14)        // 3 (truncates)
f = float(42)        // 42.0
b = bool(1)          // true
s = str.tostring(42) // "42"

// Series ↔ Array (windowing)
// Series to array: collect last N bars
var float[] recent = array.new_float(0)
array.push(recent, close)
if array.size(recent) > 50
    array.shift(recent)

// Array to series: not direct — use array.get in loop
// Or compute on array, assign to series var
var float mySeries = na
mySeries := array.get(recent, array.size(recent) - 1)
```

---

## Reuse Without Generics

Pine Script has **no generic type parameters** — there is no `array<T>` syntax, no `T` type-parameter keyword, and no `na(T)`. To write reusable logic, use the tools that do exist:

- **Tuples** — return multiple values from one function (see above).
- **UDTs** — bundle heterogeneous fields (see User-Defined Types above); constructors accept positional *or* named arguments (`OrderBlock.new(top=high, bottom=low)`).
- **Qualified types** — `const` / `simple` / `series` qualifiers (see Type Inference Rules).
- **Collection of a concrete type** — `array<float>`, `array<myType>`, `matrix<float>`, `map<string, float>`; choose the element type at declaration.

```pine
// Reusable sum over a concrete array type (no generics needed)
f_sumFloat(array<float> arr) =>
    float sum = 0.0
    for val in arr
        sum := sum + val
    sum

floatSum = f_sumFloat(array.from(1.0, 2.0, 3.0))
```

---

## Type Inference Rules

1. **Literal types:** `42` → `int`, `3.14` → `float`, `"x"` → `string`
2. **Built-in calls:** `ta.sma()` → `series float`, `array.new_float()` → `array<float>`
3. **Var initialization:** `var x = 1` → `series int` (persistent)
4. **Function returns:** inferred from return expression
5. **Explicit annotation overrides:** `float x = 1` → `series float` (int promoted)

---

## Common Type Errors

| Error | Cause | Fix |
|-------|-------|-----|
| "Cannot call `array.get` with `series int`" | Index is series, need simple int | Use `int(bar_index)` or loop index |
| "Type mismatch: `float` vs `int`" | Implicit conversion not allowed | Cast: `float(myInt)` |
| "Undeclared identifier" | Variable used before declaration | Declare with `=` first |
| "Cannot assign to `const`" | Tried to reassign non-var | Use `var` keyword |

---

## Next Layer

→ **Layer 3: Execution Model** → `runtime/execution.md`

Covers: historical vs realtime, `var`/`varip`, intrabar updates, `force_overlay`, max bars back, and performance limits.