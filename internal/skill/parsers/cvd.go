package parsers

import (
	"fmt"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
)

var CVDSkill = &skill.Skill{
	Name:     "cvd",
	Synopsis: "Cumulative Delta Volume — order flow CVD with SMA/EMA overlays",
	PineID:   "PUB;41Rc4hZyPoedSPSuMoAhAj6swEwwFDct",
	Inputs: []skill.InputDef{
		{Name: "style", TVInputID: "in_0", Type: "string", Default: "Candle"},
		{Name: "heikinAshi", TVInputID: "in_1", Type: "bool", Default: true},
		{Name: "sma1Enable", TVInputID: "in_2", Type: "bool", Default: false},
		{Name: "sma1Len", TVInputID: "in_3", Type: "int", Default: 50},
		{Name: "sma2Enable", TVInputID: "in_5", Type: "bool", Default: false},
		{Name: "sma2Len", TVInputID: "in_6", Type: "int", Default: 200},
		{Name: "ema1Enable", TVInputID: "in_8", Type: "bool", Default: false},
		{Name: "ema1Len", TVInputID: "in_9", Type: "int", Default: 50},
		{Name: "ema2Enable", TVInputID: "in_11", Type: "bool", Default: false},
		{Name: "ema2Len", TVInputID: "in_12", Type: "int", Default: 200},
	},
	Presets: map[string]map[string]any{
		"default":  {"heikinAshi": true, "sma1Enable": true, "sma1Len": 50, "sma2Enable": true, "sma2Len": 200},
		"scalping": {"heikinAshi": true, "ema1Enable": true, "ema1Len": 20, "ema2Enable": true, "ema2Len": 50},
		"swing":    {"heikinAshi": true, "ema1Enable": true, "ema1Len": 50, "ema2Enable": true, "ema2Len": 200},
	},
	ParseOutput: parseCVD,
	FormatText:  formatCVD,
}

func parseCVD(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "cumulative-delta-volume",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)
	bars := historicalBars(periods)

	cvd := getValidFloat(last, "plot_1", "CDV_Line")
	ema := getValidFloat(last, "plot_2")
	sma1 := getValidFloat(last, "plot_3")
	sma2 := getValidFloat(last, "plot_4")

	cvdOpen := toFloat(getField(last, []string{"plotcandle_0_ohlc_open"}))
	cvdHigh := toFloat(getField(last, []string{"plotcandle_0_ohlc_high"}))
	cvdLow := toFloat(getField(last, []string{"plotcandle_0_ohlc_low"}))
	cvdClose := toFloat(getField(last, []string{"plotcandle_0_ohlc_close"}))
	if cvdOpen > 1e50 { cvdOpen = cvd }
	if cvdHigh > 1e50 { cvdHigh = cvd }
	if cvdLow > 1e50 { cvdLow = cvd }
	if cvdClose > 1e50 { cvdClose = cvd }
	cvdChange := cvdClose - cvdOpen
	cvdRange := cvdHigh - cvdLow

	// CVD momentum: rate of change over last 5 bars
	prevCVD := cvd
	if len(bars) > 5 {
		prevCVD = getValidFloat(bars[5], "plot_1", "CDV_Line")
	}
	cvdMomentum := cvd - prevCVD

	// Price for divergence check
	price := toFloat(getField(last, []string{"Close", "close", "plotcandle_0_ohlc_close"}))
	prevPrice := price
	if len(bars) > 5 {
		prevPrice = toFloat(getField(bars[5], []string{"Close", "close", "plotcandle_0_ohlc_close"}))
	}
	priceChange := price - prevPrice

	divergence := "none"
	if cvdMomentum > 0 && priceChange < 0 { divergence = "bullish" }
	if cvdMomentum < 0 && priceChange > 0 { divergence = "bearish" }

	// Trend direction based on CVD vs moving averages
	bias := "neutral"
	if ema > 0 && ema < 1e50 {
		if cvd > ema { bias = "bullish" } else { bias = "bearish" }
	} else if sma1 > 0 && sma1 < 1e50 && sma2 > 0 && sma2 < 1e50 {
		if cvd > sma1 && sma1 > sma2 { bias = "bullish" }
		if cvd < sma1 && sma1 < sma2 { bias = "bearish" }
	} else {
		if cvdMomentum > 0 { bias = "bullish" }
		if cvdMomentum < 0 { bias = "bearish" }
	}

	// Agentic score
	score := 0.5
	if bias != "neutral" { score += 0.15 }
	if divergence != "none" { score += 0.2 }
	if cvdRange > 0 && abs(cvdChange) > cvdRange*0.5 { score += 0.1 }
	if score > 1.0 { score = 1.0 }

	structure := map[string]any{
		"cvd":         cvd,
		"cvdChange":   cvdChange,
		"cvdRange":    cvdRange,
		"cvdMomentum": cvdMomentum,
		"ema":         ema,
		"sma1":        sma1,
		"sma2":        sma2,
		"divergence":  divergence,
		"cvdOpen":     cvdOpen,
		"cvdHigh":     cvdHigh,
		"cvdLow":      cvdLow,
		"cvdClose":    cvdClose,
	}

	opp := []skill.Opportunity{}
	if divergence == "bullish" {
		opp = append(opp, skill.Opportunity{
			Rank: 1, Setup: "CVD Bullish Divergence", Direction: "long",
			Confidence: "medium", Rationale: "CVD rising while price falling — accumulation detected",
		})
	}
	if divergence == "bearish" {
		opp = append(opp, skill.Opportunity{
			Rank: 1, Setup: "CVD Bearish Divergence", Direction: "short",
			Confidence: "medium", Rationale: "CVD falling while price rising — distribution detected",
		})
	}

	return skill.SkillResult{
		Status:        "ok",
		Workflow:      "cumulative-delta-volume",
		Market:        skill.MarketData{Bias: bias},
		Structure:     structure,
		Opportunities: opp,
		Narrative: skill.Narrative{
			MarketStructure: fmt.Sprintf("CVD: %.0f (momentum: %.0f)", cvd, cvdMomentum),
			PrimaryOpp:      firstOppText(opp),
		},
		Validation:  skill.Validation{Passed: true},
		Conformance: skill.Conformance{HasValidData: true, AgenticScore: score},
	}
}

func formatCVD(result skill.SkillResult) string {
	s := result.Structure
	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("  CVD: %.0f (change: %.0f, range: %.0f)\n",
		toFloat(s["cvd"]), toFloat(s["cvdChange"]), toFloat(s["cvdRange"])))
	if toFloat(s["ema"]) > 0 {
		sb.WriteString(fmt.Sprintf("  EMA: %.0f | SMA1: %.0f | SMA2: %.0f\n",
			toFloat(s["ema"]), toFloat(s["sma1"]), toFloat(s["sma2"])))
	}
	sb.WriteString(fmt.Sprintf("  Momentum: %.0f | Divergence: %s\n",
		toFloat(s["cvdMomentum"]), s["divergence"]))
	return sb.String()
}

func init() { skill.Register(CVDSkill) }
