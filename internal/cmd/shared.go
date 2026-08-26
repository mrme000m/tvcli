// Package cmd holds the tvcli subcommands, each implementing cli.Command.
// One file per command. The package wires itself into a cli.Root via Register.
package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/internal/config"
	"github.com/mrme000m/tvcli/pkg/skill"
	_ "github.com/mrme000m/tvcli/pkg/skill/parsers"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
	"github.com/mrme000m/tvcli/pkg/pipeline"
)

// App is the shared context passed to every command: the loaded config plus
// any long-lived resources. Keep this small — it's the only seam between
// the command layer and the rest of the binary.
type App struct {
	Config *config.Config
}

// NewApp builds an App from a loaded config.
func NewApp(cfg *config.Config) *App { return &App{Config: cfg} }

// RegisterAll wires all built-in commands into root.
func RegisterAll(root *cli.Root, app *App) {
	root.Add(&createCmd{app: app})
	root.Add(&pullCmd{app: app})
	root.Add(&pushCmd{app: app})
	root.Add(&deleteCmd{app: app})
	root.Add(&listCmd{app: app})
	root.Add(&searchCmd{app: app})
	root.Add(&publistCmd{app: app})
	root.Add(&topCmd{app: app})
	root.Add(&compileCmd{app: app})
	root.Add(&runCmd{app: app})
	root.Add(&backtestCmd{app: app})
	root.Add(&fetchCmd{app: app})
	root.Add(&confirmCmd{app: app})
	root.Add(&syncCmd{app: app})
	root.Add(&inputsCmd{app: app})
	root.Add(&evalCmd{app: app})
	root.Add(&cleanCmd{app: app})
	root.Add(&checkAuthCmd{app: app})
	root.Add(&accountCmd{app: app})
	root.Add(&layoutsCmd{app: app})
	root.Add(&serveCmd{app: app})
	root.Add(&agentCmd{app: app})
	root.Add(&universalCmd{app: app})
	root.Add(&screenshotCmd{app: app})
	root.Add(&inputMapCmd{app: app})
	root.Add(&visualCmd{app: app})
	root.Add(&tfCmd{app: app})
	root.Add(&symCmd{app: app})
	root.Add(&studyCmd{app: app})
	root.Add(&scanCmd{app: app})
	RegisterSkills(root, app)
}

// RegisterSkills adds all indicator skill commands to root.
func RegisterSkills(root *cli.Root, app *App) {
	for _, s := range skill.All() {
		root.Add(&skillCmd{app: app, skill: s})
	}
	root.Add(&skillsCmd{})
}

// durationFromMs converts the cfg.Timeout (int milliseconds) into a time.Duration.
func durationFromMs(ms int) time.Duration {
	return time.Duration(ms) * time.Millisecond
}

// signalsToAgent converts the generic schema-guided Signals output into the
// same agent-ready-v2 envelope used by the hand-coded skill parsers. This is
// the bridge that lets any Pine script behave like a skill command.
func signalsToAgent(signals *pipeline.Signals, workflow, symbol, tf string, durationMs int64) skill.AgentResult {
	result := skill.SkillResult{
		Status:   "ok",
		Workflow: workflow,
		Market: skill.MarketData{
			LastPrice: pickLastPrice(signals),
			Bias:      signals.Bias,
		},
		Structure:     buildStructure(signals),
		Opportunities: nonNilOpps(opportunitiesFromSignals(signals)),
		Narrative: skill.Narrative{
			MarketStructure: fmt.Sprintf("%s | confidence=%.2f | fields=%d", signals.Bias, signals.Confidence, len(signals.Classifications)),
			Warnings:        signals.Warnings,
		},
		Validation: skill.Validation{
			Passed:   len(signals.Warnings) == 0,
			Warnings: signals.Warnings,
		},
		Conformance: skill.Conformance{
			HasValidData: signals.Meta.PeriodCount > 0 || len(signals.Levels) > 0 || len(signals.Events) > 0 || len(signals.GraphicCounts) > 0,
			AgenticScore: signals.Confidence,
		},
	}
	generic := &skill.Skill{Name: workflow}
	return generic.ToAgent(result, symbol, tf, durationMs)
}

