from __future__ import annotations

import sys
import math
import json
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.selection.strategy import (  # noqa: E402
    MembershipHistory,
    factor_observation,
    factor_observations,
    month_end_dates,
    rank_candidates,
    select_codes,
)
from ashare_lab.qlib_mini import build_point_in_time_samples, kbar_features, rolling_features, volume_rolling_features, price_position_features  # noqa: E402
from ashare_lab.qlib_model import RidgeRankModel, feature_definitions  # noqa: E402
from ashare_lab.selection.events import validate_corporate_action_evidence, validate_delisting_event, validate_event_evidence  # noqa: E402
from ashare_lab.selection.corporate_actions import apply_corporate_action, convert_position  # noqa: E402


class SelectionStrategyTests(unittest.TestCase):
    def valid_history(self):
        return [dict(trade_date=(date(2023, 1, 1) + timedelta(days=i)).isoformat(),
                     code="000001", close_qfq=str(100+i), tradestatus="1", is_st="0",
                     open="10", high="11", low="9", close="10", preclose="10",
                     volume="10000000", amount="50000000") for i in range(254)]

    def test_missing_price_in_middle_of_year_is_ineligible(self):
        rows = self.valid_history()
        rows[100]["close_qfq"] = ""
        self.assertIsNone(factor_observation(rows, rows[-1]["trade_date"]))

    def test_suspended_history_is_not_low_volatility_eligibility(self):
        rows = self.valid_history()
        rows[100]["tradestatus"] = "0"
        self.assertIsNone(factor_observation(rows, rows[-1]["trade_date"]))

    def test_halt_policy_can_be_bounded_without_changing_default(self):
        rows = self.valid_history()
        rows[100]["tradestatus"] = "0"
        self.assertIsNotNone(factor_observation(rows, rows[-1]["trade_date"],
                                                 max_window_halt_days=1,
                                                 max_consecutive_halt_days=1))
        recent_rows = self.valid_history(); recent_rows[-2]["tradestatus"] = "0"
        self.assertIsNone(factor_observation(recent_rows, recent_rows[-1]["trade_date"],
                                              max_window_halt_days=1,
                                              max_consecutive_halt_days=1,
                                              recent_halt_window=20,
                                              max_recent_halt_days=0))

    def test_resumption_cooldown_counts_trading_days_after_latest_halt(self):
        rows = self.valid_history()
        rows[-4]["tradestatus"] = "0"
        self.assertIsNone(factor_observation(rows, rows[-1]["trade_date"],
                                             max_window_halt_days=5,
                                             max_consecutive_halt_days=5,
                                             resumption_cooldown_days=4))
        self.assertIsNotNone(factor_observation(rows, rows[-1]["trade_date"],
                                                max_window_halt_days=5,
                                                max_consecutive_halt_days=5,
                                                resumption_cooldown_days=3))

    def test_missing_calendar_day_cannot_extend_the_momentum_window(self):
        rows = self.valid_history()
        calendar = [r["trade_date"] for r in rows]
        rows.pop(100)
        self.assertIsNone(factor_observation(rows, rows[-1]["trade_date"], trading_dates=calendar))

    def test_bad_execution_price_is_ineligible(self):
        rows = self.valid_history()
        rows[-1]["open"] = "NaN"
        self.assertIsNone(factor_observation(rows, rows[-1]["trade_date"]))

    def test_future_prices_do_not_change_observation(self):
        rows = self.valid_history()
        signal = rows[-2]["trade_date"]
        original = factor_observation(rows, signal)
        rows[-1]["close_qfq"] = "999999"
        self.assertEqual(original, factor_observation(rows, signal))

    def test_month_end_dates_uses_last_observed_trading_day(self):
        self.assertEqual(
            month_end_dates(["2024-01-02", "2024-01-31", "2024-02-01", "2024-02-29"]),
            ["2024-01-31", "2024-02-29"],
        )

    def test_membership_uses_last_observed_snapshot_not_future_update(self):
        rows = []
        for code in range(300):
            rows.append({"observed_date": "2024-01-05", "code": f"{code:06d}"})
        for code in range(1, 301):
            rows.append({"observed_date": "2024-01-12", "code": f"{code:06d}"})
        history = MembershipHistory(rows)
        self.assertIn("000000", history.as_of("2024-01-11"))
        self.assertNotIn("000000", history.as_of("2024-01-12"))

    def test_factor_uses_t_minus_21_and_t_minus_252(self):
        start = date(2023, 1, 1)
        rows = []
        for index in range(253):
            rows.append({
                "trade_date": (start + timedelta(days=index)).isoformat(),
                "code": "000001",
                "close_qfq": str(100 + index),
                "amount": "50000000",
                "tradestatus": "1",
                "is_st": "0",
                "open": "100", "high": "100", "low": "100", "close": "100",
                "preclose": "100", "volume": "500000",
            })
        observation = factor_observation(rows, rows[-1]["trade_date"])
        self.assertIsNotNone(observation)
        assert observation is not None
        expected = Decimal(str(100 + 252 - 21)) / Decimal("100") - 1
        self.assertEqual(Decimal(observation["momentum_12_1"]), expected.quantize(Decimal("0.000000000001")))
        batch = factor_observations(rows, [rows[-1]["trade_date"]])
        self.assertEqual(batch, [observation])

    def test_factor_rejects_st_and_low_liquidity(self):
        rows = []
        for index in range(253):
            rows.append({
                "trade_date": f"2024-{1 + index // 28:02d}-{1 + index % 28:02d}",
                "code": "000001",
                "close_qfq": str(100 + index),
                "amount": "1000000",
                "tradestatus": "1",
                "is_st": "0",
            })
        self.assertIsNone(factor_observation(rows, rows[-1]["trade_date"]))
        rows[-1]["amount"] = "50000000"
        rows[-1]["is_st"] = "1"
        self.assertIsNone(factor_observation(rows, rows[-1]["trade_date"], minimum_median_amount=Decimal("0")))

    def test_qlib_mini_label_uses_future_only_and_drops_tail(self):
        rows = self.valid_history()
        for row in rows:
            row["volume"] = "100000"
        samples = build_point_in_time_samples(rows, horizon=2, lookback=20)
        self.assertEqual(samples[-1]["trade_date"], rows[-3]["trade_date"])
        self.assertEqual(samples[-1]["label_forward_return"], format((float(rows[-1]["close_qfq"]) / float(rows[-2]["close_qfq"])) - 1, ".12f"))
        signal_date = samples[-1]["trade_date"]
        original = samples[-1]["label_forward_return"]
        rows[-1]["close_qfq"] = "999999"
        changed = build_point_in_time_samples(rows, horizon=2, lookback=20)[-1]["label_forward_return"]
        self.assertNotEqual(original, changed)
        rows[0]["close_qfq"] = "999999"
        self.assertEqual(changed, build_point_in_time_samples(rows, horizon=2, lookback=20)[-1]["label_forward_return"])

    def test_ridge_model_requires_fit_and_predicts_finite_scores(self):
        rows = build_point_in_time_samples(self.valid_history())
        model = RidgeRankModel(alpha=1.0)
        with self.assertRaises(RuntimeError):
            model.predict(rows[:1])
        model.fit(rows)
        scores = model.predict(rows[:3])
        self.assertEqual(len(scores), 3)
        self.assertTrue(all(math.isfinite(score) for score in scores))

    def test_qlib_feature_definitions_are_explicit_and_unique(self):
        definitions = feature_definitions()
        self.assertEqual(tuple(item["name"] for item in definitions[:5]), ("return_5", "return_20", "volatility_20", "amount_median_5", "volume_median_5"))
        self.assertEqual(len(definitions), 22)
        self.assertEqual(len({item["name"] for item in definitions}), len(definitions))
        self.assertTrue(all(item["formula"] and item["unit"] and item["missing"] for item in definitions))

    def test_alpha158_kbar_formulas(self):
        features = kbar_features({"open": "10", "high": "14", "low": "8", "close": "12"})
        self.assertAlmostEqual(features["KMID"], 0.2)
        self.assertAlmostEqual(features["KLEN"], 0.6)
        self.assertAlmostEqual(features["KMID2"], 1 / 3)
        self.assertAlmostEqual(features["KUP2"], 1 / 3)
        self.assertAlmostEqual(features["KLOW2"], 1 / 3)
        self.assertAlmostEqual(features["KSFT"], 0.2)
        self.assertAlmostEqual(features["KSFT2"], 1 / 3)
        self.assertIsNone(kbar_features({"open": "10", "high": "10", "low": "10", "close": "10"}))

    def test_alpha158_rolling_formulas(self):
        rows = [{"close": str(value)} for value in range(1, 22)]
        features = rolling_features(rows, 20, window=20)
        self.assertAlmostEqual(features["ROC20"], 1 / 21)
        self.assertAlmostEqual(features["MA20"], 11 / 21)
        self.assertIsNotNone(features["STD20"])
        self.assertIsNone(rolling_features(rows, 1, window=20))

    def test_alpha158_volume_rolling_formulas_reject_zero_current_volume(self):
        rows = [{"volume": str(value)} for value in range(1, 22)]
        features = volume_rolling_features(rows, 20, window=20)
        self.assertAlmostEqual(features["VMA20"], 11 / 21)
        self.assertIsNotNone(features["VSTD20"])
        rows[-1]["volume"] = "0"
        self.assertIsNone(volume_rolling_features(rows, 20, window=20))

    def test_alpha158_price_position_formulas(self):
        rows = [{"high": str(value + 2), "low": str(value), "close": str(value + 1)} for value in range(1, 22)]
        features = price_position_features(rows, 20, window=20)
        self.assertAlmostEqual(features["MAX20"], 23 / 22)
        self.assertAlmostEqual(features["MIN20"], 1 / 22)
        self.assertAlmostEqual(features["RSV20"], 21 / 22)
        flat = [{"high": "10", "low": "10", "close": "10"} for _ in range(21)]
        self.assertIsNone(price_position_features(flat, 20, window=20))

    def test_delisting_event_validation_requires_verified_price(self):
        event = {"code":"000540","event_type":"DELISTING","effective_date":"2023-07-01","last_tradable_date":"2023-06-30","settlement_date":"2023-07-03","settlement_price":"0.50","price_basis":"SETTLEMENT","source":"official","source_as_of":"2023-07-03","sha256":"a"*64}
        validate_delisting_event(event)
        event["settlement_price"] = ""
        with self.assertRaises(ValueError): validate_delisting_event(event)
        event["settlement_price"] = "0.50"; event["price_basis"] = "UNKNOWN"; event["settlement_price"] = "0"
        validate_delisting_event(event)

    def test_000540_evidence_snapshot_is_not_settlement_ready(self):
        path = PROJECT_ROOT / "data" / "imports" / "delisting_events_000540_20260908" / "000540.json"
        event = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(event["event_type"], "DELISTING")
        self.assertEqual(event["effective_date"], "2023-06-30")
        self.assertEqual(event["post_listing_code"], "400174")
        self.assertEqual(event["price_basis"], "UNKNOWN")
        self.assertEqual(event["settlement_price"], "0")
        validate_event_evidence(event)

    def test_600003_corporate_action_maps_successors_without_cash_settlement(self):
        path = PROJECT_ROOT / "data" / "imports" / "corporate_actions_600003_20260908" / "600003.json"
        event = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(event["event_type"], "DEMERGER_AND_TERMINATION")
        self.assertEqual({item["code"] for item in event["successors"]}, {"601188", "601518"})
        self.assertEqual([item["share_ratio"] for item in event["successors"]], ["1", "1"])
        self.assertEqual(event["price_basis"], "UNKNOWN")

    def test_batch_corporate_action_snapshots_have_explicit_ratios(self):
        expected = {"600591": ("600115", "1.3"), "600102": ("600022", "2.43"), "000527": ("000333", "0.3447")}
        for source_code, (successor, ratio) in expected.items():
            path = PROJECT_ROOT / "data" / "imports" / "corporate_actions_batch1_20260908" / f"{source_code}.json"
            event = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(event["successors"][0]["code"], successor)
            self.assertEqual(event["successors"][0]["share_ratio"], ratio)
            self.assertEqual(event["price_basis"], "UNKNOWN")
            validate_corporate_action_evidence(event)

    def test_corporate_action_validator_rejects_invalid_ratio(self):
        event = {"source_code":"600591","event_type":"MERGER_AND_TERMINATION","effective_date":"2010-01-25","successors":[{"code":"600115","share_ratio":"0"}],"cash_option":"UNKNOWN","settlement_price":"0","price_basis":"UNKNOWN","source":"test","source_as_of":"2026-09-08"}
        with self.assertRaises(ValueError):
            validate_corporate_action_evidence(event)

    def test_position_conversion_preserves_fractional_shares_for_review(self):
        result = convert_position(Decimal("100"), [{"code": "600115", "share_ratio": "1.3"}])
        self.assertEqual(result["converted_shares"]["600115"], Decimal("130.0"))
        self.assertFalse(result["cash_settlement_required"])
        fractional = convert_position(Decimal("101"), [{"code": "000333", "share_ratio": "0.3447"}])
        self.assertTrue(fractional["cash_settlement_required"])
        self.assertEqual(fractional["status"], "REQUIRES_CASH_SETTLEMENT_REVIEW")

    def test_apply_corporate_action_replaces_source_and_emits_audit(self):
        result = apply_corporate_action(
            {"600003": Decimal("100"), "000001": Decimal("50")},
            {"source_code":"600003", "event_type":"DEMERGER_AND_TERMINATION", "effective_date":"2010-02-26", "successors":[{"code":"601188","share_ratio":"1"},{"code":"601518","share_ratio":"1"}]},
        )
        self.assertNotIn("600003", result["positions"])
        self.assertEqual(result["positions"]["601188"], Decimal("100"))
        self.assertEqual(result["positions"]["601518"], Decimal("100"))
        self.assertEqual(len(result["audit"]), 2)

    def test_composite_ranking_is_deterministic(self):
        ranked = rank_candidates([
            {"code": "000001", "momentum_12_1": "0.3", "volatility_63": "0.4"},
            {"code": "000002", "momentum_12_1": "0.2", "volatility_63": "0.1"},
            {"code": "000003", "momentum_12_1": "-0.1", "volatility_63": "0.3"},
        ])
        self.assertEqual(select_codes(ranked, 2), ["000002", "000001"])


if __name__ == "__main__":
    unittest.main()
