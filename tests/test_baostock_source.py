from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.sources.baostock_source import (  # noqa: E402
    BaoStockError,
    BaoStockSource,
    QueryResult,
    RetryingBaoStockSource,
    market_code,
)


class FakeResponse:
    def __init__(self, fields, rows, error_code="0", error_msg="success"):
        self.fields = fields
        self._rows = rows
        self._index = -1
        self.error_code = error_code
        self.error_msg = error_msg

    def next(self):
        self._index += 1
        return self._index < len(self._rows)

    def get_row_data(self):
        return self._rows[self._index]


class FakeClient:
    def __init__(self):
        self.calls = []
        self.logged_out = False

    def login(self):
        return FakeResponse([], [])

    def logout(self):
        self.logged_out = True

    def query_history_k_data_plus(self, code, fields, **kwargs):
        self.calls.append(("daily", code, fields, kwargs))
        return FakeResponse(["date", "code"], [["2026-08-10", code]])

    def query_hs300_stocks(self, **kwargs):
        self.calls.append(("members", kwargs))
        return FakeResponse(["code", "code_name"], [["sh.600000", "浦发银行"]])

    def query_profit_data(self, **kwargs):
        self.calls.append(("profit", kwargs))
        return FakeResponse(
            ["code", "pubDate", "statDate"],
            [[kwargs["code"], "2026-04-30", "2025-12-31"]],
        )

    def query_stock_basic(self, **kwargs):
        self.calls.append(("stock_basic", kwargs))
        return FakeResponse(
            ["code", "code_name", "ipoDate", "outDate", "type", "status"],
            [[kwargs["code"], "沪深300ETF", "2012-05-28", "", "2", "1"]],
        )

    def query_dividend_data(self, **kwargs):
        self.calls.append(("dividend", kwargs))
        return FakeResponse(["code", "dividOperateDate"], [[kwargs["code"], "2025-01-01"]])

    def query_adjust_factor(self, **kwargs):
        self.calls.append(("adjust_factor", kwargs))
        return FakeResponse(["code", "dividOperateDate", "foreAdjustFactor"], [[kwargs["code"], "2025-01-01", "1"]])


