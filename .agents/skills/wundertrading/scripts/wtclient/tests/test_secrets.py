import tempfile
import unittest
from pathlib import Path

from wtclient.secrets import Secrets, load_secrets, parse_env_file


class TestParseEnvFile(unittest.TestCase):
    def test_parse_and_quote_strip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "env"
            p.write_text('# comment\nWT_API_KEY="abc"\nWT_API_SECRET = \'def\'\nEMPTY=\n')
            out = parse_env_file(p)
            self.assertEqual(out["WT_API_KEY"], "abc")
            self.assertEqual(out["WT_API_SECRET"], "def")
            self.assertNotIn("EMPTY", out)


class TestLoadSecrets(unittest.TestCase):
    def test_env_wins_and_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1 = Path(tmp) / "a.env"
            p1.write_text("WUN_API_KEY=a\nWT_PHPSESSID=s1\n")
            p2 = Path(tmp) / "b.env"
            p2.write_text("WUN_API_KEY=b\nWT_CF_CLEARANCE=c1\n")
            secrets = load_secrets(paths=[p1, p2], environ={"WUN_API_KEY": "env"})
            self.assertEqual(secrets.api_key, "env")
            self.assertEqual(secrets.phpsessid, "s1")
            self.assertEqual(secrets.cf_clearance, "c1")

    def test_require_api_keys_raises(self):
        from wtclient.errors import WunConfigError
        secrets = Secrets(values={})
        with self.assertRaises(WunConfigError):
            secrets.require_api_keys()

    def test_cookies_merge_json_and_named(self):
        secrets = Secrets(values={
            "WT_COOKIES_JSON": '[{"name":"PHPSESSID","value":"json-sess"},{"name":"extra","value":"e"}]',
            "WT_CF_CLEARANCE": "clear",
        })
        cookies = secrets.cookies
        self.assertEqual(cookies["PHPSESSID"], "json-sess")
        self.assertEqual(cookies["cf_clearance"], "clear")
        self.assertEqual(cookies["extra"], "e")


if __name__ == "__main__":
    unittest.main()
