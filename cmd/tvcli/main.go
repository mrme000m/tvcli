package main

import (
	"fmt"
	"io"
	"os"

	"github.com/ch99q/tvcli/internal/cli"
	"github.com/ch99q/tvcli/internal/cmd"
	"github.com/ch99q/tvcli/internal/config"
)

func main() {
	cfg := config.Load()
	args := os.Args[1:]

	if len(args) == 0 {
		cmd.PrintHelp(os.Stdout)
		return
	}

	name := args[0]

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
