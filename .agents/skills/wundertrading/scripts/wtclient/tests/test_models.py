import unittest

from pydantic import ValidationError

from wtclient.models import (
    EditTradeStrategy,
    GridUpsertPayload,
    PlaceStrategyTrade,
    grid_line_geometry,
)


class TestPlaceStrategyTrade(unittest.TestCase):
    def base(self):
        return dict(
            exchangeCode="HYPERLIQUID_SWAP",
            pairCode="191",
            profilesCodes=["prof1"],
            side="long",
            orderType="market",
            amountPerTrade=50,
            amountPerTradeType="quote",
        )

    def test_valid_market(self):
        m = PlaceStrategyTrade.model_validate(self.base())
        self.assertIsNone(m.payload().get("price"))
        self.assertNotIn("price", m.payload())

    def test_limit_requires_price_and_ttl(self):
        with self.assertRaises(ValidationError):
            PlaceStrategyTrade.model_validate({**self.base(), "orderType": "limit"})

    def test_market_rejects_price(self):
        with self.assertRaises(ValidationError):
            PlaceStrategyTrade.model_validate({**self.base(), "price": 100})

    def test_percent_coercion_and_tp_sum(self):
        m = PlaceStrategyTrade.model_validate({
            **self.base(),
            "takeProfits": [
                {"priceDeviation": "2%", "portfolio": "40%"},
                {"priceDeviation": "4%", "portfolio": 60},
            ],
            "stopLoss": "3%",
        })
        self.assertEqual(m.takeProfits[0].priceDeviation, "0.02")
        self.assertEqual(m.takeProfits[1].portfolio, "0.6")
        self.assertEqual(m.stopLoss, "0.03")

    def test_tp_sum_must_equal_one(self):
        with self.assertRaises(ValidationError):
            PlaceStrategyTrade.model_validate({
                **self.base(),
                "takeProfits": [
                    {"priceDeviation": "2%", "portfolio": "30%"},
                    {"priceDeviation": "4%", "portfolio": "30%"},
                ],
            })

    def test_client_id_regex(self):
        with self.assertRaises(ValidationError):
            PlaceStrategyTrade.model_validate({**self.base(), "clientId": "bad id with spaces!!!"})


class TestEditTradeStrategy(unittest.TestCase):
    def test_classic_rejects_dca(self):
        m = EditTradeStrategy.model_validate({"id": "abc", "extraOrderCount": 3})
        with self.assertRaises(ValueError):
            m.validate_for_group("classic")

    def test_move_execute_requires_move_price(self):
        with self.assertRaises(ValidationError):
            EditTradeStrategy.model_validate({"id": "abc", "stopLossMoveExecutePrice": 100})


class TestGridGeometry(unittest.TestCase):
    def test_bracket_levels(self):
        lines, levels, low, high = grid_line_geometry(61.055, 112.935, 3.0, 87.009)
        self.assertGreater(levels, 1)
        self.assertLessEqual(low, 87.009)
        self.assertGreaterEqual(high, 87.009)
        self.assertAlmostEqual(lines[-1], 112.935)

    def test_grid_interval_requires_channel(self):
        with self.assertRaises(ValidationError):
            GridUpsertPayload.model_validate({
                "exchangeCode": "HYPERLIQUID_SWAP",
                "pairCode": "191",
                "profilesCodes": ["p"],
                "gridType": "interval",
                "gridPercentStep": 0.03,
                "amountPerTrade": 20,
                "amountPerTradeType": "base",
            })

    def test_grid_with_channel(self):
        m = GridUpsertPayload.model_validate({
            "exchangeCode": "HYPERLIQUID_SWAP",
            "pairCode": "191",
            "profilesCodes": ["p"],
            "gridPercentStep": 0.03,
            "amountPerTrade": 20,
            "amountPerTradeType": "base",
            "lowPrice": 61.055,
            "highPrice": 112.935,
        })
        self.assertIsNotNone(m.gridLevels if m.gridLevels else m.with_channel(61.055, 112.935).gridLevels)


if __name__ == "__main__":
    unittest.main()
