import json
import tempfile
import unittest
from pathlib import Path

from prime_stack.stages.fleet import (load_env_file, preset_marker_status,
                                      qd_env_block, upsert_grid_rows)


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


class QdEnvBlockTests(unittest.TestCase):
    def test_block_references_path_and_guards_names(self):
        block = qd_env_block("/ws/browser-debug/secrets/runtime/qd-agent.env")
        self.assertIn('if [ -f "/ws/browser-debug/secrets/runtime/qd-agent.env" ]', block)
        self.assertIn('case "$__qd_k" in *[!A-Za-z0-9_]*) continue ;; esac', block)
        self.assertIn('[ -n "${!__qd_k:-}" ] && continue', block)

    def test_block_is_valid_guarded_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "qd-agent.env"
            env_file.write_text("QD_AGENT_TOKEN=tok123\nBAD NAME=x\n# comment\n")
            rc_file = Path(tmp) / ".profile"
            rc_file.write_text(qd_env_block(str(env_file)) + "\n")
            import subprocess
            proc = subprocess.run(
                ["bash", "-c", f'source "{rc_file}" && echo "$QD_AGENT_TOKEN"'],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), "tok123")
            proc2 = subprocess.run(
                ["bash", "-c", f'QD_AGENT_TOKEN=fromenv && source "{rc_file}" && echo "$QD_AGENT_TOKEN"'],
                capture_output=True, text=True,
            )
            self.assertEqual(proc2.stdout.strip(), "fromenv")


if __name__ == "__main__":
    unittest.main()
