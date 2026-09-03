// Package server provides an HTTP server wrapping tvcli's core functions
// for AI agent integration. It exposes endpoints for compiling Pine scripts,
// fetching OHLCV data, running indicators, and cleaning up chart sessions.
package server

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/mrme000m/tvcli/pkg/account"
	"github.com/mrme000m/tvcli/internal/config"
	"github.com/mrme000m/tvcli/internal/service"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
	"github.com/mrme000m/tvcli/pkg/runner"
	"github.com/mrme000m/tvcli/pkg/schema"
	"github.com/mrme000m/tvcli/pkg/skill"
	_ "github.com/mrme000m/tvcli/pkg/skill/parsers" // register all skills via init()
	"github.com/mrme000m/tvcli/pkg/tradingview"
	"github.com/mrme000m/tvcli/pkg/tradingview/auth"
)

// Server wraps the tvcli core functions behind an HTTP API.
//
// Concurrency is tracked per account, not globally. TradingView limits each
// account to MaxIndicators concurrent studies, so we allow up to that many
// in-flight requests per account and rotate across accounts on account-scoped
// failures (expired cookies, study/connection limits, WS dial errors). This
// lets 20 valid accounts analyze 20 symbols simultaneously.
type Server struct {
	cfg      *config.Config
	pfClient *pinefacade.Client
	mux      *http.ServeMux

	// ── Per-account concurrency ──────────────────────────────────────
	// accountUsage[name] = number of in-flight WS requests currently using
	// that account. A request must acquire a slot (tryAcquireAccountSlot)
	// before opening a TradingView WS connection and release it
	// (releaseAccountSlot) when done. The per-account cap is the account's
	// tier MaxIndicators (free = 2).
	accountMu    sync.Mutex
	accountUsage map[string]int

	// ── Saved-script cache (/run) ────────────────────────────────────
	// /run clients (e.g. the faber0 signal-time gate) re-send the same Pine
	// source on every call. Compiling, saving a temp script, fetching its
	// metaInfo and deleting it again costs three Pine Facade round-trips per
	// request, so successfully saved scripts are kept and reused, keyed by
	// (account, source hash). Entries are only cached per account because a
	// private script saved under one account is invisible to the others.
	scriptCache   map[string]*scriptCacheEntry
	scriptCacheMu sync.Mutex
}

// scriptCacheEntry is a Pine script saved under one account, reusable across
// /run requests until it disappears server-side (then it is evicted and
// re-saved once transparently).
type scriptCacheEntry struct {
	account  string
	pineID   string
	metaInfo map[string]any
	lastUse  time.Time
}

// scriptCacheMaxEntries bounds the number of saved scripts kept per server
// process; eviction is LRU with a best-effort remote delete.
const scriptCacheMaxEntries = 32

// New creates a Server with the given config.
func New(cfg *config.Config) *Server {
	s := &Server{
		cfg:          cfg,
		pfClient:     pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, time.Duration(cfg.Timeout)*time.Millisecond, pinefacade.WithProxy(cfg.ProxyURL)),
		mux:          http.NewServeMux(),
		accountUsage: make(map[string]int),
		scriptCache:  make(map[string]*scriptCacheEntry),
	}
	s.registerRoutes()
	return s
}

// tryAcquireAccountSlot attempts to reserve a concurrency slot for the given
// account. It returns true (and increments the account's usage) if the account
// has not yet reached its per-account cap (tier MaxIndicators). The empty
// account name ("") is the single-account / legacy bucket.
func (s *Server) tryAcquireAccountSlot(accountName string) bool {
	s.accountMu.Lock()
	defer s.accountMu.Unlock()
	maxConcurrent := s.maxConcurrentFor(accountName)
	if s.accountUsage[accountName] >= maxConcurrent {
		return false
	}
	s.accountUsage[accountName]++
	return true
}

// releaseAccountSlot frees a slot previously reserved with
// tryAcquireAccountSlot. It is safe to call even if the slot was never taken.
func (s *Server) releaseAccountSlot(accountName string) {
	s.accountMu.Lock()
	defer s.accountMu.Unlock()
	if s.accountUsage[accountName] > 0 {
		s.accountUsage[accountName]--
	}
}

// maxConcurrentFor returns the per-account concurrency cap (tier
// MaxIndicators, defaulting to 2 for the free tier) for the named account.
func (s *Server) maxConcurrentFor(accountName string) int {
	limits := s.cfg.Limits()
	if accountName != "" && s.cfg.Accounts != nil {
		if acc, ok := s.cfg.Accounts.Get(accountName); ok {
			limits = acc.Limits()
		}
	}
	if limits.MaxIndicators <= 0 {
		return 2
	}
	return limits.MaxIndicators
}

// --- Saved-script cache (/run) ----------------------------------------------

func scriptCacheKey(account, source string) string {
	return account + "|" + pinefacade.SHA256(source)
}

func (s *Server) lookupScript(key string) (*scriptCacheEntry, bool) {
	s.scriptCacheMu.Lock()
	defer s.scriptCacheMu.Unlock()
	ent, ok := s.scriptCache[key]
	if ok {
		ent.lastUse = time.Now()
	}
	return ent, ok
}

// storeScript caches a saved script, evicting the least-recently-used entry
// (with a best-effort remote delete) when the cache is full.
func (s *Server) storeScript(key, account, pineID string, metaInfo map[string]any) {
	s.scriptCacheMu.Lock()
	if len(s.scriptCache) >= scriptCacheMaxEntries {
		var oldestKey string
		var oldest time.Time
		for k, e := range s.scriptCache {
			if oldestKey == "" || e.lastUse.Before(oldest) {
				oldestKey, oldest = k, e.lastUse
			}
		}
		if ent, ok := s.scriptCache[oldestKey]; ok {
			delete(s.scriptCache, oldestKey)
			go s.deleteCachedScript(ent)
		}
	}
	s.scriptCache[key] = &scriptCacheEntry{account: account, pineID: pineID, metaInfo: metaInfo, lastUse: time.Now()}
	s.scriptCacheMu.Unlock()
}

// evictScript drops a cache entry (e.g. the script vanished server-side) and
// best-effort deletes it from the owning account.
func (s *Server) evictScript(key string) {
	s.scriptCacheMu.Lock()
	ent, ok := s.scriptCache[key]
	if ok {
		delete(s.scriptCache, key)
	}
	s.scriptCacheMu.Unlock()
	if ok {
		go s.deleteCachedScript(ent)
	}
}

func (s *Server) deleteCachedScript(ent *scriptCacheEntry) {
	_, _ = s.pfClient.Delete(ent.pineID, cfgForAccount(s.cfg, ent.account).CookieHeaderOrEmpty())
}

