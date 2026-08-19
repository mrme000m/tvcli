package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
)

// XAUTrendSkill wraps a public EMA + Bollinger Bands Pine Script focused on
// XAUUSD trend detection. It compares the short and long EMAs, reports the
// Bollinger envelope, and derives a trend bias. No price plot is emitted, so
// the structural trend metrics are the primary output.
var XAUTrendSkill = &skill.Skill{
	Name:     "xau-trend",
	Synopsis: "XAUUSD trend — EMA and Bollinger structure",
	PineID:   "PUB;a4e47455574243fe9731423c4ddb50ca",
	Inputs: []skill.InputDef{
		{Name: "emaShort", TVInputID: "in_0", Type: "int", Default: 9},
		{Name: "emaLong", TVInputID: "in_1", Type: "int", Default: 21},
		{Name: "bollingerPeriod", TVInputID: "in_5", Type: "int", Default: 20},
		{Name: "bollingerMult", TVInputID: "in_6", Type: "float", Default: 2.0},
	},
	Presets: map[string]map[string]any{
		"default":  {"emaShort": 9, "emaLong": 21, "bollingerPeriod": 20, "bollingerMult": 2.0},
		"scalping": {"emaShort": 5, "emaLong": 13, "bollingerPeriod": 20, "bollingerMult": 2.0},
		"swing":    {"emaShort": 21, "emaLong": 55, "bollingerPeriod": 20, "bollingerMult": 2.0},
	},
	ParseOutput: parseXAUTrend,
	FormatText:  formatXAUTrend,
}

func parseXAUTrend(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{
			Status:    "no_data",
			Workflow:  "xau-trend",
			Narrative: skill.Narrative{MarketStructure: "No data"},
		}
	}

	last := latestClosed(periods)
	emaShort := toFloat(getField(last, []string{"EMA_Court_Terme", "plot_0"}))
	emaLong := toFloat(getField(last, []string{"EMA_Long_Terme", "plot_1"}))
	basis := toFloat(getField(last, []string{"Bollinger_Basis", "plot_2"}))
	upper := toFloat(getField(last, []string{"Bollinger_Upper", "plot_3"}))
	lower := toFloat(getField(last, []string{"Bollinger_Lower", "plot_4"}))

	bias := "neutral"
	if emaShort > emaLong {
		bias = "bullish"
	} else if emaShort < emaLong {
		bias = "bearish"
	}

	emaSpread := emaShort - emaLong
	bandWidth := upper - lower
	rawTrendStrength := 0.0
	if emaLong != 0 {
		rawTrendStrength = math.Abs(emaSpread) / emaLong
	}
	trendStrengthPct := rawTrendStrength * 100

	// EMA-crossover detection
	crossover := "none"
	if len(periods) > 2 {
		prev := periods[2] // periods[0] is in-progress, [1] is latest closed, [2] previous closed
		prevShort := toFloat(getField(prev, []string{"EMA_Court_Terme", "plot_0"}))
		prevLong := toFloat(getField(prev, []string{"EMA_Long_Terme", "plot_1"}))
		if prevShort <= prevLong && emaShort > emaLong {
			crossover = "bullish_cross"
		} else if prevShort >= prevLong && emaShort < emaLong {
			crossover = "bearish_cross"
		}
	}

	agenticScore := 0.2
	if len(periods) > 0 {
		agenticScore += 0.2
	}
	if emaShort > 0 && emaLong > 0 {
		agenticScore += 0.1
	}
	if bias != "neutral" {
		agenticScore += 0.1
	}
	if rawTrendStrength > 0.001 {
		agenticScore += 0.1
	}
	if crossover != "none" {
		agenticScore += 0.15
	}
	if basis > lower && basis < upper {
		agenticScore += 0.05
	}
	agenticScore = math.Min(agenticScore, 0.99)

	opps := make([]skill.Opportunity, 0)
	if crossover == "bullish_cross" {
		opps = append(opps, skill.Opportunity{
			Rank:            1,
			Setup:           "ema_crossover",
			Direction:       "long",
			Confidence:      "HIGH",
			ConfluenceScore: round2(0.78),
			Rationale:       "Short EMA crossed above long EMA with Bollinger support",
		})
	} else if crossover == "bearish_cross" {
		opps = append(opps, skill.Opportunity{
			Rank:            1,
			Setup:           "ema_crossover",
			Direction:       "short",
			Confidence:      "HIGH",
			ConfluenceScore: round2(0.78),
			Rationale:       "Short EMA crossed below long EMA with Bollinger resistance",
		})
	}

	return skill.SkillResult{
		Status:   "ok",
		Workflow: "xau-trend",
		Market:   skill.MarketData{LastPrice: 0, Bias: bias},
		Structure: map[string]any{
			"emaShort":         round2(emaShort),
			"emaLong":          round2(emaLong),
			"emaSpread":        round2(emaSpread),
			"trendStrengthPct": round2(trendStrengthPct),
			"crossover":        crossover,
			"bollingerBasis":   round2(basis),
			"bollingerUpper":   round2(upper),
			"bollingerLower":   round2(lower),
			"bandWidth":        round2(bandWidth),
		},
		Opportunities: opps,
		Narrative: skill.Narrative{
			MarketStructure: fmt.Sprintf("EMA short=%.2f long=%.2f | Bollinger %.2f/%.2f/%.2f | Cross: %s", emaShort, emaLong, lower, basis, upper, crossover),
			PrimaryOpp:      primaryOpp(opps),
			Warnings:        []string{},
		},
		Validation:    skill.Validation{Passed: true},
		Conformance:   skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatXAUTrend(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  XAUUSD TREND — EMA + BOLLINGER\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  EMA short:  %.2f\n", result.Structure["emaShort"]))
	sb.WriteString(fmt.Sprintf("  EMA long:   %.2f\n", result.Structure["emaLong"]))
	sb.WriteString(fmt.Sprintf("  Spread:     %.2f\n", result.Structure["emaSpread"]))
	sb.WriteString(fmt.Sprintf("  Strength:   %.2f%%\n", result.Structure["trendStrengthPct"]))
	sb.WriteString(fmt.Sprintf("  Crossover:  %v\n", result.Structure["crossover"]))
	sb.WriteString(fmt.Sprintf("  Bollinger:  %.2f / %.2f / %.2f\n", result.Structure["bollingerLower"], result.Structure["bollingerBasis"], result.Structure["bollingerUpper"]))
	sb.WriteString(fmt.Sprintf("  Bias:       %s\n", result.Market.Bias))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("  -> %s %s [%s] %.2f\n", o.Direction, o.Setup, o.Confidence, o.ConfluenceScore))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(XAUTrendSkill) }
