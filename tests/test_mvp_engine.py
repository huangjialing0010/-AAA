from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.mvp.engine import CostConfig, DividendEvent, run_engine  # noqa: E402
from ashare_lab.mvp.metrics import calculate_period_metrics  # noqa: E402
from ashare_lab.mvp.strategy import moving_average_signals  # noqa: E402


def bar(day: str, price: str = "10", volume: str = "100000") -> dict[str, str]:
    return {
        "trade_date": day, "open": price, "high": price, "low": price, "close": price,
        "volume": volume, "total_return_index": price,
    }


def signal(day: str, target: int) -> dict[str, str]:
    return {"signal_date": day, "target_position": str(target)}


class StrategyTests(unittest.TestCase):
    def test_signal_uses_current_close_and_frozen_window(self):
        rows = [bar("2024-01-01", "1"), bar("2024-01-02", "2"), bar("2024-01-03", "3")]
        signals = moving_average_signals(rows, 2, first_signal_date="2024-01-01")
        self.assertEqual([row["target_position"] for row in signals], ["1", "1"])
        self.assertEqual(signals[0]["signal_date"], "2024-01-02")


class EngineTests(unittest.TestCase):
    def test_signal_executes_only_on_next_trading_day_and_in_lots(self):
        rows = [bar("2024-01-02"), bar("2024-01-03"), bar("2024-01-04")]
        result = run_engine(
            rows, [signal("2024-01-02", 1)], [],
            initial_cash=Decimal("1005"), costs=CostConfig(slippage_bps=Decimal("0")),
        )
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0]["signal_date"], "2024-01-02")
        self.assertEqual(result.trades[0]["trade_date"], "2024-01-03")
        self.assertEqual(result.trades[0]["quantity"], "100")
        self.assertEqual(result.ledger[1]["cash"], "0.00")

    def test_slippage_price_uses_explicit_half_up_rounding(self):
        rows = [bar("2024-01-02", "2.609"), bar("2024-01-03", "2.609")]
        result = run_engine(
            rows, [signal("2024-01-02", 1)], [],
            initial_cash=Decimal("1000"),
            costs=CostConfig(minimum_commission=Decimal("0")),
        )
        self.assertEqual(result.trades[0]["execution_price"], "2.610305")

    def test_zero_volume_rejects_order_with_reason(self):
        rows = [bar("2024-01-02"), bar("2024-01-03", volume="0")]
        result = run_engine(rows, [signal("2024-01-02", 1)], [], costs=CostConfig.zero())
        self.assertEqual(result.orders[0]["status"], "REJECTED")
        self.assertEqual(result.orders[0]["reason"], "ZERO_VOLUME_OR_SUSPENDED")
        self.assertEqual(result.trades, [])

    def test_dividend_entitlement_becomes_receivable_then_cash(self):
        rows = [
            bar("2024-01-16"), bar("2024-01-17"), bar("2024-01-18", "9"),
            bar("2024-01-19", "9"), bar("2024-01-23", "9"),
        ]
        event = DividendEvent("2024-01-17", "2024-01-18", "2024-01-23", Decimal("1"))
        result = run_engine(
            rows, [signal("2024-01-16", 1)], [event],
            initial_cash=Decimal("1000"), costs=CostConfig.zero(),
        )
        self.assertEqual(result.ledger[2]["dividend_receivable"], "100.00")
        self.assertEqual(result.ledger[-1]["dividend_receivable"], "0.00")
        self.assertEqual(result.ledger[-1]["cash"], "100.00")
        self.assertEqual([row["event_type"] for row in result.dividend_ledger], ["ENTITLEMENT", "PAYMENT"])

    def test_every_ledger_row_satisfies_asset_identity(self):
        rows = [bar("2024-01-02"), bar("2024-01-03", "11"), bar("2024-01-04", "12")]
        result = run_engine(rows, [signal("2024-01-02", 1), signal("2024-01-03", 0)], [], costs=CostConfig.zero())
        for row in result.ledger:
            self.assertEqual(
                Decimal(row["cash"]) + Decimal(row["dividend_receivable"]) + Decimal(row["market_value"]),
                Decimal(row["equity"]),
            )
        self.assertGreater(result.trades[1]["trade_date"], result.trades[1]["signal_date"])

    def test_period_metrics_use_prior_day_as_baseline(self):
        rows = [bar("2023-12-29", "10"), bar("2024-01-02", "11"), bar("2024-01-03", "12")]
        result = run_engine(rows, [], [], initial_cash=Decimal("1000"), costs=CostConfig.zero())
        metrics = calculate_period_metrics(result.ledger, result.trades, "2024-01-01", "2024-12-31")
        self.assertEqual(metrics["start_equity"], 1000.0)
        self.assertEqual(metrics["total_return"], 0.0)


if __name__ == "__main__":
    unittest.main()
