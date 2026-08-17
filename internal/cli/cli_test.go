package cli

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"strings"
	"testing"
)

func TestParseFlagsLong(t *testing.T) {
	fs := ParseFlags([]string{"--symbol", "BTCUSDT", "--bars=100", "--json"})
	if fs.Get("symbol") != "BTCUSDT" {
		t.Errorf("symbol = %q, want BTCUSDT", fs.Get("symbol"))
	}
	if fs.GetInt("bars", 0) != 100 {
		t.Errorf("bars = %d, want 100", fs.GetInt("bars", 0))
	}
	if !fs.Has("json") {
		t.Error("json flag not set")
	}
}

func TestParseFlagsBool(t *testing.T) {
	fs := ParseFlags([]string{"--verbose"})
	if !fs.Has("verbose") {
		t.Error("verbose should be true")
	}
	if fs.Get("verbose") != "true" {
		t.Errorf("verbose Get = %q, want \"true\"", fs.Get("verbose"))
	}
}

func TestParseFlagsShort(t *testing.T) {
	fs := ParseFlags([]string{"-n", "5"})
	if fs.Get("n") != "5" {
		t.Errorf("n = %q, want 5", fs.Get("n"))
	}
}

func TestParseFlagsPositional(t *testing.T) {
	// `--flag c` consumes `c` as flag's value, so only a and b are positional.
	fs := ParseFlags([]string{"a", "b", "--flag", "c"})
	if len(fs.Positional) != 2 {
		t.Fatalf("positional len = %d, want 2: %v", len(fs.Positional), fs.Positional)
	}
	if fs.Positional[0] != "a" || fs.Positional[1] != "b" {
		t.Errorf("positional = %v, want [a b]", fs.Positional)
	}
	if fs.Get("flag") != "c" {
		t.Errorf("flag = %q, want c", fs.Get("flag"))
	}

	// A `--` style separator isn't supported by ParseFlags (matches the
	// original parseFlags behavior). Verify multiple positionals before
	// any flag are captured in order.
	fs2 := ParseFlags([]string{"x", "y", "z"})
	if len(fs2.Positional) != 3 || fs2.Positional[2] != "z" {
		t.Errorf("positional = %v, want [x y z]", fs2.Positional)
	}
}

func TestGetIntDefault(t *testing.T) {
	fs := ParseFlags([]string{})
	if fs.GetInt("missing", 42) != 42 {
		t.Error("default not returned for missing key")
	}
}

func TestAllCopiesMap(t *testing.T) {
	fs := ParseFlags([]string{"--a=1", "--b=2"})
	all := fs.All()
	if all["a"] != "1" || all["b"] != "2" {
		t.Errorf("All() = %v, want a=1 b=2", all)
	}
	// Mutating the returned map must not affect the Flags.
	all["a"] = "X"
	if fs.Get("a") != "1" {
		t.Error("All() returned map is not a copy")
	}
}

func TestParseFlagsRepeated(t *testing.T) {
	// Last-wins for Get, but every occurrence is preserved via GetAll.
	fs := ParseFlags([]string{"--input", "a=1", "--input", "b=2", "--input=c=3"})
	if fs.Get("input") != "c=3" {
		t.Errorf("Get(input) = %q, want last occurrence c=3", fs.Get("input"))
	}
	all := fs.GetAll("input")
	if len(all) != 3 || all[0] != "a=1" || all[1] != "b=2" || all[2] != "c=3" {
		t.Errorf("GetAll(input) = %v, want [a=1 b=2 c=3]", all)
	}
	// Mutating the returned slice must not affect the Flags.
	all[0] = "X"
	if fs.GetAll("input")[0] != "a=1" {
		t.Error("GetAll returned slice is not a copy")
	}
	if fs.GetAll("missing") != nil && len(fs.GetAll("missing")) != 0 {
		t.Error("GetAll(missing) should be empty")
	}
	m := fs.AllMulti()
	if len(m["input"]) != 3 {
		t.Errorf("AllMulti()[input] = %v, want 3 occurrences", m["input"])
	}
}

// stubCmd is a minimal Command for dispatch tests.
type stubCmd struct {
	name    string
	aliases []string
	called  bool
	err     error
}

func (s *stubCmd) Name() string         { return s.name }
func (s *stubCmd) Aliases() []string     { return s.aliases }
func (s *stubCmd) Synopsis() string      { return "stub" }
func (s *stubCmd) Run(env *Env) error    { s.called = true; return s.err }

func TestRootDispatch(t *testing.T) {
	root := NewRoot()
	c := &stubCmd{name: "list", aliases: []string{"ls"}}
	root.Add(c)

	if root.Lookup("list") != c {
		t.Error("Lookup by name failed")
	}
	if root.Lookup("ls") != c {
		t.Error("Lookup by alias failed")
	}
	if root.Lookup("nope") != nil {
		t.Error("Lookup of unknown should be nil")
	}

	if err := root.Execute([]string{"list"}, io.Discard, io.Discard); err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if !c.called {
		t.Error("Run was not called")
	}

	// alias dispatch
	c.called = false
	if err := root.Execute([]string{"ls"}, io.Discard, io.Discard); err != nil {
		t.Fatalf("Execute alias: %v", err)
	}
	if !c.called {
		t.Error("Run was not called via alias")
	}
}

func TestRootUnknownCommand(t *testing.T) {
	root := NewRoot()
	var stderr bytes.Buffer
	err := root.Execute([]string{"nope"}, io.Discard, &stderr)
	if err == nil {
		t.Fatal("expected error for unknown command")
	}
	if !strings.Contains(stderr.String(), "Unknown command: nope") {
		t.Errorf("stderr = %q, want it to contain 'Unknown command: nope'", stderr.String())
	}
}

func TestRootHelp(t *testing.T) {
	root := NewRoot()
	root.Add(&stubCmd{name: "list"})
	root.Add(&stubCmd{name: "search"})

	var out bytes.Buffer
	if err := root.Execute([]string{}, &out, io.Discard); err != nil {
		t.Fatalf("Execute empty: %v", err)
	}
	if !strings.Contains(out.String(), "list") || !strings.Contains(out.String(), "search") {
		t.Errorf("help output missing command names: %q", out.String())
	}

	// explicit help
	out.Reset()
	if err := root.Execute([]string{"help"}, &out, io.Discard); err != nil {
		t.Fatalf("Execute help: %v", err)
	}
	if !strings.Contains(out.String(), "list") {
		t.Errorf("help output missing command: %q", out.String())
	}
}

func TestRootCommandErrorPropagates(t *testing.T) {
	root := NewRoot()
	root.Add(&stubCmd{name: "boom", err: errors.New("kaboom")})
	err := root.Execute([]string{"boom"}, io.Discard, io.Discard)
	if err == nil || err.Error() != "kaboom" {
		t.Errorf("Execute returned %v, want kaboom", err)
	}
}

func TestRootSetHelp(t *testing.T) {
	root := NewRoot()
	root.SetHelp(func(_ *Root, w io.Writer) { fmt.Fprintln(w, "custom help") })
	var out bytes.Buffer
	root.Execute([]string{}, &out, io.Discard)
	if out.String() != "custom help\n" {
		t.Errorf("custom help not used: %q", out.String())
	}
}
