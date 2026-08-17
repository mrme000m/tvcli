package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
	"github.com/ch99q/tvcli/pkg/schema"
)

var GoldenSkill = &skill.Skill{
	Name:     "golden",
	Synopsis: "Golden Rule Strategy — multi-TF weekly/daily/4H alignment",
	PineID:   "PUB;6daafb2cabe6419d98ae25229d2327f8",
	// Runs SMC script; parser extracts BOS/CHoCH/FVG/OB to compute a
	// Golden Rule bias (first signal in the 4-signal checklist).
	Inputs: []skill.InputDef{
		{Name: "showStructureInput", TVInputID: "in_10", Type: "bool", Default: true},
		{Name: "showFairValueGapsInput", TVInputID: "in_33", Type: "bool", Default: true},
		{Name: "showInternalOrderBlocksInput", TVInputID: "in_19", Type: "bool", Default: true},
		{Name: "swingsLengthInput", TVInputID: "in_17", Type: "int", Default: 50},
	},
	ParseOutput:     parseGolden,
	ParseWithSchema: parseGoldenSchema,
	FormatText:      formatGolden,
}

func parseGolden(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	return parseGoldenSchema(periods, graphic, nil, tf, symbol, args)
}

func parseGoldenSchema(periods []map[string]any, graphic map[string]map[string]any, sch *schema.PineSchema, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "golden-rule-strategy",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)

	// The Golden Rule Strategy runs SMC (Smart Money Concepts) as its Pine
	// script. The original JS implementation computes a multi-timeframe
	// verdict locally; the Go CLI runs a single timeframe. We extract the
	// SMC structure data and compute a bias from the BOS/CHoCH/FVG counts,
	// which is the first signal in the Golden Rule 4-signal checklist.
	bullBOS := toFloat(getField(last, []string{"Bullish_BOS", "Internal_Bullish_BOS"}))
	bearBOS := toFloat(getField(last, []string{"Bearish_BOS", "Internal_Bearish_BOS"}))
	bullCHoCH := toFloat(getField(last, []string{"Bullish_CHoCH", "Internal_Bullish_CHoCH"}))
	bearCHoCH := toFloat(getField(last, []string{"Bearish_CHoCH", "Internal_Bearish_CHoCH"}))
	bullFVG := toFloat(getField(last, []string{"Bullish_FVG"}))
	bearFVG := toFloat(getField(last, []string{"Bearish_FVG"}))
	bullOB := toFloat(getField(last, []string{"Bullish_Internal_OB_Breakout", "Bullish_Swing_OB_Breakout"}))
	bearOB := toFloat(getField(last, []string{"Bearish_Internal_OB_Breakout", "Bearish_Swing_OB_Breakout"}))

	// Count totals across all bars for a broader view.
	totalBullBOS, totalBearBOS, totalBullCHoCH, totalBearCHoCH := 0, 0, 0, 0
	totalBullFVG, totalBearFVG := 0, 0
	for _, p := range historicalBars(periods) {
		if toFloat(getField(p, []string{"Bullish_BOS", "Internal_Bullish_BOS"})) > 0 { totalBullBOS++ }
		if toFloat(getField(p, []string{"Bearish_BOS", "Internal_Bearish_BOS"})) > 0 { totalBearBOS++ }
		if toFloat(getField(p, []string{"Bullish_CHoCH", "Internal_Bullish_CHoCH"})) > 0 { totalBullCHoCH++ }
		if toFloat(getField(p, []string{"Bearish_CHoCH", "Internal_Bearish_CHoCH"})) > 0 { totalBearCHoCH++ }
		if toFloat(getField(p, []string{"Bullish_FVG"})) > 0 { totalBullFVG++ }
		if toFloat(getField(p, []string{"Bearish_FVG"})) > 0 { totalBearFVG++ }
	}

	bullScore := bullBOS + bullCHoCH + bullFVG + bullOB
	bearScore := bearBOS + bearCHoCH + bearFVG + bearOB

	// Bias from historical totals (not just last bar, since SMC events
	// fire sporadically — most bars have 0 for all signal fields).
	totalBull := totalBullBOS + totalBullCHoCH + totalBullFVG
	totalBear := totalBearBOS + totalBearCHoCH + totalBearFVG

	bias := "neutral"
	verdict := "NEUTRAL"
	if totalBull > totalBear {
		bias = "bullish"
		verdict = "PASS"
	} else if totalBear > totalBull {
		bias = "bearish"
		verdict = "FAIL"
	}

	// Agentic score reflects data richness and signal clarity.
	agenticScore := 0.3
	if len(periods) > 50 { agenticScore += 0.2 }
	if totalBull != totalBear { agenticScore += 0.2 }
	if totalBullBOS+totalBearBOS > 5 { agenticScore += 0.15 }
	if totalBullFVG+totalBearFVG > 5 { agenticScore += 0.15 }
	agenticScore = math.Min(agenticScore, 0.99)

	return skill.SkillResult{
		Status: "ok", Workflow: "golden-rule-strategy",
		Market: skill.MarketData{LastPrice: nil, Bias: bias},
		Structure: map[string]any{
			"verdict":       verdict,
			"bias":          bias,
			"bullScore":     bullScore,
			"bearScore":     bearScore,
			"bullBOS":       totalBullBOS,
			"bearBOS":       totalBearBOS,
			"bullCHoCH":     totalBullCHoCH,
			"bearCHoCH":     totalBearCHoCH,
			"bullFVG":       totalBullFVG,
			"bearFVG":       totalBearFVG,
			"lastBullFVG":   bullFVG,
			"lastBearFVG":   bearFVG,
		},
		Opportunities: []skill.Opportunity{},
		Narrative: skill.Narrative{
			MarketStructure: fmt.Sprintf("Verdict: %s | Bull: %.0f vs Bear: %.0f | BOS: %d/%d CHoCH: %d/%d FVG: %d/%d",
				verdict, bullScore, bearScore, totalBullBOS, totalBearBOS, totalBullCHoCH, totalBearCHoCH, totalBullFVG, totalBearFVG),
		},
		Validation:  skill.Validation{Passed: true},
		Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatGolden(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  GOLDEN RULE STRATEGY\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Verdict: %v\n", result.Structure["verdict"]))
	sb.WriteString(fmt.Sprintf("  Bias: %s | Price: %v\n", result.Market.Bias, result.Market.LastPrice))
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(GoldenSkill) }
