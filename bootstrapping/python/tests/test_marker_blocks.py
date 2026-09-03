import unittest

from prime_stack.core import marker_block_text, version_lt
from prime_stack.stages.env_bridge import BLOCK, MARKER_BEGIN, MARKER_END

LEGACY_ANSIBLE = """\
earlier content
# # >>> dsh-prime-stack bootstrap >>> ANSIBLE MANAGED BLOCK
export PATH="$HOME/.local/bin:$PATH"
export CLOUDFLARE_AI_TOKEN="${CLOUDFLARE_AI_TOKEN:-${CLOUDFLARE_API_KEY:-}}"
export CF_ACCOUNT_ID="${CF_ACCOUNT_ID:-${CLOUDFLARE_ACCOUNT_ID:-}}"
# # <<< dsh-prime-stack bootstrap <<< ANSIBLE MANAGED BLOCK
later content
"""

CANONICAL = f"{MARKER_BEGIN}\n{BLOCK}\n{MARKER_END}\n"


class MarkerBlockTests(unittest.TestCase):
    def test_fresh_file(self):
        text, changed = marker_block_text("", MARKER_BEGIN, MARKER_END, BLOCK)
        self.assertTrue(changed)
        self.assertEqual(text, CANONICAL)

    def test_identical_block_unchanged(self):
        existing = "before\n\n" + CANONICAL + "after\n"
        text, changed = marker_block_text(existing, MARKER_BEGIN, MARKER_END, BLOCK)
        self.assertFalse(changed)
        self.assertEqual(text, existing)

    def test_legacy_ansible_markers_converge(self):
        text, changed = marker_block_text(LEGACY_ANSIBLE, MARKER_BEGIN, MARKER_END, BLOCK)
        self.assertTrue(changed)
        self.assertEqual(
            text, "earlier content\n" + CANONICAL + "later content\n"
        )

    def test_stale_content_replaced(self):
        existing = f"{MARKER_BEGIN}\nexport OLD=1\n{MARKER_END}\n"
        text, changed = marker_block_text(existing, MARKER_BEGIN, MARKER_END, BLOCK)
        self.assertTrue(changed)
        self.assertIn("CLOUDFLARE_AI_TOKEN", text)
        self.assertNotIn("export OLD=1", text)
        self.assertEqual(text, CANONICAL)

    def test_append_to_file_without_markers(self):
        existing = "export A=1\n"
        text, changed = marker_block_text(existing, MARKER_BEGIN, MARKER_END, BLOCK)
        self.assertTrue(changed)
        self.assertTrue(text.startswith("export A=1\n\n"))
        self.assertIn(CANONICAL, text)
        self.assertEqual(text.count(MARKER_BEGIN), 1)

    def test_double_run_is_idempotent(self):
        text, _ = marker_block_text(LEGACY_ANSIBLE, MARKER_BEGIN, MARKER_END, BLOCK)
        self.assertEqual(marker_block_text(text, MARKER_BEGIN, MARKER_END, BLOCK), (text, False))


class VersionCompareTests(unittest.TestCase):
    def test_numeric_compare(self):
        self.assertTrue(version_lt("9.9", "10.0.0"))
        self.assertFalse(version_lt("10.0.0", "10.0.0"))
        self.assertFalse(version_lt("11.0.0", "10.0.0"))
        self.assertTrue(version_lt("0.0.0", "10.0.0"))

    def test_prefix_v(self):
        self.assertTrue(version_lt("v9.1.0", "10.0.0"))

    def test_missing(self):
        self.assertTrue(version_lt("", "10.0.0"))


if __name__ == "__main__":
    unittest.main()
