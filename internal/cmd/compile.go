package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/ch99q/tvcli/internal/cli"
	"github.com/ch99q/tvcli/pkg/pinefacade"
)

type compileCmd struct{ app *App }

func (c *compileCmd) Name() string      { return "compile" }
func (c *compileCmd) Aliases() []string { return []string{"check"} }
func (c *compileCmd) Synopsis() string  { return "Compile script" }

func (c *compileCmd) Run(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags

	if len(flags.Positional) == 0 {
		return fmt.Errorf("usage: compile <file.pine>")
	}

	filePath := flags.Positional[0]
	source, err := os.ReadFile(filePath)
	if err != nil {
		return fmt.Errorf("file not found: %s", filePath)
	}

	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, durationFromMs(cfg.Timeout))
	res, err := client.Compile(string(source), cfg.CookieHeader())
	if err != nil {
		return fmt.Errorf("compile error: %w", err)
	}

	b, _ := json.MarshalIndent(res, "", "  ")
	fmt.Fprintln(env.Stdout, string(b))
	return nil
}
