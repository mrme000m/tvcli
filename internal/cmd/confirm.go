// confirm.go — batch visual confirmation of indicator/strategy scripts on the
// LIVE chart. For each script (pine id) the command:
//
//  1. applies it to the chart via bdg `tv study add --pine <id>`,
//  2. waits `--settle` ms for the Pine visuals to render,
//  3. captures a screenshot,
//  4. removes the study (unless --keep) so the free-tier 2-study cap is not hit,
//  5. records the result into a JSON report.
//
// Sources of script ids, in priority order:
//   - positional pine ids, e.g. `tv confirm "STD;RSI" "STD;MACD"`
//   - `--file builtin-indicators.json` (the built-in STD; catalog produced by
//     `tv indicators`) — default when no positional ids are given.
package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/mrme000m/tvcli/internal/cli"
)

// builtinEntry mirrors one entry of builtin-indicators.json (pineId + title +
// kind). Positional pine ids are wrapped into this shape too.
type builtinEntry struct {
	PineID      string `json:"pineId"`
	Title       string `json:"title"`
	ShortTitle  string `json:"shortTitle"`
	Kind        string `json:"kind"` // "indicator" | "strategy"
	PineVersion string `json:"pineVersion"`
}

// confirmResult is the per-script outcome recorded in confirm-report.json.
type confirmResult struct {
	PineID     string `json:"pineId"`
	Title      string `json:"title"`
	Kind       string `json:"kind"`
	Added      bool   `json:"added"`
	EntityID   string `json:"entityId,omitempty"`
	Screenshot string `json:"screenshot,omitempty"`
	Error      string `json:"error,omitempty"`
}

type confirmCmd struct{ app *App }

func (c *confirmCmd) Name() string     { return "confirm" }
func (c *confirmCmd) Aliases() []string { return []string{"viz-batch", "shots"} }
func (c *confirmCmd) Synopsis() string {
	return "Apply each script to the live chart, screenshot it, and report which rendered"
}

func (c *confirmCmd) Run(env *cli.Env) error {
	flags := env.Flags
	if flags.Has("help") || flags.Has("h") {
		c.printHelp(env)
		return nil
	}

	entries, err := c.loadTargets(flags)
	if err != nil {
		return err
	}

	kind := strings.ToLower(flags.Get("type"))
	limit := flags.GetInt("limit", 0)
	outDir := flags.Get("out")
	if outDir == "" {
		outDir = flags.Get("dir")
	}
	if outDir == "" {
		outDir = "shots"
	}
	settleMs := flags.GetInt("settle", 2500)
	keep := flags.Has("keep")

	targets := entries[:0]
	for _, e := range entries {
		if kind != "" && kind != "all" && strings.ToLower(e.Kind) != kind {
			continue
		}
		targets = append(targets, e)
		if limit > 0 && len(targets) >= limit {
			break
		}
	}
	if len(targets) == 0 {
		return fmt.Errorf("no scripts matched (--type=%s); check --file or positional pine ids", kind)
	}

	if err := os.MkdirAll(outDir, 0755); err != nil {
		return fmt.Errorf("mkdir %s: %w", outDir, err)
	}

	bdg := bdgPath()
	results := make([]confirmResult, 0, len(targets))

	for i, t := range targets {
		fmt.Fprintf(env.Stderr, "\n[%d/%d] %s — %s\n", i+1, len(targets), t.PineID, t.Title)
		res := confirmResult{PineID: t.PineID, Title: t.Title, Kind: t.Kind}

		addedIDs, addErr := addStudyLive(bdg, t.Title, t.PineID)
		if addErr != nil {
			res.Error = addErr.Error()
			fmt.Fprintf(env.Stderr, "  ✗ %s\n", addErr)
			results = append(results, res)
			continue
		}
		res.Added = true
		if len(addedIDs) > 0 {
			res.EntityID = addedIDs[0]
		}
		fmt.Fprintf(env.Stderr, "  ✓ added → %s\n", strings.Join(addedIDs, ", "))

		time.Sleep(time.Duration(settleMs) * time.Millisecond)

		shot := filepath.Join(outDir, sanitizeFileName(t.PineID)+".png")
		if _, err := runBDGCmd(bdg, []string{"dom", "screenshot", shot}); err != nil {
			res.Error = fmt.Sprintf("screenshot: %v", err)
			fmt.Fprintf(env.Stderr, "  ✗ screenshot failed: %v\n", err)
		} else {
			res.Screenshot = shot
			fmt.Fprintf(env.Stderr, "  📸 %s\n", shot)
		}

		if !keep {
			for _, id := range addedIDs {
				if _, err := runBDGCmd(bdg, []string{"tv", "study", "remove", id}); err != nil {
					fmt.Fprintf(env.Stderr, "  ⚠ remove %s: %v\n", id, err)
				}
			}
		}
		results = append(results, res)
	}

	rendered, failed := 0, 0
	for _, r := range results {
		if r.Screenshot != "" {
			rendered++
		} else {
			failed++
		}
	}

	report := map[string]any{
		"capturedAt": time.Now().UTC().Format(time.RFC3339),
		"total":      len(results),
		"rendered":   rendered,
		"failed":     failed,
		"results":    results,
	}
	reportPath := filepath.Join(outDir, "confirm-report.json")
	rb, _ := json.MarshalIndent(report, "", "  ")
	if err := os.WriteFile(reportPath, rb, 0644); err != nil {
		return fmt.Errorf("write report: %w", err)
	}

	fmt.Fprintf(env.Stderr, "\n✓ %d/%d rendered, %d failed\n", rendered, len(results), failed)
	fmt.Fprintf(env.Stderr, "  report: %s\n", reportPath)

	if flags.Has("json") {
		fmt.Fprintln(env.Stdout, string(rb))
	}
	return nil
}