func (s *Server) scriptCacheLen() int {
	s.scriptCacheMu.Lock()
	defer s.scriptCacheMu.Unlock()
	return len(s.scriptCache)
}

// isMissingScriptError reports whether err means the saved script is gone
// server-side (deleted on TradingView or invisible to this account), in which
// case a cached entry must be evicted and re-saved.
func isMissingScriptError(err error) bool {
	return err != nil && strings.Contains(err.Error(), "no source")
}

// saveScriptOnce compiles source, saves it as a temp script under the account
// and fetches its metaInfo. saved=false means TradingView refused the save and
// the returned pineID is the deterministic fallback — nothing to cache or
// delete. err is only ever a compile failure (request-scoped, 400 territory).
func (s *Server) saveScriptOnce(accCfg *config.Config, source string) (pineID string, metaInfo map[string]any, saved bool, err error) {
	if _, err = s.pfClient.Compile(source, accCfg.CookieHeaderOrEmpty()); err != nil {
		return "", nil, false, fmt.Errorf("compile: %w", err)
	}
	tempName := "agent_server_" + pinefacade.SHA256(source)[:12]
	saveResp, saveErr := s.pfClient.SaveNew(source, tempName, accCfg.CookieHeaderOrEmpty())
	if saveErr == nil {
		if pid := extractPineID(saveResp); pid != "" {
			if fetched, ferr := s.pfClient.Get(pid, "last", accCfg.CookieHeaderOrEmpty()); ferr == nil && fetched.MetaInfo != nil {
				metaInfo = fetched.MetaInfo
			}
			return pid, metaInfo, true, nil
		}
	}
	return "USER;eval" + pinefacade.SHA256(source)[:12], nil, false, nil
}

// resolveScript returns a runnable pineID for source under the given account,
// reusing a cached saved script when one exists. wasCached tells the caller
// the entry may be stale server-side (retry once via evict+resolve on a
// missing-script error).
func (s *Server) resolveScript(cacheKey, account string, accCfg *config.Config, source string) (pineID string, metaInfo map[string]any, wasCached bool, err error) {
	if ent, ok := s.lookupScript(cacheKey); ok {
		return ent.pineID, ent.metaInfo, true, nil
	}
	pineID, metaInfo, saved, err := s.saveScriptOnce(accCfg, source)
	if err != nil {
		return "", nil, false, err
	}
	if saved {
		s.storeScript(cacheKey, account, pineID, metaInfo)
	}
	return pineID, metaInfo, false, nil
}

// Handler returns the HTTP handler.
func (s *Server) Handler() http.Handler { return s.mux }

func (s *Server) registerRoutes() {
	s.mux.HandleFunc("/health", s.handleHealth)
	s.mux.HandleFunc("/compile", s.handleCompile)
	s.mux.HandleFunc("/fetch", s.handleFetch)
	s.mux.HandleFunc("/clean", s.handleClean)
	s.mux.HandleFunc("/run", s.handleRun)
	s.mux.HandleFunc("/run-skill", s.handleRunSkill)
	s.mux.HandleFunc("/hunt", s.handleHunt)
	s.mux.HandleFunc("/skills", s.handleSkills)
	s.mux.HandleFunc("/check-auth", s.handleCheckAuth)
	s.mux.HandleFunc("/accounts", s.handleAccounts)
	s.mux.HandleFunc("/queue-stats", s.handleQueueStats)
}

// --- Types ------------------------------------------------------------------

type compileRequest struct {
	Source string `json:"source"`
}

type compileResponse struct {
	Success       bool   `json:"success"`
	SourceHash    string `json:"sourceHash"`
	ErrorMessage  string `json:"error,omitempty"`
	CompileResult any    `json:"compileResult,omitempty"`
}

type fetchRequest struct {
	Symbol    string `json:"symbol"`
	Timeframe string `json:"timeframe"`
	Bars      int    `json:"bars"`
	To        int64  `json:"to"` // unix seconds: window ends here (0 = live)
}

type cleanRequest struct {
	Symbol     string `json:"symbol"`
	Iterations int    `json:"iterations"`
	DelayMs    int    `json:"delayMs"`
}

type runRequest struct {
	Source     string            `json:"source"`
	Symbol     string            `json:"symbol"`
	Timeframe  string            `json:"timeframe"`
	Bars       int               `json:"bars"`
	To         int64             `json:"to"` // unix seconds: window ends here (0 = live)
	Inputs     map[string]string `json:"inputs"`
	ForceClean bool              `json:"forceCleanup"`
}

// --- Handlers ---------------------------------------------------------------

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	// Include auth status for agent health checks.
	authed := false
	plan := ""
	if s.cfg.HasAuth() {
		info := auth.FetchAuthInfo(s.cfg.SessionID, s.cfg.Signature, "", s.cfg.DeviceToken, auth.WithProxy(s.cfg.ProxyURL))
		authed = info.Authenticated
		plan = info.Plan
	}
	limits := s.cfg.Limits()

	s.accountMu.Lock()
	usage := make(map[string]int, len(s.accountUsage))
	for k, v := range s.accountUsage {
		usage[k] = v
	}
	s.accountMu.Unlock()

	// Multi-account registry state (accounts.json sidecar). `accounts` is the
	// registry size (1 in legacy single-account mode with auth), `activeAccount`
	// is the account main.go resolved at startup (--account / TV_ACCOUNT / the
	// registry default), and `failoverMax` is the per-request failover ceiling
	// (TV_FAILOVER_MAX, default 4). The QD backend's tvcli_status() reads these
	// to report "degraded but failover-capable" when the active account is down.
	accounts := 0
	activeAccount := s.cfg.ActiveAccount
	if s.cfg.Accounts != nil {
		accounts = len(s.cfg.Accounts.Accounts)
	} else if s.cfg.HasAuth() {
		accounts = 1
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"status":          "ok",
		"tier":            s.cfg.TierName(),
		"authenticated":   authed,
		"plan":            plan,
		"user":            s.cfg.UserName,
		"endpoint":        s.cfg.PineFacadeURL,
		"maxIndicators":   limits.MaxIndicators,
		"maxBars":         limits.MaxBars,
		"calcTimeoutSecs": limits.CalcTimeoutSecs,
		"accounts":        accounts,
		"activeAccount":   activeAccount,
		"failoverMax":     failoverMax(),
		"accountUsage":    usage,
		"scriptCacheSize": s.scriptCacheLen(),
	})
}

