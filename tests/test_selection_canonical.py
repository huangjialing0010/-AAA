from __future__ import annotations

import csv
import unittest
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.selection.canonical import merge_code_entries, merge_dual_price_rows, rows_from_manifest_entry  # noqa: E402


def row(date: str, adjustflag: str, close: str, *, volume: str = "100") -> dict[str, str]:
    return {
        "date": date,
        "code": "sz.000001",
        "open": "10",
        "high": "11",
        "low": "9",
        "close": close,
        "preclose": "9.5",
        "volume": volume,
        "amount": "1000",
        "adjustflag": adjustflag,
        "turn": "1",
        "tradestatus": "1",
        "pctChg": "5",
        "isST": "0",
    }


class SelectionCanonicalTests(unittest.TestCase):
    def test_merges_execution_fields_with_qfq_close(self):
        merged = merge_dual_price_rows(
            [row("2024-01-02", "3", "10")],
            [row("2024-01-02", "2", "8")],
            expected_code="000001",
        )
        self.assertEqual(merged[0]["close"], "10")
        self.assertEqual(merged[0]["close_qfq"], "8")
        self.assertEqual(merged[0]["open_qfq"], "10")
        self.assertEqual(merged[0]["code"], "000001")

    def test_rejects_different_date_sets(self):
        with self.assertRaises(ValueError):
            merge_dual_price_rows(
                [row("2024-01-02", "3", "10")],
                [row("2024-01-03", "2", "8")],
                expected_code="000001",
            )

    def test_rejects_cross_adjustment_market_mismatch(self):
        with self.assertRaises(ValueError):
            merge_dual_price_rows(
                [row("2024-01-02", "3", "10", volume="100")],
                [row("2024-01-02", "2", "8", volume="101")],
                expected_code="000001",
            )

    def test_manifest_entries_map_previous_code_only_in_standard_layer(self):
        with TemporaryDirectory() as temporary:
            snapshot = Path(temporary)
            raw_row = row("2025-02-14", "3", "72.18")
            raw_row["code"] = "sz.302132"
            qfq_row = row("2025-02-14", "2", "72.18")
            qfq_row["code"] = "sz.300114"
            entries = []
            for category, source_row, response_code in (
                ("daily_unadjusted", raw_row, "302132"),
                ("daily_qfq", qfq_row, "300114"),
            ):
                relative = f"{category}/302132/2025-02-14_2025-02-14.csv"
                path = snapshot / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(source_row))
                    writer.writeheader()
                    writer.writerow(source_row)
                entries.append({
                    "stock_code": "302132",
                    "response_stock_code": response_code,
                    "category": category,
                    "adjustflag": source_row["adjustflag"],
                    "start_date": "2025-02-14",
                    "end_date": "2025-02-14",
                    "relative_path": relative,
                })

            normalized = rows_from_manifest_entry(snapshot, entries[-1], effective_code="302132")
            self.assertEqual(normalized[0]["code"], "302132")
            self.assertEqual(normalized[0]["_source_code"], "300114")
            with self.assertRaisesRegex(ValueError, "证据"):
                merge_code_entries(snapshot, "302132", entries)
            self.assertEqual(qfq_row["code"], "sz.300114")


if __name__ == "__main__":
    unittest.main()
