package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
)

type topCmd struct{ app *App }

func (c *topCmd) Name() string      { return "top" }
func (c *topCmd) Aliases() []string { return nil }
func (c *topCmd) Synopsis() string  { return "Fetch top public scripts to JSON" }

func (c *topCmd) Run(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags

	limit := flags.GetInt("limit", 100)
	output := flags.Get("output")
	if output == "" {
		output = "top_scripts.json"
	}

	fmt.Fprintf(env.Stdout, "Fetching top %d scripts...\n", limit)

	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, durationFromMs(cfg.Timeout))

	var allItems []map[string]any
	offset := 0
	batchSize := 20

	for len(allItems) < limit {
		data, err := client.ListPublicScripts(offset)
		if err != nil {
			return fmt.Errorf("failed to fetch scripts at offset %d: %w", offset, err)
		}

		items := NormalizeSearchResults(data, batchSize)
		if len(items) == 0 {
			break
		}

		allItems = append(allItems, items...)
		offset += batchSize
		fmt.Fprintf(env.Stderr, "  Fetched %d scripts...\n", len(allItems))

		if len(items) < batchSize {
			break
		}
	}

	if len(allItems) > limit {
		allItems = allItems[:limit]
	}

	payload := map[string]any{
		"total":   len(allItems),
		"scripts": allItems,
	}
	b, _ := json.MarshalIndent(payload, "", "  ")
	os.WriteFile(output, b, 0644)
	fmt.Fprintf(env.Stdout, "\n✓ Saved %d scripts to %s\n", len(allItems), output)
	return nil
}
