package account

import (
	"encoding/json"
	"fmt"
	"os"
)

// SaveToJSON writes the registry to an accounts.json sidecar (0600, since it
// holds session cookies). The on-disk layout mirrors LoadFromJSON exactly, so
// a registry loaded from disk round-trips.
func (r *Registry) SaveToJSON(path string) error {
	f := jsonFile{Default: r.Default, Accounts: make(map[string]jsonAcct, len(r.Accounts))}
	for name, a := range r.Accounts {
		f.Accounts[name] = jsonAcct{
			Role:         a.Role,
			SessionID:    a.SessionID,
			Signature:    a.Signature,
			DeviceToken:  a.DeviceToken,
			UserName:     a.UserName,
			Tier:         a.Tier,
			Cookies:      a.Cookies,
			ExtraCookies: a.ExtraCookies,
			ProxyURL:     a.ProxyURL,
		}
	}
	data, err := json.MarshalIndent(f, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal accounts: %w", err)
	}
	if err := os.WriteFile(path, append(data, '\n'), 0600); err != nil {
		return fmt.Errorf("write accounts file: %w", err)
	}
	return nil
}

// Add inserts or replaces an account under its Name. A Name is required; a
// nameless account is rejected to keep the registry keys usable as CLI
// identifiers.
func (r *Registry) Add(a Account) error {
	if a.Name == "" {
		return fmt.Errorf("account name is required")
	}
	if r.Accounts == nil {
		r.Accounts = make(map[string]Account)
	}
	r.Accounts[a.Name] = a
	return nil
}

// Remove deletes an account by name. It reports whether the account existed.
// Removing the default leaves Default pointing at a missing key, which
// DefaultAccount() tolerates (it falls back to any remaining account).
func (r *Registry) Remove(name string) bool {
	if _, ok := r.Accounts[name]; !ok {
		return false
	}
	delete(r.Accounts, name)
	return true
}

// SetDefault marks name as the default account. It errors when the name is not
// present, so a typo can't silently leave the registry pointing at nothing.
func (r *Registry) SetDefault(name string) error {
	if _, ok := r.Accounts[name]; !ok {
		return fmt.Errorf("account %q not found (known: %v)", name, r.Names())
	}
	r.Default = name
	return nil
}