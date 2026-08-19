package config

import (
	"os"

	"github.com/mrme000m/tvcli/pkg/account"
)

// TierLimits is the resource-caps type for a subscription tier. The
// canonical definition lives in pkg/account so external programs share it.
type TierLimits = account.TierLimits

// GetTierLimits returns the TierLimits for the TV_TIER env var, defaulting to
// "free". Legacy single-account behavior is unchanged: the tier is global.
func GetTierLimits() TierLimits {
	return account.LimitsForTier(os.Getenv("TV_TIER"))
}