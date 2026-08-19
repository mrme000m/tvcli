package parsers

import (
	"testing"
)

func TestParseMTFGraphic(t *testing.T) {
	graphic := mtfLikeGraphic()

	res := parseMTF(nil, graphic, "5m", "OANDA:XAUUSD", nil)
	if res.Status != "ok" {
		t.Fatalf("status = %q, want ok", res.Status)
	}
	if res.Market.Bias != "bearish" {
		t.Errorf("bias = %q, want bearish (D1/H4 downtrend dominates)", res.Market.Bias)
	}
	tfs, ok := res.Structure["timeframes"].(map[string]any)
	if !ok {
		t.Fatalf("timeframes missing from structure")
	}
	h1, ok := tfs["H1"].(map[string]any)
	if !ok {
		t.Fatalf("H1 timeframe missing")
	}
	if h1["trend"] != "Strong Uptrend" {
		t.Errorf("H1 trend = %v, want Strong Uptrend", h1["trend"])
	}
	if toFloat(h1["strength"]) != 6 {
		t.Errorf("H1 strength = %v, want 6", h1["strength"])
	}
	if !res.Conformance.HasValidData {
		t.Errorf("HasValidData = false, want true")
	}
}

func TestParseMTFNoData(t *testing.T) {
	res := parseMTF(nil, map[string]map[string]any{}, "5m", "OANDA:XAUUSD", nil)
	if res.Status != "no_data" {
		t.Errorf("status = %q, want no_data", res.Status)
	}
}

// mtfLikeGraphic mirrors the live MTF dashboard raw dump shape.
func mtfLikeGraphic() map[string]map[string]any {
	cells := map[string]any{
		"2":  map[string]any{"col": 0, "row": 0, "t": "Timeframe"},
		"3":  map[string]any{"col": 1, "row": 0, "t": "M15"},
		"4":  map[string]any{"col": 2, "row": 0, "t": "M30"},
		"5":  map[string]any{"col": 3, "row": 0, "t": "H1"},
		"6":  map[string]any{"col": 4, "row": 0, "t": "H4"},
		"7":  map[string]any{"col": 5, "row": 0, "t": "D1"},
		"8":  map[string]any{"col": 0, "row": 1, "t": "Trend"},
		"9":  map[string]any{"col": 1, "row": 1, "t": "Weak Uptrend"},
		"10": map[string]any{"col": 2, "row": 1, "t": "Strong Uptrend"},
		"11": map[string]any{"col": 3, "row": 1, "t": "Strong Uptrend"},
		"12": map[string]any{"col": 4, "row": 1, "t": "Strong Downtrend"},
		"13": map[string]any{"col": 5, "row": 1, "t": "Strong Downtrend"},
		"14": map[string]any{"col": 0, "row": 2, "t": "Strength"},
		"15": map[string]any{"col": 1, "row": 2, "t": "2"},
		"16": map[string]any{"col": 2, "row": 2, "t": "6"},
		"17": map[string]any{"col": 3, "row": 2, "t": "6"},
		"18": map[string]any{"col": 4, "row": 2, "t": "-4"},
		"19": map[string]any{"col": 5, "row": 2, "t": "-4"},
	}
	return map[string]map[string]any{
		"dwgtables":     {"1": map[string]any{"cols": 6, "rows": 3, "id": 1}},
		"dwgtablecells": cells,
	}
}