// loadTargets resolves the script list: positional pine ids first, else the
// --file JSON catalog (default builtin-indicators.json).
func (c *confirmCmd) loadTargets(flags cli.Flags) ([]builtinEntry, error) {
	if len(flags.Positional) > 0 {
		out := make([]builtinEntry, 0, len(flags.Positional))
		for _, id := range flags.Positional {
			out = append(out, builtinEntry{
				PineID: id,
				Title:  id,
				Kind:   guessKind(id),
			})
		}
		return out, nil
	}

	file := flags.Get("file")
	if file == "" {
		file = "builtin-indicators.json"
	}
	b, err := os.ReadFile(file)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w (pass pine ids positionally, or run 'tv indicators' first)", file, err)
	}
	var doc struct {
		Indicators []builtinEntry `json:"indicators"`
		Strategies []builtinEntry `json:"strategies"`
	}
	if err := json.Unmarshal(b, &doc); err != nil {
		return nil, fmt.Errorf("parse %s: %w", file, err)
	}
	out := append([]builtinEntry{}, doc.Indicators...)
	out = append(out, doc.Strategies...)
	if len(out) == 0 {
		return nil, fmt.Errorf("no entries in %s", file)
	}
	return out, nil
}

// addStudyLive adds a Pine script by pine id to the live chart via bdg and
// returns the newly-created entity ids. Uses the {type:"pine", pineId,
// pineVersion:"last"} descriptor, which works for USER;/PUB;/STD; ids.
func addStudyLive(bdg, name, pineID string) ([]string, error) {
	args := []string{"tv", "study", "add", name, "-j", "--pine", pineID, "--inputs", "{}"}
	out, err := runBDGCmd(bdg, args)
	if err != nil {
		return nil, err
	}
	var env struct {
		Success bool   `json:"success"`
		Error   string `json:"error"`
		Data    struct {
			Added []string `json:"added"`
			Error string   `json:"error"`
		} `json:"data"`
	}
	if err := json.Unmarshal(out, &env); err != nil {
		return nil, fmt.Errorf("parse bdg add output: %w\n%s", err, string(out))
	}
	if env.Error != "" {
		return nil, fmt.Errorf("%s", env.Error)
	}
	if env.Data.Error != "" {
		return nil, fmt.Errorf("%s", env.Data.Error)
	}
	if len(env.Data.Added) == 0 {
		return nil, fmt.Errorf("no entity id appeared (pine id may not resolve, or free-tier 2-study cap reached)")
	}
	return env.Data.Added, nil
}

// sanitizeFileName converts a pine id into a filesystem-safe basename.
func sanitizeFileName(id string) string {
	repl := strings.NewReplacer(
		";", "_", "%", "_", ":", "_", "/", "_", "\\", "_",
		" ", "_", "\"", "_", "'", "_", "(", "_", ")", "_",
	)
	return repl.Replace(id)
}

// guessKind infers indicator vs strategy from a pine id string.
func guessKind(id string) string {
	if strings.Contains(strings.ToLower(id), "strategy") {
		return "strategy"
	}
	return "indicator"
}

func (c *confirmCmd) printHelp(env *cli.Env) {
	w := env.Stdout
	fmt.Fprintln(w, "confirm — Apply each script to the LIVE chart, screenshot it, report which rendered")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Usage:")
	fmt.Fprintln(w, "  tv confirm \"STD;RSI\" \"STD;MACD\" ...         Positional pine ids")
	fmt.Fprintln(w, "  tv confirm --file builtin-indicators.json      Batch over the built-in catalog")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Requires bdg attached to a chart tab:")
	fmt.Fprintln(w, "  node /Volumes/ExMac/code/tradingview/minimal-mjs/bdg/dist/index.js https://www.tradingview.com/chart/")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Options:")
	fmt.Fprintln(w, "  --file FILE            JSON catalog of scripts (default: builtin-indicators.json)")
	fmt.Fprintln(w, "  --type KIND            Filter: indicator | strategy | all")
	fmt.Fprintln(w, "  --limit N              Stop after N scripts")
	fmt.Fprintln(w, "  --out DIR              Screenshot + report directory (default: shots/)")
	fmt.Fprintln(w, "  --settle MS            Wait for Pine graphics to render (default: 2500)")
	fmt.Fprintln(w, "  --keep                 Keep studies on the chart (default: removed after each)")
	fmt.Fprintln(w, "  --json                 Also print the JSON report to stdout")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Each script is added, screenshotted, then removed (free tier allows only 2")
	fmt.Fprintln(w, "user studies at once). Results land in <out>/confirm-report.json.")
}