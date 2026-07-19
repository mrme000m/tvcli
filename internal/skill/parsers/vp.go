package parsers

import (
	"fmt"
	"math"
	"sort"
	"strings"

	"github.com/ch99q/tvcli/internal/skill"
)

// VPSkill wraps TradingView's "Volume Profile / Fixed Range" public indicator.
//
// The indicator does not emit numeric periods[]; instead it draws a histogram of
// volume-at-price as dwgboxes and marks the POC with a label/line. We compute
// POC, VAH, VAL and HVN/LVN directly from the box widths (x2-x1 == relative
// volume) and price levels (midpoint of y1-y2).
//
// Usage examples:
//   ./tvcli vp --symbol BTCUSDT --tf 1W --bars 52
//   ./tvcli vp --symbol BTCUSDT --tf 1h  --bars 48 --length 48 --value-area 70
var VPSkill = &skill.Skill{
	Name:     "vp",
	Synopsis: "Volume Profile Fixed Range — POC, VAH, VAL, HVN/LVN levels",
	PineID:   "PUB;aea729456b7a44e09661b70ce9e4e987",
	Inputs: []skill.InputDef{
		{Name: "rows", TVInputID: "in_0", Type: "int", Default: 150},
		{Name: "length", TVInputID: "in_1", Type: "int", Default: 24},
		{Name: "valueArea", TVInputID: "in_2", Type: "float", Default: 70},
		{Name: "showPoc", TVInputID: "in_9", Type: "bool", Default: true},
	},
	Presets: map[string]map[string]any{
		"weekly":    {"rows": 150, "length": 52, "valueArea": 70},
		"daily":     {"rows": 150, "length": 30, "valueArea": 70},
		"intraday":  {"rows": 100, "length": 24, "valueArea": 70},
		"scalping":  {"rows": 100, "length": 12, "valueArea": 70},
	},
	ParseOutput: parseVP,
	FormatText:  formatVP,
}

// vpNode aggregates volume-at-price data extracted from a box graphic.
type vpNode struct {
	price  float64
	volume float64
}

