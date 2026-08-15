package cmd

import (
	"fmt"
	"strings"

	"github.com/ch99q/tvcli/internal/cli"
	"github.com/ch99q/tvcli/internal/server"
)

type serveCmd struct{ app *App }

func (c *serveCmd) Name() string      { return "serve" }
func (c *serveCmd) Aliases() []string { return []string{"server"} }
func (c *serveCmd) Synopsis() string  { return "Start HTTP server for AI agent integration (default :8765)" }

func (c *serveCmd) Run(env *cli.Env) error {
	flags := env.Flags
	addr := flags.Get("addr")
	if addr == "" {
		addr = ":8765"
	}
	if len(addr) > 0 && addr[0] != ':' && !strings.Contains(addr, ":") {
		addr = ":" + addr
	}

	srv := server.New(c.app.Config)
	fmt.Fprintf(env.Stderr, "tvcli server endpoints:\n")
	fmt.Fprintf(env.Stderr, "  GET  /health  — status check\n")
	fmt.Fprintf(env.Stderr, "  POST /compile — compile Pine script source\n")
	fmt.Fprintf(env.Stderr, "  POST /fetch   — fetch OHLCV data\n")
	fmt.Fprintf(env.Stderr, "  POST /clean   — clean chart sessions\n")
	fmt.Fprintf(env.Stderr, "  POST /run     — compile + run Pine script\n")
	fmt.Fprintf(env.Stderr, "\n")
	return srv.Serve(addr)
}
