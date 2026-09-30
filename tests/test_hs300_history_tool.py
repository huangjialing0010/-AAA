from __future__ import annotations

import importlib.util
import unittest
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = PROJECT_ROOT / "tools" / "fetch_baostock_hs300_history.py"
SPEC = importlib.util.spec_from_file_location("fetch_baostock_hs300_history", TOOL_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class MembershipScheduleTests(unittest.TestCase):
    def test_weekly_dates_use_first_project_trade_date(self):
        values = MODULE.weekly_query_dates(date(2006, 1, 31))
        parsed = [date.fromisoformat(value) for value in values]
        self.assertEqual(parsed[0], date(2006, 1, 4))
        weeks = [(value.isocalendar().year, value.isocalendar().week) for value in parsed]
        self.assertEqual(len(weeks), len(set(weeks)))
        self.assertEqual(len(parsed), 4)


if __name__ == "__main__":
    unittest.main()
