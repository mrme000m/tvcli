// inputmap.go — show input ID mappings between Go client (tvcli) and browser (bdg).
//
// The bdg-tv-guide.md documents a verified live issue: the created study's `in_N`
// numbering can differ from the `tvcli inputs` listing because hidden inputs shift
// the index. This command helps users discover the correct mapping by fetching
// inputs from both the Pine Facade (Go side) and the live chart (browser side).
package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/internal/service"
	"github.com/mrme000m/tvcli/pkg/tradingview"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
)

type inputMapCmd struct{ app *App }

func (c *inputMapCmd) Name() string     { return "input-map" }
func (c *inputMapCmd) Aliases() []string { return []string{"imap"} }
func (c *inputMapCmd) Synopsis() string {
	return "Show Pine input ID mapping: Go client (tvcli) vs Browser (bdg) — resolves in_N offset issues"
}

func (c *inputMapCmd) Run(env *cli.Env) error {
	flags := env.Flags

	if flags.Has("help") || flags.Has("h") {
		c.printHelp(env)
		return nil
	}

	if len(flags.Positional) == 0 {
		return fmt.Errorf("usage: input-map <pineId|skillName> [--browser-entity <entityId>]")
	}
	arg := flags.Positional[0]

	var pineID string
	if pinefacade.LooksLikePineID(arg) {
		pineID = arg
	} else {
		// Try to resolve skill name
		// We'd need access to skill registry; for now just error
		return fmt.Errorf("skill lookup not yet implemented; use Pine ID directly")
	}

	// 1. Get Go-side inputs from Pine Facade (tvcli's view)
	indicator, err := service.LoadIndicator(c.app.Config, pineID, map[string]string{}, nil)
	if err != nil {
		return fmt.Errorf("load indicator: %w", err)
	}
	goInputs := c.collectGoInputs(indicator)

	// 2. If browser entity ID provided, fetch browser-side inputs via bdg
	browserEntity := flags.Get("browser-entity")
	var browserInputs []BrowserInput
	if browserEntity != "" {
		fmt.Fprintf(env.Stderr, "📡 Fetching browser-side inputs for entity %s...\n", browserEntity)
		browserInputs, err = c.fetchBrowserInputs(browserEntity)
		if err != nil {
			fmt.Fprintf(env.Stderr, "⚠ Browser fetch failed: %v\n", err)
		}
	}

	// 3. Output comparison
	if flags.Has("json") {
		return c.printJSON(env.Stdout, pineID, goInputs, browserInputs)
	}
	c.printTable(env.Stdout, pineID, goInputs, browserInputs)
	return nil
}

type GoInput struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Type     string `json:"type"`
	Defval   any    `json:"defval"`
	IsFake   bool   `json:"isFake"`
	IsHidden bool   `json:"isHidden"`
	Index    int    `json:"index"` // position in InputsOrder
}

type BrowserInput struct {
	ID    string `json:"id"`    // e.g., "in_15"
	Value any    `json:"value"` // current value
	Title string `json:"title"` // human-readable title
}

func (c *inputMapCmd) collectGoInputs(ind *tradingview.PineIndicator) []GoInput {
	var out []GoInput
	for idx, id := range ind.InputsOrder {
		def := ind.Inputs[id]
		if def == nil {
			continue
		}
		out = append(out, GoInput{
			ID:       id,
			Name:     def.Name,
			Type:     def.Type,
			Defval:   def.Value,
			IsFake:   def.IsFake,
			IsHidden: def.IsHidden,
			Index:    idx,
		})
	}
	return out
}

func (c *inputMapCmd) fetchBrowserInputs(entityID string) ([]BrowserInput, error) {
	// Use bdg to get study inputs
	bdgPath := c.resolveBDGPath()
	args := []string{"tv", "study", "inputs", entityID, "-j"}

	bdgParts := strings.Fields(bdgPath)
	cmd := exec.Command(bdgParts[0], append(bdgParts[1:], args...)...)
	output, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("bdg tv study inputs failed: %w", err)
	}

	var result struct {
		ID     string `json:"id"`
		Count  int    `json:"count"`
		Inputs []struct {
			ID    string `json:"id"`
			Value any    `json:"value"`
		} `json:"inputs"`
		Error string `json:"error"`
	}
	if err := json.Unmarshal(output, &result); err != nil {
		return nil, fmt.Errorf("parse bdg output: %w", err)
	}
	if result.Error != "" {
		return nil, fmt.Errorf("bdg error: %s", result.Error)
	}

	var out []BrowserInput
	for _, inp := range result.Inputs {
		out = append(out, BrowserInput{
			ID:    inp.ID,
			Value: inp.Value,
		})
	}
	return out, nil
}

