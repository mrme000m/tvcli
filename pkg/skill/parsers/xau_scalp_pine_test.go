package parsers

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// repoRoot walks up from the current package directory to the repository
// root (the directory containing AGENTS.md).
func repoRoot(t *testing.T) string {
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	dir := filepath.Dir(thisFile)
	for {
		if _, err := os.Stat(filepath.Join(dir, "AGENTS.md")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Fatal("could not locate repo root (AGENTS.md)")
		}
		dir = parent
	}
}

// TestXauScalpPine_SuperTrendVarState is a regression guard for a silent
// headless failure. #xau-scalp uses a stateful SuperTrend (stDir / stLine).
// These MUST be declared with `var`: a self-referential non-var form
// (e.g. `stDir = ... ? nz(stDir[1], 1)`) compiles clean via Pine Facade but
// degrades the whole study to 0 fields / 0 periods at runtime. This test
// nails every committed copy of the script to the safe form so a future edit
// that reintroduces the bug is caught immediately.
func TestXauScalpPine_SuperTrendVarState(t *testing.T) {
	root := repoRoot(t)
	pines := []string{
		filepath.Join(root, "xau-scalp.pine"),
		filepath.Join(root, ".agents", "skills", "tvcli", "assets", "xau-scalp.pine"),
	}
	for _, p := range pines {
		b, err := os.ReadFile(p)
		if err != nil {
			t.Fatalf("read %s: %v", p, err)
		}
		src := string(b)
		// Guard exists and the safe `var` declarations are present.
		if !strings.Contains(src, "HEADLESS-SAFETY GUARD") {
			t.Errorf("%s: missing HEADLESS-SAFETY GUARD comment", p)
		}
		if !strings.Contains(src, "var float stLine = na") || !strings.Contains(src, "var int stDir = 1") {
			t.Errorf("%s: SuperTrend must declare stLine/stDir with `var`", p)
		}
		// The forbidden self-referential non-var form must never reappear in
		// actual code. Scan non-comment lines only, so the explanatory guard
		// comment (which quotes the example) does not trip the check.
		for _, line := range strings.Split(src, "\n") {
			trimmed := strings.TrimSpace(line)
			if strings.HasPrefix(trimmed, "//") || strings.HasPrefix(trimmed, "*") {
				continue
			}
			if strings.Contains(trimmed, "stDir[1]") {
				t.Errorf("%s: forbidden self-referential stDir[1] in a non-var code line (causes silent 0 periods): %q", p, trimmed)
			}
		}
		// The two copies must stay in sync.
	}
	a, _ := os.ReadFile(filepath.Join(root, "xau-scalp.pine"))
	b, _ := os.ReadFile(filepath.Join(root, ".agents", "skills", "tvcli", "assets", "xau-scalp.pine"))
	if string(a) != string(b) {
		t.Errorf("xau-scalp.pine and assets/xau-scalp.pine drifted out of sync")
	}
}
