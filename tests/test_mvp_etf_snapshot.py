from __future__ import annotations

import importlib.util
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = PROJECT_ROOT / "tools" / "fetch_mvp_etf_snapshot.py"
SPEC = importlib.util.spec_from_file_location("fetch_mvp_etf_snapshot", TOOL_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class MvpEtfSnapshotTests(unittest.TestCase):
    def test_yahoo_parser_filters_to_frozen_complete_range(self):
        stamps = [
            int(datetime(2012, 5, 28, tzinfo=timezone.utc).timestamp()),
            int(datetime(2012, 5, 29, tzinfo=timezone.utc).timestamp()),
            int(datetime(2012, 5, 30, tzinfo=timezone.utc).timestamp()),
        ]
        payload = {
            "chart": {
                "error": None,
                "result": [{
                    "meta": {"symbol": "510300.SS"},
                    "timestamp": stamps,
                    "indicators": {
                        "quote": [{
                            "open": [2.0, 2.1, None],
                            "high": [2.1, 2.2, None],
                            "low": [1.9, 2.0, None],
                            "close": [2.0, 2.15, None],
                            "volume": [100, 200, 50],
                        }],
                        "adjclose": [{"adjclose": [1.5, 1.6, None]}],
                    },
                    "events": {
                        "dividends": {str(stamps[1]): {"date": stamps[1], "amount": 0.048}},
                    },
                }],
            }
        }
        rows, dividends, splits, missing = MODULE.parse_yahoo_chart(
            json.dumps(payload).encode(), "2012-05-28", "2012-05-29"
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["close"], "2.150000")
        self.assertEqual(dividends, [{"date": "2012-05-29", "amount": "0.048000"}])
        self.assertEqual(splits, [])
        self.assertEqual(missing, [])

    def test_yahoo_parser_reports_incomplete_day_inside_frozen_range(self):
        stamps = [
            int(datetime(2012, 5, day, tzinfo=timezone.utc).timestamp())
            for day in (28, 29, 30)
        ]
        payload = {
            "chart": {"error": None, "result": [{
                "meta": {"symbol": "510300.SS"},
                "timestamp": stamps,
                "indicators": {
                    "quote": [{
                        "open": [2, None, 2], "high": [2, None, 2], "low": [2, None, 2],
                        "close": [2, None, 2], "volume": [1, None, 1],
                    }],
                    "adjclose": [{"adjclose": [2, None, 2]}],
                },
            }]}
        }
        rows, _, _, missing = MODULE.parse_yahoo_chart(
            json.dumps(payload).encode(), "2012-05-28", "2012-05-30"
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(missing, ["2012-05-29"])

    def test_sina_daily_parser_is_safe_jsonp_parser(self):
        raw = (
            "/* guard */\nvar _data=(["
            '{"day":"2025-10-24","open":"4.728","high":"4.772",'
            '"low":"4.727","close":"4.770","volume":"865303618"}'
            "]);"
        ).encode()
        rows = MODULE.parse_sina_daily(raw)
        self.assertEqual(rows[0]["date"], "2025-10-24")
        self.assertEqual(rows[0]["close"], "4.770000")

    def test_cross_source_anomalies_are_explicit(self):
        yahoo = [{
            "date": "2024-01-15", "open": "3.349", "high": "3.349",
            "low": "3.349", "close": "3.349", "volume": "0",
        }]
        sina = [{
            "date": "2024-01-15", "open": "3.330", "high": "3.375",
            "low": "3.324", "close": "3.345", "volume": "905570620",
        }]
        self.assertEqual(MODULE.cross_source_anomalies(yahoo, sina), ["2024-01-15"])

    def test_tencent_qfq_parser_preserves_field_order_and_lot_volume(self):
        payload = {"code": 0, "data": {"sh510300": {"qfqday": [
            ["2024-01-15", "3.050", "3.065", "3.095", "3.044", "9055706.000"]
        ]}}}
        row = MODULE.parse_tencent_qfq(json.dumps(payload).encode())[0]
        self.assertEqual(row["close"], "3.065000")
        self.assertEqual(row["high"], "3.095000")
        self.assertEqual(row["volume_lots"], "9055706.000")

    def test_factor_parser_converts_cumulative_cash_to_event_cash(self):
        raw = (
            b'var KKE_ShareAmount_sh510300={"data":['
            b'{"d":"1900-01-01","f":"1","u":"0","s":"1"},'
            b'{"d":"2012-12-18","f":"1","u":"0.033","s":"1"},'
            b'{"d":"2014-01-21","f":"1","u":"0.081","s":"1"}'
            b']} /* test */'
        )
        rows = MODULE.parse_factor_events(raw)
        self.assertEqual([row["cash_per_share"] for row in rows], ["0.033", "0.048"])
        self.assertEqual(rows[-1]["cumulative_cash"], "0.081")

    def test_dividend_table_parser_extracts_dates_and_cash(self):
        content = """
        <h3>历史分红</h3><table>
          <tr><th>登记日</th><th>发放日</th><th>金额</th></tr>
          <tr><td>2012/12/17</td><td>2012/12/24</td><td>0.033</td></tr>
          <tr><td>2014/1/20</td><td>2014/1/27</td><td>0.048</td></tr>
        </table>
        """
        original_minimum = MODULE.MIN_DIVIDEND_TABLE_ROWS
        MODULE.MIN_DIVIDEND_TABLE_ROWS = 2
        try:
            rows = MODULE.parse_dividend_table(content.encode("gb18030"))
        finally:
            MODULE.MIN_DIVIDEND_TABLE_ROWS = original_minimum
        self.assertEqual(rows[0]["record_date"], "2012-12-17")
        self.assertEqual(rows[1]["payment_date"], "2014-01-27")

    def test_combine_requires_unique_amount_and_date_match(self):
        factors = [{
            "ex_date": "2012-12-18",
            "cash_per_share": "0.033",
            "cumulative_cash": "0.033",
        }]
        table = [{
            "record_date": "2012-12-17",
            "payment_date": "2012-12-24",
            "cash_per_share": "0.033",
        }]
        self.assertEqual(
            MODULE.combine_dividends(factors, table)[0]["payment_date"],
            "2012-12-24",
        )

    def test_two_thousand_daily_rows_are_rejected(self):
        fields = sorted(MODULE.DAILY_FIELDS)
        row = {field: "" for field in fields}
        row.update({"date": "2012-05-28", "code": "sh.510300", "adjustflag": "3"})
        rows = [dict(row) for _ in range(2000)]
        result = MODULE.QueryResult(fields=fields, rows=rows)
        with self.assertRaises(RuntimeError):
            MODULE.validate_daily(result, "3", "2012-05-28", "2017-12-31")


if __name__ == "__main__":
    unittest.main()
