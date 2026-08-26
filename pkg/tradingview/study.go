package tradingview

import (
	"encoding/json"
	"fmt"
	"log"
	"sort"
	"strconv"
	"sync"
	"time"
)

type ChartStudy struct {
	session   *ChartSession
	studyID   string
	indicator any
	periods   map[float64]map[string]any
	periodsMu sync.RWMutex
	// graphicMu guards graphic + strategyReport (written by the WS readLoop,
	// read by the request goroutine after the study settle window).
	graphicMu      sync.RWMutex
	graphic        map[string]map[string]any
	strategyReport map[string]any
	// mu guards the callback slices (readLoop dispatch vs request-goroutine
	// registration).
	mu       sync.Mutex
	onUpdate []func()
	onError  []func(error)
	onReady  []func()
}

func NewChartStudy(session *ChartSession, indicator any) *ChartStudy {
	cs := &ChartStudy{
		session:   session,
		studyID:   genSessionID("st"),
		indicator: indicator,
		periods:   make(map[float64]map[string]any),
		graphic:   make(map[string]map[string]any),
	}

	session.registerStudy(cs.studyID, cs.onData)

	inputs := cs.getInputs()
	session.Send("create_study", []any{
		session.sessionID,
		cs.studyID,
		"st1",
		"$prices",
		cs.indicatorType(),
		inputs,
	})

	return cs
}

func (cs *ChartStudy) indicatorType() string {
	switch ind := cs.indicator.(type) {
	case *PineIndicator:
		return ind.Type
	case *BuiltinIndicator:
		return ind.Type
	default:
		return "Script@tv-scripting-101!"
	}
}

func (cs *ChartStudy) getInputs() map[string]any {
	switch ind := cs.indicator.(type) {
	case *PineIndicator:
		return ind.GetInputs()
	case *BuiltinIndicator:
		return ind.Options
	default:
		return map[string]any{}
	}
}

// getPlotNames returns the plot_N → human-readable name mapping from the indicator's metadata.
func (cs *ChartStudy) getPlotNames() map[string]string {
	switch ind := cs.indicator.(type) {
	case *PineIndicator:
		return ind.Plots
	default:
		return nil
	}
}

func (cs *ChartStudy) onData(packet map[string]any) {
	msgType, _ := packet["type"].(string)
	data, _ := packet["data"].([]any)

	switch msgType {
	case "study_completed":
		for _, fn := range cs.snapshotReady() {
			fn()
		}

	case "timescale_update", "du":
		if len(data) > 1 {
			if dataMap, ok := data[1].(map[string]any); ok {
				if cs.session != nil && cs.session.client != nil && cs.session.client.Debug() {
					keys := make([]string, 0, len(dataMap))
					for k := range dataMap {
						keys = append(keys, k)
					}
					log.Printf("[DEBUG] du keys: %v (study=%s)", keys, cs.studyID)
				}
				if studyData, ok := dataMap[cs.studyID].(map[string]any); ok {
					if cs.session != nil && cs.session.client != nil && cs.session.client.Debug() {
						skeys := make([]string, 0, len(studyData))
						for k := range studyData {
							skeys = append(skeys, k)
						}
						log.Printf("[DEBUG] study %s entry keys: %v", cs.studyID, skeys)
					}
					cs.processStudyData(studyData)
				}
			}
		}
		for _, fn := range cs.snapshotUpdate() {
			fn()
		}

	case "study_error":
		errMsg := "study error"
		if len(data) > 3 {
			if s, ok := data[3].(string); ok {
				errMsg = s
			}
		}
		if len(data) > 4 {
			errMsg = fmt.Sprintf("%s (details: %v)", errMsg, data[4])
		}
		for _, fn := range cs.snapshotError() {
			fn(fmt.Errorf(errMsg))
		}
	}
}

