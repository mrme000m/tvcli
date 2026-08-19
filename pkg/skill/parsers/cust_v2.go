package parsers

import (
	"fmt"
	"math"
	"strings"

	"github.com/mrme000m/tvcli/pkg/skill"
	"github.com/mrme000m/tvcli/pkg/schema"
)

// CustSkill is the consolidated quantitative scalping indicator (ScalpQuant v2).
var CustSkill = &skill.Skill{
	Name:     "cust",
	Synopsis: "ScalpQuant v2 — extended consolidated quantitative scalping indicator",
	PineID:   "USER;e2451c36dec24ebeb10ae3f7c0dd37ac",
	Inputs: []skill.InputDef{
		// Bias engine
		{Name: "fastLength", TVInputID: "in_0", Type: "int", Default: 10},
		{Name: "slowLength", TVInputID: "in_1", Type: "int", Default: 20},
		{Name: "rsiLength", TVInputID: "in_2", Type: "int", Default: 14},
		{Name: "macdFastLength", TVInputID: "in_3", Type: "int", Default: 12},
		{Name: "macdSlowLength", TVInputID: "in_4", Type: "int", Default: 26},
		{Name: "macdSignalLength", TVInputID: "in_5", Type: "int", Default: 9},
		{Name: "bbLength", TVInputID: "in_6", Type: "int", Default: 20},
		{Name: "bbMultiplier", TVInputID: "in_7", Type: "float", Default: 2.0},
		{Name: "dmiLength", TVInputID: "in_8", Type: "int", Default: 14},
		{Name: "dmiSmoothing", TVInputID: "in_9", Type: "int", Default: 14},
		{Name: "sarStartValue", TVInputID: "in_10", Type: "float", Default: 0.02},
		{Name: "sarIncrement", TVInputID: "in_11", Type: "float", Default: 0.02},
		{Name: "sarMaxValue", TVInputID: "in_12", Type: "float", Default: 0.2},
		// Trend / stop
		{Name: "ema2Len", TVInputID: "in_13", Type: "int", Default: 20},
		{Name: "ema3Len", TVInputID: "in_14", Type: "int", Default: 50},
		{Name: "atrLen", TVInputID: "in_15", Type: "int", Default: 7},
		{Name: "atrMult", TVInputID: "in_16", Type: "float", Default: 1.4},
		{Name: "atrP1", TVInputID: "in_17", Type: "int", Default: 10},
		{Name: "mult1", TVInputID: "in_18", Type: "float", Default: 1.0},
		{Name: "atrP2", TVInputID: "in_19", Type: "int", Default: 5},
		{Name: "mult2", TVInputID: "in_20", Type: "float", Default: 0.5},
		{Name: "swATRLen", TVInputID: "in_21", Type: "int", Default: 28},
		{Name: "swATRFactor", TVInputID: "in_22", Type: "float", Default: 5.0},
		// EMA ribbon
		{Name: "emaFastLen", TVInputID: "in_23", Type: "int", Default: 14},
		{Name: "emaSlowLen", TVInputID: "in_24", Type: "int", Default: 21},
		{Name: "ema200Len", TVInputID: "in_25", Type: "int", Default: 200},
		{Name: "tp1Pct", TVInputID: "in_26", Type: "float", Default: 1.1},
		{Name: "tp2Pct", TVInputID: "in_27", Type: "float", Default: 3.3},
		{Name: "tp3Pct", TVInputID: "in_28", Type: "float", Default: 5.5},
		// Volume
		{Name: "volMaLen", TVInputID: "in_29", Type: "int", Default: 10},
		{Name: "vpLookback", TVInputID: "in_30", Type: "int", Default: 200},
		{Name: "vpBins", TVInputID: "in_31", Type: "int", Default: 50},
		{Name: "volMaLenOF", TVInputID: "in_32", Type: "int", Default: 20},
		{Name: "volMultOF", TVInputID: "in_33", Type: "float", Default: 3.0},
		{Name: "coinMaLen", TVInputID: "in_34", Type: "int", Default: 5},
		// Levels
		{Name: "leftB", TVInputID: "in_35", Type: "int", Default: 15},
		{Name: "rightB", TVInputID: "in_36", Type: "int", Default: 15},
		// Quality
		{Name: "hmaLength", TVInputID: "in_37", Type: "int", Default: 50},
		{Name: "stAtr", TVInputID: "in_38", Type: "int", Default: 10},
		{Name: "stFactor", TVInputID: "in_39", Type: "float", Default: 3.0},
		{Name: "kernelPeriod", TVInputID: "in_40", Type: "int", Default: 30},
		{Name: "dviVolLen", TVInputID: "in_41", Type: "int", Default: 14},
		{Name: "dviMomLen", TVInputID: "in_42", Type: "int", Default: 14},
		{Name: "erLen", TVInputID: "in_43", Type: "int", Default: 20},
		// Liquidity
		{Name: "sweepLookback", TVInputID: "in_44", Type: "int", Default: 20},
		{Name: "sweepMult", TVInputID: "in_45", Type: "float", Default: 1.5},
		// Ichimoku
		{Name: "ichiConvLen", TVInputID: "in_46", Type: "int", Default: 9},
		{Name: "ichiBaseLen", TVInputID: "in_47", Type: "int", Default: 26},
		{Name: "ichiSpanLen", TVInputID: "in_48", Type: "int", Default: 52},
		// Order Blocks
		{Name: "obLookback", TVInputID: "in_49", Type: "int", Default: 20},
		{Name: "obStrength", TVInputID: "in_50", Type: "int", Default: 3},
		// FVG
		{Name: "fvgMinATR", TVInputID: "in_51", Type: "float", Default: 0.5},
		// Regime
		{Name: "regimeLen", TVInputID: "in_52", Type: "int", Default: 50},
		{Name: "regimeATRLen", TVInputID: "in_53", Type: "int", Default: 14},
		// Keltner
		{Name: "keltLen", TVInputID: "in_54", Type: "int", Default: 20},
		{Name: "keltMult", TVInputID: "in_55", Type: "float", Default: 1.5},
		// Session
		{Name: "asiaStart", TVInputID: "in_56", Type: "int", Default: 0},
		{Name: "asiaEnd", TVInputID: "in_57", Type: "int", Default: 8},
		{Name: "londonStart", TVInputID: "in_58", Type: "int", Default: 7},
		{Name: "londonEnd", TVInputID: "in_59", Type: "int", Default: 16},
		{Name: "nyStart", TVInputID: "in_60", Type: "int", Default: 13},
		{Name: "nyEnd", TVInputID: "in_61", Type: "int", Default: 21},
		// Candle patterns
		{Name: "candleLookback", TVInputID: "in_62", Type: "int", Default: 3},
		// Composite
		{Name: "decayBars", TVInputID: "in_63", Type: "int", Default: 10},
		{Name: "compWeightBias", TVInputID: "in_64", Type: "float", Default: 25.0},
		{Name: "compWeightTrend", TVInputID: "in_65", Type: "float", Default: 10.0},
		{Name: "compWeightST", TVInputID: "in_66", Type: "float", Default: 10.0},
		{Name: "compWeightConf", TVInputID: "in_67", Type: "float", Default: 15.0},
		{Name: "compWeightTQI", TVInputID: "in_68", Type: "float", Default: 10.0},
		{Name: "compWeightDiv", TVInputID: "in_69", Type: "float", Default: 5.0},
		{Name: "compWeightLiq", TVInputID: "in_70", Type: "float", Default: 5.0},
		{Name: "compWeightVol", TVInputID: "in_71", Type: "float", Default: 5.0},
		{Name: "compWeightOB", TVInputID: "in_72", Type: "float", Default: 5.0},
		{Name: "compWeightFVG", TVInputID: "in_73", Type: "float", Default: 5.0},
		{Name: "compWeightIchi", TVInputID: "in_74", Type: "float", Default: 5.0},
	},
	Presets: map[string]map[string]any{
		"default": {},
		"scalping": {
			"fastLength": 10, "slowLength": 20, "atrLen": 7, "atrMult": 1.4,
			"emaFastLen": 14, "emaSlowLen": 21, "vpLookback": 200, "vpBins": 50,
			"sweepLookback": 20, "sweepMult": 1.5, "decayBars": 5,
		},
		"swing": {
			"fastLength": 20, "slowLength": 50, "atrLen": 14, "atrMult": 2.0,
			"emaFastLen": 21, "emaSlowLen": 50, "vpLookback": 400, "vpBins": 80,
			"sweepLookback": 40, "sweepMult": 2.0, "decayBars": 20,
			"compWeightBias": 30.0, "compWeightTrend": 15.0, "compWeightIchi": 10.0,
		},
		"aggressive": {
			"decayBars": 3, "atrMult": 1.0, "mult1": 0.7, "mult2": 0.3,
			"compWeightLiq": 10.0, "compWeightOB": 10.0, "compWeightFVG": 10.0,
		},
	},
	Category:        "scalp",
	RequiresGraphic: false,
	ParseOutput:     parseCustOutput,
	ParseWithSchema: parseCust,
	FormatText:      formatCust,
}

