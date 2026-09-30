#!/usr/bin/env python
"""抓取并封存腾讯主源的510300 MVP快照。"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / ".python-packages"))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.sources.baostock_source import QueryResult, RetryingBaoStockSource  # noqa: E402
from ashare_lab.sources.public_etf_sources import (  # noqa: E402
    combine_dividends,
    parse_sina_daily,
    parse_sina_dividend_table,
    parse_sina_factor_events,
    parse_tencent_unadjusted,
)


STOCK_CODE = "510300"
START_DATE = "2012-05-28"
END_DATE = "2026-09-01"
TENCENT_WINDOWS = (
    ("2012-05-28", "2019-12-31", 2000),
    ("2020-01-01", "2026-09-01", 2000),
)
SINA_DAILY_URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService.getKLineData?symbol=sh510300&scale=240&ma=no&datalen=1023"
SINA_FACTOR_URL = "https://finance.sina.com.cn/realstock/company/sh510300/hfq.js"
SINA_DIVIDEND_URL = "https://stock.finance.sina.com.cn/fundInfo/view/FundInfo_JJFH.php?symbol=510300"
TRADE_CALENDAR_PATH = PROJECT_ROOT / "data" / "canonical" / "reference" / "trade_calendar.csv"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_url(url: str) -> bytes:
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 AshareResearchLab/0.1",
        "Referer": "https://gu.qq.com/" if "gtimg.cn" in url else "https://finance.sina.com.cn/",
    })
    with urlopen(request, timeout=30) as response:
        return response.read()


def tencent_url(start_date: str, end_date: str, count: int) -> str:
    return (
        "https://web.ifzq.gtimg.cn/appstock/app/kline/kline?"
        f"param=sh510300,day,{start_date},{end_date},{count}"
    )


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_query_result(path: Path, result: QueryResult) -> None:
    write_csv(path, list(result.fields), result.rows)


def file_entry(root: Path, path: Path, category: str, rows: int | None = None) -> dict[str, Any]:
    output = {
        "path": path.relative_to(root).as_posix(),
        "category": category,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if rows is not None:
        output["rows"] = rows
    return output


def verified_baostock_latest() -> str:
    report = json.loads(
        (PROJECT_ROOT / "reports" / "baostock_daily_snapshot_quality.json").read_text(encoding="utf-8")
    )
    if report.get("status") != "PASS_WITH_KNOWN_LIMITS":
        raise RuntimeError("BaoStock近期快照未通过")
    counts = report.get("latest_date_counts", {})
    if len(counts) != 1:
        raise RuntimeError(f"BaoStock近期截止日不唯一: {counts}")
    return next(iter(counts))


def calendar_rows() -> list[dict[str, str]]:
    with TRADE_CALENDAR_PATH.open("r", encoding="utf-8", newline="") as handle:
        rows = [
            {"trade_date": row["trade_date"]}
            for row in csv.DictReader(handle)
            if START_DATE <= row["trade_date"] <= END_DATE
        ]
    dates = [row["trade_date"] for row in rows]
    if dates != sorted(set(dates)) or len(rows) != 3468:
        raise RuntimeError(f"冻结交易日历异常: {len(rows)}")
    return rows


def main() -> int:
    started = datetime.now(timezone.utc)
    latest = verified_baostock_latest()
    if latest < END_DATE:
        raise RuntimeError(f"BaoStock近期快照未覆盖MVP截止日: {latest}")
    root = PROJECT_ROOT / "data" / "imports" / started.strftime("mvp_etf_510300_tencent_%Y%m%dT%H%M%SZ")
    if root.exists():
        raise RuntimeError(f"输出目录已存在，禁止覆盖: {root}")
    root.mkdir(parents=True)
    entries: list[dict[str, Any]] = []

    with RetryingBaoStockSource(max_queries_per_session=10) as source:
        basic = source.stock_basic(STOCK_CODE)
        if len(basic.rows) != 1 or not (
            basic.rows[0].get("code") == "sh.510300"
            and basic.rows[0].get("type") == "5"
            and basic.rows[0].get("status") == "1"
            and basic.rows[0].get("ipoDate") == START_DATE
        ):
            raise RuntimeError(f"510300证券身份异常: {basic.rows}")
        basic_path = root / "security_basic.csv"
        write_query_result(basic_path, basic)
        entries.append(file_entry(root, basic_path, "security_basic", len(basic.rows)))

        factor = source.adjust_factor(STOCK_CODE, START_DATE, END_DATE)
        factor_path = root / "cross_validation" / "baostock_adjust_factor.csv"
        write_query_result(factor_path, factor)
        entries.append(file_entry(root, factor_path, "baostock_adjust_factor", len(factor.rows)))

        dividend_fields: list[str] = []
        dividend_rows: list[dict[str, str]] = []
        for year in range(2012, date.fromisoformat(END_DATE).year + 1):
            result = source.dividend(STOCK_CODE, year, year_type="operate")
            dividend_fields = result.fields or dividend_fields
            dividend_rows.extend({"query_year": str(year), **row} for row in result.rows)
        baostock_dividend_path = root / "cross_validation" / "baostock_dividend_queries.csv"
        write_csv(baostock_dividend_path, ["query_year", *dividend_fields], dividend_rows)
        entries.append(file_entry(root, baostock_dividend_path, "baostock_dividend", len(dividend_rows)))

        overlap = source.daily(STOCK_CODE, "2026-01-01", END_DATE, adjustflag="3")
        if not overlap.rows or any(row["adjustflag"] != "3" for row in overlap.rows):
            raise RuntimeError("BaoStock 2026重叠日线为空或复权标记错误")
        overlap_path = root / "cross_validation" / "baostock_2026_unadjusted.csv"
        write_query_result(overlap_path, overlap)
        entries.append(file_entry(root, overlap_path, "baostock_overlap_unadjusted", len(overlap.rows)))

    tencent_rows: list[dict[str, str]] = []
    tencent_requests = []
    for index, (start_date, end_date, count) in enumerate(TENCENT_WINDOWS, start=1):
        url = tencent_url(start_date, end_date, count)
        raw = fetch_url(url)
        rows = parse_tencent_unadjusted(raw)
        if rows[0]["date"] < start_date or rows[-1]["date"] > end_date:
            raise RuntimeError(f"腾讯分段边界异常: {start_date} {end_date}")
        raw_path = root / "market_data" / f"tencent_unadjusted_raw_{index}.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw)
        entries.append(file_entry(root, raw_path, "tencent_unadjusted_raw"))
        tencent_rows.extend(rows)
        tencent_requests.append({"url": url, "rows": len(rows), "min_date": rows[0]["date"], "max_date": rows[-1]["date"]})
    dates = [row["date"] for row in tencent_rows]
    if dates != sorted(set(dates)):
        raise RuntimeError("腾讯分段合并后日期重复或未升序")
    market_path = root / "market_data" / "daily_unadjusted.csv"
    market_fields = [
        "date", "code", "open", "high", "low", "close", "volume",
        "volume_lots_source", "row_source",
    ]
    write_csv(market_path, market_fields, tencent_rows)
    entries.append(file_entry(root, market_path, "daily_unadjusted_normalized", len(tencent_rows)))

    sina_daily_raw = fetch_url(SINA_DAILY_URL)
    sina_daily = parse_sina_daily(sina_daily_raw)
    sina_daily_raw_path = root / "cross_validation" / "sina_recent_raw.js"
    sina_daily_path = root / "cross_validation" / "sina_recent.csv"
    sina_daily_raw_path.write_bytes(sina_daily_raw)
    write_csv(sina_daily_path, ["date", "code", "open", "high", "low", "close", "volume"], sina_daily)
    entries.extend([
        file_entry(root, sina_daily_raw_path, "sina_daily_raw"),
        file_entry(root, sina_daily_path, "sina_daily_normalized", len(sina_daily)),
    ])

    factor_raw = fetch_url(SINA_FACTOR_URL)
    table_raw = fetch_url(SINA_DIVIDEND_URL)
    factor_events = parse_sina_factor_events(factor_raw)
    table_rows = parse_sina_dividend_table(table_raw)
    dividends = combine_dividends(factor_events, table_rows)
    factor_raw_path = root / "fund_actions" / "sina_hfq_raw.js"
    table_raw_path = root / "fund_actions" / "sina_dividend_page_raw.html"
    dividends_path = root / "fund_actions" / "dividends.csv"
    factor_raw_path.parent.mkdir(parents=True, exist_ok=True)
    factor_raw_path.write_bytes(factor_raw)
    table_raw_path.write_bytes(table_raw)
    write_csv(
        dividends_path,
        ["record_date", "ex_date", "payment_date", "cash_per_share", "cumulative_cash"],
        dividends,
    )
    entries.extend([
        file_entry(root, factor_raw_path, "fund_action_raw_factor"),
        file_entry(root, table_raw_path, "fund_action_raw_table"),
        file_entry(root, dividends_path, "fund_action_normalized", len(dividends)),
    ])

    trade_dates = calendar_rows()
    calendar_path = root / "cross_validation" / "trade_calendar.csv"
    write_csv(calendar_path, ["trade_date"], trade_dates)
    entries.append(file_entry(root, calendar_path, "trade_calendar_evidence", len(trade_dates)))

    manifest = {
        "schema_version": 2,
        "status": "SEALED_PENDING_INDEPENDENT_VALIDATION",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "purpose": "mvp_510300_trend_backtest_and_paper_ledger",
        "security": basic.rows[0],
        "query_start_date": START_DATE,
        "query_end_date": END_DATE,
        "sources": {
            "primary_unadjusted_daily": "Tencent public kline",
            "tencent_requests": tencent_requests,
            "recent_daily_cross_validation": SINA_DAILY_URL,
            "identity_and_recent_cross_validation": "BaoStock",
            "fund_action_factor": SINA_FACTOR_URL,
            "fund_action_table": SINA_DIVIDEND_URL,
            "trade_calendar": {
                "path": TRADE_CALENDAR_PATH.relative_to(PROJECT_ROOT).as_posix(),
                "sha256": sha256_file(TRADE_CALENDAR_PATH),
            },
        },
        "files": len(entries),
        "rows": sum(item.get("rows", 0) for item in entries),
        "bytes": sum(item["bytes"] for item in entries),
        "entries": entries,
        "summary": {
            "market_dates": len(tencent_rows),
            "sina_recent_dates": len(sina_daily),
            "baostock_overlap_dates": len(overlap.rows),
            "dividend_events": len(dividends),
            "cumulative_cash_per_share": dividends[-1]["cumulative_cash"],
            "volume_conversion": "Tencent volume lots multiplied by 100 to shares",
        },
        "limits": [
            "腾讯、新浪和BaoStock均为公开数据接口，不等同于正式授权数据服务",
            "腾讯成交量由手乘100转换为份，跨源验证允许最多100份尾差",
            "总回报信号必须由不复权价格和14次现金分红内部构建，不使用外部复权价格",
            "本快照仅用于MVP研究，不构成收益承诺或实盘数据准入",
        ],
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "snapshot": str(root),
        "status": manifest["status"],
        "market_dates": len(tencent_rows),
        "sina_recent_dates": len(sina_daily),
        "baostock_overlap_dates": len(overlap.rows),
        "dividend_events": len(dividends),
        "files": len(entries),
        "elapsed_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
