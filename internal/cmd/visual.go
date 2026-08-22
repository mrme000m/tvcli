// visual.go — add a Pine script to the LIVE chart with custom inputs, wait
// for it to render, then capture a screenshot of the indicator visuals.
//
// This is the frontend-facing counterpart to `tv analyze` / `tv run` (which
// drive studies headlessly over the WebSocket API): `visual` drives the live
// chart widget through bdg so the analysis is VISIBLE on the chart, with the
// exact custom inputs the user requested, and returns a screenshot proving it.
//
// Workflow:
//   1. `bdg tv study add "<name>" --pine <id> --inputs '<json>' -j` — add the
//      script with custom input overrides (matched against input id OR title).
//   2. Wait `--settle` ms so Pine graphics (lines/labels/boxes/tables)
//      materialize on the chart.
//   3. `bdg dom screenshot <out>` — capture the chart (or --selector region).
package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/mrme000m/tvcli/internal/cli"
)

type visualCmd struct{ app *App }

func (c *visualCmd) Name() string     { return "visual" }
func (c *visualCmd) Aliases() []string { return []string{"viz", "show"} }
func (c *visualCmd) Synopsis() string {
	return "Add any Pine script to the live chart with custom inputs, then screenshot the rendered visuals"
}

func (c *visualCmd) Run(env *cli.Env) error {
	flags := env.Flags

	if flags.Has("help") || flags.Has("h") {
		c.printHelp(env)
		return nil
	}

	name := flags.Get("name")
	pineID := flags.Get("pine")
	if name == "" && pineID == "" {
		// Try positional: first is the display name or pine id.
		if len(flags.Positional) > 0 {
			name = flags.Positional[0]
		}
	}
	if name == "" && pineID == "" {
		return fmt.Errorf("usage: visual <displayName|pineId> [--pine <pineId>] [--inputs '<json>'] [--out file.png]")
	}

	inputsJSON := flags.Get("inputs")
	if inputsJSON == "" {
		inputsJSON = "{}"
	}
	outputPath := flags.Get("out")
	if outputPath == "" && len(flags.Positional) > 1 {
		outputPath = flags.Positional[1]
	}
	if outputPath == "" {
		ts := time.Now().Format("20060102-150405")
		outputPath = fmt.Sprintf("tv-visual-%s.png", ts)
	}
	settleMs := flags.GetInt("settle", 4000) // Pine graphics need a beat to materialize
	fullPage := flags.Has("full") || flags.Has("full-page")
	selector := flags.Get("selector")
	keep := flags.Has("keep") // keep the study on the chart after capture
	verbose := flags.Has("verbose")

	bdgPath := c.resolveBDGPath()

	// 1. Add study with custom inputs via bdg. bdg's `study add` requires a
	// name positional; when adding by pine id the name is unused by
	// createStudy, so fall back to the pine id itself.
	addName := name
	if addName == "" {
		addName = pineID
	}
	args := []string{"tv", "study", "add", addName, "-j"}
	if pineID != "" {
		args = append(args, "--pine", pineID)
	}
	args = append(args, "--inputs", inputsJSON)

	fmt.Fprintf(env.Stderr, "🖼  Adding '%s' to the live chart with inputs %s...\n", addName, inputsJSON)
	if verbose {
		fmt.Fprintf(env.Stderr, "   %s %s\n", bdgPath, strings.Join(args, " "))
	}

	addOut, err := c.runBDG(bdgPath, args)
	if err != nil {
		return fmt.Errorf("add study: %w", err)
	}
	// bdg -j wraps results in {version, success, data:{...}}; unwrap data.
	// On failure the envelope is {version, success:false, error:msg, ...}.
	var addEnvelope struct {
		Success bool   `json:"success"`
		Error   string `json:"error"`
		Data    struct {
			Added    []string `json:"added"`
			Error    string   `json:"error"`
			Inputs   *struct {
				Matched   []map[string]any `json:"matched"`
				Unmatched []string         `json:"unmatched"`
				Error     string           `json:"error"`
			} `json:"inputsApplied"`
		} `json:"data"`
	}
	if err := json.Unmarshal(addOut, &addEnvelope); err != nil {
		// bdg returned non-JSON (e.g. human formatter); show raw and continue.
		fmt.Fprintln(env.Stderr, string(addOut))
	} else {
		addRes := &addEnvelope.Data
		if addEnvelope.Error != "" {
			return fmt.Errorf("bdg add failed: %s", addEnvelope.Error)
		}
		if addRes.Error != "" {
			return fmt.Errorf("bdg add failed: %s", addRes.Error)
		}
		if len(addRes.Added) == 0 {
			return fmt.Errorf("no new entity id appeared (name may not resolve, or free-tier 2-study cap reached); raw bdg output:\n%s", string(addOut))
		}
		fmt.Fprintf(env.Stderr, "✓ Added study → entity id: %s\n", strings.Join(addRes.Added, ", "))
		if addRes.Inputs != nil {
			if addRes.Inputs.Error != "" {
				fmt.Fprintf(env.Stderr, "⚠ Inputs could not be applied: %s\n", addRes.Inputs.Error)
			} else {
				matched := make([]string, 0, len(addRes.Inputs.Matched))
				for _, m := range addRes.Inputs.Matched {
					id, _ := m["id"].(string)
					val, _ := m["value"].(any)
					matched = append(matched, fmt.Sprintf("%s=%v", id, val))
				}
				fmt.Fprintf(env.Stderr, "✓ Inputs applied: %s\n", strings.Join(matched, ", "))
				if len(addRes.Inputs.Unmatched) > 0 {
					fmt.Fprintf(env.Stderr, "⚠ Inputs unmatched: %s (use the title or canonical id from 'tvcli inputs')\n", strings.Join(addRes.Inputs.Unmatched, ", "))
				}
			}
		}
		// Track added ids for the cleanup step.
		addedIDs := addRes.Added
		defer c.cleanupStudy(bdgPath, addedIDs, keep, env)
	}

	// 2. Wait for graphics to render.
	fmt.Fprintf(env.Stderr, "⏳ Waiting %d ms for Pine graphics to render...\n", settleMs)
	time.Sleep(time.Duration(settleMs) * time.Millisecond)

	// 3. Screenshot.
	shotArgs := []string{"dom", "screenshot", outputPath}
	if fullPage {
		shotArgs = append(shotArgs, "--full-page")
	}
	if selector != "" {
		shotArgs = append(shotArgs, "--selector", selector)
	}
	fmt.Fprintf(env.Stderr, "📸 Capturing screenshot...\n")
	if _, err := c.runBDG(bdgPath, shotArgs); err != nil {
		return fmt.Errorf("screenshot: %w", err)
	}

	fmt.Fprintf(env.Stderr, "✓ Screenshot saved: %s\n", outputPath)
	return nil
}

