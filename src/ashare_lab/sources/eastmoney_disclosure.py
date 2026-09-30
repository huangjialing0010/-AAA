"""东方财富季度业绩报表的最小分页客户端。"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
A_SHARE_SECURITY_TYPE = "058001001"


class EastmoneyDisclosureError(RuntimeError):
    """东方财富响应失败或结构不符合契约。"""


@dataclass(frozen=True)
class PeriodResult:
    report_period: str
    pages: int
    source_rows: int
    security_type_counts: dict[str, int]
    rows: list[dict[str, Any]]


def normalize_api_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    candidate = text[:10]
    return date.fromisoformat(candidate).isoformat()


class EastmoneyDisclosureSource:
    def __init__(self, opener: Callable[..., Any] | None = None, timeout: int = 20):
        self._opener = opener or urlopen
        self.timeout = timeout

    def _page(self, report_period: str, page_number: int, page_size: int) -> dict[str, Any]:
        normalized_period = date.fromisoformat(report_period).isoformat()
        params = {
            "sortColumns": "UPDATE_DATE,SECURITY_CODE",
            "sortTypes": "-1,-1",
            "pageSize": str(page_size),
            "pageNumber": str(page_number),
            "reportName": "RPT_LICO_FN_CPD",
            "columns": "ALL",
            "filter": f"(REPORTDATE='{normalized_period}')",
        }
        request = Request(
            f"{API_URL}?{urlencode(params)}",
            headers={"User-Agent": "AshareResearchLab/0.1 data-quality-pilot"},
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise EastmoneyDisclosureError(
                f"东方财富 {normalized_period} 第{page_number}页请求失败: {exc}"
            ) from exc
        if not payload.get("success") or not isinstance(payload.get("result"), dict):
            raise EastmoneyDisclosureError(
                f"东方财富 {normalized_period} 第{page_number}页响应异常: {payload.get('message', '')}"
            )
        return payload["result"]

    def period(self, report_period: str, page_size: int = 500) -> PeriodResult:
        first = self._page(report_period, 1, page_size)
        pages = int(first.get("pages") or 0)
        if pages < 1:
            raise EastmoneyDisclosureError(f"东方财富 {report_period} 没有有效分页")
        source_rows = list(first.get("data") or [])
        for page_number in range(2, pages + 1):
            page = self._page(report_period, page_number, page_size)
            source_rows.extend(page.get("data") or [])
        required = {"SECURITY_CODE", "SECURITY_TYPE_CODE", "REPORTDATE", "UPDATE_DATE"}
        for row in source_rows:
            if required - set(row):
                raise EastmoneyDisclosureError(
                    f"东方财富 {report_period} 缺少字段: {sorted(required - set(row))}"
                )
            if normalize_api_date(row["REPORTDATE"]) != report_period:
                raise EastmoneyDisclosureError(
                    f"东方财富报告期不匹配: 请求{report_period}, 返回{row['REPORTDATE']}"
                )
        security_type_counts = dict(Counter(str(row["SECURITY_TYPE_CODE"]) for row in source_rows))
        rows = [
            row for row in source_rows
            if str(row["SECURITY_TYPE_CODE"]) == A_SHARE_SECURITY_TYPE
        ]
        if not rows:
            raise EastmoneyDisclosureError(f"东方财富 {report_period} 原始响应中没有A股记录")
        return PeriodResult(
            report_period=report_period,
            pages=pages,
            source_rows=len(source_rows),
            security_type_counts=security_type_counts,
            rows=rows,
        )
