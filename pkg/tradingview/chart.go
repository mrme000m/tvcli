package tradingview

import (
	"encoding/json"
	"fmt"
	"sort"
	"sync"
	"time"
)

type ChartSession struct {
	client        Client
	sessionID     string
	seriesID      string // price-series data-source id ("$prices")
	seriesCreated bool
	currentSeries int
	periods       map[float64]map[string]any
	periodsMu     sync.RWMutex
	// mu guards the fields shared between the WS readLoop (onData) and the
	// request goroutine: studyListeners, infos, and the callback slices. The
	// readLoop dispatches onData while a concurrent hunt worker calls
	// RemoveAllStudies/Delete/GetStudies/GetSymbolInfo, so every access to
	// these must go through mu or a concurrent map read+write panics.
	mu                sync.RWMutex
	infos             map[string]any
	studyListeners    map[string]func(map[string]any)
	onSymbolLoaded    []func()
	onSeriesCompleted []func()
	onUpdate          []func()
	onError           []func(error)
}

func NewChartSession(client Client) *ChartSession {
	cs := &ChartSession{
		client:         client,
		sessionID:      genSessionID("cs"),
		seriesID:       "$prices",
		periods:        make(map[float64]map[string]any),
		studyListeners: make(map[string]func(map[string]any)),
	}

	client.RegisterSession(cs.sessionID, "chart", cs.onData)
	client.Send("chart_create_session", []any{cs.sessionID})
	return cs
}

// --- concurrent accessors (WS readLoop vs request goroutine) --------------

func (cs *ChartSession) registerStudy(id string, fn func(map[string]any)) {
	cs.mu.Lock()
	cs.studyListeners[id] = fn
	cs.mu.Unlock()
}

func (cs *ChartSession) unregisterStudy(id string) {
	cs.mu.Lock()
	delete(cs.studyListeners, id)
	cs.mu.Unlock()
}

func (cs *ChartSession) lookupStudy(id string) (func(map[string]any), bool) {
	cs.mu.RLock()
	fn, ok := cs.studyListeners[id]
	cs.mu.RUnlock()
	return fn, ok
}

func (cs *ChartSession) setInfos(m map[string]any) {
	cs.mu.Lock()
	cs.infos = m
	cs.mu.Unlock()
}

func (cs *ChartSession) snapshotSymbolLoaded() []func() {
	cs.mu.RLock()
	out := append([]func(){}, cs.onSymbolLoaded...)
	cs.mu.RUnlock()
	return out
}

func (cs *ChartSession) snapshotSeriesCompleted() []func() {
	cs.mu.RLock()
	out := append([]func(){}, cs.onSeriesCompleted...)
	cs.mu.RUnlock()
	return out
}

func (cs *ChartSession) snapshotUpdate() []func() {
	cs.mu.RLock()
	out := append([]func(){}, cs.onUpdate...)
	cs.mu.RUnlock()
	return out
}

func (cs *ChartSession) snapshotError() []func(error) {
	cs.mu.RLock()
	out := append([]func(error){}, cs.onError...)
	cs.mu.RUnlock()
	return out
}

func (cs *ChartSession) Send(msgType string, params []any) {
	cs.client.Send(msgType, params)
}

func (cs *ChartSession) onData(packet map[string]any) {
	msgType, _ := packet["type"].(string)
	data, _ := packet["data"].([]any)

	switch msgType {
	case "symbol_resolved":
		if len(data) > 1 {
			if infoMap, ok := data[1].(map[string]any); ok {
				cs.setInfos(infoMap)
			}
		}
		for _, fn := range cs.snapshotSymbolLoaded() {
			fn()
		}

	case "series_completed":
		for _, fn := range cs.snapshotSeriesCompleted() {
			fn()
		}

	case "timescale_update", "du":
		if len(data) > 1 {
			if dataMap, ok := data[1].(map[string]any); ok {
				for key, val := range dataMap {
					if key == "s1" || key == cs.seriesID {
						cs.parsePrices(val)
					} else if listener, ok := cs.lookupStudy(key); ok {
						listener(packet)
					}
				}
			}
		}
		for _, fn := range cs.snapshotUpdate() {
			fn()
		}

	case "symbol_error", "series_error", "critical_error":
		errMsg := "unknown error"
		if len(data) > 2 {
			if s, ok := data[2].(string); ok {
				errMsg = s
			}
		}
		for _, fn := range cs.snapshotError() {
			fn(fmt.Errorf(errMsg))
		}

	case "study_error", "study_completed":
		if len(data) > 1 {
			if studyID, ok := data[1].(string); ok {
				if listener, ok := cs.lookupStudy(studyID); ok {
					listener(packet)
				}
			}
		}
	}
}

