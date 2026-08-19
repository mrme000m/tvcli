package cmd

import (
	"github.com/mrme000m/tvcli/internal/cli"
)

type publistCmd struct{ app *App }

func (c *publistCmd) Name() string      { return "publist" }
func (c *publistCmd) Aliases() []string { return []string{"pl"} }
func (c *publistCmd) Synopsis() string  { return "List public TradingView scripts" }

func (c *publistCmd) Run(env *cli.Env) error {
	return runPubList(c.app.Config, env.Flags, env.Stdout)
}
