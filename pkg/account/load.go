package account

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
)

// Legacy env keys — the same set the app's internal/config.Load reads.
const (
	envSession    = "SESSION"
	envSignature  = "SIGNATURE"
	envUser       = "TV_USER"
	envDevice     = "DEVICE_T"
	envTier       = "TV_TIER"
	envCookies    = "TV_COOKIES"
	envExtraCook  = "EXTRA_COOKIES"
	envProxy      = "TV_PROXY"
)

// LoadFromEnv builds a Registry from the environment. When no ACCOUNT_*
// block is present, a single "default" account is synthesized from the legacy
// SESSION/SIGNATURE/TV_USER/DEVICE_T/TV_TIER vars, preserving single-account
// behavior exactly. When ACCOUNT_N_* rows are present they are loaded in
// addition, and the first account with role "core" (or the first account,
// fallback) becomes the default.
//
// Env-array format (zero-based, all keys optional except NAME):
//
//	ACCOUNT_0_NAME=core          ACCOUNT_0_ROLE=core
//	ACCOUNT_0_SESSION=...        ACCOUNT_0_SIGNATURE=...
//	ACCOUNT_0_DEVICE_T=...       ACCOUNT_0_USER=...
//	ACCOUNT_0_TIER=free          ACCOUNT_0_COOKIES=...
//	ACCOUNT_0_EXTRA_COOKIES=...
//	ACCOUNT_0_PROXY=socks5://127.0.0.1:1080
//	ACCOUNT_1_NAME=xau-scalp     ...
//
// The loop stops at the first missing ACCOUNT_N_NAME.
func LoadFromEnv() *Registry {
	reg := NewRegistry()
	accounts := parseEnvAccounts()
	if len(accounts) == 0 {
		reg.Accounts["default"] = legacyAccount()
		reg.Default = "default"
		return reg
	}
	for _, a := range accounts {
		reg.Accounts[a.Name] = a
	}
	if reg.Default == "" {
		if core, ok := reg.Accounts[RoleCore]; ok {
			reg.Default = core.Name
		} else {
			reg.Default = reg.Names()[0]
		}
	}
	return reg
}

func legacyAccount() Account {
	return Account{
		Name:         "default",
		Role:         RoleAdhoc,
		SessionID:    firstNonEmpty(envSession, "SESSION_ID", "TV_SESSION"),
		Signature:    firstNonEmpty(envSignature, "SESSION_SIGN", "TV_SIGNATURE"),
		DeviceToken:  firstNonEmpty(envDevice, "TV_DEVICE_T"),
		UserName:     firstNonEmpty(envUser, "TV_USERNAME"),
		Tier:         os.Getenv(envTier),
		Cookies:      os.Getenv(envCookies),
		ExtraCookies: os.Getenv(envExtraCook),
		ProxyURL:     os.Getenv(envProxy),
	}
}

func parseEnvAccounts() []Account {
	var out []Account
	for i := 0; ; i++ {
		prefix := "ACCOUNT_" + strconv.Itoa(i) + "_"
		name := os.Getenv(prefix + "NAME")
		if name == "" {
			break
		}
		out = append(out, Account{
			Name:         name,
			Role:         os.Getenv(prefix + "ROLE"),
			SessionID:    os.Getenv(prefix + "SESSION"),
			Signature:    os.Getenv(prefix + "SIGNATURE"),
			DeviceToken:  os.Getenv(prefix + "DEVICE_T"),
			UserName:     os.Getenv(prefix + "USER"),
			Tier:         os.Getenv(prefix + "TIER"),
			Cookies:      os.Getenv(prefix + "COOKIES"),
			ExtraCookies: os.Getenv(prefix + "EXTRA_COOKIES"),
			ProxyURL:     os.Getenv(prefix + "PROXY"),
		})
	}
	return out
}

// jsonFile mirrors the accounts.json sidecar layout.
type jsonFile struct {
	Default  string             `json:"default"`
	Accounts map[string]jsonAcct `json:"accounts"`
}

type jsonAcct struct {
	Role         string `json:"role,omitempty"`
	SessionID    string `json:"sessionId,omitempty"`
	Signature    string `json:"signature,omitempty"`
	DeviceToken  string `json:"deviceToken,omitempty"`
	UserName     string `json:"userName,omitempty"`
	Tier         string `json:"tier,omitempty"`
	Cookies      string `json:"cookies,omitempty"`
	ExtraCookies string `json:"extraCookies,omitempty"`
	ProxyURL     string `json:"proxy,omitempty"`
}

// LoadFromJSON reads an accounts.json sidecar. Layout:
//
//	{
//	  "default": "core",
//	  "accounts": {
//	    "core":      {"role": "core",   "sessionId": "...", "tier": "free"},
//	    "xau-scalp": {"role": "script", "sessionId": "...", "tier": "free",
//	                  "proxy": "socks5://127.0.0.1:1080"}
//	  }
//	}
//
// It does not read the environment; combine with LoadFromEnv when legacy
// fallback is desired.
func LoadFromJSON(path string) (*Registry, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read accounts file: %w", err)
	}
	var f jsonFile
	if err := json.Unmarshal(data, &f); err != nil {
		return nil, fmt.Errorf("parse accounts file: %w", err)
	}
	reg := NewRegistry()
	for name, ja := range f.Accounts {
		reg.Accounts[name] = Account{
			Name:         name,
			Role:         ja.Role,
			SessionID:    ja.SessionID,
			Signature:    ja.Signature,
			DeviceToken:  ja.DeviceToken,
			UserName:     ja.UserName,
			Tier:         ja.Tier,
			Cookies:      ja.Cookies,
			ExtraCookies: ja.ExtraCookies,
			ProxyURL:     ja.ProxyURL,
		}
	}
	reg.Default = f.Default
	if reg.Default == "" && len(reg.Accounts) > 0 {
		reg.Default = reg.Names()[0]
	}
	return reg, nil
}

func firstNonEmpty(keys ...string) string {
	for _, k := range keys {
		if v := os.Getenv(k); v != "" {
			return v
		}
	}
	return ""
}