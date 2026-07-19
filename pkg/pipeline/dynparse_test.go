package pipeline

import (
	"testing"

	"github.com/ch99q/tvcli/pkg/schema"
)

func TestParse_WithSchema(t *testing.T) {
	sch := &schema.PineSchema{
		PineID: "PUB;175",
		Plots: []schema.PlotDecl{
			{Index: 0, Name: "momentum", PlotType: "histogram", IsColorer: false},
			{Index: 1, Name: "zero", PlotType: "cross", IsColorer: false},
		},
		PlotByName: map[string]schema.PlotDecl{
			"momentum": {Index: 0, Name: "momentum", PlotType: "histogram"},
			"zero":     {Index: 1, Name: "zero", PlotType: "cross"},
		},
	}

	periods := []map[string]any{
		{"$time": float64(1000), "plot_0": float64(5.2), "plot_1": float64(0)},
		{"$time": float64(999), "plot_0": float64(-3.1), "plot_1": float64(0)},
		{"$time": float64(998), "plot_0": 1e100, "plot_1": float64(0)}, // NaN sentinel
	}

	result := Parse(periods, sch)

	if len(result.Bars) != 3 {
		t.Fatalf("Bars count = %d, want 3", len(result.Bars))
	}
	if len(result.FieldNames) != 2 {
		t.Fatalf("FieldNames count = %d, want 2", len(result.FieldNames))
	}

	// Check first bar (newest)
	bar := result.Bars[0]
	if bar.Time != 1000 {
		t.Errorf("Bar[0].Time = %v, want 1000", bar.Time)
	}

	// Check field names are semantic
	if result.FieldNames[0] != "momentum" {
		t.Errorf("FieldNames[0] = %q, want momentum", result.FieldNames[0])
	}
	if result.FieldNames[1] != "zero" {
		t.Errorf("FieldNames[1] = %q, want zero", result.FieldNames[1])
	}

	// Check categories
	momentumVal := bar.Values[0]
	if momentumVal.Name != "momentum" {
		t.Errorf("Values[0].Name = %q, want momentum", momentumVal.Name)
	}
	if momentumVal.Category != "histogram" {
		t.Errorf("Values[0].Category = %q, want histogram", momentumVal.Category)
	}
	if momentumVal.Value != 5.2 {
		t.Errorf("Values[0].Value = %v, want 5.2", momentumVal.Value)
	}

	zeroVal := bar.Values[1]
	if zeroVal.Name != "zero" {
		t.Errorf("Values[1].Name = %q, want zero", zeroVal.Name)
	}
	if zeroVal.Category != "reference" {
		t.Errorf("Values[1].Category = %q, want reference", zeroVal.Category)
	}

	// Check NaN sentinel handling
	bar2 := result.Bars[2]
	if !bar2.Values[0].IsNull {
		t.Error("Expected plot_0 with NaN sentinel to be IsNull=true")
	}
}

func TestParse_Fallback_NoSchema(t *testing.T) {
	periods := []map[string]any{
		{"$time": float64(1000), "plot_0": float64(5.2), "plot_1": float64(0)},
		{"$time": float64(999), "plot_0": float64(-3.1), "plot_1": float64(0)},
	}

	result := Parse(periods, nil)

	if len(result.Bars) != 2 {
		t.Fatalf("Bars count = %d, want 2", len(result.Bars))
	}

	// Field names should be positional
	if result.FieldNames[0] != "plot_0" {
		t.Errorf("FieldNames[0] = %q, want plot_0", result.FieldNames[0])
	}

	// Categories should be inferred
	bar := result.Bars[0]
	// 5.2 is in oscillator range (-100 to 100), 0 is also in that range
	cat := bar.Values[0].Category
	if cat != "metric" && cat != "signal" && cat != "oscillator" {
		t.Errorf("Fallback category = %q, expected metric, signal, or oscillator", cat)
	}
}

