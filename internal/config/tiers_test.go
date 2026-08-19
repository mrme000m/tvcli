package config

import (
	"os"
	"testing"
)

func TestGetTierLimitsDefault(t *testing.T) {
	os.Unsetenv("TV_TIER")
	l := GetTierLimits()
	if l.MaxCharts != 1 || l.MaxIndicators != 2 || l.MaxBars != 180 {
		t.Errorf("default tier = %+v, want free", l)
	}
}

func TestGetTierLimitsKnownTiers(t *testing.T) {
	cases := []struct {
		tier     string
		expected TierLimits
	}{
		{"free", TierLimits{MaxCharts: 1, MaxIndicators: 2, MaxConnections: 2, MaxBars: 180, CalcTimeoutSecs: 20}},
		{"essential", TierLimits{MaxCharts: 2, MaxIndicators: 5, MaxConnections: 10, MaxBars: 365, CalcTimeoutSecs: 40}},
		{"plus", TierLimits{MaxCharts: 4, MaxIndicators: 10, MaxConnections: 20, MaxBars: 0, CalcTimeoutSecs: 40}},
		{"premium", TierLimits{MaxCharts: 8, MaxIndicators: 25, MaxConnections: 50, MaxBars: 0, CalcTimeoutSecs: 40}},
		{"ultimate", TierLimits{MaxCharts: 16, MaxIndicators: 50, MaxConnections: 200, MaxBars: 0, CalcTimeoutSecs: 100}},
	}
	for _, c := range cases {
		t.Setenv("TV_TIER", c.tier)
		got := GetTierLimits()
		if got != c.expected {
			t.Errorf("tier=%s: got %+v, want %+v", c.tier, got, c.expected)
		}
	}
}

func TestGetTierLimitsUnknownFallsBackToFree(t *testing.T) {
	t.Setenv("TV_TIER", "nonexistent")
	l := GetTierLimits()
	if l.MaxCharts != 1 {
		t.Errorf("unknown tier = %+v, want free fallback", l)
	}
}
