package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"strings"

	"github.com/ch99q/tvcli/internal/cli"
	"github.com/ch99q/tvcli/internal/skill"
)

type skillsCmd struct{}

func (c *skillsCmd) Name() string      { return "skills" }
func (c *skillsCmd) Aliases() []string { return []string{"indicators"} }
func (c *skillsCmd) Synopsis() string  { return "List available indicator skills" }

func (c *skillsCmd) Run(env *cli.Env) error {
	if env.Flags.Has("json") {
		type skillInfo struct {
			Name     string   `json:"name"`
			Synopsis string   `json:"synopsis"`
			PineID   string   `json:"pineId"`
			Inputs   int      `json:"inputs"`
			Presets  []string `json:"presets,omitempty"`
		}
		all := skill.All()
		infos := make([]skillInfo, len(all))
		for i, s := range all {
			presets := make([]string, 0, len(s.Presets))
			for k := range s.Presets {
				presets = append(presets, k)
			}
			infos[i] = skillInfo{
				Name:     s.Name,
				Synopsis: s.Synopsis,
				PineID:   s.PineID,
				Inputs:   len(s.Inputs),
				Presets:  presets,
			}
		}
		b, _ := json.MarshalIndent(infos, "", "  ")
		fmt.Fprintln(env.Stdout, string(b))
		return nil
	}

	printSkillsList(env.Stdout)
	return nil
}

func printSkillsList(w io.Writer) {
	all := skill.All()
	fmt.Fprintf(w, "\nAvailable indicator skills (%d):\n\n", len(all))

	maxName := 0
	for _, s := range all {
		if len(s.Name) > maxName {
			maxName = len(s.Name)
		}
	}

	for _, s := range all {
		presets := ""
		if len(s.Presets) > 0 {
			pk := make([]string, 0, len(s.Presets))
			for k := range s.Presets {
				pk = append(pk, k)
			}
			presets = fmt.Sprintf("  presets: %s", strings.Join(pk, ", "))
		}
		fmt.Fprintf(w, "  tv %-"+fmt.Sprintf("%d", maxName)+"s  %s  (%d inputs)%s\n",
			s.Name, s.Synopsis, len(s.Inputs), presets)
	}

	fmt.Fprintf(w, "\nUsage: tv <skill> [options]  --help for details\n")
	fmt.Fprintf(w, "Example: tv bsv --symbol OANDA:XAUUSD --tf 15m --json --agent\n")
}
