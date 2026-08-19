package parsers

import (
	"strings"
	"testing"
)

// TestCustV2_ParseSynthetic verifies the ScalpQuant v2 parser produces a valid
// SkillResult from a populated period and that the structured trade levels
// (Entry/StopLoss/TP1..TP3/RiskReward) populate when a scalp signal fires.
// This guards the v2-only Opportunity fields added to skill.Opportunity.
func TestCustV2_ParseSynthetic(t *testing.T) {
	period := map[string]any{
		"price": 2000.0, "open": 1990.0, "high": 2005.0, "low": 1985.0, "vol": 1000.0,
		"biasWeighted": 0.45, "biasStrength": 0.6, "biasM15": 1, "biasM30": 1,
		"biasH1": 1, "biasH4": 1, "biasD1": 1, "mtfAlignment": 1,
		"emaTrend": 1, "atrStopLong": 1980.0, "atrStopShort": 2020.0,
		"emaATRTrail": 1985.0, "stTrend": 1, "stTrend2": 1, "stAgree": 1,
		"swingTrail": 1975.0, "swTrend": 1, "fib1": 1990.0, "fib2": 1980.0,
		"fib3": 1970.0, "atr": 5.0, "atr14": 6.0, "trendDuration": 10.0,
		"emaFast": 1995.0, "emaSlow": 1985.0, "ema200": 1900.0,
		"emaTrendCross": 1, "emaSpread": 0.5, "ema200dist": 5.0,
		"dynSL_long": 1980.0, "dynSL_short": 2020.0,
		"dynTP1_long": 2022.0, "dynTP2_long": 2066.0, "dynTP3_long": 2110.0,
		"dynTP1_short": 1978.0, "dynTP2_short": 1934.0, "dynTP3_short": 1890.0,
		"tp1L": 2022.0, "tp2L": 2066.0, "tp3L": 2110.0,
		"tp1S": 1978.0, "tp2S": 1934.0, "tp3S": 1890.0,
		"buyVol": 600.0, "sellVol": 400.0, "buyPressure": 0.6,
		"deltaVol": 200.0, "deltaMa": 150.0,
		"poc": 1995.0, "vah": 2010.0, "val": 1980.0, "vpWidth": 1.5,
		"volSpike": 1, "volRatio": 2.5, "obvTrend": 1,
		"support": 1980.0, "resistance": 2020.0, "support2": 1970.0, "resistance2": 2030.0,
		"srBreakUp": 0, "srBreakDn": 0,
		"rsi": 55.0, "rsiZone": 0, "bullDiv": 0, "bearDiv": 0, "divScore": 0,
		"stochK": 70.0, "stochD": 65.0, "stochCross": 1,
		"macdLine": 0.5, "macdSignal": 0.3, "macdHist": 0.2,
		"confidence": 75.0, "tqi": 0.7, "dviTrend": 1, "roc": 1.2, "er": 0.6,
		"liqSweep": 1, "sweepDepth": 1.5,
		"ichiConv": 1995.0, "ichiBase": 1985.0, "ichiSpanA": 1990.0, "ichiSpanB": 1980.0,
		"ichiCloudTop": 1990.0, "ichiCloudBottom": 1980.0, "ichiCloudWidth": 10.0,
		"ichiSignal": 1, "tkCross": 1, "chikouBull": 1,
		"obBullHigh": 1990.0, "obBullLow": 1980.0, "obBearHigh": 2020.0, "obBearLow": 2010.0,
		"obSignal": 1, "obBullAge": 5.0, "obBearAge": 0,
		"fvgBullTop": 1995.0, "fvgBullBottom": 1985.0, "fvgBearTop": 2005.0, "fvgBearBottom": 2015.0,
		"fvgSignal": 1,
		"regime": 1, "regimeLabel": 1, "atrPercentile": 50.0, "squeeze": 0,
		"sessionCode": 3, "isOverlap": 1,
		"candleScore": 1, "hammer": 0, "shootStar": 0, "bullEngulf": 1, "bearEngulf": 0, "doji": 0,
		"keltUp": 2010.0, "keltDn": 1990.0, "keltPosition": 0.5, "keltWidth": 1.0,
		"vwap": 1998.0, "vwapDist": 0.1, "vwapSignal": 1,
		"entryTiming": 80.0,
		"scalpScore": 45.0, "scalpSmooth": 40.0, "scalpSignal": 1, "scalpStrong": 1,
		"barsSinceBuy": 3, "barsSinceSell": 20, "trendAgree": 1,
		"rrLong": 2.0, "rrShort": 1.8,
	}

	res := CustSkill.ParseOutput(
		[]map[string]any{period},
		nil, "5m", "OANDA:XAUUSD", map[string]string{},
	)

	if res.Status != "ok" {
		t.Fatalf("expected ok, got %q: %+v", res.Status, res)
	}
	if res.Market.Bias != "bullish" {
		t.Fatalf("expected bullish bias, got %q", res.Market.Bias)
	}
	if len(res.Opportunities) == 0 {
		t.Fatalf("expected at least one opportunity for a firing scalp signal")
	}

	primary := res.Opportunities[0]
	if primary.Direction != "long" {
		t.Fatalf("expected long primary, got %q", primary.Direction)
	}
	if primary.Entry != 2000.0 {
		t.Fatalf("expected Entry=2000, got %v", primary.Entry)
	}
	if primary.StopLoss != 1980.0 {
		t.Fatalf("expected StopLoss=1980, got %v", primary.StopLoss)
	}
	if primary.TP1 != 2022.0 || primary.TP2 != 2066.0 || primary.TP3 != 2110.0 {
		t.Fatalf("expected TP1/2/3 = 2022/2066/2110, got %v/%v/%v",
			primary.TP1, primary.TP2, primary.TP3)
	}
	if primary.RiskReward != 2.0 {
		t.Fatalf("expected RiskReward=2.0, got %v", primary.RiskReward)
	}

	// Formatter must not panic and must mention the structured entry line.
	txt := CustSkill.FormatText(res)
	if !strings.Contains(txt, "Entry:") {
		t.Fatalf("formatter output missing 'Entry:' line:\n%s", txt)
	}

	// Verify the structured output map exposes the new v2 fields.
	if v, ok := res.Structure["scalpSmooth"].(float64); !ok || v != 40.0 {
		t.Fatalf("expected structure.scalpSmooth=40.0, got %v (%T)", res.Structure["scalpSmooth"], res.Structure["scalpSmooth"])
	}
	if v, ok := res.Structure["regimeName"].(string); !ok || v != "trending" {
		t.Fatalf("expected regimeName=trending, got %v", res.Structure["regimeName"])
	}
}
