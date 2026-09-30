from __future__ import annotations

import csv
import hashlib
import json
import sys
import unittest
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.canonical_data import (  # noqa: E402
    DAILY_COLUMNS,
    FINANCIAL_COLUMNS,
    assumed_available_date,
    parse_number,
)


CANONICAL = PROJECT_ROOT / "data" / "canonical"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: Path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


class ParsingTests(unittest.TestCase):
    def test_number_units_percent_and_missing(self):
        self.assertEqual(parse_number("4302.00万"), "43020000")
        self.assertEqual(parse_number("1.25亿"), "125000000")
        self.assertEqual(parse_number("29.86%"), "0.2986")
        self.assertEqual(parse_number("False"), "")
        self.assertEqual(parse_number(""), "")

    def test_assumed_availability_dates_are_after_period(self):
        expected = {
            "2025-03-31": "2025-05-15",
            "2025-06-30": "2025-08-31",
            "2025-09-30": "2025-11-15",
            "2025-12-31": "2026-04-30",
        }
        for period, available in expected.items():
            actual, _ = assumed_available_date(period)
            self.assertEqual(actual, available)
            self.assertGreater(date.fromisoformat(actual), date.fromisoformat(period))


class CanonicalOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manifest_path = CANONICAL / "manifest.json"
        if not manifest_path.is_file():
            raise unittest.SkipTest("请先运行 tools/build_canonical_data.py")
        cls.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    def test_expected_partition_counts(self):
        self.assertEqual(len(list((CANONICAL / "daily_prices").glob("*.csv"))), 300)
        self.assertEqual(len(list((CANONICAL / "financial_reports").glob("*.csv"))), 574)
        self.assertEqual(len(list((CANONICAL / "industry_index").glob("*.csv"))), 31)

    def test_daily_schema_and_invariants(self):
        total_rows = 0
        for path in (CANONICAL / "daily_prices").glob("*.csv"):
            seen = set()
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, DAILY_COLUMNS)
                for row in reader:
                    total_rows += 1
                    self.assertNotIn(row["trade_date"], seen)
                    seen.add(row["trade_date"])
                    self.assertEqual(row["stock_code"], path.stem)
                    self.assertEqual(row["adjustment"], "qfq")
                    open_, high, low, close = map(float, (row["open"], row["high"], row["low"], row["close"]))
                    self.assertGreater(min(open_, high, low, close), 0)
                    self.assertGreaterEqual(high, max(open_, close, low))
                    self.assertLessEqual(low, min(open_, close, high))
        self.assertEqual(total_rows, 949_431)

    def test_financial_schema_missing_and_time_semantics(self):
        total_rows = 0
        for path in (CANONICAL / "financial_reports").glob("*.csv"):
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, FINANCIAL_COLUMNS)
                for row in reader:
                    total_rows += 1
                    self.assertEqual(row["stock_code"], path.stem)
                    self.assertGreater(date.fromisoformat(row["available_date_assumed"]), date.fromisoformat(row["report_period"]))
                    self.assertNotIn("False", row.values())
        self.assertEqual(total_rows, 53_411)

    def test_current_snapshots_are_not_historical(self):
        universe = list(read_rows(CANONICAL / "reference" / "current_universe.csv"))
        industry = list(read_rows(CANONICAL / "reference" / "current_industry.csv"))
        self.assertEqual(len(universe), 300)
        self.assertTrue(all(row["historical_use_allowed"] == "false" for row in universe))
        self.assertTrue(all(row["historical_use_allowed"] == "false" for row in industry))
        universe_codes = {row["stock_code"] for row in universe}
        industry_codes = {row["stock_code"] for row in industry}
        daily_codes = {path.stem for path in (CANONICAL / "daily_prices").glob("*.csv")}
        self.assertFalse(universe_codes - industry_codes)
        self.assertFalse(universe_codes - daily_codes)

    def test_manifest_hashes_and_counts(self):
        entries = self.manifest["output"]["entries"]
        self.assertEqual(self.manifest["output"]["files"], 910)
        self.assertEqual(len(entries), 910)
        for entry in entries:
            path = CANONICAL / entry["path"]
            self.assertTrue(path.is_file(), entry["path"])
            self.assertEqual(path.stat().st_size, entry["bytes"], entry["path"])
            self.assertEqual(sha256_file(path), entry["sha256"], entry["path"])

    def test_quality_report_preserves_known_limits(self):
        report = json.loads((PROJECT_ROOT / "reports" / "canonical_data_quality.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "PASS_WITH_KNOWN_LIMITS")
        self.assertEqual(report["checks"]["benchmark_000300"]["zero_volume_rows"], 721)
        self.assertGreater(len(report["known_limits"]), 0)


if __name__ == "__main__":
    unittest.main()
