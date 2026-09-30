from __future__ import annotations

import importlib.util
import unittest
from datetime import date, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = PROJECT_ROOT / "tools" / "fetch_baostock_historical_daily.py"
SPEC = importlib.util.spec_from_file_location("fetch_baostock_historical_daily", TOOL_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def result(rows):
    fields = [
        "date", "code", "open", "high", "low", "close", "preclose", "volume", "amount",
        "adjustflag", "turn", "tradestatus", "pctChg", "isST",
    ]
    return MODULE.QueryResult(fields=fields, rows=rows)


def task(adjustflag="3"):
    return {
        "stock_code": "000001",
        "adjustflag": adjustflag,
        "category": "daily_unadjusted" if adjustflag == "3" else "daily_qfq",
        "segment_number": "1",
        "start_date": "2010-01-01",
        "end_date": "2015-12-31",
        "relative_path": "sample.csv",
        "metadata_path": "sample.json",
    }


class HistoricalDailyToolTests(unittest.TestCase):
    def test_task_count_is_codes_times_windows_times_adjustments(self):
        tasks = MODULE.build_tasks(["000001", "600000"], MODULE.build_windows("2026-09-02"))
        self.assertEqual(len(tasks), 12)
        self.assertEqual(len({item["relative_path"] for item in tasks}), 12)

    def test_302132_qfq_uses_previous_code_and_splits_change_window(self):
        tasks = MODULE.build_tasks(["302132"], MODULE.build_windows("2026-09-02"))
        self.assertEqual(len(tasks), 7)
        qfq = [item for item in tasks if item["category"] == "daily_qfq"]
        self.assertEqual(
            [(item["query_stock_code"], item["start_date"], item["end_date"]) for item in qfq],
            [
                ("300114", "2010-01-01", "2015-12-31"),
                ("300114", "2016-01-01", "2021-12-31"),
                ("300114", "2022-01-01", "2025-02-14"),
                ("302132", "2025-02-17", "2026-09-02"),
            ],
        )
        self.assertEqual(len({item["relative_path"] for item in tasks}), 7)

    def test_empty_segment_preserves_schema_and_is_allowed(self):
        summary = MODULE.validate_result(result([]), task())
        self.assertEqual(summary, {"rows": 0, "min_date": None, "max_date": None})

    def test_wrong_adjustment_is_rejected(self):
        row = {field: "" for field in result([]).fields}
        row.update({"date": "2010-01-04", "code": "sz.000001", "adjustflag": "2"})
        with self.assertRaises(RuntimeError):
            MODULE.validate_result(result([row]), task("3"))

    def test_lineage_task_accepts_previous_response_code_only(self):
        lineage_task = task("2")
        lineage_task.update({
            "stock_code": "302132",
            "query_stock_code": "300114",
            "response_stock_code": "300114",
        })
        row = {field: "" for field in result([]).fields}
        row.update({"date": "2010-01-04", "code": "sz.300114", "adjustflag": "2"})
        self.assertEqual(
            MODULE.validate_result(result([row]), lineage_task)["rows"],
            1,
        )
        row["code"] = "sz.302132"
        with self.assertRaises(RuntimeError):
            MODULE.validate_result(result([row]), lineage_task)

    def test_two_thousand_rows_are_rejected_before_pagination_can_hide_data(self):
        row = {field: "" for field in result([]).fields}
        row.update({"date": "2010-01-04", "code": "sz.000001", "adjustflag": "3"})
        rows = []
        for index in range(2000):
            copy = dict(row)
            copy["date"] = (date(2010, 1, 1) + timedelta(days=index)).isoformat()
            rows.append(copy)
        with self.assertRaises(RuntimeError):
            MODULE.validate_result(result(rows), task())


if __name__ == "__main__":
    unittest.main()
