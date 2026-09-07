"""Tests for :class:`wtclient.clients.grid.GridClient` (set_exits + list)."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from wtclient.clients.grid import GridClient

LIST_PATH = "/en/trader/grid_bots/grid?page=1&limit=50"
UPSERT_PATH = "/en/trader/grid_bots/upsert?gridMarket=derivative&code=c629f5ba3a643a8236477243"


def _response(payload, status=200, text=None):
    body = text if text is not None else json.dumps(payload)
    resp = MagicMock()
    resp.ok = 200 <= status < 300
    resp.status_code = status
    resp.text = body
    if text is None:
        resp.json.return_value = payload
        resp.json.side_effect = None
    else:
        resp.json.side_effect = ValueError("not json")
    return resp


class FakeTransport:
    """Hermetic transport: scripted (method, path) -> Response."""

    name = "fake"

    def __init__(self, script=None):
        self.script = dict(script or {})
        self.calls = []

    def request(self, method, path, *, body=None, headers=None):
        self.calls.append((method, path, body))
        handler = self.script.get((method, path))
        if handler is None:
            return _response({"error": "not scripted"}, status=404)
        return handler

    def close(self):
        pass


# Resource mirrors the live 2026-09-07 edit target (ground-truth XHR body).
RESOURCE = {
    "id": 279000,
    "code": "c629f5ba3a643a8236477243",
    "status": "active",
    "paperTrading": True,
    "gridType": "interval",
    "gridMethod": "classic",
    "gridMarket": "derivative",
    "gridTradingType": "long",
    "gridPercentStep": 0.0124,
    "gridTickStep": None,
    "gridLevels": 12,
    "initPrice": 2.295,
    "midPrice": 2.474094,
    "highPrice": 2.474094,
    "lowPrice": 2.149906,
    "closestHighLevelPrice": 2.3149002970609955,
    "closestLowLevelPrice": 2.2865471128615127,
    "amountPerTrade": 10,
    "amountPerTradeType": "base",
    "takeProfit": 5,
    "stopLoss": 3,
    "stopLossPnlCompareType": "total",
    "trailingStopActivation": 5,
    "trailingStopExecute": 2,
    "trailingStopPnlCompareType": "total",
    "profitCurrencyType": "base",
    "stopCondition": "stop_and_close_all",
    "strategyProfitCondition": "trailing_stop",
    "strategyStopLossFixedPercentRatio": 0.05,
    "stopOnOutOfGrid": False,
    "pumpProtection": True,
    "pumpProtectionOrderType": "limit",
    "exchange": {"id": 7, "code": "BINANCE_FUTURES", "types": []},
    "pair": {
        "code": "NEARUSDT",
        "refCurrency": "NEAR",
        "baseCurrency": "USDT",
        "viewSymbol": "NEAR-USDT",
        "type": "future",
        "unifiedCode": "NEAR-USDT",
    },
    "profiles": [
        {"profile": {"code": "c629f5ba3a643a82ccadd9aa", "status": "active", "paperTrading": True}}
    ],
    "startCondition": "immediate",
    "indicators": [],
    "signalSource": None,
    "signalCode": "3a6e8229bac6f53aba4864aa",
    "maxRequiredAmount": "100 USDT",
    "leverage": 1,
}


def _list_payload(resource=None):
    return {"_embedded": {"items": [{"resource": resource if resource is not None else RESOURCE}]}}


def _make_client(resource=None, upsert_response=None):
    transport = FakeTransport(
        {
            ("GET", LIST_PATH): _response(_list_payload(resource)),
            ("POST", UPSERT_PATH): _response(
                upsert_response
                if upsert_response is not None
                else {"result": {"data": {}, "status": "success"}}
            ),
        }
    )
    return GridClient(transport), transport


class TestSetExits(unittest.TestCase):
    def test_merge_preserves_geometry_and_signal_code(self):
        client, transport = _make_client()
        out = client.set_exits("c629f5ba3a643a8236477243", take_profit=7)
        self.assertEqual(out["result"]["status"], "success")
        method, path, body = transport.calls[-1]
        self.assertEqual(method, "POST")
        # Geometry + signal preserved from the resource.
        self.assertEqual(body["gridPercentStep"], 0.0124)
        self.assertEqual(body["gridLevels"], 12)
        self.assertEqual(body["highPrice"], 2.474094)
        self.assertEqual(body["lowPrice"], 2.149906)
        self.assertEqual(body["midPrice"], 2.474094)
        self.assertEqual(body["initPrice"], 2.295)
        self.assertEqual(body["closestLowLevelPrice"], 2.2865471128615127)
        self.assertEqual(body["closestHighLevelPrice"], 2.3149002970609955)
        self.assertEqual(body["signalCode"], "3a6e8229bac6f53aba4864aa")
        self.assertEqual(body["startCondition"], "immediate")
        self.assertEqual(body["exchangeCode"], "BINANCE_FUTURES")
        self.assertEqual(body["pairCode"], "NEARUSDT")
        self.assertEqual(body["profilesCodes"], ["c629f5ba3a643a82ccadd9aa"])
        self.assertEqual(body["amountPerTrade"], 10)
        self.assertEqual(body["maxRequiredAmount"], "100 USDT")
        # Only the provided exit field changed.
        self.assertEqual(body["takeProfit"], 7)
        self.assertEqual(body["stopLoss"], 3)

    def test_positions_stop_loss_pct_converts_to_ratio(self):
        client, transport = _make_client()
        client.set_exits("c629f5ba3a643a8236477243", positions_stop_loss_pct=5)
        _, _, body = transport.calls[-1]
        self.assertEqual(body["strategyStopLossFixedPercentRatio"], 0.05)
        # float percent accepted too
        client.set_exits("c629f5ba3a643a8236477243", positions_stop_loss_pct=2.5)
        _, _, body = transport.calls[-1]
        self.assertEqual(body["strategyStopLossFixedPercentRatio"], 0.025)

    def test_positions_trailing_stop_bool(self):
        client, transport = _make_client()
        client.set_exits("c629f5ba3a643a8236477243", positions_trailing_stop=True)
        _, _, body = transport.calls[-1]
        self.assertEqual(body["strategyProfitCondition"], "trailing_stop")
        client.set_exits("c629f5ba3a643a8236477243", positions_trailing_stop=False)
        _, _, body = transport.calls[-1]
        self.assertEqual(body["strategyProfitCondition"], "take_profit")

    def test_order_type_maps_to_pump_protection_order_type(self):
        client, transport = _make_client()
        client.set_exits("c629f5ba3a643a8236477243", order_type="limit")
        _, _, body = transport.calls[-1]
        self.assertEqual(body["pumpProtectionOrderType"], "limit")

    def test_pnl_compare_type_sets_both_fields(self):
        client, transport = _make_client()
        client.set_exits("c629f5ba3a643a8236477243", pnl_compare_type="unrealized")
        _, _, body = transport.calls[-1]
        self.assertEqual(body["stopLossPnlCompareType"], "unrealized")
        self.assertEqual(body["trailingStopPnlCompareType"], "unrealized")

    def test_trailing_overlays(self):
        client, transport = _make_client()
        client.set_exits("c629f5ba3a643a8236477243", trailing_activation=9, trailing_execute=4)
        _, _, body = transport.calls[-1]
        self.assertEqual(body["trailingStopActivation"], 9)
        self.assertEqual(body["trailingStopExecute"], 4)

    def test_post_url_has_grid_market_and_code(self):
        client, transport = _make_client()
        client.set_exits("c629f5ba3a643a8236477243", take_profit=5)
        method, path, _ = transport.calls[-1]
        self.assertEqual(method, "POST")
        self.assertIn("/en/trader/grid_bots/upsert", path)
        self.assertIn("gridMarket=derivative", path)
        self.assertIn("code=c629f5ba3a643a8236477243", path)
        # First call is the resource fetch (no status filter -> stopped bots included).
        self.assertEqual(transport.calls[0][0], "GET")
        self.assertEqual(transport.calls[0][1], LIST_PATH)

    def test_unknown_code_raises_value_error(self):
        transport = FakeTransport({("GET", LIST_PATH): _response(_list_payload())})
        client = GridClient(transport)
        with self.assertRaises(ValueError):
            client.set_exits("no-such-code")

    def test_pair_code_falls_back_to_unified_code(self):
        resource = dict(RESOURCE, pair={"unifiedCode": "NEAR-USDT"})
        client, transport = _make_client(resource=resource)
        client.set_exits("c629f5ba3a643a8236477243", take_profit=5)
        _, _, body = transport.calls[-1]
        self.assertEqual(body["pairCode"], "NEARUSDT")

    def test_grid_market_kwarg_overrides(self):
        client, transport = _make_client()
        client.set_exits("c629f5ba3a643a8236477243", take_profit=5, grid_market="spot")
        _, path, _ = transport.calls[-1]
        self.assertIn("gridMarket=spot", path)

    def test_does_not_stop_or_restart(self):
        client, transport = _make_client()
        client.set_exits("c629f5ba3a643a8236477243", take_profit=5)
        for method, path, _ in transport.calls:
            self.assertNotIn("/stop", path)
            self.assertNotIn("/restart", path)


class TestListExits(unittest.TestCase):
    def test_list_includes_exit_fields(self):
        client, transport = _make_client()
        out = client.list(active_only=False)
        self.assertEqual(len(out), 1)
        row = out[0]
        # Pre-existing keys stay stable.
        for key in ("code", "status", "pair", "exchange", "paperTrading", "gridTradingType",
                    "gridType", "step", "levels", "high", "low", "actions"):
            self.assertIn(key, row)
        # New exit/risk keys.
        self.assertEqual(row["amountPerTrade"], 10)
        self.assertEqual(row["takeProfit"], 5)
        self.assertEqual(row["stopLoss"], 3)
        self.assertEqual(row["stopLossPnlCompareType"], "total")
        self.assertEqual(row["trailingStopActivation"], 5)
        self.assertEqual(row["trailingStopExecute"], 2)
        self.assertEqual(row["trailingStopPnlCompareType"], "total")
        self.assertEqual(row["strategyProfitCondition"], "trailing_stop")
        self.assertEqual(row["strategyStopLossFixedPercentRatio"], 0.05)
        self.assertTrue(row["pumpProtection"])
        self.assertEqual(row["pumpProtectionOrderType"], "limit")
        self.assertEqual(row["stopCondition"], "stop_and_close_all")
        self.assertEqual(row["startCondition"], "immediate")


if __name__ == "__main__":
    unittest.main()
