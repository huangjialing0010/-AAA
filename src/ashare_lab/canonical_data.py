"""把只读导入快照转换为可追溯的标准 CSV 数据层。"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


TRANSFORM_VERSION = "canonical-v1"
SNAPSHOT_NAME = "legacy_snapshot_20260901"
SNAPSHOT_DATE = "2026-09-01"
MISSING_TOKENS = {"", "false", "none", "null", "nan", "--", "-"}

DAILY_COLUMNS = [
    "trade_date",
    "stock_code",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "outstanding_share",
    "turnover",
    "adjustment",
]

FINANCIAL_FIELD_MAP = {
    "净利润": "net_profit",
    "净利润同比增长率": "net_profit_yoy",
    "扣非净利润": "deducted_net_profit",
    "扣非净利润同比增长率": "deducted_net_profit_yoy",
    "营业总收入": "revenue",
    "营业总收入同比增长率": "revenue_yoy",
    "基本每股收益": "basic_eps",
    "每股净资产": "book_value_per_share",
    "每股资本公积金": "capital_reserve_per_share",
    "每股未分配利润": "retained_earnings_per_share",
    "每股经营现金流": "operating_cashflow_per_share",
    "销售净利率": "net_profit_margin",
    "销售毛利率": "gross_margin",
    "净资产收益率": "roe",
    "净资产收益率-摊薄": "roe_diluted",
    "营业周期": "operating_cycle_days",
    "存货周转率": "inventory_turnover",
    "存货周转天数": "inventory_turnover_days",
    "应收账款周转天数": "receivable_turnover_days",
    "流动比率": "current_ratio",
    "速动比率": "quick_ratio",
    "保守速动比率": "conservative_quick_ratio",
    "产权比率": "debt_to_equity",
    "资产负债率": "debt_ratio",
}

FINANCIAL_COLUMNS = [
    "report_period",
    "available_date_assumed",
    "stock_code",
    *FINANCIAL_FIELD_MAP.values(),
    "availability_method",
]


class DataQualityError(RuntimeError):
    """输入或输出违反数据契约。"""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_number(value: Any) -> str:
    """把中文单位和百分数转为稳定的十进制字符串，缺失值返回空串。"""
    if value is None:
        return ""
    text = str(value).strip().replace(",", "")
    if text.lower() in MISSING_TOKENS:
        return ""

    multiplier = 1.0
    if text.endswith("万"):
        multiplier = 10_000.0
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 100_000_000.0
        text = text[:-1]

    is_percent = text.endswith("%")
    if is_percent:
        text = text[:-1]
    try:
        number = float(text) * multiplier
    except ValueError:
        return ""
    if not math.isfinite(number):
        return ""
    if is_percent:
        number /= 100.0
    return format(number, ".15g")


def assumed_available_date(report_period: str) -> tuple[str, str]:
    period = date.fromisoformat(report_period)
    month_day = (period.month, period.day)
    if month_day == (3, 31):
        result = date(period.year, 5, 15)
        method = "conservative_q1_deadline_proxy"
    elif month_day == (6, 30):
        result = date(period.year, 8, 31)
        method = "conservative_half_year_deadline_proxy"
    elif month_day == (9, 30):
        result = date(period.year, 11, 15)
        method = "conservative_q3_deadline_proxy"
    elif month_day == (12, 31):
        result = date(period.year + 1, 4, 30)
        method = "conservative_annual_deadline_proxy"
    else:
        result = period + timedelta(days=120)
        method = "fallback_report_period_plus_120_days"
    return result.isoformat(), method


def read_csv(path: Path) -> Iterable[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def write_csv(path: Path, columns: list[str], rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
            count += 1
    return count


def stock_code_from_name(path: Path) -> str:
    match = re.match(r"(\d{6})", path.stem)
    if not match:
        raise DataQualityError(f"无法从文件名识别股票代码: {path.name}")
    return match.group(1)


def normalize_daily(source: Path, target: Path) -> dict[str, Any]:
    code = stock_code_from_name(source)
    seen_dates: set[str] = set()
    rows: list[dict[str, str]] = []
    for raw in read_csv(source):
        trade_date = raw.get("日期", "").strip()
        date.fromisoformat(trade_date)
        if trade_date in seen_dates:
            raise DataQualityError(f"{code} 日线日期重复: {trade_date}")
        seen_dates.add(trade_date)
        numeric = {key: parse_number(raw.get(label)) for key, label in {
            "open": "开盘", "high": "最高", "low": "最低", "close": "收盘",
            "volume": "成交量", "amount": "amount",
            "outstanding_share": "outstanding_share", "turnover": "turnover",
        }.items()}
        required = [numeric[name] for name in ("open", "high", "low", "close")]
        if any(value == "" for value in required):
            raise DataQualityError(f"{code} {trade_date} OHLC 缺失")
        open_, high, low, close = map(float, required)
        if min(open_, high, low, close) <= 0 or high < max(open_, close, low) or low > min(open_, close, high):
            raise DataQualityError(f"{code} {trade_date} OHLC 关系异常")
        rows.append({
            "trade_date": trade_date,
            "stock_code": code,
            **numeric,
            "adjustment": "qfq",
        })
    rows.sort(key=lambda item: item["trade_date"])
    write_csv(target, DAILY_COLUMNS, rows)
    return {
        "stock_code": code,
        "rows": len(rows),
        "min_date": rows[0]["trade_date"] if rows else None,
        "max_date": rows[-1]["trade_date"] if rows else None,
    }


def normalize_financial(source: Path, target: Path) -> dict[str, Any]:
    code = stock_code_from_name(source)
    seen_periods: set[str] = set()
    rows: list[dict[str, str]] = []
    missing_cells = 0
    fallback_dates = 0
    for raw in read_csv(source):
        report_period = raw.get("报告期", "").strip()
        date.fromisoformat(report_period)
        if report_period in seen_periods:
            raise DataQualityError(f"{code} 财务报告期重复: {report_period}")
        seen_periods.add(report_period)
        available_date, method = assumed_available_date(report_period)
        if method.startswith("fallback"):
            fallback_dates += 1
        row = {
            "report_period": report_period,
            "available_date_assumed": available_date,
            "stock_code": code,
            "availability_method": method,
        }
        for source_name, target_name in FINANCIAL_FIELD_MAP.items():
            parsed = parse_number(raw.get(source_name))
            if parsed == "":
                missing_cells += 1
            row[target_name] = parsed
        rows.append(row)
    rows.sort(key=lambda item: item["report_period"])
    write_csv(target, FINANCIAL_COLUMNS, rows)
    return {
        "stock_code": code,
        "rows": len(rows),
        "min_period": rows[0]["report_period"] if rows else None,
        "max_period": rows[-1]["report_period"] if rows else None,
        "missing_numeric_cells": missing_cells,
        "fallback_availability_dates": fallback_dates,
    }


def normalize_trade_calendar(source: Path, target: Path) -> dict[str, Any]:
    rows = []
    seen: set[str] = set()
    for raw in read_csv(source):
        trade_date = raw.get("trade_date", "").strip()
        date.fromisoformat(trade_date)
        if trade_date in seen:
            raise DataQualityError(f"交易日历日期重复: {trade_date}")
        seen.add(trade_date)
        rows.append({"trade_date": trade_date})
    rows.sort(key=lambda item: item["trade_date"])
    write_csv(target, ["trade_date"], rows)
    return {"rows": len(rows), "min_date": rows[0]["trade_date"], "max_date": rows[-1]["trade_date"]}


def normalize_benchmark(source: Path, target: Path) -> dict[str, Any]:
    rows = []
    seen: set[str] = set()
    zero_volume_rows = 0
    for raw in read_csv(source):
        trade_date = raw.get("date", "").strip()
        date.fromisoformat(trade_date)
        if trade_date in seen:
            raise DataQualityError(f"沪深300基准日期重复: {trade_date}")
        seen.add(trade_date)
        row = {"trade_date": trade_date}
        for field in ("open", "high", "low", "close", "volume"):
            row[field] = parse_number(raw.get(field))
        if any(row[field] == "" for field in ("open", "high", "low", "close", "volume")):
            raise DataQualityError(f"沪深300基准字段缺失: {trade_date}")
        if float(row["volume"]) == 0:
            zero_volume_rows += 1
        rows.append(row)
    rows.sort(key=lambda item: item["trade_date"])
    columns = ["trade_date", "open", "high", "low", "close", "volume"]
    write_csv(target, columns, rows)
    return {
        "rows": len(rows), "min_date": rows[0]["trade_date"], "max_date": rows[-1]["trade_date"],
        "zero_volume_rows": zero_volume_rows,
    }


def normalize_universe(source: Path, target: Path) -> tuple[dict[str, Any], set[str]]:
    rows = []
    codes: set[str] = set()
    for raw in read_csv(source):
        code = raw.get("code", "").strip().zfill(6)
        if code in codes:
            raise DataQualityError(f"当前股票池代码重复: {code}")
        codes.add(code)
        rows.append({
            "stock_code": code,
            "name": raw.get("name", "").strip(),
            "index_name": raw.get("index", "").strip(),
            "snapshot_import_date": SNAPSHOT_DATE,
            "historical_use_allowed": "false",
        })
    rows.sort(key=lambda item: item["stock_code"])
    columns = ["stock_code", "name", "index_name", "snapshot_import_date", "historical_use_allowed"]
    write_csv(target, columns, rows)
    return {"rows": len(rows)}, codes


def normalize_industry_map(source: Path, target: Path) -> tuple[dict[str, Any], set[str]]:
    with source.open("r", encoding="utf-8-sig") as handle:
        raw_map = json.load(handle)
    rows = []
    for code, values in raw_map.items():
        rows.append({
            "stock_code": str(code).zfill(6),
            "level1_name": values.get("level1_name", ""),
            "level2_name": values.get("level2_name", ""),
            "level2_code": str(values.get("level2_code", "")),
            "snapshot_import_date": SNAPSHOT_DATE,
            "historical_use_allowed": "false",
        })
    rows.sort(key=lambda item: item["stock_code"])
    columns = ["stock_code", "level1_name", "level2_name", "level2_code", "snapshot_import_date", "historical_use_allowed"]
    write_csv(target, columns, rows)
    return {"rows": len(rows)}, {row["stock_code"] for row in rows}


def normalize_sw_level1(source: Path, target: Path) -> dict[str, Any]:
    rows = []
    for raw in read_csv(source):
        rows.append({
            "industry_code": raw.get("行业代码", "").strip(),
            "industry_name": raw.get("行业名称", "").strip(),
            "constituent_count_snapshot": parse_number(raw.get("成份个数")),
            "pe_static_snapshot": parse_number(raw.get("静态市盈率")),
            "pe_ttm_snapshot": parse_number(raw.get("TTM(滚动)市盈率")),
            "pb_snapshot": parse_number(raw.get("市净率")),
            "dividend_yield_percent_snapshot": parse_number(raw.get("静态股息率")),
            "snapshot_import_date": SNAPSHOT_DATE,
            "historical_use_allowed": "false",
        })
    columns = [
        "industry_code", "industry_name", "constituent_count_snapshot", "pe_static_snapshot",
        "pe_ttm_snapshot", "pb_snapshot", "dividend_yield_percent_snapshot",
        "snapshot_import_date", "historical_use_allowed",
    ]
    write_csv(target, columns, rows)
    return {"rows": len(rows)}


def normalize_industry_index(source: Path, target: Path, name_by_code: dict[str, str]) -> dict[str, Any]:
    code = stock_code_from_name(source)
    rows = []
    seen: set[str] = set()
    for raw in read_csv(source):
        trade_date = raw.get("date", "").strip()
        date.fromisoformat(trade_date)
        if trade_date in seen:
            raise DataQualityError(f"行业指数 {code} 日期重复: {trade_date}")
        seen.add(trade_date)
        row = {"trade_date": trade_date, "industry_code": code, "industry_name": name_by_code.get(code, "")}
        for target_name, source_name in {
            "open": "open", "high": "high", "low": "low", "close": "close",
            "volume": "volume", "amount": "成交额",
        }.items():
            row[target_name] = parse_number(raw.get(source_name))
        if any(row[field] == "" for field in ("open", "high", "low", "close")):
            raise DataQualityError(f"行业指数 {code} OHLC 缺失: {trade_date}")
        rows.append(row)
    rows.sort(key=lambda item: item["trade_date"])
    columns = ["trade_date", "industry_code", "industry_name", "open", "high", "low", "close", "volume", "amount"]
    write_csv(target, columns, rows)
    return {
        "industry_code": code, "rows": len(rows),
        "min_date": rows[0]["trade_date"] if rows else None,
        "max_date": rows[-1]["trade_date"] if rows else None,
    }


def output_entries(root: Path) -> list[dict[str, Any]]:
    entries = []
    for path in sorted(root.rglob("*.csv")):
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = max(sum(1 for _ in handle) - 1, 0)
        entries.append({
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "rows": rows,
            "sha256": sha256_file(path),
        })
    return entries


def build(project_root: Path) -> dict[str, Any]:
    snapshot = project_root / "data" / "imports" / SNAPSHOT_NAME
    canonical = project_root / "data" / "canonical"
    reports = project_root / "reports"
    import_manifest = snapshot / "manifest.json"
    required_dirs = [snapshot / "daily_qfq", snapshot / "financial_raw", snapshot / "industry_index", snapshot / "reference"]
    if not import_manifest.is_file() or any(not path.is_dir() for path in required_dirs):
        raise DataQualityError("导入快照或清单缺失")

    daily_summaries = []
    for source in sorted((snapshot / "daily_qfq").glob("*.csv")):
        code = stock_code_from_name(source)
        daily_summaries.append(normalize_daily(source, canonical / "daily_prices" / f"{code}.csv"))

    financial_summaries = []
    for source in sorted((snapshot / "financial_raw").glob("*.csv")):
        code = stock_code_from_name(source)
        financial_summaries.append(normalize_financial(source, canonical / "financial_reports" / f"{code}.csv"))

    reference_in = snapshot / "reference"
    reference_out = canonical / "reference"
    calendar_summary = normalize_trade_calendar(reference_in / "trade_calendar.csv", reference_out / "trade_calendar.csv")
    benchmark_summary = normalize_benchmark(reference_in / "benchmark_000300.csv", reference_out / "benchmark_000300.csv")
    universe_summary, universe_codes = normalize_universe(reference_in / "stock_universe.csv", reference_out / "current_universe.csv")
    industry_map_summary, industry_codes = normalize_industry_map(reference_in / "stock_industry_map_v2.json", reference_out / "current_industry.csv")
    sw_level1_summary = normalize_sw_level1(reference_in / "sw_level1.csv", reference_out / "current_sw_level1.csv")

    name_by_code: dict[str, str] = {}
    for raw in read_csv(reference_in / "sw_level1.csv"):
        code = raw.get("行业代码", "").split(".", 1)[0].strip()
        name_by_code[code] = raw.get("行业名称", "").strip()
    industry_summaries = []
    for source in sorted((snapshot / "industry_index").glob("*.csv")):
        code = stock_code_from_name(source)
        industry_summaries.append(normalize_industry_index(source, canonical / "industry_index" / f"{code}.csv", name_by_code))

    daily_codes = {item["stock_code"] for item in daily_summaries}
    financial_codes = {item["stock_code"] for item in financial_summaries}
    failures = []
    if len(daily_codes) != 300:
        failures.append(f"标准日线股票数应为300，实际{len(daily_codes)}")
    if len(financial_codes) != 574:
        failures.append(f"标准财务股票数应为574，实际{len(financial_codes)}")
    if len(industry_summaries) != 31:
        failures.append(f"行业指数文件数应为31，实际{len(industry_summaries)}")
    if universe_codes - daily_codes:
        failures.append(f"当前股票池缺少日线: {sorted(universe_codes - daily_codes)}")
    if universe_codes - financial_codes:
        failures.append(f"当前股票池缺少财务: {sorted(universe_codes - financial_codes)}")
    if universe_codes - industry_codes:
        failures.append(f"当前股票池缺少行业映射: {sorted(universe_codes - industry_codes)}")
    if failures:
        raise DataQualityError("; ".join(failures))

    quality = {
        "status": "PASS_WITH_KNOWN_LIMITS",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "transform_version": TRANSFORM_VERSION,
        "input_snapshot": SNAPSHOT_NAME,
        "checks": {
            "daily_stock_count": len(daily_codes),
            "daily_rows": sum(item["rows"] for item in daily_summaries),
            "daily_global_min_date": min(item["min_date"] for item in daily_summaries),
            "daily_global_max_date": max(item["max_date"] for item in daily_summaries),
            "financial_stock_count": len(financial_codes),
            "financial_rows": sum(item["rows"] for item in financial_summaries),
            "financial_missing_numeric_cells": sum(item["missing_numeric_cells"] for item in financial_summaries),
            "financial_fallback_availability_dates": sum(item["fallback_availability_dates"] for item in financial_summaries),
            "industry_index_count": len(industry_summaries),
            "industry_index_rows": sum(item["rows"] for item in industry_summaries),
            "current_universe_rows": universe_summary["rows"],
            "current_industry_rows": industry_map_summary["rows"],
            "trade_calendar": calendar_summary,
            "benchmark_000300": benchmark_summary,
            "current_sw_level1": sw_level1_summary,
        },
        "known_limits": [
            "股票日线截止2026-08-07，尚未补齐到当前日期",
            "财务可用日期是保守估算，不是真实公告日期",
            "当前股票池和行业映射禁止用于历史截面",
            "沪深300基准保留源数据中的零成交量记录，早期来源仍需核实",
            "前复权价格不等于当时可观察到的历史价格，正式研究需明确复权使用场景",
        ],
    }

    reports.mkdir(parents=True, exist_ok=True)
    quality_path = reports / "canonical_data_quality.json"
    quality_path.write_text(json.dumps(quality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    entries = output_entries(canonical)
    manifest = {
        "schema_version": 1,
        "transform_version": TRANSFORM_VERSION,
        "generated_at": quality["generated_at"],
        "input": {
            "snapshot": snapshot.relative_to(project_root).as_posix(),
            "manifest_sha256": sha256_file(import_manifest),
        },
        "output": {
            "files": len(entries),
            "bytes": sum(item["bytes"] for item in entries),
            "rows": sum(item["rows"] for item in entries),
            "entries": entries,
        },
        "quality_report": {
            "path": quality_path.relative_to(project_root).as_posix(),
            "sha256": sha256_file(quality_path),
            "status": quality["status"],
        },
    }
    manifest_path = canonical / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"manifest": manifest, "quality": quality}
