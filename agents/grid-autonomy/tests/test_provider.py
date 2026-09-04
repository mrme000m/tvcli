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
        os.environ["GRID_LLM_CHAIN"] = "openrouter,cf"
        names = [n for n, _ in provider._providers()]
        self.assertEqual(names, ["openrouter", "cf"])
        del os.environ["GRID_LLM_CHAIN"]


if __name__ == "__main__":
    unittest.main()
