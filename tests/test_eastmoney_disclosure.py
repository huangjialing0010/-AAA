from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.sources.eastmoney_disclosure import (  # noqa: E402
    EastmoneyDisclosureError,
    EastmoneyDisclosureSource,
    normalize_api_date,
)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


class FakeOpener:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request.full_url, timeout))
        payload = self.payloads.pop(0)
        return FakeResponse(json.dumps(payload).encode("utf-8"))


def payload(page, pages, code):
    return {
        "success": True,
        "result": {
            "page": page,
            "pages": pages,
            "data": [{
                "SECURITY_CODE": code,
                "SECURITY_TYPE_CODE": "058001001",
                "REPORTDATE": "2025-12-31 00:00:00",
                "UPDATE_DATE": "2026-03-21 00:00:00",
            }],
        },
    }


class EastmoneyDisclosureTests(unittest.TestCase):
    def test_date_normalization(self):
        self.assertEqual(normalize_api_date("2026-03-21 00:00:00"), "2026-03-21")
        self.assertEqual(normalize_api_date(""), "")

    def test_pagination_and_required_fields(self):
        opener = FakeOpener([payload(1, 2, "000001"), payload(2, 2, "600000")])
        result = EastmoneyDisclosureSource(opener=opener).period("2025-12-31")
        self.assertEqual(result.pages, 2)
        self.assertEqual([row["SECURITY_CODE"] for row in result.rows], ["000001", "600000"])
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(result.source_rows, 2)
        self.assertEqual(result.security_type_counts, {"058001001": 2})

    def test_api_failure_is_not_empty_data(self):
        opener = FakeOpener([{"success": False, "message": "blocked", "result": None}])
        with self.assertRaises(EastmoneyDisclosureError):
            EastmoneyDisclosureSource(opener=opener).period("2025-12-31")

    def test_report_period_mismatch_is_rejected(self):
        bad = payload(1, 1, "000001")
        bad["result"]["data"][0]["REPORTDATE"] = "2025-09-30 00:00:00"
        with self.assertRaises(EastmoneyDisclosureError):
            EastmoneyDisclosureSource(opener=FakeOpener([bad])).period("2025-12-31")

    def test_non_a_share_security_type_is_filtered_and_counted(self):
        first = payload(1, 2, "000001")
        second = payload(2, 2, "900901")
        second["result"]["data"][0]["SECURITY_TYPE_CODE"] = "058001002"
        result = EastmoneyDisclosureSource(opener=FakeOpener([first, second])).period("2025-12-31")
        self.assertEqual([row["SECURITY_CODE"] for row in result.rows], ["000001"])
        self.assertEqual(result.source_rows, 2)
        self.assertEqual(result.security_type_counts, {"058001001": 1, "058001002": 1})


if __name__ == "__main__":
    unittest.main()