class BaoStockSourceTests(unittest.TestCase):
    def test_market_code(self):
        self.assertEqual(market_code("000001"), "sz.000001")
        self.assertEqual(market_code("600000"), "sh.600000")
        self.assertEqual(market_code("688001"), "sh.688001")
        with self.assertRaises(ValueError):
            market_code("1")

    def test_context_login_logout_and_query_arguments(self):
        client = FakeClient()
        with BaoStockSource(client) as source:
            daily = source.daily_qfq("000001", "2026-08-08", "2026-09-01")
            members = source.hs300_members("2020-01-02")
            profit = source.profit("600000", 2025, 4)
            basic = source.stock_basic("510300")
            dividend = source.dividend("510300", 2025)
            factor = source.adjust_factor("510300", "2012-01-01", "2026-09-02")
        self.assertTrue(client.logged_out)
        self.assertEqual(daily.rows[0]["code"], "sz.000001")
        self.assertEqual(members.rows[0]["code"], "sh.600000")
        self.assertEqual(profit.rows[0]["pubDate"], "2026-04-30")
        self.assertEqual(basic.rows[0]["type"], "2")
        self.assertEqual(dividend.rows[0]["dividOperateDate"], "2025-01-01")
        self.assertEqual(factor.rows[0]["foreAdjustFactor"], "1")
        self.assertEqual(client.calls[0][3]["adjustflag"], "2")
        self.assertEqual(client.calls[1][1]["date"], "2020-01-02")

    def test_default_client_socket_timeout_is_applied_and_global_default_restored(self):
        source = BaoStockSource(socket_timeout_seconds=12.5)
        source._client = FakeClient()
        with patch(
            "ashare_lab.sources.baostock_source.socket.getdefaulttimeout", return_value=None
        ), patch("ashare_lab.sources.baostock_source.socket.setdefaulttimeout") as set_timeout:
            with source:
                pass
        self.assertEqual([call.args[0] for call in set_timeout.call_args_list], [12.5, None])

    def test_invalid_socket_timeout_is_rejected(self):
        with self.assertRaises(ValueError):
            BaoStockSource(socket_timeout_seconds=0)

    def test_unadjusted_daily_uses_adjustflag_three(self):
        client = FakeClient()
        with BaoStockSource(client) as source:
            source.daily("600000", "2010-01-01", "2026-09-01", adjustflag="3")
        self.assertEqual(client.calls[0][3]["adjustflag"], "3")

    def test_daily_valuation_requests_explicit_valuation_fields(self):
        client = FakeClient()
        with BaoStockSource(client) as source:
            source.daily_valuation("600000", "2023-01-01", "2026-09-28", adjustflag="3")
        _, code, fields, kwargs = client.calls[0]
        self.assertEqual(code, "sh.600000")
        self.assertIn("peTTM", fields)
        self.assertIn("pbMRQ", fields)
        self.assertEqual(kwargs["adjustflag"], "3")
        with self.assertRaises(ValueError):
            BaoStockSource(FakeClient()).daily_valuation("600000", "2023-01-01", "2026-09-28", adjustflag="1")

    def test_invalid_adjustflag_is_rejected(self):
        with self.assertRaises(ValueError):
            BaoStockSource(FakeClient()).daily("600000", "2010-01-01", "2026-09-01", adjustflag="0")

    def test_query_error_is_not_treated_as_empty(self):
        result = FakeResponse([], [], error_code="100", error_msg="bad request")
        with self.assertRaises(BaoStockError):
            BaoStockSource._consume(result, "test")

    def test_field_count_mismatch_is_rejected(self):
        result = FakeResponse(["a", "b"], [["only-one"]])
        with self.assertRaises(BaoStockError):
            BaoStockSource._consume(result, "test")


class StubSource:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.entered = False
        self.closed = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.closed = True

    def daily(self, *args, **kwargs):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RetryingBaoStockSourceTests(unittest.TestCase):
    def test_transient_failure_reconnects_and_retries(self):
        expected = QueryResult(fields=["date"], rows=[{"date": "2026-09-02"}])
        sources = [StubSource([BaoStockError("socket closed")]), StubSource([expected])]
        sleeps = []
        retries = []
        with RetryingBaoStockSource(
            source_factory=lambda: sources.pop(0),
            max_attempts=2,
            backoff_seconds=(5,),
            max_queries_per_session=25,
            sleeper=sleeps.append,
            on_retry=lambda event: retries.append(event),
        ) as source:
            actual = source.daily("600219", "2026-08-01", "2026-09-03", adjustflag="3")
        self.assertEqual(actual, expected)
        self.assertEqual(sleeps, [5])
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0]["attempt"], 1)

    def test_retry_limit_still_raises(self):
        sources = [StubSource([BaoStockError("one")]), StubSource([BaoStockError("two")])]
        with self.assertRaises(BaoStockError):
            with RetryingBaoStockSource(
                source_factory=lambda: sources.pop(0),
                max_attempts=2,
                backoff_seconds=(0,),
                sleeper=lambda _: None,
            ) as source:
                source.daily("600219", "2026-08-01", "2026-09-03", adjustflag="3")

    def test_session_rotates_after_configured_query_count(self):
        result = QueryResult(fields=["date"], rows=[])
        created = []

        def factory():
            source = StubSource([result])
            created.append(source)
            return source

        with RetryingBaoStockSource(
            source_factory=factory,
            max_queries_per_session=1,
            sleeper=lambda _: None,
        ) as source:
            source.daily("000001", "2026-08-01", "2026-09-03", adjustflag="3")
            source.daily("600000", "2026-08-01", "2026-09-03", adjustflag="3")
        self.assertEqual(len(created), 2)
        self.assertTrue(all(item.closed for item in created))


if __name__ == "__main__":
    unittest.main()
