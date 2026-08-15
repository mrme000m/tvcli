package pipeline

import (
	"testing"
)

// mtfLikeGraphic builds a graphic shaped like the XAUUSD MTF dashboard:
// a 6-col x 3-row table with header + Trend + Strength rows.
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
	tables := map[string]any{
		"1": map[string]any{"cols": 6, "rows": 3, "id": 1},
	}
	return map[string]map[string]any{
		"dwgtables":     tables,
		"dwgtablecells": cells,
	}
}

func TestReconstructTables(t *testing.T) {
	grids := ReconstructTables(mtfLikeGraphic())
	if len(grids) != 1 {
		t.Fatalf("expected 1 table, got %d", len(grids))
	}
	g := grids[0]
	if g.Cols != 6 || g.Rows != 3 {
		t.Fatalf("expected 6x3 grid, got %dx%d", g.Cols, g.Rows)
	}
	if got := g.Cells[0][1]; got != "M15" {
		t.Errorf("header[1] = %q, want M15", got)
	}
	if got := g.Cells[1][4]; got != "Strong Downtrend" {
		t.Errorf("trend row H4 = %q, want Strong Downtrend", got)
	}
	if got := g.Cells[2][5]; got != "-4" {
		t.Errorf("strength row D1 = %q, want -4", got)
	}
	if got := g.RowByLabel("Trend"); got != 1 {
		t.Errorf("RowByLabel(Trend) = %d, want 1", got)
	}
	if got := g.RowByLabel("Strength"); got != 2 {
		t.Errorf("RowByLabel(Strength) = %d, want 2", got)
	}
	if got := g.RowByLabel("Nope"); got != -1 {
		t.Errorf("RowByLabel(Nope) = %d, want -1", got)
	}
}

func TestReconstructTablesEmpty(t *testing.T) {
	if grids := ReconstructTables(nil); grids != nil {
		t.Errorf("nil graphic should yield nil, got %v", grids)
	}
	if grids := ReconstructTables(map[string]map[string]any{}); grids != nil {
		t.Errorf("empty graphic should yield nil, got %v", grids)
	}
	if grids := ReconstructTables(map[string]map[string]any{
		"dwgtables":     {"1": map[string]any{"cols": 6, "rows": 3}},
		"dwgtablecells": map[string]any{}, // no cells -> skipped
	}); len(grids) != 0 {
		t.Errorf("table with no cells should be skipped, got %d", len(grids))
	}
}
