package parsers

import (
	"fmt"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
	"github.com/mrme000m/tvcli/pkg/schema"
)

// VPProSkill wraps the local consolidated "VP Pro — Fixed + Anchored Volume
// Profile" script (assets/vp-pro.pine). It draws the profile as boxes and
// POC/VAH/VAL as lines + labels; the numeric POC/VAH/VAL plots are gated on
// barstate.islast, so this parser reads the levels from the graphic layer
// (labels first, lines as fallback). An ungated, non-displayed Close plot
// carries the reference price for bias and level distances.
var VPProSkill = &skill.Skill{
	Name:            "vp-pro",
	Synopsis:        "Fixed-Range + Anchored Volume Profile — POC, VAH, VAL, value area",
	PineID:          "USER;d496e2656da745a5b79f39140bde7c1f",
	RequiresGraphic: true,
	Inputs: []skill.InputDef{
		{Name: "mode", TVInputID: "in_0", Type: "string", Default: "Fixed Range"},
		{Name: "numBars", TVInputID: "in_1", Type: "int", Default: 150},
		{Name: "anchorOffset", TVInputID: "in_2", Type: "int", Default: 60},
		{Name: "rows", TVInputID: "in_3", Type: "int", Default: 24},
		{Name: "valueAreaPct", TVInputID: "in_4", Type: "float", Default: 70},
	},
	Presets: map[string]map[string]any{
		"fixed":    {"mode": "Fixed Range", "numBars": 150, "rows": 24, "valueAreaPct": 70},
		"anchored": {"mode": "Anchored", "anchorOffset": 100, "rows": 40, "valueAreaPct": 68},
		"scalping": {"mode": "Fixed Range", "numBars": 60, "rows": 20, "valueAreaPct": 70},
	},
	ParseOutput:     parseVPPro,
	ParseWithSchema: parseVPProSchema,
	FormatText:      formatVPPro,
}

func parseVPPro(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	return parseVPProSchema(periods, graphic, nil, tf, symbol, args)
}

func parseVPProSchema(periods []map[string]any, graphic map[string]map[string]any, sch *schema.PineSchema, tf string, symbol string, args map[string]string) skill.SkillResult {
	poc, vah, val := vpLevelsFromGraphic(graphic)
	if poc == 0 && vah == 0 && val == 0 {
		return skill.SkillResult{
			Status:   "no_data",
			Workflow: "volume-profile",
			Narrative: skill.Narrative{
				MarketStructure: "No POC/VAH/VAL in graphic layer (labels or lines)",
				Warnings:        []string{"Volume Profile script returned no graphic levels"},
			},
		}
	}

	// Reference price from the ungated Close plot. Market.LastPrice stays 0
	// here — the command layer back-fills it via FetchOHLCV — but the parser
	// uses `price` internally for bias and level distances.
	price := 0.0
	if len(periods) > 0 {
		price = SchemaFloat(latestClosed(periods), sch, "Close", "close")
		if price > 1e50 {
			price = 0
		}
	}

	// Per-level guards: with show_va=false (or a missing line fallback) VAH/VAL
	// parse as 0; never emit opportunities for levels that did not parse.
	var warnings []string
	if poc == 0 {
		warnings = append(warnings, "POC missing from graphic layer (show_poc off?)")
	}
	if vah == 0 {
		warnings = append(warnings, "VAH missing from graphic layer (show_va off?)")
	}
	if val == 0 {
		warnings = append(warnings, "VAL missing from graphic layer (show_va off?)")
	}
	if price == 0 {
		warnings = append(warnings, "Close plot missing; bias and distances unavailable")
	}

	status := "ok"
	if poc == 0 || vah == 0 || val == 0 {
		status = "partial"
	}

	// Bias from the close's position relative to the value area (or POC when
	// the value area is unavailable).
	bias := "neutral"
	position := ""
	if price > 0 {
		switch {
		case vah > 0 && val > 0:
			switch {
			case price > vah:
				bias, position = "bullish", "above_value_area"
			case price < val:
				bias, position = "bearish", "below_value_area"
			default:
				position = "inside_value_area"
			}
		case poc > 0:
			if price > poc {
				bias = "bullish"
			} else if price < poc {
				bias = "bearish"
			}
		}
	}

	structure := map[string]any{
		"poc":  poc,
		"vah":  vah,
		"val":  val,
		"bias": bias,
	}
	if vah > 0 && val > 0 {
		structure["valueAreaWidth"] = round2(vah - val)
	}
	if price > 0 {
		structure["price"] = round2(price)
		if position != "" {
			structure["pricePosition"] = position
		}
		if poc > 0 {
			structure["distToPOCPct"] = round2(pctDist(price, poc))
		}
		if vah > 0 {
			structure["distToVAHPct"] = round2(pctDist(price, vah))
		}
		if val > 0 {
			structure["distToVALPct"] = round2(pctDist(price, val))
		}
	}

	opps := []skill.Opportunity{}
	addOpp := func(setup string, level float64, confidence string, score float64, rationale string) {
		if level == 0 {
			return
		}
		dist := 0.0
		if price > 0 {
			dist = round2(pctDist(price, level))
			rationale += fmt.Sprintf(" (%.2f%% from price)", dist)
		}
		opps = append(opps, skill.Opportunity{
			Rank:              len(opps) + 1,
			Setup:             setup,
			Direction:         "neutral",
			Confidence:        confidence,
			ConfluenceScore:   round2(score),
			DistanceFromPrice: dist,
			Rationale:         rationale,
		})
	}
	addOpp("vp_poc", poc, "MED", 0.6, fmt.Sprintf("POC %.2f — highest-volume level, acts as a magnet", poc))
	addOpp("vp_vah", vah, "MED", 0.5, fmt.Sprintf("VAH %.2f — value-area high, resistance above", vah))
	addOpp("vp_val", val, "MED", 0.5, fmt.Sprintf("VAL %.2f — value-area low, support below", val))

	agenticScore := 0.7
	if status == "partial" {
		agenticScore = 0.5
	}

	return skill.SkillResult{
		Status:        status,
		Workflow:      "volume-profile",
		Market:        skill.MarketData{Bias: bias},
		Structure:     structure,
		Opportunities: opps,
		Narrative: skill.Narrative{
			MarketStructure: fmt.Sprintf("POC %.2f | VAH %.2f | VAL %.2f | Value Area %.2f wide | bias %s", poc, vah, val, vah-val, bias),
			Warnings:        warnings,
		},
		Validation:  skill.Validation{Passed: len(warnings) == 0, Warnings: warnings},
		Conformance: skill.Conformance{HasValidData: true, AgenticScore: agenticScore},
	}
}