func (s *Server) handleCompile(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "POST required"})
		return
	}
	var req compileRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON: " + err.Error()})
		return
	}
	if req.Source == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "source is required"})
		return
	}

	resp, err := s.pfClient.Compile(req.Source, s.cfg.CookieHeaderOrEmpty())
	if err != nil {
		writeJSON(w, http.StatusOK, compileResponse{
			Success:      false,
			SourceHash:   pinefacade.SHA256(req.Source),
			ErrorMessage: err.Error(),
		})
		return
	}

	success := true
	if m, ok := resp.(map[string]any); ok {
		if sv, ok := m["success"]; ok && sv == false {
			success = false
		}
	}
	writeJSON(w, http.StatusOK, compileResponse{
		Success:       success,
		SourceHash:    pinefacade.SHA256(req.Source),
		CompileResult: resp,
	})
}

// handleFetch fetches OHLCV bars for a symbol. It rotates across the account
// registry on account-scoped failures, respecting each account's per-account
// concurrency cap.
func (s *Server) handleFetch(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "POST required"})
		return
	}
	var req fetchRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON: " + err.Error()})
		return
	}
	if req.Symbol == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "symbol is required"})
		return
	}
	if req.Timeframe == "" {
		req.Timeframe = "5m"
	}
	if req.Bars == 0 {
		req.Bars = 500
	}

	symbol, err := pinefacade.ValidateSymbol(req.Symbol)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": fmt.Sprintf("invalid symbol: %v", err)})
		return
	}

	// Failover loop over candidate accounts.
	tried := 0
	for _, name := range candidateAccounts(s.cfg) {
		if tried >= failoverMax() {
			break
		}
		if !s.tryAcquireAccountSlot(name) {
			continue // at cap → try next account
		}
		tried++
		accCfg := cfgForAccount(s.cfg, name)
		limits := accCfg.Limits()
		bars := req.Bars
		if limits.MaxBars > 0 && bars > limits.MaxBars {
			bars = limits.MaxBars
		}

		client := tradingview.NewClient(
			tradingview.WithToken(accCfg.SessionID),
			tradingview.WithSignature(accCfg.Signature),
			tradingview.WithDeviceToken(accCfg.DeviceToken),
			tradingview.WithProxy(accCfg.ProxyURL),
			tradingview.WithDebug(accCfg.Debug),
		)
		if err := client.Connect(); err != nil {
			s.releaseAccountSlot(name)
			if isFailoverError(err) {
				continue
			}
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "ws connect: " + err.Error()})
			return
		}
		if !client.WaitForConnected(10 * time.Second) {
			client.Close()
			s.releaseAccountSlot(name)
			continue
		}

		chart := tradingview.NewChartSession(client)

		// Wait for the first data update before reading periods, matching
		// the service.FetchOHLCVBarsWithClient pattern. Without this, the
		// server's /fetch endpoint races the data arrival and returns 0 bars.
		done := make(chan struct{})
		once := sync.Once{}
		chart.OnUpdate(func() {
			once.Do(func() { close(done) })
		})

		marketOpts := map[string]any{
			"timeframe": pinefacade.NormalizeTimeframe(req.Timeframe),
			"range":     bars,
		}
		if req.To != 0 {
			marketOpts["to"] = req.To
		}
		chart.SetMarket(symbol, marketOpts)
		if err := chart.WaitForSymbol(15 * time.Second); err != nil {
			client.Close()
			s.releaseAccountSlot(name)
			if isFailoverError(err) {
				continue
			}
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "symbol load: " + err.Error()})
			return
		}

		select {
		case <-done:
		case <-time.After(30 * time.Second):
			client.Close()
			s.releaseAccountSlot(name)
			continue
		}
		// Brief settle to allow follow-up data to arrive.
		time.Sleep(500 * time.Millisecond)

		periods := chart.Periods()
		chart.RemoveAllStudies()
		chart.Delete()
		client.Close()
		s.releaseAccountSlot(name)

		writeJSON(w, http.StatusOK, map[string]any{
			"symbol":    symbol,
			"timeframe": req.Timeframe,
			"bars":      len(periods),
			"periods":   periods,
			"account":   name,
		})
		return
	}

	writeJSON(w, http.StatusTooManyRequests, map[string]any{
		"error":      "no account slot available or all candidate accounts failed",
		"retryAfter": 5,
	})
}

func (s *Server) handleClean(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "POST required"})
		return
	}
	var req cleanRequest
	_ = json.NewDecoder(r.Body).Decode(&req)
	if req.Iterations == 0 {
		req.Iterations = 5
	}
	if req.DelayMs == 0 {
		req.DelayMs = 300
	}
	if req.Symbol == "" {
		req.Symbol = "OANDA:XAUUSD"
	}

	// Cleanup targets the first available account (it flushes that account's
	// own stale chart sessions). It uses the same per-account concurrency cap.
	for _, name := range candidateAccounts(s.cfg) {
		if !s.tryAcquireAccountSlot(name) {
			continue
		}
		accCfg := cfgForAccount(s.cfg, name)

		client := tradingview.NewClient(
			tradingview.WithToken(accCfg.SessionID),
			tradingview.WithSignature(accCfg.Signature),
			tradingview.WithDeviceToken(accCfg.DeviceToken),
			tradingview.WithProxy(accCfg.ProxyURL),
			tradingview.WithDebug(accCfg.Debug),
		)
		if err := client.Connect(); err != nil {
			s.releaseAccountSlot(name)
			if isFailoverError(err) {
				continue
			}
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "ws connect: " + err.Error()})
			return
		}
		if !client.WaitForConnected(10 * time.Second) {
			client.Close()
			s.releaseAccountSlot(name)
			continue
		}

		sessionsCleaned := 0
		for i := 0; i < req.Iterations; i++ {
			chart := tradingview.NewChartSession(client)
			chart.SetMarket(req.Symbol, map[string]any{"timeframe": "1", "range": 1})
			_ = chart.WaitForSymbol(5 * time.Second)
			existing := chart.GetStudies()
			if len(existing) > 0 {
				chart.RemoveAllStudies()
			}
			chart.Delete()
			sessionsCleaned++
			time.Sleep(time.Duration(req.DelayMs) * time.Millisecond)
		}
		client.Close()
		s.releaseAccountSlot(name)

		writeJSON(w, http.StatusOK, map[string]any{
			"success":  true,
			"sessions": sessionsCleaned,
			"message":  fmt.Sprintf("Cleaned %d chart sessions", sessionsCleaned),
			"account":  name,
		})
		return
	}

	writeJSON(w, http.StatusTooManyRequests, map[string]any{
		"error":      "no account slot available for cleanup",
		"retryAfter": 5,
	})
}

