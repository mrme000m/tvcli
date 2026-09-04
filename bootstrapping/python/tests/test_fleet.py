import json
import tempfile
import unittest
from pathlib import Path

from prime_stack.config import Config
from prime_stack.core import Context
from prime_stack.stages.fleet import (load_env_file, preset_marker_status,
                                      run_presets, upsert_grid_rows)


class PresetMarkerTests(unittest.TestCase):
    HASHES = {"preset.yml": "aaa", "agent.cordis.yml": "bbb"}

    def test_marker_matches_and_identical(self):
        marker = {"managedBy": "prime-stack-bootstrap", "files": self.HASHES}
        self.assertEqual(preset_marker_status(marker, self.HASHES, "prime-stack-bootstrap"), "unchanged")

    def test_marker_matches_sha_drift(self):
        marker = {"managedBy": "prime-stack-bootstrap", "files": {"preset.yml": "old"}}
        self.assertEqual(preset_marker_status(marker, self.HASHES, "prime-stack-bootstrap"), "update")

    def test_foreign_marker_is_user_owned(self):
        marker = {"managedBy": "someone-else", "files": self.HASHES}
        self.assertEqual(preset_marker_status(marker, self.HASHES, "prime-stack-bootstrap"), "user-owned")

    def test_empty_marker_is_user_owned(self):
        self.assertEqual(preset_marker_status({}, self.HASHES, "prime-stack-bootstrap"), "user-owned")


class GridRowUpsertTests(unittest.TestCase):
    KEYS = {"WT_API_KEY": "k1", "WT_API_SECRET": "s1"}

    def test_adds_both_rows(self):
        doc, rows = upsert_grid_rows([], self.KEYS, "/cloak", "https://mcp")
        self.assertEqual(rows, ["mcp-wundertrading", "wt-tools"])
        mcp = doc[0]
        self.assertEqual(mcp["id"], "mcp-wundertrading")
        self.assertEqual(mcp["config"]["headers"]["X-API-Key"], "k1")
        self.assertEqual(mcp["config"]["headers"]["X-Secret-Key"], "s1")
        self.assertEqual(doc[1], {"id": "wt-tools", "config": {"cloakDir": "/cloak"}})

    def test_missing_keys_skip_mcp_row(self):
        doc, rows = upsert_grid_rows([], {"WT_API_KEY": "", "WT_API_SECRET": "s"}, "/c", "https://mcp")
        self.assertEqual(rows, ["wt-tools"])
        self.assertEqual(len(doc), 1)

    def test_user_rows_preserved_and_converges(self):
        user_row = {"id": "user-thing", "config": {}}
        doc, rows = upsert_grid_rows([user_row, {"id": "wt-tools", "config": {"cloakDir": "/old"}}],
                                      self.KEYS, "/cloak", "https://mcp")
        self.assertEqual(doc[0], user_row)
        self.assertEqual([r["id"] for r in doc], ["user-thing", "mcp-wundertrading", "wt-tools"])
        # second run over the persisted order is a no-op
        doc2, rows2 = upsert_grid_rows(json.loads(json.dumps(doc)), self.KEYS, "/cloak", "https://mcp")
        self.assertEqual(json.dumps(doc2, sort_keys=True), json.dumps(doc, sort_keys=True))
        self.assertEqual(rows2, ["mcp-wundertrading", "wt-tools"])


class EnvFileTests(unittest.TestCase):
    def test_load_env_file(self):
        keys = load_env_file("# comment\nWT_API_KEY=abc\n WT_API_SECRET = def\nEMPTY=\n")
        self.assertEqual(keys, {"WT_API_KEY": "abc", "WT_API_SECRET": "def", "EMPTY": ""})



class RunPresetsTests(unittest.TestCase):
    """run_presets marker semantics end-to-end (incl. the fresh-install path)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = self.root / "workspace"
        self.src = self.ws / "bootstrapping" / "presets"
        self.home = self.root / "home"
        self.dsh = self.home / ".dsh"
        self.src.mkdir(parents=True)
        self.dsh.mkdir(parents=True)
        for n in ("demo", "userdir"):
            (self.src / n).mkdir()
            (self.src / n / "preset.yml").write_text(f"name: {n}\norder: 1\ndescription: >\n  uses @TV_WORKSPACE@\n")
            (self.src / n / "agent.cordis.yml").write_text("# agent @CLOAK_DIR@\n")
        self.cfg = Config(workspace=self.ws, home=self.home, dsh_home=self.dsh)
        self.cfg.fleet_presets = ["demo", "userdir"]

    def tearDown(self):
        self._tmp.cleanup()

    def test_fresh_preset_installs_then_converges(self):
        r1 = run_presets(self.cfg, Context())
        self.assertEqual(r1.details["presets"]["demo"], "installed")
        self.assertTrue(r1.changed)
        text = (self.cfg.preset_root / "demo" / "preset.yml").read_text()
        self.assertIn(str(self.ws), text)  # placeholders rendered
        marker = json.loads((self.cfg.preset_root / "demo" / self.cfg.fleet_preset_marker).read_text())
        self.assertEqual(marker["managedBy"], "prime-stack-bootstrap")
        r2 = run_presets(self.cfg, Context())
        self.assertEqual(r2.details["presets"]["demo"], "unchanged")
        self.assertFalse(r2.changed)

    def test_user_dir_without_marker_is_preserved(self):
        dst = self.cfg.preset_root / "userdir"
        dst.mkdir(parents=True)
        (dst / "preset.yml").write_text("name: user-owned\n")
        r = run_presets(self.cfg, Context())
        self.assertEqual(r.details["presets"]["userdir"], "preserved: user-owned (no prime-stack marker)")
        self.assertEqual((dst / "preset.yml").read_text(), "name: user-owned\n")


if __name__ == "__main__":
    unittest.main()
