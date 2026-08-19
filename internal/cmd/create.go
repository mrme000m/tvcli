package cmd

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/internal/metadb"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
	"gopkg.in/yaml.v3"
)

type createCmd struct{ app *App }

func (c *createCmd) Name() string      { return "create" }
func (c *createCmd) Aliases() []string { return []string{"new"} }
func (c *createCmd) Synopsis() string  { return "Create new remote script" }

func (c *createCmd) Run(env *cli.Env) error {
	cfg := c.app.Config
	flags := env.Flags

	if len(flags.Positional) == 0 {
		return fmt.Errorf("usage: create <file.pine> [--name \"Script Name\"]")
	}
	filePath := flags.Positional[0]

	absPath, _ := filepath.Abs(filePath)
	source, err := os.ReadFile(absPath)
	if err != nil {
		return fmt.Errorf("file not found: %s", filePath)
	}
	sourceStr := string(source)

	store, err := metadb.Load(cfg)
	if err != nil {
		return fmt.Errorf("failed to load metadata: %w", err)
	}

	if existing := store.FindByLocalPath(absPath); existing != nil {
		fmt.Fprintf(env.Stdout, "Script already tracked as #%s. Use \"push\" to update.\n", existing.ID)
		return nil
	}

	fmt.Fprintln(env.Stdout, "Compiling...")
	client := pinefacade.NewClient(cfg.PineFacadeURL, cfg.UserName, durationFromMs(cfg.Timeout))
	compileRes, err := client.Compile(sourceStr, cfg.CookieHeader())
	if err != nil {
		return fmt.Errorf("compile error: %w", err)
	}
	if cr, ok := compileRes.(map[string]any); ok {
		if success, ok := cr["success"].(bool); ok && !success {
			fmt.Fprintln(env.Stdout, "Compilation failed:")
			if result, ok := cr["result"].(map[string]any); ok {
				if errors, ok := result["errors"].([]any); ok {
					for i, e := range errors {
						if i >= 5 {
							break
						}
						fmt.Fprintf(env.Stdout, "  %v\n", e)
					}
				}
			}
			return fmt.Errorf("fix compilation errors before creating")
		}
	}
	fmt.Fprintln(env.Stdout, "✓ Compiled")

	name := flags.Get("name")
	if name == "" {
		base := filepath.Base(absPath)
		name = strings.TrimSuffix(base, filepath.Ext(base))
	}
	fmt.Fprintf(env.Stdout, "Creating remote script: %s\n", name)

	createRes, err := client.SaveNew(sourceStr, name, cfg.CookieHeader())
	if err != nil {
		return fmt.Errorf("create error: %w", err)
	}

	pineID := ExtractPineID(createRes)
	if pineID == "" {
		fmt.Fprintf(env.Stdout, "Response: %v\n", createRes)
		return fmt.Errorf("could not extract pineId from create response")
	}
	pineID = strings.ReplaceAll(pineID, "USER;USER;", "USER;")

	fmt.Fprintf(env.Stdout, "✓ Created: %s\n", pineID)

	id := store.NextID()
	store.Set(id, metadb.Entry{
		Name:          name,
		PineID:        pineID,
		Owner:         cfg.UserName,
		Access:        pinefacade.AccessFromPineID(pineID),
		ScriptType:    pinefacade.ScriptTypeFromSource(sourceStr),
		LocalPath:     RelPath(cfg, absPath),
		LocalHash:     pinefacade.SHA256(sourceStr),
		RemoteHash:    pinefacade.SHA256(sourceStr),
		RemoteVersion: "1.0",
	})

	inputsData := pinefacade.GenerateInputsYAML(sourceStr, name, pineID)
	inputsPath := filepath.Join(cfg.DataDir, "inputs", name+"_inputs.yaml")
	os.MkdirAll(filepath.Dir(inputsPath), 0755)
	b, _ := yaml.Marshal(inputsData)
	os.WriteFile(inputsPath, b, 0644)
	fmt.Fprintf(env.Stdout, "✓ Generated: %s\n", inputsPath)

	fmt.Fprintf(env.Stdout, "\n✓ Created script #%s\n", id)
	return nil
}