func (s *Server) handleRun(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "POST required"})
		return
	}
	var req runRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON: " + err.Error()})
		return
	}
	if req.Source == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "source is required"})
		return
	}

	// Failover loop over candidate accounts.
	tried := 0
	for _, name := range candidateAccounts(s.cfg) {
		if tried >= failoverMax() {
			break
		}
		if !s.tryAcquireAccountSlot(name) {
			continue
		}
		tried++
		accCfg := cfgForAccount(s.cfg, name)

		// 1. Resolve symbol / timeframe / bars (request validation before any
		// facade work — a bad request must not compile/save anything).
		symbol := req.Symbol
		if symbol == "" {
			symbol = "OANDA:XAUUSD"
		}
		normalized, err := pinefacade.ValidateSymbol(symbol)
		if err != nil {
			s.releaseAccountSlot(name)
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": fmt.Sprintf("invalid symbol: %v", err)})
			return
		}
		symbol = normalized

		tf := req.Timeframe
		if tf == "" {
			tf = "5m"
		}

		limits := accCfg.Limits()
		bars := req.Bars
		if bars == 0 {
			bars = 500
		}
		if limits.MaxBars > 0 && bars > limits.MaxBars {
			bars = limits.MaxBars
		}

		// 2. Resolve the script, reusing the cached saved script for this
		// (account, source) when one exists — repeat /run callers skip the
		// compile+save+metaInfo+delete facade cycle entirely. Cache misses
		// compile (400 on syntax error), save, and cache.
		cacheKey := scriptCacheKey(name, req.Source)
		pineID, metaInfo, wasCached, err := s.resolveScript(cacheKey, name, accCfg, req.Source)
		if err != nil {
			s.releaseAccountSlot(name)
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}

		// 3. Pre-check auth before running (fail fast on expired cookies).
		if authInfo := auth.FetchAuthInfo(accCfg.SessionID, accCfg.Signature, "", accCfg.DeviceToken, auth.WithProxy(accCfg.ProxyURL)); !authInfo.Authenticated {
			s.releaseAccountSlot(name)
			writeJSON(w, http.StatusUnauthorized, map[string]any{
				"error":         "auth: cookies expired — re-extract SESSION/SIGNATURE/DEVICE_T",
				"authenticated": false,
				"canRunStudies": false,
			})
			return
		}

		// 4. Run via service.RunScript (fetches compiled IL from Pine Facade).
		runStudy := func(pineID string) (*service.RunResult, error) {
			return service.RunScript(context.Background(), accCfg, service.RunRequest{
				PineID:       pineID,
				Symbol:       symbol,
				Timeframe:    tf,
				Bars:         bars,
				ToTime:       req.To,
				Inputs:       req.Inputs,
				ReservedKeys: []string{"source", "symbol", "timeframe", "bars", "to", "inputs", "forceCleanup"},
				SettleMs:     1500,
				ForceCleanup: req.ForceClean,
				CalcTimeout:  time.Duration(limits.CalcTimeoutSecs) * time.Second,
				Debug:        accCfg.Debug,
			})
		}
		res, err := runStudy(pineID)
		if err != nil && wasCached && isMissingScriptError(err) {
			// Cached script vanished server-side — evict, re-save once, retry.
			s.evictScript(cacheKey)
			if pineID, metaInfo, _, err = s.resolveScript(cacheKey, name, accCfg, req.Source); err == nil {
				res, err = runStudy(pineID)
			}
		}
		if err != nil {
			s.releaseAccountSlot(name)
			if isFailoverError(err) {
				continue
			}
			writeJSON(w, http.StatusServiceUnavailable, map[string]any{
				"error":   err.Error(),
				"pineId":  pineID,
				"periods": nil,
			})
			return
		}

		// 5. Build schema and parse output.
		var sch *schema.PineSchema
		if metaInfo != nil {
			sch = schema.FromMetaInfo(pineID, metaInfo)
		}

		result := runner.ParseOutput(res.Periods, res.Graphic, res.StrategyReport, tf, pineID, sch)
		s.releaseAccountSlot(name)
		writeJSON(w, http.StatusOK, map[string]any{
			"status":    "ok",
			"pineId":    pineID,
			"symbol":    symbol,
			"timeframe": tf,
			"result":    result,
			"account":   name,
			"cached":    wasCached,
		})
		return
	}

	writeJSON(w, http.StatusTooManyRequests, map[string]any{
		"error":      "no account slot available or all candidate accounts failed",
		"retryAfter": 5,
	})
}

// lastPriceMissing returns true when the parser did not produce a price.
func lastPriceMissing(v any) bool {
	if v == nil {
		return true
	}
	switch p := v.(type) {
	case float64:
		return p == 0
	case int:
		return p == 0
	case string:
		return p == "" || p == "0" || p == "0.0"
	}
	return false
}

// isStudyLimitError reports whether an error is a TradingView study-limit
// rejection (account-level indicator/chart-session cap on the free tier).
func isStudyLimitError(err error) bool {
	if err == nil {
		return false
	}
	msg := err.Error()
	return strings.Contains(msg, "maximum number of studies") ||
		strings.Contains(msg, "too many") ||
		strings.Contains(msg, "study limit")
}

// roundPrice rounds a price to a reasonable number of decimals.
func roundPrice(f float64) float64 {
	if f == 0 {
		return 0
	}
	// Round to 5 decimal places.
	scale := 1e5
	return math.Round(f*scale) / scale
}

// runSkillRequest is the input to /run-skill — accepts a skill name instead of raw source.
type runSkillRequest struct {
	Skill     string            `json:"skill"`
	Symbol    string            `json:"symbol"`
	Timeframe string            `json:"timeframe"`
	Bars      int               `json:"bars"`
	To        int64             `json:"to"` // unix seconds: window ends here (0 = live)
	Inputs    map[string]string `json:"inputs"`
}

