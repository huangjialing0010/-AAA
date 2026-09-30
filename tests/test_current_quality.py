from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.selection.current_quality import current_quality_profile  # noqa: E402


def annual(period: str, profit: str = "100", ocf: str = "1.2", roe: str = "0.15", eps: str = "1") -> dict[str, str]:
    return {
        "report_period": period,
        "available_date_assumed": str(int(period[:4]) + 1) + "-04-30",
        "net_profit": profit,
        "operating_cashflow_per_share": ocf,
        "roe": roe,
        "basic_eps": eps,
    }


class CurrentQualityTests(unittest.TestCase):
    def test_complete_positive_profile_is_eligible(self):
        rows = [annual(f"{year}-12-31") for year in (2023, 2024, 2025)]
        result = current_quality_profile(rows, as_of="2026-09-02", h1_parent_netprofit="50")
        self.assertTrue(result["eligible"])
        self.assertAlmostEqual(result["roe_median"], 0.15)
        self.assertAlmostEqual(result["cash_profit_per_share_proxy"], 1.2)

    def test_proxy_below_threshold_does_not_reject_hard_quality(self):
        rows = [annual(f"{year}-12-31", ocf="0.2") for year in (2023, 2024, 2025)]
        result = current_quality_profile(rows, as_of="2026-09-02", h1_parent_netprofit="50")
        self.assertTrue(result["eligible"])
        self.assertFalse(result["cash_profit_proxy_ge_0_8"])

    def test_missing_or_losing_period_is_rejected(self):
        rows = [annual("2023-12-31"), annual("2024-12-31", profit="-1")]
        result = current_quality_profile(rows, as_of="2026-09-02", h1_parent_netprofit="")
        self.assertFalse(result["eligible"])
        self.assertIn("net_profit", result["reasons"])
        self.assertIn("h1_parent_netprofit", result["reasons"])

    def test_future_available_row_is_not_used(self):
        rows = [annual(f"{year}-12-31") for year in (2023, 2024, 2025)]
        rows[-1]["available_date_assumed"] = "2026-10-01"
        result = current_quality_profile(rows, as_of="2026-09-02", h1_parent_netprofit="50")
        self.assertFalse(result["eligible"])
        self.assertIn("net_profit", result["reasons"])


if __name__ == "__main__":
    unittest.main()
