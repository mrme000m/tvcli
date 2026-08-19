package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"strings"

	"github.com/mrme000m/tvcli/internal/cli"
	"github.com/mrme000m/tvcli/pkg/skill"
	"github.com/mrme000m/tvcli/internal/service"
	"github.com/mrme000m/tvcli/pkg/pinefacade"
	"github.com/mrme000m/tvcli/pkg/tradingview"
)

// inputsCmd implements `tv inputs <pineId|skillName>` — a diagnostic that
// fetches Pine's actual declared inputs from the metaInfo and prints them
// alongside the Go skill's InputDef declarations, flagging type/default
// mismatches and phantom inputs (Go declares an in_N that Pine does not
// have). The whole point: fix skills without manually dumping metaInfo.
type inputsCmd struct{ app *App }

func (c *inputsCmd) Name() string     { return "inputs" }
func (c *inputsCmd) Aliases() []string { return nil }
func (c *inputsCmd) Synopsis() string  { return "Inspect Pine inputs for a script or skill (Pine-actual vs Go-declared)" }

func (c *inputsCmd) Run(env *cli.Env) error {
	flags := env.Flags
	if len(flags.Positional) == 0 {
		return fmt.Errorf("usage: inputs <pineId|skillName> [--json] [--raw]")
	}
	arg := flags.Positional[0]

	var pineID string
	var s *skill.Skill
	if pinefacade.LooksLikePineID(arg) {
		pineID = arg
	} else {
		s = skill.Get(arg)
		if s == nil {
			return fmt.Errorf("unknown skill '%s' (run 'tv skills' to list)", arg)
		}
		pineID = s.PineID
	}

	// Load with no input overrides — defaults only, so we see Pine's truth.
	indicator, err := service.LoadIndicator(c.app.Config, pineID, map[string]string{}, nil)
	if err != nil {
		return fmt.Errorf("load indicator: %w", err)
	}

	if flags.Has("raw") {
		return c.printRaw(env.Stdout, indicator)
	}

	pineInputs := c.pineInputs(indicator)
	if flags.Has("json") {
		return c.printJSON(env.Stdout, pineID, s, pineInputs)
	}
	c.printTable(env.Stdout, pineID, s, pineInputs)
	return nil
}

// pineRow is one row of the comparison: Pine truth + (optional) Go decl.
type pineRow struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Type     string `json:"type"`
	Defval   any    `json:"defval"`
	IsFake   bool   `json:"isFake"`
	IsHidden bool   `json:"isHidden"`
	// Go declaration (empty if no skill or Pine input has no Go match).
	GoName   string `json:"goName,omitempty"`
	GoType   string `json:"goType,omitempty"`
	GoDef    any    `json:"goDefault,omitempty"`
	Status   string `json:"status"` // ok | type-mismatch | missing-in-go | extra-in-pine | go-only
	Note     string `json:"note,omitempty"`
}

// pineInputs walks the indicator's Inputs (preserving InputsOrder) and
// attaches the matching Go InputDef when a skill is provided. It also
// surfaces Go inputs whose TVInputID does not exist in Pine (phantom).
func (c *inputsCmd) pineInputs(ind *tradingview.PineIndicator) []pineRow {
	rows := make([]pineRow, 0, len(ind.InputsOrder))
	for _, id := range ind.InputsOrder {
		def := ind.Inputs[id]
		if def == nil {
			continue
		}
		rows = append(rows, pineRow{
			ID:       id,
			Name:     def.Name,
			Type:     def.Type,
			Defval:   def.Value,
			IsFake:   def.IsFake,
			IsHidden: def.IsHidden,
			Status:   "ok",
		})
	}
	return rows
}