// handleRunSkill resolves a skill name to its PineID via the skill registry,
// runs it through the per-account failover loop, parses the output with the
// skill's parser, and returns the agent-formatted JSON.
//
// POST /run-skill { "skill": "smc", "symbol": "OANDA:XAUUSD", "timeframe": "1H", "bars": 180 }
// → 200 { ...agent-formatted skill output... }
// → 404 "skill not found: <name>"
// → 429 { "error": "no account slot available", "retryAfter": 5 }
// → 503 "study limit exceeded" / "account-scoped failure"
func (s *Server) handleRunSkill(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "POST required"})
		return
	}
	var req runSkillRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON: " + err.Error()})
		return
	}
	if req.Skill == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "skill is required"})
		return
	}

	// Resolve skill name → *Skill via the global registry.
	sk := skill.Get(req.Skill)
	if sk == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{
			"error": fmt.Sprintf("skill not found: %s (available: use /skills endpoint)", req.Skill),
		})
		return
	}

	// Symbol normalization + defaults.
	symbol := req.Symbol
	if symbol == "" {
		symbol = "OANDA:XAUUSD"
	}
	normalized, err := pinefacade.ValidateSymbol(symbol)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid symbol: " + err.Error()})
		return
	}
	symbol = normalized

	tf := req.Timeframe
	if tf == "" {
		tf = "5m"
	}

	bars := req.Bars
	if bars == 0 {
		bars = 180
	}

	// Ownership precheck: a private (USER;) skill script must still exist in
	// the current user's saved list, otherwise it will 401 ("no source")
	// mid-run. Skipped for skills that carry raw Source (no facade fetch).
	//
	// Private skills fail the same ownership precheck under every account, so
	// we skip failover for them and run against the first available account.
	if pinefacade.AccessFromPineID(sk.PineID) == "private" && sk.Source == "" {
		for _, name := range candidateAccounts(s.cfg) {
			if !s.tryAcquireAccountSlot(name) {
				continue
			}
			accCfg := cfgForAccount(s.cfg, name)
			if err := service.PreCheckScriptOwnership(accCfg, sk.PineID); err != nil {
				s.releaseAccountSlot(name)
				writeJSON(w, http.StatusNotFound, map[string]any{
					"error":   fmt.Sprintf("skill %s: %s", req.Skill, err.Error()),
					"code":    "script_not_owned",
					"skill":   req.Skill,
					"account": name,
					"symbol":  symbol,
				})
				return
			}
			// Run on this account (no failover for private skills).
			s.runSkillWithAccount(w, sk, req, symbol, tf, bars, name, accCfg)
			return
		}
		writeJSON(w, http.StatusTooManyRequests, map[string]any{
			"error":      "no account slot available for private skill",
			"retryAfter": 5,
		})
		return
	}

	// Public skills / skills with embedded Source: rotate across accounts.
	tried := 0
	for _, name := range candidateAccounts(s.cfg) {
		if tried >= failoverMax() {
			break
		}
		if !s.tryAcquireAccountSlot(name) {
			continue
		}
		tried++
		accCfg := cfgForAccount(s.cfg, name)

		// Embedded-source skills never resolve the PineID through the facade
		// (it's a label), so the ownership precheck is meaningless for them.
		if sk.Source == "" {
			if err := service.PreCheckScriptOwnership(accCfg, sk.PineID); err != nil {
				s.releaseAccountSlot(name)
				writeJSON(w, http.StatusNotFound, map[string]any{
					"error":   fmt.Sprintf("skill %s: %s", req.Skill, err.Error()),
					"code":    "script_not_owned",
					"skill":   req.Skill,
					"account": name,
					"symbol":  symbol,
				})
				return
			}
		}

		agentOut, err := s.runSkillCompute(sk, name, accCfg, req.Inputs, req.Skill, symbol, tf, bars, req.To)
		if err != nil {
			s.releaseAccountSlot(name)
			if isFailoverError(err) {
				continue
			}
			msg := err.Error()
			code := "generic"
			switch {
			case isStudyLimitError(err):
				code = "study_limit"
			case strings.Contains(msg, "timeout"):
				code = "timeout"
			case strings.Contains(msg, "auth") || strings.Contains(msg, "cookies"):
				code = "auth"
			case strings.Contains(msg, "symbol"):
				code = "symbol"
			}
			writeJSON(w, http.StatusServiceUnavailable, map[string]any{
				"error":  fmt.Sprintf("skill %s: %s", req.Skill, err.Error()),
				"code":   code,
				"skill":  req.Skill,
				"symbol": symbol,
			})
			return
		}
		agentOut.AgentContext.Account = name
		agentOut.Execution.Attempts = tried
		s.releaseAccountSlot(name)
		writeJSON(w, http.StatusOK, agentOut)
		return
	}

	writeJSON(w, http.StatusTooManyRequests, map[string]any{
		"error":      "no account slot available or all candidate accounts failed",
		"retryAfter": 5,
	})
}

// runSkillCompute runs a resolved skill against a specific account config and
// returns the agent-formatted result (or an error). It performs no HTTP
// writing and does not acquire/release the account slot — callers own the slot
// so the same logic can serve both the single-symbol /run-skill endpoint and
// the multi-symbol /hunt batch endpoint.
func (s *Server) runSkillCompute(sk *skill.Skill, accountName string, accCfg *config.Config, inputs map[string]string, skillName string, symbol, tf string, bars int, to int64) (skill.AgentResult, error) {
	limits := accCfg.Limits()
	accountBars := bars
	if limits.MaxBars > 0 && accountBars > limits.MaxBars {
		accountBars = limits.MaxBars
	}

	merged := make(map[string]string)
	for _, inp := range sk.Inputs {
		if inp.Default != nil {
			merged[inp.TVInputID] = fmt.Sprintf("%v", inp.Default)
		}
	}
	for k, v := range inputs {
		merged[k] = v
	}

	// Embedded-source skill: TradingView stores script source encrypted
	// (facade Get returns a key_cipher blob), so raw text cannot execute as a
	// study payload — save the source under the serving account (cached per
	// account+source hash) and run by the resulting real PineID, which also
	// delivers full metaInfo so runtime inputs map correctly.
	pineID := sk.PineID
	source := sk.Source
	wasCached := false
	cacheKey := ""
	if source != "" {
		cacheKey = scriptCacheKey(accountName, source)
		pid, _, cached, err := s.resolveScript(cacheKey, accountName, accCfg, source)
		if err != nil {
			return skill.AgentResult{}, err
		}
		pineID, source, wasCached = pid, "", cached
	}

	start := time.Now()
	runOnce := func(pineID, src string) (*service.RunResult, error) {
		return service.RunScript(context.Background(), accCfg, service.RunRequest{
			PineID:       pineID,
			Symbol:       symbol,
			Timeframe:    tf,
			Bars:         accountBars,
			ToTime:       to,
			Inputs:       merged,
			ReservedKeys: nil,
			SettleMs:     1500,
			CalcTimeout:  time.Duration(limits.CalcTimeoutSecs) * time.Second,
			Source:       src,
		})
	}
	res, err := runOnce(pineID, source)
	if err != nil && wasCached && isMissingScriptError(err) {
		// Cached script vanished server-side — evict, re-save once, retry.
		s.evictScript(cacheKey)
		if pid, _, _, rerr := s.resolveScript(cacheKey, accountName, accCfg, sk.Source); rerr == nil {
			pineID = pid
			res, err = runOnce(pineID, "")
		}
	}
	if err != nil {
		return skill.AgentResult{}, err
	}
	duration := time.Since(start)

	var result skill.SkillResult
	if sk.ParseWithSchema != nil && res.Indicator != nil && res.Indicator.Schema != nil {
		result = sk.ParseWithSchema(res.Periods, res.Graphic, res.Indicator.Schema, tf, symbol, nil)
	} else {
		result = sk.ParseOutput(res.Periods, res.Graphic, tf, symbol, nil)
	}
	if result.Status == "" {
		result.Status = "ok"
	}
	if result.Workflow == "" {
		result.Workflow = sk.Name
	}

	if result.Status == "ok" && lastPriceMissing(result.Market.LastPrice) {
		// Keep the fallback price anchored to the run's window (to=0 → live).
		if ohlcvBars, ferr := service.FetchOHLCVBarsToTime(accCfg, symbol, tf, 2, to); ferr == nil && len(ohlcvBars) > 0 {
			result.Market.LastPrice = roundPrice(ohlcvBars[len(ohlcvBars)-1].Close)
		}
	}

	return sk.ToAgent(result, symbol, tf, duration.Milliseconds()), nil
}