func (cs *ChartStudy) processStudyData(studyData map[string]any) {
	// Extract plot name mapping from indicator (if available)
	plotNames := cs.getPlotNames()

	// Process period data
	if stArr, ok := studyData["st"].([]any); ok {
		cs.periodsMu.Lock()
		for _, p := range stArr {
			pObj, ok := p.(map[string]any)
			if !ok {
				continue
			}
			vArr, ok := pObj["v"].([]any)
			if !ok || len(vArr) == 0 {
				continue
			}
			time, _ := vArr[0].(float64)
			period := map[string]any{"$time": time}
			for i := 1; i < len(vArr); i++ {
				plotIdx := i - 1
				plotKey := fmt.Sprintf("plot_%d", plotIdx)
				// Use named key from metadata if available
				if name, ok := plotNames[plotKey]; ok && name != "" {
					period[name] = vArr[i]
				}
				// Always store with plot_N key for backward compatibility
				period[plotKey] = vArr[i]
			}
			cs.periods[time] = period
		}
		cs.periodsMu.Unlock()
	}

	// Process graphics + strategy report
	if ns, ok := studyData["ns"].(map[string]any); ok {
		if cs.session != nil && cs.session.client != nil && cs.session.client.Debug() {
			keys := make([]string, 0, len(ns))
			for k := range ns {
				keys = append(keys, k)
			}
			log.Printf("[DEBUG] study %s ns keys: %v", cs.studyID, keys)
		}
		var inlineParsed map[string]any

		// Inline path: ns.d is a JSON string containing graphicsCmds and/or report.
		if d, ok := ns["d"].(string); ok {
			if err := json.Unmarshal([]byte(d), &inlineParsed); err == nil {
				if cs.session != nil && cs.session.client != nil && cs.session.client.Debug() {
					dkeys := make([]string, 0, len(inlineParsed))
					for k := range inlineParsed {
						dkeys = append(dkeys, k)
					}
					log.Printf("[DEBUG] study %s ns.d keys: %v (len=%d)", cs.studyID, dkeys, len(d))
				}
				if graphicsCmds, ok := inlineParsed["graphicsCmds"].(map[string]any); ok {
					cs.processGraphics(graphicsCmds)
				}
				cs.mergeStrategyReport(inlineParsed["report"])
				// Some payloads nest the report under `data`.
				if data, ok := inlineParsed["data"].(map[string]any); ok {
					cs.mergeStrategyReport(data["report"])
				}
				// dataCompressed at the top level of the parsed `d` JSON (strategy reports).
				if dataComp, ok := inlineParsed["dataCompressed"].(string); ok && dataComp != "" {
					if decomp, err := parseCompressed(dataComp); err == nil {
						if cs.session != nil && cs.session.client != nil && cs.session.client.Debug() {
							dkeys := make([]string, 0, len(decomp))
							for k := range decomp {
								dkeys = append(dkeys, k)
							}
							log.Printf("[DEBUG] study %s decompressed keys: %v", cs.studyID, dkeys)
						}
						cs.mergeStrategyReport(decomp["report"])
						// Some payloads decompress to the report directly (no
						// "report" wrapper): performance/trades at the top level.
						if _, hasPerf := decomp["performance"]; hasPerf {
							cs.mergeStrategyReport(decomp)
						}
						if graphicsCmds, ok := decomp["graphicsCmds"].(map[string]any); ok {
							cs.processGraphics(graphicsCmds)
						}
					} else if cs.session != nil && cs.session.client != nil && cs.session.client.Debug() {
						log.Printf("[DEBUG] inline dataCompressed parse error: %v", err)
					}
				}
			}
		}

		// Compressed path: ns.dCompressed is a base64/zlib/zip payload.
		if comp, ok := ns["dCompressed"].(string); ok && comp != "" {
			if parsed, err := parseCompressed(comp); err == nil {
				cs.mergeStrategyReport(parsed["report"])
				if graphicsCmds, ok := parsed["graphicsCmds"].(map[string]any); ok {
					cs.processGraphics(graphicsCmds)
				}
			} else if cs.session != nil && cs.session.client != nil && cs.session.client.Debug() {
				log.Printf("[DEBUG] compressed report: %v", err)
			}
		}

		// Fallback: dataCompressed nested inside the parsed `d` JSON.
		if inlineParsed != nil {
			if dataMap, ok := inlineParsed["data"].(map[string]any); ok {
				if dataComp, ok := dataMap["dataCompressed"].(string); ok && dataComp != "" {
					if decomp, err := parseCompressed(dataComp); err == nil {
						cs.mergeStrategyReport(decomp["report"])
						if graphicsCmds, ok := decomp["graphicsCmds"].(map[string]any); ok {
							cs.processGraphics(graphicsCmds)
						}
					}
				}
			}
		}
	}
}

// mergeStrategyReport merges a report object (currency, settings, performance,
// trades, equity) into cs.strategyReport. A nil report is a no-op. Matches
// the JS updateStrategyReport behavior in tv-optimized.cjs.
func (cs *ChartStudy) mergeStrategyReport(report any) {
	r, ok := report.(map[string]any)
	if !ok || r == nil {
		return
	}
	cs.graphicMu.Lock()
	defer cs.graphicMu.Unlock()
	if cs.strategyReport == nil {
		cs.strategyReport = map[string]any{}
	}
	for _, k := range []string{"currency", "settings", "performance", "trades"} {
		if v, ok := r[k]; ok && v != nil {
			cs.strategyReport[k] = v
		}
	}
	// Equity comes paired with buyHold/drawDown series — store them together.
	if _, ok := r["equity"]; ok {
		cs.strategyReport["history"] = map[string]any{
			"equity":          r["equity"],
			"equityPercent":   r["equityPercent"],
			"buyHold":         r["buyHold"],
			"buyHoldPercent":  r["buyHoldPercent"],
			"drawDown":        r["drawDown"],
			"drawDownPercent": r["drawDownPercent"],
		}
	}
}

