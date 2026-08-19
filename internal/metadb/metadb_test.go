package metadb

import (
	"path/filepath"
	"testing"

	"github.com/ch99q/tvcli/internal/config"
)

// withTempConfig returns a Config whose DataDir and MetaFile point at a
// temporary directory. The meta file does NOT exist yet — Load must
// tolerate that.
func withTempConfig(t *testing.T) *config.Config {
	t.Helper()
	dir := t.TempDir()
	return &config.Config{
		DataDir:  filepath.Join(dir, ".tv-scripts"),
		MetaFile: filepath.Join(dir, ".tv-meta.json"),
	}
}

func TestLoadMissingFileReturnsEmpty(t *testing.T) {
	cfg := withTempConfig(t)
	store, err := Load(cfg)
	if err != nil {
		t.Fatalf("Load on missing file: %v", err)
	}
	if store == nil {
		t.Fatal("Load returned nil store")
	}
	if got := store.List(); len(got) != 0 {
		t.Errorf("expected empty list, got %d entries", len(got))
	}
}

func TestSetGetRoundTrip(t *testing.T) {
	cfg := withTempConfig(t)
	store, _ := Load(cfg)

	store.Set("1", Entry{Name: "My Script", PineID: "PUB;abc", LocalPath: "/tmp/x.pine"})
	got := store.Get("1")
	if got == nil {
		t.Fatal("Get returned nil after Set")
	}
	if got.Name != "My Script" || got.PineID != "PUB;abc" {
		t.Errorf("Get = %+v", got)
	}
	if got.UpdatedAt == "" {
		t.Error("UpdatedAt not set")
	}
	if got.ID != "1" {
		t.Errorf("ID = %q, want 1", got.ID)
	}
}

func TestSetPreservesExistingFields(t *testing.T) {
	cfg := withTempConfig(t)
	store, _ := Load(cfg)
	store.Set("1", Entry{Name: "Old", PineID: "PUB;1", LocalPath: "/old", LocalHash: "h1"})

	// Update only the remote hash; other fields must be preserved.
	store.Set("1", Entry{RemoteHash: "h2"})

	got := store.Get("1")
	if got.Name != "Old" {
		t.Errorf("Name overwritten: %q", got.Name)
	}
	if got.PineID != "PUB;1" {
		t.Errorf("PineID overwritten: %q", got.PineID)
	}
	if got.LocalHash != "h1" {
		t.Errorf("LocalHash overwritten: %q", got.LocalHash)
	}
	if got.RemoteHash != "h2" {
		t.Errorf("RemoteHash not updated: %q", got.RemoteHash)
	}
}

func TestNextID(t *testing.T) {
	cfg := withTempConfig(t)
	store, _ := Load(cfg)
	if id := store.NextID(); id != "1" {
		t.Errorf("first NextID = %q, want 1", id)
	}
	store.Set("1", Entry{Name: "a"})
	store.Set("5", Entry{Name: "b"})
	if id := store.NextID(); id != "6" {
		t.Errorf("second NextID = %q, want 6", id)
	}
}

func TestFindByPineID(t *testing.T) {
	cfg := withTempConfig(t)
	store, _ := Load(cfg)
	store.Set("1", Entry{PineID: "PUB;abc"})
	store.Set("2", Entry{PineID: "USER;me"})

	if got := store.FindByPineID("PUB;abc"); got == nil || got.ID != "1" {
		t.Errorf("FindByPineID(PUB;abc) = %+v, want ID=1", got)
	}
	// URL-encoded variant should normalize.
	if got := store.FindByPineID("PUB%3Babc"); got == nil {
		t.Error("FindByPineID did not normalize %3B → ;")
	}
	if got := store.FindByPineID("PUB;nope"); got != nil {
		t.Errorf("FindByPineID(missing) = %+v, want nil", got)
	}
}

func TestFindByLocalPath(t *testing.T) {
	cfg := withTempConfig(t)
	store, _ := Load(cfg)
	store.Set("1", Entry{LocalPath: "/tmp/x.pine"})

	if got := store.FindByLocalPath("/tmp/x.pine"); got == nil || got.ID != "1" {
		t.Errorf("FindByLocalPath absolute = %+v, want ID=1", got)
	}
	if got := store.FindByLocalPath("/tmp/./x.pine"); got == nil {
		t.Error("FindByLocalPath should resolve ./ paths to the same abs path")
	}
	if got := store.FindByLocalPath("/nonexistent"); got != nil {
		t.Errorf("FindByLocalPath(missing) = %+v, want nil", got)
	}
}

func TestDelete(t *testing.T) {
	cfg := withTempConfig(t)
	store, _ := Load(cfg)
	store.Set("1", Entry{Name: "a"})
	store.Set("2", Entry{Name: "b"})
	store.Delete("1")
	if store.Get("1") != nil {
		t.Error("Delete did not remove entry 1")
	}
	if store.Get("2") == nil {
		t.Error("Delete removed the wrong entry")
	}
}

func TestLoadPersistsAcrossInstances(t *testing.T) {
	cfg := withTempConfig(t)
	store1, _ := Load(cfg)
	store1.Set("1", Entry{Name: "Persisted", PineID: "PUB;xyz"})

	// New Store instance reading the same file must see the entry.
	store2, _ := Load(cfg)
	got := store2.Get("1")
	if got == nil || got.Name != "Persisted" {
		t.Errorf("entry did not persist: %+v", got)
	}
}

func TestSetPreservesOwnershipMetadata(t *testing.T) {
	cfg := withTempConfig(t)
	store, _ := Load(cfg)

	store.Set("1", Entry{
		Name:       "Custom",
		PineID:     "USER;abc",
		Owner:      "siner15",
		Access:     "private",
		ScriptType: "indicator",
	})

	// A partial update (e.g. push refresh) must keep the ownership metadata.
	store.Set("1", Entry{RemoteHash: "h2"})
	got := store.Get("1")
	if got.Owner != "siner15" {
		t.Errorf("Owner = %q, want siner15", got.Owner)
	}
	if got.Access != "private" {
		t.Errorf("Access = %q, want private", got.Access)
	}
	if got.ScriptType != "indicator" {
		t.Errorf("ScriptType = %q, want indicator", got.ScriptType)
	}
}
