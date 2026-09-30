from __future__ import annotations

import gzip
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.fetch_watchlist_official_reports import decode_payload  # noqa: E402


class OfficialReportFetchTests(unittest.TestCase):
    def test_plain_pdf_is_unchanged(self):
        payload = b"%PDF-1.7 test"
        self.assertEqual(decode_payload(payload), payload)

    def test_gzip_wrapped_pdf_is_decompressed(self):
        payload = b"%PDF-1.7 test"
        self.assertEqual(decode_payload(gzip.compress(payload)), payload)


if __name__ == "__main__":
    unittest.main()
