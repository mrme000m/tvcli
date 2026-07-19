package skill

import "sort"

var skills = map[string]*Skill{}

// Register adds a skill to the global registry.
func Register(s *Skill) {
	skills[s.Name] = s
}

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
