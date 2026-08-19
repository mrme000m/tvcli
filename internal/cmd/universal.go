package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/mrme000m/tvcli/internal/agent"
	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/internal/config"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
)

type universalCmd struct {
	app *App
}

func (c *universalCmd) Name() string      { return "analyze" }
func (c *universalCmd) Aliases() []string { return []string{"universal", "ua"} }
func (c *universalCmd) Synopsis() string {
	return "Universal script analyzer - auto-analyze any Pine script"
}

func (c *universalCmd) Run(env *cli.Env) error {
	flags := env.Flags

	if flags.Has("help") || flags.Has("h") {
		c.printHelp(env)
		return nil
	}

	cfg := c.app.Config

	// Parse required pineId
	pineID := flags.Get("pine")
	if pineID == "" {
		// Try first positional arg
		if len(flags.Positional) > 0 {
			pineID = flags.Positional[0]
		}
	}
	if pineID == "" {
		return fmt.Errorf("pine script ID required (use --pine or positional argument)")
	}

	// Parse configuration
	symbol := flags.Get("symbol")
	if symbol == "" {
		symbol = "OANDA:XAUUSD"
	}
	normalizedSymbol, err := pinefacade.ValidateSymbol(symbol)
	if err != nil {
		return fmt.Errorf("invalid symbol: %v", err)
	}
	symbol = normalizedSymbol

	tf := flags.Get("tf")
	if tf == "" {
		tf = flags.Get("timeframe")
	}
	if tf == "" {
		tf = "5m"
	}

	limits := config.GetTierLimits()
	bars := flags.GetInt("bars", 500)
	if limits.MaxBars > 0 && bars > limits.MaxBars {
		fmt.Fprintf(env.Stderr, "⚠ Capping bars from %d to %d (tier limit)\n", bars, limits.MaxBars)
		bars = limits.MaxBars
	}
	settle := flags.GetInt("settle", 1500)
	timeout := flags.GetInt("timeout", 120)
	debug := flags.Has("verbose") || cfg.Debug
	forceSchema := flags.Has("force-schema")
	listInputs := flags.Has("list-inputs")
	validateInputs := flags.Has("validate-inputs")

	// Parse inputs. Supports "--input.key=VALUE" (documented), the "--input
	// key=value" spelling, and positional "key=value" args after the pineId.
	// All end up keyed the same way in UniversalAnalyzerConfig.Inputs and are
	// resolved to canonical TV input IDs (by ID, index, or name) inside the
	// analyzer / SetOption.
	univReserved := []string{
		"symbol", "tf", "timeframe", "bars", "pine", "json", "report",
		"format", "title", "out", "settle", "timeout", "force-schema",
		"list-inputs", "validate-inputs", "verbose", "help", "h", "input",
		"force-cleanup", "cleanup",
	}
	inputs := collectInputs(flags, 1, univReserved)

	// Build analyzer config
	forceCleanup := flags.Has("force-cleanup") || flags.Has("cleanup")

	analyzerConfig := agent.UniversalAnalyzerConfig{
		Symbol:         symbol,
		Timeframe:      tf,
		Bars:           bars,
		Inputs:         inputs,
		ForceSchema:    forceSchema,
		Debug:          debug,
		SettleMs:       settle,
		Timeout:        time.Duration(timeout) * time.Second,
		ValidateInputs: validateInputs,
		ListInputsOnly: listInputs,
		ForceCleanup:   forceCleanup,
	}

	// Create and run analyzer
	analyzer := agent.NewUniversalAnalyzer(cfg, analyzerConfig)

	ctx := context.Background()
	result, err := analyzer.Analyze(ctx, pineID)
	if err != nil {
		return fmt.Errorf("analysis failed: %w", err)
	}

	// Handle list-inputs mode
	if listInputs {
		return c.emitInputsList(env, result, flags)
	}

	// Output
	if flags.Has("json") {
		return c.emitJSON(env, result, flags)
	}

	if flags.Has("report") {
		return c.emitReport(env, result, flags)
	}

	// Default: text output
	text := agent.FormatUniversal(result)
	fmt.Fprintln(env.Stdout, text)
	return nil
}

func (c *universalCmd) emitInputsList(env *cli.Env, result *agent.UniversalResult, flags cli.Flags) error {
	if result.Raw == nil || result.Raw.Schema == nil {
		return fmt.Errorf("no schema available")
	}
	sch := result.Raw.Schema

	if flags.Has("json") {
		b, err := json.MarshalIndent(sch.Inputs, "", "  ")
		if err != nil {
			return err
		}
		out := flags.Get("out")
		if out != "" {
			return os.WriteFile(out, b, 0644)
		}
		fmt.Fprintln(env.Stdout, string(b))
		return nil
	}

	// Text output
	w := env.Stdout
	fmt.Fprintf(w, "Inputs for %s (%s):\n\n", result.ScriptInfo.Name, result.ScriptInfo.PineID)
	fmt.Fprintf(w, "%-25s %-12s %-10s %s\n", "ID", "Type", "Default", "Description")
	fmt.Fprintln(w, strings.Repeat("-", 80))

	for _, inp := range sch.Inputs {
		defVal := ""
		if inp.Default != nil {
			defVal = fmt.Sprintf("%v", inp.Default)
		}
		desc := inp.Tooltip
		if desc == "" {
			desc = inp.Name
		}
		if len(desc) > 40 {
			desc = desc[:37] + "..."
		}
		fmt.Fprintf(w, "%-25s %-12s %-10s %s\n", inp.ID, inp.Type, defVal, desc)
		if inp.Min != nil || inp.Max != nil {
			fmt.Fprintf(w, "  min=%v max=%v\n", inp.Min, inp.Max)
		}
		if len(inp.Options) > 0 {
			fmt.Fprintf(w, "  options: %v\n", inp.Options)
		}
	}
	return nil
}

