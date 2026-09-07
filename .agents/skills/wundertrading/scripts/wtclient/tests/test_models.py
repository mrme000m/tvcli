import unittest

from pydantic import ValidationError

from wtclient.models import (
    EditTradeStrategy,
    GridUpsertPayload,
    PlaceStrategyTrade,
    PnlCompareType,
    PositionsProfitCondition,
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




GROUND_TRUTH_EDIT_PAYLOAD = {
    "exchangeCode": "BINANCE_FUTURES",
    "pairCode": "NEARUSDT",
    "profilesCodes": ["c629f5ba3a643a82ccadd9aa"],
    "gridType": "interval",
    "gridMethod": "classic",
    "gridTradingType": "long",
    "gridPercentStep": 0.0124,
    "gridTickStep": None,
    "gridLevels": 12,
    "midPrice": 2.474094,
    "initPrice": 2.295,
    "closestHighLevelPrice": 2.3149002970609955,
    "closestLowLevelPrice": 2.2865471128615127,
    "amountPerTrade": 10,
    "amountPerTradeType": "base",
    "stopOnOutOfGrid": False,
    "startCondition": "immediate",
    "signalCode": "3a6e8229bac6f53aba4864aa",
    "maxRequiredAmount": "100 USDT",
    "leverage": 1,
    "takeProfit": 5,
    "stopLoss": 3,
    "stopLossPnlCompareType": "total",
    "pumpProtection": True,
    "pumpProtectionOrderType": "limit",
    "trailingStopExecute": 2,
    "trailingStopActivation": 5,
    "trailingStopPnlCompareType": "total",
    "highPrice": 2.474094,
    "lowPrice": 2.149906,
    "stopCondition": "stop_and_close_all",
    "profitCurrencyType": "base",
    "strategyProfitCondition": "trailing_stop",
    "strategyStopLossFixedPercentRatio": 0.05,
}


class TestGridExitFields(unittest.TestCase):
    """Live-verified edit-payload facts (authenticated form, 2026-09-07)."""

    def test_ground_truth_edit_payload_validates(self):
        m = GridUpsertPayload.model_validate(GROUND_TRUTH_EDIT_PAYLOAD)
        out = m.payload()
        self.assertEqual(out["takeProfit"], 5)
        self.assertEqual(out["stopLoss"], 3)
        self.assertEqual(out["trailingStopExecute"], 2)
        self.assertEqual(out["trailingStopActivation"], 5)
        self.assertEqual(out["trailingStopPnlCompareType"], "total")
        self.assertEqual(out["stopLossPnlCompareType"], "total")
        self.assertEqual(out["pumpProtectionOrderType"], "limit")
        self.assertEqual(out["strategyStopLossFixedPercentRatio"], 0.05)
        self.assertEqual(out["strategyProfitCondition"], "trailing_stop")
        self.assertEqual(out["signalCode"], "3a6e8229bac6f53aba4864aa")
        self.assertEqual(out["startCondition"], "immediate")
        self.assertEqual(out["maxRequiredAmount"], "100 USDT")
        self.assertEqual(out["gridLevels"], 12)
        self.assertEqual(out["gridPercentStep"], 0.0124)
        self.assertEqual(out["highPrice"], 2.474094)
        self.assertEqual(out["lowPrice"], 2.149906)

    def test_pnl_compare_type_enums_resolve(self):
        self.assertEqual(PnlCompareType("unrealized"), PnlCompareType.UNREALIZED)
        self.assertEqual(PnlCompareType("total"), PnlCompareType.TOTAL)
        self.assertEqual(PositionsProfitCondition("take_profit"), PositionsProfitCondition.TAKE_PROFIT)
        self.assertEqual(PositionsProfitCondition("trailing_stop"), PositionsProfitCondition.TRAILING_STOP)

    def test_unrealized_pnl_compare_type_validates(self):
        m = GridUpsertPayload.model_validate(
            {**GROUND_TRUTH_EDIT_PAYLOAD, "stopLossPnlCompareType": "unrealized"}
        )
        self.assertEqual(m.stopLossPnlCompareType, PnlCompareType.UNREALIZED)
        self.assertEqual(m.payload()["stopLossPnlCompareType"], "unrealized")

    def test_pump_protection_limit_order_type(self):
        m = GridUpsertPayload.model_validate(GROUND_TRUTH_EDIT_PAYLOAD)
        self.assertEqual(m.pumpProtectionOrderType, "limit")
        with self.assertRaises(ValidationError):
            GridUpsertPayload.model_validate(
                {**GROUND_TRUTH_EDIT_PAYLOAD, "pumpProtectionOrderType": "iceberg"}
            )

    def test_signal_code_alongside_immediate_start(self):
        # Live edit flow sends the existing signalCode with startCondition=immediate.
        m = GridUpsertPayload.model_validate(GROUND_TRUTH_EDIT_PAYLOAD)
        self.assertEqual(m.signalCode, "3a6e8229bac6f53aba4864aa")
        self.assertEqual(m.startCondition.value, "immediate")

    def test_start_condition_rules_still_enforced(self):
        with self.assertRaises(ValidationError):
            GridUpsertPayload.model_validate(
                {**GROUND_TRUTH_EDIT_PAYLOAD, "startCondition": "indicator"}
            )
        with self.assertRaises(ValidationError):
            GridUpsertPayload.model_validate(
                {**GROUND_TRUTH_EDIT_PAYLOAD, "startCondition": "webhook_alert"}
            )


if __name__ == "__main__":
    unittest.main()
