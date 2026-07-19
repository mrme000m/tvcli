package cmd

import (
	"fmt"

	"github.com/ch99q/tvcli/internal/cli"
	"github.com/ch99q/tvcli/internal/metadb"
	"github.com/ch99q/tvcli/pkg/pinefacade"
)

type deleteCmd struct{ app *App }

func (c *deleteCmd) Name() string      { return "delete" }
func (c *deleteCmd) Aliases() []string { return []string{"rm"} }
func (c *deleteCmd) Synopsis() string  { return "Delete script" }

func (c *deleteCmd) Run(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags

	if len(flags.Positional) == 0 {
		return fmt.Errorf("usage: delete <id>")
	}

	store, err := metadb.Load(cfg)
	if err != nil {
		return fmt.Errorf("failed to load metadata: %w", err)
	}

	id := flags.Positional[0]
	entry := store.Get(id)
	if entry == nil {
		return fmt.Errorf("no script #%s", id)
	}

	if !flags.Has("yes") && !flags.Has("y") {
		fmt.Fprintf(env.Stdout, "This will delete remote script: %s\n", entry.PineID)
		fmt.Fprintln(env.Stdout, "Run with --yes to confirm.")
		return nil
	}

	if entry.PineID != "" {
		client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, durationFromMs(cfg.Timeout))
		_, err := client.Delete(entry.PineID, cfg.CookieHeader())
		if err != nil {
			fmt.Fprintf(env.Stdout, "Warning: Could not delete from remote: %v\n", err)
		} else {
			fmt.Fprintln(env.Stdout, "✓ Deleted from remote")
		}
	}

	store.Delete(id)
	fmt.Fprintf(env.Stdout, "✓ Removed #%s from tracking\n", id)
	return nil
}
