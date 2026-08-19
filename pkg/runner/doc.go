// Package runner orchestrates Pine study execution over a TradingView
// WebSocket connection and parses the results into agent-ready output.
//
// It offers:
//
//	runner.RunOnceOptions / PersistentRunner — long-lived WS reuse across runs
//	runner.ParseOutput                      — schema-guided parsing + signal
//	                                          extraction + strategy metrics
//	runner.GenerateRunConfigs / AnalyzeSensitivity — input-sweep analysis
//
// The credential surface is []tradingview.ClientOption, so a runner is bound
// to whatever account the caller supplies (see pkg/account). For the
// CLI-level orchestration with retry-on-study-limit and chart cleanup, see
// the app's internal/service package.
package runner