package cmd

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/ch99q/tvcli/internal/cli"
	"github.com/ch99q/tvcli/internal/config"
	"github.com/ch99q/tvcli/internal/service"
	"github.com/ch99q/tvcli/pkg/pinefacade"
	"github.com/ch99q/tvcli/pkg/runner"
	"github.com/ch99q/tvcli/internal/skill"
)

type skillCmd struct {
	app   *App
	skill *skill.Skill
}

func (c *skillCmd) Name() string      { return c.skill.Name }
func (c *skillCmd) Aliases() []string { return nil }
func (c *skillCmd) Synopsis() string  { return c.skill.Synopsis }

func (c *skillCmd) Run(env *cli.Env) error {
	flags := env.Flags
	if flags.Has("help") || flags.Has("h") {
		c.printHelp(env)
		return nil
	}

	cfg := c.app.Config
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
	inputs := c.resolveInputs(flags)

	start := time.Now()
	res, err := service.RunScript(nil, cfg, service.RunRequest{
		PineID:       c.skill.PineID,
		Symbol:       symbol,
		Timeframe:    tf,
		Bars:         bars,
		Inputs:       inputs,
		ReservedKeys: reservedSkillKeys,
		SettleMs:     flags.GetInt("settle", 1500),
		ForceCleanup: flags.Has("force-cleanup"),
		CalcTimeout:  time.Duration(config.GetTierLimits().CalcTimeoutSecs) * time.Second,
		Debug:        cfg.Debug,
	})
	if err != nil {
		return fmt.Errorf("%s: %w", c.skill.Name, err)
	}
	duration := time.Since(start)

	// --raw / --raw-out: dump the raw periods + graphic before parsing, so the
	// parser logic can be debugged against the actual TradingView response.
	// Mirrors `tv run --raw`. The dump goes to --raw-out (or <out>.raw.json);
	// if neither is set, it goes to stdout and parsing is skipped.
	if rawOut := flags.Get("raw-out"); flags.Has("raw") || rawOut != "" {
		rawPayload := map[string]any{
			"pineId":      c.skill.PineID,
			"workflow":    c.skill.Name,
			"symbol":      symbol,
			"timeframe":   tf,
			"bars":        bars,
			"inputs":      inputs,
			"periodCount": len(res.Periods),
			"periods":     res.Periods,
			"graphic":     res.Graphic,
		}
		rawJSON, _ := json.MarshalIndent(rawPayload, "", "  ")
		dest := ""
		switch {
		case rawOut != "" && rawOut != "true":
			dest = rawOut
		case flags.Get("out") != "":
			dest = flags.Get("out") + ".raw.json"
		}
		if dest != "" {
			os.WriteFile(dest, rawJSON, 0644)
			fmt.Fprintf(env.Stderr, "✓ Raw dump: %s\n", dest)
			// fall through to normal parsing unless --json is also set
			if !flags.Has("json") {
				return nil
			}
		} else {
			fmt.Fprintln(env.Stdout, string(rawJSON))
			return nil
		}
	}

	// --signals: bypass the per-skill parser and use the generic schema-guided
	// signal extractor. This is the script-agnostic path: it works for any
	// Pine script where metaInfo is available, including the broken field-name
	// parsers whose hand-coded aliases no longer match the actual TV output.
	if flags.Has("signals") {
		signals := runner.ExtractSignals(res.Periods, res.Graphic, res.StrategyReport, tf, c.skill.PineID, symbol, res.Indicator.Schema)
		var output any = signals
		if flags.Has("agent") {
			output = signalsToAgent(signals, c.skill.Name, symbol, tf, duration.Milliseconds())
		}
		if flags.Has("json") || flags.Has("agent") {
			b, _ := json.MarshalIndent(output, "", "  ")
			if outFile := flags.Get("out"); outFile != "" {
				os.WriteFile(outFile, b, 0644)
				fmt.Fprintf(env.Stderr, "Saved: %s\n", outFile)
			} else {
				fmt.Fprintln(env.Stdout, string(b))
			}
		} else {
			text := signals.Compact()
			if outFile := flags.Get("out"); outFile != "" {
				os.WriteFile(outFile, []byte(text), 0644)
				fmt.Fprintf(env.Stderr, "Saved: %s\n", outFile)
			} else {
				fmt.Fprintln(env.Stdout, text)
			}
		}
		return nil
	}

	result := c.skill.ParseOutput(res.Periods, res.Graphic, tf, symbol, flags.All())
	result.Status = "ok"
	if result.Workflow == "" {
		result.Workflow = c.skill.Name
	}

	// Some Pine scripts do not emit a Close plot, so the parser cannot report
	// a price. Fetch the latest underlying close and back-fill it when missing.
	if lastPriceMissing(result.Market.LastPrice) {
		if bars, err := service.FetchOHLCVBars(cfg, symbol, tf, 2); err == nil && len(bars) > 0 {
			result.Market.LastPrice = roundPrice(bars[len(bars)-1].Close)
		}
	}

	if flags.Has("json") {
		var output any
		if flags.Has("agent") {
			output = c.skill.ToAgent(result, symbol, tf, duration.Milliseconds())
		} else {
			output = result
		}
		b, _ := json.MarshalIndent(output, "", "  ")
		if outFile := flags.Get("out"); outFile != "" {
			os.WriteFile(outFile, b, 0644)
			fmt.Fprintf(env.Stderr, "Saved: %s\n", outFile)
		} else {
			fmt.Fprintln(env.Stdout, string(b))
		}
	} else {
		var text string
		if c.skill.FormatText != nil {
			text = c.skill.FormatText(result)
		} else {
			text = defaultTextFormat(result, c.skill)
		}
		if outFile := flags.Get("out"); outFile != "" {
			os.WriteFile(outFile, []byte(text), 0644)
			fmt.Fprintf(env.Stderr, "Saved: %s\n", outFile)
		} else {
			fmt.Fprintln(env.Stdout, text)
		}
	}
	return nil
}

