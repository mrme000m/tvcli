package config

import (
	"os"

	"github.com/mrme000m/tvcli/pkg/account"
)

// TierLimits is the resource-caps type for a subscription tier. The
// canonical definition lives in pkg/account so external programs share it.
type TierLimits = account.TierLimits

// GetTierLimits returns the TierLimits for the active account's tier (set by
// Config.UseAccount), falling back to the TV_TIER env var, then "free".
// Single-account legacy behavior is unchanged when no account is activated.
func GetTierLimits() TierLimits {
	tier := activeTier
	if tier == "" {
		tier = os.Getenv("TV_TIER")
	}
	return account.LimitsForTier(tier)
}