func (c *inputMapCmd) resolveBDGPath() string {
	paths := []string{
		"/Volumes/ExMac/code/tradingview/minimal-mjs/bdg/dist/index.js",
		"/Volumes/Spare/npm/global/bin/bdg",
		"bdg",
	}
	for _, p := range paths {
		if _, err := exec.LookPath(p); err == nil {
			return p
		}
		if strings.HasSuffix(p, ".js") {
			if _, err := os.Stat(p); err == nil {
				return "node " + p
			}
		}
	}
	return "bdg"
}

func (c *inputMapCmd) printTable(w io.Writer, pineID string, goInputs []GoInput, browserInputs []BrowserInput) {
	fmt.Fprintf(w, "Pine: %s\n", pineID)
	fmt.Fprintf(w, "Go-side inputs: %d\n", len(goInputs))
	if len(browserInputs) > 0 {
		fmt.Fprintf(w, "Browser-side inputs: %d\n", len(browserInputs))
	}
	fmt.Fprintln(w, "")

	// Build browser map by ID
	browserByID := make(map[string]BrowserInput)
	for _, bi := range browserInputs {
		browserByID[bi.ID] = bi
	}

	fmt.Fprintf(w, "%-6s %-25s %-10s %-12s %-6s %-6s %-15s %s\n",
		"Index", "Go ID", "Go Type", "Defval", "Fake", "Hidden", "Browser ID", "Match")
	fmt.Fprintln(w, strings.Repeat("-", 110))

	for _, gi := range goInputs {
		match := "—"
		browserID := "—"
		// Try to find matching browser input by name or index
		if bi, ok := browserByID[gi.ID]; ok {
			browserID = bi.ID
			match = "✓ exact"
		} else {
			// Try fuzzy match by index (in_N)
			expectedIn := fmt.Sprintf("in_%d", gi.Index)
			if bi, ok := browserByID[expectedIn]; ok {
				browserID = bi.ID
				match = fmt.Sprintf("≈ index (%s)", expectedIn)
			}
		}
		defVal := fmt.Sprintf("%v", gi.Defval)
		if len(defVal) > 12 {
			defVal = defVal[:12] + "…"
		}
		fmt.Fprintf(w, "%-6d %-25s %-10s %-12s %-6v %-6v %-15s %s\n",
			gi.Index, truncate(gi.ID, 25), gi.Type, defVal, gi.IsFake, gi.IsHidden, browserID, match)
	}

	// Show browser-only inputs
	if len(browserInputs) > 0 {
		goIDs := make(map[string]bool)
		for _, gi := range goInputs {
			goIDs[gi.ID] = true
		}
		fmt.Fprintln(w, "\n--- Browser-only inputs ---")
		for _, bi := range browserInputs {
			if !goIDs[bi.ID] {
				fmt.Fprintf(w, "  %s = %v\n", bi.ID, bi.Value)
			}
		}
	}

	// Key insight
	fmt.Fprintln(w, "\n💡 Key Insight:")
	fmt.Fprintln(w, "  - Go client (tvcli) uses Pine Facade metaInfo input IDs directly")
	fmt.Fprintln(w, "  - Browser (bdg) uses in_N numbering which CAN differ due to hidden/fake inputs")
	fmt.Fprintln(w, "  - Use --browser-entity <entityId> to see the live mapping")
	fmt.Fprintln(w, "  - For bdg 'tv study add --inputs', use the Browser ID column values")
}

func (c *inputMapCmd) printJSON(w io.Writer, pineID string, goInputs []GoInput, browserInputs []BrowserInput) error {
	data := map[string]any{
		"pineId":        pineID,
		"goInputs":      goInputs,
		"browserInputs": browserInputs,
	}
	b, _ := json.MarshalIndent(data, "", "  ")
	fmt.Fprintln(w, string(b))
	return nil
}

func (c *inputMapCmd) printHelp(env *cli.Env) {
	w := env.Stdout
	fmt.Fprintln(w, "input-map — Show Pine input ID mapping (Go vs Browser)")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Usage: tv input-map <pineId> [--browser-entity <entityId>] [--json]")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "The Go client (tvcli) and browser (bdg) use DIFFERENT input ID schemes:")
	fmt.Fprintln(w, "  - tvcli: uses Pine Facade metaInfo IDs (e.g., 'length', 'in_0', 'in_1')")
	fmt.Fprintln(w, "  - bdg: uses in_N numbering from getStudyById().getInputValues()")
	fmt.Fprintln(w, "  - Hidden/fake inputs shift the in_N index — causing mismatches!")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Options:")
	fmt.Fprintln(w, "  --browser-entity ID   Study entity ID from 'bdg tv studies' (e.g., 'E7gFVY')")
	fmt.Fprintln(w, "  --json                Output full JSON comparison")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Examples:")
	fmt.Fprintln(w, "  tv input-map PUB;abc123")
	fmt.Fprintln(w, "  tv input-map PUB;abc123 --browser-entity E7gFVY")
	fmt.Fprintln(w, "  tv input-map PUB;abc123 --browser-entity E7gFVY --json")
}