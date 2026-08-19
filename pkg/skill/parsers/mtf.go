package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
	"github.com/mrme000m/tvcli/pkg/pipeline"
	"github.com/mrme000m/tvcli/pkg/schema"
)

var MTFSkill = &skill.Skill{
	Name:     "mtf",
	Synopsis: "XAUUSD Multi-Timeframe Trend Dashboard",
	PineID:   "PUB;d1ad30c0261f49f297357f8aa2a7854a",
	KnownBroken: "Graphics-only (renders a trend table, no period data); XAUUSD-specific, so bias is meaningless on other symbols.",
	Inputs: []skill.InputDef{
		{Name: "show_M15", TVInputID: "in_0", Type: "bool", Default: true},
		{Name: "show_M30", TVInputID: "in_1", Type: "bool", Default: true},
		{Name: "show_H1", TVInputID: "in_2", Type: "bool", Default: true},
		{Name: "show_H4", TVInputID: "in_3", Type: "bool", Default: true},
		{Name: "show_D1", TVInputID: "in_4", Type: "bool", Default: true},
		{Name: "fastLength", TVInputID: "in_5", Type: "int", Default: 10},
		{Name: "slowLength", TVInputID: "in_6", Type: "int", Default: 20},
		{Name: "rsiLength", TVInputID: "in_7", Type: "int", Default: 14},
	},
	ParseOutput:     parseMTF,
	ParseWithSchema: parseMTFGraphic,
	FormatText:      formatMTF,
}

// mtfTimeframeWeight gives higher timeframes more influence when we roll the
// per-TF trend strengths into one overall bias. Keys match the column headers
// the script emits (case-sensitive is irrelevant; lookup is normalized).
var mtfTimeframeWeight = map[string]float64{
	"M1": 0.2, "M5": 0.3, "M15": 0.5, "15": 0.5,
	"M30": 1, "30": 1,
	"H1": 1.5, "60": 1.5, "1H": 1.5,
	"H4": 2, "240": 2, "4H": 2,
	"D1": 3, "D": 3, "1D": 3,
	"W1": 4, "W": 4, "1W": 4,
	"M1M": 5, "M": 5, "1M": 5,
}

func parseMTF(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if grids := pipeline.ReconstructTables(graphic); len(grids) > 0 {
		return mtfFromGrid(grids[0], symbol)
	}
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "xauusd-mtf-trend",
			Narrative: skill.Narrative{MarketStructure: "No data (no periods and no dashboard graphic)"}}
	}
	last := latestClosed(periods)
	price := toFloat(getField(last, []string{"Close", "close"}))
	overallBias := toFloat(getField(last, []string{"OverallBias", "overallBias", "Bias"}))

	bias := "neutral"
	if overallBias > 0 { bias = "bullish" } else if overallBias < 0 { bias = "bearish" }

	agenticScore := 0.2
	if len(periods) > 0 { agenticScore += 0.2 }
	if math.Abs(overallBias) > 0 { agenticScore += 0.2 }
	agenticScore = math.Min(agenticScore, 0.99)

	return skill.SkillResult{
		Status: "ok", Workflow: "xauusd-mtf-trend",
		Market: skill.MarketData{LastPrice: price, Bias: bias},
		Structure: map[string]any{"overallBias": overallBias, "price": price},
		Opportunities: []skill.Opportunity{},
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("MTF Bias: %s | Price: %.2f", bias, price)},
		Validation: skill.Validation{Passed: true}, Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

// parseMTFGraphic prefers the graphic-layer trend table (the script emits no
// period data), falling back to periods when present.
func parseMTFGraphic(periods []map[string]any, graphic map[string]map[string]any, sch *schema.PineSchema, tf string, symbol string, args map[string]string) skill.SkillResult {
	return parseMTF(periods, graphic, tf, symbol, args)
}

// mtfFromGrid turns the dashboard table into a per-timeframe bias map and a
// single overall bias computed from weighted trend strength.
func mtfFromGrid(g pipeline.TableGrid, symbol string) skill.SkillResult {
	header := g.Header() // ["Timeframe", "M15", "M30", "H1", "H4", "D1"]
	if len(header) < 2 {
		return skill.SkillResult{Status: "no_data", Workflow: "xauusd-mtf-trend",
			Narrative: skill.Narrative{MarketStructure: "Dashboard present but no timeframe columns"}}
	}
	trendRow := g.RowByLabel("Trend")
	strengthRow := g.RowByLabel("Strength")

	timeframes := map[string]any{}
	var weighted float64
	for c := 1; c < len(header); c++ {
		tfName := strings.TrimSpace(header[c])
		trend := ""
		strength := 0.0
		if trendRow >= 0 && trendRow < len(g.Cells) {
			trend = g.Cells[trendRow][c]
		}
		if strengthRow >= 0 && strengthRow < len(g.Cells) {
			strength = parseNumeric(g.Cells[strengthRow][c])
		}
		timeframes[tfName] = map[string]any{"trend": trend, "strength": strength}
		w := 1.0
		if wt, ok := mtfTimeframeWeight[strings.ToUpper(tfName)]; ok {
			w = wt
		}
		weighted += strength * w
	}

	bias := "neutral"
	if weighted > 0 {
		bias = "bullish"
	} else if weighted < 0 {
		bias = "bearish"
	}

	score := 0.4
	if bias != "neutral" {
		score += 0.2
	}
	score = math.Min(score, 0.99)

	return skill.SkillResult{
		Status: "ok", Workflow: "xauusd-mtf-trend",
		Market: skill.MarketData{Bias: bias},
		Structure: map[string]any{
			"symbol":       symbol,
			"timeframes":   timeframes,
			"overallBias":  round2(weighted),
			"bias":         bias,
		},
		Opportunities: []skill.Opportunity{},
		Narrative: skill.Narrative{MarketStructure: fmt.Sprintf("MTF Bias: %s (weighted strength %.1f)", bias, weighted)},
		Validation:    skill.Validation{Passed: true},
		Conformance:    skill.Conformance{HasValidData: true, AgenticScore: round2(score)},
	}
}

func formatMTF(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  XAUUSD MTF TREND DASHBOARD\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Overall Bias: %v\n", result.Structure["overallBias"]))
	sb.WriteString(fmt.Sprintf("  Bias: %s\n", result.Market.Bias))
	if tfs, ok := result.Structure["timeframes"].(map[string]any); ok {
		sb.WriteString("\n  Per-timeframe:\n")
		for tf, v := range tfs {
			if m, ok := v.(map[string]any); ok {
				sb.WriteString(fmt.Sprintf("    %-4s %-18s strength=%v\n", tf, m["trend"], m["strength"]))
			}
		}
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

// REMOVED: graphics-only table, no period data
// func init() { skill.Register(MTFSkill) }
