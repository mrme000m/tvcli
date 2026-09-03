// watch implements `tvcli watch` — a spec-driven market watch that polls the
// latest price (same fetch path as the fetch command) and evaluates the
// triggers defined in a watch spec JSON, mirroring the python watchtower
// (agents/watchtower/bin/watchtower.py).
//
// Trigger semantics (identical to the python implementation):
//   - level up-cross fires when prev < level <= cur (down: prev > level >= cur;
//     mode "touch": cur >= level for up / cur <= level for down)
//   - zone enter fires when prev is outside [lo,hi] and cur is inside
//   - pct fires when (cur-baseline)/baseline*100 >= pct (up) or <= pct (down)
//   - time fires when now >= created+afterMin (or >= at)
//
// Level/zone/pct/time triggers are one-shot per episode (recorded in the
// state file). Skill triggers re-arm per field: each watched dot-path is
// compared against the last recorded value (or the spec's skillWatch
// baselineFields on the first run) and every change fires its own event.
package cmd

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/internal/service"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
	"github.com/mrme000m/tvcli/pkg/skill"
)

// ---------------------------------------------------------------- spec / state

// WatchTrigger is one trigger entry from the watch spec.
type WatchTrigger struct {
	ID     string  `json:"id"`
	Type   string  `json:"type"` // level | zone | pct | time | skill
	Label  string  `json:"label,omitempty"`
	Action string  `json:"action,omitempty"`
	Dir    string  `json:"dir,omitempty"`  // up | down | enter
	Mode   string  `json:"mode,omitempty"` // cross (default) | touch
	Level  float64 `json:"level,omitempty"`
	Lo     float64 `json:"lo,omitempty"`
	Hi     float64 `json:"hi,omitempty"`
	Pct    float64 `json:"pct,omitempty"`
	At     string  `json:"at,omitempty"`
	AfterMin float64 `json:"afterMin,omitempty"`
}

// WatchSkillWatch is the skill-signal watch section of a spec.
type WatchSkillWatch struct {
	Skill          string         `json:"skill"`
	IntervalMin    float64        `json:"intervalMin"`
	DebounceMin    float64        `json:"debounceMin,omitempty"`
	BaselineFields map[string]any `json:"baselineFields"`
	On             string         `json:"on,omitempty"`
}

// WatchSpec is the subset of the watchtower spec the Go command needs.
type WatchSpec struct {
	Episode   int    `json:"episode"`
	ID        string `json:"id"`
	Mission   string `json:"mission"`
	Status    string `json:"status"`
	Created   string `json:"created"`
	Symbol    string `json:"symbol"`
	TF        string `json:"tf"`
	Baseline  struct {
		Time  int64   `json:"time"`
		Price float64 `json:"price"`
		At    string  `json:"at"`
	} `json:"baseline"`
	SkillWatch *WatchSkillWatch `json:"skillWatch,omitempty"`
	Triggers   []WatchTrigger   `json:"triggers"`
}

// WatchSkillState is the recorded skill-signal reference in the state file.
type WatchSkillState struct {
	LastRun string                  `json:"lastRun"`
	Values  map[string]any          `json:"values"`
	Pending map[string]WatchPending `json:"pending,omitempty"`
}

// WatchPending is a debounce-pending skill flip: it fires only if it persists.
type WatchPending struct {
	From  any    `json:"from"`
	To    any    `json:"to"`
	Since string `json:"since"`
}

// WatchLast is the last observed snapshot recorded in the state file.
type WatchLast struct {
	Time  float64 `json:"time"`
	Price float64 `json:"price"`
	At    string  `json:"at"`
}

// WatchState is the persisted per-episode watch state.
type WatchState struct {
	SpecID string           `json:"specId"`
	Last   *WatchLast      `json:"last,omitempty"`
	Fired  map[string]string `json:"fired"`
	Skill  *WatchSkillState `json:"skill,omitempty"`
}

func newWatchState(specID string) *WatchState {
	return &WatchState{SpecID: specID, Fired: map[string]string{}}
}

