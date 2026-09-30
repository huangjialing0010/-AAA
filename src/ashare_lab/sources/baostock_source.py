"""BaoStock 的最小、可测试适配器。"""

from __future__ import annotations

import sys
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable


class BaoStockError(RuntimeError):
    """BaoStock 登录或查询失败。"""


@dataclass(frozen=True)
class QueryResult:
    fields: list[str]
    rows: list[dict[str, str]]


def market_code(stock_code: str) -> str:
    code = stock_code.strip()
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"股票代码必须是六位数字: {stock_code}")
    if code.startswith(("5", "6", "9")):
        return f"sh.{code}"
    return f"sz.{code}"


class BaoStockSource:
    DAILY_FIELDS = (
        "date,code,open,high,low,close,preclose,volume,amount,adjustflag,"
        "turn,tradestatus,pctChg,isST"
    )
    DAILY_VALUATION_FIELDS = (
        "date,code,open,high,low,close,preclose,volume,amount,adjustflag,"
        "turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST"
    )

    def __init__(self, client: Any | None = None, *, socket_timeout_seconds: float = 45.0):
        if socket_timeout_seconds <= 0:
            raise ValueError("socket_timeout_seconds 必须大于0")
        self._client = client
        self._uses_default_client = client is None
        self.socket_timeout_seconds = socket_timeout_seconds
        self._logged_in = False

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import baostock as client
            except ImportError as exc:
                raise BaoStockError(
                    "缺少项目本地依赖 baostock；请按 requirements-data.txt 安装到 .python-packages"
                ) from exc
            self._client = client
        return self._client

    def __enter__(self) -> "BaoStockSource":
        previous_timeout = socket.getdefaulttimeout()
        try:
            if self._uses_default_client:
                # BaoStock 0.9.3 自己没有 connect/recv 超时；新建 socket 会继承该值。
                socket.setdefaulttimeout(self.socket_timeout_seconds)
            try:
                response = self.client.login()
            except Exception as exc:
                raise BaoStockError(f"BaoStock 登录异常: {exc}") from exc
        finally:
            if self._uses_default_client:
                socket.setdefaulttimeout(previous_timeout)
        if str(response.error_code) != "0":
            raise BaoStockError(f"BaoStock 登录失败: {response.error_code} {response.error_msg}")
        self._logged_in = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._logged_in:
            try:
                self.client.logout()
            finally:
                self._logged_in = False

    @staticmethod
    def _consume(result: Any, query_label: str) -> QueryResult:
        if str(result.error_code) != "0":
            raise BaoStockError(f"{query_label} 查询失败: {result.error_code} {result.error_msg}")
        fields = list(result.fields)
        rows: list[dict[str, str]] = []
        while result.next():
            values = result.get_row_data()
            if len(values) != len(fields):
                raise BaoStockError(f"{query_label} 返回字段数不一致")
            rows.append(dict(zip(fields, values)))
        return QueryResult(fields=fields, rows=rows)

    def daily(self, stock_code: str, start_date: str, end_date: str, adjustflag: str) -> QueryResult:
        if adjustflag not in {"1", "2", "3"}:
            raise ValueError("adjustflag 必须是 1（后复权）、2（前复权）或 3（不复权）")
        code = market_code(stock_code)
        result = self.client.query_history_k_data_plus(
            code,
            self.DAILY_FIELDS,
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag=adjustflag,
        )
        return self._consume(result, f"daily:{code}:adjustflag={adjustflag}")

    def daily_qfq(self, stock_code: str, start_date: str, end_date: str) -> QueryResult:
        return self.daily(stock_code, start_date, end_date, adjustflag="2")

    def daily_valuation(self, stock_code: str, start_date: str, end_date: str, adjustflag: str) -> QueryResult:
        if adjustflag not in {"2", "3"}:
            raise ValueError("估值日线只允许前复权2或不复权3")
        code = market_code(stock_code)
        result = self.client.query_history_k_data_plus(
            code,
            self.DAILY_VALUATION_FIELDS,
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag=adjustflag,
        )
        return self._consume(result, f"daily_valuation:{code}:adjustflag={adjustflag}")

    def hs300_members(self, query_date: str) -> QueryResult:
        result = self.client.query_hs300_stocks(date=query_date)
        return self._consume(result, f"hs300:{query_date}")

    def profit(self, stock_code: str, year: int, quarter: int) -> QueryResult:
        if quarter not in (1, 2, 3, 4):
            raise ValueError("quarter 必须是 1、2、3 或 4")
        code = market_code(stock_code)
        result = self.client.query_profit_data(code=code, year=year, quarter=quarter)
        return self._consume(result, f"profit:{code}:{year}Q{quarter}")

    def stock_basic(self, stock_code: str) -> QueryResult:
        code = market_code(stock_code)
        result = self.client.query_stock_basic(code=code)
        return self._consume(result, f"stock_basic:{code}")

    def dividend(self, stock_code: str, year: int, year_type: str = "operate") -> QueryResult:
        if year_type not in {"report", "operate"}:
            raise ValueError("year_type 必须是 report 或 operate")
        code = market_code(stock_code)
        result = self.client.query_dividend_data(code=code, year=str(year), yearType=year_type)
        return self._consume(result, f"dividend:{code}:{year}:{year_type}")

    def adjust_factor(self, stock_code: str, start_date: str, end_date: str) -> QueryResult:
        code = market_code(stock_code)
        result = self.client.query_adjust_factor(
            code=code, start_date=start_date, end_date=end_date
        )
        return self._consume(result, f"adjust_factor:{code}:{start_date}:{end_date}")


