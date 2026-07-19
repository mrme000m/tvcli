package parsers

import (
	"encoding/json"
	"os"
	"testing"

	"github.com/ch99q/tvcli/internal/skill"
)

func TestXAUTrendSkill_Fixture(t *testing.T) {
	b, err := os.ReadFile("testdata/xau_trend_fixture.json")
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

	result := XAUTrendSkill.ParseOutput(fixture.Periods, fixture.Graphic, "1h", "OANDA:XAUUSD", map[string]string{})

	if result.Status != "ok" {
		t.Fatalf("expected status ok, got %s", result.Status)
	}
	if result.Workflow != "xau-trend" {
		t.Fatalf("unexpected workflow: %s", result.Workflow)
	}
	emaShort := toFloat(result.Structure["emaShort"])
	emaLong := toFloat(result.Structure["emaLong"])
	if emaShort <= 0 || emaLong <= 0 {
		t.Fatalf("expected positive EMA values: short=%v long=%v", emaShort, emaLong)
	}
	upper := toFloat(result.Structure["bollingerUpper"])
	lower := toFloat(result.Structure["bollingerLower"])
	if upper <= lower {
		t.Fatalf("Bollinger upper (%.2f) must be greater than lower (%.2f)", upper, lower)
	}
	if result.Market.Bias == "" {
		t.Fatal("expected non-empty bias")
	}
	if !result.Validation.Passed {
		t.Fatalf("validation should pass")
	}
	if !result.Conformance.HasValidData {
		t.Fatalf("expected valid data")
	}
}

func TestXAUTrendSkill_NoData(t *testing.T) {
	result := XAUTrendSkill.ParseOutput(nil, nil, "1h", "OANDA:XAUUSD", map[string]string{})
	if result.Status != "no_data" {
		t.Fatalf("expected no_data status, got %s", result.Status)
	}
}

func TestXAUTrendSkill_Register(t *testing.T) {
	s := skill.Get("xau-trend")
	if s == nil {
		t.Fatal("expected xau-trend to be registered")
	}
	if s.PineID != XAUTrendSkill.PineID {
		t.Fatalf("unexpected PineID: %s", s.PineID)
	}
}
