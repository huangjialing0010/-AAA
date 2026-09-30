from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.sources.public_etf_sources import (  # noqa: E402
    PublicEtfSourceError,
    combine_dividends,
    parse_sina_daily,
    parse_tencent_unadjusted,
)


class PublicEtfSourceTests(unittest.TestCase):
    def test_tencent_unadjusted_maps_fields_and_converts_lots_to_shares(self):
        payload = {"code": 0, "data": {"sh510300": {"day": [
            ["2019-04-29", "3.888", "3.894", "3.945", "3.871", "5593755.000"]
        ]}}}
        row = parse_tencent_unadjusted(json.dumps(payload).encode())[0]
        self.assertEqual(row["open"], "3.888000")
        self.assertEqual(row["close"], "3.894000")
        self.assertEqual(row["volume"], "559375500")
        self.assertEqual(row["row_source"], "tencent_unadjusted")

    def test_tencent_rejects_non_integral_share_conversion(self):
        payload = {"code": 0, "data": {"sh510300": {"day": [
            ["2019-04-29", "3", "3", "3", "3", "1.234"]
        ]}}}
        with self.assertRaises(PublicEtfSourceError):
            parse_tencent_unadjusted(json.dumps(payload).encode())

    def test_sina_daily_is_parsed_without_eval(self):
        raw = (
            'var _data=([{"day":"2026-09-01","open":"4.7","high":"4.8",'
            '"low":"4.6","close":"4.7","volume":"100"}]);'
        ).encode()
        self.assertEqual(parse_sina_daily(raw)[0]["close"], "4.700000")

    def test_dividend_sources_require_unique_match(self):
        factors = [{"ex_date": "2026-01-19", "cash_per_share": "0.123", "cumulative_cash": "0.880"}]
        table = [{"record_date": "2026-01-16", "payment_date": "2026-01-27", "cash_per_share": "0.123"}]
        self.assertEqual(combine_dividends(factors, table)[0]["record_date"], "2026-01-16")


if __name__ == "__main__":
    unittest.main()
