package config

import "os"

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
	"plus":      {4, 10, 20, 0, 40},   // 0 = unlimited
	"premium":   {8, 25, 50, 0, 40},
	"ultimate":  {16, 50, 200, 0, 100},
}

// GetTierLimits returns the TierLimits for the TV_TIER env var, defaulting to "free".
func GetTierLimits() TierLimits {
	tier := os.Getenv("TV_TIER")
	if tier == "" {
		tier = "free"
	}
	if l, ok := tiers[tier]; ok {
		return l
	}
	return tiers["free"]
}
