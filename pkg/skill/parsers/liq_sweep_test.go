package parsers

import (
	"encoding/json"
	"os"
	"testing"

	"github.com/mrme000m/tvcli/pkg/skill"
)

func TestLiqSweepSkill_Fixture(t *testing.T) {
	b, err := os.ReadFile("testdata/liq_sweep_fixture.json")
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}

	var fixture struct {
		Periods []map[string]any            `json:"periods"`
		Graphic map[string]map[string]any `json:"graphic"`
	}
	if err := json.Unmarshal(b, &fixture); err != nil {
		t.Fatalf("unmarshal fixture: %v", err)
	}

	result := LiqSweepSkill.ParseOutput(fixture.Periods, fixture.Graphic, "1h", "OANDA:XAUUSD", map[string]string{})

	if result.Status != "ok" {
		t.Fatalf("expected status ok, got %s", result.Status)
	}
	if result.Workflow != "liquidity-sweep" {
		t.Fatalf("unexpected workflow: %s", result.Workflow)
	}

	bull := result.Structure["bullSweeps"].(int)
	bear := result.Structure["bearSweeps"].(int)
	if bull < 0 || bear < 0 {
		t.Fatalf("sweep counts must be non-negative: bull=%d bear=%d", bull, bear)
	}
	if (bull + bear) == 0 && result.Market.Bias != "neutral" {
		t.Fatalf("bias should be neutral when no sweeps, got %s", result.Market.Bias)
	}
	if result.Market.LastPrice == 0 {
		t.Fatalf("expected a non-zero price from graphic labels")
	}
	if !result.Validation.Passed {
		t.Fatalf("validation should pass")
	}
	if !result.Conformance.HasValidData {
		t.Fatalf("expected valid data")
	}

	// New fields populated by the enhanced parser.
	if _, ok := result.Structure["weightedBullSweeps"]; !ok {
		t.Fatal("expected weightedBullSweeps in structure")
	}
	if _, ok := result.Structure["latestSweep"]; !ok {
		t.Fatal("expected latestSweep in structure")
	}
	if _, ok := result.Structure["barsSinceLastSweep"]; !ok {
		t.Fatal("expected barsSinceLastSweep in structure")
	}
	if _, ok := result.Structure["nearestSweepPrice"]; !ok {
		t.Fatal("expected nearestSweepPrice in structure")
	}
	if _, ok := result.Structure["sweepLevels"]; !ok {
		t.Fatal("expected sweepLevels in structure")
	}
}

func TestLiqSweepSkill_NoData(t *testing.T) {
	result := LiqSweepSkill.ParseOutput(nil, nil, "5m", "BINANCE:BTCUSDT", map[string]string{})
	if result.Status != "no_data" {
		t.Fatalf("expected no_data status, got %s", result.Status)
	}
}

func TestLatestSweepLabel(t *testing.T) {
	if latestSweepLabel(true, false) != "bullish" {
		t.Fatal("expected bullish")
	}
	if latestSweepLabel(false, true) != "bearish" {
		t.Fatal("expected bearish")
	}
	if latestSweepLabel(false, false) != "none" {
		t.Fatal("expected none")
	}
}

func TestSweepDominance(t *testing.T) {
	if sweepDominance(5, 2) != "bullish" {
		t.Fatal("expected bullish dominance")
	}
	if sweepDominance(2, 5) != "bearish" {
		t.Fatal("expected bearish dominance")
	}
	if sweepDominance(3, 3) != "neutral" {
		t.Fatal("expected neutral dominance")
	}
}

func TestWeightedSweepCounts(t *testing.T) {
	bars := []map[string]any{
		{"Bullish_Sweep_Shape": 1},
		{"Bearish_Sweep_Shape": 1},
		{"Bullish_Sweep_Shape": 1},
	}
	bull, bear := weightedSweepCounts(bars)
	if bull <= 0 || bear <= 0 {
		t.Fatalf("expected positive weighted counts, got bull=%f bear=%f", bull, bear)
	}
	// First bar (newest) should contribute full weight.
	if bull < 1.0 || bear < 0.9 {
		t.Fatalf("newest sweep should dominate, got bull=%f bear=%f", bull, bear)
	}
}