// cleanupStudy removes the studies added by visual unless --keep was given.
// Called via defer so a screenshot failure still frees the study slot.
func (c *visualCmd) cleanupStudy(bdgPath string, addedIDs []string, keep bool, env *cli.Env) {
	if keep || len(addedIDs) == 0 {
		return
	}
	for _, id := range addedIDs {
		fmt.Fprintf(env.Stderr, "🧹 Removing study %s (use --keep to leave it on the chart)...\n", id)
		rmArgs := []string{"tv", "study", "remove", id}
		if _, err := c.runBDG(bdgPath, rmArgs); err != nil {
			fmt.Fprintf(env.Stderr, "⚠ Could not remove study %s: %v\n", id, err)
		} else {
			fmt.Fprintf(env.Stderr, "✓ Study %s removed.\n", id)
		}
	}
}

// runBDG executes bdg with the given args and returns its stdout.
func (c *visualCmd) runBDG(bdgPath string, args []string) ([]byte, error) {
	bdgParts := strings.Fields(bdgPath)
	cmd := exec.Command(bdgParts[0], append(bdgParts[1:], args...)...)
	cmd.Stderr = os.Stderr
	return cmd.Output()
}

// resolveBDGPath returns the bdg executable; when bdg isn't on PATH, the
// local dist entrypoint is used via `node <entry>`.
func (c *visualCmd) resolveBDGPath() string {
	paths := []string{
		"/Volumes/ExMac/code/tradingview/minimal-mjs/bdg/dist/index.js",
		"/Volumes/Spare/npm/global/bin/bdg",
		"bdg",
	}
	for _, p := range paths {
		if strings.HasSuffix(p, ".js") {
			if _, err := os.Stat(p); err == nil {
				return "node " + p
			}
			continue
		}
		if _, err := exec.LookPath(p); err == nil {
			return p
		}
	}
	return "bdg"
}

func (c *visualCmd) printHelp(env *cli.Env) {
	w := env.Stdout
	fmt.Fprintln(w, "visual — Add a Pine script to the LIVE chart with custom inputs, then screenshot it")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Usage: tv visual <displayName|pineId> [options]")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "This drives the live TradingView chart (through bdg) so the indicator's")
	fmt.Fprintln(w, "visuals are actually RENDERED on the chart — the same script you analyze")
	fmt.Fprintln(w, "headlessly with 'tv analyze'. Requires bdg attached to a chart tab:")
	fmt.Fprintln(w, "  node /Volumes/ExMac/code/tradingview/minimal-mjs/bdg/dist/index.js https://www.tradingview.com/chart/")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Options:")
	fmt.Fprintln(w, "  --name NAME            Display name as the indicators dialog resolves it")
	fmt.Fprintln(w, "  --pine ID              Pine id (USER;/PUB;) — adds any saved/public script")
	fmt.Fprintln(w, "  --inputs '<json>'      Custom input overrides, e.g. '{\"in_15\": 30, \"length\": 21}'")
	fmt.Fprintln(w, "  --out FILE             Screenshot output path (default: tv-visual-TIMESTAMP.png)")
	fmt.Fprintln(w, "  --settle MS            Wait for graphics to render (default: 4000)")
	fmt.Fprintln(w, "  --full, --full-page    Capture the full page instead of the viewport")
	fmt.Fprintln(w, "  --selector CSS         Capture a specific element (e.g. '.chart-container')")
	fmt.Fprintln(w, "  --keep                 Keep the study on the chart after capture (default: removed)")
	fmt.Fprintln(w, "  --verbose              Show the underlying bdg commands")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Examples:")
	fmt.Fprintln(w, "  tv visual \"Smart Money Concepts\" --pine PUB;6daafb2cabe6419d98ae25229d2327f8 \\")
	fmt.Fprintln(w, "      --inputs '{\"in_17\": 30}' --out smc.png")
	fmt.Fprintln(w, "  tv visual \"Relative Strength Index\" --inputs '{\"length\": 21}' --keep")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Notes:")
	fmt.Fprintln(w, "  - Free tier: max 2 user studies per chart — remove one first if the add silently no-ops.")
	fmt.Fprintln(w, "  - Input ids on the browser side (in_N) can differ from 'tvcli inputs' — run")
	fmt.Fprintln(w, "    'tv input-map <pineId> --browser-entity <entityId>' to see the live mapping.")
	fmt.Fprintln(w, "  - After applying inputs, heavy scripts may take a while for Pine graphics to appear;")
	fmt.Fprintln(w, "    increase --settle if the screenshot looks empty.")
}