// parseCustOutput adapts the schema-aware parser to the ParseOutput signature.
func parseCustOutput(periods []map[string]any, graphic map[string]map[string]any, tf string, symbol string, args map[string]string) skill.SkillResult {
	return parseCust(periods, graphic, nil, tf, symbol, args)
}

// f reads a named plot value from the period, falling back to plot_N index.
func f(period map[string]any, title string, idx int) float64 {
	names := []string{title}
	if idx >= 0 {
		names = append(names, fmt.Sprintf("plot_%d", idx))
	}
	return toFloat(getField(period, names))
}

// sessionName maps the numeric session code to a human-readable label.
func sessionName(code float64) string {
	switch int(code) {
	case 1:
		return "Asia"
	case 2:
		return "London"
	case 3:
		return "New York"
	case 4:
		return "London/NY Overlap"
	default:
		return "Off-hours"
	}
}

// regimeName maps the numeric regime label to a human-readable label.
func regimeName(code float64) string {
	switch {
	case code >= 2:
		return "strong_trend"
	case code >= 1:
		return "trending"
	case code >= 0:
		return "ranging"
	default:
		return "choppy"
	}
}

// confidenceBucket returns a human-readable confidence label from 0..1 quality.
func confidenceBucket(q float64) string {
	switch {
	case q >= 0.8:
		return "very_high"
	case q >= 0.6:
		return "high"
	case q >= 0.4:
		return "moderate"
	case q >= 0.2:
		return "low"
	default:
		return "very_low"
	}
}

