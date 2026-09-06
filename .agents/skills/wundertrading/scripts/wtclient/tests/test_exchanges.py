"""Tests for :class:`wtclient.clients.exchanges.ExchangesClient` + Profile."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from wtclient.clients.exchanges import (
    ACCOUNT_LIMITS_PATH,
    DEFAULT_VENUE_FAMILIES,
    PROFILE_GRID_PATH,
    PROFILE_UPSERT_PATH,
    ExchangesClient,
    paper_profile_body,
)
from wtclient.errors import WunApiError
from wtclient.models.profiles import Profile, parse_profiles


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
        resp.json.side_effect = WunApiError("not json")
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
        return handler  # NOTE: a MagicMock would be truthy-callable; never call it

    def close(self):
        pass


PROFILE_HAL = {
    "_embedded": {
        "items": [
            {
                "id": "47ca341d5a01c1df3781e490",
                "code": "47ca341d5a01c1df91b8b9ed",
                "name": "demo-hype",
                "exchangeFamily": "HYPERLIQUID",
                "paperTrading": True,
                "enabled": True,
                "marginMode": "cross",
                "tradeMode": "hedge_mode",
                "favorite": False,
            },
            {
                "id": "0f0e",
                "code": "0f0ecode",
                "name": "live-bn",
                "exchangeFamily": "BINANCE",
                "paperTrading": False,
                "enabled": True,
                "marginMode": "cross",
                "tradeMode": "hedge_mode",
                "favorite": False,
            },
            {"id": "missing-fields", "name": "sparse"},
        ]
    },
    "total_items": 3,
}

LIMITS = {
    "gridBots": {"allowOnCurrentPlan": True, "active": 1, "max": 200, "exists": True},
    "openPositions": {"allowOnCurrentPlan": True, "active": 0, "max": 100, "exists": False},
}


class TestProfileModel(unittest.TestCase):
    def test_from_hal_full(self):
        p = Profile.from_hal(PROFILE_HAL["_embedded"]["items"][0])
        self.assertEqual(p.id, "47ca341d5a01c1df3781e490")
        self.assertEqual(p.name, "demo-hype")
        self.assertEqual(p.exchange_family, "HYPERLIQUID")
        self.assertTrue(p.paper_trading)
        self.assertTrue(p.enabled)
        self.assertEqual(p.margin_mode, "cross")
        self.assertEqual(p.trade_mode, "hedge_mode")
        self.assertFalse(p.favorite)

    def test_from_hal_tolerates_missing(self):
        p = Profile.from_hal({"id": "x", "name": "sparse"})
        self.assertEqual(p.exchange_family, None)
        self.assertIsNone(p.paper_trading)

    def test_from_hal_tolerates_non_dict(self):
        p = Profile.from_hal(None)
        self.assertIsNone(p.name)

    def test_as_dict_roundtrip(self):
        p = Profile.from_hal(PROFILE_HAL["_embedded"]["items"][0])
        d = p.as_dict()
        self.assertEqual(d["exchangeFamily"], "HYPERLIQUID")
        self.assertEqual(d["paperTrading"], True)
        self.assertIn("tradeMode", d)

    def test_parse_profiles(self):
        out = parse_profiles(PROFILE_HAL["_embedded"]["items"])
        self.assertEqual(len(out), 3)
        self.assertEqual(parse_profiles(None), [])
        self.assertEqual(parse_profiles([]), [])


class TestPaperProfileBody(unittest.TestCase):
    def test_shape(self):
        body = paper_profile_body("demo-hype", "HYPERLIQUID")
        self.assertEqual(body["name"], "demo-hype")
        self.assertEqual(body["exchangeFamily"], "HYPERLIQUID")
        self.assertTrue(body["paperTrading"])
        self.assertTrue(body["enabled"])
        self.assertFalse(body["favorite"])
        self.assertEqual(body["marginMode"], "cross")
        self.assertEqual(body["tradeMode"], "hedge_mode")
        # placeholder keys are 32-hex, never real
        for key in ("api", "secret"):
            self.assertEqual(len(body[key]), 32)
            int(body[key], 16)

    def test_unique_placeholder_keys(self):
        bodies = [paper_profile_body("x") for _ in range(5)]
        keys = {(b["api"], b["secret"]) for b in bodies}
        self.assertEqual(len(keys), 5)

    def test_strips_and_validates_name(self):
        self.assertEqual(paper_profile_body("  demo  ")["name"], "demo")
        with self.assertRaises(ValueError):
            paper_profile_body("   ")
        with self.assertRaises(ValueError):
            paper_profile_body("")


class TestClientReads(unittest.TestCase):
    def test_list_profiles(self):
        t = FakeTransport({("GET", PROFILE_GRID_PATH): _response(PROFILE_HAL)})
        client = ExchangesClient(t)
        out = client.list_profiles()
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0].name, "demo-hype")
        self.assertEqual(out[1].paper_trading, False)
        self.assertEqual(out[2].name, "sparse")
        self.assertEqual(t.calls[0][0], "GET")

    def test_list_profiles_empty(self):
        t = FakeTransport({("GET", PROFILE_GRID_PATH): _response({"_embedded": {"items": []}})})
        self.assertEqual(ExchangesClient(t).list_profiles(), [])

    def test_account_limits_raw(self):
        t = FakeTransport({("GET", ACCOUNT_LIMITS_PATH): _response(LIMITS)})
        out = ExchangesClient(t).account_limits()
        self.assertEqual(out["gridBots"]["max"], 200)
        self.assertEqual(out["openPositions"]["active"], 0)


class TestCreatePaperProfile(unittest.TestCase):
    def test_success(self):
        t = FakeTransport(
            {("POST", PROFILE_UPSERT_PATH): _response({"result": {"id": "abc"}}, status=201)}
        )
        out = ExchangesClient(t).create_paper_profile("demo-hype", "HYPERLIQUID")
        self.assertTrue(out["created"])
        self.assertFalse(out["already_exists"])
        self.assertEqual(out["status"], 201)
        self.assertEqual(out["violations"], [])
        self.assertEqual(out["response"], {"result": {"id": "abc"}})
        # body shape
        _, _, body = t.calls[0]
        self.assertEqual(body["name"], "demo-hype")
        self.assertTrue(body["paperTrading"])
        self.assertEqual(body["exchangeFamily"], "HYPERLIQUID")

    def test_duplicate_400(self):
        dup = {
            "code": 400,
            "result": {
                "violations": [
                    {
                        "propertyPath": "name",
                        "message": "You have already created an account with that name",
                    }
                ]
            },
        }
        t = FakeTransport({("POST", PROFILE_UPSERT_PATH): _response(dup, status=400)})
        out = ExchangesClient(t).create_paper_profile("demo-hype")
        self.assertFalse(out["created"])
        self.assertTrue(out["already_exists"])
        self.assertEqual(out["status"], 400)
        self.assertEqual(len(out["violations"]), 1)
        self.assertEqual(out["violations"][0]["propertyPath"], "name")
        self.assertNotIn("error", out)
        self.assertEqual(out["response"], dup)

    def test_other_400_is_error(self):
        bad = {"code": 400, "result": {"violations": [{"propertyPath": "exchangeFamily", "message": "unsupported"}]}}
        t = FakeTransport({("POST", PROFILE_UPSERT_PATH): _response(bad, status=400)})
        out = ExchangesClient(t).create_paper_profile("x", "NOPE")
        self.assertFalse(out["created"])
        self.assertFalse(out["already_exists"])
        self.assertIn("error", out)

    def test_non_json_error_body(self):
        t = FakeTransport(
            {("POST", PROFILE_UPSERT_PATH): _response(None, status=502, text="<html>bad gateway")}
        )
        out = ExchangesClient(t).create_paper_profile("x")
        self.assertFalse(out["created"])
        self.assertEqual(out["status"], 502)
        self.assertIn("bad gateway", (out["message"] or ""))

    def test_transport_error_never_raises(self):
        class Boom:
            def request(self, *a, **k):
                raise WunApiError("HTTP 500", status_code=500)

            def close(self):
                pass

        out = ExchangesClient(Boom()).create_paper_profile("x")
        self.assertFalse(out["created"])
        self.assertIn("error", out)

    def test_empty_name_never_raises(self):
        out = ExchangesClient(MagicMock()).create_paper_profile("  ")
        self.assertFalse(out["created"])
        self.assertIn("error", out)


class TestEnsurePaperProfiles(unittest.TestCase):
    def _transport(self, existing=None, create_response=None):
        script = {
            ("GET", PROFILE_GRID_PATH): _response(
                {"_embedded": {"items": existing or []}}
            )
        }
        if create_response is not None:
            script[("POST", PROFILE_UPSERT_PATH)] = create_response
        return FakeTransport(script)

    def test_present(self):
        existing = PROFILE_HAL["_embedded"]["items"][:1]  # demo-hype HYPERLIQUID paper
        t = self._transport(existing=existing)
        out = ExchangesClient(t).ensure_paper_profiles({"hyperliquid": ["demo-hype"]})
        self.assertTrue(out["ok"])
        self.assertEqual(
            out["venues"]["hyperliquid"]["demo-hype"]["state"], "present"
        )
        self.assertEqual(out["created"], [])
        self.assertEqual(out["errors"], [])
        # listing happens exactly once, no writes
        self.assertEqual([c[:2] for c in t.calls], [("GET", PROFILE_GRID_PATH)])

    def test_created(self):
        t = self._transport(existing=[], create_response=_response({"ok": True}, status=201))
        out = ExchangesClient(t).ensure_paper_profiles({"hyperliquid": ["demo-hype"]})
        self.assertTrue(out["ok"])
        self.assertEqual(out["venues"]["hyperliquid"]["demo-hype"]["state"], "created")
        self.assertEqual(out["created"], ["hyperliquid/demo-hype"])
        _, _, body = t.calls[1]
        self.assertEqual(body["exchangeFamily"], "HYPERLIQUID")

    def test_wrong_shape_not_paper_never_mutates(self):
        t = self._transport(existing=[  # live BINANCE profile named demo-hype
            {"id": "1", "name": "demo-hype", "exchangeFamily": "BINANCE",
             "paperTrading": False, "enabled": True}
        ])
        out = ExchangesClient(t).ensure_paper_profiles({"hyperliquid": ["demo-hype"]})
        self.assertFalse(out["ok"])
        entry = out["venues"]["hyperliquid"]["demo-hype"]
        self.assertEqual(entry["state"], "error")
        self.assertIn("paper", entry["detail"].lower())
        # no POST happened
        self.assertEqual(len(t.calls), 1)

    def test_wrong_shape_family_mismatch(self):
        t = self._transport(existing=[
            {"id": "1", "name": "demo-hype", "exchangeFamily": "BINANCE",
             "paperTrading": True, "enabled": True}
        ])
        out = ExchangesClient(t).ensure_paper_profiles({"hyperliquid": ["demo-hype"]})
        self.assertFalse(out["ok"])
        entry = out["venues"]["hyperliquid"]["demo-hype"]
        self.assertEqual(entry["state"], "error")
        self.assertIn("family", entry["detail"].lower())

    def test_create_error(self):
        t = self._transport(
            existing=[],
            create_response=_response({"code": 500}, status=500),
        )
        out = ExchangesClient(t).ensure_paper_profiles({"binance": ["demo-bn"]})
        self.assertFalse(out["ok"])
        self.assertEqual(out["venues"]["binance"]["demo-bn"]["state"], "error")
        self.assertEqual(len(out["errors"]), 1)

    def test_unknown_venue(self):
        t = self._transport(existing=[])
        out = ExchangesClient(t).ensure_paper_profiles({"okx": ["demo-okx"]})
        self.assertFalse(out["ok"])
        self.assertEqual(out["venues"]["okx"]["demo-okx"]["state"], "error")

    def test_custom_families(self):
        t = self._transport(existing=[], create_response=_response({"ok": True}, status=201))
        out = ExchangesClient(t).ensure_paper_profiles(
            {"okx": ["x"]}, families={"okx": "OKX"}
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["venues"]["okx"]["x"]["state"], "created")

    def test_list_failure_never_raises(self):
        class Boom:
            def request(self, *a, **k):
                raise WunApiError("HTTP 503", status_code=503)

            def close(self):
                pass

        out = ExchangesClient(Boom()).ensure_paper_profiles({"binance": ["demo-bn"]})
        self.assertFalse(out["ok"])
        self.assertEqual(
            out["venues"]["binance"]["demo-bn"]["state"], "error"
        )

    def test_default_venue_families(self):
        self.assertEqual(
            DEFAULT_VENUE_FAMILIES,
            {"hyperliquid": "HYPERLIQUID", "binance": "BINANCE"},
        )


class TestDeleteProfile(unittest.TestCase):
    DELETE_PATH = "/en/trader/my-exchanges/master-api-profile/47ca341d5a01c1df91b8b9ed/delete"

    def _client(self, existing=None, delete_response=None):
        script = {
            ("GET", PROFILE_GRID_PATH): _response(
                {"_embedded": {"items": existing or []}}
            ),
            ("DELETE", self.DELETE_PATH): delete_response
            or _response({"status": "ok", "message": "Delete success."}),
        }
        t = FakeTransport(script)
        return ExchangesClient(t), t

    def test_delete_profile_request_shape(self):
        client, t = self._client()
        out = client.delete_profile("47ca341d5a01c1df91b8b9ed")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(t.calls, [("DELETE", self.DELETE_PATH, None)])

    def test_delete_profile_requires_code(self):
        client, _ = self._client()
        with self.assertRaises(ValueError):
            client.delete_profile("")
        with self.assertRaises(ValueError):
            client.delete_profile(None)

    def test_delete_profile_non_json_response(self):
        client, _ = self._client(
            delete_response=_response(None, status=200, text="Delete success.")
        )
        out = client.delete_profile("47ca341d5a01c1df91b8b9ed")
        self.assertEqual(out["status"], "ok")

    def test_delete_by_name_paper(self):
        client, t = self._client(existing=PROFILE_HAL["_embedded"]["items"][:1])
        out = client.delete_profile_by_name("demo-hype")
        self.assertTrue(out["deleted"])
        self.assertEqual(out["code"], "47ca341d5a01c1df91b8b9ed")
        self.assertEqual([c[:2] for c in t.calls],
                         [("GET", PROFILE_GRID_PATH), ("DELETE", self.DELETE_PATH)])

    def test_delete_by_name_refuses_non_paper(self):
        client, t = self._client(existing=PROFILE_HAL["_embedded"]["items"][1:2])
        out = client.delete_profile_by_name("live-bn")
        self.assertFalse(out["deleted"])
        self.assertIn("not a paper account", out["error"])
        # listing only — the live profile was never deleted
        self.assertEqual([c[:2] for c in t.calls], [("GET", PROFILE_GRID_PATH)])

    def test_delete_by_name_paper_only_false_allows_live(self):
        live_delete = "/en/trader/my-exchanges/master-api-profile/0f0ecode/delete"
        script = {
            ("GET", PROFILE_GRID_PATH): _response(
                {"_embedded": {"items": PROFILE_HAL["_embedded"]["items"][1:2]}}
            ),
            ("DELETE", live_delete): _response(
                {"status": "ok", "message": "Delete success."}
            ),
        }
        t = FakeTransport(script)
        out = ExchangesClient(t).delete_profile_by_name("live-bn", paper_only=False)
        self.assertTrue(out["deleted"])
        self.assertEqual(
            [c[:2] for c in t.calls],
            [("GET", PROFILE_GRID_PATH), ("DELETE", live_delete)],
        )

    def test_delete_by_name_missing(self):
        client, t = self._client()
        out = client.delete_profile_by_name("nope")
        self.assertFalse(out["deleted"])
        self.assertIn("no profile named", out["error"])

    def test_delete_by_name_list_failure_never_raises(self):
        t = FakeTransport({})  # GET not scripted -> 404
        out = ExchangesClient(t).delete_profile_by_name("demo-hype")
        self.assertFalse(out["deleted"])
        self.assertTrue(out["error"])

    def test_profile_code_parsed(self):
        p = Profile.from_hal(PROFILE_HAL["_embedded"]["items"][0])
        self.assertEqual(p.code, "47ca341d5a01c1df91b8b9ed")
        self.assertEqual(p.as_dict()["code"], "47ca341d5a01c1df91b8b9ed")


if __name__ == "__main__":
    unittest.main()
