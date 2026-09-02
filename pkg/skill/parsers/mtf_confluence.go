package parsers

// mtf_confluence.go — "mtf-confluence" skill: multi-timeframe confluence in
// ONE study run. The Pine source is embedded (no facade metaInfo, works on
// every pool account and under /hunt); input and plot metadata are
// synthesized from the source by pkg/tradingview's source parser.
//
// The source is vendored in two places: here (go:embed for the skill) and
// faber0's channel_verifier/pine/mtf_confluence.pine (the signal-time gate).
// Keep them in sync — the gate's field mapping depends on the plot titles.

import (
	_ "embed"
	"fmt"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
)

//go:embed mtf_confluence.pine
var mtfConfluenceSource string

var MtfConfluenceSkill = &skill.Skill{
	Name:     "mtf-confluence",
	Synopsis: "MTF Confluence Engine — chart TF + 2 HTF composites in one run (EMA stack, SuperTrend, RSI, alignment, signal grade)",
	// Embedded source: the PineID is a label only (registry requires the ';'
	// shape); RunScript uses Source directly and never resolves it.
	PineID: "USER;mtf-confluence-embedded",
	Source: mtfConfluenceSource,
	Inputs: []skill.InputDef{
		{Name: "htf1", TVInputID: "in_0", Type: "string", Default: "60"},
		{Name: "htf2", TVInputID: "in_1", Type: "string", Default: "240"},
		{Name: "wChart", TVInputID: "in_2", Type: "float", Default: 0.5},
		{Name: "wHtf1", TVInputID: "in_3", Type: "float", Default: 0.3},
		{Name: "wHtf2", TVInputID: "in_4", Type: "float", Default: 0.2},
		{Name: "ema1Len", TVInputID: "in_5", Type: "int", Default: 3},
		{Name: "ema2Len", TVInputID: "in_6", Type: "int", Default: 8},
		{Name: "ema3Len", TVInputID: "in_7", Type: "int", Default: 21},
		{Name: "ema4Len", TVInputID: "in_8", Type: "int", Default: 55},
		{Name: "stAtrLen", TVInputID: "in_9", Type: "int", Default: 10},
		{Name: "stFactor", TVInputID: "in_10", Type: "float", Default: 3.0},
		{Name: "rsiLen", TVInputID: "in_11", Type: "int", Default: 14},
		{Name: "bbLen", TVInputID: "in_12", Type: "int", Default: 20},
		{Name: "bbMult", TVInputID: "in_13", Type: "float", Default: 2.0},
		{Name: "kcLen", TVInputID: "in_14", Type: "int", Default: 20},
		{Name: "kcMult", TVInputID: "in_15", Type: "float", Default: 1.5},
		{Name: "volLen", TVInputID: "in_16", Type: "int", Default: 20},
	},
	Presets: map[string]map[string]any{
		"default": {},
		"scalp":   {"htf1": "60", "htf2": "240"},
		"swing":   {"htf1": "240", "htf2": "D"},
	},
	ParseOutput: parseMtfConfluence,
	FormatText:  formatMtfConfluence,
}

// mtfFields maps logical field → (plot title, plot index) per the plot order
// in mtf_confluence.pine ("DO NOT reorder" there is load-bearing).
var mtfFields = []struct {
	name  string
	title string
	idx   int
}{
	{"composite", "Composite", 0},
	{"emaStack", "EMA_Stack", 1},
	{"stDir", "ST_Dir", 2},
	{"rsi", "RSI", 3},
	{"atr", "ATR", 4},
	{"signalRaw", "Signal", 5},
	{"sl", "SL", 6},
	{"tp", "TP", 7},
	{"h1Composite", "HTF1_Composite", 8},
	{"h1Ema", "HTF1_EMA", 9},
	{"h1St", "HTF1_ST", 10},
	{"h1Rsi", "HTF1_RSI", 11},
	{"h2Composite", "HTF2_Composite", 12},
	{"h2Ema", "HTF2_EMA", 13},
	{"h2St", "HTF2_ST", 14},
	{"h2Rsi", "HTF2_RSI", 15},
	{"mtfComposite", "MTF_Composite", 16},
	{"mtfAligned", "MTF_Aligned", 17},
	{"squeeze", "Squeeze", 18},
	{"volRatio", "Vol_Ratio", 19},
}

