from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.selection.metrics import calculate_index_metrics, calculate_research_metrics  # noqa: E402


class SelectionMetricsTests(unittest.TestCase):
    def test_metrics_include_slippage_and_research_scope(self):
        ledger = [
            {"trade_date": "2024-01-01", "equity": "100.00"},
            {"trade_date": "2024-01-02", "equity": "110.00"},
            {"trade_date": "2024-01-03", "equity": "99.00"},
        ]
        trades = [{
            "trade_date": "2024-01-02", "gross_notional": "50", "commission": "1",
            "stamp_tax": "0.5", "slippage_cost": "0.25",
        }]
        metrics = calculate_research_metrics(ledger, trades, "2024-01-02", "2024-01-03")
        self.assertEqual(metrics["total_costs"], 1.75)
        self.assertAlmostEqual(metrics["max_drawdown"], -0.1)
        self.assertEqual(metrics["accounting_scope"], "RESEARCH_PORTFOLIO")

    def test_index_metrics_use_prior_day_as_baseline(self):
        rows = [
            {"trade_date": "2024-01-01", "total_return_index": "100.00"},
            {"trade_date": "2024-01-02", "total_return_index": "110.00"},
            {"trade_date": "2024-01-03", "total_return_index": "99.00"},
        ]

        metrics = calculate_index_metrics(rows, "2024-01-02", "2024-01-03")

        self.assertAlmostEqual(metrics["total_return"], -0.01)
        self.assertAlmostEqual(metrics["max_drawdown"], -0.1)
        self.assertEqual(metrics["accounting_scope"], "BENCHMARK_TOTAL_RETURN_INDEX")


if __name__ == "__main__":
    unittest.main()
