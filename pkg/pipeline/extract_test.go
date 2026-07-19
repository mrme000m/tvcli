package pipeline

import (
	"testing"
)

func TestExtractFiltersNoiseAndDetectsSignal(t *testing.T) {
	periods := []map[string]any{
		{"$time": 1.0, "plot_0": 100.0, "plot_1": 1.0, "plot_2": 1e100, "plot_3": 0.0},
		{"$time": 2.0, "plot_0": 101.0, "plot_1": 0.0, "plot_2": 1e100, "plot_3": 0.0},
		{"$time": 3.0, "plot_0": 102.0, "plot_1": 1.0, "plot_2": 1e100, "plot_3": 0.0},
	}

	sig := Extract("PUB;test", "TEST", "1m", periods, nil, nil)

	if sig.Classifications["plot_2"] != ClassNoise {
		t.Errorf("expected plot_2 noise, got %s", sig.Classifications["plot_2"])
	}
	if sig.Classifications["plot_3"] != ClassNoise {
		t.Errorf("expected plot_3 noise, got %s", sig.Classifications["plot_3"])
	}
	if sig.Classifications["plot_1"] != ClassSignal {
		t.Errorf("expected plot_1 signal, got %s", sig.Classifications["plot_1"])
	}
	if len(sig.Events) != 1 {
		t.Errorf("expected 1 event, got %d", len(sig.Events))
	}
	if sig.Bias != "long" {
		t.Errorf("expected long bias, got %s", sig.Bias)
	}
}

func TestExtractGraphicLabels(t *testing.T) {
	periods := []map[string]any{
		{"$time": 1.0, "plot_0": 100.0},
	}
	graphic := map[string]map[string]any{
		"dwglabels": {
			"1": map[string]any{"t": "BUY", "x": 1.0, "y": 99.0},
			"2": map[string]any{"t": "SELL", "x": 2.0, "y": 101.0},
		},
	}

	sig := Extract("PUB;test", "TEST", "1m", periods, graphic, nil)

	if sig.GraphicCounts["buy"] != 1 || sig.GraphicCounts["sell"] != 1 {
		t.Errorf("expected buy=1 sell=1 graphic counts, got %v", sig.GraphicCounts)
	}
	if len(sig.Events) == 0 {
		t.Errorf("expected graphic events")
	}
}
