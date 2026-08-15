package cmd

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/ch99q/tvcli/internal/cli"
	"github.com/ch99q/tvcli/internal/metadb"
	"github.com/ch99q/tvcli/pkg/pinefacade"
)

type pullCmd struct{ app *App }

func (c *pullCmd) Name() string      { return "pull" }
func (c *pullCmd) Aliases() []string { return nil }
func (c *pullCmd) Synopsis() string  { return "Pull remote script to local" }

func (c *pullCmd) Run(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags

	store, err := metadb.Load(cfg)
	if err != nil {
		return fmt.Errorf("failed to load metadata: %w", err)
	}

	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, durationFromMs(cfg.Timeout))
	var pineID, localPath, scriptName string

	if len(flags.Positional) > 0 {
		target := flags.Positional[0]
		if IsNumeric(target) {
			entry := store.Get(target)
			if entry == nil || entry.PineID == "" {
				return fmt.Errorf("no pineId for #%s", target)
			}
			pineID = entry.PineID
			localPath = entry.LocalPath
			scriptName = entry.Name
		} else if pinefacade.LooksLikePineID(target) {
			pineID = pinefacade.NormalizePineID(target)
			if existing := store.FindByPineID(pineID); existing != nil {
				localPath = existing.LocalPath
				scriptName = existing.Name
			}
		} else {
			return fmt.Errorf("unknown target: %s. Use numeric ID or pineId", target)
		}
	}

	fmt.Fprintf(env.Stdout, "Pulling %s...\n", pineID)
	result, err := client.GetSource(pineID, cfg.CookieHeaderOrEmpty())
	if err != nil {
		return fmt.Errorf("failed to fetch %s: %w", pineID, err)
	}

	if result.Source == "" {
		return fmt.Errorf("pulled empty source")
	}

	if scriptName == "" && result.Meta != nil {
		scriptName = result.Meta.ScriptName
	}
	if result.Access != "" {
		fmt.Fprintf(env.Stdout, "✓ Access: %s\n", result.Access)
	}
	if scriptName == "" {
		scriptName = "script"
	}

	if localPath == "" {
		id := store.NextID()
		fileName := id + "--" + Slugify(scriptName) + ".pine"
		store.Set(id, metadb.Entry{
			Name:          scriptName,
			PineID:        pineID,
			LocalPath:     fileName,
			LocalHash:     pinefacade.SHA256(result.Source),
			RemoteHash:    pinefacade.SHA256(result.Source),
			RemoteVersion: result.Meta.Version,
		})
		localPath = fileName
		fmt.Fprintf(env.Stdout, "✓ Tracked as #%s\n", id)
	}

	absPath := filepath.Join(cfg.DataDir, localPath)
	os.MkdirAll(filepath.Dir(absPath), 0755)
	os.WriteFile(absPath, []byte(result.Source), 0644)
	fmt.Fprintf(env.Stdout, "✓ Saved: %s\n", localPath)
	return nil
}
