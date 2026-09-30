from __future__ import annotations

import importlib.util
import unittest
from datetime import date, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = PROJECT_ROOT / "tools" / "fetch_baostock_daily_snapshot.py"
SPEC = importlib.util.spec_from_file_location("fetch_baostock_daily_snapshot", TOOL_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
QueryResult = MODULE.QueryResult


def make_row(day: str, adjustflag: str = "3") -> dict[str, str]:
    return {
        "date": day,
        "code": "sh.600196",
        "open": "10", "high": "11", "low": "9", "close": "10",
        "preclose": "10", "volume": "1", "amount": "10",
        "adjustflag": adjustflag, "turn": "0.1", "tradestatus": "1",
        "pctChg": "0", "isST": "0",
    }


class DailySnapshotValidationTests(unittest.TestCase):
    def test_exact_page_multiple_ending_early_is_rejected(self):
        start = date(2000, 1, 1)
        rows = [make_row((start + timedelta(days=index)).isoformat()) for index in range(4000)]
        result = QueryResult(fields=list(rows[0]), rows=rows)
        with self.assertRaisesRegex(RuntimeError, "疑似分页截断"):
            MODULE.validate_result(result, "600196", "3", "2000-01-01", "2026-09-01")

    def test_short_complete_window_is_accepted(self):
        rows = [make_row("2026-09-01"), make_row("2026-09-02")]
        result = QueryResult(fields=list(rows[0]), rows=rows)
        summary = MODULE.validate_result(result, "600196", "3", "2026-09-01", "2026-09-03")
        self.assertEqual(summary["max_date"], "2026-09-02")


if __name__ == "__main__":
    unittest.main()
