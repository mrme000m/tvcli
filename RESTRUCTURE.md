# tvcli Restructuring Proposal

## Current state (8.7k LoC)

```
cmd/tvcli/main.go          2196  ← monolith: 12 commands + tier limits + OHLCV + meta store + flags + help
internal/config/config.go   153
pkg/pinefacade/             842  (client 545, parser 234, types 65)
pkg/runner/                 977  (runner 452, multirun 309, persistent 218)
pkg/dynparse/               347
pkg/schema/                1040  (schema 745, semantic 294)
pkg/tradingview/           1522  (client 334, chart 296, study 339, indicator 337, compressed 140, protocol 82)
pkg/extract/               1158  (extract 841, extract_schema 319)
```

## Problems

1. **`cmd/tvcli/main.go` is a 2196-line monolith.** All 12 subcommands, tier limits, OHLCV fetch, meta-store, flag parser, help text, and slugify helpers live in one `package main`. Hard to extend (adding a command = editing the giant switch + appending a 200-line func), hard to test (no command is importable), hard to reuse (the `cmdRun` orchestration can't be called from another binary).

2. **No command abstraction.** `main()` is a `switch cmd` over string literals. No `Command` interface, no per-command file, no per-command flag set — every command shares one `parseFlags` and one `flags map[string]string`.

3. **Layering violations.** `cmd/tvcli` imports `pkg/runner`, `pkg/pinefacade`, `pkg/tradingview` directly and wires them together in `cmdRun` (444 lines) and `cmdSync` (210 lines). There is no service/use-case layer; business orchestration lives in the CLI binary. Any second binary (TUI, HTTP server, daemon, tests) has to copy-paste `cmdRun`.

4. **Concerns mixed inside packages.**
   - `pkg/tradingview/client.go` does WS *and* HTTP token fetch *and* auth-cookie generation.
   - `pkg/tradingview/indicator.go` defines `InputDef` while `pkg/schema/schema.go` defines a *different* `InputDef`-like `InputDef` — two input models for the same concept.
   - `pkg/extract/extract.go` (841 lines) reimplements classification heuristics that overlap with `pkg/schema/semantic.go`.

5. **Duplicated classification.** `extract.classify()` and `schema.classifyFromMetadata()` both score plots by keyword. Two sources of truth drift apart.

6. **No tier/config as data.** `tiers` map is a global var in `main.go`. It belongs with config.

7. **Dead coupling direction.** `pkg/runner/persistent.go` imports `pkg/tradingview` — fine — but `pkg/tradingview/indicator.go` imports `pkg/schema`, and `pkg/extract` imports both `pkg/schema` and `pkg/dynparse`, while `pkg/runner` imports `pkg/extract`, `pkg/schema`, `pkg/dynparse`. The dependency graph is acyclic but wide; `runner` is really an *orchestrator* disguised as a leaf package.

8. **No tests for the big risk areas.** `extract.go` (841 lines) has a 52-line test. `runner.go` (452) has none. `main.go` (2196) has none. The CLI surface is untested.

9. **`pkg/dynparse` and `pkg/extract` overlap.** Both walk typed bars and produce "signals". `dynparse` produces `TypedBar[]`; `extract` consumes it but also re-derives stats. One pipeline, two packages, fuzzy boundary.

## Proposed structure

```
go/
├── cmd/
│   └── tvcli/
│       └── main.go                  ~30 lines: wire rootCmd, Execute()
├── internal/
│   ├── config/                       env + .env + tiers + cookies  (existing, +tiers.go)
│   ├── cli/                          command framework
│   │   ├── command.go                Command interface + Env + flag parsing
│   │   └── flags.go                  typed flag set (string/int/bool/enum)
│   └── metadb/                       .tv-meta.json load/save (extracted from main.go)
├── pkg/
│   ├── pinefacade/                   HTTP API (keep as-is, maybe split client.go)
│   │   ├── client.go                 CRUD: get/save/delete/compile  (~250)
│   │   ├── search.go                 search + publist + top          (~150)
│   │   ├── compile.go                compile + error parsing          (~150)
│   │   ├── parser.go                 Pine source input parser
│   │   └── types.go
│   ├── tradingview/
│   │   ├── ws/                       WebSocket transport
│   │   │   ├── client.go             WS client only
│   │   │   ├── protocol.go           ~m~ framing
│   │   │   └── compressed.go         gzip/zip/flate decoding
│   │   ├── auth/                     HTTP token + cookie helpers (extracted from client.go)
│   │   ├── chart/                    ChartSession
│   │   ├── study/                    ChartStudy + PineIndicator + BuiltinIndicator
│   │   └── quote/                    QuoteSession (if present)
│   ├── schema/                       keep; drop duplicate InputDef, re-export tradingview one
│   ├── pipeline/                     dynparse + extract merged
│   │   ├── dynparse.go               raw periods → TypedBar[]
│   │   ├── classify.go               single source of truth (was schema/semantic.go + extract/classify)
│   │   ├── extract.go                TypedBar[] → Signals
│   │   └── strategy.go               strategy-report decoding
│   └── runner/
│       ├── runner.go                 RunOptions + one-shot run
│       ├── persistent.go             persistent WS
│       └── multirun.go               sweep
├── internal/service/                use-case layer between CLI and pkg/*
│   ├── run.go                        RunScript(ctx, RunRequest) → RunResult  (was cmdRun)
│   ├── fetch.go                     FetchOHLCV(...)                   (was cmdFetch)
│   ├── sync.go                      Sync(...)                         (was cmdSync)
│   └── search.go                    Search/List/PubList/Top          (thin wrapper over pinefacade)
├── internal/cmd/                     one file per subcommand
│   ├── list.go   create.go  pull.go  push.go  delete.go
│   ├── search.go publist.go top.go  compile.go
│   ├── run.go    fetch.go   sync.go  help.go
│   └── root.go                      registers all commands
└── pkg/... (libraries)
```

### The Command interface

```go
// internal/cli/command.go
package cli

type Command interface {
    Name() string
    Aliases() []string
    Synopsis() string
    Usage() string
    Run(ctx context.Context, env *Env) error
}

type Env struct {
    Config  *config.Config
    Pine    *pinefacade.Client
    Runner  *runner.Runner
    Flags   *FlagSet
    Args    []string
    Stdout  io.Writer
    Stderr  io.Writer
}
```

Each subcommand is a file in `internal/cmd/` implementing `Command`. `cmd/tvcli/main.go` just builds `Env` and calls `root.Execute()`. Adding a command = one new file + one registration line. Testable: each command is a function `Run(ctx, env)` you can call from a test.

### Layering (dependency direction, no cycles)

```
cmd/tvcli/main.go
        │
        ▼
internal/cmd/*   ──►  internal/cli   (framework)
        │
        ▼
internal/service  ──►  pkg/runner
        │              pkg/pinefacade
        │              pkg/tradingview/*
        │              pkg/pipeline
        │              pkg/schema
        ▼
internal/config, internal/metadb
```

Rules:
- `internal/cmd/*` only translates flags → service calls → formats output. No business logic.
- `internal/service/*` is the **only** place that wires multiple pkg's together. Reusable by any binary.
- `pkg/*` never imports `internal/service` or `internal/cmd`.
- `pkg/pipeline` is the merged `dynparse`+`extract`; `pkg/schema` keeps the data model and the *declarative* classification tables, but the runtime classifier lives in `pipeline/classify.go` so there's one consumer-facing function.

## Migration order (smallest risk first)

1. **Extract tier limits** from `main.go` → `internal/config/tiers.go`. Pure data move. ~20 lines.
2. **Extract meta-store** (`loadMetaStore` etc.) → `internal/metadb/metadb.go`. ~80 lines.
3. **Extract OHLCV helpers** (`loadOHLCV`, `saveOHLCV`, `fetchOHLCVBars`, `mergeOHLCV`, `timeframeSeconds`) → `internal/service/fetch.go`. ~250 lines. Update `cmdFetch`/`cmdSync` to call it.
4. **Extract `cmdRun` body** (lines 649–1092) → `internal/service/run.go` as `RunScript(ctx, env, req) (*RunResult, error)`. `cmdRun` becomes a 30-line flag parser that calls it. ~450 lines moved.
5. **Introduce `internal/cli` + `Command` interface.** Convert one command (e.g. `list`) as a proof. Then convert the rest mechanically. Each PR ≤ 200 lines.
6. **Split `pkg/pinefacade/client.go`** into `client.go` / `search.go` / `compile.go`. No API change.
7. **Split `pkg/tradingview`** into `ws/`, `auth/`, `chart/`, `study/`. Move `protocol.go` + `compressed.go` into `ws/`. Move HTTP token fetch into `auth/`. This is the riskiest split — do it last, after the CLI layer is decoupled enough that the compiler tells you every call site.
8. **Merge `pkg/dynparse` + `pkg/extract`** into `pkg/pipeline`. Unify the two classifiers into one. Drop `schema/semantic.go`'s runtime classifier; keep `schema` as pure data + the keyword tables, exported for `pipeline/classify.go` to consume.
9. **Add tests** for each `internal/service/*` function. These are now pure functions over injected deps — table-testable.

Each step compiles and ships independently. No big-bang rewrite.

## What to *not* do

- Don't introduce interfaces for `pinefacade.Client` or `tradingview.Client` "for mocking". Add a thin `doer` interface at the service boundary only if a test actually needs it (postpone).
- Don't move to `spf13/cobra` unless you need its completion/help features. The 60-line `cli.Command` interface above does the job; cobra is a 2k-LoC dependency for a 12-command CLI.
- Don't split `pkg/runner` further — `runner.go`/`multirun.go`/`persistent.go` is a reasonable split already.
- Don't merge `pkg/schema` into `pkg/pipeline` — `schema` is a stable data contract other packages import; keep it leaf.

## Payoff

| Pain                          | Before                        | After                                  |
|------------------------------|-------------------------------|----------------------------------------|
| Add a subcommand             | edit 2196-LoC switch + append| new file in `internal/cmd/`, 1 reg line |
| Reuse `run` from another bin | copy 444 lines                | `service.RunScript(ctx, req)`           |
| Test a command               | untestable                    | `cmd.Run(ctx, fakeEnv)`                 |
| Test business logic          | untestable                   | `service.RunScript` table tests         |
| Understand tier limits       | grep `main.go`               | `internal/config/tiers.go`             |
| Two input defs               | drift                        | one in `pkg/schema`                     |
| Two classifiers              | drift                        | one in `pkg/pipeline/classify.go`      |
| `main.go` size               | 2196                         | ~30                                    |

## TL;DR

Move the 2196-line `main.go` into `internal/cmd/*` (one file per subcommand) + `internal/service/*` (orchestration) + `internal/cli` (command framework). Split `pkg/tradingview` along WS/HTTP/chart/study lines. Merge `pkg/dynparse` + `pkg/extract` into `pkg/pipeline` with one classifier. Keep `pkg/schema` as a pure data contract. Do it in 9 small PRs, each compiles.

---

# Progress log (implementation started 2026-07-19)

## Done — Steps 1–9 (build + tests green after each step)

| Step | Moved                                          | From                          | To                              | LoC moved |
|------|------------------------------------------------|-------------------------------|---------------------------------|-----------|
| 1    | Tier limits (`tiers`, `getTierLimits`)          | `cmd/tvcli/main.go`            | `internal/config/tiers.go`      | ~20       |
| 2    | Meta-store (`metaStore`, `metaEntry`, methods)  | `cmd/tvcli/main.go`            | `internal/metadb/metadb.go`     | ~130      |
| 3    | OHLCV helpers + `timeframeSeconds`              | `cmd/tvcli/main.go`            | `internal/service/fetch.go`     | ~230      |
| 4    | `cmdRun` core (WS+study loop) → `RunScript`     | `cmd/tvcli/main.go`            | `internal/service/run.go`       | ~280      |
| 4b   | `LoadIndicator` (shared w/ persistent runner)  | (duplicated in 2 places)       | `internal/service/run.go`       | ~60       |
| 5    | `cli` framework (FlagSet, Command, Root, Env)  | (new)                         | `internal/cli/cli.go`           | ~170      |
| 5b   | `list` command → `cli.Command`                  | `cmd/tvcli/main.go`            | `internal/cmd/list.go`          | ~130      |
| 5c   | Search-table helpers (`normalizeSearchResults`)| `cmd/tvcli/main.go`            | `internal/cmd/searchutil.go`     | ~90       |
| 5d   | Help text → `cmd.PrintHelp`                    | `cmd/tvcli/main.go`            | `internal/cmd/help.go`           | ~100      |
| 6    | `pkg/pinefacade/client.go` split into 3 files   | `client.go` (545 LoC)          | `client.go` (263) + `search.go` (46) + `util.go` (203) | no API change |
| 7    | HTTP auth extracted from `pkg/tradingview`     | `client.go` (333 LoC, mixed)   | `pkg/tradingview/auth/auth.go` (67) + `client.go` (263, WS only) | see note |
| 8    | `pkg/dynparse` + `pkg/extract` merged           | 2 packages (~1500 LoC)         | `pkg/pipeline/` (1 package, 5 files) | see note |
| 9    | Service-layer tests added                       | (none)                        | `cli_test.go`, `tiers_test.go`, `fetch_test.go`, `metadb_test.go` | ~350 |

### Step 7 deviation from the original proposal

The original plan called for splitting `pkg/tradingview` into four subpackages (`ws/`, `auth/`, `chart/`, `study/`). Ponytail re-check found that `chart`, `study`, and `client` share unexported helpers (`genSessionID`, `sessionEntry`, `parseCompressed`) — forcing them into separate packages would either create import cycles (`study` → `chart` → `client` → …) or require exporting many internals for no real benefit on a 1500-LoC package.

The genuine architectural problem the proposal identified was **HTTP auth mixed with WS transport in `client.go`**. That's the one extracted:

- `pkg/tradingview/auth/` — `GenCookies`, `FetchToken` (HTTP only, no WS deps)
- `pkg/tradingview/` — WS client + chart + study + indicator + protocol + compressed (kept together; they're tightly coupled via unexported helpers)

### Step 8 deviation from the original proposal

The original plan also called for unifying "duplicate classifiers" in `pipeline/classify.go`. On inspection they're not duplicates — they're a layered pipeline:

1. `schema.classifyFromMetadata` → fills `PlotDef.Semantic` (string) at schema build time
2. `pipeline.classifyFromSchema` → uses `PlotDef.Semantic` + stats to assign `PlotClass` at extraction time
3. `pipeline.classify` → pure statistical fallback when schema is absent

Moving `classifyFromMetadata` out of `schema` would create an import cycle (`schema` → `pipeline` → `schema`). The merge into one package was done; the classifier consolidation was not — it would have been busywork.

### `main.go` size over time

```
2196  baseline
2167  after step 1  (-29)
2036  after step 2  (-131)
1825  after step 3  (-211)
1549  after step 4  (-647)
1329  after step 5  (-867)
1329  after step 6  (no main.go change — pkg-only)
1329  after step 7  (no main.go change — pkg-only)
1329  after step 8  (no main.go change — pkg-only)
1329  after step 9  (no main.go change — tests only)
```

Final: `main.go` is **39% smaller** (2196 → 1329), all extracted code lives in testable internal/ or pkg/ packages.

### Current package layout

```
cmd/tvcli/main.go             1329  entry point + legacy switch (11 commands still here)
internal/
├── cli/
│   ├── cli.go                 170  Command interface, FlagSet, Root dispatcher
│   └── cli_test.go            ~170  92% coverage
├── cmd/                       318  list.go, help.go, searchutil.go (1 of 12 migrated)
├── config/
│   ├── config.go              152  env + .env loading, CookieHeader
│   ├── tiers.go                32  tier limits as data
│   └── tiers_test.go          ~50  tier lookup
├── metadb/
│   ├── metadb.go              148  .tv-meta.json script registry
│   └── metadb_test.go         ~150  94% coverage (round-trip + Find*)
└── service/
    ├── fetch.go               228  OHLCV load/save/merge/fetch + TimeframeSeconds
    ├── fetch_test.go          ~90  TimeframeSeconds + MergeOHLCV + LastTimestamp
    └── run.go                 305  LoadIndicator + RunScript (was cmdRun core)

pkg/
├── pinefacade/                583  client.go (263) + search.go (46) + util.go (203) + parser.go + types.go
├── pipeline/                 1830  dynparse.go + extract.go + extract_schema.go (+ 2 tests)
├── runner/                    977  runner.go + multirun.go + persistent.go
├── schema/                   1040  schema.go + semantic.go (+ test)
├── tradingview/              1455  client.go + chart.go + study.go + indicator.go + protocol.go + compressed.go
└── tradingview/auth/           67  auth.go (HTTP cookie + token fetch)
```

### Test coverage

```
internal/cli         92.2%   framework dispatch + flag parsing + aliases + help
internal/config     10.3%   tiers (config.go untested — env glue)
internal/metadb     94.7%   full round-trip + Find* + Delete + NextID
internal/service    15.1%   pure helpers (RunScript/FetchOHLCVBars need a client iface)
pkg/pipeline        46.4%   ported dynparse/extract tests
pkg/schema          38.9%   unchanged
```

### Dispatch path in `main.go` today

```
main() → cli.Root.Lookup(name)
       ├── found     → cli.Command.Run(env)         (list, ls)
       └── not found → legacy switch                (11 commands: create/pull/push/delete/
                                                    search/publist/top/compile/run/fetch/sync)
```

Each legacy command's case gets deleted as it migrates to `internal/cmd/`. The migration path is mechanical: copy the function body into a new `internal/cmd/X.go` implementing `cli.Command`, register in `RegisterAll`, delete the case from the switch. The skill layer (Part 2 of this doc) will plug into `RegisterAll` the same way the built-in commands do.

## What's left (not in the 9 steps)

- [x] Migrate the remaining 11 commands from the legacy switch to `internal/cmd/` (done: `create`, `pull`, `push`, `delete`, `search`, `publist`, `top`, `compile`, `run`, `fetch`, `sync`).
- [x] Introduce a `tradingview.Client` interface so `service.RunScript` and `service.FetchOHLCVBars` can be table-tested with a fake WS client (the concrete `WSClient` now implements it; `NewClient` returns the interface).
- [x] Part 2 of this doc (skills as dynamic commands) — implemented as Go-per-parser approach (see Part 2 Implementation below).

---

# Part 2 — Skills as dynamic commands

## The opportunity

`/Volumes/ExMac/code/tradingview/js-experiment06/` has 14 standalone `.cjs` runners (anchored-clusters-vp, buying-selling-volume, precision-sniper, …) plus a `SKILL_INDEX.md` listing 17 skills. Each `.cjs` is ~90% identical boilerplate; only **three things vary per skill**:

| Varying piece             | Example (anchored-clusters-vp)                 | Already modeled in Go by            |
|--------------------------|------------------------------------------------|-------------------------------------|
| **Identity**             | `PINE_ID = "PUB;92974e0a…"`                    | `pinefacade.Get(pineID, …)`         |
| **Input map**            | `[{variable:"kInput", tvInputId:"in_3", type:"int", default:5}, …]` | `pkg/pinefacade/parser.go` `PineInput`, `pkg/schema.InputDef`, `pkg/tradingview/indicator.go` `InputDef` |
| **Output profile**       | graphics-only; read `dwgBoxes`/`dwgLabels`/`dwgLines`; clusters, POC labels, POC lines | `pkg/pipeline` (after Part-1 merge) + `pkg/schema.GraphicsProfile` |

So a "skill" is just a **declarative descriptor** + the existing generic runner. The restructure from Part 1 makes this trivial because:

- `internal/cli.Command` is an interface → you can register commands at runtime.
- `internal/service.RunScript(ctx, RunRequest)` already accepts `PineID`, `Inputs`, `Schema`, returns structured `RunResult` — it doesn't care *which* script.
- `pkg/pipeline` produces `extract.Signals` with `Classifications`, `Levels`, `Events`, `GraphicCounts` — the same fields every `.cjs` re-derives by hand.
- `pkg/schema` already classifies plots by keyword (`signal`, `band`, `price`, `oscillator`, …) — that's exactly what each `.cjs` does inline.

## The design

### 1. One descriptor type

```go
// pkg/skill/skill.go  (new, small leaf package — no internal/ deps)
package skill

type Descriptor struct {
    Name        string         `yaml:"name"`         // "anchored-clusters-vp"
    PineID      string         `yaml:"pineId"`       // "PUB;92974e0a…"
    Version     string         `yaml:"version"`      // "" = latest
    Synopsis    string         `yaml:"synopsis"`     // one-liner for --help
    Inputs      []InputBinding `yaml:"inputs"`       // the INPUT_MAP
    Output      OutputProfile  `yaml:"output"`       // how to interpret
    Presets     map[string]map[string]any `yaml:"presets,omitempty"` // "scalping", "swing", …
    Skills      []string       `yaml:"skills,omitempty"` // claude-skill names that invoke this
}

type InputBinding struct {
    Variable string `yaml:"variable"`   // "kInput"     (user-facing flag name)
    TVInput  string `yaml:"tvInput"`    // "in_3"       (wire id)
    Type     string `yaml:"type"`       // int|float|bool|string|color|time
    Default  any    `yaml:"default"`
    Help     string `yaml:"help,omitempty"`
}

type OutputProfile struct {
    Mode        string   `yaml:"mode"`        // "graphics" | "periods" | "both"
    GraphicKeys []string `yaml:"graphicKeys"` // ["dwgBoxes","dwgLabels","dwgLines"]
    Extractors  []string `yaml:"extractors"` // ["clusters","pocLabels","pocLines"]
    BiasFrom    string   `yaml:"biasFrom"`   // which field/extractor drives Bias
}
```

### 2. One descriptor per skill = one YAML file

```yaml
# skills/anchored-clusters-vp.yaml
name: anchored-clusters-vp
pineId: "PUB;92974e0a3cfb481eaf058cdab9f925a3"
synopsis: "Anchored volume profile clusters — POC, cluster extremes, fair value"
inputs:
  - { variable: startTime,  tvInput: in_0, type: time,   default: 1704067200000 }
  - { variable: kInput,     tvInput: in_3, type: int,    default: 5,   help: "cluster count" }
  - { variable: iters,      tvInput: in_4, type: int,    default: 50 }
  - { variable: rowsInput,  tvInput: in_5, type: int,    default: 20 }
  - { variable: showDots,   tvInput: in_8, type: bool,   default: true }
output:
  mode: graphics
  graphicKeys: [dwgBoxes, dwgLabels, dwgLines]
  extractors: [clusters, pocLabels, pocLines]
  biasFrom: pocLabels
skills: [anchored-clusters-vp]
```

This replaces the entire 380-line `anchored-clusters-vp.cjs`. The 14 cjs files collapse to 14 YAML files in `skills/`.

### 3. A registry that builds `cli.Command`s dynamically

```go
// internal/skillcmd/register.go
package skillcmd

func RegisterAll(root *cli.Root, svc *service.Service, skillsDir string) error {
    descs, err := skill.LoadDir(skillsDir) // glob skills/*.yaml
    if err != nil { return err }
    for _, d := range descs {
        root.Add(buildCommand(d, svc))
    }
    return nil
}

func buildCommand(d skill.Descriptor, svc *service.Service) cli.Command {
    return &skillCommand{desc: d, svc: svc}
}

type skillCommand struct{ desc skill.Descriptor; svc *service.Service }

func (c *skillCommand) Name() string    { return c.desc.Name }
func (c *skillCommand) Synopsis() string { return c.desc.Synopsis }

func (c *skillCommand) Run(ctx context.Context, env *cli.Env) error {
    fs := env.Flags
    fs.String("symbol", "BTCUSDT", "trading symbol")
    fs.String("tf", "15m", "timeframe")
    fs.Int("bars", 500, "bar count")
    fs.String("preset", "", "named preset from descriptor (e.g. scalping)")
    fs.Bool("json", false, "JSON output")
    fs.Bool("agent", false, "agent-ready JSON")
    // Auto-register one --<variable> flag per InputBinding
    for _, in := range c.desc.Inputs {
        fs.Var(in.Variable, in.Default, in.Help)
    }
    if err := fs.Parse(env.Args); err != nil { return err }

    req := service.RunRequest{
        PineID:         c.desc.PineID,
        Version:        c.desc.Version,
        Symbol:         fs.String("symbol"),
        Timeframe:      fs.String("tf"),
        Bars:           fs.Int("bars"),
        Inputs:         c.collectInputs(fs),     // map[string]any, coerced via InputBinding.Type
        OutputProfile:  c.desc.Output,           // pipeline uses this to pick extractors
    }
    if p := fs.String("preset"); p != "" {
        req.ApplyPreset(c.desc.Presets[p])       // merges preset inputs
    }
    res, err := c.svc.RunScript(ctx, req)
    if err != nil { return err }

    if fs.Bool("agent") { return render.Agent(env.Stdout, res, c.desc) }
    if fs.Bool("json")  { return render.JSON(env.Stdout, res) }
    return render.Human(env.Stdout, res, c.desc)
}
```

That's the whole skill layer — **one ~80-line file**, **N YAML descriptors**, **zero per-skill Go code**.

### 4. Output interpretation is shared, not per-skill

The `.cjs` files each hand-roll a `parseGraphicOutput` / `interpret` block. In Go this becomes a small library of named **extractors** registered in `pkg/pipeline/extract`:

```go
// pkg/pipeline/extract/extractors.go
var extractorRegistry = map[string]Extractor{
    "clusters":    extractClusters,    // dwgBoxes → {top,bottom,left,right,color}
    "pocLabels":   extractPOCLabels,    // dwgLabels matching /^(poc|volume|…)$/i → {price,volume}
    "pocLines":    extractPOCLines,     // dwgLines → level[]
    "boschoch":    extractBOSCHoCH,    // for ict/smc skills
    "fvgs":        extractFVGs,
    "orderblocks":  extractOrderBlocks,
    "mtfBias":     extractMTFBias,     // for xauusd-mtf-trend
    // …
}
```

Each `OutputProfile.Extractors` list picks which ones run. A skill author adds a new extractor once, every graphics-only skill benefits. This is the only place new Go code gets written — and only when a *genuinely new* graphic shape appears.

### 5. The existing Claude skills keep working unchanged

Each `~/.claude/skills/<name>/SKILL.md` already tells the agent to invoke `node scripts/<name>.cjs <symbol> --tf … --json --agent`. After this change, the SKILL.md gets a one-line edit: invoke `tvcli <name> <symbol> --tf … --agent` instead. Same flags, same JSON contract (because `render.Agent` matches the existing `.cjs --agent` output). The skill's natural-language layer — when to use it, how to read the output — is unchanged.

## What this replaces

| Today (js-experiment06)                          | After restructure                          |
|--------------------------------------------------|--------------------------------------------|
| 14 × ~380-line `.cjs` files (5.3k LoC)            | 14 × ~30-line YAML descriptors             |
| Per-skill `parseArgs` / `parseGraphicOutput`     | Shared `cli.FlagSet` + `pipeline/extract` |
| Per-skill `INPUT_MAP` hardcoded in JS             | `inputs:` in YAML, auto-generates flags    |
| Per-skill preset logic in JS                     | `presets:` map in YAML, `--preset` flag    |
| Skill layer calls `node scripts/X.cjs`            | Skill layer calls `tvcli X`               |
| Adding a new skill = copy a 380-line cjs + edit   | Add a 30-line YAML + (rarely) one extractor|
| No tests on per-skill logic                       | Descriptors are data → table-testable      |

## Why the restructure is what makes this *easy* (vs. bolt-on)

Without Part 1, you'd be writing skill descriptors against a `cmd/tvcli/main.go` monolith that has no `Command` interface, no service layer, no shared `RunRequest`, no pipeline. You'd end up either (a) duplicating `cmdRun`'s 444 lines per skill, or (b) building the Part-1 layer anyway, just badly, under deadline pressure. Doing Part 1 first means the skill layer is **one small file plus data** — exactly the "stdlib + data beats code" rung of the ladder.

Concretely, the dependency:

```
internal/skillcmd ──► internal/cli      (Command interface — from Part 1)
                  ──► internal/service  (RunScript — from Part 1)
                  ──► pkg/skill        (Descriptor — new, leaf)
                  ──► pkg/pipeline     (extractors — from Part 1 merge)
```

Nothing in `internal/skillcmd` imports `pkg/tradingview` or `pkg/pinefacade`. The skill layer is *pure orchestration over the service layer* — which only exists because Part 1 created it.

## Migration (do after Part-1 step 5, in parallel with steps 6–9)

1. Add `pkg/skill` (descriptor types + `LoadDir`). ~120 LoC.
2. Add `pkg/pipeline/extract/extractors.go` with the ~6 extractors that cover today's 14 skills (clusters, pocLabels, pocLines, boschoch, fvgs, orderblocks, mtfBias, signalBars). ~300 LoC, ported from existing `.cjs` parsers.
3. Add `internal/skillcmd` (the `~80-line` file above).
4. Author 14 YAML descriptors in `skills/` — port by reading each `.cjs`'s `PINE_ID` + `INPUT_MAP` + output keys. ~30 min each.
5. Wire `internal/skillcmd.RegisterAll` into `cmd/tvcli/main.go` after the built-in commands.
6. Update each `~/.claude/skills/<name>/SKILL.md` "Quick Start" block: `node scripts/X.cjs` → `tvcli X`. One-line edit per skill.
7. Delete the 14 `.cjs` files (or keep as reference). 5.3k LoC → ~420 LoC YAML + ~500 LoC shared Go.

Each step compiles and ships. The skill layer is opt-in — built-in commands work without any YAML.

## TL;DR (Part 2)

A skill is a `(pineId, inputMap, outputProfile, presets)` tuple. The Part-1 restructure already gives you a `Command` interface, a `service.RunScript` orchestrator, and a `pkg/pipeline` extractor layer. Add one small `pkg/skill` descriptor package + one `internal/skillcmd` file that builds a `cli.Command` per YAML descriptor. The 14 js-experiment06 `.cjs` runners (5.3k LoC) become 14 YAML files (~420 lines) plus a shared ~800-LoC Go skill layer. Existing Claude SKILL.md files keep their natural-language layer; only the invocation line changes from `node scripts/X.cjs` to `tvcli X`.


---

# Part 2 Implementation (actual)

## What was built

16 Pine Script skills ported from JS to Go as dynamic CLI commands. Each skill is a named command (`tv smc`, `tv sniper`, `tv trend`, etc.) with all indicator inputs overridable via `--flag value` syntax.

### Architecture

The implementation uses a **Go-per-parser** approach instead of the YAML-descriptor approach described above. This is because each indicator has genuinely different parsing logic (graphic extraction, table parsing, k-means clustering, multi-TF correlation) that can't be easily generalized into shared extractors.

### Files

**Core framework:**
- `internal/skill/skill.go` — Types: `Skill`, `InputDef`, `SkillResult`, `AgentResult`
- `internal/skill/registry.go` — `Register()`, `Get()`, `All()`, `Names()` registry
- `internal/cmd/skillcmd.go` — Generic `skillCmd` implementing `cli.Command`
- `internal/cmd/skills.go` — `tv skills` list command

**Parsers (one per skill):**
- `internal/skill/parsers/bsv.go` — Buy/Selling Volume (3 inputs, 3 presets)
- `internal/skill/parsers/dvi.go` — Delta Volume Intensity (3 inputs)
- `internal/skill/parsers/ust.go` — Ultra Sensitive SuperTrend (7 inputs)
- `internal/skill/parsers/swingarm.go` — SwingArm ATR Trend (4 inputs)
- `internal/skill/parsers/ema_atr.go` — EMA + ATR Pro Engine (10 inputs)
- `internal/skill/parsers/sr_breaks.go` — Support/Resistance Breaks (7 inputs)
- `internal/skill/parsers/shemar.go` — SHEMAR SMC Confidence (10 inputs)
- `internal/skill/parsers/quantum.go` — Quantum Ribbon Lite (4 inputs)
- `internal/skill/parsers/vgaps.go` — Volume Gaps & Imbalances (4 inputs)
- `internal/skill/parsers/anchored_vp.go` — Anchored Volume Profile (5 inputs)
- `internal/skill/parsers/mtf.go` — XAUUSD MTF Trend Dashboard (8 inputs)
- `internal/skill/parsers/sniper.go` — Precision Sniper (13 inputs, 7 presets)
- `internal/skill/parsers/smc.go` — Smart Money Concepts (9 inputs)
- `internal/skill/parsers/trend.go` — Self-Aware Trend System (10 inputs, 5 presets)
- `internal/skill/parsers/ict.go` — ICT Auto-Validated SMC (12 inputs)
- `internal/skill/parsers/golden.go` — Golden Rule Strategy (4 inputs)

**Shared utilities:**
- `internal/skill/parsers/helpers.go` — `getField`, `toFloat`, `resolveBarColor`, `confidenceLabel`, `round2`

### Dependency graph

```
internal/cmd/skillcmd.go ──► internal/cli      (Command interface — from Part 1)
                          ──► internal/service  (RunScript — from Part 1)
                          ──► internal/skill    (Skill types — new)
                          ──► pkg/pinefacade    (ValidateSymbol)

internal/skill/parsers/*  ──► internal/skill    (Skill types)
```

Each parser registers itself via `init()` calling `skill.Register()`. The `cmd` package imports `skill/parsers` for side effects.

### Commands

| Command | Skill | Inputs | Presets |
|---|---|---|---|
| `tv bsv` | Buy/Sell Volume | 3 | scalping, default, swing |
| `tv dvi` | Delta Volume Intensity | 3 | — |
| `tv ust` | Ultra Sensitive SuperTrend | 7 | — |
| `tv swingarm` | SwingArm ATR Trend | 4 | — |
| `tv ema-atr` | EMA + ATR Pro Engine | 10 | — |
| `tv sr-breaks` | Support/Resistance Breaks | 7 | — |
| `tv shemar` | SHEMAR SMC Confidence | 10 | — |
| `tv quantum` | Quantum Ribbon Lite | 4 | — |
| `tv vgaps` | Volume Gaps & Imbalances | 4 | — |
| `tv anchored-vp` | Anchored Volume Profile | 5 | — |
| `tv mtf` | XAUUSD MTF Trend | 8 | — |
| `tv sniper` | Precision Sniper | 13 | 7 presets |
| `tv smc` | Smart Money Concepts | 9 | — |
| `tv golden` | Golden Rule Strategy | 4 | — |
| `tv trend` | Self-Aware Trend System | 10 | 5 presets |
| `tv ict` | ICT Auto-Validated SMC | 12 | — |

### Usage

```bash
tv skills                          # List all skills
tv bsv --symbol OANDA:XAUUSD       # Run with defaults
tv sniper --preset scalping        # Run with preset
tv trend --atr-len=20              # Override specific input
tv smc --json --agent              # Agent-ready JSON output
tv sniper --help                   # Get help for any skill
```

### Deviation from YAML-descriptor approach

The original Part 2 design proposed YAML descriptors + shared extractors. The Go-per-parser approach was chosen because:

1. **Per-indicator parsing is genuinely different** — SMC parses graphic boxes/labels, sniper parses dashboard tables, trend computes local indicators, MTF extracts table cells across timeframes.
2. **Shared extractors would still need per-indicator code** — the extractor registry would grow to match the number of parsers.
3. **Type safety** — Go parsers catch type errors at compile time.
4. **Testability** — Each parser is a pure function that can be unit-tested independently.

The YAML approach could be revisited if a truly general-purpose graphic/table extractor library is built. For now, the Go-per-parser approach provides the same CLI interface and JSON output contract with less abstraction overhead.
