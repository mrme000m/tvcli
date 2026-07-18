package tradingview

import (
	"encoding/json"
	"fmt"
	"sort"
	"sync"
)

type ChartStudy struct {
	session         *ChartSession
	studyID         string
	indicator       any
	periods         map[float64]map[string]any
	periodsMu       sync.RWMutex
	graphic         map[string]map[string]any
	strategyReport  map[string]any
	onUpdate        []func()
	onError         []func(error)
	onReady         []func()
}

func NewChartStudy(session *ChartSession, indicator any) *ChartStudy {
	cs := &ChartStudy{
		session:    session,
		studyID:    genSessionID("st"),
		indicator:  indicator,
		periods:    make(map[float64]map[string]any),
		graphic:    make(map[string]map[string]any),
	}

	session.studyListeners[cs.studyID] = cs.onData

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

func (cs *ChartStudy) onData(packet map[string]any) {
	msgType, _ := packet["type"].(string)
	data, _ := packet["data"].([]any)

	switch msgType {
	case "study_completed":
		for _, fn := range cs.onReady {
			fn()
		}

	case "timescale_update", "du":
		if len(data) > 1 {
			if dataMap, ok := data[1].(map[string]any); ok {
				if studyData, ok := dataMap[cs.studyID].(map[string]any); ok {
					cs.processStudyData(studyData)
				}
			}
		}
		for _, fn := range cs.onUpdate {
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
		for _, fn := range cs.onError {
			fn(fmt.Errorf(errMsg))
		}
	}
}

func (cs *ChartStudy) processStudyData(studyData map[string]any) {
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
				key := fmt.Sprintf("plot_%d", i-1)
				period[key] = vArr[i]
			}
			cs.periods[time] = period
		}
		cs.periodsMu.Unlock()
	}

	// Process graphics
	if ns, ok := studyData["ns"].(map[string]any); ok {
		if d, ok := ns["d"].(string); ok {
			var parsed map[string]any
			if err := json.Unmarshal([]byte(d), &parsed); err == nil {
				if graphicsCmds, ok := parsed["graphicsCmds"].(map[string]any); ok {
					cs.processGraphics(graphicsCmds)
				}
				if report, ok := parsed["report"].(map[string]any); ok {
					cs.strategyReport = report
				}
			}
		}
	}
}

func (cs *ChartStudy) processGraphics(cmds map[string]any) {
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
									id, _ := iMap["id"].(string)
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
	return cs.graphic
}

func (cs *ChartStudy) StrategyReport() map[string]any {
	return cs.strategyReport
}

func (cs *ChartStudy) OnUpdate(fn func()) {
	cs.onUpdate = append(cs.onUpdate, fn)
}

func (cs *ChartStudy) OnError(fn func(error)) {
	cs.onError = append(cs.onError, fn)
}

func (cs *ChartStudy) OnReady(fn func()) {
	cs.onReady = append(cs.onReady, fn)
}

func (cs *ChartStudy) Remove() {
	cs.session.Send("remove_study", []any{cs.session.sessionID, cs.studyID})
	delete(cs.session.studyListeners, cs.studyID)
}
