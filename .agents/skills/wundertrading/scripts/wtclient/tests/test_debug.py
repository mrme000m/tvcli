"""Tests for :mod:`wtclient.debug` — install/dump/log/trace."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from wtclient import debug as dbg
from wtclient.debug import (
    STATE,
    DebugState,
    default_dump_dir,
    dump_failure,
    install,
    is_enabled,
    log_request,
    trace,
    unwrap,
)
from wtclient.discovery import Recorder
from wtclient.response import Response


class TestIsEnabled(unittest.TestCase):
    def test_default_disabled(self):
        # ensure clean env (other tests may set WT_DEBUG)
        prev = os.environ.pop("WT_DEBUG", None)
        try:
            self.assertFalse(is_enabled())
        finally:
            if prev is not None:
                os.environ["WT_DEBUG"] = prev

    def test_env_var_enables(self):
        prev = os.environ.get("WT_DEBUG")
        os.environ["WT_DEBUG"] = "1"
        try:
            self.assertTrue(is_enabled())
        finally:
            if prev is None:
                os.environ.pop("WT_DEBUG", None)
            else:
                os.environ["WT_DEBUG"] = prev


class TestInstall(unittest.TestCase):
    def test_install_is_idempotent(self):
        s = install()
        s2 = install()
        self.assertIs(s, s2)
        self.assertTrue(s.enabled)

    def test_install_configures_handler(self):
        install()
        # one StreamHandler attached
        self.assertTrue(any(isinstance(h, logging.StreamHandler) for h in STATE.logger.handlers))


class TestDumpFailure(unittest.TestCase):
    def test_writes_json_to_dir(self):
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            dump = dump_failure(
                method="GET",
                url="https://wundertrading.com/en/trader/grid_bots/upsert",
                surface="session",
                request_headers={"X-W-CSRF-Token": "supersecret"},
                request_body={"foo": "bar"},
                response_text='{"error":"oops"}',
                response_status=403,
                response_headers={"Server": "Cloudflare"},
                error="WunCloudflareError",
                dump_dir=dpath,
            )
            self.assertTrue(dump.path.exists())
            data = json.loads(dump.path.read_text())
            self.assertEqual(data["method"], "GET")
            self.assertEqual(data["response"]["status"], 403)
            self.assertEqual(data["request"]["headers"]["X-W-CSRF-Token"], "supersecret")
            self.assertEqual(dump.summary["status"], 403)


class TestLogRequest(unittest.TestCase):
    def test_noop_when_disabled(self):
        prev = STATE.enabled
        STATE.enabled = False
        try:
            log_request(
                method="GET",
                url="https://x",
                surface="hmac",
                headers={},
                body=None,
                response_status=500,
            )
            # no dump written
            self.assertEqual(len(STATE.dumps), 0)
        finally:
            STATE.enabled = prev

    def test_writes_dump_on_failure_when_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            prev_enabled = STATE.enabled
            prev_dir = STATE.dump_dir
            STATE.enabled = True
            STATE.dump_dir = Path(d)
            STATE.dumps.clear()
            try:
                log_request(
                    method="GET",
                    url="https://wundertrading.com/x",
                    surface="hmac",
                    headers={"X-API-Key": "abcd1234"},
                    body=None,
                    response_status=403,
                    response_text='{"error":"blocked"}',
                    response_headers={"content-type": "application/json"},
                    error=None,
                )
                self.assertEqual(len(STATE.dumps), 1)
                self.assertTrue(STATE.dumps[0].path.exists())
            finally:
                STATE.enabled = prev_enabled
                STATE.dump_dir = prev_dir


class TestTrace(unittest.TestCase):
    def test_trace_wraps_all_transports(self):
        # Build a real-ish wun facade using simple attribute objects so the
        # trace wrapper sees the actual transports (MagicMock would auto-
        # generate `_wt_debug_recorder` and confuse the test).
        class _FakeClient:
            def __init__(self, transport):
                self.transport = transport

        class _FakeWun:
            _wt_debug_recorder = None  # declare so getattr sees it
            rest = _FakeClient(MagicMock(name="rest.transport"))
            mcp = _FakeClient(MagicMock(name="mcp.transport"))
            grid = _FakeClient(MagicMock(name="grid.transport"))
            market = _FakeClient(MagicMock(name="market.transport"))
            for c in (rest, mcp, grid, market):
                c.transport.name = "stub"

        wun = _FakeWun()
        rec = trace(wun)
        self.assertIsInstance(rec, Recorder)
        # every client's transport is now a RecordedTransport
        for c in (wun.rest, wun.mcp, wun.grid, wun.market):
            self.assertEqual(type(c.transport).__name__, "RecordedTransport")

    def test_trace_returns_same_recorder_on_second_call(self):
        class _FakeClient:
            def __init__(self, transport):
                self.transport = transport

        class _FakeWun:
            _wt_debug_recorder = None
            rest = _FakeClient(MagicMock(name="rest.transport"))
            mcp = _FakeClient(MagicMock(name="mcp.transport"))
            grid = _FakeClient(MagicMock(name="grid.transport"))
            market = _FakeClient(MagicMock(name="market.transport"))
            for c in (rest, mcp, grid, market):
                c.transport.name = "stub"

        wun = _FakeWun()
        rec1 = trace(wun)
        rec2 = trace(wun)
        self.assertIs(rec1, rec2)

    def test_unwrap_restores_original_transports(self):
        class _FakeClient:
            def __init__(self, transport):
                self.transport = transport

        class _FakeWun:
            _wt_debug_recorder = None
            rest = _FakeClient(MagicMock(name="rest.transport"))
            mcp = _FakeClient(MagicMock(name="mcp.transport"))
            grid = _FakeClient(MagicMock(name="grid.transport"))
            market = _FakeClient(MagicMock(name="market.transport"))
            for c in (rest, mcp, grid, market):
                c.transport.name = "stub"

        wun = _FakeWun()
        originals = [c.transport for c in (wun.rest, wun.mcp, wun.grid, wun.market)]
        trace(wun)
        unwrap(wun)
        for c, orig in zip((wun.rest, wun.mcp, wun.grid, wun.market), originals):
            self.assertIs(c.transport, orig)
        # _wt_debug_recorder cleaned up
        self.assertFalse(hasattr(wun, "_wt_debug_recorder") and wun._wt_debug_recorder is not None)


if __name__ == "__main__":
    unittest.main()