// WatchEvent is one journaled event line (JSONL).
type WatchEvent struct {
	ID            string            `json:"id"`
	TS            string            `json:"ts"`
	Trigger       WatchEventTrigger `json:"trigger"`
	Price         float64           `json:"price"`
	BaselinePrice float64           `json:"baselinePrice"`
	Delta         float64           `json:"delta"`
	ElapsedMin    float64           `json:"elapsedMin"`
	Outcomes      map[string]any    `json:"outcomes"`
}

// WatchEventTrigger is the trigger reference embedded in a WatchEvent.
type WatchEventTrigger struct {
	ID     string `json:"id"`
	Type   string `json:"type"`
	Label  string `json:"label,omitempty"`
	Action string `json:"action,omitempty"`
}

func loadWatchSpec(path string) (*WatchSpec, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read spec: %w", err)
	}
	var spec WatchSpec
	if err := json.Unmarshal(b, &spec); err != nil {
		return nil, fmt.Errorf("parse spec %s: %w", path, err)
	}
	if spec.ID == "" {
		return nil, fmt.Errorf("spec %s has no id", path)
	}
	return &spec, nil
}

func loadWatchState(path, specID string) *WatchState {
	b, err := os.ReadFile(path)
	if err == nil {
		var st WatchState
		if err := json.Unmarshal(b, &st); err == nil && st.SpecID == specID {
			if st.Fired == nil {
				st.Fired = map[string]string{}
			}
			return &st
		}
	}
	return newWatchState(specID)
}

func saveWatchState(path string, st *WatchState) error {
	b, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}
	return os.WriteFile(path, append(b, '\n'), 0644)
}

func appendWatchJournal(path string, ev WatchEvent) error {
	b, err := json.Marshal(ev)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.Write(append(b, '\n'))
	return err
}

// ---------------------------------------------------------------- evaluators

// LevelFires reports whether a level trigger fires on the prev->cur move.
// Up-cross: prev < level <= cur. Down-cross: prev > level >= cur. Touch mode
// only looks at cur (>= level for up, <= level for down).
func LevelFires(t WatchTrigger, prev, cur float64) bool {
	lvl := t.Level
	if t.Dir == "up" {
		if t.Mode == "touch" {
			return cur >= lvl
		}
		return prev < lvl && lvl <= cur
	}
	if t.Mode == "touch" {
		return cur <= lvl
	}
	return prev > lvl && lvl >= cur
}

// ZoneFires reports whether price entered the [lo,hi] zone this bar: prev
// outside and cur inside (either side).
func ZoneFires(t WatchTrigger, prev, cur float64) bool {
	inside := func(p float64) bool { return t.Lo <= p && p <= t.Hi }
	return !inside(prev) && inside(cur)
}

// PctFires reports whether cur moved pct percent from baseline (up: >= pct;
// down: <= pct, with pct negative for down triggers).
func PctFires(t WatchTrigger, baseline, cur float64) bool {
	move := (cur - baseline) / baseline * 100.0
	if t.Dir == "up" {
		return move >= t.Pct
	}
	return move <= t.Pct
}

// TimeFires reports whether the time trigger's deadline has been reached.
// afterMin counts from the spec's created timestamp; at is an absolute
// RFC3339 deadline.
func TimeFires(t WatchTrigger, created, now time.Time) bool {
	if t.AfterMin != 0 {
		return !now.Before(created.Add(time.Duration(t.AfterMin * float64(time.Minute))))
	}
	if t.At != "" {
		at, err := time.Parse(time.RFC3339, t.At)
		if err != nil {
			return false
		}
		return !now.Before(at)
	}
	return false
}

// dig resolves a dot path ("structure.stDir") against a nested JSON object.
func dig(obj map[string]any, dotted string) any {
	var cur any = obj
	for _, part := range strings.Split(dotted, ".") {
		m, ok := cur.(map[string]any)
		if !ok {
			return nil
		}
		cur = m[part]
		if cur == nil {
			return nil
		}
	}
	return cur
}

// equalWatchValues compares two decoded-JSON scalars (numbers of mixed
// int/float representation compare numerically).
func equalWatchValues(a, b any) bool {
	an, aok := toFloat(a)
	bn, bok := toFloat(b)
	if aok && bok {
		return an == bn
	}
	if aok != bok {
		return false
	}
	return a == b
}

func toFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	}
	return 0, false
}

// ---------------------------------------------------------------- command

type watchCmd struct{ app *App }

func (c *watchCmd) Name() string     { return "watch" }
func (c *watchCmd) Aliases() []string { return nil }
func (c *watchCmd) Synopsis() string {
	return "Spec-driven market watch: poll price, evaluate triggers, journal events"
}

const watchUsage = `watch — Spec-driven market watch (watchtower mirror)

Usage: tvcli watch --spec <spec.json> [--once] [--interval 60] [--dry-run] [--exec-notify]

Evaluates the triggers in a watchtower spec against a live price snapshot
(same fetch path as the fetch command). Level/zone/pct/time triggers are
one-shot per episode; skill triggers fire per changed watched field.

Options:
  --spec FILE       Watch spec JSON (required). State is kept next to it as
                    <dir>/<basename>.state.json, events are appended to
                    <dir>/<basename>.journal.jsonl.
  --once            Single poll then exit. Exit code 10 when any trigger
                    fired, 0 otherwise, 1 on error.
  --interval N      Loop mode poll interval in seconds (default 60). Without
                    --once the command loops until Ctrl-C.
  --dry-run         Evaluate and print what WOULD fire against the current
                    snapshot without writing state or journal files.
  --exec-notify     Run a macOS notification (osascript, best-effort) for
                    every fired trigger with action L1/L2.
`

func (c *watchCmd) Run(env *cli.Env) error {
	flags := env.Flags
	if flags.Has("help") || flags.Has("h") {
		fmt.Fprint(env.Stdout, watchUsage)
		return nil
	}
	specPath := flags.Get("spec")
	if specPath == "" {
		fmt.Fprint(env.Stdout, watchUsage)
		return fmt.Errorf("--spec is required")
	}
	if _, err := os.Stat(specPath); err != nil {
		return fmt.Errorf("--spec: %v", err)
	}

	dryRun := flags.Has("dry-run")
	once := flags.Has("once")
	execNotify := flags.Has("exec-notify")
	interval := flags.GetInt("interval", 60)
	if interval < 1 {
		interval = 1
	}

	if !once {
		fmt.Fprintf(env.Stderr, "watch loop every %ds — Ctrl-C to stop\n", interval)
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
		defer signal.Stop(sigCh)
		for {
			if _, err := c.poll(env, specPath, dryRun, execNotify); err != nil {
				fmt.Fprintf(env.Stderr, "poll error: %v\n", err)
			}
			select {
			case <-sigCh:
				fmt.Fprintln(env.Stderr, "watch stopped")
				return nil
			case <-time.After(time.Duration(interval) * time.Second):
			}
		}
	}

	fired, err := c.poll(env, specPath, dryRun, execNotify)
	if err != nil {
		return err
	}
	if fired > 0 {
		// Distinct exit code so scripts can branch on "something fired".
		os.Exit(10)
	}
	return nil
}