func parseMtfConfluence(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "mtf-confluence",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)

	vals := map[string]float64{}
	for _, f := range mtfFields {
		vals[f.name] = toFloat(getField(last, []string{f.title, fmt.Sprintf("plot_%d", f.idx)}))
	}

	// Signal plot is grade*50: ±150 = strong + MTF-confirmed, ±100 strong,
	// ±50 mild, 0 none.
	signalGrade := 0
	switch raw := vals["signalRaw"]; {
	case raw > 125:
		signalGrade = 3
	case raw > 75:
		signalGrade = 2
	case raw > 25:
		signalGrade = 1
	case raw < -125:
		signalGrade = -3
	case raw < -75:
		signalGrade = -2
	case raw < -25:
		signalGrade = -1
	}

	bias := "neutral"
	if vals["mtfComposite"] > 20 {
		bias = "bullish"
	}
	if vals["mtfComposite"] < -20 {
		bias = "bearish"
	}

	price := toFloat(getField(last, []string{"Close", "close", "plotcandle_0_ohlc_close"}))
	warnings := []string{}
	if price == 0 && vals["sl"] > 0 && vals["tp"] > 0 {
		price = vals["sl"] + 2*(vals["tp"]-vals["sl"])/5
		warnings = append(warnings, "price recovered from SL/TP plots; no Close plot emitted")
	}

	structure := map[string]any{
		"composite":    vals["composite"],
		"emaStack":     int(vals["emaStack"]),
		"stDir":        int(vals["stDir"] / 100),
		"rsi":          vals["rsi"],
		"atr":          vals["atr"],
		"signal":       signalGrade,
		"slLevel":      vals["sl"],
		"tpLevel":      vals["tp"],
		"htf1":         map[string]any{"composite": vals["h1Composite"], "emaStack": int(vals["h1Ema"]), "stDir": int(vals["h1St"] / 100), "rsi": vals["h1Rsi"]},
		"htf2":         map[string]any{"composite": vals["h2Composite"], "emaStack": int(vals["h2Ema"]), "stDir": int(vals["h2St"] / 100), "rsi": vals["h2Rsi"]},
		"mtfComposite": vals["mtfComposite"],
		"mtfAligned":   int(vals["mtfAligned"] / 100),
		"squeezeOn":    vals["squeeze"] > 0,
		"volRatio":     vals["volRatio"] / 100,
		"price":        price,
	}

	gradeLabel := map[int]string{
		3: "STRONG LONG (MTF)", 2: "STRONG LONG", 1: "mild long", 0: "neutral",
		-1: "mild short", -2: "STRONG SHORT", -3: "STRONG SHORT (MTF)",
	}[signalGrade]

	opp := []skill.Opportunity{}
	hasLevels := price > 0 && vals["sl"] > 0 && vals["tp"] > 0
	if abs := signalGrade; abs >= 2 && hasLevels {
		dir, d := "long", 1.0
		if signalGrade < 0 {
			dir, d = "short", -1.0
		}
		conf := "medium"
		if abs == 3 {
			conf = "high"
		}
		opp = append(opp, skill.Opportunity{
			Rank: 1, Setup: "MTF scalp " + gradeLabel,
			Direction: dir, Confidence: conf,
			ConfluenceScore:   round2(0.5 + 0.1*float64(abs) + absF(vals["mtfComposite"])/400),
			DistanceFromPrice: 0.0,
			Entry:             price, StopLoss: vals["sl"], TP1: vals["tp"],
			RiskReward: 1.5,
			Rationale: fmt.Sprintf("MTF composite %.0f (chart %.0f / htf1 %.0f / htf2 %.0f), aligned %+.0f, RSI %.0f",
				d*vals["mtfComposite"], d*vals["composite"], d*vals["h1Composite"], d*vals["h2Composite"], vals["mtfAligned"], vals["rsi"]),
		})
	}

	narrative := fmt.Sprintf("Signal: %s | MTF composite: %.0f | chart: %.0f | HTF1: %.0f | HTF2: %.0f | aligned: %+.0f",
		gradeLabel, vals["mtfComposite"], vals["composite"], vals["h1Composite"], vals["h2Composite"], vals["mtfAligned"])

	hasValidData := vals["composite"] != 0 || vals["mtfComposite"] != 0 || price > 0

	return skill.SkillResult{
		Status:        "ok",
		Workflow:      "mtf-confluence",
		Market:        skill.MarketData{Bias: bias, LastPrice: price, LastBarTime: toFloat(last["$time"])},
		Structure:     structure,
		Opportunities: opp,
		Narrative: skill.Narrative{
			MarketStructure: narrative,
			PrimaryOpp:      firstOppText(opp),
			Warnings:        warnings,
		},
		Validation:  skill.Validation{Passed: true},
		Conformance: skill.Conformance{HasValidData: hasValidData, AgenticScore: round2(0.5 + absF(vals["mtfComposite"])/200)},
	}
}

func absF(f float64) float64 {
	if f < 0 {
		return -f
	}
	return f
}

func formatMtfConfluence(result skill.SkillResult) string {
	if result.Status != "ok" {
		return "  " + result.Narrative.MarketStructure + "\n"
	}
	s := result.Structure
	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("  Signal: %v | MTF composite: %.0f | aligned: %+d\n",
		s["signal"], toFloat(s["mtfComposite"]), toInt(s["mtfAligned"])))
	for _, k := range []string{"htf1", "htf2"} {
		if m, ok := s[k].(map[string]any); ok {
			sb.WriteString(fmt.Sprintf("  %s: composite %.0f | EMA %+d | ST %+d | RSI %.0f\n",
				strings.ToUpper(k), toFloat(m["composite"]), toInt(m["emaStack"]), toInt(m["stDir"]), toFloat(m["rsi"])))
		}
	}
	sb.WriteString(fmt.Sprintf("  chart: composite %.0f | EMA %+d | ST %+d | RSI %.0f | ATR %.2f\n",
		toFloat(s["composite"]), toInt(s["emaStack"]), toInt(s["stDir"]), toFloat(s["rsi"]), toFloat(s["atr"])))
	if toFloat(s["slLevel"]) > 0 {
		sb.WriteString(fmt.Sprintf("  Entry: %.2f | SL: %.2f | TP: %.2f\n",
			toFloat(s["price"]), toFloat(s["slLevel"]), toFloat(s["tpLevel"])))
	}
	return sb.String()
}

func init() { skill.Register(MtfConfluenceSkill) }
