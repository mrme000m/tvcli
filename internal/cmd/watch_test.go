package cmd

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// Synthetic trigger cases mirrored from the python watchtower selftest
// (bin/watchtower.py: do_selftest) against the live ACTIVE_SPEC.json values.

func TestWatchLevelFiresUpCross(t *testing.T) {
	tp2 := WatchTrigger{ID: "TP2", Type: "level", Level: 4539.48, Dir: "up", Mode: "cross"}
	if !LevelFires(tp2, 4500.0, 4540.0) {
		t.Errorf("up-cross 4500->4540 must fire on level 4539.48")
	}
	if LevelFires(tp2, 4550.0, 4560.0) {
		t.Errorf("up-move 4550->4560 entirely above 4539.48 must NOT fire")
	}
}

func TestWatchLevelFiresDownCross(t *testing.T) {
	l1 := WatchTrigger{ID: "L1", Type: "level", Level: 4464.82, Dir: "down", Mode: "cross"}
	if !LevelFires(l1, 4465.5, 4464.0) {
		t.Errorf("down-cross 4465.5->4464.0 must fire on level 4464.82")
	}
	if LevelFires(l1, 4464.0, 4470.0) {
		t.Errorf("up-move from below the level must NOT fire a down trigger")
	}
}

func TestWatchLevelFiresTouchMode(t *testing.T) {
	up := WatchTrigger{ID: "T", Type: "level", Level: 4539.48, Dir: "up", Mode: "touch"}
	if !LevelFires(up, 4550.0, 4540.0) {
		t.Errorf("touch up: cur 4540 >= 4539.48 must fire")
	}
	if LevelFires(up, 4550.0, 4539.0) {
		t.Errorf("touch up: cur 4539 < 4539.48 must NOT fire")
	}
	down := WatchTrigger{ID: "T", Type: "level", Level: 4464.82, Dir: "down", Mode: "touch"}
	if !LevelFires(down, 4464.0, 4464.5) {
		t.Errorf("touch down: cur 4464.5 <= 4464.82 must fire")
	}
	if LevelFires(down, 4464.0, 4465.0) {
		t.Errorf("touch down: cur 4465 > 4464.82 must NOT fire")
	}
	// Empty mode defaults to cross semantics.
	def := WatchTrigger{ID: "T", Type: "level", Level: 4539.48, Dir: "up"}
	if LevelFires(def, 4550.0, 4560.0) {
		t.Errorf("default mode must be cross, not touch")
	}
}

func TestWatchZoneFiresEnter(t *testing.T) {
	z1 := WatchTrigger{ID: "Z1", Type: "zone", Lo: 4490.45, Hi: 4493.54, Dir: "enter"}
	if !ZoneFires(z1, 4497.75, 4492.0) {
		t.Errorf("enter from above 4497.75->4492.0 must fire")
	}
	if !ZoneFires(z1, 4489.0, 4492.0) {
		t.Errorf("enter from below 4489.0->4492.0 must fire")
	}
	if ZoneFires(z1, 4492.5, 4491.0) {
		t.Errorf("already inside 4492.5->4491.0 must NOT fire")
	}
}

func TestWatchPctFires(t *testing.T) {
	up := WatchTrigger{ID: "MV-UP", Type: "pct", Pct: 0.5, Dir: "up"}
	if !PctFires(up, 4497.75, 4520.26) {
		t.Errorf("+0.5%% from 4497.75 must fire at 4520.26")
	}
	if PctFires(up, 4497.75, 4500.0) {
		t.Errorf("+0.5%% from 4497.75 must NOT fire at 4500.0")
	}
	down := WatchTrigger{ID: "MV-DN", Type: "pct", Pct: -0.5, Dir: "down"}
	if !PctFires(down, 4497.75, 4475.24) {
		t.Errorf("-0.5%% from 4497.75 must fire at 4475.24")
	}
	if PctFires(down, 4497.75, 4490.0) {
		t.Errorf("-0.5%% from 4497.75 must NOT fire at 4490.0")
	}
}

func TestWatchTimeFiresAfterMin(t *testing.T) {
	tplus := WatchTrigger{ID: "TPLUS", Type: "time", AfterMin: 120}
	created := time.Date(2026, 9, 3, 15, 40, 0, 0, time.UTC)
	if !TimeFires(tplus, created, created.Add(180*time.Minute)) {
		t.Errorf("afterMin=120 must fire at +180min")
	}
	if TimeFires(tplus, created, created.Add(60*time.Minute)) {
		t.Errorf("afterMin=120 must NOT fire at +60min")
	}
}

