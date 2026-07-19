package tradingview

import (
	"encoding/json"
	"fmt"
	"sort"
	"sync"
	"time"
)

type ChartSession struct {
	client          *Client
	sessionID       string
	seriesCreated   bool
	currentSeries   int
	periods         map[float64]map[string]any
	periodsMu       sync.RWMutex
	infos           map[string]any
	studyListeners  map[string]func(map[string]any)
	onSymbolLoaded  []func()
	onUpdate        []func()
	onError         []func(error)
}

func NewChartSession(client *Client) *ChartSession {
	cs := &ChartSession{
		client:         client,
		sessionID:      genSessionID("cs"),
		periods:        make(map[float64]map[string]any),
		studyListeners: make(map[string]func(map[string]any)),
	}

	client.sessions[cs.sessionID] = &sessionEntry{
		typ:    "chart",
		onData: cs.onData,
	}

	client.Send("chart_create_session", []any{cs.sessionID})
	return cs
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
				cs.infos = infoMap
			}
		}
		for _, fn := range cs.onSymbolLoaded {
			fn()
		}

	case "timescale_update", "du":
		if len(data) > 1 {
			if dataMap, ok := data[1].(map[string]any); ok {
				for key, val := range dataMap {
					if key == "s1" || key == "$prices" {
						cs.parsePrices(val)
					} else if listener, ok := cs.studyListeners[key]; ok {
						listener(packet)
					}
				}
			}
		}
		for _, fn := range cs.onUpdate {
			fn()
		}

	case "symbol_error", "series_error", "critical_error":
		errMsg := "unknown error"
		if len(data) > 2 {
			if s, ok := data[2].(string); ok {
				errMsg = s
			}
		}
		for _, fn := range cs.onError {
			fn(fmt.Errorf(errMsg))
		}

	case "study_error", "study_completed":
		if len(data) > 1 {
			if studyID, ok := data[1].(string); ok {
				if listener, ok := cs.studyListeners[studyID]; ok {
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

	cs.setSeries(timeframe, rangeVal, nil)
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
			cs.sessionID, "$prices", "s1", serID, timeframe, "",
		})
	} else {
		cs.Send("create_series", []any{
			cs.sessionID, "$prices", "s1", serID, timeframe, calcRange,
		})
		cs.seriesCreated = true
	}
}

func (cs *ChartSession) OnSymbolLoaded(fn func()) {
	cs.onSymbolLoaded = append(cs.onSymbolLoaded, fn)
}

func (cs *ChartSession) OnUpdate(fn func()) {
	cs.onUpdate = append(cs.onUpdate, fn)
}

func (cs *ChartSession) OnError(fn func(error)) {
	cs.onError = append(cs.onError, fn)
}

func (cs *ChartSession) Study(indicator any) *ChartStudy {
	return NewChartStudy(cs, indicator)
}

// GetStudies returns a list of active study IDs on this chart.
func (cs *ChartSession) GetStudies() []string {
	cs.periodsMu.RLock()
	defer cs.periodsMu.RUnlock()

	var ids []string
	for id := range cs.studyListeners {
		ids = append(ids, id)
	}
	return ids
}

// RemoveStudy removes a study by ID from the chart.
func (cs *ChartSession) RemoveStudy(studyID string) {
	if _, ok := cs.studyListeners[studyID]; !ok {
		return
	}
	cs.Send("remove_study", []any{cs.sessionID, studyID})
	delete(cs.studyListeners, studyID)
}

// RemoveAllStudies removes all studies from the chart.
func (cs *ChartSession) RemoveAllStudies() {
	for id := range cs.studyListeners {
		cs.Send("remove_study", []any{cs.sessionID, id})
		delete(cs.studyListeners, id)
	}
}

func (cs *ChartSession) Delete() {
	cs.client.Send("chart_delete_session", []any{cs.sessionID})
	delete(cs.client.sessions, cs.sessionID)
}

// GetSymbolInfo returns the resolved symbol info for this chart session.
func (cs *ChartSession) GetSymbolInfo() map[string]any {
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
