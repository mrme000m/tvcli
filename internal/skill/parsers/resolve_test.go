package parsers

import (
	"testing"

	"github.com/ch99q/tvcli/pkg/schema"
)

func TestSchemaField_ResolvesByName(t *testing.T) {
	// A schema where "Buy" lives at plot_3 (renumbered from the parser's
	// historical plot_0 guess). Resolution must follow the human-readable
	// plot Name, not the hardcoded plot_N index.
	sch := &schema.PineSchema{Plots: []schema.PlotDef{
		{Index: 3, Name: "Buy"},
		{Index: 4, Name: "Sell"},
	}}
	period := map[string]any{"plot_3": 1.0, "plot_4": 0.0}

	got := SchemaField(period, sch, "Buy", "bell", "plot_0")
	if toFloat(got) != 1 {
		t.Fatalf("expected Buy resolved to 1 via name, got %v", got)
	}
	if SchemaFloat(period, sch, "Sell", "plot_1") != 0 {
		t.Fatal("expected Sell to resolve to 0")
	}
}

func TestSchemaField_NilSchemaFallsBack(t *testing.T) {
	// With a nil schema, SchemaField must behave exactly like the literal
	// key lookup (getField), so parsers can adopt it unconditionally.
	period := map[string]any{"plot_0": 1.0, "Close": 123.0}

	if SchemaFloat(period, nil, "Buy", "plot_0") != 1 {
		t.Fatal("nil schema should fall back to literal plot_0")
	}
	if SchemaFloat(period, nil, "Close", "close") != 123 {
		t.Fatal("nil schema should fall back to literal Close")
	}
	if SchemaFloat(period, nil, "Missing") != 0 {
		t.Fatal("missing key should yield 0")
	}
}

func TestSchemaField_NoMatchReturnsNil(t *testing.T) {
	sch := &schema.PineSchema{Plots: []schema.PlotDef{{Index: 0, Name: "Buy"}}}
	period := map[string]any{"plot_0": 1.0}
	if SchemaField(period, sch, "Nonexistent") != nil {
		t.Fatal("unmatched name with no literal key should return nil")
	}
}

func TestPlotKeyForTitle(t *testing.T) {
	sch := &schema.PineSchema{Plots: []schema.PlotDef{
		{Index: 2, Name: "Trailing Stop"},
		{Index: 9, Name: "BG Color"},
	}}
	if k, ok := plotKeyForTitle(sch, "trailing stop"); !ok || k != "plot_2" {
		t.Fatalf("expected plot_2 for 'trailing stop', got %q ok=%v", k, ok)
	}
	if k, ok := plotKeyForTitle(sch, "BG COLOR"); !ok || k != "plot_9" {
		t.Fatalf("expected plot_9 for 'BG COLOR', got %q ok=%v", k, ok)
	}
	if _, ok := plotKeyForTitle(sch, "nope"); ok {
		t.Fatal("expected no match for 'nope'")
	}
}
