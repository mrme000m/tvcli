// Package service is the use-case layer between the CLI and the pkg/* libraries.
// It is the only place that wires multiple pkg's together.
package service

import (
	"compress/gzip"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/mrme000m/tvcli/internal/config"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
	"github.com/mrme000m/tvcli/pkg/tradingview"
)

// OHLCVBar is a single OHLCV bar for serialization.
type OHLCVBar struct {
	Time   float64 `json:"t"`
	Open   float64 `json:"o"`
	High   float64 `json:"h"`
	Low    float64 `json:"l"`
	Close  float64 `json:"c"`
	Volume float64 `json:"v"`
}

// OHLCVFile is the on-disk compressed format.
type OHLCVFile struct {
	Symbol    string     `json:"symbol"`
	Timeframe string     `json:"tf"`
	Count     int        `json:"count"`
	UpdatedAt string     `json:"updatedAt"`
	Data      []OHLCVBar `json:"data"`
}

// LoadOHLCV reads a gzipped OHLCVFile from path.
func LoadOHLCV(path string) (*OHLCVFile, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	gz, err := gzip.NewReader(f)
	if err != nil {
		return nil, err
	}
	defer gz.Close()

	var file OHLCVFile
	if err := json.NewDecoder(gz).Decode(&file); err != nil {
		return nil, err
	}
	return &file, nil
}

// SaveOHLCV writes file to path as gzipped JSON, creating parent dirs as needed.
func SaveOHLCV(path string, file *OHLCVFile) error {
	os.MkdirAll(filepath.Dir(path), 0755)

	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()

	gz := gzip.NewWriter(f)
	defer gz.Close()

	gz.Comment = "tvcli ohlcv"
	enc := json.NewEncoder(gz)
	enc.SetIndent("", "")
	return enc.Encode(file)
}

// LastTimestamp returns the time of the last bar, or 0 if empty.
func LastTimestamp(data []OHLCVBar) float64 {
	if len(data) == 0 {
		return 0
	}
	return data[len(data)-1].Time
}

// MergeOHLCV merges fresh bars into existing, deduplicating by timestamp.
// Both slices must be sorted ascending by time. Returns merged sorted slice.
func MergeOHLCV(existing, fresh []OHLCVBar) []OHLCVBar {
	if len(existing) == 0 {
		return fresh
	}
	if len(fresh) == 0 {
		return existing
	}

	seen := make(map[float64]bool, len(existing))
	for _, b := range existing {
		seen[b.Time] = true
	}

	merged := make([]OHLCVBar, 0, len(existing)+len(fresh))
	merged = append(merged, existing...)
	for _, b := range fresh {
		if !seen[b.Time] {
			merged = append(merged, b)
		}
	}

	sort.Slice(merged, func(i, j int) bool {
		return merged[i].Time < merged[j].Time
	})
	return merged
}

// FetchOHLCVBars connects via WS, fetches raw OHLCV bars, and returns them
// sorted ascending.
func FetchOHLCVBars(cfg *config.Config, symbol, tf string, bars int) ([]OHLCVBar, error) {
	client := tradingview.NewClient(
		tradingview.WithToken(cfg.SessionID),
		tradingview.WithSignature(cfg.Signature),
			tradingview.WithDeviceToken(cfg.DeviceToken),
		tradingview.WithDebug(cfg.Debug),
	)
	if err := client.Connect(); err != nil {
		return nil, fmt.Errorf("ws connect: %w", err)
	}
	if !client.WaitForConnected(10 * time.Second) {
		client.Close()
		return nil, fmt.Errorf("ws timeout")
	}
	defer client.Close()

	return FetchOHLCVBarsWithClient(client, symbol, tf, bars)
}

// FetchOHLCVBarsWithClient fetches OHLCV using an existing WS client
// (for persistent/loop connections).
func FetchOHLCVBarsWithClient(client tradingview.Client, symbol, tf string, bars int) ([]OHLCVBar, error) {
	ch := tradingview.NewChartSession(client)
	ch.OnError(func(err error) {
		fmt.Fprintf(os.Stderr, "Chart error: %v\n", err)
	})

	done := make(chan struct{})
	once := sync.Once{}
	ch.OnUpdate(func() {
		once.Do(func() { close(done) })
	})

	ch.SetMarket(symbol, map[string]any{
		"timeframe": pinefacade.NormalizeTimeframe(tf),
		"range":     bars,
	})

	if err := ch.WaitForSymbol(15 * time.Second); err != nil {
		return nil, fmt.Errorf("symbol load: %w", err)
	}

	select {
	case <-done:
	case <-time.After(30 * time.Second):
		return nil, fmt.Errorf("timeout waiting for OHLCV data")
	}
	time.Sleep(800 * time.Millisecond)

	periods := ch.Periods()
	if len(periods) == 0 {
		return nil, fmt.Errorf("no OHLCV data received")
	}

	out := make([]OHLCVBar, 0, len(periods))
	for _, p := range periods {
		out = append(out, OHLCVBar{
			Time:   p["time"].(float64),
			Open:   p["open"].(float64),
			High:   p["high"].(float64),
			Low:    p["low"].(float64),
			Close:  p["close"].(float64),
			Volume: p["volume"].(float64),
		})
	}

	sort.Slice(out, func(i, j int) bool {
		return out[i].Time < out[j].Time
	})

	return out, nil
}

// TimeframeSeconds returns the approximate seconds per bar for a timeframe string.
func TimeframeSeconds(tf string) int64 {
	t := strings.ToUpper(tf)
	switch t {
	case "1":
		return 60
	case "3":
		return 180
	case "5":
		return 300
	case "15":
		return 900
	case "30":
		return 1800
	case "45":
		return 2700
	case "60", "1H":
		return 3600
	case "120", "2H":
		return 7200
	case "180", "3H":
		return 10800
	case "240", "4H":
		return 14400
	case "D", "1D":
		return 86400
	case "W", "1W":
		return 604800
	case "M", "1M":
		return 2592000
	}
	n := 0
	fmt.Sscanf(tf, "%d", &n)
	if n > 0 {
		return int64(n) * 60
	}
	return 300 // default 5m
}
