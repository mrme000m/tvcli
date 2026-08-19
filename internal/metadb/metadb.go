// Package metadb loads and persists the .tv-meta.json script registry.
package metadb

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/ch99q/tvcli/internal/config"
	"github.com/ch99q/tvcli/pkg/pinefacade"
)

// Entry is one tracked Pine script's local metadata.
type Entry struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	PineID string `json:"pineId"`
	// Owner is the TradingView username that created/owns the script. It is
	// captured at create/push/pull time so private (USER;) scripts can be
	// attributed and re-checked against the current session's saved list.
	Owner string `json:"owner,omitempty"`
	// Access classifies the script's visibility: "public" (PUB;), "private"
	// (USER;), or "invite-only" (PRIVATE/STD/PRO;). Derived from the Pine ID
	// namespace — see pinefacade.AccessFromPineID.
	Access string `json:"access,omitempty"`
	// ScriptType is "strategy" (emits signals) or "indicator" (analysis
	// only), detected from the Pine source declaration.
	ScriptType    string `json:"scriptType,omitempty"`
	LocalPath     string `json:"localPath"`
	LocalHash     string `json:"localHash"`
	RemoteHash    string `json:"remoteHash"`
	RemoteVersion string `json:"remoteVersion"`
	UpdatedAt     string `json:"updatedAt"`
}

// Store is the in-memory .tv-meta.json-backed script registry.
type Store struct {
	dataDir  string
	metaFile string
	scripts  map[string]*Entry
}

// Load reads (or initializes) the meta store described by cfg.
// Returns an empty store if the meta file is missing or unreadable.
func Load(cfg *config.Config) (*Store, error) {
	absMeta, _ := filepath.Abs(cfg.MetaFile)
	ms := &Store{
		dataDir:  cfg.DataDir,
		metaFile: absMeta,
		scripts:  make(map[string]*Entry),
	}

	os.MkdirAll(cfg.DataDir, 0755)
	os.MkdirAll(filepath.Join(cfg.DataDir, "inputs"), 0755)

	data, err := os.ReadFile(absMeta)
	if err != nil {
		return ms, nil
	}

	var raw struct {
		Version int               `json:"version"`
		Scripts map[string]*Entry `json:"scripts"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return ms, nil
	}
	ms.scripts = raw.Scripts
	return ms, nil
}

// Save writes the store back to disk.
func (ms *Store) Save() {
	data := map[string]any{
		"version": 1,
		"scripts": ms.scripts,
	}
	b, _ := json.MarshalIndent(data, "", "  ")
	os.WriteFile(ms.metaFile, b, 0644)
}

func (ms *Store) Get(id string) *Entry { return ms.scripts[id] }

// Set inserts or updates an entry, preserving fields that are empty on the
// incoming entry, then persists immediately.
func (ms *Store) Set(id string, entry Entry) {
	if existing, ok := ms.scripts[id]; ok {
		if entry.Name == "" {
			entry.Name = existing.Name
		}
		if entry.PineID == "" {
			entry.PineID = existing.PineID
		}
		if entry.Owner == "" {
			entry.Owner = existing.Owner
		}
		if entry.Access == "" {
			entry.Access = existing.Access
		}
		if entry.ScriptType == "" {
			entry.ScriptType = existing.ScriptType
		}
		if entry.LocalPath == "" {
			entry.LocalPath = existing.LocalPath
		}
		if entry.LocalHash == "" {
			entry.LocalHash = existing.LocalHash
		}
		if entry.RemoteHash == "" {
			entry.RemoteHash = existing.RemoteHash
		}
		if entry.RemoteVersion == "" {
			entry.RemoteVersion = existing.RemoteVersion
		}
	}
	entry.ID = id
	entry.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	ms.scripts[id] = &entry
	ms.Save()
}

func (ms *Store) Delete(id string) {
	delete(ms.scripts, id)
	ms.Save()
}

func (ms *Store) List() []*Entry {
	var result []*Entry
	for _, s := range ms.scripts {
		result = append(result, s)
	}
	return result
}

func (ms *Store) NextID() string {
	max := 0
	for id := range ms.scripts {
		n := 0
		fmt.Sscanf(id, "%d", &n)
		if n > max {
			max = n
		}
	}
	return fmt.Sprintf("%d", max+1)
}

func (ms *Store) FindByPineID(pineID string) *Entry {
	norm := pinefacade.NormalizePineID(pineID)
	for _, s := range ms.scripts {
		if pinefacade.NormalizePineID(s.PineID) == norm {
			return s
		}
	}
	return nil
}

func (ms *Store) FindByLocalPath(filePath string) *Entry {
	abs, _ := filepath.Abs(filePath)
	for _, s := range ms.scripts {
		sAbs, _ := filepath.Abs(s.LocalPath)
		if sAbs == abs {
			return s
		}
	}
	return nil
}
