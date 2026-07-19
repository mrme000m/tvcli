package parsers

import (
	"encoding/json"
	"os"
	"testing"

	"github.com/ch99q/tvcli/internal/skill"
)

func TestOrderFlowSkill_Fixture(t *testing.T) {
	b, err := os.ReadFile("testdata/order_flow_fixture.json")
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

	result := OrderFlowSkill.ParseOutput(fixture.Periods, fixture.Graphic, "5m", "BINANCE:BTCUSDT", map[string]string{})

	if result.Status != "ok" {
		t.Fatalf("expected status ok, got %s", result.Status)
	}
	if result.Workflow != "order-flow" {
		t.Fatalf("unexpected workflow: %s", result.Workflow)
	}
	bull := result.Structure["bullSpikes"].(int)
	bear := result.Structure["bearSpikes"].(int)
	if bull < 0 || bear < 0 {
		t.Fatalf("spike counts must be non-negative: bull=%d bear=%d", bull, bear)
	}
	if bear == 0 && bull == 0 {
		t.Logf("fixture has no spikes; counts are zero as expected")
	}
	if !result.Validation.Passed {
		t.Fatalf("validation should pass")
	}
	if !result.Conformance.HasValidData {
		t.Fatalf("expected valid data")
	}
}

func TestOrderFlowSkill_NoData(t *testing.T) {
	result := OrderFlowSkill.ParseOutput(nil, nil, "5m", "BINANCE:BTCUSDT", map[string]string{})
	if result.Status != "no_data" {
		t.Fatalf("expected no_data status, got %s", result.Status)
	}
}

func TestOrderFlowSkill_LatestSpikeLabel(t *testing.T) {
	if latestSpikeLabel(true, false) != "bullish" {
		t.Fatal("expected bullish")
	}
	if latestSpikeLabel(false, true) != "bearish" {
		t.Fatal("expected bearish")
	}
	if latestSpikeLabel(false, false) != "none" {
		t.Fatal("expected none")
	}
}

func TestOrderFlowSkill_Register(t *testing.T) {
	s := skill.Get("order-flow")
	if s == nil {
		t.Fatal("expected order-flow to be registered")
	}
	if s.PineID != OrderFlowSkill.PineID {
		t.Fatalf("unexpected PineID: %s", s.PineID)
	}
}
