"""Tests for :mod:`wtclient.discovery` — recorder, catalog, probe."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from wtclient.discovery import (
    PUBLIC_SURFACES,
    EndpointCatalog,
    Probe,
    ProbeResult,
    Recorder,
    RecordedRequest,
    RecordedTransport,
    surface_index,
)
from wtclient.response import Response


def _resp(status=200, text="{}", headers=None):
    return Response(
        status_code=status,
        headers=headers or {"content-type": "application/json"},
        text=text,
        url="https://example/test",
        method="GET",
    )


def _row(**kw):
    base = dict(
        method="GET",
        url="https://wundertrading.com/open_api/api_profiles/abc123",
        status=200,
        surface="hmac",
        started_at=1700000000.0,
        duration_ms=12.5,
        request_headers={"X-API-Key": "redact_me", "User-Agent": "test"},
        request_body=None,
        response_headers={"content-type": "application/json"},
        response_body_preview='{"code":"abc","status":"active"}',
        response_content_type="application/json",
        ok=True,
    )
    base.update(kw)
    return RecordedRequest(**base)


class TestRecorder(unittest.TestCase):
    def test_empty_recorder_has_no_rows(self):
        rec = Recorder()
        self.assertEqual(rec.rows, [])
        self.assertEqual(rec.catalog().all(), [])

    def test_record_then_query(self):
        rec = Recorder()
        rec(_row(method="GET", url="/open_api/api_profiles/abcdef0123456789abcdef01", surface="hmac"))
        rec(_row(method="GET", url="/open_api/api_profiles/0123456789abcdef01234567", surface="hmac"))
        catalog = rec.catalog()
        # both IDs collapse to <id> so they aggregate into one endpoint
        self.assertEqual(len(catalog), 1)
        s = catalog.all()[0]
        self.assertEqual(s.calls, 2)
        self.assertEqual(s.surface, "hmac")
        self.assertEqual(s.method, "GET")
        self.assertEqual(s.path, "/open_api/api_profiles/<id>")
        self.assertIn("code", s.response_top_level_keys)
        self.assertEqual(s.statuses[200], 2)
        self.assertEqual(s.success_rate, 1.0)

    def test_redacts_api_keys_in_headers(self):
        rec = Recorder()
        rec(
            _row(
                request_headers={
                    "X-API-Key": "1234567890abcdef",
                    "X-Secret-Key": "supersecret",
                    "User-Agent": "wtclient",
                },
                surface="hmac",
            )
        )
        row = rec.rows[0]
        self.assertEqual(row.request_headers["X-API-Key"], "<redacted>")
        self.assertEqual(row.request_headers["X-Secret-Key"], "<redacted>")
        self.assertEqual(row.request_headers["User-Agent"], "wtclient")

    def test_redacts_nested_body_keys(self):
        rec = Recorder()
        rec(
            _row(
                request_body={
                    "exchangeCode": "HYPERLIQUID_SWAP",
                    "auth": {"api_key": "1234567890", "secret_key": "deadbeef"},
                    "clientId": "abc",
                },
            )
        )
        body = rec.rows[0].request_body
        self.assertEqual(body["exchangeCode"], "HYPERLIQUID_SWAP")
        self.assertEqual(body["clientId"], "abc")
        self.assertEqual(body["auth"]["api_key"], "<redacted>")
        self.assertEqual(body["auth"]["secret_key"], "<redacted>")

    def test_to_jsonl_roundtrip(self):
        rec = Recorder()
        rec(_row(method="GET", url="/a", surface="hmac", status=200))
        rec(_row(method="POST", url="/b", surface="session", status=403))
        with tempfile.TemporaryDirectory() as d:
            path = rec.to_jsonl(Path(d) / "rows.jsonl")
            self.assertTrue(path.exists())
            rows = Recorder.from_jsonl(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].url, "/a")
        self.assertEqual(rows[1].status, 403)
        self.assertEqual(rows[1].surface, "session")

    def test_ring_buffer_caps_rows(self):
        rec = Recorder(max_rows=3)
        for i in range(5):
            rec(_row(url=f"/a/{i}"))
        self.assertEqual(len(rec.rows), 3)
        # FIFO: oldest dropped
        urls = [r.url for r in rec.rows]
        self.assertEqual(urls, ["/a/2", "/a/3", "/a/4"])

    def test_sink_called_on_record(self):
        seen = []

        def sink(row):
            seen.append(row)

        rec = Recorder(sink=sink)
        rec(_row())
        rec(_row())
        self.assertEqual(len(seen), 2)


class TestEndpointCatalog(unittest.TestCase):
    def test_aggregates_by_method_path_surface(self):
        cat = EndpointCatalog(
            [
                _row(method="GET", url="/open_api/api_profiles/abc", surface="hmac"),
                _row(method="GET", url="/open_api/api_profiles/def", surface="hmac"),
                _row(method="POST", url="/open_api/strategies/trade", surface="hmac"),
                _row(method="GET", url="/en/trader/grid_bots/grid", surface="session"),
            ]
        )
        # 4 unique (method, path, surface) tuples
        self.assertEqual(len(cat), 4)
        by_hmac = cat.by_surface("hmac")
        self.assertEqual(len(by_hmac), 3)
        by_session = cat.by_surface("session")
        self.assertEqual(len(by_session), 1)

    def test_success_rate(self):
        cat = EndpointCatalog(
            [
                _row(status=200),
                _row(status=200),
                _row(status=403),
            ]
        )
        self.assertAlmostEqual(cat.all()[0].success_rate, 2 / 3, places=3)

    def test_request_body_keys_collected(self):
        cat = EndpointCatalog(
            [
                _row(request_body={"exchangeCode": "X", "pairCode": "1"}),
                _row(request_body={"exchangeCode": "Y", "pairCode": "2"}),
            ]
        )
        self.assertEqual(cat.all()[0].request_body_keys, {"exchangeCode", "pairCode"})

    def test_response_top_level_keys_from_json(self):
        cat = EndpointCatalog(
            [
                _row(response_body_preview='{"code":"a","status":"active","balance":1.0}'),
            ]
        )
        self.assertEqual(
            cat.all()[0].response_top_level_keys, {"balance", "code", "status"}
        )

    def test_to_dict_serializable(self):
        cat = EndpointCatalog([_row()])
        d = cat.to_dict()
        self.assertEqual(len(d), 1)
        # JSON-roundtrippable
        json.dumps(d)


class TestRecordedTransport(unittest.TestCase):
    def test_records_response(self):
        rec = Recorder()
        inner = MagicMock()
        inner.name = "stub"
        inner.request.return_value = _resp(status=200, text='{"ok":true}')
        wrapped = rec.wrap(inner)
        resp = wrapped.request("GET", "/x")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(rec.rows), 1)
        self.assertEqual(rec.rows[0].surface, "stub")

    def test_records_exceptions(self):
        from wtclient.errors import WunCloudflareError

        rec = Recorder()
        inner = MagicMock()
        inner.name = "stub"
        inner.request.side_effect = WunCloudflareError(
            "blocked", status_code=403, url="/x"
        )
        wrapped = rec.wrap(inner)
        with self.assertRaises(WunCloudflareError):
            wrapped.request("GET", "/x")
        self.assertEqual(len(rec.rows), 1)
        self.assertEqual(rec.rows[0].status, 403)


class TestProbe(unittest.TestCase):
    def test_first_working_returns_lowest_index(self):
        # build a fake wun whose transports are mocks
        from wtclient.errors import WunTransportError

        wun = MagicMock()
        wun.secrets = MagicMock()
        wun.grid.transport = MagicMock()
        wun.grid.transport.request.return_value = _resp(status=200)
        wun.secrets.require_session.return_value = {"PHPSESSID": "x"}
        p = Probe(wun)
        results = p.try_method("GET", "/open_api/exchanges")
        # first surface tried = browser (uses wun.grid.transport here)
        self.assertGreater(len(results), 0)
        # first_working returns the first ok
        first = p.first_working("GET", "/open_api/exchanges")
        self.assertIsNotNone(first)
        self.assertTrue(first.ok)


class TestSurfaceIndex(unittest.TestCase):
    def test_returns_known_surfaces(self):
        idx = surface_index()
        self.assertIn("hmac", idx)
        self.assertIn("mcp", idx)
        self.assertIn("session", idx)
        self.assertIn("market", idx)
        self.assertEqual(idx, PUBLIC_SURFACES)

    def test_each_surface_non_empty(self):
        for surface, eps in PUBLIC_SURFACES.items():
            self.assertGreater(len(eps), 0, f"{surface} has no endpoints")


if __name__ == "__main__":
    unittest.main()
