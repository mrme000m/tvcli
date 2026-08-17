package cmd

import (
	"context"
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/ch99q/tvcli/internal/cli"
	"github.com/ch99q/tvcli/internal/config"
	"github.com/ch99q/tvcli/internal/service"
	"github.com/ch99q/tvcli/internal/skill"
	"github.com/ch99q/tvcli/pkg/pinefacade"
	"github.com/ch99q/tvcli/pkg/runner"
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

	// Public vs private gating. Public scripts (PUB;) run on any TradingView
	// tier, including free. Only invite-only / private namespaces need an
	// invitation, so we dynamically detect them (via the Pine ID prefix and,
	// when reachable, the public script library search) and negate them
	// before attempting a run. PUB scripts are never blocked on this check.
	if !pinefacade.IsPublicPineID(c.skill.PineID) {
		facade := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond)
		sa, saErr := facade.GetScriptAccess(c.skill.PineID, cfg.CookieHeaderOrEmpty())
		if flags.Has("verify-access") {
			fmt.Fprintf(env.Stdout, "script %q (%s): access=%s type=%s source=%s\n",
				c.skill.Name, c.skill.PineID, sa.Access, sa.Type, sa.Source)
			return nil
		}
		if saErr == nil && sa.Access != "public" && sa.Access != "unknown" {
			if flags.Has("allow-private") {
				fmt.Fprintf(env.Stderr, "⚠ %s is a %s script; running anyway (--allow-private)\n", c.skill.Name, sa.Access)
			} else {
				return fmt.Errorf("skill %q uses a %s script (%s); it requires an invitation and is skipped. Use --allow-private to override",
					c.skill.Name, sa.Access, c.skill.PineID)
			}
		}
	} else if flags.Has("verify-access") {
		fmt.Fprintf(env.Stdout, "script %q (%s): access=public type=%s source=prefix\n",
			c.skill.Name, c.skill.PineID, pinefacade.AccessFromPineID(c.skill.PineID))
		return nil
	}

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
	inputs := c.resolveInputs(flags)

	if flags.Has("schema") {
		indicator, err := service.LoadIndicator(cfg, c.skill.PineID, inputs, reservedSkillKeys)
		if err != nil {
			return fmt.Errorf("%s: %w", c.skill.Name, err)
		}
		if indicator.Schema != nil {
			fmt.Fprintln(env.Stdout, indicator.Schema.Summary())
			if flags.Has("json") {
				emitJSON(env, indicator.Schema, flags.Get("out"))
			}
		} else {
			fmt.Fprintf(env.Stderr, "No schema available for %s (metaInfo had no plots/styles)\n", c.skill.PineID)
		}
		return nil
	}

	// Pre-check auth before running (fail fast on expired cookies).
	if err := PreCheckAuth(cfg); err != nil {
		return err
	}

	start := time.Now()
	res, err := service.RunScript(context.Background(), cfg, service.RunRequest{
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
	// Mirrors `tv run --raw`.
	if execRaw(env, map[string]any{
		"pineId":      c.skill.PineID,
		"workflow":    c.skill.Name,
		"symbol":      symbol,
		"timeframe":   tf,
		"bars":        bars,
		"inputs":      inputs,
		"periodCount": len(res.Periods),
		"periods":     res.Periods,
		"graphic":     res.Graphic,
	}, flags) {
		return nil
	}

	// Parse the raw data with the skill's parser. Prefer the schema-aware
	// variant when present so plot names are resolved from metaInfo rather
	// than guessed plot_N indices; fall back to ParseOutput otherwise.
	var result skill.SkillResult
	if c.skill.ParseWithSchema != nil {
		result = c.skill.ParseWithSchema(res.Periods, res.Graphic, res.Indicator.Schema, tf, symbol, flags.All())
	} else {
		result = c.skill.ParseOutput(res.Periods, res.Graphic, tf, symbol, flags.All())
	}
	if result.Status == "" {
		result.Status = "ok"
	}
	if result.Workflow == "" {
		result.Workflow = c.skill.Name
	}

	// Some Pine scripts do not emit a Close plot, so the parser cannot report
	// a price. Fetch the latest underlying close and back-fill it when missing,
	// but only when the parser actually produced data so we don't mask a
	// no_data result with a fake price.
	if result.Status == "ok" && lastPriceMissing(result.Market.LastPrice) {
		if bars, err := service.FetchOHLCVBars(cfg, symbol, tf, 2); err == nil && len(bars) > 0 {
			result.Market.LastPrice = roundPrice(bars[len(bars)-1].Close)
		}
	}

	// --signals: bypass the per-skill parser and use the generic schema-guided
	// signal extractor. This is the script-agnostic path: it works for any
	// Pine script where metaInfo is available. We also fall back to it when a
	// hand-coded parser yields no_data but a schema exists, so a renamed or
	// mismatched script still produces structured output instead of silently
	// reporting no_data. Graphics-only skills (RequiresGraphic) are exempt:
	// their per-skill parser already inspected the graphic layer and returned
	// no_data on purpose, so routing them to a period-based extractor would
	// only produce empty noise.
	useSignals := flags.Has("signals")
	if !useSignals && result.Status == "no_data" && !c.skill.RequiresGraphic &&
		res.Indicator != nil && res.Indicator.Schema != nil {
		useSignals = true
	}
	if useSignals {
		signals := runner.ExtractSignals(res.Periods, res.Graphic, res.StrategyReport, tf, c.skill.PineID, symbol, res.Indicator.Schema)
		if flags.Has("agent") {
			emitJSON(env, signalsToAgent(signals, c.skill.Name, symbol, tf, duration.Milliseconds()), flags.Get("out"))
		} else if flags.Has("json") {
			emitJSON(env, signals, flags.Get("out"))
		} else {
			emitText(env, signals.Compact(), flags.Get("out"))
		}
		return nil
	}

	if flags.Has("json") {
		var output any
		if flags.Has("agent") {
			output = c.skill.ToAgent(result, symbol, tf, duration.Milliseconds())
		} else {
			output = result
		}
		emitJSON(env, output, flags.Get("out"))
	} else {
		var text string
		if c.skill.FormatText != nil {
			text = c.skill.FormatText(result)
		} else {
			text = defaultTextFormat(result, c.skill)
		}
		emitText(env, text, flags.Get("out"))
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
	// 4) Apply explicit input overrides. collectInputs reassembles the Flag
	// parser's split representation ("--input k=v", "--input.k=v", positional
	// "k=v", raw "--in_3=42") into one map, then every key is translated to the
	// canonical TVInputID (by name or by kebab-case flag name) before being sent
	// to Pine. Unknown keys pass through so raw Pine IDs still work.
	reserved := make([]string, 0, len(skill.ReservedFlags))
	for k := range skill.ReservedFlags {
		reserved = append(reserved, k)
	}
	explicit := collectInputs(flags, 0, reserved)
	for k, v := range explicit {
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
	fmt.Fprintln(w, "  --bars 500                   Number of bars (auto-capped to tier limit: free=180)")
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
	"allow-private", "verify-access",
}
