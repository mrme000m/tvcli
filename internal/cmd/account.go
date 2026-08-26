// account.go — multi-account storage + switching for tvcli.
//
// Accounts live in an accounts.json sidecar (pkg/account). This command
// manages that registry: list/show (masked), add, use (set default), remove.
// Any other command selects an account with the global --account flag (or the
// TV_ACCOUNT env var, or the registry default) — resolved in main.go before
// dispatch, so the rest of the CLI is unchanged.
package cmd

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/pkg/account"
)

type accountCmd struct{ app *App }

func (c *accountCmd) Name() string      { return "account" }
func (c *accountCmd) Aliases() []string { return []string{"accounts", "acct"} }
func (c *accountCmd) Synopsis() string {
	return "Manage multiple TradingView accounts (list / show / add / use / remove)"
}

func (c *accountCmd) Run(env *cli.Env) error {
	sub := ""
	if len(env.Flags.Positional) > 0 {
		sub = env.Flags.Positional[0]
	}
	switch sub {
	case "list", "ls":
		return c.list(env)
	case "show":
		return c.show(env)
	case "add", "set":
		return c.add(env)
	case "use", "switch":
		return c.use(env)
	case "remove", "rm":
		return c.remove(env)
	case "import":
		if len(env.Flags.Positional) < 2 {
			return fmt.Errorf("usage: account import <accounts.csv>")
		}
		return c.importCSV(env, env.Flags.Positional[1])
	default:
		c.printHelp(env)
		return nil
	}
}

// registry returns the loaded registry, loading the sidecar if needed.
func (c *accountCmd) registry() (*account.Registry, error) {
	if c.app.Config.Accounts != nil {
		return c.app.Config.Accounts, nil
	}
	reg, err := account.LoadFromJSON(c.app.Config.AccountsFile)
	if err != nil {
		return nil, fmt.Errorf("no accounts registry (create one with `tvcli account add <name> ...`): %w", err)
	}
	return reg, nil
}

func (c *accountCmd) persist(reg *account.Registry) error {
	if err := reg.SaveToJSON(c.app.Config.AccountsFile); err != nil {
		return err
	}
	// Keep the in-memory copy in sync for this process.
	c.app.Config.Accounts = reg
	return nil
}

// maskCred renders a credential for display without leaking it: 8 chars +
// ellipsis, or "-" when empty.
func maskCred(v string) string {
	if v == "" {
		return "-"
	}
	if len(v) <= 8 {
		return "•••"
	}
	return v[:8] + "…"
}

func (c *accountCmd) list(env *cli.Env) error {
	reg, err := c.registry()
	if err != nil {
		return err
	}
	names := reg.Names()
	if len(names) == 0 {
		fmt.Fprintln(env.Stdout, "No accounts stored.")
		return nil
	}

	type row struct {
		Name    string `json:"name"`
		Role    string `json:"role"`
		Tier    string `json:"tier"`
		User    string `json:"username"`
		Session string `json:"session"`
		Default bool   `json:"default"`
	}
	rows := make([]row, 0, len(names))
	for _, name := range names {
		a := reg.Accounts[name]
		rows = append(rows, row{
			Name:    name,
			Role:    a.Role,
			Tier:    a.Tier,
			User:    a.UserName,
			Session: maskCred(a.SessionID),
			Default: name == reg.Default,
		})
	}

	if env.Flags.Has("json") {
		b, _ := json.MarshalIndent(map[string]any{"default": reg.Default, "accounts": rows}, "", "  ")
		fmt.Fprintln(env.Stdout, string(b))
		return nil
	}

	fmt.Fprintf(env.Stdout, "Accounts (%d, default: %s) — stored in %s\n", len(rows), reg.Default, c.app.Config.AccountsFile)
	for _, r := range rows {
		mark := " "
		if r.Default {
			mark = "*"
		}
		fmt.Fprintf(env.Stdout, " %s %-18s role=%-8s tier=%-9s user=%-18s session=%s\n",
			mark, r.Name, orDash(r.Role), orDash(r.Tier), orDash(r.User), r.Session)
	}
	return nil
}

