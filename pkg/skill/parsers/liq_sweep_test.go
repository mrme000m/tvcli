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
