import unittest

from wtclient.curl import curl_command, redact


class TestCurl(unittest.TestCase):
    def test_redact(self):
        self.assertEqual(redact("abcdefgh"), "abcdef…REDACTED")
        self.assertEqual(redact("abc"), "abc")

    def test_sensitive_headers_redacted(self):
        cmd = curl_command(
            "GET",
            "https://wundertrading.com/open_api/exchanges",
            headers={"X-API-Key": "super-secret-key", "Accept": "application/json"},
        )
        self.assertNotIn("super-secret-key", cmd)
        self.assertIn("X-API-Key", cmd)
        self.assertIn("REDACTED", cmd)

    def test_cookie_redaction(self):
        cmd = curl_command(
            "GET",
            "https://wundertrading.com/en/trader/grid_bots/grid",
            cookies={"PHPSESSID": "session-cookie-value", "foo": "bar"},
        )
        self.assertNotIn("session-cookie-value", cmd)
        self.assertIn("REDACTED", cmd)


if __name__ == "__main__":
    unittest.main()