func parseVP(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	boxes, ok := graphic["dwgboxes"]
	if !ok || len(boxes) == 0 {
		return skill.SkillResult{
			Status:   "no_data",
			Workflow: "volume-profile",
			Narrative: skill.Narrative{
				MarketStructure: "No volume profile histogram received",
				Warnings:        []string{"Indicator produced no graphic histogram (try a larger --bars/--length)"},
			},
		}
	}

	// Aggregate volume per price level. Box width (x2-x1) encodes volume;
	// y1/y2 encode the price band.
	volByPrice := map[float64]float64{}
	var totalVol float64
	for _, raw := range boxes {
		obj, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		y1 := toFloat(obj["y1"])
		y2 := toFloat(obj["y2"])
		x1 := toFloat(obj["x1"])
		x2 := toFloat(obj["x2"])
		if y1 == 0 && y2 == 0 {
			continue
		}
		price := round2((y1 + y2) / 2)
		vol := math.Abs(x2 - x1)
		if vol <= 0 {
			continue
		}
		volByPrice[price] += vol
		totalVol += vol
	}

	if len(volByPrice) == 0 || totalVol == 0 {
		return skill.SkillResult{
			Status:   "no_data",
			Workflow: "volume-profile",
			Narrative: skill.Narrative{
				MarketStructure: "Could not decode volume profile histogram",
				Warnings:        []string{"Graphic boxes had no usable volume/price data"},
			},
		}
	}

	nodes := make([]vpNode, 0, len(volByPrice))
	for p, v := range volByPrice {
		nodes = append(nodes, vpNode{price: p, volume: v})
	}

	// POC = highest-volume price.
	sort.Slice(nodes, func(i, j int) bool { return nodes[i].volume > nodes[j].volume })
	poc := nodes[0]

	// Value Area: expand outward from POC until cumulative volume >= valueArea%.
	valueAreaPct := 0.7
	if vaStr, ok := args["valueArea"]; ok {
		fmt.Sscanf(vaStr, "%f", &valueAreaPct)
		valueAreaPct /= 100
	}
	if valueAreaPct <= 0 || valueAreaPct > 1 {
		valueAreaPct = 0.7
	}

	byPrice := make([]vpNode, len(nodes))
	copy(byPrice, nodes)
	sort.Slice(byPrice, func(i, j int) bool { return byPrice[i].price < byPrice[j].price })

	pocIdx := 0
	for i, n := range byPrice {
		if math.Abs(n.price-poc.price) < 1e-9 {
			pocIdx = i
			break
		}
	}

	selected := map[int]bool{pocIdx: true}
	cumulative := byPrice[pocIdx].volume
	lo, hi := pocIdx, pocIdx
	target := totalVol * valueAreaPct
	for cumulative < target && (lo > 0 || hi < len(byPrice)-1) {
		var candidates []int
		if lo > 0 {
			candidates = append(candidates, lo-1)
		}
		if hi < len(byPrice)-1 {
			candidates = append(candidates, hi+1)
		}
		if len(candidates) == 0 {
			break
		}
		// Add the adjacent level with larger volume first (greedy expansion).
		best := candidates[0]
		for _, idx := range candidates[1:] {
			if byPrice[idx].volume > byPrice[best].volume {
				best = idx
			}
		}
		selected[best] = true
		cumulative += byPrice[best].volume
		if best < lo {
			lo = best
		} else if best > hi {
			hi = best
		}
	}

	// byPrice is sorted low->high, so lo index = lowest price, hi index = highest price.
	val := byPrice[lo].price
	vah := byPrice[hi].price

	// HVN = top 30% volume nodes; LVN = bottom 30% volume nodes.
	sort.Slice(byPrice, func(i, j int) bool { return byPrice[i].volume < byPrice[j].volume })
	cutLow := int(math.Ceil(float64(len(byPrice)) * 0.3))
	cutHigh := int(math.Floor(float64(len(byPrice)) * 0.7))
	var lvn []float64
	for i := 0; i < cutLow && i < len(byPrice); i++ {
		lvn = append(lvn, byPrice[i].price)
	}
	var hvn []float64
	for i := cutHigh; i < len(byPrice); i++ {
		hvn = append(hvn, byPrice[i].price)
	}
	sort.Float64s(lvn)
	sort.Float64s(hvn)

	// Current price, if available from chart periods provided alongside the indicator.
	last := latestClosed(periods)
	price := toFloat(getField(last, []string{"Close", "close"}))
	if price == 0 && len(periods) > 0 {
		price = toFloat(getField(periods[0], []string{"Close", "close"}))
	}

	// Bias relative to value area.
	bias := "neutral"
	if price > 0 {
		if price < val {
			bias = "bearish-oversold"
		} else if price > vah {
			bias = "bullish-overbought"
		} else if price < poc.price {
			bias = "bearish"
		} else if price > poc.price {
			bias = "bullish"
		}
	}

	// Opportunities based on mean-reversion to POC/VAH/VAL.
	var opps []skill.Opportunity
	score := 0.55
	if len(hvn) > 0 && len(lvn) > 0 {
		score += 0.1
	}
	if price > 0 {
		score += 0.1
	}
	score = math.Min(score, 0.95)

	if price > 0 {
		if price < val {
			opps = append(opps, skill.Opportunity{
				Rank:            1,
				Setup:           "vp_mean_reversion_long",
				Direction:       "long",
				Confidence:      confidenceLabel(score),
				ConfluenceScore: round2(score),
				Rationale:       fmt.Sprintf("price %.2f below VAL %.2f — target POC %.2f then VAH %.2f", price, val, poc.price, vah),
			})
		} else if price > vah {
			opps = append(opps, skill.Opportunity{
				Rank:            1,
				Setup:           "vp_mean_reversion_short",
				Direction:       "short",
				Confidence:      confidenceLabel(score),
				ConfluenceScore: round2(score),
				Rationale:       fmt.Sprintf("price %.2f above VAH %.2f — target POC %.2f then VAL %.2f", price, vah, poc.price, val),
			})
		}
	}
	// Always surface the key structural levels.
	opps = append(opps, skill.Opportunity{
		Rank:            2,
		Setup:           "vp_levels",
		Direction:       bias,
		Confidence:      confidenceLabel(score - 0.1),
		ConfluenceScore: round2(score - 0.1),
		Rationale:       fmt.Sprintf("POC=%.2f VAH=%.2f VAL=%.2f HVN=%d LVN=%d", poc.price, vah, val, len(hvn), len(lvn)),
	})

	narrative := skill.Narrative{
		MarketStructure: fmt.Sprintf("Volume Profile over %d price levels | POC: %.2f | VAH: %.2f | VAL: %.2f", len(volByPrice), poc.price, vah, val),
		PrimaryOpp:      primaryOppFromOpps(opps),
	}

	agenticScore := 0.35
	if poc.volume > 0 {
		agenticScore += 0.25
	}
	if price > 0 && (price < val || price > vah) {
		agenticScore += 0.25
	}
	if len(hvn) > 0 && len(lvn) > 0 {
		agenticScore += 0.15
	}
	agenticScore = math.Min(agenticScore, 0.99)

	return skill.SkillResult{
		Status:   "ok",
		Workflow: "volume-profile",
		Market: skill.MarketData{
			LastPrice: price,
			Bias:      bias,
		},
		Structure: map[string]any{
			"poc":       poc.price,
			"pocVolume": round2(poc.volume),
			"vah":       vah,
			"val":       val,
			"valueArea": round2(valueAreaPct * 100),
			"hvn":       hvn,
			"lvn":       lvn,
			"totalVol":  round2(totalVol),
			"levels":    len(volByPrice),
			"bias":      bias,
		},
		Opportunities: opps,
		Narrative:     narrative,
		Validation:    skill.Validation{Passed: true},
		Conformance:   skill.Conformance{HasValidData: true, AgenticScore: round2(agenticScore)},
	}
}

func formatVP(result skill.SkillResult) string {
	var sb strings.Builder
	sb.WriteString("\n======================================================================\n")
	sb.WriteString("  VOLUME PROFILE (Fixed Range)\n")
	sb.WriteString("======================================================================\n\n")
	sb.WriteString(fmt.Sprintf("  POC: %.2f  |  VAH: %.2f  |  VAL: %.2f\n",
		result.Structure["poc"], result.Structure["vah"], result.Structure["val"]))
	sb.WriteString(fmt.Sprintf("  Price: %.2f  |  Bias: %s\n", result.Market.LastPrice, result.Market.Bias))
	sb.WriteString(fmt.Sprintf("  HVN levels: %v\n", result.Structure["hvn"]))
	sb.WriteString(fmt.Sprintf("  LVN levels: %v\n", result.Structure["lvn"]))
	for _, o := range result.Opportunities {
		sb.WriteString(fmt.Sprintf("  -> %s %s [%s] %.2f: %s\n", o.Direction, o.Setup, o.Confidence, o.ConfluenceScore, o.Rationale))
	}
	sb.WriteString(fmt.Sprintf("\n  Score: %.2f\n", result.Conformance.AgenticScore))
	sb.WriteString("======================================================================\n")
	return sb.String()
}

func init() { skill.Register(VPSkill) }
