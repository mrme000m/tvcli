package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
)

var DVISkill = &skill.Skill{
	Name:     "dvi",
	Synopsis: "Delta Volume Intensity — trend, S/R, momentum",
	PineID:   "PUB;bdd3bc54cf9f4dc6b42e6b2879b4eed2",
	Inputs: []skill.InputDef{
		{Name: "length_volatility", TVInputID: "in_0", Type: "int", Default: 14},
		{Name: "length_momentum", TVInputID: "in_1", Type: "int", Default: 14},
		{Name: "lookback_sr", TVInputID: "in_2", Type: "int", Default: 7},
	},
	ParseOutput: parseDVI,
	FormatText:  formatDVI,
}

func parseDVI(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "trend-following-sr-break",
			Narrative: skill.Narrative{MarketStructure: "No data", Warnings: []string{"No period data"}}}
	}
	last := latestClosed(periods)
	trend := toFloat(getField(last, []string{"Trend", "trend", "TrendLine"}))
	volatility := toFloat(getField(last, []string{"Volatility", "volatility", "ATR"}))
	momentum := toFloat(getField(last, []string{"Momentum", "momentum", "ROC"}))
	bias := "neutral"
	if trend > 0 { bias = "bullish" } else if trend < 0 { bias = "bearish" }

	trendUp, trendDown := 0, 0
	for _, p := range historicalBars(periods) {
		t := toFloat(getField(p, []string{"Trend", "trend"}))
		if t > 0 { trendUp++ } else if t < 0 { trendDown++ }
	}

	agenticScore := 0.2
	if len(periods) > 0 { agenticScore += 0.2 }
	if math.Abs(trend) > 0 { agenticScore += 0.2 }
	if math.Abs(momentum) > 0 { agenticScore += 0.15 }
	agenticScore = math.Min(agenticScore, 0.99)

	var opps []skill.Opportunity
	if bias != "neutral" {
		score := 0.6
		if math.Abs(momentum) > 1 { score = 0.8 }
		opps = append(opps, skill.Opportunity{
			Rank: 1, Setup: "delta_volume_trend", Direction: bias,
			Confidence: confidenceLabel(score), ConfluenceScore: round2(score),
			Rationale: fmt.Sprintf("Trend=%s Vol=%.2f Mom=%.2f", bias, volatility, momentum),
		})
	}

	return skill.SkillResult{
		Status: "ok", Workflow: "trend-following-sr-break",
		Market: skill.MarketData{LastPrice: getField(last, []string{"Close", "close"}), Bias: bias},
		Structure: map[string]any{"trend": trend, "volatility": volatility, "momentum": momentum, "trendUp": trendUp, "trendDown": trendDown},
		Opportunities: opps,
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("Trend: %s | Vol: %.2f | Mom: %.2f", bias, volatility, momentum), PrimaryOpp: primaryOppFromOpps(opps)},
		Validation: skill.Validation{Passed: true}, Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatDVI(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  DELTA VOLUME INTENSITY\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Trend: %v | Vol: %v | Mom: %v\n", result.Structure["trend"], result.Structure["volatility"], result.Structure["momentum"]))
	sb.WriteString(fmt.Sprintf("  Bias: %s\n", result.Market.Bias))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("  -> %s %s [%s] %.2f: %s\n", o.Direction, o.Setup, o.Confidence, o.ConfluenceScore, o.Rationale))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(DVISkill) }
