package main

import (
	"fmt"
	"io"
	"os"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/internal/cmd"
	"github.com/mrme000m/tvcli/internal/config"
)

func main() {
	cfg := config.Load()
	args := os.Args[1:]

	if len(args) == 0 {
		cmd.PrintHelp(os.Stdout)
		return
	}

	name := args[0]

	// Resolve the active account before any auth-sensitive work: --account
	// flag > TV_ACCOUNT env > registry default. Overrides cfg credentials so
	// every command uses the selected account transparently.
	if acctName := resolveAccountName(args, cfg); acctName != "" {
		if err := cfg.UseAccount(acctName); err != nil {
			fatal("%v", err)
		}
	}

	if cfg.Debug {
		fmt.Fprintf(os.Stderr, "[debug] auth: %s\n", cfg.AuthSummary())
	}

	// Write operations require auth.
	writeCmds := map[string]bool{
		"create": true, "new": true, "push": true, "delete": true, "rm": true,
	}
	if writeCmds[name] && !cfg.HasAuth() {
		fatal("Write operation '%s' requires SESSION/SIGNATURE cookies.\n"+
			"Extract them from your browser and set in .env:\n"+
			"  SESSION=<sessionid cookie>\n"+
			"  SIGNATURE=<sessionid_sign cookie>\n"+
			"  TV_USER=<your TradingView username>", name)
	}

	root := cli.NewRoot()
	cmd.RegisterAll(root, cmd.NewApp(cfg))
	root.SetHelp(func(_ *cli.Root, w io.Writer) { cmd.PrintHelp(w) })

	if err := root.Execute(args, os.Stdout, os.Stderr); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
}

func fatal(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "Error: "+format+"\n", args...)
	os.Exit(1)
}

// resolveAccountName returns the account to activate for this invocation, in
// priority order: the --account flag, the TV_ACCOUNT env var, then the
// registry's default account. Empty means "no explicit account" (single-
// account legacy mode or no registry).
func resolveAccountName(args []string, cfg *config.Config) string {
	flags := cli.ParseFlags(args)
	if name := flags.Get("account"); name != "" {
		return name
	}
	if name := os.Getenv("TV_ACCOUNT"); name != "" {
		return name
	}
	if cfg.Accounts != nil && len(cfg.Accounts.Accounts) > 0 {
		return cfg.Accounts.Default
	}
	return ""
}
