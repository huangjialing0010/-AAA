#!/usr/bin/env python
"""抓取 BaoStock 小样本并生成不可覆盖的审计快照。"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGES = PROJECT_ROOT / ".python-packages"
sys.path.insert(0, str(LOCAL_PACKAGES))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.sources.baostock_source import BaoStockSource, QueryResult  # noqa: E402


SAMPLE_STOCKS = ("000001", "600000")
MEMBERSHIP_DATES = ("2015-01-05", "2020-01-02", "2025-01-02", "2026-08-31")
FINANCIAL_PERIODS = ((2025, 4), (2026, 1), (2026, 2))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_result(path: Path, result: QueryResult) -> int:
    if not result.fields:
        raise RuntimeError(f"查询没有返回字段: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=result.fields)
        writer.writeheader()
        writer.writerows(result.rows)
    return len(result.rows)


def add_entry(entries: list[dict[str, Any]], root: Path, path: Path, query: dict[str, Any], rows: int) -> None:
    entries.append({
        "path": path.relative_to(root).as_posix(),
        "query": query,
        "rows": rows,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    })


def validate_daily(result: QueryResult, start_date: str, end_date: str) -> None:
    required = {"date", "code", "open", "high", "low", "close", "volume", "amount", "adjustflag", "tradestatus", "isST"}
    if required - set(result.fields):
        raise RuntimeError(f"日线缺少字段: {sorted(required - set(result.fields))}")
    for row in result.rows:
        if not start_date <= row["date"] <= end_date:
            raise RuntimeError(f"日线日期越界: {row['date']}")
        if row["adjustflag"] != "2":
            raise RuntimeError(f"日线复权标记不是前复权: {row['adjustflag']}")


def validate_members(result: QueryResult, query_date: str) -> None:
    if not {"code", "code_name"}.issubset(result.fields):
        raise RuntimeError(f"{query_date} 成分字段缺失: {result.fields}")
    codes = [row["code"] for row in result.rows]
    if not 250 <= len(codes) <= 350:
        raise RuntimeError(f"{query_date} 成分数量异常: {len(codes)}")
    if len(codes) != len(set(codes)):
        raise RuntimeError(f"{query_date} 成分代码重复")


def validate_profit(result: QueryResult) -> None:
    if not {"code", "pubDate", "statDate"}.issubset(result.fields):
        raise RuntimeError(f"财务字段缺少发布日期: {result.fields}")
    for row in result.rows:
        if not row["pubDate"] or not row["statDate"]:
            raise RuntimeError("非空财务记录缺少 pubDate 或 statDate")
        if date.fromisoformat(row["pubDate"]) <= date.fromisoformat(row["statDate"]):
            raise RuntimeError(f"财务发布日期不晚于报告期: {row}")


def main() -> int:
    now = datetime.now(timezone.utc)
    run_id = now.strftime("baostock_pilot_%Y%m%dT%H%M%SZ")
    output_root = PROJECT_ROOT / "data" / "imports" / run_id
    if output_root.exists():
        raise RuntimeError(f"输出目录已存在，禁止覆盖: {output_root}")
    output_root.mkdir(parents=True)

    entries: list[dict[str, Any]] = []
    # 必须覆盖旧快照末日前的一段重叠区间，用于识别跨来源复权尺度差异。
    daily_start = "2026-07-01"
    daily_end = datetime.now().astimezone().date().isoformat()

    with BaoStockSource() as source:
        for stock_code in SAMPLE_STOCKS:
            result = source.daily_qfq(stock_code, daily_start, daily_end)
            validate_daily(result, daily_start, daily_end)
            path = output_root / "daily_qfq" / f"{stock_code}.csv"
            rows = write_result(path, result)
            add_entry(entries, output_root, path, {
                "api": "query_history_k_data_plus", "stock_code": stock_code,
                "start_date": daily_start, "end_date": daily_end, "frequency": "d", "adjustflag": "2",
            }, rows)

        member_sets: list[set[str]] = []
        for query_date in MEMBERSHIP_DATES:
            result = source.hs300_members(query_date)
            validate_members(result, query_date)
            member_sets.append({row["code"] for row in result.rows})
            path = output_root / "hs300_membership" / f"{query_date}.csv"
            rows = write_result(path, result)
            add_entry(entries, output_root, path, {"api": "query_hs300_stocks", "date": query_date}, rows)
        if len({frozenset(values) for values in member_sets}) < 2:
            raise RuntimeError("不同年份的沪深300成分集合完全相同，历史日期能力验证失败")

        nonempty_financial = 0
        for stock_code in SAMPLE_STOCKS:
            for year, quarter in FINANCIAL_PERIODS:
                result = source.profit(stock_code, year, quarter)
                validate_profit(result)
                if result.rows:
                    nonempty_financial += 1
                path = output_root / "financial_profit" / f"{stock_code}_{year}Q{quarter}.csv"
                rows = write_result(path, result)
                add_entry(entries, output_root, path, {
                    "api": "query_profit_data", "stock_code": stock_code,
                    "year": year, "quarter": quarter,
                }, rows)
        if nonempty_financial < 4:
            raise RuntimeError(f"非空财务样本过少: {nonempty_financial}/6")

    try:
        import baostock
        client_version = getattr(baostock, "__version__", "unknown")
    except ImportError:
        client_version = "unknown"
    manifest = {
        "schema_version": 1,
        "status": "SEALED",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": "BaoStock",
        "client_version": client_version,
        "purpose": "time_semantics_and_connectivity_pilot",
        "files": len(entries),
        "rows": sum(entry["rows"] for entry in entries),
        "bytes": sum(entry["bytes"] for entry in entries),
        "entries": entries,
        "limits": [
            "小样本只证明本次查询可用，不证明全历史完整",
            "pubDate仍需与交易所公告抽样交叉核对",
            "BaoStock免费服务可能限频、断线或变更字段",
        ],
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "snapshot": str(output_root), "status": manifest["status"],
        "files": manifest["files"], "rows": manifest["rows"], "bytes": manifest["bytes"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
