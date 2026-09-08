"""Unit tests for llm/provider.py — chain/fallback logic with stubbed transports."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "llm"))

import provider


class TestChain(unittest.TestCase):
    def test_chat_first_healthy_wins(self):
        calls = []

        def ok(msgs, mt):
            calls.append("ok")
            return "hello"

        def bad(msgs, mt):
            calls.append("bad")
            raise RuntimeError("down")

        name, text = provider.chat("hi", _chain=[("bad", bad), ("ok", ok)])
        self.assertEqual((name, text), ("ok", "hello"))
        self.assertEqual(calls, ["bad", "ok"])

    def test_chat_all_fail(self):
        def bad(msgs, mt):
            raise RuntimeError("down")

        with self.assertRaises(RuntimeError):
            provider.chat("hi", _chain=[("a", bad), ("b", bad)])

    def test_chat_empty_chain(self):
        with self.assertRaises(RuntimeError):
            provider.chat("hi", _chain=[])

    def test_chat_str_wrapped(self):
        seen = {}

        def ok(msgs, mt):
            seen["msgs"] = msgs
            return "x"

        provider.chat("hi", _chain=[("ok", ok)])
        self.assertEqual(seen["msgs"], [{"role": "user", "content": "hi"}])

    def test_chat_json_strips_fences(self):
        def ok(msgs, mt):
            return '```json\n{"a": 1}\n```'

        name, obj = provider.chat_json("hi", _chain=[("ok", ok)])
        self.assertEqual(obj, {"a": 1})

    def test_providers_respects_chain_order(self):
        os.environ["MISTRAL_API_KEY"] = "k_test"
        os.environ["CLOUDFLARE_ACCOUNT_ID"] = "acct_test"
        os.environ["CLOUDFLARE_API_KEY"] = "tok_test"
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ["GRID_LLM_CHAIN"] = "openrouter,cf"
        try:
            names = [n for n, _ in provider._providers()]
            # openrouter has no key in this test → stripped; cf is creded
            self.assertEqual(names, ["cf"])
        finally:
            for k in ("MISTRAL_API_KEY", "CLOUDFLARE_ACCOUNT_ID",
                      "CLOUDFLARE_API_KEY", "GRID_LLM_CHAIN"):
                os.environ.pop(k, None)

    def test_providers_strips_cred_less_cf(self):
        """A CF with no creds must be removed from the chain at boot,
        not silently fast-fail on every call (the gap-report hit
        `cf: missing CLOUDFLARE_ACCOUNT_ID/API_KEY` once per LLM call)."""
        # save and clear CF creds; mistral stays creded so the chain
        # has at least one healthy provider to test against
        for k in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_KEY",
                  "CLOUDFLARE_AI_TOKEN"):
            os.environ.pop(k, None)
        os.environ["MISTRAL_API_KEY"] = "k_test"
        os.environ["GRID_LLM_CHAIN"] = "cf,mistral,openrouter"
        try:
            names = [n for n, _ in provider._providers()]
            self.assertNotIn("cf", names)
            self.assertIn("mistral", names)
        finally:
            os.environ.pop("MISTRAL_API_KEY", None)
            os.environ.pop("GRID_LLM_CHAIN", None)

    def test_providers_keeps_creded_cf(self):
        os.environ["CLOUDFLARE_ACCOUNT_ID"] = "acct_test"
        os.environ["CLOUDFLARE_API_KEY"] = "tok_test"
        os.environ.pop("CLOUDFLARE_AI_TOKEN", None)
        os.environ.pop("MISTRAL_API_KEY", None)
        os.environ["GRID_LLM_CHAIN"] = "cf,mistral"
        try:
            names = [n for n, _ in provider._providers()]
            self.assertIn("cf", names)
            self.assertNotIn("mistral", names)  # no MISTRAL key
        finally:
            for k in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_KEY",
                      "GRID_LLM_CHAIN"):
                os.environ.pop(k, None)

    def test_providers_strips_nvidia_when_410(self):
        """The NV free model is retired; with no key the chain must skip
        NV rather than burn an HTTP attempt returning 410."""
        os.environ.pop("NVIDIA_API_KEY", None)
        os.environ["GRID_LLM_CHAIN"] = "nvidia,mistral"
        os.environ["MISTRAL_API_KEY"] = "k"
        try:
            names = [n for n, _ in provider._providers()]
            self.assertNotIn("nvidia", names)
            self.assertEqual(names, ["mistral"])
        finally:
            os.environ.pop("GRID_LLM_CHAIN", None)
            os.environ.pop("MISTRAL_API_KEY", None)


if __name__ == "__main__":
    unittest.main()
