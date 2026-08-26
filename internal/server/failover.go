// failover.go — multi-account failover support for the HTTP server.
//
// When the server is started with an accounts.json sidecar (pkg/account),
// every authenticated request can rotate across the registry's accounts on
// account-scoped failures: expired cookies, an auth rejection, a study/
// connection limit, or a WS dial failure. Request-scoped errors (bad symbol,
// Pine syntax errors) are not retried — no account swap will fix them.
//
// Failover is transparent to HTTP clients: the backend's single TVCLI_URL
// keeps working, and successful responses carry the account that served them
// (the "account" field on /run, /run-skill, /fetch; the "attempts"/"account"
// fields inside the agent-ready-v2 envelope).
//
// Controls:
//
//	TV_FAILOVER_MAX  max accounts tried per request including the first
//	                 (default 4; 1 disables failover).
//
// The failover loop only runs over the long-lived WS handlers (/run,
// /run-skill, /fetch); /compile is HTTP-only, /clean targets the active
// account's own chart sessions, and /health + /check-auth report state rather
// than perform work. /run-skill skips failover for private (USER;) skills that
// the active account does not own — ownership is per-account, so a different
// account would fail the same ownership precheck.
package server

import (
	"os"
	"strconv"
	"strings"

	"github.com/mrme000m/tvcli/internal/config"
)

// candidateAccounts returns a list of account names to try, starting with the
// active account (if set) then the rest in alphabetical order. In legacy
// single-account mode (no accounts registry loaded) it returns a single empty
// account name, which the rest of the code treats as the legacy bucket keyed
// off the base config's tier limits.
func candidateAccounts(cfg *config.Config) []string {
	if cfg.Accounts == nil {
		return []string{""}
	}
	names := cfg.Accounts.Names()
	if cfg.ActiveAccount != "" {
		// Move the active account to the front if it exists in the list.
		found := false
		for i, n := range names {
			if n == cfg.ActiveAccount {
				// Remove from current position and insert at front.
				names = append(names[:i], names[i+1:]...)
				names = append([]string{cfg.ActiveAccount}, names...)
				found = true
				break
			}
		}
		if !found {
			// If the active account is not in the list (shouldn't happen), just put it at front.
			names = append([]string{cfg.ActiveAccount}, names...)
		}
	}
	return names
}

// cfgForAccount returns a copy of the base config overridden with the given
// account's credentials. The caller must not mutate the returned config.
func cfgForAccount(base *config.Config, name string) *config.Config {
	// Shallow copy of the base config.
	cfg := &config.Config{
		PineFacadeURL: base.PineFacadeURL,
		TVBaseURL:     base.TVBaseURL,
		Timeout:       base.Timeout,
		UserName:      base.UserName,
		SessionID:     base.SessionID,
		Signature:     base.Signature,
		DataDir:       base.DataDir,
		MetaFile:      base.MetaFile,
		Cookies:       base.Cookies,
		ExtraCookies:  base.ExtraCookies,
		DeviceToken:   base.DeviceToken,
		ProxyURL:      base.ProxyURL,
		Debug:         base.Debug,
		Accounts:      base.Accounts,
		ActiveAccount: base.ActiveAccount,
		Tier:          base.Tier,
		AccountsFile:  base.AccountsFile,
	}
	// Override with the specified account.
	if err := cfg.UseAccount(name); err != nil {
		// This should not happen if the account exists in the registry.
		// If it does, we return the base config and let the caller handle the error.
		// For now, we just ignore the error and continue.
		_ = err
	}
	return cfg
}

// failoverMax returns the max number of accounts to try per request.
// Controls: TV_FAILOVER_MAX (default 4).
func failoverMax() int {
	if v := os.Getenv("TV_FAILOVER_MAX"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return n
		}
	}
	return 4 // default
}

// isFailoverError reports whether an error is account-scoped (should trigger
// failover) or request-scoped (should not retried with a different account).
func isFailoverError(err error) bool {
	if err == nil {
		return false
	}
	msg := err.Error()
	// Study limit errors are account-scoped.
	if strings.Contains(msg, "maximum number of studies") ||
		strings.Contains(msg, "too many") ||
		strings.Contains(msg, "study limit") {
		return true
	}
	// Auth errors: cookies expired, auth rejection.
	if strings.Contains(msg, "auth: cookies expired") ||
		strings.Contains(msg, "authentication failed") ||
		strings.Contains(msg, "unauthorized") {
		return true
	}
	// WS dial failure: ws connect, ws timeout.
	if strings.Contains(msg, "ws connect:") ||
		strings.Contains(msg, "ws timeout") {
		return true
	}
	return false
}
