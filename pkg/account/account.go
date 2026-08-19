// Package account defines TradingView account credentials as a typed,
// importable registry. It is the multi-account foundation of tvcli: a single
// legacy account (SESSION/SIGNATURE/TV_USER/DEVICE_T/TV_TIER) is synthesized
// into a one-entry registry, so single-account programs work unchanged, while
// programs that need more headroom (e.g. several free-tier accounts each
// holding a long-lived study) can load N accounts and route work between them.
//
// The package is intentionally transport-agnostic: it only models credentials
// and per-account tier limits. Connection pooling, role-based routing, and
// per-account queues are application concerns built on top of this registry
// (see docs/MULTI_ACCOUNT.md in the repo root for the full design).
package account

import (
	"sort"
	"strings"
)

// Role constants are advisory routing hints. They do not gate behavior; they
// let a router map an intent ("run the xau-scalp script", "search public
// scripts", "one-off sweep") to the account configured for that role.
const (
	RoleCore   = "core"   // HTTP-only work: search/list/compile public scripts
	RoleScript = "script" // long-lived study for one dedicated custom script
	RoleSignal = "signal" // long-lived study that extracts signals over time
	RoleAdhoc  = "adhoc"  // short-lived one-off runs, round-robined
)

// Account is one TradingView user's credentials plus its subscription tier.
// All credential fields are optional except SessionID; an account without a
// session is anonymous (fetch-only) and will fail study creation.
type Account struct {
	// Name is the human label used as the registry key, e.g. "core",
	// "xau-scalp", "signal-runner", "adhoc-1".
	Name string
	// Role is an advisory routing hint: "core" | "script" | "signal" | "adhoc".
	Role string
	// SessionID is the sessionid cookie value.
	SessionID string
	// Signature is the sessionid_sign cookie value (optional but recommended).
	Signature string
	// DeviceToken is the device_t cookie value. Required by TradingView for
	// proper authentication; without it the auth_token fetch fails and the WS
	// client falls back to an unauthorized token (0 studies).
	DeviceToken string
	// UserName is the TradingView username (TV_USER). Used by the Pine Facade
	// HTTP API for private-script ownership checks.
	UserName string
	// Tier is the subscription tier name: "free" (default), "essential",
	// "plus", "premium", "ultimate". Empty means "free".
	Tier string
	// Cookies is a full raw Cookie header override. When set, it takes
	// precedence over SessionID/Signature/DeviceToken when building the
	// Cookie header.
	Cookies string
	// ExtraCookies holds additional raw cookie pairs appended to the built
	// header (e.g. "theme=dark; locale=en").
	ExtraCookies string
}

// HasAuth reports whether the account carries a session cookie.
func (a Account) HasAuth() bool { return a.SessionID != "" || a.Cookies != "" }

// CookieHeader builds the Cookie header for TradingView API requests, with the
// same priority as the legacy config: full Cookies override > built from
// session/signature/device_t. Returns "" for an anonymous account.
func (a Account) CookieHeader() string {
	if a.Cookies != "" {
		return a.Cookies
	}
	if a.SessionID == "" {
		return ""
	}
	var parts []string
	parts = append(parts, "sessionid="+a.SessionID)
	if a.Signature != "" {
		parts = append(parts, "sessionid_sign="+a.Signature)
	}
	if a.DeviceToken != "" {
		parts = append(parts, "device_t="+a.DeviceToken)
	}
	if a.ExtraCookies != "" {
		parts = append(parts, a.ExtraCookies)
	}
	return strings.Join(parts, "; ")
}

// Limits returns the tier resource caps for this account. Unknown or empty
// tiers resolve to the free tier, matching the legacy TV_TIER behavior.
func (a Account) Limits() TierLimits { return LimitsForTier(a.Tier) }

// TierLimits holds subscription-tier resource caps (from tradingview.com/pricing/).
type TierLimits struct {
	MaxCharts       int // charts per tab
	MaxIndicators   int // indicators per chart
	MaxConnections  int // simultaneous WebSocket connections
	MaxBars         int // historical bars (minute); 0 = unlimited
	CalcTimeoutSecs int // calculation time limit
}

var tiers = map[string]TierLimits{
	"free":      {1, 2, 2, 180, 20},
	"essential": {2, 5, 10, 365, 40},
	"plus":      {4, 10, 20, 0, 40},  // 0 = unlimited
	"premium":   {8, 25, 50, 0, 40},
	"ultimate":  {16, 50, 200, 0, 100},
}

// LimitsForTier returns the TierLimits for a tier name. Empty and unknown
// names resolve to "free", mirroring the legacy TV_TIER default.
func LimitsForTier(tier string) TierLimits {
	if tier == "" {
		tier = "free"
	}
	if l, ok := tiers[tier]; ok {
		return l
	}
	return tiers["free"]
}

// Registry is an ordered set of named accounts plus a default.
type Registry struct {
	// Default is the name of the account used when none is specified.
	// Always set: legacy single-account loads synthesize Name "default".
	Default string
	// Accounts maps account name -> account.
	Accounts map[string]Account
}

// NewRegistry returns an empty registry.
func NewRegistry() *Registry {
	return &Registry{Accounts: make(map[string]Account)}
}

// Get returns the account by name.
func (r *Registry) Get(name string) (Account, bool) {
	a, ok := r.Accounts[name]
	return a, ok
}

// DefaultAccount returns the default account. If no default is set, the first
// account in map iteration order is used; an empty registry yields a zero
// Account.
func (r *Registry) DefaultAccount() Account {
	if r.Default != "" {
		if a, ok := r.Accounts[r.Default]; ok {
			return a
		}
	}
	for _, a := range r.Accounts {
		return a
	}
	return Account{}
}

// Names returns all account names sorted alphabetically.
func (r *Registry) Names() []string {
	names := make([]string, 0, len(r.Accounts))
	for name := range r.Accounts {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// AccountsForRole returns the names of accounts carrying the given role.
func (r *Registry) AccountsForRole(role string) []string {
	var out []string
	for name, a := range r.Accounts {
		if a.Role == role {
			out = append(out, name)
		}
	}
	sort.Strings(out)
	return out
}