class RetryingBaoStockSource:
    """为瞬时网络故障提供有限重试，并定期主动更换会话。"""

    TRANSIENT_ERRORS = (BaoStockError, OSError, TimeoutError, ConnectionError)

    def __init__(
        self,
        source_factory: Callable[[], Any] = BaoStockSource,
        *,
        max_attempts: int = 4,
        backoff_seconds: tuple[int, ...] = (5, 15, 30),
        max_queries_per_session: int = 25,
        sleeper: Callable[[float], None] = time.sleep,
        on_retry: Callable[[dict[str, Any]], None] | None = None,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts 必须至少为1")
        if len(backoff_seconds) < max_attempts - 1:
            raise ValueError("backoff_seconds 数量不足")
        if max_queries_per_session < 1:
            raise ValueError("max_queries_per_session 必须至少为1")
        self.source_factory = source_factory
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.max_queries_per_session = max_queries_per_session
        self.sleeper = sleeper
        self.on_retry = on_retry or self._print_retry
        self._source: Any | None = None
        self._queries_in_session = 0

    @staticmethod
    def _print_retry(event: dict[str, Any]) -> None:
        print(
            "retry "
            f"operation={event['operation']} attempt={event['attempt']}/{event['max_attempts']} "
            f"sleep_seconds={event['sleep_seconds']} error={event['error']}",
            file=sys.stderr,
            flush=True,
        )

    def __enter__(self) -> "RetryingBaoStockSource":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _open(self) -> None:
        candidate = self.source_factory()
        try:
            self._source = candidate.__enter__()
        except Exception:
            try:
                candidate.__exit__(*sys.exc_info())
            except Exception:
                pass
            self._source = None
            raise
        self._queries_in_session = 0

    def close(self) -> None:
        source, self._source = self._source, None
        self._queries_in_session = 0
        if source is not None:
            try:
                source.__exit__(None, None, None)
            except Exception:
                # 已失效连接的登出失败不能覆盖原始查询错误。
                pass

    def _call(self, operation: str, *args: Any, **kwargs: Any) -> QueryResult:
        last_error: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                if self._source is None:
                    self._open()
                result = getattr(self._source, operation)(*args, **kwargs)
                self._queries_in_session += 1
                if self._queries_in_session >= self.max_queries_per_session:
                    self.close()
                return result
            except self.TRANSIENT_ERRORS as exc:
                last_error = exc
                self.close()
                if attempt >= self.max_attempts:
                    raise
                sleep_seconds = self.backoff_seconds[attempt - 1]
                self.on_retry({
                    "operation": operation,
                    "attempt": attempt,
                    "max_attempts": self.max_attempts,
                    "sleep_seconds": sleep_seconds,
                    "error": str(exc),
                })
                self.sleeper(sleep_seconds)
        assert last_error is not None
        raise last_error

    def daily(self, stock_code: str, start_date: str, end_date: str, adjustflag: str) -> QueryResult:
        return self._call("daily", stock_code, start_date, end_date, adjustflag=adjustflag)

    def daily_qfq(self, stock_code: str, start_date: str, end_date: str) -> QueryResult:
        return self._call("daily_qfq", stock_code, start_date, end_date)

    def daily_valuation(self, stock_code: str, start_date: str, end_date: str, adjustflag: str) -> QueryResult:
        return self._call("daily_valuation", stock_code, start_date, end_date, adjustflag=adjustflag)

    def hs300_members(self, query_date: str) -> QueryResult:
        return self._call("hs300_members", query_date)

    def profit(self, stock_code: str, year: int, quarter: int) -> QueryResult:
        return self._call("profit", stock_code, year, quarter)

    def stock_basic(self, stock_code: str) -> QueryResult:
        return self._call("stock_basic", stock_code)

    def dividend(self, stock_code: str, year: int, year_type: str = "operate") -> QueryResult:
        return self._call("dividend", stock_code, year, year_type=year_type)

    def adjust_factor(self, stock_code: str, start_date: str, end_date: str) -> QueryResult:
        return self._call("adjust_factor", stock_code, start_date, end_date)


def unique_values(rows: Iterable[dict[str, str]], field: str) -> set[str]:
    return {row.get(field, "") for row in rows}