func (c *skillCmd) resolveInputs(flags cli.Flags) map[string]string {
	// Pine indicators are configured via their TV input IDs (in_0, in_1, ...).
	// We build a Name→TVInputID and FlagName→TVInputID lookup so that defaults,
	// presets, --<flag> overrides, and --input key=value all translate to the
	// canonical TVInputID before being sent to Pine. Falling back to the raw
	// key lets users still pass `--input in_3=42` directly.
	nameToTV := make(map[string]string, len(c.skill.Inputs))
	flagToTV := make(map[string]string, len(c.skill.Inputs))
	for _, inp := range c.skill.Inputs {
		nameToTV[inp.Name] = inp.TVInputID
		flagToTV[inp.FlagName()] = inp.TVInputID
	}

	inputs := make(map[string]string)
	// 1) Apply defaults, keyed by TVInputID.
	for _, inp := range c.skill.Inputs {
		if inp.Default != nil {
			inputs[inp.TVInputID] = fmt.Sprintf("%v", inp.Default)
		}
	}
	// 2) Apply preset overrides. Presets are keyed by JS variable name in the
	// skill definition; translate to TVInputID.
	if presetName := flags.Get("preset"); presetName != "" {
		if preset, ok := c.skill.Presets[presetName]; ok {
			for k, v := range preset {
				tvID, ok := nameToTV[k]
				if !ok {
					tvID = k // preset key is already a TVInputID or a passthrough
				}
				inputs[tvID] = fmt.Sprintf("%v", v)
			}
		} else {
			available := make([]string, 0, len(c.skill.Presets))
			for k := range c.skill.Presets {
				available = append(available, k)
			}
			fmt.Fprintf(os.Stderr, "Unknown preset '%s'. Available: %s\n", presetName, strings.Join(available, ", "))
		}
	}
	// 3) Apply --<flag> overrides (kebab-case input names → TVInputID).
	for _, inp := range c.skill.Inputs {
		flagName := inp.FlagName()
		if flags.Has(flagName) {
			inputs[inp.TVInputID] = flags.Get(flagName)
		}
	}
	// 4) Apply --input key=value passthrough. Translate known JS variable
	// names to TVInputID; pass through unknown keys (lets users target raw
	// Pine IDs like `in_3` directly).
	for k, v := range flags.All() {
		if skill.ReservedFlags[k] {
			continue
		}
		if _, isDef := inputs[k]; isDef {
			continue
		}
		if tvID, ok := nameToTV[k]; ok {
			inputs[tvID] = v
			continue
		}
		if tvID, ok := flagToTV[k]; ok {
			inputs[tvID] = v
			continue
		}
		inputs[k] = v
	}
	return inputs
}

