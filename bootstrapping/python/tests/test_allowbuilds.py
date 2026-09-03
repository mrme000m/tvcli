import unittest

from prime_stack.stages.plugin import merge_allowbuilds_lines, parse_allowbuilds_keys

PNPM11_LOG = """\
ERR_PNPM_GIT_DEP_PREPARE_NOT_ALLOWED  A git-hosted dependency has a prepare
script that was not allowed to run. Add the following to pnpm-workspace.yaml
to allow the build scripts of this dependency:

allowBuilds:
  "dsh-prime-orchestrator@https://codeload.github.com/mrme000m/dsh-prime-orchestrator/tar.gz/9f2c1ab2": true
  other-package@2.0.0: true

pnpm: command finished with an error (rc=1)
"""

PNPM10_LOG = """\
Packages: +42
+
+lib/index.js
Done in 12s
"""


class ParseAllowbuildsTests(unittest.TestCase):
    def test_extracts_keys_after_allowbuilds_header(self):
        keys = parse_allowbuilds_keys(PNPM11_LOG)
        self.assertEqual(keys, [
            "dsh-prime-orchestrator@https://codeload.github.com/mrme000m/"
            "dsh-prime-orchestrator/tar.gz/9f2c1ab2",
            "other-package@2.0.0",
        ])

    def test_no_allowbuilds_section(self):
        self.assertEqual(parse_allowbuilds_keys(PNPM10_LOG), [])

    def test_empty_log(self):
        self.assertEqual(parse_allowbuilds_keys(""), [])

    def test_only_takes_from_first_allow_builds_to_eof(self):
        keys = parse_allowbuilds_keys("junk\nallowBuilds:\n  a: true\nnot-a-key\n")
        self.assertEqual(keys, ["a"])


class MergeAllowbuildsTests(unittest.TestCase):
    def test_creates_workspace_base_when_file_absent(self):
        text, changed = merge_allowbuilds_lines(None, ["node-pty"])
        self.assertTrue(changed)
        self.assertEqual(text, "packages:\n  - .\nnodeLinker: hoisted\nallowBuilds:\n  \"node-pty\": true\n")

    def test_no_keys_is_noop(self):
        self.assertEqual(merge_allowbuilds_lines("packages: []\n", []), ("packages: []\n", False))

    def test_inserts_after_existing_allowbuilds_header(self):
        base = "packages:\n  - .\nnodeLinker: hoisted\nallowBuilds:\n  \"ssh2\": true\n"
        text, changed = merge_allowbuilds_lines(base, ["node-pty"])
        self.assertTrue(changed)
        lines = text.splitlines()
        self.assertEqual(lines[lines.index("allowBuilds:") + 1], '  "node-pty": true')
        self.assertIn('  "ssh2": true', lines)

    def test_recognizes_key_in_any_quoting_style(self):
        base = "allowBuilds:\n  'node-pty': true\n"
        text, changed = merge_allowbuilds_lines(base, ["node-pty"])
        self.assertFalse(changed)
        self.assertEqual(text, base)

    def test_idempotent_second_call(self):
        text, _ = merge_allowbuilds_lines(None, ["node-pty", "ssh2"])
        self.assertEqual(merge_allowbuilds_lines(text, ["node-pty", "ssh2"]), (text, False))

    def test_duplicate_keys_deduped(self):
        text, _ = merge_allowbuilds_lines(None, ["node-pty", "node-pty"])
        self.assertEqual(text.count("node-pty"), 1)


if __name__ == "__main__":
    unittest.main()
