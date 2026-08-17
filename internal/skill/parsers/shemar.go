package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
)

var ShemarSkill = &skill.Skill{
	Name:     "shemar",
	Synopsis: "SHEMAR HMA ST + SMC Confidence — HMA, Supertrend, Kernel convergence",
	PineID:   "PUB;70f6e4e05f9c439c9d1f8fe26019357e",
	Inputs: []skill.InputDef{
		{Name: "hmaLength", TVInputID: "in_0", Type: "int", Default: 50},
		{Name: "atrPeriod", TVInputID: "in_1", Type: "int", Default: 10},
		{Name: "factor", TVInputID: "in_2", Type: "int", Default: 3},
		{Name: "enableShorts", TVInputID: "in_3", Type: "bool", Default: true},
		{Name: "useStopEntry", TVInputID: "in_4", Type: "bool", Default: true},
		{Name: "htfPeriod", TVInputID: "in_6", Type: "int", Default: 50},
		{Name: "sqzLength", TVInputID: "in_7", Type: "int", Default: 20},
		{Name: "sqzMult", TVInputID: "in_8", Type: "int", Default: 2},
		{Name: "kernelPeriod", TVInputID: "in_13", Type: "int", Default: 30},
		{Name: "confidenceThresh", TVInputID: "in_14", Type: "int", Default: 30},
	},
	ParseOutput: parseShemar,
	FormatText:  formatShemar,
}

func parseShemar(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "shemar-smc-confidence",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	buySignal := toFloat(getField(last, []string{"BUY", "Buy", "BuySignal"})) == 1
	sellSignal := toFloat(getField(last, []string{"SELL", "Sell", "SellSignal"})) == 1
	price := toFloat(getField(last, []string{"Close", "close"}))

	bias := "neutral"
	if buySignal { bias = "bullish" }
	if sellSignal { bias = "bearish" }

	buyCount, sellCount := 0, 0
	for _, p := range historicalBars(periods) {
		if toFloat(getField(p, []string{"BUY", "Buy"})) == 1 { buyCount++ }
		if toFloat(getField(p, []string{"SELL", "Sell"})) == 1 { sellCount++ }
	}

	agenticScore := 0.2
	if len(periods) > 0 { agenticScore += 0.2 }
	if buySignal || sellSignal { agenticScore += 0.25 }
	agenticScore = math.Min(agenticScore, 0.99)

	var opps []skill.Opportunity
	if buySignal || sellSignal {
		dir := "long"
		if sellSignal { dir = "short" }
		opps = append(opps, skill.Opportunity{
			Rank: 1, Setup: "shemar_confidence", Direction: dir,
			Confidence: confidenceLabel(0.75), ConfluenceScore: 0.75,
			Rationale: fmt.Sprintf("SHEMAR %s signal. HMA+Kernel+squeeze convergence.", dir),
		})
	}

	return skill.SkillResult{
		Status: "ok", Workflow: "shemar-smc-confidence",
		Market: skill.MarketData{LastPrice: price, Bias: bias},
		Structure: map[string]any{"buySignal": buySignal, "sellSignal": sellSignal, "buyCount": buyCount, "sellCount": sellCount},
		Opportunities: opps,
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("Buy signals: %d | Sell signals: %d", buyCount, sellCount), PrimaryOpp: primaryOppFromOpps(opps)},
		Validation: skill.Validation{Passed: true}, Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatShemar(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  SHEMAR SMC CONFIDENCE\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Buy: %v | Sell: %v\n", result.Structure["buySignal"], result.Structure["sellSignal"]))
	sb.WriteString(fmt.Sprintf("  Bias: %s | Price: %v\n", result.Market.Bias, result.Market.LastPrice))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("  -> %s %s [%s] %.2f\n", o.Direction, o.Setup, o.Confidence, o.ConfluenceScore))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

// REMOVED: low signal quality (score 0.4)
// func init() { skill.Register(ShemarSkill) }