// vpLevelsFromGraphic reads POC/VAH/VAL from the graphic layer. Labels whose
// text starts with "POC"/"VAH"/"VAL" carry their price in the y coordinate;
// if absent, fall back to lines (solid = POC, dashed pair = VAH above VAL).
// All coordinate reads are sentinel-guarded (1e+100 is Pine na).
func vpLevelsFromGraphic(graphic map[string]map[string]any) (poc, vah, val float64) {
	if labels, ok := graphic["dwglabels"]; ok {
		for _, v := range labels {
			m, ok := v.(map[string]any)
			if !ok {
				continue
			}
			text, _ := m["t"].(string)
			y := getValidFloat(m, "y")
			switch {
			case strings.HasPrefix(text, "POC"):
				poc = y
			case strings.HasPrefix(text, "VAH"):
				vah = y
			case strings.HasPrefix(text, "VAL"):
				val = y
			}
		}
		if poc != 0 || vah != 0 || val != 0 {
			return poc, vah, val
		}
	}

	lines, ok := graphic["dwglines"]
	if !ok {
		return 0, 0, 0
	}
	var dashed []float64
	for _, v := range lines {
		m, ok := v.(map[string]any)
		if !ok {
			continue
		}
		y := getValidFloat(m, "y1")
		st, _ := m["st"].(string)
		if st == "sol" {
			poc = y
		} else if st == "dsh" {
			dashed = append(dashed, y)
		}
	}
	if len(dashed) >= 2 {
		vah = max(dashed[0], dashed[1])
		val = min(dashed[0], dashed[1])
	}
	return poc, vah, val
}

func formatVPPro(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  VOLUME PROFILE (FIXED + ANCHORED)\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  POC: %.2f  |  VAH: %.2f  |  VAL: %.2f\n",
		toFloat(result.Structure["poc"]), toFloat(result.Structure["vah"]), toFloat(result.Structure["val"])))
	sb.WriteString(fmt.Sprintf("  Value Area width: %.2f  |  Price: %.2f  |  Bias: %s\n",
		toFloat(result.Structure["valueAreaWidth"]), result.Market.LastPrice, result.Market.Bias))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("  -> %s [%s] %.2f: %s\n", o.Setup, o.Confidence, o.ConfluenceScore, o.Rationale))
	}
	for _, w := range result.Narrative.Warnings {
		sb.WriteString(fmt.Sprintf("  ! %s\n", w))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(VPProSkill) }
