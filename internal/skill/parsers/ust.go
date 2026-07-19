package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
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
	st1 := toFloat(getField(last, []string{"plot_0"}))
	st2 := toFloat(getField(last, []string{"plot_2"}))
	st2Color := toFloat(getField(last, []string{"plot_3", "ST2_colorer"}))
	bgColor := toFloat(getField(last, []string{"Background_Color", "plot_4"}))

	// ST2_colorer: 2 = bearish (price below ST), 4 = bullish (price above ST)
	st2Bullish := st2Color == 4
	// ST1 is bullish when price > ST1 (ST1 is below price)
	// We don't have ST1_colorer directly, but can infer from price vs ST1
	st1Bullish := st1 > 0 && st2 > st1 // Simplified: if ST2 > ST1, likely bullish

	aligned := st1Bullish == st2Bullish

	combinedTrend := "MIXED"
	if st1Bullish && st2Bullish { combinedTrend = "BULLISH" }
	if !st1Bullish && !st2Bullish { combinedTrend = "BEARISH" }

	bgTrend := "NEUTRAL"
	if bgColor == 4 { bgTrend = "BULLISH" }
	if bgColor == 5 { bgTrend = "BEARISH" }

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

	agenticScore := 0.2
	if aligned { agenticScore += 0.25 }
	if combinedTrend != "MIXED" { agenticScore += 0.15 }
	if ultraBuy || ultraSell { agenticScore += 0.2 }
	agenticScore = math.Min(agenticScore, 0.99)

	direction := "neutral"
	if aligned {
		if combinedTrend == "BULLISH" { direction = "long" }
		if combinedTrend == "BEARISH" { direction = "short" }
	}

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
		Market: skill.MarketData{LastPrice: st1, Bias: strings.ToLower(combinedTrend)},
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
