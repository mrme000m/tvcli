package parsers

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/mrme000m/tvcli/pkg/skill"
)

// fixtureFile returns the optional testdata fixture path for a skill name.
// Skill names use kebab-case ("order-flow"); fixture files use snake_case
// ("order_flow_fixture.json").
func fixtureFile(name string) string {
	return filepath.Join("testdata", strings.ReplaceAll(name, "-", "_")+"_fixture.json")
}

// TestAllSkills_NoDataSmoke is the baseline regression guard for every
// registered skill (all 21). It guarantees that a parser returns a no_data
// result and never panics when fed empty period data. Skills with a
// schema-aware parser are additionally exercised through ParseWithSchema with
// a nil schema (which must behave like ParseOutput).
func TestAllSkills_NoDataSmoke(t *testing.T) {
	for _, s := range skill.All() {
		s := s
		t.Run(s.Name, func(t *testing.T) {
			assertNoData(t, s.Name, s.FormatText, func() skill.SkillResult {
				return s.ParseOutput(nil, nil, "5m", "OANDA:XAUUSD", map[string]string{})
			})
			if s.ParseWithSchema != nil {
				assertNoData(t, s.Name+"[schema]", s.FormatText, func() skill.SkillResult {
					return s.ParseWithSchema(nil, nil, nil, "5m", "OANDA:XAUUSD", map[string]string{})
				})
			}
		})
	}
}

func assertNoData(t *testing.T, name string, format func(skill.SkillResult) string, run func() skill.SkillResult) {
	t.Helper()
	var res skill.SkillResult
	func() {
		defer func() {
			if r := recover(); r != nil {
				t.Fatalf("%s panicked on empty data: %v", name, r)
			}
		}()
		res = run()
	}()
	if res.Status != "no_data" {
		t.Fatalf("%s: expected no_data on empty data, got %q", name, res.Status)
	}
	// A formatter must tolerate a no_data result (nil Structure) without
	// panicking — e.g. xau_scalp's formatXauScalp previously did an unchecked
	// type assertion on s["squeezeOn"].
	if format != nil {
		func() {
			defer func() {
				if r := recover(); r != nil {
					t.Fatalf("%s FormatText panicked on no_data: %v", name, r)
				}
			}()
			format(res)
		}()
	}
}

// TestAllSkills_Fixtures runs real TradingView responses through every skill
// that ships a testdata/<name>_fixture.json. It asserts the parser produces
// an ok status with valid, passing data. Skills without a fixture are covered
// only by the smoke test above.
func TestAllSkills_Fixtures(t *testing.T) {
	for _, s := range skill.All() {
		s := s
		path := fixtureFile(s.Name)
		b, err := os.ReadFile(path)
		if err != nil {
			// No fixture for this skill; covered by the smoke test.
			continue
		}
		t.Run(s.Name, func(t *testing.T) {
			var fixture struct {
				Periods []map[string]any            `json:"periods"`
				Graphic map[string]map[string]any `json:"graphic"`
			}
			if err := json.Unmarshal(b, &fixture); err != nil {
				t.Fatalf("unmarshal %s: %v", path, err)
			}
			res := s.ParseOutput(fixture.Periods, fixture.Graphic, "1h", "OANDA:XAUUSD", map[string]string{})
			assertValid(t, s.Name, res)
			if s.ParseWithSchema != nil {
				res2 := s.ParseWithSchema(fixture.Periods, fixture.Graphic, nil, "1h", "OANDA:XAUUSD", map[string]string{})
				assertValid(t, s.Name+"[schema]", res2)
			}
		})
	}
}

func assertValid(t *testing.T, name string, res skill.SkillResult) {
	t.Helper()
	if res.Status != "ok" {
		t.Fatalf("%s: expected ok on fixture, got %q", name, res.Status)
	}
	if !res.Conformance.HasValidData {
		t.Fatalf("%s: expected valid data on fixture", name)
	}
	if !res.Validation.Passed {
		t.Fatalf("%s: validation should pass on fixture", name)
	}
}
