from __future__ import annotations

import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = PROJECT_ROOT / "tools" / "validate_baostock_historical_daily.py"
SPEC = importlib.util.spec_from_file_location("validate_baostock_historical_daily", TOOL_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class HistoricalDailyValidatorTests(unittest.TestCase):
    def test_bridge_preserves_actual_boundary_return_without_fake_jump(self):
        raw={"2025-02-14":{"close":"72.18"},"2025-02-17":{"preclose":"72.18"}}
        qfq={"2025-02-14":{"close":"72.18"},"2025-02-17":{"preclose":"71.1886077"}}
        errors=[]
        MODULE.validate_lineage_boundary(raw,qfq,errors,Decimal("0.986265"))
        self.assertEqual(errors,[])
        MODULE.validate_lineage_boundary(raw,qfq,errors,Decimal("1"))
        self.assertTrue(errors)

    def test_real_raw_price_break_cannot_be_hidden_by_bridge(self):
        raw={"2025-02-14":{"close":"72.18"},"2025-02-17":{"preclose":"10"}}
        qfq={"2025-02-14":{"close":"72.18"},"2025-02-17":{"preclose":"71.1886077"}}
        errors=[]
        MODULE.validate_lineage_boundary(raw,qfq,errors,Decimal("0.986265"))
        self.assertTrue(errors)

    def test_expected_paths_split_only_302132_qfq_crossing_window(self):
        paths = MODULE.expected_paths(
            ["000001", "302132"],
            [("2022-01-01", "2026-09-02")],
        )
        self.assertEqual(len(paths), 5)
        self.assertNotIn("daily_qfq/302132/2022-01-01_2026-09-02.csv", paths)
        self.assertIn("daily_qfq/302132/2022-01-01_2025-02-14.csv", paths)
        self.assertIn("daily_qfq/302132/2025-02-17_2026-09-02.csv", paths)

    def test_query_code_is_independently_derived_from_period(self):
        before = {
            "stock_code": "302132",
            "category": "daily_qfq",
            "start_date": "2016-01-01",
            "end_date": "2021-12-31",
        }
        after = dict(before, start_date="2025-02-17", end_date="2026-09-02")
        crossing = dict(before, start_date="2022-01-01", end_date="2026-09-02")
        self.assertEqual(MODULE.expected_query_code(before), ("300114", "PREVIOUS_CODE"))
        self.assertEqual(MODULE.expected_query_code(after), ("302132", "EFFECTIVE_CODE"))
        self.assertEqual(MODULE.expected_query_code(crossing), ("", "INVALID_CROSS_BOUNDARY"))


if __name__ == "__main__":
    unittest.main()
