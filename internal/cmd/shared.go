// Package cmd holds the tvcli subcommands, each implementing cli.Command.
// One file per command. The package wires itself into a cli.Root via Register.
package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"time"

	"github.com/ch99q/tvcli/internal/cli"
	"github.com/ch99q/tvcli/internal/config"
	"github.com/ch99q/tvcli/internal/skill"
	_ "github.com/ch99q/tvcli/internal/skill/parsers"
	"github.com/ch99q/tvcli/pkg/pinefacade"
)

// App is the shared context passed to every command: the loaded config plus
// any long-lived resources. Keep this small — it's the only seam between
// the command layer and the rest of the binary.
type App struct {
	Config *config.Config
}

// NewApp builds an App from a loaded config.
func NewApp(cfg *config.Config) *App { return &App{Config: cfg} }

// RegisterAll wires all built-in commands into root.
func RegisterAll(root *cli.Root, app *App) {
	root.Add(&createCmd{app: app})
	root.Add(&pullCmd{app: app})
	root.Add(&pushCmd{app: app})
	root.Add(&deleteCmd{app: app})
	root.Add(&listCmd{app: app})
	root.Add(&searchCmd{app: app})
	root.Add(&publistCmd{app: app})
	root.Add(&topCmd{app: app})
	root.Add(&compileCmd{app: app})
	root.Add(&runCmd{app: app})
	root.Add(&fetchCmd{app: app})
	root.Add(&syncCmd{app: app})
	root.Add(&inputsCmd{app: app})
	RegisterSkills(root, app)
}

// RegisterSkills adds all indicator skill commands to root.
func RegisterSkills(root *cli.Root, app *App) {
	for _, s := range skill.All() {
		root.Add(&skillCmd{app: app, skill: s})
	}
	root.Add(&skillsCmd{})
}

// durationFromMs converts the cfg.Timeout (int milliseconds) into a time.Duration.
func durationFromMs(ms int) time.Duration {
	return time.Duration(ms) * time.Millisecond
}

// runPubList lists the user's public scripts and prints to w (matching the
// legacy `list --public` / `publist` behavior).
func runPubList(cfg *config.Config, flags cli.Flags, w io.Writer) error {
	offset := flags.GetInt("offset", 0)
	limit := flags.GetInt("limit", 20)
	asJSON := flags.Has("json")

	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, durationFromMs(cfg.Timeout))
	data, err := client.ListPublicScripts(offset)
	if err != nil {
		return fmt.Errorf("list public: %w", err)
	}

	items := NormalizeSearchResults(data, limit)

	if asJSON {
		b, _ := json.MarshalIndent(map[string]any{
			"offset":  offset,
			"limit":   limit,
			"count":   len(items),
			"results": items,
		}, "", "  ")
		fmt.Fprintln(w, string(b))
	} else {
		next := ExtractNext(data)
		fmt.Fprintf(w, "\nPublic scripts: %d (offset=%d, next=%v)\n\n", len(items), offset, next)
		PrintSearchTable(w, items)
	}
	return nil
}