func TestRecentPeriodSweep(t *testing.T) {
	bars := []map[string]any{
		{},
		{},
		{"Bullish_Sweep_Shape": 1},
		{},
	}
	kind, ago := recentPeriodSweep(bars)
	if kind != "bullish" {
		t.Fatalf("expected bullish recent sweep, got %s", kind)
	}
	if ago != 2 {
		t.Fatalf("expected sweep 2 bars ago, got %d", ago)
	}
}

func TestExtractSweepLevels(t *testing.T) {
	graphic := map[string]map[string]any{
		"dwglabels": {
			"1": map[string]any{"t": "BULLISH SWEEP", "y": 4500.0, "x": 10.0, "id": 1.0},
			"2": map[string]any{"t": "BEARISH SWEEP", "y": 4600.0, "x": 11.0, "id": 2.0},
		},
	}
	levels := extractSweepLevels(graphic)
	if len(levels) != 2 {
		t.Fatalf("expected 2 sweep levels, got %d", len(levels))
	}
	if levels[0].kind != "bearish" {
		t.Fatalf("expected newest (highest id) bearish level first, got %s", levels[0].kind)
	}
}

func TestNearestSweepLevel(t *testing.T) {
	levels := []sweepLevel{
		{kind: "bullish", price: 4500.0},
		{kind: "bearish", price: 4600.0},
	}
	price, kind, dist := nearestSweepLevel(levels, 4510.0)
	if price != 4500.0 {
		t.Fatalf("expected nearest 4500, got %f", price)
	}
	if kind != "bullish" {
		t.Fatalf("expected bullish nearest, got %s", kind)
	}
	if dist != -10.0 {
		t.Fatalf("expected distance -10, got %f", dist)
	}
}

func TestExtractLiquidityMap(t *testing.T) {
	graphic := map[string]map[string]any{
		"dwglines": {
			"1": map[string]any{"y1": 4600.0, "y2": 4600.0},
			"2": map[string]any{"y1": 4550.0, "y2": 4550.0},
			"3": map[string]any{"y1": 4500.0, "y2": 4500.0},
			"4": map[string]any{"y1": 4490.0, "y2": 4490.0},
		},
	}
	above, below, nearestAbove, nearestBelow := extractLiquidityMap(graphic, 4525.0)
	if len(above) != 2 {
		t.Fatalf("expected 2 above, got %d", len(above))
	}
	if len(below) != 2 {
		t.Fatalf("expected 2 below, got %d", len(below))
	}
	// nearest-above should be the smallest level above price.
	if nearestAbove != 4550.0 {
		t.Fatalf("expected nearest above 4550, got %f", nearestAbove)
	}
	// nearest-below should be the largest level below price.
	if nearestBelow != 4500.0 {
		t.Fatalf("expected nearest below 4500, got %f", nearestBelow)
	}
}

func TestAggregateSweepFlow(t *testing.T) {
	levels := []sweepLevel{
		{kind: "bullish", price: 4500.0},
		{kind: "bullish", price: 4500.5},
		{kind: "bearish", price: 4600.0},
	}
	bull, bear, clusters := aggregateSweepFlow(levels, 4500.0)
	if bull != 2 || bear != 1 {
		t.Fatalf("expected bull=2 bear=1, got bull=%d bear=%d", bull, bear)
	}
	// Two bullish sweeps within tolerance should form one repeated cluster.
	if len(clusters) != 1 {
		t.Fatalf("expected 1 repeated cluster, got %d", len(clusters))
	}
	if clusters[0].bull != 2 {
		t.Fatalf("expected cluster bull=2, got %d", clusters[0].bull)
	}
}

func TestLiquidityImbalance(t *testing.T) {
	if liquidityImbalance(5, 2) != "above" {
		t.Fatal("expected above")
	}
	if liquidityImbalance(2, 5) != "below" {
		t.Fatal("expected below")
	}
	if liquidityImbalance(3, 3) != "balanced" {
		t.Fatal("expected balanced")
	}
}

// Ensure the skill is registered and exposes expected metadata.
func TestLiqSweepSkill_Registry(t *testing.T) {
	s := skill.Get("liq-sweep")
	if s == nil {
		t.Fatal("liq-sweep skill not registered")
	}
	if s.PineID != "PUB;b9372355c2e6483f952ca49a21d2ebbb" {
		t.Fatalf("unexpected pine id: %s", s.PineID)
	}
	if len(s.Presets) != 3 {
		t.Fatalf("expected 3 presets, got %d", len(s.Presets))
	}
}