func (c *accountCmd) show(env *cli.Env) error {
	reg, err := c.registry()
	if err != nil {
		return err
	}
	name := reg.Default
	if len(env.Flags.Positional) > 1 {
		name = env.Flags.Positional[1]
	}
	a, ok := reg.Get(name)
	if !ok {
		return fmt.Errorf("account %q not found (known: %v)", name, reg.Names())
	}
	if env.Flags.Has("json") {
		b, _ := json.MarshalIndent(map[string]any{
			"name": a.Name, "role": a.Role, "tier": a.Tier, "username": a.UserName,
			"session": maskCred(a.SessionID), "proxy": orDash(a.ProxyURL), "default": name == reg.Default,
		}, "", "  ")
		fmt.Fprintln(env.Stdout, string(b))
		return nil
	}
	fmt.Fprintf(env.Stdout, "Account %q (default=%v)\n  role=%s tier=%s user=%s session=%s proxy=%s\n",
		a.Name, name == reg.Default, orDash(a.Role), orDash(a.Tier), orDash(a.UserName), maskCred(a.SessionID), orDash(a.ProxyURL))
	return nil
}

func (c *accountCmd) add(env *cli.Env) error {
	if len(env.Flags.Positional) < 2 {
		return fmt.Errorf("usage: account add <name> --session <id> --signature <sig> --device-t <d> [--user U] [--tier free] [--role adhoc] [--proxy P]")
	}
	name := env.Flags.Positional[1]
	flags := env.Flags
	a := account.Account{
		Name:         name,
		Role:         flags.Get("role"),
		SessionID:    flags.Get("session"),
		Signature:    flags.Get("signature"),
		DeviceToken:  flags.Get("device-t"),
		UserName:     flags.Get("user"),
		Tier:         flags.Get("tier"),
		ProxyURL:     flags.Get("proxy"),
		Cookies:      flags.Get("cookies"),
		ExtraCookies: flags.Get("extra-cookies"),
	}
	if a.SessionID == "" && a.Cookies == "" {
		return fmt.Errorf("an account needs --session (or --cookies)")
	}
	if a.Role == "" {
		a.Role = account.RoleAdhoc
	}
	if a.Tier == "" {
		a.Tier = "free"
	}

	reg, err := c.registry()
	if err != nil {
		reg = account.NewRegistry() // no sidecar yet — start fresh
	}
	if err := reg.Add(a); err != nil {
		return err
	}
	// First account added becomes the default.
	if reg.Default == "" {
		reg.Default = name
	}
	if err := c.persist(reg); err != nil {
		return err
	}
	fmt.Fprintf(env.Stdout, "✓ Stored account %q (tier=%s role=%s) — %d account(s) total\n",
		name, a.Tier, a.Role, len(reg.Accounts))
	return nil
}

func (c *accountCmd) use(env *cli.Env) error {
	if len(env.Flags.Positional) < 2 {
		return fmt.Errorf("usage: account use <name>")
	}
	name := env.Flags.Positional[1]
	reg, err := c.registry()
	if err != nil {
		return err
	}
	if err := reg.SetDefault(name); err != nil {
		return err
	}
	if err := c.persist(reg); err != nil {
		return err
	}
	fmt.Fprintf(env.Stdout, "✓ Default account set to %q\n", name)
	return nil
}

func (c *accountCmd) remove(env *cli.Env) error {
	if len(env.Flags.Positional) < 2 {
		return fmt.Errorf("usage: account remove <name>")
	}
	name := env.Flags.Positional[1]
	reg, err := c.registry()
	if err != nil {
		return err
	}
	if !reg.Remove(name) {
		return fmt.Errorf("account %q not found (known: %v)", name, reg.Names())
	}
	if reg.Default == name {
		reg.Default = ""
		if names := reg.Names(); len(names) > 0 {
			reg.Default = names[0]
		}
	}
	if err := c.persist(reg); err != nil {
		return err
	}
	fmt.Fprintf(env.Stdout, "✓ Removed account %q (%d remaining)\n", name, len(reg.Accounts))
	return nil
}

