package tradingview

import (
	"fmt"
	"sync"
	"testing"
)

// These tests exercise the cross-goroutine sharing that a multi-account hunt
// actually produces: the WS readLoop dispatches onData while the request
// goroutine registers/removes studies, reads infos, and closes the client.
// Before the locking pass, concurrent map read+write on studyListeners /
// infos / sessions was a fatal runtime panic under `go test -race`.

func TestChartSessionConcurrentAccess(t *testing.T) {
	cl := NewClient() // nil conn: Send is a safe no-op
	cs := NewChartSession(cl)

	var wg sync.WaitGroup
	for g := 0; g < 4; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			for i := 0; i < 100; i++ {
				cs.Study(nil) // registerStudy via NewChartStudy
				cs.setInfos(map[string]any{"n": i})
				cs.OnSymbolLoaded(func() {})
				cs.OnSeriesCompleted(func() {})
				cs.OnUpdate(func() {})
				cs.OnError(func(error) {})
			}
		}(g)
	}
	for g := 0; g < 4; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			for i := 0; i < 100; i++ {
				_ = cs.GetStudies()
				_ = cs.GetSymbolInfo()
				_ = cs.snapshotSymbolLoaded()
				_ = cs.snapshotSeriesCompleted()
				_ = cs.snapshotUpdate()
				_ = cs.snapshotError()
				cs.RemoveStudy(fmt.Sprintf("st-%d", i))
				cs.unregisterStudy(fmt.Sprintf("st-%d", i))
				if fn, ok := cs.lookupStudy("nope"); ok {
					fn(nil)
				}
				// Dispatch side: symbol/price updates + callbacks.
				cs.onData(map[string]any{"type": "symbol_resolved", "data": []any{"s", map[string]any{"ok": true}}})
				cs.onData(map[string]any{"type": "series_completed", "data": []any{"s"}})
				cs.onData(map[string]any{"type": "timescale_update", "data": []any{"s", map[string]any{}}})
			}
		}(g)
	}
	wg.Wait()
}

func TestWSClientConcurrentSessionsAndClose(t *testing.T) {
	c := NewClient().(*WSClient)

	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		c.Close() // snapshots sessions under lock; nil conn => delete sends no-op
	}()
	for g := 0; g < 4; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			for i := 0; i < 200; i++ {
				id := fmt.Sprintf("cs-%d-%d", g, i)
				c.RegisterSession(id, "chart", func(map[string]any) {})
				c.UnregisterSession(id)
			}
		}(g)
	}
	wg.Wait()
}
