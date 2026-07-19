package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
)

var SniperSkill = &skill.Skill{
	Name:     "sniper",
	Synopsis: "Precision Sniper — EMA confluence with grade signals",
	PineID:   "PUB;1fc29950178c42a1a88f52a18161dd53",
	Inputs: []skill.InputDef{
		{Name: "sourceInput", TVInputID: "in_0", Type: "source", Default: "close"},
		{Name: "htfInput", TVInputID: "in_1", Type: "timeframe", Default: ""},
		{Name: "presetInput", TVInputID: "in_2", Type: "string", Default: "Auto"},
		{Name: "emaFastLenInput", TVInputID: "in_3", Type: "int", Default: 9},
		{Name: "emaSlowLenInput", TVInputID: "in_4", Type: "int", Default: 21},
		{Name: "emaTrendLenInput", TVInputID: "in_5", Type: "int", Default: 55},
		{Name: "minScoreInput", TVInputID: "in_6", Type: "int", Default: 5},
		{Name: "rsiLenInput", TVInputID: "in_7", Type: "int", Default: 13},
		{Name: "gradeFilterInput", TVInputID: "in_8", Type: "string", Default: "All"},
		{Name: "atrLenInput", TVInputID: "in_10", Type: "int", Default: 14},
		{Name: "slMultInput", TVInputID: "in_11", Type: "float", Default: 1.5},
		{Name: "tp1MultInput", TVInputID: "in_12", Type: "float", Default: 1},
		{Name: "tp2MultInput", TVInputID: "in_13", Type: "float", Default: 2},
		{Name: "tp3MultInput", TVInputID: "in_14", Type: "float", Default: 3},
	},
	Presets: map[string]map[string]any{
		"auto":        {"presetInput": "Auto"},
		"conservative": {"presetInput": "Conservative"},
		"default":     {"presetInput": "Default"},
		"aggressive":  {"presetInput": "Aggressive"},
		"scalping":    {"presetInput": "Scalping"},
		"swing":       {"presetInput": "Swing"},
		"crypto":      {"presetInput": "Crypto"},
	},
	ParseOutput: parseSniper,
	FormatText:  formatSniper,
}

func parseSniper(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "ema-confluence-sniper",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	emaFast := toFloat(getField(last, []string{"EMA_Fast", "plot_0"}))
	emaSlow := toFloat(getField(last, []string{"EMA_Slow", "plot_2"}))
	emaTrend := toFloat(getField(last, []string{"EMA_Trend", "plot_5"}))
	buySignal := toFloat(getField(last, []string{"Buy_Signal", "plot_8"})) == 1
	sellSignal := toFloat(getField(last, []string{"Sell_Signal", "plot_9"})) == 1

	// Compute bias from EMA alignment
	bias := "neutral"
	if emaFast > emaSlow && emaSlow > emaTrend { bias = "bullish" }
	if emaFast < emaSlow && emaSlow < emaTrend { bias = "bearish" }

	// Compute score from EMA separation
	score := 0.0
	if emaFast > 0 && emaSlow > 0 && emaTrend > 0 {
		diff1 := math.Abs(emaFast-emaSlow) / emaSlow * 100
		diff2 := math.Abs(emaSlow-emaTrend) / emaTrend * 100
		score = (diff1 + diff2) * 10
		if score > 5 { score = 5 }
	}

	agenticScore := 0.2
	if len(periods) > 0 { agenticScore += 0.2 }
	if score > 3 { agenticScore += 0.2 }
	if bias != "neutral" { agenticScore += 0.15 }
	if buySignal || sellSignal { agenticScore += 0.15 }
	agenticScore = math.Min(agenticScore, 0.99)

	var opps []skill.Opportunity
	if (score >= 3 || buySignal || sellSignal) && bias != "neutral" {
		dir := "long"
		if bias == "bearish" || sellSignal { dir = "short" }
		scoreNorm := score / 5.0
		if scoreNorm > 1 { scoreNorm = 1 }
		opps = append(opps, skill.Opportunity{
			Rank: 1, Setup: "ema_confluence", Direction: dir,
			Confidence: confidenceLabel(scoreNorm), ConfluenceScore: round2(scoreNorm),
			Rationale: fmt.Sprintf("Score=%.1f EMA=%s Fast=%.0f Slow=%.0f Trend=%.0f", score, bias, emaFast, emaSlow, emaTrend),
		})
	}

	return skill.SkillResult{
		Status: "ok", Workflow: "ema-confluence-sniper",
		Market: skill.MarketData{LastPrice: emaFast, Bias: bias},
		Structure: map[string]any{"score": round2(score), "emaFast": round2(emaFast), "emaSlow": round2(emaSlow), "emaTrend": round2(emaTrend), "buySignal": buySignal, "sellSignal": sellSignal},
		Opportunities: opps,
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("Score: %.1f | EMA: %s | Fast: %.0f Slow: %.0f Trend: %.0f", score, bias, emaFast, emaSlow, emaTrend), PrimaryOpp: primaryOppFromOpps(opps)},
		Validation: skill.Validation{Passed: true}, Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatSniper(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  PRECISION SNIPER\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Score: %v | Bias: %s\n", result.Structure["score"], result.Market.Bias))
	sb.WriteString(fmt.Sprintf("  EMA Fast: %v | Slow: %v | Trend: %v\n", result.Structure["emaFast"], result.Structure["emaSlow"], result.Structure["emaTrend"]))
	sb.WriteString(fmt.Sprintf("  Buy: %v | Sell: %v\n", result.Structure["buySignal"], result.Structure["sellSignal"]))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("\n  -> %s %s [%s] %.2f: %s\n", o.Direction, o.Setup, o.Confidence, o.ConfluenceScore, o.Rationale))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(SniperSkill) }