// poll runs one evaluation pass. It returns the number of fired events.
func (c *watchCmd) poll(env *cli.Env, specPath string, dryRun, execNotify bool) (int, error) {
	ts := time.Now().UTC()

	spec, err := loadWatchSpec(specPath)
	if err != nil {
		return 0, err
	}
	if spec.Status != "" && spec.Status != "active" {
		fmt.Fprintf(env.Stdout, "%s skipped: spec status=%s\n", isoWatch(ts), spec.Status)
		return 0, nil
	}

	dir := filepath.Dir(specPath)
	base := strings.TrimSuffix(filepath.Base(specPath), ".json")
	statePath := filepath.Join(dir, base+".state.json")
	journalPath := filepath.Join(dir, base+".journal.jsonl")

	// Dry-run evaluates against the current snapshot as a fresh episode and
	// never reads or writes the on-disk state/journal.
	state := newWatchState(spec.ID)
	if !dryRun {
		state = loadWatchState(statePath, spec.ID)
	}

	snap, err := c.fetchSnapshot(spec)
	if err != nil {
		return 0, err
	}
	cur := snap.price
	prev := snap.prevPrice
	if state.Last != nil && state.Last.Price != 0 {
		prev = state.Last.Price
	}

	created, cerr := time.Parse(time.RFC3339, spec.Created)
	if cerr != nil {
		return 0, fmt.Errorf("parse spec.created %q: %v", spec.Created, cerr)
	}
	elapsedMin := roundWatch(ts.Sub(created).Minutes(), 1)

	var events []WatchEvent
	for i := range spec.Triggers {
		t := spec.Triggers[i]
		if t.Type == "skill" {
			continue // handled below (per-field, re-arming)
		}
		if _, done := state.Fired[t.ID]; done {
			continue
		}
		var hit bool
		switch t.Type {
		case "level":
			hit = LevelFires(t, prev, cur)
		case "zone":
			hit = ZoneFires(t, prev, cur)
		case "pct":
			hit = PctFires(t, spec.Baseline.Price, cur)
		case "time":
			hit = TimeFires(t, created, ts)
		default:
			fmt.Fprintf(env.Stderr, "watch: unknown trigger type %q (%s) skipped\n", t.Type, t.ID)
			continue
		}
		if !hit {
			continue
		}
		events = append(events, buildWatchEvent(spec, t, cur, ts, elapsedMin))
		if !dryRun {
			state.Fired[t.ID] = isoWatch(ts)
		}
		fmt.Fprintf(env.Stderr, "FIRED %s: %s @ %.2f\n", t.ID, t.Label, cur)
	}

	// ---- skill signal watch (per-field, re-arming)
	if spec.SkillWatch != nil && spec.SkillWatch.Skill != "" {
		w := spec.SkillWatch
		if w.IntervalMin <= 0 {
			w.IntervalMin = 15
		}
		due := state.Skill == nil || state.Skill.LastRun == ""
		if !due {
			if lastRun, lerr := time.Parse(time.RFC3339, state.Skill.LastRun); lerr == nil {
				due = ts.Sub(lastRun) >= time.Duration(w.IntervalMin*float64(time.Minute))
			} else {
				due = true
			}
		}
		if due {
			events = append(events, c.checkSkillWatch(env, spec, w, state, cur, ts, elapsedMin, dryRun)...)
		}
	}

	// ---- persistence + side effects
	for i := range events {
		ev := events[i]
		if execNotify && (ev.Trigger.Action == "L1" || ev.Trigger.Action == "L2") {
			notifyWatch(ev)
		}
		if !dryRun {
			if jerr := appendWatchJournal(journalPath, ev); jerr != nil {
				fmt.Fprintf(env.Stderr, "watch: journal append: %v\n", jerr)
			}
		}
		b, _ := json.Marshal(ev)
		fmt.Fprintln(env.Stdout, string(b))
	}
	if !dryRun {
		state.Last = &WatchLast{Time: snap.time, Price: cur, At: isoWatch(ts)}
		if serr := saveWatchState(statePath, state); serr != nil {
			return len(events), fmt.Errorf("save state: %w", serr)
		}
	}

	firedIDs := make([]string, 0, len(events))
	for _, ev := range events {
		firedIDs = append(firedIDs, ev.Trigger.ID)
	}
	summary, _ := json.Marshal(map[string]any{
		"ts":         isoWatch(ts),
		"specId":     spec.ID,
		"symbol":     spec.Symbol,
		"tf":         spec.TF,
		"price":      cur,
		"prev":       prev,
		"elapsedMin": elapsedMin,
		"fired":      firedIDs,
		"dryRun":     dryRun,
	})
	fmt.Fprintln(env.Stdout, string(summary))
	return len(events), nil
}