// runSkillWithAccount executes a skill run against a specific already-reserved
// account and writes the agent-formatted response. The slot must already be
// acquired by the caller and is released here.
func (s *Server) runSkillWithAccount(w http.ResponseWriter, sk *skill.Skill, req runSkillRequest, symbol, tf string, bars int, name string, accCfg *config.Config) {
	defer s.releaseAccountSlot(name)

	agentOut, err := s.runSkillCompute(sk, name, accCfg, req.Inputs, req.Skill, symbol, tf, bars, req.To)
	if err != nil {
		msg := err.Error()
		code := "generic"
		switch {
		case isStudyLimitError(err):
			code = "study_limit"
		case strings.Contains(msg, "timeout"):
			code = "timeout"
		case strings.Contains(msg, "auth") || strings.Contains(msg, "cookies"):
			code = "auth"
		case strings.Contains(msg, "symbol"):
			code = "symbol"
		}
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{
			"error":  fmt.Sprintf("skill %s: %s", req.Skill, err.Error()),
			"code":   code,
			"skill":  req.Skill,
			"symbol": symbol,
		})
		return
	}
	agentOut.AgentContext.Account = name
	agentOut.Execution.Attempts = 1
	writeJSON(w, http.StatusOK, agentOut)
}

// huntRequest is the input to POST /hunt — a batch skill run over many symbols
// fanned across the account pool (N accounts → N symbols in parallel).
type huntRequest struct {
	Skill          string            `json:"skill"`
	Timeframe      string            `json:"timeframe"`
	Bars           int               `json:"bars"`
	Symbols        []string          `json:"symbols"`
	Inputs         map[string]string `json:"inputs"`
	MaxAccounts    int               `json:"maxAccounts"`    // 0 = all candidate accounts
	ConcurrencyCap int               `json:"concurrencyCap"` // hard cap on total in-flight workers (0 = auto)
	AccountRole    string            `json:"accountRole"`    // prefer accounts with this role (adhoc/script/signal/core); "" = no preference
}

// huntSymbolResult is one symbol's outcome inside the /hunt response.
type huntSymbolResult struct {
	Symbol  string             `json:"symbol"`
	Account string             `json:"account,omitempty"`
	Ok      bool               `json:"ok"`
	Error   string             `json:"error,omitempty"`
	Result  *skill.AgentResult `json:"result,omitempty"`
}

// huntResponse aggregates the per-symbol outcomes of a /hunt batch.
type huntResponse struct {
	Status       string                      `json:"status"`
	Skill        string                      `json:"skill"`
	Timeframe    string                      `json:"timeframe"`
	Bars         int                         `json:"bars"`
	AccountPool  []string                    `json:"accountPool"`
	AccountsUsed []string                    `json:"accountsUsed"`
	Completed    int                         `json:"completed"`
	Failed       int                         `json:"failed"`
	Total        int                         `json:"total"`
	Symbols      map[string]huntSymbolResult `json:"symbols"`
	ElapsedMs    int64                       `json:"elapsedMs"`
}

// normalizeHuntSymbols validates + normalizes a /hunt symbol list, deduping
// symbols that collide after normalization (e.g. "binance:btcusdt" and
// "BINANCE:BTCUSDT", or a repeated symbol) so the batch never wastes account
// slots on the same symbol twice. Invalid entries are returned in `invalid`
// keyed by the original input; empty inputs are dropped.
func normalizeHuntSymbols(symbols []string) (valid []string, invalid map[string]string) {
	valid = make([]string, 0, len(symbols))
	invalid = map[string]string{}
	seen := make(map[string]bool, len(symbols))
	for _, sym := range symbols {
		raw := strings.TrimSpace(sym)
		if raw == "" {
			continue
		}
		norm, err := pinefacade.ValidateSymbol(raw)
		if err != nil {
			invalid[raw] = "invalid symbol: " + err.Error()
			continue
		}
		if seen[norm] {
			continue
		}
		seen[norm] = true
		valid = append(valid, norm)
	}
	return valid, invalid
}

