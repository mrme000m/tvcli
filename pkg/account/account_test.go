package account

import (
	"os"
	"path/filepath"
	"strconv"
	"testing"
)

func clearEnv(t *testing.T) {
	t.Helper()
	for _, k := range []string{envSession, envSignature, envUser, envDevice, envTier, envCookies, envExtraCook, envProxy} {
		os.Unsetenv(k)
	}
	for i := 0; i < 10; i++ {
		prefix := "ACCOUNT_" + strconv.Itoa(i) + "_"
		for _, k := range []string{"NAME", "ROLE", "SESSION", "SIGNATURE", "DEVICE_T", "USER", "TIER", "COOKIES", "EXTRA_COOKIES"} {
			os.Unsetenv(prefix + k)
		}
	}
}

func TestLegacySynthesis(t *testing.T) {
	clearEnv(t)
	os.Setenv(envSession, "sess1")
	os.Setenv(envSignature, "sig1")
	os.Setenv(envUser, "trader")
	os.Setenv(envDevice, "dev1")
	os.Setenv(envTier, "plus")
	os.Setenv(envProxy, "socks5://127.0.0.1:1080")

	reg := LoadFromEnv()
	if reg.Default != "default" {
		t.Fatalf("default = %q, want default", reg.Default)
	}
	a := reg.DefaultAccount()
	if a.SessionID != "sess1" || a.Signature != "sig1" || a.UserName != "trader" || a.DeviceToken != "dev1" {
		t.Fatalf("legacy synthesis wrong: %+v", a)
	}
	if a.Tier != "plus" || a.Limits().MaxBars != 0 {
		t.Fatalf("tier not honored: tier=%q limits=%+v", a.Tier, a.Limits())
	}
	if got := a.CookieHeader(); got != "sessionid=sess1; sessionid_sign=sig1; device_t=dev1" {
		t.Fatalf("cookie header = %q", got)
	}
	if a.ProxyURL != "socks5://127.0.0.1:1080" {
		t.Fatalf("proxy not loaded: %q", a.ProxyURL)
	}
}

func TestEnvArray(t *testing.T) {
	clearEnv(t)
	os.Setenv("ACCOUNT_0_NAME", "core")
	os.Setenv("ACCOUNT_0_ROLE", "core")
	os.Setenv("ACCOUNT_0_SESSION", "sess-core")
	os.Setenv("ACCOUNT_0_PROXY", "http://proxy.local:8080")
	os.Setenv("ACCOUNT_1_NAME", "xau-scalp")
	os.Setenv("ACCOUNT_1_ROLE", "script")
	os.Setenv("ACCOUNT_1_SESSION", "sess-xau")

	reg := LoadFromEnv()
	if reg.Default != "core" {
		t.Fatalf("default = %q, want core", reg.Default)
	}
	if got := reg.AccountsForRole("script"); len(got) != 1 || got[0] != "xau-scalp" {
		t.Fatalf("role lookup = %v", got)
	}
	a, ok := reg.Get("xau-scalp")
	if !ok || a.SessionID != "sess-xau" {
		t.Fatalf("xau-scalp account wrong: %+v ok=%v", a, ok)
	}
	core, _ := reg.Get("core")
	if core.ProxyURL != "http://proxy.local:8080" {
		t.Fatalf("env-array proxy not loaded: %q", core.ProxyURL)
	}
	// Unknown tier defaults to free.
	if a.Limits().MaxConnections != 2 {
		t.Fatalf("free limits not applied: %+v", a.Limits())
	}
}

func TestEnvArrayStopsAtGap(t *testing.T) {
	clearEnv(t)
	os.Setenv("ACCOUNT_0_NAME", "a")
	os.Setenv("ACCOUNT_2_NAME", "skipped")
	reg := LoadFromEnv()
	if _, ok := reg.Get("skipped"); ok {
		t.Fatal("ACCOUNT_2 loaded despite ACCOUNT_1 gap")
	}
	if len(reg.Accounts) != 1 {
		t.Fatalf("accounts = %v, want 1", reg.Names())
	}
}

func TestLoadFromJSON(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "accounts.json")
	content := `{
		"default": "core",
		"accounts": {
			"core": {"role": "core", "sessionId": "s1", "tier": "essential"},
			"adhoc-1": {"role": "adhoc", "sessionId": "s2",
			            "proxy": "socks5h://127.0.0.1:1081"}
		}
	}`
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		t.Fatal(err)
	}
	reg, err := LoadFromJSON(path)
	if err != nil {
		t.Fatal(err)
	}
	if reg.Default != "core" {
		t.Fatalf("default = %q", reg.Default)
	}
	core, _ := reg.Get("core")
	if core.Limits().MaxBars != 365 {
		t.Fatalf("essential limits not applied: %+v", core.Limits())
	}
	adhoc, _ := reg.Get("adhoc-1")
	if !adhoc.HasAuth() || adhoc.CookieHeader() != "sessionid=s2" {
		t.Fatalf("adhoc-1 cookie header = %q", adhoc.CookieHeader())
	}
	if adhoc.ProxyURL != "socks5h://127.0.0.1:1081" {
		t.Fatalf("json proxy not loaded: %q", adhoc.ProxyURL)
	}
}

func TestCookiesOverride(t *testing.T) {
	clearEnv(t)
	a := Account{Name: "x", SessionID: "sess", Cookies: "sessionid=override"}
	if got := a.CookieHeader(); got != "sessionid=override" {
		t.Fatalf("override not honored: %q", got)
	}
	anon := Account{Name: "anon"}
	if anon.CookieHeader() != "" || anon.HasAuth() {
		t.Fatal("anonymous account should have empty cookie header")
	}
}