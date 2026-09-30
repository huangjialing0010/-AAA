from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.mvp_data import MvpDataError, build_total_return_rows  # noqa: E402


def market(day: str, close: str, *, source: str = "yahoo") -> dict[str, str]:
    return {
        "date": day, "open": close, "high": close, "low": close, "close": close,
        "volume": "1000", "row_source": source,
    }


class MvpDataTests(unittest.TestCase):
    def test_total_return_index_adds_cash_on_ex_date(self):
        rows = [market("2024-01-17", "10"), market("2024-01-18", "9"), market("2024-01-23", "9.5")]
        dividends = [{
            "record_date": "2024-01-17", "ex_date": "2024-01-18", "payment_date": "2024-01-23",
            "cash_per_share": "1", "cumulative_cash": "1",
        }]
        output = build_total_return_rows(rows, dividends)
        self.assertEqual(Decimal(output[1]["total_return_index"]), Decimal("1000"))
        self.assertEqual(output[1]["dividend_ex_cash_per_share"], "1.000000")
        self.assertGreater(Decimal(output[2]["total_return_index"]), Decimal("1000"))

    def test_dividend_dates_must_exist_in_market(self):
        with self.assertRaises(MvpDataError):
            build_total_return_rows([market("2024-01-18", "10")], [{
                "record_date": "2024-01-17", "ex_date": "2024-01-18", "payment_date": "2024-01-23",
                "cash_per_share": "1", "cumulative_cash": "1",
            }])

    def test_duplicate_market_date_is_rejected(self):
        with self.assertRaises(MvpDataError):
            build_total_return_rows([market("2024-01-18", "10"), market("2024-01-18", "10")], [])


if __name__ == "__main__":
    unittest.main()
