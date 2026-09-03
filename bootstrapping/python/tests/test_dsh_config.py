import unittest

import yaml

from prime_stack.config import (DSH_MODEL_OVERRIDES, MODEL_CATALOG,
                                enabled_model_ids, prime_provider)


class DshSettingsRenderTests(unittest.TestCase):
    ACCOUNT = "deadbeefcafe0123456789ab"

    def render(self):
        from prime_stack.stages.dsh_config import render_dsh_settings
        return render_dsh_settings(self.ACCOUNT)

    def test_parses_as_yaml(self):
        doc = yaml.safe_load(self.render())
        self.assertEqual(doc["agent-default-model"]["provider"], "cloudflare-workers-ai")
        self.assertEqual(doc["agent-default-model"]["model"], "@cf/zai-org/glm-5.3")
        self.assertEqual(doc["agent-presets"]["default"], "prime-orchestrator")
        self.assertEqual(doc["permission"]["defaultPreset"], "danger-full-access")

    def test_account_id_templated_no_placeholders_left(self):
        rendered = self.render()
        self.assertIn(self.ACCOUNT, rendered)
        self.assertNotIn("{cf_account_id}", rendered)
        self.assertNotIn("$", rendered)

    def test_provider_block(self):
        provider = yaml.safe_load(self.render())["llm-pi-ai"]["providers"]["cloudflare-workers-ai"]
        self.assertEqual(provider["apiKeyEnv"], "CLOUDFLARE_AI_TOKEN")
        self.assertEqual(provider["api"], "openai-completions")
        self.assertEqual(
            provider["baseURL"],
            f"https://api.cloudflare.com/client/v4/accounts/{self.ACCOUNT}/ai/v1",
        )

    def test_catalog_matches_config_source_of_truth(self):
        models = yaml.safe_load(self.render())["llm-pi-ai"]["providers"]["cloudflare-workers-ai"]["models"]
        by_id = {m["id"]: m for m in models}
        self.assertEqual(len(models), len(MODEL_CATALOG))
        for m in models:
            source = next(s for s in MODEL_CATALOG if s["id"] == m["id"])
            expected = dict(source)
            expected.update(DSH_MODEL_OVERRIDES.get(m["id"], {}))
            expected.pop("reasoning", None)
            self.assertEqual(m, expected, f"catalog drift for {m['id']}")
        self.assertEqual(by_id["@cf/zai-org/glm-5.3"]["maxTokens"], 256000)
        self.assertEqual(by_id["@cf/zai-org/glm-5.3"]["name"], "glm53")
        self.assertEqual(by_id["@cf/zai-org/glm-5.3-flash"]["maxTokens"], 128000)

    def test_no_account_id_in_catalog_surfaces(self):
        self.assertNotIn(self.ACCOUNT, repr(MODEL_CATALOG))


class PrimeProviderTests(unittest.TestCase):
    def test_provider_fragment(self):
        provider = prime_provider("acct123")
        self.assertEqual(provider["api"], "openai-completions")
        self.assertIn("accounts/acct123/ai/v1", provider["baseUrl"])
        self.assertEqual(len(provider["models"]), len(MODEL_CATALOG))
        glm53 = next(m for m in provider["models"] if m["id"] == "@cf/zai-org/glm-5.3")
        self.assertTrue(glm53.get("reasoning"))
        deepseek = next(m for m in provider["models"] if m["id"].startswith("@cf/deepseek"))
        self.assertNotIn("reasoning", deepseek)

    def test_enabled_model_ids_are_prefixed(self):
        ids = enabled_model_ids()
        self.assertEqual(len(ids), len(MODEL_CATALOG))
        self.assertIn("cloudflare-workers-ai/@cf/zai-org/glm-5.3", ids)
        self.assertTrue(all(i.startswith("cloudflare-workers-ai/") for i in ids))


if __name__ == "__main__":
    unittest.main()
