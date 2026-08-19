package cmd

import (
	"encoding/json"
	"fmt"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
)

type searchCmd struct{ app *App }

func (c *searchCmd) Name() string      { return "search" }
func (c *searchCmd) Aliases() []string { return []string{"find"} }
func (c *searchCmd) Synopsis() string  { return "Search public scripts" }

func (c *searchCmd) Run(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags

	if len(flags.Positional) == 0 {
		return fmt.Errorf("usage: search <query> [--limit N] [--json]")
	}

	query := flags.Positional[0]
	limit := flags.GetInt("limit", 20)
	asJSON := flags.Has("json")

	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, durationFromMs(cfg.Timeout))
	data, err := client.SearchPublicScripts(query, "")
	if err != nil {
		return fmt.Errorf("search failed: %w", err)
	}

	items := NormalizeSearchResults(data, limit)

	if asJSON {
		b, _ := json.MarshalIndent(map[string]any{
			"query":   query,
			"limit":   limit,
			"count":   len(items),
			"results": items,
		}, "", "  ")
		fmt.Fprintln(env.Stdout, string(b))
	} else {
		fmt.Fprintf(env.Stdout, "\nSearch '%s': %d results\n\n", query, len(items))
		PrintSearchTable(env.Stdout, items)
	}
	return nil
}
