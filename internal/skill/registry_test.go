package skill

import "testing"

func TestRegisterValid(t *testing.T) {
	s := &Skill{Name: "test-valid", PineID: "PUB;abc"}
	if err := Register(s); err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	if Get("test-valid") != s {
		t.Fatal("registered skill not retrievable")
	}
}

func TestRegisterDuplicate(t *testing.T) {
	before := len(RegErrors())
	if err := Register(&Skill{Name: "test-dup", PineID: "PUB;a"}); err != nil {
		t.Fatalf("first register: %v", err)
	}
	if err := Register(&Skill{Name: "test-dup", PineID: "PUB;b"}); err == nil {
		t.Fatal("expected error for duplicate name")
	}
	if len(RegErrors()) <= before {
		t.Fatal("duplicate not recorded in RegErrors")
	}
}

func TestRegisterInvalid(t *testing.T) {
	if err := Register(&Skill{Name: "", PineID: "PUB;a"}); err == nil {
		t.Fatal("expected error for empty name")
	}
	if err := Register(&Skill{Name: "x", PineID: "noSeparator"}); err == nil {
		t.Fatal("expected error for PineID missing ';'")
	}
}

func TestEffectiveCategory(t *testing.T) {
	cases := map[string]string{
		"my-swing":   "smc",
		"vol-vp":     "volume",
		"sr-levels":  "levels",
		"mtf-x":      "trend",
		"weird-thing": "other",
	}
	for name, want := range cases {
		s := &Skill{Name: name, PineID: "PUB;x"}
		if got := s.EffectiveCategory(); got != want {
			t.Fatalf("%s: got %q want %q", name, got, want)
		}
	}
	s := &Skill{Name: "foo", PineID: "PUB;x", Category: "custom"}
	if s.EffectiveCategory() != "custom" {
		t.Fatal("explicit Category should win over inference")
	}
}