// pickLastPrice chooses the market price from the extracted signals payload
// for the agent-ready envelope.
//
// Only a field literally named Close/close is trustworthy: every other
// price-classified plot is a study output (VAH, bands, SuperTrend lines,
// ...), and reporting it as market.lastPrice fabricates a price. When no
// Close plot exists we leave lastPrice nil instead of guessing — callers that
// need a price for such scripts back-fill it from OHLCV (see skillcmd.go).
func pickLastPrice(signals *pipeline.Signals) any {
	for f, v := range signals.Last {
		if strings.EqualFold(f, "close") {
			if fv, ok := v.(float64); ok && fv > 0 && fv < 1e50 {
				return v
			}
		}
	}
	return nil
}

// nonNilOpps ensures JSON emits [] instead of null for an empty slice.
func nonNilOpps(opps []skill.Opportunity) []skill.Opportunity {
	if opps == nil {
		return []skill.Opportunity{}
	}
	return opps
}

// buildStructure creates the Structure map for the agent-ready envelope,
// including strategy report data when available.
func buildStructure(signals *pipeline.Signals) map[string]any {
	// Expose the script kind explicitly so consumers can distinguish strategy
	// output (trade-driven) from indicator output (plot-driven). Defaults to
	// "indicator" when the extractor left it unset.
	kind := signals.Meta.ScriptType
	if kind == "" {
		kind = pipeline.ScriptTypeIndicator
	}
	structure := map[string]any{
		"kind":            kind,
		"classifications": signals.Classifications,
		"last":            signals.Last,
		"series":          signals.Series,
		"levels":          nonNilLevels(signals.Levels),
		"events":          nonNilEvents(signals.Events),
		"graphicCounts":   signals.GraphicCounts,
		"meta":            signals.Meta,
	}
	// Only strategies carry a strategy summary; indicators get none.
	if kind == pipeline.ScriptTypeStrategy && signals.Report != nil {
		structure["strategy"] = signals.Report
	}
	return structure
}

// nonNilEvents ensures JSON emits [] instead of null for an empty slice.
func nonNilEvents(events []pipeline.Event) []pipeline.Event {
	if events == nil {
		return []pipeline.Event{}
	}
	return events
}

// nonNilLevels ensures JSON emits [] instead of null for an empty slice.
func nonNilLevels(levels []pipeline.Level) []pipeline.Level {
	if levels == nil {
		return []pipeline.Level{}
	}
	return levels
}

// opportunitiesFromSignals turns buy/sell events into opportunities.
func opportunitiesFromSignals(signals *pipeline.Signals) []skill.Opportunity {
	var opps []skill.Opportunity
	for _, ev := range signals.Events {
		dir := ""
		switch ev.Kind {
		case "buy":
			dir = "long"
		case "sell":
			dir = "short"
		}
		if dir == "" {
			continue
		}
		opps = append(opps, skill.Opportunity{
			Rank:            len(opps) + 1,
			Setup:           ev.Field,
			Direction:       dir,
			Confidence:      "MED",
			ConfluenceScore: 0.6,
			Rationale:       fmt.Sprintf("%s event at %.2f", ev.Kind, ev.Value),
		})
	}
	return opps
}

// runPubList lists the user's public scripts and prints to w (matching the
// legacy `list --public` / `publist` behavior).
func runPubList(cfg *config.Config, flags cli.Flags, w io.Writer) error {
	offset := flags.GetInt("offset", 0)
	limit := flags.GetInt("limit", 20)
	asJSON := flags.Has("json")

	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, durationFromMs(cfg.Timeout))
	data, err := client.ListPublicScripts(offset)
	if err != nil {
		return fmt.Errorf("list public: %w", err)
	}

	items := NormalizeSearchResults(data, limit)

	if asJSON {
		b, _ := json.MarshalIndent(map[string]any{
			"offset":  offset,
			"limit":   limit,
			"count":   len(items),
			"results": items,
		}, "", "  ")
		fmt.Fprintln(w, string(b))
	} else {
		next := ExtractNext(data)
		fmt.Fprintf(w, "\nPublic scripts: %d (offset=%d, next=%v)\n\n", len(items), offset, next)
		PrintSearchTable(w, items)
	}
	return nil
}
