package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/ch99q/tvcli/internal/cli"
	"github.com/ch99q/tvcli/internal/metadb"
	"github.com/ch99q/tvcli/pkg/pinefacade"
)

type pushCmd struct{ app *App }

func (c *pushCmd) Name() string      { return "push" }
func (c *pushCmd) Aliases() []string { return nil }
func (c *pushCmd) Synopsis() string  { return "Push local changes" }

func (c *pushCmd) Run(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags

	if len(flags.Positional) == 0 {
		return fmt.Errorf("usage: push <id|file> [--force]")
	}

	store, err := metadb.Load(cfg)
	if err != nil {
		return fmt.Errorf("failed to load metadata: %w", err)
	}

	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, durationFromMs(cfg.Timeout))
	target := flags.Positional[0]
	force := flags.Has("force")

	var id, pineID, localPath string
	var entry *metadb.Entry

	if IsNumeric(target) {
		id = target
		entry = store.Get(id)
		if entry == nil {
			return fmt.Errorf("no script #%s", id)
		}
		pineID = entry.PineID
		localPath = entry.LocalPath
	} else {
		localPath, _ = filepath.Abs(target)
		entry = store.FindByLocalPath(localPath)
		if entry != nil {
			id = entry.ID
			pineID = entry.PineID
		}
	}

	if pineID == "" {
		source, _ := os.ReadFile(localPath)
		pineID = pinefacade.ExtractPineIDFromSource(string(source))
	}
	if pineID == "" {
		return fmt.Errorf("no pineId found. Use \"create\" first")
	}

	source, _ := os.ReadFile(localPath)
	sourceStr := string(source)
	localHash := pinefacade.SHA256(sourceStr)

	if !force && entry != nil && entry.RemoteHash == localHash {
		fmt.Fprintln(env.Stdout, "No changes to push. Use --force to push anyway.")
		return nil
	}

	fmt.Fprintln(env.Stdout, "Compiling...")
	compileRes, err := client.Compile(sourceStr, cfg.CookieHeader())
	if err != nil {
		return fmt.Errorf("compile error: %w", err)
	}
	if cr, ok := compileRes.(map[string]any); ok {
		if success, ok := cr["success"].(bool); ok && !success {
			return fmt.Errorf("compilation failed")
		}
	}
	fmt.Fprintln(env.Stdout, "✓ Compiled")

	fmt.Fprintln(env.Stdout, "Pushing...")
	pushRes, err := client.SaveNext(pineID, sourceStr, cfg.CookieHeader())
	if err != nil {
		return fmt.Errorf("push error: %w", err)
	}

	pushedPine := ExtractPineID(pushRes)
	if pushedPine == "" {
		pushedPine = pineID
	}
	pushedPine = strings.ReplaceAll(pushedPine, "USER;USER;", "USER;")

	version := ExtractVersion(pushRes)
	fmt.Fprintf(env.Stdout, "✓ Pushed: %s (version: %s)\n", pushedPine, version)

	if id != "" {
		store.Set(id, metadb.Entry{
			PineID:        pushedPine,
			Owner:         cfg.UserName,
			Access:        pinefacade.AccessFromPineID(pushedPine),
			ScriptType:    pinefacade.ScriptTypeFromSource(sourceStr),
			LocalHash:     localHash,
			RemoteHash:    localHash,
			RemoteVersion: version,
		})
	}
	return nil
}