func TestParseResult_ToNamedMap(t *testing.T) {
	sch := &schema.PineSchema{
		Plots: []schema.PlotDecl{
			{Index: 0, Name: "val", PlotType: "histogram"},
		},
		PlotByName: map[string]schema.PlotDecl{
			"val": {Index: 0, Name: "val", PlotType: "histogram"},
		},
	}

	periods := []map[string]any{
		{"$time": float64(1000), "plot_0": float64(1.0)},
		{"$time": float64(999), "plot_0": float64(2.0)},
		{"$time": float64(998), "plot_0": 1e100}, // NaN
	}

	result := Parse(periods, sch)
	namedMap := result.ToNamedMap()

	vals, ok := namedMap["val"]
	if !ok {
		t.Fatal("val not found in named map")
	}
	// Only 2 non-NaN values
	if len(vals) != 2 {
		t.Errorf("val values count = %d, want 2", len(vals))
	}
}

func TestParseResult_LastValues(t *testing.T) {
	sch := &schema.PineSchema{
		Plots: []schema.PlotDecl{
			{Index: 0, Name: "signal", PlotType: "line"},
		},
		PlotByName: map[string]schema.PlotDecl{
			"signal": {Index: 0, Name: "signal", PlotType: "line"},
		},
	}

	periods := []map[string]any{
		{"$time": float64(1000), "plot_0": float64(1.0)},
		{"$time": float64(999), "plot_0": float64(0.0)},
	}

	result := Parse(periods, sch)
	last := result.LastValues()

	v, ok := last["signal"]
	if !ok {
		t.Fatal("signal not found in last values")
	}
	if v.(float64) != 1.0 {
		t.Errorf("Last signal = %v, want 1.0", v)
	}
}

func TestParseResult_SignalFields(t *testing.T) {
	sch := &schema.PineSchema{
		Plots: []schema.PlotDecl{
			{Index: 0, Name: "momentum", PlotType: "histogram"},
			{Index: 1, Name: "upper", PlotType: "area"},
		},
		PlotByName: map[string]schema.PlotDecl{
			"momentum": {Index: 0, Name: "momentum", PlotType: "histogram"},
			"upper":    {Index: 1, Name: "upper", PlotType: "area"},
		},
	}

	periods := []map[string]any{
		{"$time": float64(1000), "plot_0": float64(5.0), "plot_1": float64(100.0)},
	}

	result := Parse(periods, sch)
	signals := result.SignalFields()

	if len(signals) != 1 {
		t.Fatalf("SignalFields count = %d, want 1", len(signals))
	}
	if signals[0] != "momentum" {
		t.Errorf("SignalFields[0] = %q, want momentum", signals[0])
	}
}

func TestParseResult_PriceFields(t *testing.T) {
	sch := &schema.PineSchema{
		Plots: []schema.PlotDecl{
			{Index: 0, Name: "upper", PlotType: "area"},
			{Index: 1, Name: "lower", PlotType: "area_br"},
			{Index: 2, Name: "signal", PlotType: "histogram"},
		},
		PlotByName: map[string]schema.PlotDecl{
			"upper":  {Index: 0, Name: "upper", PlotType: "area"},
			"lower":  {Index: 1, Name: "lower", PlotType: "area_br"},
			"signal": {Index: 2, Name: "signal", PlotType: "histogram"},
		},
	}

	periods := []map[string]any{
		{"$time": float64(1000), "plot_0": float64(105.0), "plot_1": float64(95.0), "plot_2": float64(1.0)},
	}

	result := Parse(periods, sch)
	prices := result.PriceFields()

	if len(prices) != 2 {
		t.Fatalf("PriceFields count = %d, want 2", len(prices))
	}
	// Order may vary, check both present
	found := make(map[string]bool)
	for _, p := range prices {
		found[p] = true
	}
	if !found["upper"] || !found["lower"] {
		t.Errorf("PriceFields = %v, want [upper, lower]", prices)
	}
}
