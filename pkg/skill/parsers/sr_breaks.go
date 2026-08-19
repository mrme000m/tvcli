package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
)

var SRBreaksSkill = &skill.Skill{
	Name:     "sr-breaks",
	Synopsis: "Support/Resistance Breaks — pivot-based S/R detection",
	PineID:   "PUB;NXS6SoOdr880Hrvh9vA36UcAjC14bOkc",
	Inputs: []skill.InputDef{
		{Name: "showBreaks", TVInputID: "in_0", Type: "bool", Default: true},
		{Name: "leftBars", TVInputID: "in_1", Type: "int", Default: 15},
		{Name: "rightBars", TVInputID: "in_2", Type: "int", Default: 15},
		{Name: "volumeThreshold", TVInputID: "in_3", Type: "int", Default: 20},
	},
	ParseOutput: parseSRBreaks,
	FormatText:  formatSRBreaks,
}

func parseSRBreaks(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "support-resistance-breaks",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	support := toFloat(getField(last, []string{"Support", "support", "SupportLevel"}))
	resistance := toFloat(getField(last, []string{"Resistance", "resistance", "ResistanceLevel"}))

	// Pine indicator periods do not expose OHLC. Use the S/R midpoint as a
	// price proxy so the bias logic has something to compare against.
	price := 0.0
	if support > 0 && resistance > 0 {
		price = (support + resistance) / 2
	}

	// Scan historical bars (newest→oldest) for the most recent break event.
	// Break/Break_3 = bullish (resistance broken up);
	// Break_2/Break_4 = bearish (support broken down, paired with Support_Broken).
	breakDir := 0   // +1 bullish, -1 bearish
	breakBars := 0  // bars since most recent break
	breakType := "" // "bullish" | "bearish" | ""
	for i, p := range historicalBars(periods) {
		b1 := toFloat(getField(p, []string{"Break"}))
		b2 := toFloat(getField(p, []string{"Break_2"}))
		b3 := toFloat(getField(p, []string{"Break_3"}))
		b4 := toFloat(getField(p, []string{"Break_4"}))
		if b1 != 0 || b3 != 0 {
			breakDir = 1
			breakBars = i
			breakType = "bullish"
			break
		}
		if b2 != 0 || b4 != 0 {
			breakDir = -1
			breakBars = i
			breakType = "bearish"
			break
		}
	}

	// Bias: latest break direction; fall back to midpoint position vs S/R.
	bias := "neutral"
	if breakDir > 0 {
		bias = "bullish"
	} else if breakDir < 0 {
		bias = "bearish"
	}

	agenticScore := 0.2
	if len(periods) > 0 {
		agenticScore += 0.2
	}
	if support > 0 || resistance > 0 {
		agenticScore += 0.2
	}
	if breakDir != 0 {
		agenticScore += 0.2
	}
	agenticScore = math.Min(agenticScore, 0.99)

	structure := map[string]any{
		"support":    support,
		"resistance": resistance,
		"price":      price,
		"bias":       bias,
		"lastBreak":  breakType,
		"breakBarsAgo": breakBars,
	}

	opps := []skill.Opportunity{}
	if breakDir != 0 && breakBars < 20 {
		dir := "long"
		if breakDir < 0 {
			dir = "short"
		}
		opps = append(opps, skill.Opportunity{
			Rank: 1, Setup: "sr_break", Direction: dir, Confidence: "MEDIUM",
			IsStale: breakBars > 5,
			Rationale: fmt.Sprintf("%s break %d bars ago (S=%.2f R=%.2f)", breakType, breakBars, support, resistance),
		})
	}

	return skill.SkillResult{
		Status: "ok", Workflow: "support-resistance-breaks",
		Market:    skill.MarketData{LastPrice: price, Bias: bias},
		Structure: structure,
		Opportunities: opps,
		Narrative:  skill.Narrative{MarketStructure: fmt.Sprintf("Support: %.2f | Resistance: %.2f | Last break: %s (%d bars ago)", support, resistance, breakType, breakBars)},
		Validation: skill.Validation{Passed: true},
		Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatSRBreaks(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  SUPPORT/RESISTANCE BREAKS\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Support: %v | Resistance: %v\n", result.Structure["support"], result.Structure["resistance"]))
	sb.WriteString(fmt.Sprintf("  Price (S/R midpoint): %v | Bias: %s\n", result.Market.LastPrice, result.Market.Bias))
	if lb, ok := result.Structure["lastBreak"].(string); ok && lb != "" {
		sb.WriteString(fmt.Sprintf("  Last break: %s (%v bars ago)\n", lb, result.Structure["breakBarsAgo"]))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(SRBreaksSkill) }