// importCSV bulk-loads free accounts from a CSV with the columns
// profile,username,sessionid,sessionid_sign,device_t (the shape of
// tv_free_accounts.csv). Each row becomes a free-tier adhoc account; names are
// the username, deduped with a -N suffix when a username repeats.
func (c *accountCmd) importCSV(env *cli.Env, path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()

	r := csv.NewReader(f)
	r.FieldsPerRecord = -1
	records, err := r.ReadAll()
	if err != nil {
		return fmt.Errorf("parse csv: %w", err)
	}
	if len(records) == 0 {
		return fmt.Errorf("empty csv")
	}

	idx := map[string]int{}
	for i, h := range records[0] {
		idx[strings.ToLower(strings.TrimSpace(h))] = i
	}
	for _, need := range []string{"username", "sessionid", "sessionid_sign", "device_t"} {
		if _, ok := idx[need]; !ok {
			return fmt.Errorf("missing column %q in %s", need, path)
		}
	}

	reg, err := c.registry()
	if err != nil {
		reg = account.NewRegistry()
	}

	used := map[string]int{}
	imported := 0
	for i, rec := range records[1:] {
		get := func(col string) string {
			j, ok := idx[col]
			if !ok || j >= len(rec) {
				return ""
			}
			return strings.TrimSpace(rec[j])
		}
		username := get("username")
		sid := get("sessionid")
		if sid == "" {
			continue
		}
		name := username
		if name == "" {
			name = fmt.Sprintf("free-%d", i+1)
		}
		if used[name] > 0 {
			used[name]++
			name = fmt.Sprintf("%s-%d", name, used[name])
		} else {
			used[name] = 1
		}
		reg.Add(account.Account{
			Name:        name,
			Role:        account.RoleAdhoc,
			Tier:        "free",
			SessionID:   sid,
			Signature:   get("sessionid_sign"),
			DeviceToken: get("device_t"),
			UserName:    username,
		})
		imported++
	}

	if reg.Default == "" && len(reg.Accounts) > 0 {
		reg.Default = reg.Names()[0]
	}
	if err := c.persist(reg); err != nil {
		return err
	}
	fmt.Fprintf(env.Stdout, "✓ Imported %d free accounts — %d total (default: %s)\n",
		imported, len(reg.Accounts), reg.Default)
	return nil
}

func orDash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}

func (c *accountCmd) printHelp(env *cli.Env) {
	w := env.Stdout
	fmt.Fprintln(w, "account — Manage multiple TradingView accounts")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Usage:")
	fmt.Fprintln(w, "  tv account list [--json]              List accounts (credentials masked)")
	fmt.Fprintln(w, "  tv account show [name] [--json]       Show one account (default: active)")
	fmt.Fprintln(w, "  tv account add <name> --session <id> --signature <sig> --device-t <d> [options]")
	fmt.Fprintln(w, "  tv account use <name>                 Set the default account")
	fmt.Fprintln(w, "  tv account remove <name>              Delete an account")
	fmt.Fprintln(w, "  tv account import <accounts.csv>      Bulk-import free accounts (profile,username,sessionid,sessionid_sign,device_t)")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "add options: --user, --tier (default free), --role (default adhoc),")
	fmt.Fprintln(w, "             --proxy URL, --cookies RAW, --extra-cookies RAW")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Any command selects an account with --account <name> (or TV_ACCOUNT env,")
	fmt.Fprintln(w, "or the registry default). Example:")
	fmt.Fprintln(w, "  tv run \"PUB;...\" --account core")
	fmt.Fprintln(w, "  tv fetch --symbol BINANCE:BTCUSDT --account adhoc-1")
	fmt.Fprintln(w, "")
	fmt.Fprintln(w, "Credentials are stored in", c.app.Config.AccountsFile, "(0600); never printed.")
}
