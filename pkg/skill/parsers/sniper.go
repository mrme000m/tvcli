package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
)

var SniperSkill = &skill.Skill{
	Name:     "sniper",
	Synopsis: "BS Buy & Sell Signals with EMA — multi-EMA confluence with buy/sell signals",
	PineID:   "PUB;0287a71c10904118b75d4360a32c0579",
	Inputs: []skill.InputDef{
		{Name: "ema1Len", TVInputID: "in_0", Type: "int", Default: 2},
		{Name: "ema2Len", TVInputID: "in_1", Type: "int", Default: 4},
		{Name: "ema3Len", TVInputID: "in_2", Type: "int", Default: 6},
		{Name: "ema4Len", TVInputID: "in_3", Type: "int", Default: 8},
		{Name: "ema5Len", TVInputID: "in_4", Type: "int", Default: 10},
	},
	Presets: map[string]map[string]any{
		"default":  {},
		"scalping": {"ema1Len": 2, "ema2Len": 4, "ema3Len": 6, "ema4Len": 8, "ema5Len": 10},
		"swing":    {"ema1Len": 10, "ema2Len": 20, "ema3Len": 50, "ema4Len": 100, "ema5Len": 200},
	},
	ParseOutput: parseSniper,
	FormatText:  formatSniper,
}

func parseSniper(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "bs-buy-sell-ema",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	ema1 := toFloat(getField(last, []string{"EMA_1", "plot_0"}))
	ema2 := toFloat(getField(last, []string{"EMA_2", "plot_2"}))
	ema3 := toFloat(getField(last, []string{"EMA_3", "plot_4"}))
	ema4 := toFloat(getField(last, []string{"EMA_4", "plot_6"}))
	ema5 := toFloat(getField(last, []string{"EMA_5", "plot_8"}))
	buySignal := toFloat(getField(last, []string{"Buy_Signal", "plot_10"})) == 1
	sellSignal := toFloat(getField(last, []string{"Sell_Signal", "plot_11"})) == 1
	resistance := toFloat(getField(last, []string{"Resistance", "plot_20"}))
	support := toFloat(getField(last, []string{"Support", "plot_21"}))
	price := toFloat(getField(last, []string{"Close", "close"}))
	if price == 0 {
		price = ema1
	}

	// Bias from EMA alignment (shortest > longest = bullish)
	bias := "neutral"
	if ema1 > ema5 && ema5 > 0 {
		bias = "bullish"
	} else if ema1 < ema5 && ema5 > 0 {
		bias = "bearish"
	}

	// Score from EMA separation
	score := 0.0
	if ema1 > 0 && ema5 > 0 {
		diff := math.Abs(ema1-ema5) / ema5 * 100
		score = math.Min(diff, 5.0)
	}

	// Continuous agentic score: base for data, scaled by EMA separation,
	// bias clarity, and active signals.
	agenticScore := 0.2
	if len(periods) > 0 {
		agenticScore += 0.2
	}
	// Scale score contribution continuously (0-5 range → 0-0.2 contribution).
	agenticScore += math.Min(score/5.0, 1.0) * 0.2
	if bias != "neutral" {
		agenticScore += 0.15
	}
	if buySignal || sellSignal {
		agenticScore += 0.15
	}
	// Bonus for strong EMA alignment (all EMAs stacked bullishly or bearishly).
	if ema1 > ema2 && ema2 > ema3 && ema3 > ema4 && ema4 > ema5 {
		agenticScore += 0.1
	}
	if ema1 < ema2 && ema2 < ema3 && ema3 < ema4 && ema4 < ema5 {
		agenticScore += 0.1
	}
	agenticScore = math.Min(agenticScore, 0.99)

	var opps []skill.Opportunity
	if bias != "neutral" && (buySignal || sellSignal || score >= 1.5) {
		dir := "long"
		if bias == "bearish" || sellSignal {
			dir = "short"
		}
		scoreNorm := score / 5.0
		if scoreNorm > 1 {
			scoreNorm = 1
		}
		opps = append(opps, skill.Opportunity{
			Rank: 1, Setup: "ema_confluence", Direction: dir,
			Confidence: confidenceLabel(scoreNorm), ConfluenceScore: round2(scoreNorm),
			Rationale: fmt.Sprintf("Score=%.1f EMA=%s Fast=%.0f Slow=%.0f", score, bias, ema1, ema5),
		})
	}

	return skill.SkillResult{
		Status: "ok", Workflow: "bs-buy-sell-ema",
		Market: skill.MarketData{LastPrice: price, Bias: bias},
		Structure: map[string]any{
			"score": round2(score), "ema1": round2(ema1), "ema2": round2(ema2),
			"ema3": round2(ema3), "ema4": round2(ema4), "ema5": round2(ema5),
			"buySignal": buySignal, "sellSignal": sellSignal,
			"resistance": resistance, "support": support,
		},
		Opportunities: opps,
		Narrative: skill.Narrative{
			MarketStructure: fmt.Sprintf("Score: %.1f | EMA: %s | Fast: %.0f Slow: %.0f", score, bias, ema1, ema5),
			PrimaryOpp:      primaryOppFromOpps(opps),
		},
		Validation:  skill.Validation{Passed: true},
		Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatSniper(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  BS BUY & SELL SIGNALS WITH EMA\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Score: %v | Bias: %s\n", result.Structure["score"], result.Market.Bias))
	sb.WriteString(fmt.Sprintf("  EMA 1: %v | EMA 5: %v\n", result.Structure["ema1"], result.Structure["ema5"]))
	sb.WriteString(fmt.Sprintf("  Buy: %v | Sell: %v\n", result.Structure["buySignal"], result.Structure["sellSignal"]))
	sb.WriteString(fmt.Sprintf("  Support: %v | Resistance: %v\n", result.Structure["support"], result.Structure["resistance"]))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("\n  -> %s %s [%s] %.2f: %s\n", o.Direction, o.Setup, o.Confidence, o.ConfluenceScore, o.Rationale))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(SniperSkill) }
