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

// TestXauScalpPine_MildSignalOpportunity locks in the "trend without a fresh
// catalyst" entry path. The Pine script only reaches signal==2 when freshLong
// (squeeze release or trend-EMA pullback) fires, which can stay dormant for
// hours during a clean trend. A mild signal (signal==1) with strongly
// one-sided confluence and a high composite magnitude must still emit a
// tradable opportunity — otherwise the executor watches an entire trend
// without a single entry.
func TestXauScalpPine_MildSignalOpportunity(t *testing.T) {
	mildLongBar := func(composite, signal float64) map[string]any {
		return map[string]any{
			"$time":   "2026-08-19T19:52:00Z",
			"plot_0":  composite, // Composite
			"plot_1":  100.0,     // EMA_Stack (bullish)
			"plot_2":  100.0,     // ST_Dir * 100 (bullish)
			"plot_3":  65.0,      // RSI (>55 -> bullish confluence)
			"plot_4":  5.0,       // Sqz_Mom (>0 -> bullish confluence)
			"plot_5":  0.0,       // Squeeze off
			"plot_6":  0.0,       // Sqz_Release (no fresh catalyst)
			"plot_7":  20.0,      // Vol_Delta (>10 -> bullish confluence)
			"plot_8":  60.0,      // BB_Pct (>0 -> bullish confluence)
			"plot_9":  signal,    // Signal raw (50 = mild long)
			"plot_10": 4486.0,    // SL
			"plot_11": 4515.0,    // TP
			"plot_12": 60.0,      // EMA_Slope
			"plot_13": 120.0,     // Vol_Ratio * 100
			"plot_14": 0.0,       // FreshLong (no catalyst)
			"plot_15": 0.0,       // FreshShort
			"Close":   4495.69,
		}
	}

	// Mild long with strong confluence (6/6) + composite 80 -> must emit.
	periods := []map[string]any{
		mildLongBar(75.0, 50.0), // in-progress bar (dropped by latestClosed)
		mildLongBar(80.0, 50.0), // latest closed bar (periods[1])
	}
	res := XauScalpSkill.ParseOutput(periods, nil, "5m", "OANDA:XAUUSD", map[string]string{})
	if len(res.Opportunities) == 0 {
		t.Fatalf("expected a mild-long opportunity, got none (bias=%s, structure=%v)", res.Market.Bias, res.Structure["signal"])
	}
	opp := res.Opportunities[0]
	if opp.Direction != "long" {
		t.Errorf("expected long direction, got %q", opp.Direction)
	}
	if !strings.Contains(opp.Setup, "mild long") {
		t.Errorf("expected setup to mention 'mild long', got %q", opp.Setup)
	}
	if opp.Entry == 0 || opp.StopLoss == 0 || opp.TP1 == 0 {
		t.Errorf("expected real SL/TP levels, got entry=%v sl=%v tp=%v", opp.Entry, opp.StopLoss, opp.TP1)
	}

	// Mild long with weak composite (<30) must NOT emit (below the confluence
	// quality bar; avoids noise chasing).
	periodsWeak := []map[string]any{
		mildLongBar(20.0, 50.0),
		mildLongBar(20.0, 50.0),
	}
	resWeak := XauScalpSkill.ParseOutput(periodsWeak, nil, "5m", "OANDA:XAUUSD", map[string]string{})
	for _, o := range resWeak.Opportunities {
		if o.Direction == "long" || o.Direction == "short" {
			t.Errorf("weak composite should not emit a directional opportunity, got %q", o.Direction)
		}
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
