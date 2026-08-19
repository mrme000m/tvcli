package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
	"github.com/mrme000m/tvcli/pkg/schema"
)

var SwingArmSkill = &skill.Skill{
	Name:     "swingarm",
	Synopsis: "SwingArm ATR Trend — trailing stop with Fibonacci levels",
	PineID:   "PUB;GdkmXaTINI8knwuCrctQD1pB5dFaRnyr",
	Inputs: []skill.InputDef{
		{Name: "trailType", TVInputID: "in_0", Type: "string", Default: "modified"},
		{Name: "ATRPeriod", TVInputID: "in_1", Type: "int", Default: 28},
		{Name: "ATRFactor", TVInputID: "in_2", Type: "int", Default: 5},
		{Name: "show_fib_entries", TVInputID: "in_3", Type: "bool", Default: true},
	},
	ParseOutput:     parseSwingArm,
	ParseWithSchema: parseSwingArmSchema,
	FormatText:      formatSwingArm,
}

func parseSwingArm(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	return parseSwingArmSchema(periods, graphic, nil, tf, symbol, args)
}

func parseSwingArmSchema(periods []map[string]any, graphic map[string]map[string]any, sch *schema.PineSchema, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "swingarm-atr-trend",
			Narrative: skill.Narrative{MarketStructure: "No data", Warnings: []string{"No period data"}}}
	}
	last := latestClosed(periods)
	trailingStop := SchemaFloat(last, sch, "Trailingstop", "plot_0")
	extremum := SchemaFloat(last, sch, "Extremum", "plot_2")
	fib1 := SchemaFloat(last, sch, "Fib_1", "plot_4")
	fib2 := SchemaFloat(last, sch, "Fib_2", "plot_5")
	fib3 := SchemaFloat(last, sch, "Fib_3", "plot_6")
	signal := SchemaFloat(last, sch, "plot_8")
	bgColor := SchemaFloat(last, sch, "plot_9")

	// bgColor: 4 = bullish, 5 = bearish
	bias := "neutral"
	if bgColor == 4 { bias = "bullish" }
	if bgColor == 5 { bias = "bearish" }

	buySignal := signal == 1
	sellSignal := signal == -1

	agenticScore := 0.2
	if len(periods) > 0 { agenticScore += 0.2 }
	if trailingStop > 0 { agenticScore += 0.15 }
	if extremum > 0 { agenticScore += 0.1 }
	if buySignal || sellSignal { agenticScore += 0.2 }
	agenticScore = math.Min(agenticScore, 0.99)

	opps := []skill.Opportunity{}
	if buySignal || sellSignal {
		dir := "long"
		if sellSignal { dir = "short" }
		score := 0.7
		opps = append(opps, skill.Opportunity{
			Rank: 1, Setup: "swingarm_signal", Direction: dir,
			Confidence: confidenceLabel(score), ConfluenceScore: round2(score),
			Rationale: fmt.Sprintf("SwingArm %s signal. Stop=%.0f Extremum=%.0f Fib1=%.0f", dir, trailingStop, extremum, fib1),
		})
	}

	return skill.SkillResult{
		Status: "ok", Workflow: "swingarm-atr-trend",
		Market: skill.MarketData{LastPrice: trailingStop, Bias: bias},
		Structure: map[string]any{"trailingStop": round2(trailingStop), "extremum": round2(extremum), "fib1": round2(fib1), "fib2": round2(fib2), "fib3": round2(fib3), "signal": signal, "bias": bias},
		Opportunities: opps,
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("Stop: %.0f | Extremum: %.0f | Bias: %s", trailingStop, extremum, bias), PrimaryOpp: primaryOppFromOpps(opps), Warnings: []string{}},
		Validation: skill.Validation{Passed: true, Warnings: []string{}}, Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatSwingArm(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  SWINGARM ATR TREND\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Trailing Stop: %v | Extremum: %v\n", result.Structure["trailingStop"], result.Structure["extremum"]))
	sb.WriteString(fmt.Sprintf("  Fib1: %v | Fib2: %v | Fib3: %v\n", result.Structure["fib1"], result.Structure["fib2"], result.Structure["fib3"]))
	sb.WriteString(fmt.Sprintf("  Signal: %v | Bias: %s\n", result.Structure["signal"], result.Market.Bias))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("  -> %s %s [%s] %.2f: %s\n", o.Direction, o.Setup, o.Confidence, o.ConfluenceScore, o.Rationale))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(SwingArmSkill) }
