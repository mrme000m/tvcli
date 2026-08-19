// Helpers shared by command implementations in internal/cmd.
package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/mrme000m/tvcli/internal/config"
	"github.com/mrme000m/tvcli/pkg/tradingview/auth"
)

// PreCheckAuth verifies TradingView auth cookies before attempting to run a
// study. Returns an error if cookies are expired, nil if authenticated or no
// cookies are configured (anonymous mode — fetch works without auth).
// All study-requiring commands (run, eval, skills) should call this to fail
// fast with an actionable message instead of silently hitting "study limit".
func PreCheckAuth(cfg *config.Config) error {
	if !cfg.HasAuth() {
		return nil // anonymous mode — some commands work without auth
	}
	info := auth.FetchAuthInfo(cfg.SessionID, cfg.Signature, "", cfg.DeviceToken)
	if !info.Authenticated {
		fmt.Fprintf(os.Stderr, "❌ Authentication failed: %v\n", info.Error)
		fmt.Fprintf(os.Stderr, "  TradingView will reject all studies with a 'study limit' error.\n")
		fmt.Fprintf(os.Stderr, "  Fix: re-extract SESSION/SIGNATURE/DEVICE_T cookies from your browser.\n")
		fmt.Fprintf(os.Stderr, "  Run 'tvcli check-auth' to diagnose.\n")
		return fmt.Errorf("auth: cookies expired")
	}
	return nil
}

// Fatal prints a formatted error to stderr and exits 1. Kept as a thin
// wrapper around os.Exit so command implementations have a familiar name.
// Prefer returning an error from Run() — Fatal is for the legacy paths
// that haven't been converted yet.
func Fatal(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "Error: "+format+"\n", args...)
	os.Exit(1)
}

// IsNumeric returns true if s is a non-empty all-digit string.
func IsNumeric(s string) bool {
	for _, c := range s {
		if c < '0' || c > '9' {
			return false
		}
	}
	return len(s) > 0
}

// Slugify converts a script name to a filesystem-safe slug.
func Slugify(input string) string {
	s := strings.TrimSpace(input)
	s = strings.ToLower(s)
	s = strings.Map(func(r rune) rune {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '-' {
			return r
		}
		return '-'
	}, s)
	for strings.Contains(s, "--") {
		s = strings.ReplaceAll(s, "--", "-")
	}
	s = strings.Trim(s, "-")
	if s == "" {
		return "script"
	}
	return s
}

// RelPath returns absPath relative to the current working directory, or the
// absolute path if the relationship can't be expressed.
func RelPath(cfg *config.Config, absPath string) string {
	rel, err := filepath.Rel(".", absPath)
	if err != nil {
		return absPath
	}
	return rel
}

// ExtractPineID pulls a Pine ID out of a Pine Facade API response. Returns
// the first USER;/PUB;/STD;/INDIC;... string found, or "".
func ExtractPineID(data any) string {
	switch v := data.(type) {
	case map[string]any:
		for _, key := range []string{"pineId", "id", "scriptIdPart"} {
			if s, ok := v[key].(string); ok && strings.Contains(s, ";") {
				return s
			}
		}
		if result, ok := v["result"].(map[string]any); ok {
			if mi, ok := result["metaInfo"].(map[string]any); ok {
				if s, ok := mi["scriptIdPart"].(string); ok {
					return s
				}
			}
		}
	}
	return ""
}

// ExtractVersion pulls the "version" field out of a Pine Facade response.
func ExtractVersion(data any) string {
	if m, ok := data.(map[string]any); ok {
		if v, ok := m["version"].(string); ok {
			return v
		}
	}
	return ""
}

// ReservedRunKeys are flag names that are NOT indicator inputs — they're
// CLI flags consumed by the run command. Passed to service.LoadIndicator so
// it doesn't try to set them on the indicator.
var ReservedRunKeys = []string{
	"symbol", "tf", "timeframe", "bars", "json", "out",
	"force-cleanup", "cleanup", "raw", "raw-out", "signals", "agent",
	"settle", "schema", "multi-run", "sweep",
	"persistent", "loop",
	"allow-private", "verify-access",
}
