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
	// Surface any registration problems (duplicate names, invalid PineIDs)
	// collected during init().
	if errs := skill.RegErrors(); len(errs) > 0 {
		for _, e := range errs {
			fmt.Fprintf(env.Stderr, "⚠ skill registration: %v\n", e)
		}
	}

	if env.Flags.Has("json") {
		type skillInfo struct {
			Name        string   `json:"name"`
			Synopsis    string   `json:"synopsis"`
			PineID      string   `json:"pineId"`
			Category    string   `json:"category"`
			Tier        string   `json:"tier,omitempty"`
			KnownBroken string   `json:"knownBroken,omitempty"`
			Inputs      int      `json:"inputs"`
			Presets     []string `json:"presets,omitempty"`
		}
		all := skill.All()
		infos := make([]skillInfo, len(all))
		for i, s := range all {
			presets := make([]string, 0, len(s.Presets))
			for k := range s.Presets {
				presets = append(presets, k)
			}
			infos[i] = skillInfo{
				Name:        s.Name,
				Synopsis:    s.Synopsis,
				PineID:      s.PineID,
				Category:    s.EffectiveCategory(),
				Tier:        s.Tier,
				KnownBroken: s.KnownBroken,
				Inputs:      len(s.Inputs),
				Presets:     presets,
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

	for _, s := range all {
		flag := ""
		if s.KnownBroken != "" {
			flag = "  ⚠ " + s.KnownBroken
		} else if s.Tier != "" {
			flag = fmt.Sprintf("  [tier: %s]", s.Tier)
		}
		fmt.Fprintf(w, "  tv %-14s %-10s %s  (%d inputs)%s\n",
			s.Name, s.EffectiveCategory(), s.Synopsis, len(s.Inputs), flag)
		if len(s.Presets) > 0 {
			pk := make([]string, 0, len(s.Presets))
			for k := range s.Presets {
				pk = append(pk, k)
			}
			fmt.Fprintf(w, "      presets: %s\n", strings.Join(pk, ", "))
		}
	}

	fmt.Fprintf(w, "\nUsage: tv <skill> [options]  --help for details\n")
	fmt.Fprintf(w, "Example: tv bsv --symbol OANDA:XAUUSD --tf 15m --json --agent\n")
}
