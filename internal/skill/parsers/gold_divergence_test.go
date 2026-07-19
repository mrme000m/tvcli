package parsers

import (
	"encoding/json"
	"os"
	"testing"

	"github.com/ch99q/tvcli/internal/skill"
)

func TestGoldDivergenceSkill_Fixture(t *testing.T) {
	b, err := os.ReadFile("testdata/gold_divergence_fixture.json")
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

	result := GoldDivergenceSkill.ParseOutput(fixture.Periods, fixture.Graphic, "1h", "OANDA:XAUUSD", map[string]string{})

	if result.Status != "ok" {
		t.Fatalf("expected status ok, got %s", result.Status)
	}
	if result.Workflow != "gold-divergence" {
		t.Fatalf("unexpected workflow: %s", result.Workflow)
	}
	rsi := toFloat(result.Structure["rsi"])
	if rsi <= 0 {
		t.Fatalf("expected positive RSI, got %v", result.Structure["rsi"])
	}
	bull := result.Structure["bullDivergences"].(int)
	bear := result.Structure["bearDivergences"].(int)
	if bull < 0 || bear < 0 {
		t.Fatalf("divergence counts must be non-negative: bull=%d bear=%d", bull, bear)
	}
	if !result.Validation.Passed {
		t.Fatalf("validation should pass")
	}
	if !result.Conformance.HasValidData {
		t.Fatalf("expected valid data")
	}
}

func TestGoldDivergenceSkill_NoData(t *testing.T) {
	result := GoldDivergenceSkill.ParseOutput(nil, nil, "1h", "OANDA:XAUUSD", map[string]string{})
	if result.Status != "no_data" {
		t.Fatalf("expected no_data status, got %s", result.Status)
	}
}

func TestGoldDivergenceSkill_RealValue(t *testing.T) {
	// Sentinel means "no divergence".
	if isRealDivergenceValue(1e100) {
		t.Fatal("sentinel should not be a real divergence")
	}
	if !isRealDivergenceValue(50.0) {
		t.Fatal("normal value should be real")
	}
}

func TestGoldDivergenceSkill_Register(t *testing.T) {
	s := skill.Get("gold-divergence")
	if s == nil {
		t.Fatal("expected gold-divergence to be registered")
	}
	if s.PineID != GoldDivergenceSkill.PineID {
		t.Fatalf("unexpected PineID: %s", s.PineID)
	}
}