func (cs *ChartSession) parsePrices(val any) {
	pMap, ok := val.(map[string]any)
	if !ok {
		return
	}
	sArr, ok := pMap["s"].([]any)
	if !ok {
		return
	}

	cs.periodsMu.Lock()
	defer cs.periodsMu.Unlock()

	for _, p := range sArr {
		pObj, ok := p.(map[string]any)
		if !ok {
			continue
		}
		vArr, ok := pObj["v"].([]any)
		if !ok || len(vArr) < 6 {
			continue
		}
		time, _ := vArr[0].(float64)
		open, _ := vArr[1].(float64)
		high, _ := vArr[2].(float64)
		low, _ := vArr[3].(float64)
		close, _ := vArr[4].(float64)
		volume, _ := vArr[5].(float64)

		cs.periods[time] = map[string]any{
			"time":   time,
			"open":   open,
			"high":   high,
			"low":    low,
			"close":  close,
			"volume": volume,
		}
	}
}

func (cs *ChartSession) SetMarket(symbol string, opts map[string]any) {
	cs.periodsMu.Lock()
	cs.periods = make(map[float64]map[string]any)
	cs.periodsMu.Unlock()

	timeframe, _ := opts["timeframe"].(string)
	if timeframe == "" {
		timeframe = "240"
	}
	rangeVal, _ := opts["range"].(int)
	if rangeVal == 0 {
		rangeVal = 100
	}

	symbolInit := map[string]any{
		"symbol":     symbol,
		"adjustment": "splits",
	}

	cs.currentSeries++
	serID := fmt.Sprintf("ser_%d", cs.currentSeries)

	cs.Send("resolve_symbol", []any{
		cs.sessionID,
		serID,
		"=" + toJSON(symbolInit),
	})

	// Optional historical anchor: opts["to"] = unix seconds. When set, the
	// chart window ends at that moment instead of "now", so studies and
	// OHLCV fetches reflect the market at a past timestamp (bar replay
	// semantics without the replay session). Zero/absent → live window.
	var toAny any
	switch v := opts["to"].(type) {
	case int:
		if v != 0 {
			toAny = v
		}
	case int64:
		if v != 0 {
			toAny = v
		}
	case float64:
		if v != 0 {
			toAny = int64(v)
		}
	}

	cs.setSeries(timeframe, rangeVal, toAny)
}

func (cs *ChartSession) setSeries(timeframe string, rangeVal int, to any) {
	if cs.currentSeries == 0 {
		return
	}

	var calcRange any
	if to == nil {
		calcRange = rangeVal
	} else {
		calcRange = []any{"bar_count", to, rangeVal}
	}

	serID := fmt.Sprintf("ser_%d", cs.currentSeries)

	if cs.seriesCreated {
		cs.Send("modify_series", []any{
			cs.sessionID, cs.seriesID, "s1", serID, timeframe, "",
		})
	} else {
		cs.Send("create_series", []any{
			cs.sessionID, cs.seriesID, "s1", serID, timeframe, calcRange,
		})
		cs.seriesCreated = true
	}
}

// RequestMoreData asks the server to backfill `count` additional bars BEFORE
// the earliest bar currently loaded. This is the same message the live chart
// sends when the user scrolls back in time: request_more_data
// [chart_session_id, series_id, count]. The server replies series_loading →
// timescale_update/du (older bars) → series_completed. Call it repeatedly to
// walk arbitrarily far back in history.
func (cs *ChartSession) RequestMoreData(count int) {
	if count <= 0 {
		return
	}
	cs.Send("request_more_data", []any{cs.sessionID, cs.seriesID, count})
}

// SeriesID returns the price-series data-source id used by this session.
func (cs *ChartSession) SeriesID() string {
	return cs.seriesID
}

func (cs *ChartSession) OnSymbolLoaded(fn func()) {
	cs.mu.Lock()
	cs.onSymbolLoaded = append(cs.onSymbolLoaded, fn)
	cs.mu.Unlock()
}