func (c *inputsCmd) printTable(w io.Writer, pineID string, s *skill.Skill, rows []pineRow) {
	fmt.Fprintf(w, "Pine: %s\n", pineID)
	if s != nil {
		fmt.Fprintf(w, "Skill: %s\n", s.Name)
	}
	fmt.Fprintf(w, "Inputs: %d\n\n", len(rows))

	// Build a TVInputID -> Go InputDef map for the diff.
	goByID := map[string]skill.InputDef{}
	if s != nil {
		for _, d := range s.Inputs {
			goByID[d.TVInputID] = d
		}
	}
	used := map[string]bool{}

	fmt.Fprintf(w, "%-8s %-32s %-10s %-12s %-6s %-32s %-10s %s\n",
		"ID", "Pine Name", "Pine Type", "Pine Defval", "Fake", "Go Name", "Go Type", "Status")
	fmt.Fprintln(w, strings.Repeat("-", 130))

	for _, r := range rows {
		used[r.ID] = true
		status := "ok"
		var goName, goType string
		if s != nil {
			if gd, ok := goByID[r.ID]; ok {
				goName = gd.Name
				goType = gd.Type
				if !typeMatches(gd.Type, r.Type) {
					status = "TYPE-MISMATCH"
				}
			} else if !r.IsFake && !r.IsHidden {
				// Pine input exists but Go does not declare it.
				status = "missing-in-go"
			}
		}
		fmt.Fprintf(w, "%-8s %-32s %-10s %-12v %-6v %-32s %-10s %s\n",
			r.ID, truncate(r.Name, 32), r.Type, formatVal(r.Defval), r.IsFake, truncate(goName, 32), goType, status)
	}

	// Phantom Go inputs (declared but Pine has no such ID).
	if s != nil {
		for _, d := range s.Inputs {
			if !used[d.TVInputID] {
				fmt.Fprintf(w, "%-8s %-32s %-10s %-12s %-6s %-32s %-10s %s\n",
					d.TVInputID, "(not in Pine)", "-", "-", "-",
					truncate(d.Name, 32), d.Type, "GO-ONLY/PHANTOM")
			}
		}
	}
}

func (c *inputsCmd) printJSON(w io.Writer, pineID string, s *skill.Skill, rows []pineRow) error {
	goByID := map[string]skill.InputDef{}
	if s != nil {
		for _, d := range s.Inputs {
			goByID[d.TVInputID] = d
		}
	}
	used := map[string]bool{}
	out := []pineRow{}
	for _, r := range rows {
		used[r.ID] = true
		if s != nil {
			if gd, ok := goByID[r.ID]; ok {
				r.GoName = gd.Name
				r.GoType = gd.Type
				r.GoDef = gd.Default
				if !typeMatches(gd.Type, r.Type) {
					r.Status = "type-mismatch"
				}
			} else if !r.IsFake && !r.IsHidden {
				r.Status = "missing-in-go"
			}
		}
		out = append(out, r)
	}
	if s != nil {
		for _, d := range s.Inputs {
			if !used[d.TVInputID] {
				out = append(out, pineRow{
					ID:     d.TVInputID,
					Status: "go-only",
					GoName: d.Name,
					GoType: d.Type,
					GoDef:  d.Default,
					Note:   "declared in Go but Pine has no such input",
				})
			}
		}
	}
	b, _ := json.MarshalIndent(map[string]any{
		"pineId":  pineID,
		"skill":   skillNameOrNil(s),
		"inputs":  out,
		"count":   len(out),
	}, "", "  ")
	fmt.Fprintln(w, string(b))
	return nil
}

func (c *inputsCmd) printRaw(w io.Writer, ind *tradingview.PineIndicator) error {
	// Dump in InputID order for stable diffs.
	ordered := make([]map[string]any, 0, len(ind.InputsOrder))
	for _, id := range ind.InputsOrder {
		def := ind.Inputs[id]
		if def == nil {
			continue
		}
		ordered = append(ordered, map[string]any{
			"id":       id,
			"name":     def.Name,
			"type":     def.Type,
			"defval":   def.Value,
			"isFake":   def.IsFake,
			"isHidden": def.IsHidden,
			"options":  def.Options,
		})
	}
	b, _ := json.MarshalIndent(ordered, "", "  ")
	fmt.Fprintln(w, string(b))
	return nil
}

// typeMatches normalizes Go's loose type names against Pine's actual type
// strings. "int" in Go corresponds to "integer" in Pine; "string" matches
// "text"; "float" matches "float"; "bool" matches "bool".
func typeMatches(goType, pineType string) bool {
	g := strings.ToLower(strings.TrimSpace(goType))
	p := strings.ToLower(strings.TrimSpace(pineType))
	if g == p {
		return true
	}
	switch g {
	case "int":
		return p == "integer"
	case "string":
		return p == "text" || p == "symbol" || p == "session" || p == "resolution"
	case "float":
		return p == "float"
	case "bool":
		return p == "bool"
	}
	return false
}

func formatVal(v any) string {
	if v == nil {
		return ""
	}
	b, _ := json.Marshal(v)
	s := string(b)
	if len(s) > 12 {
		s = s[:12] + "…"
	}
	return s
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n-1] + "…"
}

func skillNameOrNil(s *skill.Skill) string {
	if s == nil {
		return ""
	}
	return s.Name
}
