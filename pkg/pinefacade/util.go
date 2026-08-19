// Utility helpers for pine-facade: Pine ID detection/normalization, hashing,
// timeframe normalization, symbol validation, and version sorting.
package pinefacade

import (
	"crypto/sha256"
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

var pineIDRegex = regexp.MustCompile(`(?:USER|PUB|STD|INDIC);[^\s"'<>]+`)

// ExtractPineIDFromSource scans a Pine source file for a `@pineId`-style
// declaration and returns the normalized Pine ID, or "" if none is found.
func ExtractPineIDFromSource(source string) string {
	m := regexp.MustCompile(`(?m)^\s*(?://\s*)?(?:@?pineId\b\s*(?::|=)?\s*)(?:"|')?\s*((?:USER|PUB|STD|INDIC);[^\s"'<>]+)`).FindStringSubmatch(source)
	if len(m) > 1 {
		return NormalizePineID(m[1])
	}
	return ""
}

// NormalizePineID trims whitespace and decodes URL-encoded semicolons (%3B → ;).
func NormalizePineID(raw string) string {
	return strings.ReplaceAll(strings.TrimSpace(raw), "%3B", ";")
}

// LooksLikePineID returns true if s begins with a known Pine ID prefix.
func LooksLikePineID(s string) bool {
	return regexp.MustCompile(`(?i)^\s*(USER|PUB|STD|INDIC);`).MatchString(s)
}

// ScriptTypeFromSource reports whether Pine source declares a strategy
// (emits signals) or an indicator (analysis only). It scans the declaration
// line (`strategy(`, `indicator(`, or legacy `study(`) — the authoritative
// marker set at script creation. Anything that is not an explicit strategy is
// reported as an indicator.
func ScriptTypeFromSource(source string) string {
	m := regexp.MustCompile(`(?m)^\s*(strategy|indicator|study)\s*\(`).FindStringSubmatch(source)
	if len(m) > 1 && strings.EqualFold(m[1], "strategy") {
		return "strategy"
	}
	return "indicator"
}

// SHA256 returns the hex-encoded SHA-256 hash of text.
func SHA256(text string) string {
	h := sha256.Sum256([]byte(text))
	return fmt.Sprintf("%x", h)
}

// NormalizeTimeframe converts a human timeframe (5m, 1h, 1D, …) into the
// bare-minute / single-letter form TradingView's WS API expects (5, 60, D).
func NormalizeTimeframe(tf string) string {
	t := strings.TrimSpace(tf)
	if t == "" {
		return "5"
	}
	// Already a bare number or single-letter D/W/M
	if regexp.MustCompile(`^\d+$`).MatchString(t) || regexp.MustCompile(`(?i)^[DWM]$`).MatchString(t) {
		return strings.ToUpper(t)
	}
	// Nm → N (minutes) — lowercase m only, must check before NM
	if m := regexp.MustCompile(`^(\d+)m$`).FindStringSubmatch(t); len(m) > 1 {
		return m[1]
	}
	// NM → M (monthly) — uppercase M only
	if regexp.MustCompile(`^\d+M$`).MatchString(t) {
		return "M"
	}
	// Nh → N*60 (minutes)
	if h := regexp.MustCompile(`(?i)^(\d+)h$`).FindStringSubmatch(t); len(h) > 1 {
		n, _ := strconv.Atoi(h[1])
		return strconv.Itoa(n * 60)
	}
	// Nd → D, Nw → W
	if d := regexp.MustCompile(`(?i)^(\d+)[dw]$`).FindStringSubmatch(t); len(d) > 0 {
		letter := d[0][len(d[0])-1]
		return strings.ToUpper(string(letter))
	}
	return t
}

// ValidateSymbol checks if a symbol has the required exchange prefix format.
// TradingView requires symbols in the format EXCHANGE:SYMBOL (e.g.,
// OANDA:XAUUSD, BINANCE:BTCUSDT). Common bare symbols are auto-mapped.
func ValidateSymbol(symbol string) (string, error) {
	s := strings.TrimSpace(symbol)
	if s == "" {
		return "", fmt.Errorf("symbol cannot be empty")
	}

	// Already has exchange prefix
	if strings.Contains(s, ":") {
		parts := strings.SplitN(s, ":", 2)
		if len(parts) == 2 && parts[0] != "" && parts[1] != "" {
			return strings.ToUpper(parts[0]) + ":" + strings.ToUpper(parts[1]), nil
		}
		return "", fmt.Errorf("invalid symbol format: %s (expected EXCHANGE:SYMBOL)", s)
	}

	// Auto-detect common symbols and add exchange prefix
	upper := strings.ToUpper(s)
	autoMap := map[string]string{
		"XAUUSD":  "OANDA:XAUUSD",
		"XAGUSD":  "OANDA:XAGUSD",
		"EURUSD":  "OANDA:EURUSD",
		"GBPUSD":  "OANDA:GBPUSD",
		"USDJPY":  "OANDA:USDJPY",
		"BTCUSDT": "BINANCE:BTCUSDT",
		"ETHUSDT": "BINANCE:ETHUSDT",
		"SOLUSDT": "BINANCE:SOLUSDT",
		"BTCUSD":  "COINBASE:BTCUSD",
		"ETHUSD":  "COINBASE:ETHUSD",
	}

	if mapped, ok := autoMap[upper]; ok {
		return mapped, nil
	}

	// Guess exchange based on symbol suffix
	if strings.HasSuffix(upper, "USD") || strings.HasSuffix(upper, "USDT") {
		return "BINANCE:" + upper, nil
	}
	if len(upper) == 6 && !strings.Contains(upper, " ") {
		// Likely a forex pair
		return "OANDA:" + upper, nil
	}

	return "", fmt.Errorf("cannot determine exchange for symbol: %s (use EXCHANGE:SYMBOL format)", s)
}

// --- version sorting ---

// normalizeVersionEntries coerces a raw API response into a []any of versions.
func normalizeVersionEntries(raw any) []any {
	switch v := raw.(type) {
	case []any:
		return v
	case map[string]any:
		if arr, ok := v["versions"].([]any); ok {
			return arr
		}
		if res, ok := v["result"].(map[string]any); ok {
			if arr, ok := res["versions"].([]any); ok {
				return arr
			}
		}
		if arr, ok := v["data"].([]any); ok {
			return arr
		}
	}
	return nil
}

func extractVersion(entry any) string {
	switch v := entry.(type) {
	case string:
		return v
	case map[string]any:
		for _, key := range []string{"version", "scriptVersion", "sourceVersion"} {
			if s, ok := v[key].(string); ok {
				return s
			}
		}
		if res, ok := v["result"].(map[string]any); ok {
			if s, ok := res["version"].(string); ok {
				return s
			}
		}
		if mi, ok := v["metaInfo"].(map[string]any); ok {
			if s, ok := mi["version"].(string); ok {
				return s
			}
		}
	}
	return ""
}

func compareVersions(a, b string) int {
	aParts := strings.Split(a, ".")
	bParts := strings.Split(b, ".")
	maxLen := len(aParts)
	if len(bParts) > maxLen {
		maxLen = len(bParts)
	}
	for i := 0; i < maxLen; i++ {
		aVal, _ := strconv.Atoi(loot(aParts, i))
		bVal, _ := strconv.Atoi(loot(bParts, i))
		if aVal > bVal {
			return 1
		}
		if aVal < bVal {
			return -1
		}
	}
	return 0
}

func loot(parts []string, i int) string {
	if i < len(parts) {
		return parts[i]
	}
	return "0"
}

// SortVersions sorts a slice of "N.M.K" version strings in descending order.
func SortVersions(versions []string) []string {
	sort.Slice(versions, func(i, j int) bool {
		return compareVersions(versions[i], versions[j]) > 0
	})
	return versions
}
