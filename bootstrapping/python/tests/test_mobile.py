"""tests for the extras-mobile gateway config (the loopback codespace shape).

Encodes the empirically probed contract (2026-09-04): no publicOrigin (its
port IS the listen port, and the codespace subdomain port differs from the
public 443), tls disabled on loopback, loopback authorities matching the
Host the codespace forwarder sends, and instanceId derived from the pairing
CA's fingerprint256 so a drifted stored value self-heals.
"""

import base64
import hashlib
import tempfile
import unittest
from pathlib import Path

from prime_stack.stages.extras import build_mobile_setup_doc


def _fake_ca_pem() -> str:
    """A PEM-shaped block whose DER payload is arbitrary bytes — the
    fingerprint is sha256 of whatever b64 body we ship, cert validity is
    irrelevant for this test."""
    body = base64.b64encode(b"pretend-this-is-a-der-encoded-cert").decode()
    return f"-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----"


class MobileSetupDocTests(unittest.TestCase):
    def _home_with_ca(self, tmp: str) -> Path:
        home = Path(tmp) / "mobile-access"
        (home / "tls").mkdir(parents=True, exist_ok=True)
        (home / "tls" / "ca.pem").write_text(_fake_ca_pem())
        return home

    def test_loopback_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home_with_ca(tmp)
            doc = build_mobile_setup_doc({}, 3443, home)
            self.assertEqual(doc["listenHost"], "127.0.0.1")
            self.assertEqual(doc["listenPort"], 3443)
            self.assertEqual(doc["upstreamOrigin"], "http://127.0.0.1:3081")
            self.assertEqual(doc["publicAuthorities"], ["localhost:3443", "127.0.0.1:3443"])
            self.assertEqual(doc["tls"], {"mode": "disabled"})
            self.assertEqual(doc["allowedCidrs"], ["127.0.0.0/8", "::1/128"])
            # the shape the codespace edge can actually front: NO publicOrigin
            self.assertNotIn("publicOrigin", doc)
            self.assertNotIn("certFile", doc["tls"])

    def test_instance_id_is_the_ca_fingerprint_not_a_stored_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._home_with_ca(tmp)
            expected = hashlib.sha256(base64.b64decode(
                "".join(l for l in _fake_ca_pem().splitlines() if not l.startswith("---"))
            )).hexdigest()
            doc = build_mobile_setup_doc({"instanceId": "drifted-token"}, 3443, home)
            self.assertEqual(doc["instanceId"], expected)
            self.assertNotEqual(doc["instanceId"], "drifted-token")

    def test_instance_id_preserved_when_no_ca_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "mobile-access"  # no tls/ dir at all
            doc = build_mobile_setup_doc({"instanceId": "cli-written"}, 3443, home)
            self.assertEqual(doc["instanceId"], "cli-written")


if __name__ == "__main__":
    unittest.main()
