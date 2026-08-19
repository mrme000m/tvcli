package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
	"github.com/mrme000m/tvcli/pkg/schema"
)

var BSVSkill = &skill.Skill{
	Name:     "bsv",
	Synopsis: "Buy/Sell Volume analysis with MA crossovers",
	PineID:   "PUB;28a4da159ce246dab2cb6524c25f950f",
	Inputs: []skill.InputDef{
		{Name: "lengthMA1", TVInputID: "in_0", Type: "int", Default: 10},
		{Name: "lengthMA2", TVInputID: "in_1", Type: "int", Default: 10},
		{Name: "maType", TVInputID: "in_2", Type: "string", Default: "SMA"},
	},
	Presets: map[string]map[string]any{
		"scalping": {"lengthMA1": 9, "lengthMA2": 21, "maType": "EMA"},
		"default":  {"lengthMA1": 10, "lengthMA2": 10, "maType": "SMA"},
		"swing":    {"lengthMA1": 50, "lengthMA2": 200, "maType": "SMA"},
	},
	ParseOutput:     parseBSV,
	ParseWithSchema: parseBSVSchema,
	FormatText:      formatBSV,
}

func parseBSV(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	return parseBSVSchema(periods, graphic, nil, tf, symbol, args)
}

func parseBSVSchema(periods []map[string]any, graphic map[string]map[string]any, sch *schema.PineSchema, tf string, symbol string, args map[string]string) skill.SkillResult {
	if len(periods) == 0 {
		return skill.SkillResult{
			Status: "no_data", Workflow: "buying-selling-volume",
			Narrative: skill.Narrative{MarketStructure: "No data", Warnings: []string{"No period data"}},
		}
	}

	buyDominant, sellDominant, neutral, crossCount := 0, 0, 0, 0
	var dominanceRatio float64

	type barData struct {
		bgColor float64
		dom     float64
	}

	// periods is newest-first. periods[0] is the in-progress bar (partial
	// volume). Iterate historicalBars (periods[1:]) so dominance and crosses
	// are computed over closed bars only.
	hist := historicalBars(periods)
	bars := make([]barData, 0, len(hist))
	for i, p := range hist {
		buyRaw := math.Abs(SchemaFloat(p, sch, "BuyVolume", "buyVolume"))
		sellRaw := math.Abs(SchemaFloat(p, sch, "SellVolume", "sellVolume"))
		bgColor := SchemaFloat(p, sch, "BackgroundColor", "backgroundColor", "Background Color")
		totalVol := buyRaw + sellRaw
		dom := 0.0
		if totalVol > 0 {
			dom = (buyRaw - sellRaw) / totalVol
		}
		bars = append(bars, barData{bgColor: bgColor, dom: dom})

		// Cross detection: hist is newest-first, so hist[i+1] is the OLDER
		// bar (previous in time). A "previous" background transition is
		// current vs older.
		if i+1 < len(hist) {
			prevBg := SchemaFloat(hist[i+1], sch, "BackgroundColor", "backgroundColor")
			if bgColor == 4 && prevBg != 4 {
				crossCount++
			} else if bgColor == 5 && prevBg != 5 {
				crossCount++
			}
		}
	}

	// bars is newest-first (most recent closed bar at bars[0]). Take the
	// first 20 for "recent" dominance, not the last 20.
	lastBars := bars
	if len(bars) > 20 {
		lastBars = bars[:20]
	}

	for _, b := range lastBars {
		if b.dom > 0.1 {
			buyDominant++
		} else if b.dom < -0.1 {
			sellDominant++
		} else {
			neutral++
		}
	}

	if len(lastBars) > 0 {
		dominanceRatio = float64(buyDominant-sellDominant) / float64(len(lastBars))
	}

	bgCounts := map[string]int{}
	for _, b := range lastBars {
		switch int(b.bgColor) {
		case 4:
			bgCounts["bull"]++
		case 5:
			bgCounts["bear"]++
		default:
			bgCounts["neutral"]++
		}
	}
	bgConsensus := "neutral"
	maxCount := 0
	for state, count := range bgCounts {
		if count > maxCount {
			maxCount = count
			bgConsensus = state
		}
	}

	var opps []skill.Opportunity
	if dominanceRatio > 0.3 && bgConsensus == "bull" {
		score := 0.60
		if math.Abs(dominanceRatio) > 0.4 {
			score = 0.85
		}
		opps = append(opps, skill.Opportunity{
			Rank: 1, Setup: "volume_pressure", Direction: "long",
			Confidence: confidenceLabel(score), ConfluenceScore: round2(score),
			Rationale: fmt.Sprintf("Buy pressure dominant (%.2f). BG: %s.", round2(dominanceRatio), bgConsensus),
		})
	} else if dominanceRatio < -0.3 && bgConsensus == "bear" {
		score := 0.60
		if math.Abs(dominanceRatio) > 0.4 {
			score = 0.85
		}
		opps = append(opps, skill.Opportunity{
			Rank: 1, Setup: "volume_pressure", Direction: "short",
			Confidence: confidenceLabel(score), ConfluenceScore: round2(score),
			Rationale: fmt.Sprintf("Sell pressure dominant (%.2f). BG: %s.", round2(dominanceRatio), bgConsensus),
		})
	}

	warnings := []string{}
	if math.Abs(dominanceRatio) < 0.2 {
		warnings = append(warnings, "Low volume dominance.")
	}

	agenticScore := 0.2
	if len(bars) > 0 {
		agenticScore += 0.2
	}
	if math.Abs(dominanceRatio) > 0.2 {
		agenticScore += 0.15
	}
	if crossCount > 0 {
		agenticScore += 0.15
	}
	agenticScore = math.Min(agenticScore, 0.99)

	// Last closed bar (periods[1]) is the most recent finalized bar. We do
	// not read OHLC from periods[0] because BSV does not expose Close.
	lastClosed := latestClosed(periods)
	lastPrice := SchemaField(lastClosed, sch, "Close", "close")

	return skill.SkillResult{
		Status: "ok", Workflow: "buying-selling-volume",
		Market: skill.MarketData{
			LastPrice: lastPrice,
			Bias:      biasFromDominance(dominanceRatio),
		},
		Structure: map[string]any{
			"totalBars": len(bars), "buyDominant": buyDominant, "sellDominant": sellDominant,
			"neutral": neutral, "dominanceRatio": round2(dominanceRatio), "bgConsensus": bgConsensus,
			"recentCrosses": crossCount,
		},
		Opportunities: opps,
		Narrative: skill.Narrative{
			MarketStructure: fmt.Sprintf("Buy:%d Sell:%d Neutral:%d Dom:%.2f BG:%s", buyDominant, sellDominant, neutral, round2(dominanceRatio), bgConsensus),
			PrimaryOpp:      primaryOppFromOpps(opps),
			Warnings:        warnings,
		},
		Validation:  skill.Validation{Passed: true},
		Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func primaryOppFromOpps(opps []skill.Opportunity) string {
	if len(opps) > 0 {
		return opps[0].Rationale
	}
	return "Wait for signal."
}

func formatBSV(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  BUYING SELLING VOLUME\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  Bars:%v Buy:%v Sell:%v Neutral:%v\n", result.Structure["totalBars"], result.Structure["buyDominant"], result.Structure["sellDominant"], result.Structure["neutral"]))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("  -> %s %s [%s] %.2f: %s\n", o.Direction, o.Setup, o.Confidence, o.ConfluenceScore, o.Rationale))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

// REMOVED: low signal quality (score 0.4)
// func init() { skill.Register(BSVSkill) }