// checkSkillWatch runs the watched skill, digs the watched dot-paths from the
// agent JSON envelope, and returns one event per field whose value changed
// against the last recorded value (or the spec baseline on first run).
func (c *watchCmd) checkSkillWatch(env *cli.Env, spec *WatchSpec, w *WatchSkillWatch,
	state *WatchState, cur float64, ts time.Time, elapsedMin float64, dryRun bool) []WatchEvent {

	report, err := c.runSkillReport(w.Skill, spec.Symbol, spec.TF)
	if err != nil {
		fmt.Fprintf(env.Stderr, "watch: skill run failed (%s): %v\n", w.Skill, err)
		return nil
	}
	vals := make(map[string]any, len(w.BaselineFields))
	for path := range w.BaselineFields {
		vals[path] = dig(report, path)
	}
	// recorded = last CONFIRMED values (only updated when a flip fires or
	// debounce is disabled); pending flips age until they persist or revert.
	recorded := make(map[string]any, len(w.BaselineFields))
	pending := map[string]WatchPending{}
	var ref map[string]any
	if state.Skill != nil && len(state.Skill.Values) > 0 {
		ref = state.Skill.Values
	}
	for k, v := range w.BaselineFields {
		recorded[k] = v
	}
	if ref != nil {
		for k, v := range ref {
			recorded[k] = v
		}
	}
	if state.Skill != nil {
		for k, p := range state.Skill.Pending {
			pending[k] = p
		}
	}

	fired, recorded, newPending := applySkillWatch(recorded, pending, vals, w.DebounceMin, ts)
	var events []WatchEvent
	for path, ch := range fired {
		if ev, ok := fireSkillEvent(spec, w, path, ch[0], ch[1], cur, ts, elapsedMin, env); ok {
			events = append(events, ev)
		}
	}
	if !dryRun {
		state.Skill = &WatchSkillState{LastRun: isoWatch(ts), Values: recorded, Pending: newPending}
	}
	return events
}

// applySkillWatch is the pure debounce decision: compares live values vs the
// last CONFIRMED values, aging pending flips until they persist (fire) or
// revert (die). fired maps field -> {from, to}.
func applySkillWatch(recorded map[string]any, pending map[string]WatchPending,
	vals map[string]any, debounceMin float64,
	ts time.Time) (fired map[string][2]any, newRecorded map[string]any, newPending map[string]WatchPending) {
	fired = map[string][2]any{}
	newRecorded = make(map[string]any, len(recorded))
	for k, v := range recorded {
		newRecorded[k] = v
	}
	newPending = map[string]WatchPending{}
	for path, v := range vals {
		if v == nil {
			continue
		}
		base := recorded[path]
		if equalWatchValues(v, base) {
			continue // matches confirmed state; any pending flip for it dies
		}
		prior, hasPrior := pending[path]
		switch {
		case debounceMin <= 0:
			fired[path] = [2]any{base, v}
			newRecorded[path] = v
		case hasPrior && equalWatchValues(prior.To, v):
			if since, err := time.Parse(time.RFC3339, prior.Since); err == nil &&
				ts.Sub(since).Minutes() >= debounceMin {
				fired[path] = [2]any{prior.From, v}
				newRecorded[path] = v
			} else {
				newPending[path] = prior // still aging
			}
		default:
			newPending[path] = WatchPending{From: base, To: v, Since: isoWatch(ts)}
		}
	}
	return fired, newRecorded, newPending
}

// fireSkillEvent emits one skill flip event under the SIG-<field> id.
func fireSkillEvent(spec *WatchSpec, w *WatchSkillWatch, path string, from, to any,
	cur float64, ts time.Time, elapsedMin float64, env *cli.Env) (WatchEvent, bool) {
	trig, ok := findSkillTrigger(spec)
	if !ok {
		return WatchEvent{}, false
	}
	fire := trig
	fire.ID = fmt.Sprintf("SIG-%s", watchFieldSlug(path))
	fire.Label = fmt.Sprintf("%s flip: %s %v → %v", w.Skill, path, from, to)
	ev := buildWatchEvent(spec, fire, cur, ts, elapsedMin)
	fmt.Fprintf(env.Stderr, "FIRED %s: %v → %v\n", fire.ID, from, to)
	return ev, true
}

