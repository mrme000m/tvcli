package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
)

var EMAATRSkill = &skill.Skill{
	Name:     "ema-atr",
	Synopsis: "EMA + ATR Pro Engine — trailing stop with re-entry",
	PineID:   "PUB;7d5f8755ab67400899ef73a9898471e4",
	Inputs: []skill.InputDef{
		{Name: "ema2Len", TVInputID: "in_0", Type: "int", Default: 20},
		{Name: "ema3Len", TVInputID: "in_1", Type: "int", Default: 50},
		{Name: "useEMA2", TVInputID: "in_2", Type: "bool", Default: true},
		{Name: "useEMA3", TVInputID: "in_3", Type: "bool", Default: false},
		{Name: "pivotLen", TVInputID: "in_4", Type: "int", Default: 1},
		{Name: "atrLen", TVInputID: "in_5", Type: "int", Default: 7},
		{Name: "atrMult", TVInputID: "in_6", Type: "float", Default: 1.4},
		{Name: "confirmClose", TVInputID: "in_7", Type: "bool", Default: true},
		{Name: "fastMode", TVInputID: "in_8", Type: "bool", Default: false},
		{Name: "enableReentry", TVInputID: "in_9", Type: "bool", Default: false},
	},
	ParseOutput: parseEMAATR,
	FormatText:  formatEMAATR,
}

func parseEMAATR(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "ema-atr-structure",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	plot0 := toFloat(getField(last, []string{"plot_0", "Plot"}))
	plot2 := toFloat(getField(last, []string{"plot_2", "Plot_2"}))
	plot2Color := toFloat(getField(last, []string{"Plot_2_colorer"}))
	buySignal := toFloat(getField(last, []string{"BUY_Signal"})) == 1
	sellSignal := toFloat(getField(last, []string{"SELL_Signal"})) == 1
	buyReentry := toFloat(getField(last, []string{"BUY_Re_entry"})) == 1
	sellReentry := toFloat(getField(last, []string{"SELL_Re_entry"})) == 1

	// plot2Color: 2 = bullish, 3 = bearish, 4 = neutral
	bias := "neutral"
	if plot2Color == 2 { bias = "bullish" }
	if plot2Color == 3 { bias = "bearish" }

	// Compute trail trend from plot relationship
	trailTrend := 0.0
	if plot0 > plot2 { trailTrend = 1 }
	if plot0 < plot2 { trailTrend = -1 }

	buyCount, sellCount := 0, 0
	for _, p := range historicalBars(periods) {
		if toFloat(getField(p, []string{"BUY_Signal"})) == 1 { buyCount++ }
		if toFloat(getField(p, []string{"SELL_Signal"})) == 1 { sellCount++ }
	}

	agenticScore := 0.2
	if len(periods) > 0 { agenticScore += 0.2 }
	if math.Abs(trailTrend) > 0 { agenticScore += 0.15 }
	if buySignal || sellSignal { agenticScore += 0.2 }
	agenticScore = math.Min(agenticScore, 0.99)

	var opps []skill.Opportunity
	if buySignal || sellSignal || buyReentry || sellReentry {
		dir := "long"
		if sellSignal || sellReentry { dir = "short" }
		score := 0.7
		if buyReentry || sellReentry { score = 0.65 } // Re-entry slightly lower confidence
		opps = append(opps, skill.Opportunity{
			Rank: 1, Setup: "ema_atr_signal", Direction: dir,
			Confidence: confidenceLabel(score), ConfluenceScore: round2(score),
			Rationale: fmt.Sprintf("EMA+ATR %s signal. Plot0=%.0f Plot2=%.0f", dir, plot0, plot2),
		})
	}

	return skill.SkillResult{
		Status: "ok", Workflow: "ema-atr-structure",
		Market: skill.MarketData{LastPrice: plot0, Bias: bias},
		Structure: map[string]any{"trailTrend": trailTrend, "plot0": round2(plot0), "plot2": round2(plot2), "buySignal": buySignal, "sellSignal": sellSignal, "buyReentry": buyReentry, "sellReentry": sellReentry},
		Opportunities: opps,
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("Plot0: %.0f | Plot2: %.0f | Bias: %s", plot0, plot2, bias), PrimaryOpp: primaryOppFromOpps(opps)},
		Validation: skill.Validation{Passed: true}, Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatEMAATR(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  EMA + ATR PRO ENGINE\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Plot0: %v | Plot2: %v\n", result.Structure["plot0"], result.Structure["plot2"]))
	sb.WriteString(fmt.Sprintf("  Buy: %v | Sell: %v | Reentry Buy: %v | Reentry Sell: %v\n", result.Structure["buySignal"], result.Structure["sellSignal"], result.Structure["buyReentry"], result.Structure["sellReentry"]))
	sb.WriteString(fmt.Sprintf("  Bias: %s | Price: %v\n", result.Market.Bias, result.Market.LastPrice))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("  -> %s %s [%s] %.2f\n", o.Direction, o.Setup, o.Confidence, o.ConfluenceScore))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

// REMOVED: low signal quality (score 0.55), overlaps with swingarm
// func init() { skill.Register(EMAATRSkill) }