// handleHunt runs one skill across many symbols in parallel, distributing the
// symbols across the account registry so each account runs up to its
// MaxIndicators concurrency cap at once. On an account-scoped failure (study/
// auth/connection limit) the symbol's slot is released and the next candidate
// account is tried, mirroring the single-symbol failover path.
//
// POST /hunt { "skill": "squeeze", "timeframe": "4H", "bars": 180,
//
//	"symbols": ["BINANCE:BTCUSDT", ...] }
//
// → 200 { "status":"ok", "completed":N, "failed":M, "accountsUsed":[...],
//
//	"symbols": { "BINANCE:BTCUSDT": {"ok":true,"account":"a1","result":{...}} } }
func (s *Server) handleHunt(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "POST required"})
		return
	}
	var req huntRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON: " + err.Error()})
		return
	}
	if req.Skill == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "skill is required"})
		return
	}
	sk := skill.Get(req.Skill)
	if sk == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": fmt.Sprintf("skill not found: %s", req.Skill)})
		return
	}
	// Private skills without embedded source cannot be owned by every account
	// in the pool, so reject the batch rather than waste N symbol runs.
	if pinefacade.AccessFromPineID(sk.PineID) == "private" && sk.Source == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"error": "private script cannot be hunted across the account pool; use a public skill or one with embedded Source",
			"code":  "private_skill",
		})
		return
	}

	// Normalize + validate symbols up front; invalid ones are reported as
	// failed without a TradingView round-trip. Duplicates after normalization
	// are deduped (see normalizeHuntSymbols).
	validSyms, invalid := normalizeHuntSymbols(req.Symbols)
	if len(validSyms) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "no valid symbols provided"})
		return
	}

	tf := req.Timeframe
	if tf == "" {
		tf = "4H"
	}
	bars := req.Bars
	if bars == 0 {
		bars = 180
	}

	accounts := candidateAccounts(s.cfg)
	// Role-based filtering: when accountRole is set, prefer accounts with
	// that role. If any accounts match the role, use only those; otherwise
	// fall back to the full pool (the role is a hint, not a hard gate).
	if req.AccountRole != "" && s.cfg.Accounts != nil {
		roleAccounts := s.cfg.Accounts.AccountsForRole(req.AccountRole)
		if len(roleAccounts) > 0 {
			// Keep the active account first if it has the requested role.
			activeFirst := []string{}
			if s.cfg.ActiveAccount != "" {
				for _, n := range roleAccounts {
					if n == s.cfg.ActiveAccount {
						activeFirst = append(activeFirst, n)
					}
				}
			}
			rest := []string{}
			for _, n := range roleAccounts {
				if n != s.cfg.ActiveAccount {
					rest = append(rest, n)
				}
			}
			accounts = append(activeFirst, rest...)
		}
	}
	if req.MaxAccounts > 0 && req.MaxAccounts < len(accounts) {
		accounts = accounts[:req.MaxAccounts]
	}

	// Total slot capacity across the chosen accounts bounds the worker count.
	totalCap := 0
	for _, name := range accounts {
		totalCap += s.maxConcurrentFor(name)
	}
	if totalCap < 1 {
		totalCap = 1
	}
	workers := totalCap
	if workers > len(validSyms) {
		workers = len(validSyms)
	}
	if req.ConcurrencyCap > 0 && workers > req.ConcurrencyCap {
		workers = req.ConcurrencyCap
	}
	if workers < 1 {
		workers = 1
	}

	start := time.Now()
	symCh := make(chan string)
	go func() {
		for _, sym := range validSyms {
			symCh <- sym
		}
		close(symCh)
	}()

	var (
		mu           sync.Mutex
		results      = make(map[string]huntSymbolResult, len(validSyms))
		accountsUsed = map[string]bool{}
		completed    int
		failed       int
		rr           uint64
		wg           sync.WaitGroup
	)

	runOne := func(symbol string) {
		maxAttempts := failoverMax()
		if maxAttempts < 1 {
			maxAttempts = 1
		}
		var lastErr error
		// Slot contention must not consume the failover budget: an account at
		// its per-account cap (tier MaxIndicators) is transiently busy while
		// other workers finish and release their slots. Only real
		// account-scoped errors (expired cookies, study limits, WS dial
		// failures) rotate the account and count toward maxAttempts. The probe
		// bound below keeps a pathological all-accounts-busy moment from
		// spinning forever while still giving a large pool ample chances to
		// find a free slot.
		maxProbes := len(accounts) * maxAttempts
		if maxProbes < maxAttempts {
			maxProbes = maxAttempts
		}
		attempts := 0
		for probes := 0; probes < maxProbes; probes++ {
			idx := int(atomic.AddUint64(&rr, 1)-1) % len(accounts)
			name := accounts[idx]
			if !s.tryAcquireAccountSlot(name) {
				continue // at cap → rotate; contention is transient
			}
			accCfg := cfgForAccount(s.cfg, name)
			res, err := s.runSkillCompute(sk, name, accCfg, req.Inputs, req.Skill, symbol, tf, bars, 0)
			s.releaseAccountSlot(name)
			if err != nil {
				lastErr = err
				if isFailoverError(err) {
					attempts++
					if attempts >= maxAttempts {
						break
					}
					continue // account-scoped → rotate to a fresh account
				}
				// request-scoped error: no account will fix it
				mu.Lock()
				results[symbol] = huntSymbolResult{Symbol: symbol, Ok: false, Error: err.Error()}
				failed++
				mu.Unlock()
				return
			}
			mu.Lock()
			res.AgentContext.Account = name
			results[symbol] = huntSymbolResult{Symbol: symbol, Account: name, Ok: true, Result: &res}
			completed++
			accountsUsed[name] = true
			mu.Unlock()
			return
		}
		mu.Lock()
		msg := "no account slot available or all candidate accounts failed"
		if lastErr != nil {
			msg = lastErr.Error()
		}
		results[symbol] = huntSymbolResult{Symbol: symbol, Ok: false, Error: msg}
		failed++
		mu.Unlock()
	}

	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for symbol := range symCh {
				runOne(symbol)
			}
		}()
	}
	wg.Wait()

	for sym, reason := range invalid {
		mu.Lock()
		results[sym] = huntSymbolResult{Symbol: sym, Ok: false, Error: reason}
		failed++
		mu.Unlock()
	}

	used := make([]string, 0, len(accountsUsed))
	for _, a := range accounts {
		if accountsUsed[a] {
			used = append(used, a)
		}
	}

	writeJSON(w, http.StatusOK, huntResponse{
		Status:       "ok",
		Skill:        req.Skill,
		Timeframe:    tf,
		Bars:         bars,
		AccountPool:  accounts,
		AccountsUsed: used,
		Completed:    completed,
		Failed:       failed,
		Total:        len(validSyms) + len(invalid),
		Symbols:      results,
		ElapsedMs:    time.Since(start).Milliseconds(),
	})
}

// extractPineID tries to find a pineId in a SaveNew response.
// Matches the cmd.ExtractPineID logic: checks pineId, id, scriptIdPart at
// top level, inside "response", and inside "result.metaInfo.scriptIdPart".
func extractPineID(resp any) string {
	m, ok := resp.(map[string]any)
	if !ok {
		return ""
	}
	// Top-level keys with semicolons.
	for _, key := range []string{"pineId", "id", "scriptIdPart"} {
		if s, ok := m[key].(string); ok && strings.Contains(s, ";") {
			return strings.ReplaceAll(s, "%3B", ";")
		}
	}
	// Nested "response" object.
	if inner, ok := m["response"].(map[string]any); ok {
		for _, key := range []string{"pineId", "id", "scriptIdPart"} {
			if s, ok := inner[key].(string); ok && strings.Contains(s, ";") {
				return strings.ReplaceAll(s, "%3B", ";")
			}
		}
	}
	// Nested "result.metaInfo.scriptIdPart".
	if result, ok := m["result"].(map[string]any); ok {
		if mi, ok := result["metaInfo"].(map[string]any); ok {
			if s, ok := mi["scriptIdPart"].(string); ok && s != "" {
				if strings.Contains(s, ";") {
					return strings.ReplaceAll(s, "%3B", ";")
				}
				return "USER;" + strings.ReplaceAll(s, "%3B", ";")
			}
		}
	}
	return ""
}

