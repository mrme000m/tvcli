import json
import os
import tempfile
import unittest
from pathlib import Path

from prime_stack.core import Context, marker_block_text
from prime_stack.stages import cloudflare as cf
from prime_stack.stages.cloudflare import (AUTH_BLOCK, AUTH_MARKER_BEGIN,
                                           AUTH_MARKER_END,
                                           cloudflare_mcp_row, link_state,
                                           merge_opencode_mcp,
                                           plan_skill_links, upsert_row)


class SkillLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vendor = self.root / "vendor"
        for name in ("wrangler", "cloudflare"):
            (self.vendor / "skills" / name).mkdir(parents=True)
            (self.vendor / "skills" / name / "SKILL.md").write_text("# x\n")
        self.dest_root = self.root / "skills"

    def test_plan_pairs(self):
        pairs = plan_skill_links(self.vendor, self.dest_root, ["wrangler", "cloudflare-one"])
        self.assertEqual(pairs, [
            (self.vendor / "skills" / "wrangler", self.dest_root / "wrangler"),
            (self.vendor / "skills" / "cloudflare-one", self.dest_root / "cloudflare-one"),
        ])

    def test_link_state_transitions(self):
        src = self.vendor / "skills" / "wrangler"
        dest = self.dest_root / "wrangler"
        self.assertEqual(link_state(dest, src), "missing")
        self.dest_root.mkdir(parents=True)
        os.symlink(src, dest)
        self.assertEqual(link_state(dest, src), "current")
        other = self.vendor / "skills" / "cloudflare"
        os.symlink(other, self.dest_root / "other")
        self.assertEqual(link_state(self.dest_root / "other", src), "stale")
        (self.dest_root / "owned.md").write_text("user file")
        self.assertEqual(link_state(self.dest_root / "owned.md", src), "stale")


class OpencodeMcpTests(unittest.TestCase):
    def test_merge_adds_server_and_preserves_rest(self):
        doc = {"$schema": "https://opencode.ai/config.json", "theme": "keep"}
        merge_opencode_mcp(doc)
        self.assertEqual(doc["mcp"]["cloudflare"],
                         {"type": "remote", "url": cf.CF_MCP_URL, "enabled": True})
        self.assertEqual(doc["theme"], "keep")

    def test_merge_is_idempotent(self):
        doc = {}
        merge_opencode_mcp(doc)
        before = json.dumps(doc, sort_keys=True)
        merge_opencode_mcp(doc)
        self.assertEqual(json.dumps(doc, sort_keys=True), before)

    def test_merge_preserves_other_servers(self):
        doc = {"mcp": {"local-x": {"type": "local", "command": "x"}}}
        merge_opencode_mcp(doc)
        self.assertEqual(doc["mcp"]["local-x"], {"type": "local", "command": "x"})
        self.assertIn("cloudflare", doc["mcp"])


class DshRowTests(unittest.TestCase):
    def test_row_shape(self):
        row = cloudflare_mcp_row()
        self.assertEqual(row["id"], "mcp-cloudflare")
        self.assertEqual(row["config"]["transport"], "streamable-http")
        self.assertEqual(row["config"]["url"], cf.CF_MCP_URL)
        self.assertNotIn("headers", row["config"])  # no secrets in the patch

    def test_upsert_is_idempotent_and_replaces(self):
        doc = [{"id": "a"}, cloudflare_mcp_row()]
        doc2, changed = upsert_row(doc, cloudflare_mcp_row())
        self.assertFalse(changed)
        self.assertEqual(len(doc2), 2)
        row = cloudflare_mcp_row()
        row["config"]["url"] = "https://example.invalid/mcp"
        doc3, changed3 = upsert_row(doc2, row)
        self.assertTrue(changed3)
        self.assertEqual([r for r in doc3 if r["id"] == "mcp-cloudflare"][0]["config"]["url"],
                         "https://example.invalid/mcp")


class AuthBridgeTests(unittest.TestCase):
    def test_marker_block_converges(self):
        new_text, changed = marker_block_text("", AUTH_MARKER_BEGIN, AUTH_MARKER_END, AUTH_BLOCK)
        self.assertTrue(changed)
        self.assertIn("CLOUDFLARE_API_TOKEN", new_text)
        self.assertNotIn("cfa-", new_text)  # references only, never values
        again, changed2 = marker_block_text(new_text, AUTH_MARKER_BEGIN, AUTH_MARKER_END, AUTH_BLOCK)
        self.assertFalse(changed2)
        self.assertEqual(again, new_text)


class StageSmokeTests(unittest.TestCase):
    def test_stage_registered(self):
        from prime_stack.stages import GROUPS, STAGES
        self.assertIn("cloudflare", STAGES)
        all_stages = GROUPS["all"]
        self.assertLess(all_stages.index("secrets"), all_stages.index("cloudflare"))
        self.assertLess(all_stages.index("cloudflare"), all_stages.index("fleet-presets"))


if __name__ == "__main__":
    unittest.main()
