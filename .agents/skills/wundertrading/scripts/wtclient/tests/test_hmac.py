import unittest

from wtclient.query import append_query, encode_query, serialize_body
from wtclient.transport.hmac import sign_payload


class TestSignPayload(unittest.TestCase):
    def test_known_vector(self):
        sig = sign_payload("secret", "GET", "/open_api/exchanges", "1700000000000", "60000", "")
        self.assertEqual(sig, "8+LxDqnAPCdVzK7K+uFn6/+/FDH+79KMLGud8T6NJG8=")

    def test_method_is_uppercased(self):
        self.assertEqual(
            sign_payload("secret", "get", "/x", "1", "2", "{}"),
            sign_payload("secret", "GET", "/x", "1", "2", "{}"),
        )


class TestQueryEncoding(unittest.TestCase):
    def test_list_becomes_comma_joined(self):
        self.assertIn("exchanges=HYPERLIQUID_SWAP%2CBINANCE", encode_query({"exchanges": ["HYPERLIQUID_SWAP", "BINANCE"]}))

    def test_append_preserves_existing_query(self):
        path = append_query("/open_api/markets?x=1", {"exchanges": ["A", "B"]})
        self.assertTrue(path.startswith("/open_api/markets?x=1&"))

    def test_serialize_body(self):
        self.assertEqual(serialize_body({"a": 1}), '{"a":1}')
        self.assertEqual(serialize_body(None), "")
        self.assertEqual(serialize_body("raw"), "raw")


if __name__ == "__main__":
    unittest.main()
