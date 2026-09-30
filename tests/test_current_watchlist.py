from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.selection.current_watchlist import evaluate_candidate, select_watchlist  # noqa: E402


def make_rows(closes: list[float], *, pe: float = 10.0, pb: float = 1.0):
    start = date(2024, 1, 1)
    rows = []
    for index, close in enumerate(closes):
        rows.append({
            "date": (start + timedelta(days=index)).isoformat(),
            "close": str(close),
            "tradestatus": "1",
            "isST": "0",
            "peTTM": str(pe),
            "pbMRQ": str(pb),
        })
    return rows


class CurrentWatchlistTests(unittest.TestCase):
    def test_l1_baseline_requires_valuation_and_drawdown(self):
        rows = make_rows([100.0] * 799 + [75.0])
        rows[-1]["peTTM"] = "5"
        rows[-1]["pbMRQ"] = "0.5"
        calendar = [row["date"] for row in rows]
        profile = evaluate_candidate(rows, calendar, calendar[-1])
        self.assertEqual(profile["status"], "BASELINE_TRIGGER")
        self.assertEqual(profile["mechanism"], "L1")

    def test_r1_baseline_requires_breakout_and_rising_average(self):
        closes = [90.0] * 680 + [90.0] * 60 + [100.0] * 59 + [110.0]
        rows = make_rows(closes)
        calendar = [row["date"] for row in rows]
        profile = evaluate_candidate(rows, calendar, calendar[-1])
        self.assertEqual(profile["status"], "BASELINE_TRIGGER")
        self.assertEqual(profile["mechanism"], "R1")

    def test_missing_common_calendar_day_is_review(self):
        rows = make_rows([100.0] * 799 + [75.0])
        calendar = [row["date"] for row in rows]
        del rows[-30]
        profile = evaluate_candidate(rows, calendar, calendar[-1])
        self.assertEqual(profile["status"], "REVIEW")
        self.assertIn("INCOMPLETE_COMMON_CALENDAR_PRICE_WINDOW", profile["review_reasons"])

    def test_selection_uses_quality_order_and_shared_industry_cap(self):
        candidates = [
            {"stock_code": "1", "status": "BASELINE_TRIGGER", "mechanism": "L1", "level1_industry": "A", "roe_3y_median": 0.30, "cash_profit_per_share_proxy": 1.0},
            {"stock_code": "2", "status": "BASELINE_TRIGGER", "mechanism": "R1", "level1_industry": "A", "roe_3y_median": 0.20, "cash_profit_per_share_proxy": 2.0},
            {"stock_code": "3", "status": "BASELINE_TRIGGER", "mechanism": "R1", "level1_industry": "B", "roe_3y_median": 0.10, "cash_profit_per_share_proxy": 1.0},
        ]
        selected = select_watchlist(candidates)
        self.assertEqual([row["stock_code"] for row in selected], ["1", "3"])
        self.assertTrue(all(row["watch_status"] == "FORWARD_WATCH_ONLY" for row in selected))


if __name__ == "__main__":
    unittest.main()
