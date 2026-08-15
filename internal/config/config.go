package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/joho/godotenv"
)

// Config holds all TradingView CLI configuration loaded from env vars / .env file.
type Config struct {
	PineFacadeURL string
	TVBaseURL     string
	Timeout       int
	UserName      string
	SessionID     string
	Signature     string
	DataDir       string
	MetaFile      string
	Cookies       string
	ExtraCookies  string
	DeviceToken   string
	Debug         bool
}

// Load reads .env (if present) then populates Config from environment.
// Searches for .env in: cwd, executable dir, parent of executable dir.
func Load() *Config {
	// Try multiple .env locations
	lookPaths := []string{"."}
	if exe, err := os.Executable(); err == nil {
		dir := filepath.Dir(exe)
		lookPaths = append(lookPaths, dir, filepath.Join(dir, ".."))
	}
	for _, p := range lookPaths {
		godotenv.Load(filepath.Join(p, ".env"))
	}

	c := &Config{
		PineFacadeURL: envOrDefault("PINE_FACADE_BASE_URL", "https://pine-facade.tradingview.com/pine-facade"),
		TVBaseURL:     envOrDefault("TV_BASE_URL", "https://www.tradingview.com"),
		Timeout:       envIntOrDefault("TV_TIMEOUT_MS", 120000),
		UserName:      firstNonEmpty("TV_USER", "TV_USERNAME"),
		SessionID:     firstNonEmpty("SESSION", "SESSION_ID", "TV_SESSION"),
		Signature:     firstNonEmpty("SIGNATURE", "SESSION_SIGN", "TV_SIGNATURE"),
		DataDir:       envOrDefault("TV_DATA_DIR", ".tv-scripts"),
		MetaFile:      envOrDefault("TV_META_FILE", ".tv-meta.json"),
		Cookies:       os.Getenv("TV_COOKIES"),
		ExtraCookies:  os.Getenv("EXTRA_COOKIES"),
		DeviceToken:   firstNonEmpty("DEVICE_T", "TV_DEVICE_T"),
		Debug:         os.Getenv("DEBUG") == "1" || os.Getenv("TW_DEBUG") == "1",
	}
	return c
}

// HasAuth returns true if session cookies are available.
func (c *Config) HasAuth() bool {
	return c.SessionID != ""
}

// RequireUser returns the TV_USER or panics.
func (c *Config) RequireUser() string {
	if c.UserName == "" {
		panic("Missing TV_USER env var (set in .env or export)")
	}
	return c.UserName
}

// CookieHeader builds the Cookie header string for TradingView API requests.
// Priority: TV_COOKIES (full override) > SESSION/SIGNATURE (built) > error.
func (c *Config) CookieHeader() string {
	if c.Cookies != "" {
		return c.Cookies
	}
	if c.SessionID == "" {
		panic("Missing SESSION env var — extract it from your browser (see README)")
	}
	return c.buildCookieString()
}

// CookieHeaderOrEmpty returns the cookie header, or empty string if no SESSION is set.
// Use this for endpoints that work without auth (public indicators).
func (c *Config) CookieHeaderOrEmpty() string {
	if c.Cookies != "" {
		return c.Cookies
	}
	if c.SessionID == "" {
		return ""
	}
	return c.buildCookieString()
}

func (c *Config) buildCookieString() string {
	var parts []string
	parts = append(parts, "sessionid="+c.SessionID)
	if c.Signature != "" {
		parts = append(parts, "sessionid_sign="+c.Signature)
	}
	if c.DeviceToken != "" {
		parts = append(parts, "device_t="+c.DeviceToken)
	}
	if c.ExtraCookies != "" {
		parts = append(parts, c.ExtraCookies)
	}
	return strings.Join(parts, "; ")
}

// AuthSummary returns a one-line description of the current auth state.
func (c *Config) AuthSummary() string {
	if c.SessionID == "" {
		return "no auth (anonymous)"
	}
	user := c.UserName
	if user == "" {
		user = "(no TV_USER set)"
	}
	sig := "with signature"
	if c.Signature == "" {
		sig = "without signature"
	}
	return fmt.Sprintf("session=%s... %s user=%s", truncate(c.SessionID, 12), sig, user)
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

func firstNonEmpty(keys ...string) string {
	for _, k := range keys {
		if v := os.Getenv(k); v != "" {
			return v
		}
	}
	return ""
}

func envOrDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func envIntOrDefault(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}
