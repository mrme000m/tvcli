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

	// DVI exposes trend/alert plots named by the script:
	//   Uptrend_Alert / Downtrend_Alert / Sideways_Alert / Background_Color
	//   Volatility_ATR / Momentum_ROC / Support / Resistance
	up := toFloat(getField(last, []string{"Uptrend_Alert", "uptrendAlert", "UptrendAlert"}))
	dn := toFloat(getField(last, []string{"Downtrend_Alert", "downtrendAlert", "DowntrendAlert"}))
	sideways := toFloat(getField(last, []string{"Sideways_Alert", "sidewaysAlert", "SidewaysAlert"}))
	volatility := toFloat(getField(last, []string{"Volatility_ATR", "volatilityATR", "ATR"}))
	momentum := toFloat(getField(last, []string{"Momentum_ROC", "momentumROC", "ROC", "Momentum"}))
	support := toFloat(getField(last, []string{"Support"}))
	resistance := toFloat(getField(last, []string{"Resistance"}))

	trend := 0.0
	bias := "neutral"
	if up > 0 {
		bias = "bullish"
		trend = 1
	} else if dn > 0 {
		bias = "bearish"
		trend = -1
	} else if sideways > 0 {
		bias = "neutral"
		trend = 0
	}

	trendUp, trendDown, sidewaysCount := 0, 0, 0
	for _, p := range historicalBars(periods) {
		if toFloat(getField(p, []string{"Uptrend_Alert", "uptrendAlert"})) > 0 {
			trendUp++
		} else if toFloat(getField(p, []string{"Downtrend_Alert", "downtrendAlert"})) > 0 {
			trendDown++
		} else if toFloat(getField(p, []string{"Sideways_Alert", "sidewaysAlert"})) > 0 {
			sidewaysCount++
		}
	}

	agenticScore := 0.2
	if len(periods) > 0 { agenticScore += 0.2 }
	if up > 0 || dn > 0 { agenticScore += 0.2 }
	if math.Abs(momentum) > 0 { agenticScore += 0.15 }
	if support > 0 && resistance > support { agenticScore += 0.1 }
	agenticScore = math.Min(agenticScore, 0.99)

	opps := []skill.Opportunity{}
	if bias != "neutral" {
		score := 0.6
		if math.Abs(momentum) > 1 { score = 0.8 }
		if bias == "bullish" && momentum > 0 { score += 0.1 }
		if bias == "bearish" && momentum < 0 { score += 0.1 }
		opps = append(opps, skill.Opportunity{
			Rank: 1, Setup: "delta_volume_trend", Direction: bias,
			Confidence: confidenceLabel(score), ConfluenceScore: round2(math.Min(score, 0.99)),
			Rationale: fmt.Sprintf("Trend=%s Vol=%.2f Mom=%.2f SR=[%.2f,%.2f]", bias, volatility, momentum, support, resistance),
		})
	}

	lastPrice := getField(last, []string{"plotcandle_0_ohlc_close", "Close", "close"})

	return skill.SkillResult{
		Status: "ok", Workflow: "trend-following-sr-break",
		Market: skill.MarketData{LastPrice: lastPrice, Bias: bias},
		Structure: map[string]any{
			"trend": trend, "volatility": volatility, "momentum": momentum,
			"trendUp": trendUp, "trendDown": trendDown, "sideways": sidewaysCount,
			"support": support, "resistance": resistance,
		},
		Opportunities: opps,
		Narrative: skill.Narrative{
			MarketStructure: fmt.Sprintf("Trend: %s | Vol: %.2f | Mom: %.2f | S/R: %.2f / %.2f", bias, volatility, momentum, support, resistance),
			PrimaryOpp:      primaryOppFromOpps(opps),
			Warnings:        []string{},
		},
		Validation:    skill.Validation{Passed: true, Warnings: []string{}},
		Conformance:   skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
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