func (cs *ChartStudy) processGraphics(cmds map[string]any) {
	cs.graphicMu.Lock()
	defer cs.graphicMu.Unlock()

	if erase, ok := cmds["erase"].([]any); ok {
		for _, instr := range erase {
			if instrMap, ok := instr.(map[string]any); ok {
				if instrMap["action"] == "all" {
					if t, ok := instrMap["type"].(string); ok {
						delete(cs.graphic, t)
					} else {
						cs.graphic = make(map[string]map[string]any)
					}
				}
			}
		}
	}

	if create, ok := cmds["create"].(map[string]any); ok {
		for drawType, groups := range create {
			if arr, ok := groups.([]any); ok {
				for _, group := range arr {
					if gMap, ok := group.(map[string]any); ok {
						if dataArr, ok := gMap["data"].([]any); ok {
							for _, item := range dataArr {
								if iMap, ok := item.(map[string]any); ok {
									id := graphicIDToString(iMap["id"])
									if id != "" {
										if cs.graphic[drawType] == nil {
											cs.graphic[drawType] = make(map[string]any)
										}
										cs.graphic[drawType][id] = iMap
									}
								}
							}
						}
					}
				}
			}
		}
	}
}

// graphicIDToString coerces TradingView graphic item IDs (which may be float64
// or int in the wire format) into string map keys, mirroring JS object-key
// coercion.
func graphicIDToString(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case float64:
		return strconv.FormatInt(int64(x), 10)
	case int:
		return strconv.Itoa(x)
	case int64:
		return strconv.FormatInt(x, 10)
	default:
		return ""
	}
}

func (cs *ChartStudy) Periods() []map[string]any {
	cs.periodsMu.RLock()
	defer cs.periodsMu.RUnlock()

	var result []map[string]any
	for _, p := range cs.periods {
		result = append(result, p)
	}
	sort.Slice(result, func(i, j int) bool {
		t1, _ := result[i]["$time"].(float64)
		t2, _ := result[j]["$time"].(float64)
		return t1 > t2
	})
	return result
}

func (cs *ChartStudy) Graphic() map[string]map[string]any {
	cs.graphicMu.RLock()
	defer cs.graphicMu.RUnlock()
	if cs.graphic == nil {
		return nil
	}
	out := make(map[string]map[string]any, len(cs.graphic))
	for k, v := range cs.graphic {
		inner := make(map[string]any, len(v))
		for id, item := range v {
			inner[id] = item
		}
		out[k] = inner
	}
	return out
}

func (cs *ChartStudy) StrategyReport() map[string]any {
	cs.graphicMu.RLock()
	defer cs.graphicMu.RUnlock()
	if cs.strategyReport == nil {
		return nil
	}
	out := make(map[string]any, len(cs.strategyReport))
	for k, v := range cs.strategyReport {
		out[k] = v
	}
	return out
}

func (cs *ChartStudy) OnUpdate(fn func()) {
	cs.mu.Lock()
	cs.onUpdate = append(cs.onUpdate, fn)
	cs.mu.Unlock()
}

func (cs *ChartStudy) OnError(fn func(error)) {
	cs.mu.Lock()
	cs.onError = append(cs.onError, fn)
	cs.mu.Unlock()
}

func (cs *ChartStudy) OnReady(fn func()) {
	cs.mu.Lock()
	cs.onReady = append(cs.onReady, fn)
	cs.mu.Unlock()
}

func (cs *ChartStudy) snapshotReady() []func() {
	cs.mu.Lock()
	out := append([]func(){}, cs.onReady...)
	cs.mu.Unlock()
	return out
}

func (cs *ChartStudy) snapshotUpdate() []func() {
	cs.mu.Lock()
	out := append([]func(){}, cs.onUpdate...)
	cs.mu.Unlock()
	return out
}

func (cs *ChartStudy) snapshotError() []func(error) {
	cs.mu.Lock()
	out := append([]func(error){}, cs.onError...)
	cs.mu.Unlock()
	return out
}

func (cs *ChartStudy) Remove() {
	cs.session.Send("remove_study", []any{cs.session.sessionID, cs.studyID})
	cs.session.unregisterStudy(cs.studyID)
	// 100ms flush so the server processes the removal before the caller
	// proceeds to chart cleanup or connection close.
	time.Sleep(100 * time.Millisecond)
}
