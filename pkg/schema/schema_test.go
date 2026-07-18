package schema

import (
	"testing"
)

func TestFromMetaInfo_SqueezeMomentum(t *testing.T) {
	// Simulated metaInfo from /translate/ for Squeeze Momentum Indicator
	metaInfo := map[string]any{
		"pine": map[string]any{
			"version": "2",
		},
		"plots": map[string]any{
			"plot_0": map[string]any{
				"name": "val",
			},
			"plot_1": map[string]any{
				"name": "zero",
			},
		},
		"styles": map[string]any{
			"val": map[string]any{
				"title":        "Momentum",
				"plottype":     "histogram",
				"trackPrice":   false,
				"colorPalette": "palette_0",
			},
			"zero": map[string]any{
				"title":      "Zero Line",
				"plottype":   "cross",
				"trackPrice": false,
			},
		},
		"palettes": map[string]any{
			"palette_0": map[string]any{
				"colors": map[string]any{
					"0": map[string]any{"color": "#00FF00"},
					"1": map[string]any{"color": "#008000"},
					"2": map[string]any{"color": "#FF0000"},
					"3": map[string]any{"color": "#800000"},
				},
				"valToIndex": "val > 0 ? (val > val[1] ? 0 : 1) : (val < val[1] ? 2 : 3)",
			},
		},
		"inputs": []any{
			map[string]any{
				"id":     "length",
				"type":   "integer",
				"defval": float64(20),
				"title":  "BB Length",
			},
			map[string]any{
				"id":     "mult",
				"type":   "float",
				"defval": float64(2.0),
				"title":  "BB MultFactor",
			},
		},
	}

	sch := FromMetaInfo("PUB;175", metaInfo)
	if sch == nil {
		t.Fatal("expected non-nil schema")
	}

	if sch.PineID != "PUB;175" {
		t.Errorf("PineID = %q, want %q", sch.PineID, "PUB;175")
	}
	if sch.Version != "2" {
		t.Errorf("Version = %q, want %q", sch.Version, "2")
	}
	if len(sch.Plots) != 2 {
		t.Errorf("Plots count = %d, want 2", len(sch.Plots))
	}

	// Check plot 0
	p0 := sch.Plots[0]
	if p0.Name != "val" {
		t.Errorf("Plot[0].Name = %q, want %q", p0.Name, "val")
	}
	if p0.PlotType != "histogram" {
		t.Errorf("Plot[0].PlotType = %q, want %q", p0.PlotType, "histogram")
	}
	if !p0.IsColorer {
		t.Error("Plot[0] should be a colorer")
	}
	if p0.Palette != "palette_0" {
		t.Errorf("Plot[0].Palette = %q, want %q", p0.Palette, "palette_0")
	}

	// Check plot 1
	p1 := sch.Plots[1]
	if p1.Name != "zero" {
		t.Errorf("Plot[1].Name = %q, want %q", p1.Name, "zero")
	}
	if p1.PlotType != "cross" {
		t.Errorf("Plot[1].PlotType = %q, want %q", p1.PlotType, "cross")
	}

	// Check palette
	pal, ok := sch.Palettes["palette_0"]
	if !ok {
		t.Fatal("palette_0 not found")
	}
	if len(pal.Colors) != 4 {
		t.Errorf("Palette colors count = %d, want 4", len(pal.Colors))
	}

	// Check inputs
	if len(sch.Inputs) != 2 {
		t.Errorf("Inputs count = %d, want 2", len(sch.Inputs))
	}
	if sch.Inputs[0].Name != "length" {
		t.Errorf("Input[0].Name = %q, want %q", sch.Inputs[0].Name, "length")
	}

	// Check PlotByName lookup
	byName, ok := sch.PlotByName["val"]
	if !ok {
		t.Fatal("PlotByName[val] not found")
	}
	if byName.PlotType != "histogram" {
		t.Errorf("PlotByName[val].PlotType = %q, want histogram", byName.PlotType)
	}
}

func TestFromMetaInfo_NilMetaInfo(t *testing.T) {
	sch := FromMetaInfo("PUB;test", nil)
	if sch != nil {
		t.Error("expected nil schema for nil metaInfo")
	}
}

func TestFromMetaInfo_EmptyPlots(t *testing.T) {
	metaInfo := map[string]any{
		"inputs": []any{},
	}
	sch := FromMetaInfo("PUB;test", metaInfo)
	if sch != nil {
		t.Error("expected nil schema when no plots or styles")
	}
}

func TestParsePlotIndex(t *testing.T) {
	tests := []struct {
		key  string
		want int
	}{
		{"plot_0", 0},
		{"plot_1", 1},
		{"plot_12", 12},
		{"0", 0},
		{"5", 5},
		{"plot_99", 99},
	}
	for _, tt := range tests {
		got := parsePlotIndex(tt.key)
		if got != tt.want {
			t.Errorf("parsePlotIndex(%q) = %d, want %d", tt.key, got, tt.want)
		}
	}
}

func TestPlotTypeCategory(t *testing.T) {
	tests := []struct {
		pt   string
		want string
	}{
		{"histogram", "histogram"},
		{"columns", "histogram"},
		{"cross", "reference"},
		{"line", "line"},
		{"area", "band"},
		{"area_br", "band"},
		{"circles", "marker"},
		{"unknown", "line"},
	}
	for _, tt := range tests {
		got := PlotTypeCategory(tt.pt)
		if got != tt.want {
			t.Errorf("PlotTypeCategory(%q) = %q, want %q", tt.pt, got, tt.want)
		}
	}
}

func TestIsSignalPlot(t *testing.T) {
	if !IsSignalPlot("cross") {
		t.Error("cross should be signal plot")
	}
	if !IsSignalPlot("circles") {
		t.Error("circles should be signal plot")
	}
	if IsSignalPlot("histogram") {
		t.Error("histogram should not be signal plot")
	}
	if IsSignalPlot("line") {
		t.Error("line should not be signal plot")
	}
}

func TestIsBandPlot(t *testing.T) {
	if !IsBandPlot("area") {
		t.Error("area should be band plot")
	}
	if !IsBandPlot("area_br") {
		t.Error("area_br should be band plot")
	}
	if IsBandPlot("line") {
		t.Error("line should not be band plot")
	}
}
