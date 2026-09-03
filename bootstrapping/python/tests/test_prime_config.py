import json
import os
import tempfile
import unittest
from pathlib import Path

from prime_stack.core import Context
from prime_stack.stages.prime_config import (merge_auth_entry,
                                             merge_provider_into_models,
                                             merge_settings_defaults)


class PrimeConfigMergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "agent" / "models.json"

    def test_creates_missing_file_and_is_idempotent(self):
        ctx = Context()
        ctx.dry_run = False
        from prime_stack.core import json_text, load_json_file

        def do():
            doc = load_json_file(self.path)
            before = json_text(doc)
            merge_provider_into_models(doc, {"baseUrl": "x", "models": []})
            after = json_text(doc)
            if after == before:
                return ctx.write_text(self.path, after, mode=0o600) if self.path.exists() else False
            return ctx.write_text(self.path, after, mode=0o600)

        self.assertTrue(do())
        first = self.path.read_text()
        self.assertFalse(do())
        self.assertEqual(self.path.read_text(), first)
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o600")

    def test_other_providers_preserved(self):
        existing = {"providers": {"openai": {"type": "api_key", "key": "sk-keep"}}}
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(existing))
        doc = json.loads(self.path.read_text())
        merge_provider_into_models(doc, {"baseUrl": "x", "models": []})
        self.assertEqual(doc["providers"]["openai"], {"type": "api_key", "key": "sk-keep"})
        self.assertEqual(doc["providers"]["cloudflare-workers-ai"], {"baseUrl": "x", "models": []})

    def test_auth_entry(self):
        doc = {"anthropic": {"type": "api_key", "key": "keep"}}
        merge_auth_entry(doc, "cf-key")
        self.assertEqual(doc["cloudflare-workers-ai"], {"type": "api_key", "key": "cf-key"})
        self.assertEqual(doc["anthropic"]["key"], "keep")

    def test_settings_defaults_and_union(self):
        doc = {"defaultProvider": "openai", "enabledModels": ["openai/gpt-x", "cloudflare-workers-ai/@cf/zai-org/glm-5.2"]}
        merge_settings_defaults(doc, ["cloudflare-workers-ai/@cf/zai-org/glm-5.2",
                                       "cloudflare-workers-ai/@cf/zai-org/glm-5.3"])
        self.assertEqual(doc["defaultProvider"], "cloudflare-workers-ai")
        self.assertEqual(doc["defaultModel"], "@cf/zai-org/glm-5.3")
        self.assertEqual(doc["defaultThinkingLevel"], "high")
        self.assertEqual(doc["enabledModels"], [
            "openai/gpt-x", "cloudflare-workers-ai/@cf/zai-org/glm-5.2",
            "cloudflare-workers-ai/@cf/zai-org/glm-5.3",
        ])

    def test_merge_is_stable(self):
        doc = {"enabledModels": ["b", "a"]}
        snapshot = json.dumps(doc, sort_keys=True)
        merge_settings_defaults(doc, ["a"])
        doc2 = json.loads(json.dumps(doc))
        merge_settings_defaults(doc2, ["a"])
        self.assertEqual(json.dumps(doc, sort_keys=True), json.dumps(doc2, sort_keys=True))
        self.assertNotEqual(snapshot, json.dumps(doc, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
