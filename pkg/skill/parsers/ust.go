package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
)

var USTSkill = &skill.Skill{
	Name:     "ust",
	Synopsis: "Ultra Sensitive SuperTrend — dual ST alignment",
	PineID:   "PUB;fc33f2d98699414a8585923116dbd959",
	Inputs: []skill.InputDef{
		{Name: "atrPeriod1", TVInputID: "in_0", Type: "int", Default: 10},
		{Name: "multiplier1", TVInputID: "in_1", Type: "float", Default: 1.0},
		{Name: "atrPeriod2", TVInputID: "in_2", Type: "int", Default: 5},
		{Name: "multiplier2", TVInputID: "in_3", Type: "float", Default: 0.5},
		{Name: "useHeikenAshi", TVInputID: "in_4", Type: "bool", Default: true},
		{Name: "showLabels", TVInputID: "in_5", Type: "bool", Default: true},
		{Name: "showBG", TVInputID: "in_6", Type: "bool", Default: true},
	},
	ParseOutput: parseUST,
	FormatText:  formatUST,
}

func parseUST(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "ultra-sensitive-supertrend",
			Narrative: skill.Narrative{MarketStructure: "No data", Warnings: []string{"No period data"}}}
	}
	last := latestClosed(periods)
	// The script exposes line plots as plot_0/plot_2 and colorers as plot_1/plot_3.
	// Read the line plots directly; the "ST1"/"ST2" style keys map to the colorers.
	st1 := toFloat(getField(last, []string{"plot_0", "ST1"}))
	st2 := toFloat(getField(last, []string{"plot_2", "ST2"}))
	st1Color := toFloat(getField(last, []string{"plot_1", "ST1_colorer"}))
	st2Color := toFloat(getField(last, []string{"plot_3", "ST2_colorer"}))
	bgColor := toFloat(getField(last, []string{"Background_Color", "plot_4"}))

	// Pine colorer values: 2 = bearish (red), 3 = bullish-transition, 4 = bullish (green).
	// Treat 3 and 4 as bullish; 2 as bearish; 0 as unknown (infer from ST relationship).
	st2Bullish := st2Color == 3 || st2Color == 4
	if st2Color == 0 && st2 > 0 {
		// No colorer info — infer from ST2 position relative to ST1.
		st2Bullish = st2 >= st1
	}
	st2Bearish := st2Color == 2
	if st2Color == 0 && st2 > 0 {
		st2Bearish = st2 < st1
	}

	// ST1 colorer: same mapping. When colorer is 0, infer from ST relationship.
	st1Bullish := st1Color == 3 || st1Color == 4
	if st1Color == 0 && st1 > 0 && st2 > 0 {
		st1Bullish = st2 >= st1
	}
	st1Bearish := st1Color == 2
	if st1Color == 0 && st1 > 0 && st2 > 0 {
		st1Bearish = st2 < st1
	}

	// Background color: 4 = bullish, 5 = bearish (from Pine color indices).
	bgTrend := "NEUTRAL"
	if bgColor == 3 || bgColor == 4 { bgTrend = "BULLISH" }
	if bgColor == 5 || bgColor == 2 { bgTrend = "BEARISH" }

	// Combined trend: require both STs to agree.
	combinedTrend := "MIXED"
	if st1Bullish && st2Bullish { combinedTrend = "BULLISH" }
	if st1Bearish && st2Bearish { combinedTrend = "BEARISH" }

	// Alignment: both STs agree OR background confirms.
	aligned := (st1Bullish == st2Bullish) || (bgTrend != "NEUTRAL" &&
		((bgTrend == "BULLISH" && st1Bullish) || (bgTrend == "BEARISH" && st1Bearish)))

	buySignal := toFloat(getField(last, []string{"BUY"})) == 1
	sellSignal := toFloat(getField(last, []string{"SELL"})) == 1
	ultraBuy := toFloat(getField(last, []string{"ULTRA_BUY"})) == 1
	ultraSell := toFloat(getField(last, []string{"ULTRA_SELL"})) == 1

	buyCount, sellCount, ultraBuyCount, ultraSellCount := 0, 0, 0, 0
	for _, p := range historicalBars(periods) {
		if toFloat(getField(p, []string{"BUY"})) == 1 { buyCount++ }
		if toFloat(getField(p, []string{"SELL"})) == 1 { sellCount++ }
		if toFloat(getField(p, []string{"ULTRA_BUY"})) == 1 { ultraBuyCount++ }
		if toFloat(getField(p, []string{"ULTRA_SELL"})) == 1 { ultraSellCount++ }
	}

	agenticScore := 0.3
	if aligned { agenticScore += 0.2 }
	if combinedTrend != "MIXED" { agenticScore += 0.15 }
	if bgTrend != "NEUTRAL" { agenticScore += 0.1 }
	if ultraBuy || ultraSell { agenticScore += 0.15 }
	if buySignal || sellSignal { agenticScore += 0.1 }
	agenticScore = math.Min(agenticScore, 0.99)

	direction := "neutral"
	if combinedTrend == "BULLISH" { direction = "long" }
	if combinedTrend == "BEARISH" { direction = "short" }

	var opps []skill.Opportunity
	if direction != "neutral" {
		score := 0.7
		if ultraBuy || ultraSell { score = 0.9 }
		opps = append(opps, skill.Opportunity{
			Rank: 1, Setup: "dual_supertrend", Direction: direction,
			Confidence: confidenceLabel(score), ConfluenceScore: round2(score),
			Rationale: fmt.Sprintf("%s dual ST alignment. ST1=%.0f ST2=%.0f BG=%s", direction, st1, st2, bgTrend),
		})
	}

	warnings := []string{}
	if !aligned { warnings = append(warnings, "ST1 and ST2 disagree.") }

	return skill.SkillResult{
		Status: "ok", Workflow: "ultra-sensitive-supertrend",
		Market: skill.MarketData{LastPrice: nil, Bias: strings.ToLower(combinedTrend)},
		Structure: map[string]any{"combined": combinedTrend, "aligned": aligned, "st1": round2(st1), "st2": round2(st2), "background": bgTrend, "buySignals": buyCount, "sellSignals": sellCount, "ultraBuy": ultraBuyCount, "ultraSell": ultraSellCount, "currentBuy": buySignal, "currentSell": sellSignal, "currentUltraBuy": ultraBuy, "currentUltraSell": ultraSell},
		Opportunities: opps,
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("Dual SuperTrend: %s. Aligned: %v. ST1=%.0f ST2=%.0f", combinedTrend, aligned, st1, st2), PrimaryOpp: primaryOppFromOpps(opps), Warnings: warnings},
		Validation: skill.Validation{Passed: true}, Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatUST(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  ULTRA SENSITIVE SUPERTREND\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Combined: %v | Aligned: %v\n", result.Structure["combined"], result.Structure["aligned"]))
	sb.WriteString(fmt.Sprintf("  ST1: %v | ST2: %v | BG: %v\n", result.Structure["st1"], result.Structure["st2"], result.Structure["background"]))
	sb.WriteString(fmt.Sprintf("  Buy: %v | Sell: %v | UltraBuy: %v | UltraSell: %v\n", result.Structure["currentBuy"], result.Structure["currentSell"], result.Structure["currentUltraBuy"], result.Structure["currentUltraSell"]))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("\n  -> %s %s [%s] %.2f: %s\n", o.Direction, o.Setup, o.Confidence, o.ConfluenceScore, o.Rationale))
	}
	if len(result.Narrative.Warnings) > 0 {
		sb.WriteString("\n  WARNINGS\n")
		for _, w := range result.Narrative.Warnings { sb.WriteString(fmt.Sprintf("    - %s\n", w)) }
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(USTSkill) }
