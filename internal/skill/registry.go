package skill

import (
	"fmt"
	"sort"
	"strings"
)

var (
	skills     = map[string]*Skill{}
	regErrors  []error
)

// Register adds a skill to the global registry. It returns an error (instead
// of silently overwriting) on a duplicate name or an invalid definition.
// Errors are also accumulated in RegErrors() so a command can surface them
// once after all init() registrations have run.
func Register(s *Skill) error {
	if s == nil {
		return fmt.Errorf("skill: nil registration")
	}
	if s.Name == "" {
		return fmt.Errorf("skill: registration missing Name (pineID=%q)", s.PineID)
	}
	if !strings.Contains(s.PineID, ";") {
		return fmt.Errorf("skill %q: PineID %q is missing the ';' separator", s.Name, s.PineID)
	}
	if _, dup := skills[s.Name]; dup {
		err := fmt.Errorf("skill: duplicate registration for %q", s.Name)
		regErrors = append(regErrors, err)
		return err
	}
	skills[s.Name] = s
	return nil
}

// RegErrors returns registration errors collected since process start.
func RegErrors() []error { return regErrors }

// Get returns a skill by name, or nil if not found.
func Get(name string) *Skill {
	return skills[name]
}

// All returns all registered skills sorted by name.
func All() []*Skill {
	out := make([]*Skill, 0, len(skills))
	for _, s := range skills {
		out = append(out, s)
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].Name < out[j].Name
	})
	return out
}

// Names returns all registered skill names sorted.
func Names() []string {
	out := make([]string, 0, len(skills))
	for name := range skills {
		out = append(out, name)
	}
	sort.Strings(out)
	return out
}
