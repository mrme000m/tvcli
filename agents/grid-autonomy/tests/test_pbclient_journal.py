#!/usr/bin/env python3
"""pbclient.journal extra-packing — schema keys go to their columns, every
other key (e.g. the pnl-snapshot fleet/bots payload) is packed into the
free `extra` JSON column so structured data survives the PB write-through.
Verified live 2026-09-06: without this, /api/pnl points had fleet:null
because PocketBase silently drops unknown fields on create."""

import json
import sys
import unittest
from unittest import mock

sys.path.insert(0, ".")

from pbclient import PB  # noqa: E402


class JournalExtraPacking(unittest.TestCase):
    def _captured(self, event):
        rec = PB.__new__(PB)          # no __init__ (no network/creds)
        captured = {}
        rec.create = lambda coll, data: captured.update(coll=coll, data=data) or {"id": "x"}
        return rec, captured

    def test_plain_event_passes_through_unchanged(self):
        rec, captured = self._captured({})
        rec.journal({"kind": "screen", "msg": "hi", "at": "t0", "slot": "2"})
        self.assertEqual(captured["coll"], "journal")
        self.assertEqual(captured["data"],
                         {"kind": "screen", "msg": "hi", "at": "t0", "slot": "2"})
        self.assertNotIn("extra", captured["data"])

    def test_payload_keys_packed_into_extra(self):
        rec, captured = self._captured({})
        rec.journal({"kind": "pnl-snapshot", "msg": "fleet net $+0.30",
                     "at": "t1", "fleet": {"net": 0.3},
                     "bots": {"2": {"symbol": "CHIP"}}})
        data = captured["data"]
        self.assertEqual(data["kind"], "pnl-snapshot")
        self.assertNotIn("fleet", data)
        self.assertNotIn("bots", data)
        extra = json.loads(data["extra"])
        self.assertEqual(extra["fleet"], {"net": 0.3})
        self.assertEqual(extra["bots"], {"2": {"symbol": "CHIP"}})

    def test_round_trip_via_console_parser_shape(self):
        """The packed shape is exactly what console._pnl_points reads:
        extra (string) -> json.loads -> {fleet, bots}."""
        rec, captured = self._captured({})
        rec.journal({"kind": "pnl-snapshot", "msg": "m", "at": "t2",
                     "fleet": {"realized": 1.4582, "net": 0.2982},
                     "bots": {}})
        row = dict(captured["data"])
        # simulate the console path
        extra = row.get("extra")
        if isinstance(extra, str):
            extra = json.loads(extra)
        fleet = extra.get("fleet") if isinstance(extra, dict) else None
        self.assertEqual(fleet, {"realized": 1.4582, "net": 0.2982})


if __name__ == "__main__":
    unittest.main()
