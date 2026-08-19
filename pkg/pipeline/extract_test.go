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

func TestResolveScriptTypeSeparatesStrategyFromIndicator(t *testing.T) {
	cases := []struct {
		name    string
		isStrat bool
		report  map[string]any
		want    ScriptType
	}{
		{"schema hint strategy wins", true, nil, "strategy"},
		{"no hint, no report -> indicator", false, nil, "indicator"},
		{"report performance -> strategy", false, map[string]any{"performance": map[string]any{"all": map[string]any{}}}, "strategy"},
		{"report trades -> strategy", false, map[string]any{"trades": []any{}}, "strategy"},
		{"empty report -> indicator", false, map[string]any{}, "indicator"},
	}
	for _, c := range cases {
		if got := resolveScriptType(c.isStrat, c.report); got != c.want {
			t.Errorf("%s: resolveScriptType(%v,%+v)=%q want %q", c.name, c.isStrat, c.report, got, c.want)
		}
	}
}

func TestStrategyEventsAndBias(t *testing.T) {
	report := map[string]any{
		"trades": []any{
			map[string]any{"e": map[string]any{"tp": "le", "p": 100.0, "c": "L", "tm": float64(10)}},
			map[string]any{"e": map[string]any{"tp": "se", "p": 110.0, "c": "S", "tm": float64(20)}},
			map[string]any{"e": map[string]any{"tp": "le", "p": 105.0, "c": "L2", "tm": float64(30)}},
		},
	}
	evs := strategyEvents(report)
	if len(evs) != 3 {
		t.Fatalf("expected 3 trade events, got %d: %+v", len(evs), evs)
	}
	if evs[0].Kind != "buy" || evs[1].Kind != "sell" || evs[2].Kind != "buy" {
		t.Errorf("unexpected kinds: %s,%s,%s", evs[0].Kind, evs[1].Kind, evs[2].Kind)
	}
	if evs[0].Value != 100.0 || evs[0].Field != "trade_L" || evs[0].Time != 10 {
		t.Errorf("unexpected first event: %+v", evs[0])
	}
	if b := strategyBias(report); b != "long" {
		t.Errorf("strategyBias=%q want long (last trade is long entry)", b)
	}
	if strategyBias(nil) != "" {
		t.Error("strategyBias(nil) should be empty")
	}
	if strategyBias(map[string]any{}) != "" {
		t.Error("strategyBias(empty) should be empty")
	}
}

func TestExtractMarksStrategyScriptType(t *testing.T) {
	periods := []map[string]any{
		{"$time": 1.0, "plot_0": 100.0},
		{"$time": 2.0, "plot_0": 101.0},
	}
	report := map[string]any{
		"performance": map[string]any{"all": map[string]any{"totalTrades": 2.0}},
		"trades": []any{
			map[string]any{"e": map[string]any{"tp": "le", "p": 100.0, "c": "Long", "tm": float64(1)}},
		},
	}
	sig := Extract("PUB;test", "TEST", "1m", periods, nil, report)
	if sig.Meta.ScriptType != ScriptTypeStrategy {
		t.Errorf("ScriptType=%q want strategy", sig.Meta.ScriptType)
	}
	if sig.Report == nil || sig.Report.TotalTrades != 2 {
		t.Errorf("expected strategy report totalTrades=2, got %+v", sig.Report)
	}
	if len(sig.Events) != 1 || sig.Events[0].Kind != "buy" || sig.Events[0].Field != "trade_Long" {
		t.Errorf("expected 1 strategy buy event, got %+v", sig.Events)
	}
	if sig.Bias != "long" {
		t.Errorf("strategy bias=%q want long", sig.Bias)
	}

	// Same periods, no report -> indicator.
	sig2 := Extract("PUB;test", "TEST", "1m", periods, nil, nil)
	if sig2.Meta.ScriptType != ScriptTypeIndicator {
		t.Errorf("no-report ScriptType=%q want indicator", sig2.Meta.ScriptType)
	}
	if sig2.Report != nil {
		t.Errorf("indicator should have nil report, got %+v", sig2.Report)
	}
}