// --- Helpers ----------------------------------------------------------------

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

// Serve starts the HTTP server on the given address.
func (s *Server) Serve(addr string) error {
	fmt.Fprintf(os.Stderr, "tvcli server listening on %s\n", addr)
	srv := &http.Server{
		Addr:              addr,
		Handler:           s.mux,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       60 * time.Second,
		IdleTimeout:       120 * time.Second,
		// WriteTimeout is left unset (0): /hunt fans out across the account
		// pool and a full sweep can legitimately take minutes.
	}
	return srv.ListenAndServe()
}

// ParseAddr extracts the address from the --addr flag, defaulting to :8765.
func ParseAddr(flags map[string]string) string {
	addr := flags["addr"]
	if addr == "" {
		addr = ":8765"
	}
	if !strings.HasPrefix(addr, ":") && !strings.Contains(addr, ":") {
		addr = ":" + addr
	}
	return addr
}

// handleSkills lists the registered skill names (for /run-skill discovery).
// GET /skills → { "skills": ["smc", ...], "count": N }
func (s *Server) handleSkills(w http.ResponseWriter, r *http.Request) {
	names := make([]string, 0)
	for _, sk := range skill.All() {
		if sk != nil && sk.Name != "" {
			names = append(names, sk.Name)
		}
	}
	sort.Strings(names)
	writeJSON(w, http.StatusOK, map[string]any{
		"skills": names,
		"count":  len(names),
	})
}

// handleCheckAuth verifies TradingView auth cookies and subscription tier via
// the cookies-only JSON profile APIs (no HTML scrape).
//
//   GET /check-auth            → { "count", "results": [ { name, configured,
//                                 authenticated, pro, plan, username, statusCode,
//                                 canRunStudies, error }, ... ] }
//   GET /check-auth?account=N  → single-account result (same shape, no "count")
//
// With a registry loaded and no ?account=, every registry account is validated
// in one pass (batch). With ?account=NAME, only that account is probed.
func (s *Server) handleCheckAuth(w http.ResponseWriter, r *http.Request) {
	if name := r.URL.Query().Get("account"); name != "" {
		if s.cfg.Accounts == nil {
			writeJSON(w, http.StatusNotFound, map[string]any{
				"configured":    false,
				"authenticated": false,
				"canRunStudies": false,
				"error":         "no accounts registry loaded",
			})
			return
		}
		acc, ok := s.cfg.Accounts.Get(name)
		if !ok {
			writeJSON(w, http.StatusNotFound, map[string]any{
				"configured":    false,
				"authenticated": false,
				"canRunStudies": false,
				"error":         fmt.Sprintf("account %q not found", name),
			})
			return
		}
		writeJSON(w, http.StatusOK, accountAuthResult(name, acc))
		return
	}
	// No ?account= — batch the whole registry when available.
	if s.cfg.Accounts == nil {
		writeJSON(w, http.StatusOK, accountAuthResult("", legacyAccount(s.cfg)))
		return
	}
	reg := s.cfg.Accounts
	names := reg.Names()
	results := make([]map[string]any, 0, len(names))
	for _, name := range names {
		results = append(results, accountAuthResult(name, reg.Accounts[name]))
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"count":   len(results),
		"results": results,
	})
}

// legacyAccount synthesizes a one-entry account from the legacy single-account
// config fields (SESSION/SIGNATURE/DEVICE_T/COOKIES/EXTRA_COOKIES/PROXY), so
// the batch validator can treat both modes uniformly.
func legacyAccount(cfg *config.Config) account.Account {
	return account.Account{
		SessionID:    cfg.SessionID,
		Signature:    cfg.Signature,
		DeviceToken:  cfg.DeviceToken,
		Cookies:      cfg.Cookies,
		ExtraCookies: cfg.ExtraCookies,
		ProxyURL:     cfg.ProxyURL,
	}
}

// accountAuthResult validates one account's cookies against the JSON profile
// APIs and returns its auth state as a JSON-ready map. username comes from the
// API response, not the stored UserName field.
func accountAuthResult(name string, acc account.Account) map[string]any {
	configured := acc.HasAuth()
	if !configured {
		return map[string]any{
			"name":          name,
			"configured":    false,
			"authenticated": false,
			"pro":           false,
			"plan":          "",
			"canRunStudies": false,
			"error":         "no SESSION cookie configured",
		}
	}
	info := auth.FetchAccountStateWithCookies(acc.CookieHeader(), auth.WithProxy(acc.ProxyURL))
	result := map[string]any{
		"name":          name,
		"configured":    true,
		"authenticated": info.Authenticated,
		"pro":           info.Pro,
		"plan":          info.Plan,
		"canRunStudies": info.Authenticated,
	}
	if info.StatusCode != 0 {
		result["statusCode"] = info.StatusCode
	}
	if info.Username != "" {
		result["username"] = info.Username
	}
	if info.Error != nil {
		result["error"] = info.Error.Error()
	}
	return result
}

// handleAccounts lists the account registry masked (no credentials). Mirrors the
// `tvcli account list` output so the QD backend and operators can see the pool
// shape — role, tier, username, auth/proxy presence, and default/active flags.
// GET /accounts → { "default", "active", "count", "accounts": [ ... ] }
func (s *Server) handleAccounts(w http.ResponseWriter, r *http.Request) {
	if s.cfg.Accounts == nil {
		writeJSON(w, http.StatusOK, map[string]any{
			"default":  "",
			"active":   "",
			"count":    0,
			"accounts": []any{},
		})
		return
	}
	reg := s.cfg.Accounts
	names := reg.Names()
	rows := make([]map[string]any, 0, len(names))
	for _, name := range names {
		a := reg.Accounts[name]
		rows = append(rows, map[string]any{
			"name":     name,
			"role":     a.Role,
			"tier":     a.Tier,
			"username": a.UserName,
			"hasAuth":  a.HasAuth(),
			"hasProxy": a.ProxyURL != "",
			"default":  name == reg.Default,
			"active":   name == s.cfg.ActiveAccount,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"default":  reg.Default,
		"active":   s.cfg.ActiveAccount,
		"count":    len(rows),
		"accounts": rows,
	})
}

// handleQueueStats returns the current per-account concurrency usage.
// GET /queue-stats → { "accountUsage": { "acc1": 1, "acc2": 0 } }
func (s *Server) handleQueueStats(w http.ResponseWriter, r *http.Request) {
	s.accountMu.Lock()
	usage := make(map[string]int, len(s.accountUsage))
	for k, v := range s.accountUsage {
		usage[k] = v
	}
	s.accountMu.Unlock()
	writeJSON(w, http.StatusOK, map[string]any{
		"accountUsage": usage,
	})
}