func parseCust(periods []map[string]any, graphic map[string]map[string]any, sch *schema.PineSchema, tf string, symbol string, args map[string]string) skill.SkillResult {
	_ = sch
	_ = graphic
	if len(periods) == 0 {
		return skill.SkillResult{Status: "no_data", Workflow: "scalpquant_v2",
			Narrative: skill.Narrative{MarketStructure: "No data"}}
	}
	last := latestClosed(periods)

	// ── Core price ──
	price := f(last, "price", -1)
	open := f(last, "open", -1)
	high := f(last, "high", -1)
	low := f(last, "low", -1)
	vol := f(last, "vol", -1)

	// ── Bias engine ──
	biasW := f(last, "biasWeighted", -1)
	biasS := f(last, "biasStrength", -1)
	biasM15 := f(last, "biasM15", -1)
	biasM30 := f(last, "biasM30", -1)
	biasH1 := f(last, "biasH1", -1)
	biasH4 := f(last, "biasH4", -1)
	biasD1 := f(last, "biasD1", -1)
	mtfAlign := f(last, "mtfAlignment", -1)

	// ── Trend / stop ──
	emaTrend := f(last, "emaTrend", -1)
	atrStopLong := f(last, "atrStopLong", -1)
	atrStopShort := f(last, "atrStopShort", -1)
	emaATRTrail := f(last, "emaATRTrail", -1)
	stTrend := f(last, "stTrend", -1)
	stTrend2 := f(last, "stTrend2", -1)
	stAgree := f(last, "stAgree", -1)
	swTrail := f(last, "swingTrail", -1)
	swTrend := f(last, "swTrend", -1)
	fib1 := f(last, "fib1", -1)
	fib2 := f(last, "fib2", -1)
	fib3 := f(last, "fib3", -1)
	atrVal := f(last, "atr", -1)
	atr14 := f(last, "atr14", -1)
	trendDur := f(last, "trendDuration", -1)

	// ── EMA ribbon ──
	emaFast := f(last, "emaFast", -1)
	emaSlow := f(last, "emaSlow", -1)
	ema200 := f(last, "ema200", -1)
	emaTrendCross := f(last, "emaTrendCross", -1)
	emaSpread := f(last, "emaSpread", -1)
	ema200dist := f(last, "ema200dist", -1)

	// Dynamic TP/SL
	dynSLLong := f(last, "dynSL_long", -1)
	dynSLShort := f(last, "dynSL_short", -1)
	dynTP1Long := f(last, "dynTP1_long", -1)
	dynTP2Long := f(last, "dynTP2_long", -1)
	dynTP3Long := f(last, "dynTP3_long", -1)
	dynTP1Short := f(last, "dynTP1_short", -1)
	dynTP2Short := f(last, "dynTP2_short", -1)
	dynTP3Short := f(last, "dynTP3_short", -1)

	// Static TP
	tp1L := f(last, "tp1L", -1)
	tp2L := f(last, "tp2L", -1)
	tp3L := f(last, "tp3L", -1)
	tp1S := f(last, "tp1S", -1)
	tp2S := f(last, "tp2S", -1)
	tp3S := f(last, "tp3S", -1)

	// ── Volume ──
	buyVol := f(last, "buyVol", -1)
	sellVol := f(last, "sellVol", -1)
	buyPressure := f(last, "buyPressure", -1)
	deltaVol := f(last, "deltaVol", -1)
	deltaMa := f(last, "deltaMa", -1)
	poc := f(last, "poc", -1)
	vah := f(last, "vah", -1)
	valV := f(last, "val", -1)
	vpWidth := f(last, "vpWidth", -1)
	volSpike := f(last, "volSpike", -1)
	volRatio := f(last, "volRatio", -1)
	obvTrend := f(last, "obvTrend", -1)

	// ── Levels ──
	support := f(last, "support", -1)
	resistance := f(last, "resistance", -1)
	support2 := f(last, "support2", -1)
	resistance2 := f(last, "resistance2", -1)
	srBreakUp := f(last, "srBreakUp", -1)
	srBreakDn := f(last, "srBreakDn", -1)

	// ── Oscillator / quality ──
	rsi := f(last, "rsi", -1)
	rsiZone := f(last, "rsiZone", -1)
	bullDiv := f(last, "bullDiv", -1)
	bearDiv := f(last, "bearDiv", -1)
	divScore := f(last, "divScore", -1)
	stochK := f(last, "stochK", -1)
	stochD := f(last, "stochD", -1)
	stochCross := f(last, "stochCross", -1)
	macdLine := f(last, "macdLine", -1)
	macdSignal := f(last, "macdSignal", -1)
	macdHist := f(last, "macdHist", -1)
	confidence := f(last, "confidence", -1)
	tqi := f(last, "tqi", -1)
	dviTrend := f(last, "dviTrend", -1)
	roc := f(last, "roc", -1)
	er := f(last, "er", -1)

	// ── Liquidity ──
	liqSweep := f(last, "liqSweep", -1)
	sweepDepth := f(last, "sweepDepth", -1)

	// ── Ichimoku ──
	ichiConv := f(last, "ichiConv", -1)
	ichiBase := f(last, "ichiBase", -1)
	ichiSpanA := f(last, "ichiSpanA", -1)
	ichiSpanB := f(last, "ichiSpanB", -1)
	ichiCloudTop := f(last, "ichiCloudTop", -1)
	ichiCloudBottom := f(last, "ichiCloudBottom", -1)
	ichiCloudWidth := f(last, "ichiCloudWidth", -1)
	ichiSignal := f(last, "ichiSignal", -1)
	tkCross := f(last, "tkCross", -1)
	chikouBull := f(last, "chikouBull", -1)

	// ── Order Blocks ──
	obBullHigh := f(last, "obBullHigh", -1)
	obBullLow := f(last, "obBullLow", -1)
	obBearHigh := f(last, "obBearHigh", -1)
	obBearLow := f(last, "obBearLow", -1)
	obSignal := f(last, "obSignal", -1)
	obBullAge := f(last, "obBullAge", -1)
	obBearAge := f(last, "obBearAge", -1)

	// ── FVG ──
	fvgBullTop := f(last, "fvgBullTop", -1)
	fvgBullBottom := f(last, "fvgBullBottom", -1)
	fvgBearTop := f(last, "fvgBearTop", -1)
	fvgBearBottom := f(last, "fvgBearBottom", -1)
	fvgSignal := f(last, "fvgSignal", -1)

	// ── Regime ──
	regime := f(last, "regime", -1)
	regimeLabel := f(last, "regimeLabel", -1)
	atrPercentile := f(last, "atrPercentile", -1)
	squeeze := f(last, "squeeze", -1)

	// ── Session ──
	sessionCode := f(last, "sessionCode", -1)
	isOverlap := f(last, "isOverlap", -1)

	// ── Candle patterns ──
	candleScore := f(last, "candleScore", -1)
	hammer := f(last, "hammer", -1)
	shootStar := f(last, "shootStar", -1)
	bullEngulf := f(last, "bullEngulf", -1)
	bearEngulf := f(last, "bearEngulf", -1)
	doji := f(last, "doji", -1)

	// ── Keltner ──
	keltUp := f(last, "keltUp", -1)
	keltDn := f(last, "keltDn", -1)
	keltPosition := f(last, "keltPosition", -1)
	keltWidth := f(last, "keltWidth", -1)

	// ── VWAP ──
	vwap := f(last, "vwap", -1)
	vwapDist := f(last, "vwapDist", -1)
	vwapSignal := f(last, "vwapSignal", -1)

	// ── Entry timing ──
	entryTiming := f(last, "entryTiming", -1)

	// ── Composite ──
	scalpScore := f(last, "scalpScore", -1)
	scalpSmooth := f(last, "scalpSmooth", -1)
	scalpSignal := f(last, "scalpSignal", -1)
	scalpStrong := f(last, "scalpStrong", -1)
	barsSinceBuy := f(last, "barsSinceBuy", -1)
	barsSinceSell := f(last, "barsSinceSell", -1)
	trendAgree := f(last, "trendAgree", -1)
	rrLong := f(last, "rrLong", -1)
	rrShort := f(last, "rrShort", -1)

	if price == 0 {
		price = emaFast
	}

	// ── Determine bias ──
	bias := "neutral"
	if scalpSignal > 0 {
		bias = "bullish"
	} else if scalpSignal < 0 {
		bias = "bearish"
	} else if biasW > 0.15 {
		bias = "bullish"
	} else if biasW < -0.15 {
		bias = "bearish"
	}

	// ── Distance computations ──
	distPOC := pctDist(price, poc)
	distRes := 0.0
	if resistance > 0 && price > 0 {
		distRes = (resistance - price) / price * 100
	}
	distSup := 0.0
	if support > 0 && price > 0 {
		distSup = (price - support) / price * 100
	}
	distVWAP := vwapDist

	// ── Quality score (0..1) ──
	q := 0.0
	q += math.Max(0, math.Min(1, confidence/100)) * 0.30
	q += math.Max(0, math.Min(1, tqi)) * 0.20
	q += math.Max(0, math.Min(1, math.Abs(scalpSmooth)/100)) * 0.15
	q += math.Max(0, math.Min(1, entryTiming/100)) * 0.15
	q += math.Max(0, math.Min(1, er)) * 0.10
	// Regime bonus
	if regime == 1 {
		q += 0.05
	}
	// MTF alignment bonus
	if mtfAlign != 0 {
		q += 0.05
	}
	q = math.Max(0, math.Min(1, q))

	// ── Opportunities ──
	var opps []skill.Opportunity
	rank := 0

	// Primary scalp signal
	if scalpSignal != 0 || math.Abs(scalpSmooth) >= 20 {
		rank++
		dir := "long"
		if scalpSignal < 0 || (scalpSignal == 0 && biasW < 0) {
			dir = "short"
		}

		var sl, tp1, tp2, tp3 float64
		if dir == "long" {
			sl = dynSLLong
			tp1 = dynTP1Long
			tp2 = dynTP2Long
			tp3 = dynTP3Long
		} else {
			sl = dynSLShort
			tp1 = dynTP1Short
			tp2 = dynTP2Short
			tp3 = dynTP3Short
		}

		rr := rrLong
		if dir == "short" {
			rr = rrShort
		}

		rationale := fmt.Sprintf(
			"smooth=%.1f raw=%.1f signal=%.0f strong=%.0f biasW=%.2f "+
				"conf=%.0f tqi=%.2f regime=%s session=%s trendAgree=%.0f",
			scalpSmooth, scalpScore, scalpSignal, scalpStrong, biasW,
			confidence, tqi, regimeName(regimeLabel), sessionName(sessionCode), trendAgree)

		if liqSweep != 0 {
			rationale += fmt.Sprintf(" liqSweep=%.0f(depth=%.2f)", liqSweep, sweepDepth)
		}
		if divScore != 0 {
			rationale += fmt.Sprintf(" div=%.0f", divScore)
		}
		if obSignal != 0 {
			rationale += fmt.Sprintf(" ob=%.0f", obSignal)
		}
		if fvgSignal != 0 {
			rationale += fmt.Sprintf(" fvg=%.0f", fvgSignal)
		}
		if squeeze > 0 {
			rationale += " SQUEEZE"
		}
		if candleScore != 0 {
			rationale += fmt.Sprintf(" candle=%.0f", candleScore)
		}
		if emaTrendCross != 0 {
			rationale += fmt.Sprintf(" emaCross=%.0f", emaTrendCross)
		}

		opps = append(opps, skill.Opportunity{
			Rank:              rank,
			Setup:             "scalp_confluence",
			Direction:         dir,
			Confidence:        confidenceBucket(q),
			ConfluenceScore:   round2(q),
			DistanceFromPrice: round2(distPOC),
			Rationale:         rationale,
			Entry:             round2(price),
			StopLoss:          round2(sl),
			TP1:               round2(tp1),
			TP2:               round2(tp2),
			TP3:               round2(tp3),
			RiskReward:        round2(rr),
		})
	}

	// Order block retest opportunity
	if obSignal != 0 && math.Abs(scalpSmooth) >= 10 {
		rank++
		dir := "long"
		if obSignal < 0 {
			dir = "short"
		}
		var entry, sl float64
		if dir == "long" {
			entry = obBullLow
			sl = obBullLow - atr14*1.0
		} else {
			entry = obBearHigh
			sl = obBearHigh + atr14*1.0
		}
		opps = append(opps, skill.Opportunity{
			Rank:              rank,
			Setup:             "order_block_retest",
			Direction:         dir,
			Confidence:        confidenceBucket(q * 0.8),
			ConfluenceScore:   round2(q * 0.8),
			DistanceFromPrice: round2(pctDist(price, entry)),
			Rationale: fmt.Sprintf(
				"OB %s zone [%.2f-%.2f] age_bull=%v age_bear=%v",
				dir, obBullLow, obBullHigh, obBullAge, obBearAge),
			Entry:    round2(entry),
			StopLoss: round2(sl),
		})
	}

	// FVG fill opportunity
	if fvgSignal != 0 && math.Abs(scalpSmooth) >= 10 {
		rank++
		dir := "long"
		fvgMid := (fvgBullTop + fvgBullBottom) / 2
		if fvgSignal < 0 {
			dir = "short"
			fvgMid = (fvgBearTop + fvgBearBottom) / 2
		}
		opps = append(opps, skill.Opportunity{
			Rank:              rank,
			Setup:             "fvg_fill",
			Direction:         dir,
			Confidence:        confidenceBucket(q * 0.7),
			ConfluenceScore:   round2(q * 0.7),
			DistanceFromPrice: round2(pctDist(price, fvgMid)),
			Rationale: fmt.Sprintf(
				"FVG %s [bull: %.2f-%.2f | bear: %.2f-%.2f]",
				dir, fvgBullBottom, fvgBullTop, fvgBearBottom, fvgBearTop),
		})
	}

	// Liquidity sweep reversal
	if liqSweep != 0 {
		rank++
		dir := "long"
		if liqSweep < 0 {
			dir = "short"
		}
		opps = append(opps, skill.Opportunity{
			Rank:              rank,
			Setup:             "liquidity_sweep",
			Direction:         dir,
			Confidence:        confidenceBucket(q * 0.75),
			ConfluenceScore:   round2(q * 0.75),
			DistanceFromPrice: 0,
			Rationale: fmt.Sprintf(
				"Sweep %s depth=%.2fATR volRatio=%.1f",
				dir, sweepDepth, volRatio),
		})
	}

	// Squeeze breakout watch
	if squeeze > 0 {
		rank++
		dir := "long"
		if biasW < 0 {
			dir = "short"
		}
		opps = append(opps, skill.Opportunity{
			Rank:              rank,
			Setup:             "squeeze_breakout",
			Direction:         dir,
			Confidence:        "watch",
			ConfluenceScore:   round2(q * 0.5),
			DistanceFromPrice: 0,
			Rationale: fmt.Sprintf(
				"BB inside Keltner, expansion imminent. keltWidth=%.2f bias=%s",
				keltWidth, bias),
		})
	}

	// ── Warnings ──
	var warns []string
	if math.Abs(ema200dist) > 5 {
		warns = append(warns, fmt.Sprintf("Price %.1f%% from EMA200 — mean-reversion risk", ema200dist))
	}
	if tqi < 0.3 {
		warns = append(warns, "Low trend quality (TQI < 0.3): choppy regime")
	}
	if regime == -1 {
		warns = append(warns, "High-volatility regime — wider stops recommended")
	}
	if atrPercentile > 90 {
		warns = append(warns, fmt.Sprintf("ATR at %.0f percentile — extreme volatility", atrPercentile))
	}
	if rsiZone == 2 {
		warns = append(warns, "RSI overbought (>70)")
	}
	if rsiZone == -2 {
		warns = append(warns, "RSI oversold (<30)")
	}
	if sessionCode == 0 {
		warns = append(warns, "Off-session hours — lower liquidity expected")
	}
	if mtfAlign == 0 && math.Abs(scalpSmooth) > 30 {
		warns = append(warns, "Signal present but no MTF alignment — higher risk")
	}
	if trendDur < 3 && emaTrend != 0 {
		warns = append(warns, "Fresh trend flip (<3 bars) — wait for confirmation")
	}

	// ── Build structured output ──
	structOut := map[string]any{
		// Core
		"price": round2(price), "open": round2(open), "high": round2(high),
		"low": round2(low), "volume": round2(vol),
		// Bias
		"biasWeighted": round2(biasW), "biasStrength": round2(biasS),
		"biasM15": round2(biasM15), "biasM30": round2(biasM30),
		"biasH1": round2(biasH1), "biasH4": round2(biasH4), "biasD1": round2(biasD1),
		"mtfAlignment": round2(mtfAlign),
		// Trend
		"emaTrend": round2(emaTrend), "stTrend": round2(stTrend),
		"stTrend2": round2(stTrend2), "stAgree": round2(stAgree),
		"swTrend": round2(swTrend), "trendDuration": round2(trendDur),
		"atrStopLong": round2(atrStopLong), "atrStopShort": round2(atrStopShort),
		"emaATRTrail": round2(emaATRTrail), "swingTrail": round2(swTrail),
		"fib1": round2(fib1), "fib2": round2(fib2), "fib3": round2(fib3),
		"atr": round2(atrVal), "atr14": round2(atr14),
		// EMA
		"emaFast": round2(emaFast), "emaSlow": round2(emaSlow), "ema200": round2(ema200),
		"emaTrendCross": round2(emaTrendCross), "emaSpread": round2(emaSpread),
		"ema200dist": round2(ema200dist),
		// TP/SL
		"dynSL_long": round2(dynSLLong), "dynSL_short": round2(dynSLShort),
		"dynTP1_long": round2(dynTP1Long), "dynTP2_long": round2(dynTP2Long), "dynTP3_long": round2(dynTP3Long),
		"dynTP1_short": round2(dynTP1Short), "dynTP2_short": round2(dynTP2Short), "dynTP3_short": round2(dynTP3Short),
		"tp1L": round2(tp1L), "tp2L": round2(tp2L), "tp3L": round2(tp3L),
		"tp1S": round2(tp1S), "tp2S": round2(tp2S), "tp3S": round2(tp3S),
		"rrLong": round2(rrLong), "rrShort": round2(rrShort),
		// Volume
		"buyVol": round2(buyVol), "sellVol": round2(sellVol),
		"buyPressure": round2(buyPressure), "deltaVol": round2(deltaVol), "deltaMa": round2(deltaMa),
		"poc": round2(poc), "vah": round2(vah), "val": round2(valV), "vpWidth": round2(vpWidth),
		"volSpike": round2(volSpike), "volRatio": round2(volRatio), "obvTrend": round2(obvTrend),
		// Levels
		"support": round2(support), "resistance": round2(resistance),
		"support2": round2(support2), "resistance2": round2(resistance2),
		"srBreakUp": round2(srBreakUp), "srBreakDn": round2(srBreakDn),
		// Oscillator
		"rsi": round2(rsi), "rsiZone": round2(rsiZone),
		"bullDiv": round2(bullDiv), "bearDiv": round2(bearDiv), "divScore": round2(divScore),
		"stochK": round2(stochK), "stochD": round2(stochD), "stochCross": round2(stochCross),
		"macdLine": round2(macdLine), "macdSignal": round2(macdSignal), "macdHist": round2(macdHist),
		"confidence": round2(confidence), "tqi": round2(tqi),
		"dviTrend": round2(dviTrend), "roc": round2(roc), "er": round2(er),
		// Liquidity
		"liqSweep": round2(liqSweep), "sweepDepth": round2(sweepDepth),
		// Ichimoku
		"ichiConv": round2(ichiConv), "ichiBase": round2(ichiBase),
		"ichiSpanA": round2(ichiSpanA), "ichiSpanB": round2(ichiSpanB),
		"ichiCloudTop": round2(ichiCloudTop), "ichiCloudBottom": round2(ichiCloudBottom),
		"ichiCloudWidth": round2(ichiCloudWidth), "ichiSignal": round2(ichiSignal),
		"tkCross": round2(tkCross), "chikouBull": round2(chikouBull),
		// OB
		"obBullHigh": round2(obBullHigh), "obBullLow": round2(obBullLow),
		"obBearHigh": round2(obBearHigh), "obBearLow": round2(obBearLow),
		"obSignal": round2(obSignal), "obBullAge": round2(obBullAge), "obBearAge": round2(obBearAge),
		// FVG
		"fvgBullTop": round2(fvgBullTop), "fvgBullBottom": round2(fvgBullBottom),
		"fvgBearTop": round2(fvgBearTop), "fvgBearBottom": round2(fvgBearBottom),
		"fvgSignal": round2(fvgSignal),
		// Regime
		"regime": round2(regime), "regimeLabel": round2(regimeLabel),
		"atrPercentile": round2(atrPercentile), "squeeze": round2(squeeze),
		"regimeName": regimeName(regimeLabel),
		// Session
		"sessionCode": round2(sessionCode), "isOverlap": round2(isOverlap),
		"sessionName": sessionName(sessionCode),
		// Candle patterns
		"candleScore": round2(candleScore), "hammer": round2(hammer),
		"shootStar": round2(shootStar), "bullEngulf": round2(bullEngulf),
		"bearEngulf": round2(bearEngulf), "doji": round2(doji),
		// Keltner
		"keltUp": round2(keltUp), "keltDn": round2(keltDn),
		"keltPosition": round2(keltPosition), "keltWidth": round2(keltWidth),
		// VWAP
		"vwap": round2(vwap), "vwapDist": round2(vwapDist), "vwapSignal": round2(vwapSignal),
		// Entry timing
		"entryTiming": round2(entryTiming),
		// Composite
		"scalpScore": round2(scalpScore), "scalpSmooth": round2(scalpSmooth),
		"scalpSignal": round2(scalpSignal), "scalpStrong": round2(scalpStrong),
		"barsSinceBuy": round2(barsSinceBuy), "barsSinceSell": round2(barsSinceSell),
		"trendAgree": round2(trendAgree),
		// Distance
		"distPOC": round2(distPOC), "distRes": round2(distRes),
		"distSup": round2(distSup), "distVWAP": round2(distVWAP),
	}

	return skill.SkillResult{
		Status:        "ok",
		Workflow:      "scalpquant_v2",
		Market:        skill.MarketData{LastPrice: round2(price), Bias: bias},
		Structure:     structOut,
		Opportunities: opps,
		Narrative: skill.Narrative{
			MarketStructure: fmt.Sprintf(
				"Bias:%s Score:%.1f(smooth:%.1f) Signal:%.0f Strong:%.0f | "+
					"MTF W:%.2f Str:%g Align:%.0f | Conf:%.0f TQI:%.2f RSI:%.0f | "+
					"Regime:%s Session:%s | Entry:%.0f Agree:%.0f R:R=%.1f/%.1f",
				bias, scalpScore, scalpSmooth, scalpSignal, scalpStrong,
				biasW, biasS, mtfAlign, confidence, tqi, rsi,
				regimeName(regimeLabel), sessionName(sessionCode),
				entryTiming, trendAgree, rrLong, rrShort),
			PrimaryOpp: primaryOppFromOpps(opps),
			Warnings:   warns,
		},
		Validation:  skill.Validation{Passed: true},
		Conformance: skill.Conformance{HasValidData: true, AgenticScore: round2(q)},
	}
}

