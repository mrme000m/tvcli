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
		{"free", TierLimits{1, 2, 2, 180, 20}},
		{"essential", TierLimits{2, 5, 10, 365, 40}},
		{"plus", TierLimits{4, 10, 20, 0, 40}},
		{"premium", TierLimits{8, 25, 50, 0, 40}},
		{"ultimate", TierLimits{16, 50, 200, 0, 100}},
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
