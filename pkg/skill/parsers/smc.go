package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
)

var SMCSkill = &skill.Skill{
	Name:     "smc",
	Synopsis: "Smart Money Concepts — BOS/CHoCH, FVG, Order Blocks",
	PineID:   "PUB;6daafb2cabe6419d98ae25229d2327f8",
	Inputs: []skill.InputDef{
		{Name: "showStructureInput", TVInputID: "in_10", Type: "bool", Default: true},
		{Name: "showSwingBullInput", TVInputID: "in_11", Type: "string", Default: "ALL"},
		{Name: "showSwingBearInput", TVInputID: "in_13", Type: "string", Default: "ALL"},
		{Name: "showInternalOrderBlocksInput", TVInputID: "in_19", Type: "bool", Default: true},
		{Name: "showSwingOrderBlocksInput", TVInputID: "in_21", Type: "bool", Default: false},
		{Name: "showFairValueGapsInput", TVInputID: "in_33", Type: "bool", Default: true},
		{Name: "fairValueGapsThresholdInput", TVInputID: "in_34", Type: "bool", Default: true},
		{Name: "showEqualHighsLowsInput", TVInputID: "in_29", Type: "bool", Default: true},
		{Name: "swingsLengthInput", TVInputID: "in_17", Type: "int", Default: 50},
	},
	ParseOutput: parseSMC,
	FormatText:  formatSMC,
}

func parseSMC(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "smart-money-concepts",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	price := toFloat(getField(last, []string{"Close", "close", "plotcandle_0_ohlc_close", "plot_3"}))

	// This Pine script emits 0/1 event flags per bar, not pre-aggregated counts.
	// Count the bullish and bearish variants across closed bars.
	bars := historicalBars(periods)
	count := func(p map[string]any, names []string) float64 {
		total := 0.0
		for _, n := range names {
			if toFloat(getField(p, []string{n})) > 0 {
				total++
			}
		}
		return total
	}
	bullishBOS, bearishBOS := 0.0, 0.0
	bullishCHoCH, bearishCHoCH := 0.0, 0.0
	bullishFVG, bearishFVG := 0.0, 0.0
	bullishOB, bearishOB := 0.0, 0.0
	for _, p := range bars {
		bullishBOS += count(p, []string{"Bullish_BOS", "Internal_Bullish_BOS"})
		bearishBOS += count(p, []string{"Bearish_BOS", "Internal_Bearish_BOS"})
		bullishCHoCH += count(p, []string{"Bullish_CHoCH", "Internal_Bullish_CHoCH"})
		bearishCHoCH += count(p, []string{"Bearish_CHoCH", "Internal_Bearish_CHoCH"})
		bullishFVG += count(p, []string{"Bullish_FVG"})
		bearishFVG += count(p, []string{"Bearish_FVG"})
		bullishOB += count(p, []string{"Bullish_Internal_OB_Breakout", "Bullish_Swing_OB_Breakout", "Equal_Highs"})
		bearishOB += count(p, []string{"Bearish_Internal_OB_Breakout", "Bearish_Swing_OB_Breakout", "Equal_Lows"})
	}
	bosCount := bullishBOS + bearishBOS
	chochCount := bullishCHoCH + bearishCHoCH
	fvgCount := bullishFVG + bearishFVG
	obCount := bullishOB + bearishOB

	bias := "neutral"
	bullTotal := bullishBOS + bullishCHoCH + bullishFVG + bullishOB
	bearTotal := bearishBOS + bearishCHoCH + bearishFVG + bearishOB
	if bullTotal > bearTotal { bias = "bullish" }
	if bearTotal > bullTotal { bias = "bearish" }

	agenticScore := 0.2
	if len(bars) > 0 { agenticScore += 0.2 }
	if bosCount > 0 || chochCount > 0 { agenticScore += 0.15 }
	if fvgCount > 0 { agenticScore += 0.1 }
	if obCount > 0 { agenticScore += 0.1 }
	agenticScore = math.Min(agenticScore, 0.99)

	return skill.SkillResult{
		Status: "ok", Workflow: "smart-money-concepts",
		Market: skill.MarketData{LastPrice: price, Bias: bias},
		Structure: map[string]any{
			"bosCount": bosCount, "bullishBOS": bullishBOS, "bearishBOS": bearishBOS,
			"chochCount": chochCount, "bullishCHoCH": bullishCHoCH, "bearishCHoCH": bearishCHoCH,
			"fvgCount": fvgCount, "bullishFVG": bullishFVG, "bearishFVG": bearishFVG,
			"obCount": obCount, "bullishOB": bullishOB, "bearishOB": bearishOB,
		},
		Opportunities: []skill.Opportunity{},
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("BOS: %.0f | CHoCH: %.0f | FVG: %.0f | OB: %.0f | Bias: %s", bosCount, chochCount, fvgCount, obCount, bias)},
		Validation:    skill.Validation{Passed: true}, Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatSMC(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  SMART MONEY CONCEPTS\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  BOS: %v | CHoCH: %v\n", result.Structure["bosCount"], result.Structure["chochCount"]))
	sb.WriteString(fmt.Sprintf("  FVG: %v | OB: %v\n", result.Structure["fvgCount"], result.Structure["obCount"]))
	sb.WriteString(fmt.Sprintf("  Bias: %s | Price: %v\n", result.Market.Bias, result.Market.LastPrice))
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(SMCSkill) }
