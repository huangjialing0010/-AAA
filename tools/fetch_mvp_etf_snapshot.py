#!/usr/bin/env python
"""抓取并封存510300 MVP所需的行情、身份和现金分红原始证据。"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / ".python-packages"))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.sources.baostock_source import QueryResult, RetryingBaoStockSource  # noqa: E402


STOCK_CODE = "510300"
SINA_FACTOR_URL = "https://finance.sina.com.cn/realstock/company/sh510300/hfq.js"
SINA_DIVIDEND_URL = "https://stock.finance.sina.com.cn/fundInfo/view/FundInfo_JJFH.php?symbol=510300"
SINA_DAILY_URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService.getKLineData?symbol=sh510300&scale=240&ma=no&datalen=1023"
TENCENT_QFQ_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh510300,day,2024-01-01,2026-09-02,1000,qfq"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/510300.SS"
MVP_START_DATE = "2012-05-28"
MVP_END_DATE = "2026-09-01"
YAHOO_OVERRIDE_DATES = ("2024-01-15", "2024-03-29", "2025-05-23")
DAILY_FIELDS = {
    "date", "code", "open", "high", "low", "close", "preclose", "volume", "amount",
    "adjustflag", "turn", "tradestatus", "pctChg", "isST",
}
MIN_DIVIDEND_TABLE_ROWS = 10


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified_end_date() -> str:
    report = json.loads(
        (PROJECT_ROOT / "reports" / "baostock_daily_snapshot_quality.json").read_text(encoding="utf-8")
    )
    if report.get("status") != "PASS_WITH_KNOWN_LIMITS":
        raise RuntimeError("近期BaoStock日线报告未通过")
    counts = report.get("latest_date_counts", {})
    if len(counts) != 1:
        raise RuntimeError(f"近期已验证截止日不唯一: {counts}")
    return next(iter(counts))


def write_query_result(path: Path, result: QueryResult, extra_fields: list[str] | None = None) -> None:
    fields = list(extra_fields or []) + list(result.fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in result.rows:
            writer.writerow(row)


def validate_daily(result: QueryResult, adjustflag: str, start_date: str, end_date: str) -> None:
    missing = DAILY_FIELDS - set(result.fields)
    if missing:
        raise RuntimeError(f"日线字段缺失: {sorted(missing)}")
    dates = [row["date"] for row in result.rows]
    if len(dates) != len(set(dates)) or dates != sorted(dates):
        raise RuntimeError("日线日期重复或未升序")
    if len(dates) >= 2000:
        raise RuntimeError(f"分段返回{len(dates)}行，未规避BaoStock分页风险")
    for row in result.rows:
        if row["adjustflag"] != adjustflag or row["code"] != "sh.510300":
            raise RuntimeError("日线代码或复权标记不一致")
        if not start_date <= row["date"] <= end_date:
            raise RuntimeError(f"日线日期越界: {row['date']}")


def fetch_url(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "AshareResearchLab/0.1 mvp-data-audit"})
    with urlopen(request, timeout=30) as response:
        return response.read()


def yahoo_url(start_date: str, end_date: str) -> str:
    start_epoch = int(datetime.combine(date.fromisoformat(start_date), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    exclusive_end = date.fromisoformat(end_date) + timedelta(days=1)
    end_epoch = int(datetime.combine(exclusive_end, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    params = {
        "period1": str(start_epoch),
        "period2": str(end_epoch),
        "interval": "1d",
        "events": "div,splits",
    }
    return f"{YAHOO_CHART_URL}?{urlencode(params)}"


def _decimal_text(value: Any) -> str:
    if value is None:
        raise RuntimeError("Yahoo完整交易日包含空价格")
    return format(Decimal(str(value)).quantize(Decimal("0.000001")), "f")


def parse_yahoo_chart(raw: bytes, start_date: str, end_date: str) -> tuple[
    list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[str]
]:
    payload = json.loads(raw.decode("utf-8"))
    chart = payload.get("chart", {})
    if chart.get("error") is not None:
        raise RuntimeError(f"Yahoo图表接口报错: {chart['error']}")
    results = chart.get("result") or []
    if len(results) != 1:
        raise RuntimeError(f"Yahoo图表结果数量异常: {len(results)}")
    result = results[0]
    if result.get("meta", {}).get("symbol") != "510300.SS":
        raise RuntimeError("Yahoo返回证券代码错误")
    timestamps = result.get("timestamp") or []
    quotes = (result.get("indicators", {}).get("quote") or [])
    adjusted = (result.get("indicators", {}).get("adjclose") or [])
    if len(quotes) != 1 or len(adjusted) != 1:
        raise RuntimeError("Yahoo行情数组结构异常")
    quote = quotes[0]
    adjclose = adjusted[0].get("adjclose") or []
    fields = ("open", "high", "low", "close", "volume")
    if any(len(quote.get(field) or []) != len(timestamps) for field in fields) or len(adjclose) != len(timestamps):
        raise RuntimeError("Yahoo时间戳与行情数组长度不一致")
    rows = []
    missing_dates = []
    for index, timestamp in enumerate(timestamps):
        trade_date = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date().isoformat()
        if not start_date <= trade_date <= end_date:
            continue
        values = {field: quote[field][index] for field in fields}
        if any(value is None for value in values.values()):
            missing_dates.append(trade_date)
            continue
        rows.append({
            "date": trade_date,
            "code": "510300.SS",
            "open": _decimal_text(values["open"]),
            "high": _decimal_text(values["high"]),
            "low": _decimal_text(values["low"]),
            "close": _decimal_text(values["close"]),
            "adjclose": _decimal_text(adjclose[index]) if adjclose[index] is not None else "",
            "volume": str(int(values["volume"])),
            "row_source": "yahoo",
        })
    if not rows or rows[0]["date"] != start_date or rows[-1]["date"] != end_date:
        raise RuntimeError(f"Yahoo完整历史边界异常: {rows[0]['date'] if rows else None}..{rows[-1]['date'] if rows else None}")
    dates = [row["date"] for row in rows]
    if dates != sorted(set(dates)):
        raise RuntimeError("Yahoo历史日期重复或未升序")

    dividends = []
    for event in (result.get("events", {}).get("dividends") or {}).values():
        event_date = datetime.fromtimestamp(int(event["date"]), tz=timezone.utc).date().isoformat()
        if start_date <= event_date <= end_date:
            dividends.append({"date": event_date, "amount": _decimal_text(event["amount"])})
    dividends.sort(key=lambda row: row["date"])
    splits = []
    for event in (result.get("events", {}).get("splits") or {}).values():
        event_date = datetime.fromtimestamp(int(event["date"]), tz=timezone.utc).date().isoformat()
        if start_date <= event_date <= end_date:
            splits.append({
                "date": event_date,
                "numerator": str(event.get("numerator", "")),
                "denominator": str(event.get("denominator", "")),
                "split_ratio": str(event.get("splitRatio", "")),
            })
    splits.sort(key=lambda row: row["date"])
    return rows, dividends, splits, missing_dates


def parse_sina_daily(raw: bytes) -> list[dict[str, str]]:
    text = raw.decode("utf-8")
    match = re.search(r"var\s+_data=\((\[.*\])\);\s*$", text, flags=re.S)
    if not match:
        raise RuntimeError("新浪日K响应结构异常")
    source = json.loads(match.group(1))
    rows = []
    for row in source:
        trade_date = date.fromisoformat(row["day"]).isoformat()
        rows.append({
            "date": trade_date,
            "code": "sh.510300",
            "open": format(Decimal(row["open"]).quantize(Decimal("0.000001")), "f"),
            "high": format(Decimal(row["high"]).quantize(Decimal("0.000001")), "f"),
            "low": format(Decimal(row["low"]).quantize(Decimal("0.000001")), "f"),
            "close": format(Decimal(row["close"]).quantize(Decimal("0.000001")), "f"),
            "volume": str(int(row["volume"])),
        })
    dates = [row["date"] for row in rows]
    if not rows or dates != sorted(set(dates)):
        raise RuntimeError("新浪日K为空、重复或未升序")
    return rows


def parse_tencent_qfq(raw: bytes) -> list[dict[str, str]]:
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError(f"腾讯日K接口报错: {payload.get('code')} {payload.get('msg')}")
    source = payload.get("data", {}).get("sh510300", {}).get("qfqday") or []
    rows = []
    for row in source:
        if len(row) < 6:
            raise RuntimeError(f"腾讯日K字段不足: {row}")
        rows.append({
            "date": date.fromisoformat(row[0]).isoformat(),
            "open": format(Decimal(row[1]).quantize(Decimal("0.000001")), "f"),
            "close": format(Decimal(row[2]).quantize(Decimal("0.000001")), "f"),
            "high": format(Decimal(row[3]).quantize(Decimal("0.000001")), "f"),
            "low": format(Decimal(row[4]).quantize(Decimal("0.000001")), "f"),
            "volume_lots": format(Decimal(row[5]), "f"),
        })
    if not rows:
        raise RuntimeError("腾讯日K为空")
    return rows


def cross_source_anomalies(
    yahoo_rows: list[dict[str, str]], sina_rows: list[dict[str, str]]
) -> list[str]:
    yahoo_by_date = {row["date"]: row for row in yahoo_rows}
    anomalies = []
    for row in sina_rows:
        other = yahoo_by_date.get(row["date"])
        if other is None:
            continue
        price_mismatch = any(
            abs(Decimal(row[field]) - Decimal(other[field])) > Decimal("0.0001")
            for field in ("open", "high", "low", "close")
        )
        if price_mismatch or int(row["volume"]) != int(other["volume"]):
            anomalies.append(row["date"])
    return anomalies


def write_dict_rows(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_factor_events(raw: bytes) -> list[dict[str, str]]:
    text = raw.decode("utf-8")
    match = re.search(r"=\s*(\{.*?\})\s*/\*", text, flags=re.S)
    if not match:
        raise RuntimeError("新浪复权因子响应结构异常")
    payload = json.loads(match.group(1))
    rows = [row for row in payload.get("data", []) if row.get("d") != "1900-01-01"]
    rows.sort(key=lambda row: row["d"])
    previous = Decimal("0")
    events = []
    for row in rows:
        current = Decimal(str(row["u"]))
        cash = current - previous
        previous = current
        if cash <= 0:
            raise RuntimeError(f"累计分红未单调增加: {row}")
        if Decimal(str(row["s"])) != Decimal("1"):
            raise RuntimeError(f"发现未处理的份额拆分因子: {row}")
        events.append({
            "ex_date": date.fromisoformat(row["d"]).isoformat(),
            "cash_per_share": format(cash, "f"),
            "cumulative_cash": format(current, "f"),
        })
    return events


def parse_dividend_table(raw: bytes) -> list[dict[str, str]]:
    text = raw.decode("gb18030", errors="strict")
    marker = "历史分红"
    start = text.find(marker)
    if start < 0:
        raise RuntimeError("新浪历史分红表不存在")
    table = text[start:]
    parsed = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", table, flags=re.S | re.I):
        cells = []
        for value in re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.S | re.I):
            cleaned = re.sub(r"<[^>]+>", "", value)
            cells.append(html.unescape(cleaned).strip())
        if len(cells) < 3 or not re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", cells[0]):
            continue
        amount_text = cells[2]
        if not re.fullmatch(r"\d+(?:\.\d+)?", amount_text):
            continue
        amount = Decimal(amount_text)
        if amount <= 0:
            continue
        record_date = datetime.strptime(cells[0], "%Y/%m/%d").date().isoformat()
        payment_date = datetime.strptime(cells[1], "%Y/%m/%d").date().isoformat()
        parsed.append({
            "record_date": record_date,
            "payment_date": payment_date,
            "cash_per_share": format(amount, "f"),
        })
    if len(parsed) < MIN_DIVIDEND_TABLE_ROWS:
        raise RuntimeError(f"新浪历史分红表有效记录过少: {len(parsed)}")
    return sorted(parsed, key=lambda row: row["record_date"])


def combine_dividends(
    factor_events: list[dict[str, str]], table_rows: list[dict[str, str]]
) -> list[dict[str, str]]:
    unused = list(table_rows)
    combined = []
    for event in factor_events:
        amount = Decimal(event["cash_per_share"])
        matches = [
            row for row in unused
            if Decimal(row["cash_per_share"]) == amount
            and row["record_date"] <= event["ex_date"] <= row["payment_date"]
        ]
        if len(matches) != 1:
            raise RuntimeError(f"分红因子与历史表无法唯一匹配: {event} / {matches}")
        row = matches[0]
        unused.remove(row)
        combined.append({
            "record_date": row["record_date"],
            "ex_date": event["ex_date"],
            "payment_date": row["payment_date"],
            "cash_per_share": event["cash_per_share"],
            "cumulative_cash": event["cumulative_cash"],
        })
    if unused:
        raise RuntimeError(f"历史分红表存在未匹配记录: {unused}")
    return combined


def file_entry(root: Path, path: Path, *, category: str, rows: int | None = None) -> dict[str, Any]:
    entry = {
        "path": path.relative_to(root).as_posix(),
        "category": category,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if rows is not None:
        entry["rows"] = rows
    return entry


def main() -> int:
    started = datetime.now(timezone.utc)
    verified_latest = verified_end_date()
    if verified_latest < MVP_END_DATE:
        raise RuntimeError(f"BaoStock近期快照尚未覆盖MVP截止日: {verified_latest}")
    output_root = PROJECT_ROOT / "data" / "imports" / started.strftime("mvp_etf_510300_%Y%m%dT%H%M%SZ")
    if output_root.exists():
        raise RuntimeError(f"输出目录已存在，禁止覆盖: {output_root}")
    output_root.mkdir(parents=True)
    entries = []

    with RetryingBaoStockSource(max_queries_per_session=10) as source:
        basic = source.stock_basic(STOCK_CODE)
        if len(basic.rows) != 1:
            raise RuntimeError(f"510300基本资料应为1条，实际{len(basic.rows)}")
        identity = basic.rows[0]
        if identity.get("code") != "sh.510300" or identity.get("type") != "5" or identity.get("status") != "1":
            raise RuntimeError(f"510300证券身份不符合MVP要求: {identity}")
        basic_path = output_root / "security_basic.csv"
        write_query_result(basic_path, basic)
        entries.append(file_entry(output_root, basic_path, category="security_basic", rows=1))

        factor = source.adjust_factor(STOCK_CODE, MVP_START_DATE, MVP_END_DATE)
        factor_path = output_root / "baostock_adjust_factor.csv"
        write_query_result(factor_path, factor)
        entries.append(file_entry(output_root, factor_path, category="baostock_adjust_factor", rows=len(factor.rows)))

        dividend_fields: list[str] = []
        dividend_rows: list[dict[str, str]] = []
        for year in range(2012, date.fromisoformat(MVP_END_DATE).year + 1):
            result = source.dividend(STOCK_CODE, year, year_type="operate")
            dividend_fields = result.fields or dividend_fields
            for row in result.rows:
                dividend_rows.append({"query_year": str(year), **row})
        dividend_path = output_root / "baostock_dividend_queries.csv"
        dividend_result = QueryResult(fields=["query_year", *dividend_fields], rows=dividend_rows)
        write_query_result(dividend_path, dividend_result)
        entries.append(file_entry(output_root, dividend_path, category="baostock_dividend", rows=len(dividend_rows)))

        overlap = source.daily(STOCK_CODE, "2026-01-01", MVP_END_DATE, adjustflag="3")
        validate_daily(overlap, "3", "2026-01-01", MVP_END_DATE)
        overlap_path = output_root / "cross_validation" / "baostock_2026_unadjusted.csv"
        write_query_result(overlap_path, overlap)
        overlap_entry = file_entry(output_root, overlap_path, category="baostock_overlap_unadjusted", rows=len(overlap.rows))
        overlap_entry.update({
            "stock_code": STOCK_CODE,
            "adjustflag": "3",
            "start_date": "2026-01-01",
            "end_date": MVP_END_DATE,
            "min_date": overlap.rows[0]["date"] if overlap.rows else None,
            "max_date": overlap.rows[-1]["date"] if overlap.rows else None,
        })
        entries.append(overlap_entry)

    yahoo_request_url = yahoo_url(MVP_START_DATE, MVP_END_DATE)
    yahoo_raw = fetch_url(yahoo_request_url)
    yahoo_rows, yahoo_dividends, yahoo_splits, yahoo_missing_dates = parse_yahoo_chart(
        yahoo_raw, MVP_START_DATE, MVP_END_DATE
    )
    if yahoo_missing_dates != ["2025-10-24"]:
        raise RuntimeError(f"Yahoo缺口与冻结证据不一致: {yahoo_missing_dates}")
    sina_daily_raw = fetch_url(SINA_DAILY_URL)
    sina_daily_rows = parse_sina_daily(sina_daily_raw)
    sina_by_date = {row["date"]: row for row in sina_daily_rows}
    anomaly_dates = cross_source_anomalies(yahoo_rows, sina_daily_rows)
    if anomaly_dates != list(YAHOO_OVERRIDE_DATES):
        raise RuntimeError(f"Yahoo/Sina异常日与冻结证据不一致: {anomaly_dates}")
    for missing_date in yahoo_missing_dates:
        source = sina_by_date.get(missing_date)
        if source is None:
            raise RuntimeError(f"新浪未覆盖Yahoo缺口: {missing_date}")
        yahoo_rows.append({
            "date": missing_date,
            "code": "510300.SS",
            "open": source["open"],
            "high": source["high"],
            "low": source["low"],
            "close": source["close"],
            "adjclose": "",
            "volume": source["volume"],
            "row_source": "sina_gap_fill",
        })
    yahoo_by_date = {row["date"]: row for row in yahoo_rows}
    for anomaly_date in YAHOO_OVERRIDE_DATES:
        source = sina_by_date[anomaly_date]
        target = yahoo_by_date[anomaly_date]
        target.update({
            "open": source["open"],
            "high": source["high"],
            "low": source["low"],
            "close": source["close"],
            "volume": source["volume"],
            "row_source": "sina_corroborated_override",
        })
    yahoo_rows.sort(key=lambda row: row["date"])
    tencent_raw = fetch_url(TENCENT_QFQ_URL)
    tencent_rows = parse_tencent_qfq(tencent_raw)
    yahoo_raw_path = output_root / "market_data" / "yahoo_chart_raw.json"
    yahoo_daily_path = output_root / "market_data" / "daily_unadjusted.csv"
    sina_daily_raw_path = output_root / "market_data" / "sina_kline_recent_raw.js"
    sina_daily_path = output_root / "market_data" / "sina_kline_recent.csv"
    tencent_raw_path = output_root / "market_data" / "tencent_qfq_recent_raw.json"
    tencent_path = output_root / "market_data" / "tencent_qfq_recent.csv"
    yahoo_dividend_path = output_root / "fund_actions" / "yahoo_dividends.csv"
    yahoo_split_path = output_root / "fund_actions" / "yahoo_splits.csv"
    yahoo_raw_path.parent.mkdir(parents=True, exist_ok=True)
    yahoo_raw_path.write_bytes(yahoo_raw)
    sina_daily_raw_path.write_bytes(sina_daily_raw)
    tencent_raw_path.write_bytes(tencent_raw)
    write_dict_rows(
        yahoo_daily_path,
        ["date", "code", "open", "high", "low", "close", "adjclose", "volume", "row_source"],
        yahoo_rows,
    )
    write_dict_rows(
        sina_daily_path,
        ["date", "code", "open", "high", "low", "close", "volume"],
        sina_daily_rows,
    )
    write_dict_rows(
        tencent_path,
        ["date", "open", "close", "high", "low", "volume_lots"],
        tencent_rows,
    )
    write_dict_rows(yahoo_dividend_path, ["date", "amount"], yahoo_dividends)
    write_dict_rows(yahoo_split_path, ["date", "numerator", "denominator", "split_ratio"], yahoo_splits)
    entries.extend([
        file_entry(output_root, yahoo_raw_path, category="yahoo_chart_raw"),
        file_entry(output_root, yahoo_daily_path, category="daily_unadjusted_normalized", rows=len(yahoo_rows)),
        file_entry(output_root, sina_daily_raw_path, category="sina_daily_raw"),
        file_entry(output_root, sina_daily_path, category="sina_daily_normalized", rows=len(sina_daily_rows)),
        file_entry(output_root, tencent_raw_path, category="tencent_qfq_raw"),
        file_entry(output_root, tencent_path, category="tencent_qfq_normalized", rows=len(tencent_rows)),
        file_entry(output_root, yahoo_dividend_path, category="yahoo_dividend_normalized", rows=len(yahoo_dividends)),
        file_entry(output_root, yahoo_split_path, category="yahoo_split_normalized", rows=len(yahoo_splits)),
    ])

    factor_raw = fetch_url(SINA_FACTOR_URL)
    dividend_raw = fetch_url(SINA_DIVIDEND_URL)
    factor_raw_path = output_root / "fund_actions" / "sina_hfq_raw.js"
    dividend_raw_path = output_root / "fund_actions" / "sina_dividend_page_raw.html"
    factor_raw_path.parent.mkdir(parents=True, exist_ok=True)
    factor_raw_path.write_bytes(factor_raw)
    dividend_raw_path.write_bytes(dividend_raw)
    factor_events = parse_factor_events(factor_raw)
    table_rows = parse_dividend_table(dividend_raw)
    dividends = combine_dividends(factor_events, table_rows)
    dividend_path = output_root / "fund_actions" / "dividends.csv"
    with dividend_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "record_date", "ex_date", "payment_date", "cash_per_share", "cumulative_cash"
        ])
        writer.writeheader()
        writer.writerows(dividends)
    entries.extend([
        file_entry(output_root, factor_raw_path, category="fund_action_raw_factor"),
        file_entry(output_root, dividend_raw_path, category="fund_action_raw_table"),
        file_entry(output_root, dividend_path, category="fund_action_normalized", rows=len(dividends)),
    ])

    manifest = {
        "schema_version": 1,
        "status": "SEALED_PENDING_INDEPENDENT_VALIDATION",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "purpose": "mvp_510300_trend_backtest_and_paper_ledger",
        "security": identity,
        "query_start_date": MVP_START_DATE,
        "query_end_date": MVP_END_DATE,
        "latest_available_baostock_date": verified_latest,
        "sources": {
            "daily_unadjusted": yahoo_request_url,
            "identity_and_overlap": "BaoStock",
            "fund_action_factor": SINA_FACTOR_URL,
            "fund_action_table": SINA_DIVIDEND_URL,
            "recent_daily_cross_validation_and_gap_fill": SINA_DAILY_URL,
            "anomaly_corroboration": TENCENT_QFQ_URL,
        },
        "files": len(entries),
        "rows": sum(entry.get("rows", 0) for entry in entries),
        "bytes": sum(entry["bytes"] for entry in entries),
        "entries": entries,
        "fund_action_summary": {
            "events": len(dividends),
            "cumulative_cash_per_share": dividends[-1]["cumulative_cash"],
            "baostock_adjust_factor_rows": len(factor.rows),
            "baostock_dividend_rows": len(dividend_rows),
            "yahoo_dividend_rows": len(yahoo_dividends),
            "yahoo_split_rows": len(yahoo_splits),
            "yahoo_missing_dates": yahoo_missing_dates,
            "yahoo_override_dates": list(YAHOO_OVERRIDE_DATES),
        },
        "limits": [
            "Yahoo公开图表接口提供不复权行情，接口稳定性和授权等级不等同于正式数据服务",
            "Yahoo调整收盘价不用于策略；总回报信号由不复权价格和新浪现金分红内部构建",
            "BaoStock对该ETF未返回完整历史、复权因子和分红记录，仅用于证券身份与2026重叠期交叉验证",
            "新浪页面声明数据仅供参考，正式实盘前仍须以基金公司公告为准",
            "本快照仅用于MVP研究，不构成收益承诺",
        ],
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "snapshot": str(output_root),
        "status": manifest["status"],
        "files": manifest["files"],
        "rows": manifest["rows"],
        "dividend_events": len(dividends),
        "market_dates": len(yahoo_rows),
        "baostock_overlap_dates": len(overlap.rows),
        "sina_recent_dates": len(sina_daily_rows),
        "tencent_recent_dates": len(tencent_rows),
        "elapsed_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
