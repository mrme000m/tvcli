package service

import (
	"testing"
)

func TestTimeframeSeconds(t *testing.T) {
	cases := []struct {
		in   string
		want int64
	}{
		{"1", 60},
		{"5", 300},
		{"15", 900},
		{"30", 1800},
		{"45", 2700},
		{"60", 3600},
		{"1H", 3600},
		{"120", 7200},
		{"2H", 7200},
		{"240", 14400},
		{"4H", 14400},
		{"D", 86400},
		{"1D", 86400},
		{"W", 604800},
		{"1W", 604800},
		{"M", 2592000},
		{"1M", 2592000},
		{"7", 420},     // parsed as minutes
		{"", 300},      // default 5m
		{"garbage", 300},
	}
	for _, c := range cases {
		got := TimeframeSeconds(c.in)
		if got != c.want {
			t.Errorf("TimeframeSeconds(%q) = %d, want %d", c.in, got, c.want)
		}
	}
}

func TestLastTimestamp(t *testing.T) {
	if LastTimestamp(nil) != 0 {
		t.Error("nil slice should return 0")
	}
	if LastTimestamp([]OHLCVBar{}) != 0 {
		t.Error("empty slice should return 0")
	}
	bars := []OHLCVBar{{Time: 100}, {Time: 200}, {Time: 300}}
	if LastTimestamp(bars) != 300 {
		t.Errorf("LastTimestamp = %v, want 300", LastTimestamp(bars))
	}
}

func TestMergeOHLCVDedup(t *testing.T) {
	existing := []OHLCVBar{{Time: 100, Open: 1}, {Time: 200, Open: 2}}
	fresh := []OHLCVBar{{Time: 200, Open: 9}, {Time: 300, Open: 3}}

	merged := MergeOHLCV(existing, fresh)
	if len(merged) != 3 {
		t.Fatalf("merged len = %d, want 3: %v", len(merged), merged)
	}
	// Timestamp 200 must keep the existing value (dedup, not overwrite).
	for _, b := range merged {
		if b.Time == 200 && b.Open != 2 {
			t.Errorf("dedup overwrote existing bar at t=200: open=%v", b.Open)
		}
	}
	// Must be sorted ascending.
	for i := 1; i < len(merged); i++ {
		if merged[i].Time < merged[i-1].Time {
			t.Errorf("merged not sorted: %v", merged)
		}
	}
}

func TestMergeOHLCVEmptySides(t *testing.T) {
	a := []OHLCVBar{{Time: 1}}
	if got := MergeOHLCV(nil, a); len(got) != 1 || got[0].Time != 1 {
		t.Errorf("MergeOHLCV(nil, a) = %v", got)
	}
	if got := MergeOHLCV(a, nil); len(got) != 1 || got[0].Time != 1 {
		t.Errorf("MergeOHLCV(a, nil) = %v", got)
	}
	if got := MergeOHLCV(nil, nil); got != nil {
		t.Errorf("MergeOHLCV(nil, nil) = %v, want nil", got)
	}
}