// OnSeriesCompleted registers a callback fired each time the server finishes
// streaming a price-series load (the initial create_series and every
// RequestMoreData backfill each produce one series_completed).
func (cs *ChartSession) OnSeriesCompleted(fn func()) {
	cs.mu.Lock()
	cs.onSeriesCompleted = append(cs.onSeriesCompleted, fn)
	cs.mu.Unlock()
}

func (cs *ChartSession) OnUpdate(fn func()) {
	cs.mu.Lock()
	cs.onUpdate = append(cs.onUpdate, fn)
	cs.mu.Unlock()
}

func (cs *ChartSession) OnError(fn func(error)) {
	cs.mu.Lock()
	cs.onError = append(cs.onError, fn)
	cs.mu.Unlock()
}

func (cs *ChartSession) Study(indicator any) *ChartStudy {
	return NewChartStudy(cs, indicator)
}

// GetStudies returns a list of active study IDs on this chart.
func (cs *ChartSession) GetStudies() []string {
	cs.mu.RLock()
	defer cs.mu.RUnlock()

	var ids []string
	for id := range cs.studyListeners {
		ids = append(ids, id)
	}
	return ids
}

// RemoveStudy removes a study by ID from the chart.
func (cs *ChartSession) RemoveStudy(studyID string) {
	if _, ok := cs.lookupStudy(studyID); !ok {
		return
	}
	cs.Send("remove_study", []any{cs.sessionID, studyID})
	cs.unregisterStudy(studyID)
}

// RemoveAllStudies removes all studies from the chart.
//
// Study removals are spaced ~100ms apart so the TradingView server reliably
// releases each indicator slot before the next removal and before the chart
// session is deleted. This mirrors the proven pattern in reference clients
// (e.g. tradingview-mcp's removeAllStudies uses a 100ms sleep per study) and
// avoids free-tier "maximum number of studies" errors from studies being
// dropped all-at-once.
func (cs *ChartSession) RemoveAllStudies() {
	cs.mu.RLock()
	ids := make([]string, 0, len(cs.studyListeners))
	for id := range cs.studyListeners {
		ids = append(ids, id)
	}
	cs.mu.RUnlock()

	for _, id := range ids {
		cs.Send("remove_study", []any{cs.sessionID, id})
		cs.unregisterStudy(id)
		time.Sleep(100 * time.Millisecond)
	}
}

func (cs *ChartSession) Delete() {
	// Remove studies first so TradingView releases indicator slots before the
	// chart session itself is deleted. The sleep gives the remove_study messages
	// time to be flushed and processed by the server.
	cs.RemoveAllStudies()
	// Allow the per-study remove messages (100ms apart) plus a flush buffer to
	// be drained before deleting the session, matching reference clients.
	time.Sleep(300 * time.Millisecond)

	cs.client.Send("chart_delete_session", []any{cs.sessionID})
	cs.client.UnregisterSession(cs.sessionID)
	// 500ms flush delay so the server processes the chart_delete_session and
	// releases all study slots before the caller closes the WS connection.
	// Without this, the next run may hit a study-limit error because the
	// server has not yet released the slot.
	time.Sleep(500 * time.Millisecond)
}

// GetSymbolInfo returns the resolved symbol info for this chart session.
func (cs *ChartSession) GetSymbolInfo() map[string]any {
	cs.mu.RLock()
	defer cs.mu.RUnlock()
	return cs.infos
}

// GetSessionID returns the chart session ID (for debugging).
func (cs *ChartSession) GetSessionID() string {
	return cs.sessionID
}

func (cs *ChartSession) WaitForSymbol(timeout time.Duration) error {
	done := make(chan struct{})
	once := sync.Once{}

	cs.OnSymbolLoaded(func() {
		once.Do(func() { close(done) })
	})
	cs.OnError(func(err error) {
		once.Do(func() { close(done) })
	})

	select {
	case <-done:
		return nil
	case <-time.After(timeout):
		return fmt.Errorf("timeout waiting for symbol load")
	}
}

func toJSON(v any) string {
	b, _ := json.Marshal(v)
	return string(b)
}

// Periods returns the current price bars sorted by time descending.
func (cs *ChartSession) Periods() []map[string]any {
	cs.periodsMu.RLock()
	defer cs.periodsMu.RUnlock()

	var result []map[string]any
	for _, p := range cs.periods {
		result = append(result, p)
	}
	sort.Slice(result, func(i, j int) bool {
		t1, _ := result[i]["time"].(float64)
		t2, _ := result[j]["time"].(float64)
		return t1 > t2
	})
	return result
}
