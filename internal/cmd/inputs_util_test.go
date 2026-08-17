package cmd

import (
	"reflect"
	"testing"

	"github.com/ch99q/tvcli/internal/cli"
)

// TestCollectInputs verifies that all documented Pine-input spellings
// survive the CLI flag parser and reach the runtime as one map.
func TestCollectInputs(t *testing.T) {
	reserved := []string{"symbol", "tf", "bars", "json", "out", "input"}

	// 1. eval-style: spine=1 (script path at positional[0]) + positional key=value
	fs := cli.ParseFlags([]string{"script.pine", "length=20", "src=close"})
	got := collectInputs(fs, 1, reserved)
	want := map[string]string{"length": "20", "src": "close"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("positional: got %v want %v", got, want)
	}

	// 2. run-style: "--input key=value"
	fs = cli.ParseFlags([]string{"PUB;x", "--input", "in_1=4", "--symbol", "BTCUSDT"})
	got = collectInputs(fs, 1, reserved)
	if got["in_1"] != "4" {
		t.Errorf("--input k=v: got %v", got)
	}
	if _, ok := got["symbol"]; ok {
		t.Errorf("reserved symbol leaked: %v", got)
	}

	// 3. comma-separated "--input k1=v1,k2=v2"
	fs = cli.ParseFlags([]string{"PUB;x", "--input=in_0=3,in_2=A"})
	got = collectInputs(fs, 1, reserved)
	if got["in_0"] != "3" || got["in_2"] != "A" {
		t.Errorf("comma input: got %v", got)
	}

	// 4. dotted "--input.k=v" (agent / universal form)
	fs = cli.ParseFlags([]string{"PUB;x", "--input.lookback=50"})
	got = collectInputs(fs, 1, reserved)
	if got["lookback"] != "50" {
		t.Errorf("dotted input: got %v", got)
	}

	// 5. raw TV ID flag "--in_1=4"
	fs = cli.ParseFlags([]string{"PUB;x", "--in_1=4"})
	got = collectInputs(fs, 1, reserved)
	if got["in_1"] != "4" {
		t.Errorf("raw flag: got %v", got)
	}

	// 6. spine positional (the pineId / file path) is never treated as an input
	fs = cli.ParseFlags([]string{"PUB;abc;def"}) // contains ';' not '='
	got = collectInputs(fs, 1, reserved)
	if len(got) != 0 {
		t.Errorf("pineId should not be an input: %v", got)
	}

	// 7. repeated --input flags ALL apply (regression: the flag parser used
	// to keep only the last occurrence, silently dropping earlier ones)
	fs = cli.ParseFlags([]string{"PUB;x", "--input", "in_1=4", "--input", "in_2=8"})
	got = collectInputs(fs, 1, reserved)
	if got["in_1"] != "4" || got["in_2"] != "8" {
		t.Errorf("repeated --input: got %v", got)
	}

	// 8. repeated --input mixed with comma lists, later key wins on conflict
	fs = cli.ParseFlags([]string{"PUB;x", "--input=in_0=3,in_2=A", "--input", "in_2=B"})
	got = collectInputs(fs, 1, reserved)
	if got["in_0"] != "3" || got["in_2"] != "B" {
		t.Errorf("repeated+comma --input: got %v", got)
	}

	// 9. repeated dotted form applies every occurrence in order
	fs = cli.ParseFlags([]string{"PUB;x", "--input.lookback=50", "--input.lookback=99"})
	got = collectInputs(fs, 1, reserved)
	if got["lookback"] != "99" {
		t.Errorf("repeated dotted input: got %v", got)
	}
}
