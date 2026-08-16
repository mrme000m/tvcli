package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/ch99q/tvcli/internal/agent"
	"github.com/ch99q/tvcli/internal/cli"
	"github.com/ch99q/tvcli/internal/skill"
	"github.com/ch99q/tvcli/pkg/pinefacade"
)

type agentCmd struct {
	app *App
}

func (c *agentCmd) Name() string      { return "agent" }
func (c *agentCmd) Aliases() []string { return []string{"agents", "analyze"} }
func (c *agentCmd) Synopsis() string  { return "Run multi-skill market analysis agent" }

func (c *agentCmd) Run(env *cli.Env) error {
	flags := env.Flags

	if flags.Has("help") || flags.Has("h") {
		c.printHelp(env)
		return nil
	}

	cfg := c.app.Config

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

	bars := flags.GetInt("bars", 500)
	parallel := !flags.Has("sequential")
	timeout := flags.GetInt("timeout", 120)
	debug := flags.Has("verbose") || cfg.Debug

	// Parse skills list
	skillsFlag := flags.Get("skills")
	var skillsToRun []string
	if skillsFlag != "" {
		for _, s := range strings.Split(skillsFlag, ",") {
			s = strings.TrimSpace(s)
			if s != "" {
				skillsToRun = append(skillsToRun, s)
			}
		}
	}

	// Parse presets
	presets := make(map[string]string)
	presetFlag := flags.Get("preset")
	if presetFlag != "" {
		// Global preset for all skills
		for _, s := range skill.All() {
			if _, ok := s.Presets[presetFlag]; ok {
				presets[s.Name] = presetFlag
			}
		}
	}
	// Skill-specific presets: --preset.skillname=value
	for k, v := range flags.All() {
		if strings.HasPrefix(k, "preset.") {
			skillName := strings.TrimPrefix(k, "preset.")
			if s := skill.Get(skillName); s != nil {
				if _, ok := s.Presets[v]; ok {
					presets[skillName] = v
				} else {
					fmt.Fprintf(env.Stderr, "⚠ Skill %q has no preset %q\n", skillName, v)
				}
			}
		}
	}

	// Parse global inputs
	inputs := make(map[string]string)
	for k, v := range flags.All() {
		if strings.HasPrefix(k, "input.") {
			inputs[strings.TrimPrefix(k, "input.")] = v
		}
	}

	// Build agent config
	agentConfig := agent.AgentConfig{
		Symbol:    symbol,
		Timeframe: tf,
		Bars:      bars,
		Skills:    skillsToRun,
		Presets:   presets,
		Inputs:    inputs,
		Parallel:  parallel,
		Timeout:   time.Duration(timeout) * time.Second,
		Debug:     debug,
	}

	// Create and run agent
	agt := agent.NewAgent(cfg, agentConfig)

	ctx := context.Background()
	result, err := agt.Run(ctx)
	if err != nil {
		return fmt.Errorf("agent run failed: %w", err)
	}

	// Output
	if flags.Has("json") {
		return c.emitJSON(env, result)
	}

	if flags.Has("report") {
		return c.emitReport(env, result, flags)
	}

	// Default: text output
	text := agent.FormatText(result)
	fmt.Fprintln(env.Stdout, text)
	return nil
}

func (c *agentCmd) emitJSON(env *cli.Env, result *agent.AgentResult) error {
	out := env.Flags.Get("out")
	data := result.ToJSON()
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

func (c *agentCmd) emitReport(env *cli.Env, result *agent.AgentResult, flags cli.Flags) error {
	format := flags.Get("format")
	if format == "" {
		format = "markdown"
	}

	title := flags.Get("title")
	if title == "" {
		title = fmt.Sprintf("Market Analysis: %s %s", result.Config.Symbol, result.Config.Timeframe)
	}

	reportCfg := agent.ReportConfig{
		Title:       title,
		Symbol:      result.Config.Symbol,
		Timeframe:   result.Config.Timeframe,
		Format:      format,
		IncludeCharts: false,
	}

	report := agent.GenerateReport(result, reportCfg)

	out := flags.Get("out")
	if out != "" {
		return os.WriteFile(out, []byte(report), 0644)
	}
	fmt.Fprintln(env.Stdout, report)
	return nil
}

func (c *agentCmd) printHelp(env *cli.Env) {
	w := env.Stdout
	fmt.Fprintln(w, "agent — Multi-Skill Market Analysis Agent")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Usage: tv agent [options]")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Options:")
	fmt.Fprintln(w, "  --symbol EXCHANGE:SYMBOL     Market symbol (default: OANDA:XAUUSD)")
	fmt.Fprintln(w, "  --tf 5m                      Timeframe (default: 5m)")
	fmt.Fprintln(w, "  --bars 500                   Number of bars")
	fmt.Fprintln(w, "  --skills skill1,skill2       Comma-separated skill names (default: all)")
	fmt.Fprintln(w, "  --preset NAME                Global preset for all skills that have it")
	fmt.Fprintln(w, "  --preset.skill=NAME          Skill-specific preset (e.g. --preset.cust=scalping)")
	fmt.Fprintln(w, "  --input.key=VALUE            Global input override (e.g. --input.atrLen=14)")
	fmt.Fprintln(w, "  --sequential                 Run skills sequentially (default: parallel)")
	fmt.Fprintln(w, "  --timeout SECONDS            Per-skill timeout (default: 120)")
	fmt.Fprintln(w, "  --json                       Output full JSON")
	fmt.Fprintln(w, "  --report                     Generate analysis report")
	fmt.Fprintln(w, "  --format markdown|html|text|marketing  Report format (default: markdown)")
	fmt.Fprintln(w, "  --title TITLE                Report title")
	fmt.Fprintln(w, "  --out FILE                   Save output to file")
	fmt.Fprintln(w, "  --verbose                    Verbose output")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Available Skills:")
	skills := skill.All()
	for _, s := range skills {
		cat := s.EffectiveCategory()
		fmt.Fprintf(w, "  %-12s [%s] %s\n", s.Name, cat, s.Synopsis)
	}
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Examples:")
	fmt.Fprintln(w, "  tv agent --symbol BINANCE:BTCUSDT --tf 15m --json")
	fmt.Fprintln(w, "  tv agent --skills bsv,dvi,ema-atr --report --format markdown")
	fmt.Fprintln(w, "  tv agent --skills bsv,dvi --report --format marketing --out thread.txt")
	fmt.Fprintln(w, "  tv agent --sequential --timeout 180 --out analysis.json")
}