func (c *skillCmd) printHelp(env *cli.Env) {
	w := env.Stdout
	fmt.Fprintf(w, "%s — Pine Script Analysis\n\n", c.skill.Synopsis)
	fmt.Fprintf(w, "Usage: tv %s [options]\n\n", c.skill.Name)
	if len(c.skill.Inputs) > 0 {
		fmt.Fprintln(w, "Indicator Options:")
		for _, inp := range c.skill.Inputs {
			def := ""
			if inp.Default != nil {
				def = fmt.Sprintf(" (default: %v)", inp.Default)
			}
			fmt.Fprintf(w, "  --%-28s [%s]%s\n", inp.FlagName(), inp.Type, def)
		}
		fmt.Fprintln(w)
	}
	if len(c.skill.Presets) > 0 {
		presets := make([]string, 0, len(c.skill.Presets))
		for k := range c.skill.Presets {
			presets = append(presets, k)
		}
		fmt.Fprintf(w, "Presets: %s\n\n", strings.Join(presets, ", "))
	}
	fmt.Fprintln(w, "Common Options:")
	fmt.Fprintln(w, "  --symbol EXCHANGE:SYMBOL     Market symbol (default: OANDA:XAUUSD)")
	fmt.Fprintln(w, "  --tf 5m                      Timeframe (default: 5m)")
	fmt.Fprintln(w, "  --bars 500                   Number of bars")
	fmt.Fprintln(w, "  --json                       JSON output")
	fmt.Fprintln(w, "  --agent                      Agent-ready JSON (implies --json)")
	fmt.Fprintln(w, "  --signals                    Use generic schema-guided signal extractor")
	fmt.Fprintln(w, "  --raw                        Dump raw periods + graphic (skip parsing)")
	fmt.Fprintln(w, "  --raw-out FILE               Write raw dump to file (implies --raw)")
	fmt.Fprintln(w, "  --preset NAME                Load preset")
	fmt.Fprintln(w, "  --out FILE                   Save output to file")
	fmt.Fprintln(w, "  --verbose                    Verbose output")
}

func defaultTextFormat(result skill.SkillResult, s *skill.Skill) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString(fmt.Sprintf("  %s\n", strings.ToUpper(s.Name)))
	sb.WriteString("======================================================================\n\n")
	if result.Market.Bias != "" {
		sb.WriteString(fmt.Sprintf("  Bias: %s\n", result.Market.Bias))
	}
	if result.Market.LastPrice != nil {
		sb.WriteString(fmt.Sprintf("  Last Price: %v\n", result.Market.LastPrice))
	}
	if len(result.Opportunities) > 0 {
		sb.WriteString("\nOPPORTUNITIES\n")
		for _, opp := range result.Opportunities {
			score := strconv.FormatFloat(opp.ConfluenceScore, 'f', 2, 64)
			sb.WriteString(fmt.Sprintf("  #%d %s %s [%s] score=%s\n", opp.Rank, opp.Direction, opp.Setup, opp.Confidence, score))
			if opp.Rationale != "" {
				sb.WriteString(fmt.Sprintf("      %s\n", opp.Rationale))
			}
		}
	}
	if len(result.Narrative.Warnings) > 0 {
		sb.WriteString("\nWARNINGS\n")
		for _, w := range result.Narrative.Warnings {
			sb.WriteString(fmt.Sprintf("  - %s\n", w))
		}
	}
	sb.WriteString(fmt.Sprintf("\n  Agentic Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

// lastPriceMissing returns true when a skill parser did not provide a price.
func lastPriceMissing(v any) bool {
	if v == nil {
		return true
	}
	switch n := v.(type) {
	case float64:
		return n == 0
	case int:
		return n == 0
	default:
		return false
	}
}

func roundPrice(f float64) float64 {
	return math.Round(f*100) / 100
}

var reservedSkillKeys = []string{
	"symbol", "tf", "timeframe", "bars", "json", "agent", "out",
	"raw", "raw-out", "signals", "settle", "force-cleanup", "persistent",
	"loop", "verbose", "preset", "help", "h", "v",
}
