package cmd

import (
	"encoding/json"
	"fmt"

	"github.com/ch99q/tvcli/internal/cli"
	"github.com/ch99q/tvcli/internal/metadb"
	"github.com/ch99q/tvcli/pkg/pinefacade"
)

// listCmd implements `tvcli list` (and `ls`, and `list --public/--remote`).
//
// ponytail: --public and --remote are kept on this command for backward
// compatibility with the original switch. When more `list*` variants grow,
// split them into their own commands (publist, top, search).
type listCmd struct{ app *App }

func (c *listCmd) Name() string      { return "list" }
func (c *listCmd) Aliases() []string { return []string{"ls"} }
func (c *listCmd) Synopsis() string {
	return "List tracked scripts (use --remote or --public for remote listings)"
}

func (c *listCmd) Run(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags

	store, err := metadb.Load(cfg)
	if err != nil {
		return fmt.Errorf("load metadata: %w", err)
	}

	if flags.Has("public") || flags.Has("p") {
		return runPubList(cfg, flags, env.Stdout)
	}

	if flags.Has("remote") || flags.Has("r") {
		client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, durationFromMs(cfg.Timeout))
		data, err := client.ListSaved(cfg.CookieHeader())
		if err != nil {
			return fmt.Errorf("list remote: %w", err)
		}
		b, _ := json.MarshalIndent(data, "", "  ")
		fmt.Fprintln(env.Stdout, string(b))
		return nil
	}

	scripts := store.List()
	if len(scripts) == 0 {
		fmt.Fprintln(env.Stdout, "No scripts tracked. Use \"create\" to add one.")
		return nil
	}

	fmt.Fprintln(env.Stdout, "\nTracked Scripts:")
	fmt.Fprintln(env.Stdout, "================")
	for _, s := range scripts {
		status := "!"
		if s.RemoteHash == s.LocalHash {
			status = "✓"
		}
		fmt.Fprintf(env.Stdout, "  %s #%-3s | %s\n", status, s.ID, s.Name)
		fmt.Fprintf(env.Stdout, "         pineId: %s\n", s.PineID)
		if s.Owner != "" {
			fmt.Fprintf(env.Stdout, "         owner:  %s\n", s.Owner)
		}
		if s.Access != "" {
			fmt.Fprintf(env.Stdout, "         access: %s\n", s.Access)
		}
		if s.ScriptType != "" {
			fmt.Fprintf(env.Stdout, "         type:   %s\n", s.ScriptType)
		}
		if s.LocalPath != "" {
			fmt.Fprintf(env.Stdout, "         local:  %s\n", s.LocalPath)
		}
		if s.RemoteVersion != "" {
			fmt.Fprintf(env.Stdout, "         version: %s\n", s.RemoteVersion)
		}
		fmt.Fprintln(env.Stdout)
	}
	return nil
}