func (c *universalCmd) emitJSON(env *cli.Env, result *agent.UniversalResult, flags cli.Flags) error {
	out := flags.Get("out")
	// Create JSON-serializable version
	data := map[string]any{
		"script":  result.ScriptInfo,
		"market":  result.MarketData,
		"signals": result.Signals,
		"graphic": result.GraphicData,
		"summary": result.Summary,
		"agent":   result.AgentEnvelope,
	}
	b, err := json.MarshalIndent(data, "", "  ")
	if err != nil {
		return err
	}
	if out != "" {
		return os.WriteFile(out, b, 0644)
	}
	fmt.Fprintln(env.Stdout, string(b))
	return nil
}

func (c *universalCmd) emitReport(env *cli.Env, result *agent.UniversalResult, flags cli.Flags) error {
	format := flags.Get("format")
	if format == "" {
		format = "markdown"
	}

	title := flags.Get("title")
	if title == "" {
		title = fmt.Sprintf("Universal Analysis: %s %s", result.ScriptInfo.Name, result.MarketData.Timeframe)
	}

	report := agent.GenerateUniversalReport(result, agent.ReportConfig{
		Title:         title,
		Symbol:        result.MarketData.Symbol,
		Timeframe:     result.MarketData.Timeframe,
		Format:        format,
		IncludeCharts: false,
	})

	out := flags.Get("out")
	if out != "" {
		return os.WriteFile(out, []byte(report), 0644)
	}
	fmt.Fprintln(env.Stdout, report)
	return nil
}

func (c *universalCmd) printHelp(env *cli.Env) {
	w := env.Stdout
	fmt.Fprintln(w, "analyze — Universal Pine Script Analyzer")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Usage: tv analyze <pineId> [options]")
	fmt.Fprintln(w, "       tv analyze --pine <pineId> [options]")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Automatically analyzes ANY Pine Script indicator/strategy without")
	fmt.Fprintln(w, "requiring a custom parser. Extracts signals, levels, and graphics")
	fmt.Fprintln(w, "semantically (order blocks, FVGs, volume profile, liquidity, etc.)")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Required:")
	fmt.Fprintln(w, "  <pineId>              Pine script ID (e.g., PUB;abc123)")
	fmt.Fprintln(w, "  --pine <pineId>       Alternative way to specify Pine ID")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Options:")
	fmt.Fprintln(w, "  --symbol EXCHANGE:SYMBOL     Market symbol (default: OANDA:XAUUSD)")
	fmt.Fprintln(w, "  --tf 5m                      Timeframe (default: 5m)")
	fmt.Fprintln(w, "  --bars 500                   Number of bars")
	fmt.Fprintln(w, "  --input.key=VALUE            Input overrides (e.g., --input.lookback=50)")
	fmt.Fprintln(w, "  --list-inputs                List available inputs from schema and exit")
	fmt.Fprintln(w, "  --validate-inputs            Validate inputs against schema before running")
	fmt.Fprintln(w, "  --settle 1500                Settle time in ms (default: 1500)")
	fmt.Fprintln(w, "  --timeout 120                Timeout in seconds")
	fmt.Fprintln(w, "  --force-schema               Re-fetch schema from TradingView")
	fmt.Fprintln(w, "  --json                       Output full JSON")
	fmt.Fprintln(w, "  --report                     Generate analysis report")
	fmt.Fprintln(w, "  --format markdown|html|marketing|text  Report format (default: markdown)")
	fmt.Fprintln(w, "  --title TITLE                Report title")
	fmt.Fprintln(w, "  --out FILE                   Save output to file")
	fmt.Fprintln(w, "  --verbose                    Verbose output")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Examples:")
	fmt.Fprintln(w, "  tv analyze PUB;aea729456b7a44e09661b70ce9e4e987 --symbol OANDA:XAUUSD --tf 1h")
	fmt.Fprintln(w, "  tv analyze --pine PUB;fVSb3j0I87LvTzPKrQTY5hDUEdsGdnm6 --report --format markdown")
	fmt.Fprintln(w, "  tv analyze PUB;ff639e15f24646fbaf19ae22ac663140 --json --out fvg_analysis.json")
	fmt.Fprintln(w, "  tv analyze PUB;09ebff5ba23c452b89ea82522f2aab35 --report --format marketing")
	fmt.Fprintln(w, "  tv analyze PUB;aea729456b7a44e09661b70ce9e4e987 --list-inputs")
	fmt.Fprintln(w, "  tv analyze PUB;aea729456b7a44e09661b70ce9e4e987 --validate-inputs --input.lookback=20")
}