func TestWatchTimeFiresAt(t *testing.T) {
	exp := WatchTrigger{ID: "EXP", Type: "time", At: "2026-09-06T21:00:00Z"}
	before := time.Date(2026, 9, 6, 20, 59, 59, 0, time.UTC)
	at := time.Date(2026, 9, 6, 21, 0, 0, 0, time.UTC)
	if TimeFires(exp, time.Time{}, before) {
		t.Errorf("at=21:00 must NOT fire before")
	}
	if !TimeFires(exp, time.Time{}, at) {
		t.Errorf("at=21:00 must fire at the deadline")
	}
}

func TestWatchDig(t *testing.T) {
	fake := map[string]any{
		"market":    map[string]any{"bias": "bearish"},
		"structure": map[string]any{"signal": 0.0, "stDir": 1.0},
	}
	if got := dig(fake, "market.bias"); got != "bearish" {
		t.Errorf("market.bias = %v, want bearish", got)
	}
	if got := dig(fake, "structure.stDir"); got != 1.0 {
		t.Errorf("structure.stDir = %v, want 1", got)
	}
	if got := dig(fake, "structure.missing"); got != nil {
		t.Errorf("missing path must dig nil, got %v", got)
	}
	if got := dig(fake, "market.bias.deeper"); got != nil {
		t.Errorf("path through a scalar must dig nil, got %v", got)
	}
}

func TestWatchEqualValues(t *testing.T) {
	if !equalWatchValues(0.0, 0) {
		t.Errorf("numeric 0.0 and 0 must compare equal")
	}
	if !equalWatchValues("neutral", "neutral") {
		t.Errorf("equal strings must compare equal")
	}
	if equalWatchValues("neutral", "bearish") {
		t.Errorf("different strings must not compare equal")
	}
	if equalWatchValues(nil, "x") {
		t.Errorf("nil must not equal a value")
	}
}

func TestWatchStateRoundTrip(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "S.state.json")
	st := loadWatchState(path, "ep1")
	if len(st.Fired) != 0 {
		t.Fatalf("fresh state must be empty")
	}
	st.Fired["TP2"] = "2026-09-03T16:00:00Z"
	st.Skill = &WatchSkillState{
		LastRun: "2026-09-03T16:00:00Z",
		Values:  map[string]any{"market.bias": "bearish", "structure.stDir": 1.0},
	}
	if err := saveWatchState(path, st); err != nil {
		t.Fatalf("save: %v", err)
	}
	got := loadWatchState(path, "ep1")
	if got.Fired["TP2"] != "2026-09-03T16:00:00Z" {
		t.Errorf("fired round-trip mismatch: %v", got.Fired)
	}
	if got.Skill.Values["market.bias"] != "bearish" {
		t.Errorf("skill values round-trip mismatch: %v", got.Skill.Values)
	}
	// A different spec id resets the state (new episode).
	if other := loadWatchState(path, "ep2"); len(other.Fired) != 0 {
		t.Errorf("state for another specId must start fresh")
	}
}

// TestActiveSpecLoads verifies the live watchtower spec parses into the Go
// types and carries the expected trigger set. Skipped when the spec file is
// not present (it lives outside this repo).
func TestWatchActiveSpecLoads(t *testing.T) {
	const specPath = "/Volumes/ExMac/code/tradingview/agents/watchtower/specs/ACTIVE_SPEC.json"
	if _, err := os.Stat(specPath); err != nil {
		t.Skipf("spec not available: %v", err)
	}
	spec, err := loadWatchSpec(specPath)
	if err != nil {
		t.Fatalf("load spec: %v", err)
	}
	want := map[string]bool{
		"TP2": false, "TP1": false, "Z1": false, "L1": false, "L3": false,
		"S1": false, "S2": false, "MV-UP": false, "MV-DN": false,
		"SIG": false, "TPLUS": false, "EXP": false,
	}
	for _, trig := range spec.Triggers {
		if _, ok := want[trig.ID]; ok {
			want[trig.ID] = true
		}
	}
	for id, seen := range want {
		if !seen {
			t.Errorf("trigger %s missing from spec", id)
		}
	}
	if spec.SkillWatch == nil || spec.SkillWatch.Skill != "xau-scalp" {
		t.Errorf("skillWatch must reference xau-scalp, got %+v", spec.SkillWatch)
	}
	if spec.Symbol != "OANDA:XAUUSD" || spec.TF != "15" {
		t.Errorf("unexpected symbol/tf: %s %s", spec.Symbol, spec.TF)
	}
}