// pctDist computes percent distance of price from a level.
func pctDist(price, level float64) float64 {
	if level == 0 || price == 0 {
		return 0
	}
	return (price - level) / level * 100
}

func formatCust(result skill.SkillResult) string {
	var sb strings.Builder
	s := result.Structure
	sb.WriteString("\n╔══════════════════════════════════════════════════════════════════════╗\n")
	sb.WriteString("║  SCALPQUANT v2 — EXTENDED CONSOLIDATED SCALP INDICATOR              ║\n")
	sb.WriteString("╠══════════════════════════════════════════════════════════════════════╣\n\n")

	// Price & Bias
	sb.WriteString(fmt.Sprintf("  %-14s %v      Bias: %s\n", "Price:", result.Market.LastPrice, result.Market.Bias))
	sb.WriteString(fmt.Sprintf("  %-14s %v      Session: %v\n", "OHLC:", fmt.Sprintf("O:%v H:%v L:%v", s["open"], s["high"], s["low"]), s["sessionName"]))
	sb.WriteString("\n")

	// Composite
	sb.WriteString("  ┌─ COMPOSITE ─────────────────────────────────────────────────────┐\n")
	sb.WriteString(fmt.Sprintf("  │ Score: %-8v Smooth: %-8v Signal: %-4v Strong: %-4v    │\n",
		s["scalpScore"], s["scalpSmooth"], s["scalpSignal"], s["scalpStrong"]))
	sb.WriteString(fmt.Sprintf("  │ Entry Timing: %-6v  Trend Agreement: %-4v                │\n",
		s["entryTiming"], s["trendAgree"]))
	sb.WriteString(fmt.Sprintf("  │ R:R Long: %-8v  R:R Short: %-8v                      │\n",
		s["rrLong"], s["rrShort"]))
	sb.WriteString("  └─────────────────────────────────────────────────────────────────┘\n\n")

	// MTF Bias
	sb.WriteString("  ┌─ MTF BIAS ──────────────────────────────────────────────────────┐\n")
	sb.WriteString(fmt.Sprintf("  │ Weighted: %-8v Strength: %-4v Alignment: %-4v              │\n",
		s["biasWeighted"], s["biasStrength"], s["mtfAlignment"]))
	sb.WriteString(fmt.Sprintf("  │ M15:%-3v  M30:%-3v  H1:%-3v  H4:%-3v  D1:%-3v                     │\n",
		s["biasM15"], s["biasM30"], s["biasH1"], s["biasH4"], s["biasD1"]))
	sb.WriteString("  └─────────────────────────────────────────────────────────────────┘\n\n")

	// Trend
	sb.WriteString("  ┌─ TREND ─────────────────────────────────────────────────────────┐\n")
	sb.WriteString(fmt.Sprintf("  │ EMA Trend: %-4v ST1: %-4v ST2: %-4v Agree: %-4v Swing: %-4v   │\n",
		s["emaTrend"], s["stTrend"], s["stTrend2"], s["stAgree"], s["swTrend"]))
	sb.WriteString(fmt.Sprintf("  │ Duration: %-4v bars   Regime: %-14v Squeeze: %-4v    │\n",
		s["trendDuration"], s["regimeName"], s["squeeze"]))
	sb.WriteString(fmt.Sprintf("  │ ATR: %-8v  ATR14: %-8v  ATR%%ile: %-6v              │\n",
		s["atr"], s["atr14"], s["atrPercentile"]))
	sb.WriteString("  └─────────────────────────────────────────────────────────────────┘\n\n")

	// Stops & Targets
	sb.WriteString("  ┌─ STOPS & TARGETS ───────────────────────────────────────────────┐\n")
	sb.WriteString(fmt.Sprintf("  │ ATR Stop L/S: %v / %v\n", s["atrStopLong"], s["atrStopShort"]))
	sb.WriteString(fmt.Sprintf("  │ Trail: %v   Swing Trail: %v\n", s["emaATRTrail"], s["swingTrail"]))
	sb.WriteString(fmt.Sprintf("  │ Fib: %.2v / %.2v / %.2v\n", s["fib1"], s["fib2"], s["fib3"]))
	sb.WriteString(fmt.Sprintf("  │ Dyn SL L/S: %v / %v\n", s["dynSL_long"], s["dynSL_short"]))
	sb.WriteString(fmt.Sprintf("  │ Dyn TP Long:  %v / %v / %v\n", s["dynTP1_long"], s["dynTP2_long"], s["dynTP3_long"]))
	sb.WriteString(fmt.Sprintf("  │ Dyn TP Short: %v / %v / %v\n", s["dynTP1_short"], s["dynTP2_short"], s["dynTP3_short"]))
	sb.WriteString("  └─────────────────────────────────────────────────────────────────┘\n\n")

	// Volume & Profile
	sb.WriteString("  ┌─ VOLUME PROFILE ────────────────────────────────────────────────┐\n")
	sb.WriteString(fmt.Sprintf("  │ POC: %-10v VAH: %-10v VAL: %-10v Width: %v%%    │\n",
		s["poc"], s["vah"], s["val"], s["vpWidth"]))
	sb.WriteString(fmt.Sprintf("  │ BuyPressure: %-4v Delta: %-8v DeltaMA: %-8v         │\n",
		s["buyPressure"], s["deltaVol"], s["deltaMa"]))
	sb.WriteString(fmt.Sprintf("  │ VolSpike: %-4v VolRatio: %-6v OBV Trend: %-4v           │\n",
		s["volSpike"], s["volRatio"], s["obvTrend"]))
	sb.WriteString(fmt.Sprintf("  │ VWAP: %-10v Dist: %v%%   Signal: %-4v              │\n",
		s["vwap"], s["vwapDist"], s["vwapSignal"]))
	sb.WriteString("  └─────────────────────────────────────────────────────────────────┘\n\n")

	// Levels
	sb.WriteString("  ┌─ LEVELS ────────────────────────────────────────────────────────┐\n")
	sb.WriteString(fmt.Sprintf("  │ Support:    %-10v (%v%%  from price)                     │\n",
		s["support"], s["distSup"]))
	sb.WriteString(fmt.Sprintf("  │ Resistance: %-10v (%v%%  from price)                     │\n",
		s["resistance"], s["distRes"]))
	sb.WriteString(fmt.Sprintf("  │ Support2:   %-10v Resistance2: %-10v              │\n",
		s["support2"], s["resistance2"]))
	sb.WriteString(fmt.Sprintf("  │ SR Break Up: %-4v  Down: %-4v  distPOC: %v%%              │\n",
		s["srBreakUp"], s["srBreakDn"], s["distPOC"]))
	sb.WriteString("  └─────────────────────────────────────────────────────────────────┘\n\n")

	// Oscillators
	sb.WriteString("  ┌─ OSCILLATORS ───────────────────────────────────────────────────┐\n")
	sb.WriteString(fmt.Sprintf("  │ RSI: %-6v Zone: %-4v  Stoch K/D: %v/%v Cross: %-4v     │\n",
		s["rsi"], s["rsiZone"], s["stochK"], s["stochD"], s["stochCross"]))
	sb.WriteString(fmt.Sprintf("  │ MACD: %-8v Sig: %-8v Hist: %-8v                 │\n",
		s["macdLine"], s["macdSignal"], s["macdHist"]))
	sb.WriteString(fmt.Sprintf("  │ Confidence: %-4v TQI: %-6v ER: %-6v DVI: %-4v ROC: %-6v│\n",
		s["confidence"], s["tqi"], s["er"], s["dviTrend"], s["roc"]))
	sb.WriteString(fmt.Sprintf("  │ Divergence: Score=%-4v Bull=%-4v Bear=%-4v                │\n",
		s["divScore"], s["bullDiv"], s["bearDiv"]))
	sb.WriteString("  └─────────────────────────────────────────────────────────────────┘\n\n")

	// Ichimoku
	sb.WriteString("  ┌─ ICHIMOKU ──────────────────────────────────────────────────────┐\n")
	sb.WriteString(fmt.Sprintf("  │ Conv: %-10v Base: %-10v Signal: %-4v              │\n",
		s["ichiConv"], s["ichiBase"], s["ichiSignal"]))
	sb.WriteString(fmt.Sprintf("  │ Cloud: %-10v - %-10v Width: %v%%               │\n",
		s["ichiCloudBottom"], s["ichiCloudTop"], s["ichiCloudWidth"]))
	sb.WriteString(fmt.Sprintf("  │ TK Cross: %-4v  Chikou: %-4v                               │\n",
		s["tkCross"], s["chikouBull"]))
	sb.WriteString("  └─────────────────────────────────────────────────────────────────┘\n\n")

	// Structure (OB + FVG)
	sb.WriteString("  ┌─ STRUCTURE (OB / FVG) ──────────────────────────────────────────┐\n")
	sb.WriteString(fmt.Sprintf("  │ OB Signal: %-4v  Bull[%-8v-%-8v] age=%v              │\n",
		s["obSignal"], s["obBullLow"], s["obBullHigh"], s["obBullAge"]))
	sb.WriteString(fmt.Sprintf("  │                  Bear[%-8v-%-8v] age=%v              │\n",
		s["obBearLow"], s["obBearHigh"], s["obBearAge"]))
	sb.WriteString(fmt.Sprintf("  │ FVG Signal: %-4v Bull[%-8v-%-8v]                     │\n",
		s["fvgSignal"], s["fvgBullBottom"], s["fvgBullTop"]))
	sb.WriteString(fmt.Sprintf("  │                  Bear[%-8v-%-8v]                     │\n",
		s["fvgBearBottom"], s["fvgBearTop"]))
	sb.WriteString("  └─────────────────────────────────────────────────────────────────┘\n\n")

	// Candle & Liquidity
	sb.WriteString("  ┌─ CANDLES & LIQUIDITY ───────────────────────────────────────────┐\n")
	sb.WriteString(fmt.Sprintf("  │ Candle Score: %-4v  Hammer: %-4v ShootStar: %-4v            │\n",
		s["candleScore"], s["hammer"], s["shootStar"]))
	sb.WriteString(fmt.Sprintf("  │ BullEngulf: %-4v BearEngulf: %-4v Doji: %-4v               │\n",
		s["bullEngulf"], s["bearEngulf"], s["doji"]))
	sb.WriteString(fmt.Sprintf("  │ LiqSweep: %-4v  Depth: %-6v                               │\n",
		s["liqSweep"], s["sweepDepth"]))
	sb.WriteString(fmt.Sprintf("  │ Keltner: %-4v [%-8v - %-8v] Width: %v%%              │\n",
		s["keltPosition"], s["keltDn"], s["keltUp"], s["keltWidth"]))
	sb.WriteString("  └─────────────────────────────────────────────────────────────────┘\n\n")

	// Opportunities
	if len(result.Opportunities) > 0 {
		sb.WriteString("  ┌─ OPPORTUNITIES ─────────────────────────────────────────────────┐\n")
		for _, o := range result.Opportunities {
			sb.WriteString(fmt.Sprintf("  │ #%d %s %s [%s] conf=%.2f dist=%.2f%%\n",
				o.Rank, strings.ToUpper(o.Direction), o.Setup, o.Confidence, o.ConfluenceScore, o.DistanceFromPrice))
			if o.Entry > 0 {
				sb.WriteString(fmt.Sprintf("  │    Entry: %.2f  SL: %.2f", o.Entry, o.StopLoss))
				if o.TP1 > 0 {
					sb.WriteString(fmt.Sprintf("  TP1: %.2f  TP2: %.2f  TP3: %.2f", o.TP1, o.TP2, o.TP3))
				}
				if o.RiskReward > 0 {
					sb.WriteString(fmt.Sprintf("  R:R=%.1f", o.RiskReward))
				}
				sb.WriteString("\n")
			}
			sb.WriteString(fmt.Sprintf("  │    %s\n", o.Rationale))
		}
		sb.WriteString("  └─────────────────────────────────────────────────────────────────┘\n\n")
	}

	// Warnings
	if len(result.Narrative.Warnings) > 0 {
		sb.WriteString("  ┌─ WARNINGS ──────────────────────────────────────────────────────┐\n")
		for _, w := range result.Narrative.Warnings {
			sb.WriteString(fmt.Sprintf("  │ ⚠ %s\n", w))
		}
		sb.WriteString("  └─────────────────────────────────────────────────────────────────┘\n\n")
	}

	sb.WriteString(fmt.Sprintf("  AgenticScore: %.2f    Bars Since Buy/Sell: %v / %v\n",
		result.Conformance.AgenticScore, s["barsSinceBuy"], s["barsSinceSell"]))
	sb.WriteString("╚══════════════════════════════════════════════════════════════════════╝\n")
	return sb.String()
}

// REMOVED: requires private script access
// func init() { skill.Register(CustSkill) }