// runSkillReport executes a registered skill through the exact code path the
// skill CLI commands use, capturing the agent-ready JSON envelope.
func (c *watchCmd) runSkillReport(name, symbol, tf string) (map[string]any, error) {
	var sk *skill.Skill
	for _, s := range skill.All() {
		if s.Name == name {
			sk = s
			break
		}
	}
	if sk == nil {
		return nil, fmt.Errorf("unknown skill %q", name)
	}
	var out bytes.Buffer
	runEnv := &cli.Env{
		Flags: cli.ParseFlags([]string{
			"--symbol", symbol, "--tf", tf, "--json", "--agent", "--allow-private",
		}),
		Stdout: &out,
		Stderr: io.Discard,
	}
	if err := (&skillCmd{app: c.app, skill: sk}).Run(runEnv); err != nil {
		return nil, err
	}
	var report map[string]any
	if err := json.Unmarshal(out.Bytes(), &report); err != nil {
		// Fall back to the first {...} block in case of leading noise.
		m := regexp.MustCompile(`(?s)\{.*\}`).FindString(out.String())
		if m == "" {
			return nil, fmt.Errorf("no JSON in %s output", name)
		}
		if err := json.Unmarshal([]byte(m), &report); err != nil {
			return nil, fmt.Errorf("parse %s agent envelope: %w", name, err)
		}
	}
	return report, nil
}

// fetchSnapshot mirrors the fetch command's service calls to obtain the last
// two bars (prev + current close).
func (c *watchCmd) fetchSnapshot(spec *WatchSpec) (struct {
	time, price, high, low, prevPrice float64
}, error) {
	var snap struct {
		time, price, high, low, prevPrice float64
	}
	symbol := spec.Symbol
	if symbol == "" {
		symbol = "OANDA:XAUUSD"
	}
	normalized, err := pinefacade.ValidateSymbol(symbol)
	if err != nil {
		return snap, fmt.Errorf("invalid symbol: %v", err)
	}
	symbol = normalized
	tf := spec.TF
	if tf == "" {
		tf = "5m"
	}
	bars, err := service.FetchOHLCVBars(c.app.Config, symbol, tf, 2)
	if err != nil {
		return snap, fmt.Errorf("fetch: %w", err)
	}
	if len(bars) == 0 {
		return snap, fmt.Errorf("fetch returned no bars")
	}
	last := bars[len(bars)-1]
	prevBar := last.Close
	if len(bars) > 1 {
		prevBar = bars[len(bars)-2].Close
	}
	snap.time = last.Time
	snap.price = last.Close
	snap.high = last.High
	snap.low = last.Low
	snap.prevPrice = prevBar
	return snap, nil
}

// findSkillTrigger returns the spec's skill-type trigger (the SIG entry).
func findSkillTrigger(spec *WatchSpec) (WatchTrigger, bool) {
	for _, t := range spec.Triggers {
		if t.Type == "skill" {
			return t, true
		}
	}
	return WatchTrigger{}, false
}

func buildWatchEvent(spec *WatchSpec, t WatchTrigger, cur float64, ts time.Time, elapsedMin float64) WatchEvent {
	return WatchEvent{
		ID:            fmt.Sprintf("%d-%s", ts.Unix(), t.ID),
		TS:            isoWatch(ts),
		Trigger:       WatchEventTrigger{ID: t.ID, Type: t.Type, Label: t.Label, Action: t.Action},
		Price:         cur,
		BaselinePrice: spec.Baseline.Price,
		Delta:         roundWatch(cur-spec.Baseline.Price, 2),
		ElapsedMin:    elapsedMin,
		Outcomes:     map[string]any{"t15": nil, "t60": nil, "t240": nil},
	}
}

// notifyWatch fires a macOS notification via osascript, best-effort.
func notifyWatch(ev WatchEvent) {
	esc := strings.NewReplacer(`\`, `\\`, `"`, `\"`)
	script := fmt.Sprintf(`display notification "%s" with title "WATCHTOWER %s"`,
		esc.Replace(ev.Trigger.Label), esc.Replace(ev.Trigger.ID))
	cmd := exec.Command("osascript", "-e", script)
	if err := cmd.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "watch: notify %s failed: %v\n", ev.Trigger.ID, err)
	}
}

func watchFieldSlug(path string) string {
	s := regexp.MustCompile(`[^A-Za-z0-9]+`).ReplaceAllString(path, "-")
	return strings.Trim(s, "-")
}

func isoWatch(t time.Time) string { return t.Format("2006-01-02T15:04:05Z") }

func roundWatch(v float64, places int) float64 {
	p := math.Pow(10, float64(places))
	return math.Round(v*p) / p
}
