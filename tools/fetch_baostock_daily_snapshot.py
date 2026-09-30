#!/usr/bin/env python
"""抓取当前研究股票池的完整不复权和前复权日线快照。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / ".python-packages"))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.sources.baostock_source import QueryResult, RetryingBaoStockSource  # noqa: E402


DEFAULT_START_DATE = "1990-12-19"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_universe() -> list[str]:
    path = PROJECT_ROOT / "data" / "canonical" / "reference" / "current_universe.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        codes = sorted({row["stock_code"] for row in csv.DictReader(handle)})
    if len(codes) != 300:
        raise RuntimeError(f"当前研究股票池应为300只，实际{len(codes)}只")
    return codes


def validate_result(
    result: QueryResult,
    stock_code: str,
    adjustflag: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    required = {
        "date", "code", "open", "high", "low", "close", "preclose", "volume", "amount",
        "adjustflag", "turn", "tradestatus", "pctChg", "isST",
    }
    if required - set(result.fields):
        raise RuntimeError(f"{stock_code} 日线缺少字段: {sorted(required - set(result.fields))}")
    dates = [row["date"] for row in result.rows]
    if not dates:
        raise RuntimeError(f"{stock_code} 没有返回日线")
    if len(dates) != len(set(dates)):
        raise RuntimeError(f"{stock_code} 日线日期重复")
    if any(row["adjustflag"] != adjustflag for row in result.rows):
        raise RuntimeError(f"{stock_code} 复权标记与请求不一致")
    for value in dates:
        date.fromisoformat(value)
        if not start_date <= value <= end_date:
            raise RuntimeError(f"{stock_code} 返回日期越界: {value}")
    if len(result.rows) % 2000 == 0 and max(dates) < end_date:
        raise RuntimeError(
            f"{stock_code} 返回{len(result.rows)}行且末日{max(dates)}早于请求末日{end_date}，疑似分页截断"
        )
    return {"min_date": min(dates), "max_date": max(dates)}


def write_result(path: Path, result: QueryResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=result.fields)
        writer.writeheader()
        writer.writerows(result.rows)


def read_existing(path: Path) -> QueryResult:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    return QueryResult(fields=fields, rows=rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取当前研究股票池的 BaoStock 双口径日线")
    parser.add_argument(
        "--resume",
        type=Path,
        help="续跑一个没有 manifest.json 的 baostock_daily_* 目录；已有文件只读复核，不覆盖",
    )
    parser.add_argument("--start-date", default=None, help="查询起始日，默认1990-12-19")
    parser.add_argument("--end-date", default=None, help="查询截止日，默认运行日；续跑必须与run_state一致")
    args = parser.parse_args()
    started = datetime.now(timezone.utc)
    imports_root = (PROJECT_ROOT / "data" / "imports").resolve()
    state_path: Path
    if args.resume:
        output_root = args.resume.resolve()
        if output_root.parent != imports_root or not output_root.name.startswith("baostock_daily_"):
            raise RuntimeError(f"续跑目录不在允许范围: {output_root}")
        if not output_root.is_dir():
            raise RuntimeError(f"续跑目录不存在: {output_root}")
        if (output_root / "manifest.json").exists():
            raise RuntimeError(f"快照已经封存，禁止续跑: {output_root}")
        state_path = output_root / "run_state.json"
        if not state_path.is_file():
            raise RuntimeError(f"续跑目录缺少run_state.json，不能证明统一查询区间: {output_root}")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        start_date = state["start_date"]
        end_date = state["end_date"]
        if args.start_date and args.start_date != start_date:
            raise RuntimeError("--start-date 与run_state不一致")
        if args.end_date and args.end_date != end_date:
            raise RuntimeError("--end-date 与run_state不一致")
    else:
        run_id = started.strftime("baostock_daily_%Y%m%dT%H%M%SZ")
        output_root = imports_root / run_id
        if output_root.exists():
            raise RuntimeError(f"输出目录已存在，禁止覆盖: {output_root}")
        output_root.mkdir(parents=True)
        start_date = args.start_date or DEFAULT_START_DATE
        end_date = args.end_date or datetime.now().astimezone().date().isoformat()
        date.fromisoformat(start_date)
        date.fromisoformat(end_date)
        if start_date > end_date:
            raise RuntimeError("查询起始日晚于截止日")
        state_path = output_root / "run_state.json"
        state_path.write_text(json.dumps({
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "start_date": start_date,
            "end_date": end_date,
            "target_stock_count": 300,
            "adjustflags": ["3", "2"],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    codes = load_universe()
    entries: list[dict[str, Any]] = []

    with RetryingBaoStockSource(
        max_attempts=4,
        backoff_seconds=(5, 15, 30),
        max_queries_per_session=25,
    ) as source:
        completed = 0
        total = len(codes) * 2
        for adjustflag, category in (("3", "daily_unadjusted"), ("2", "daily_qfq")):
            for stock_code in codes:
                path = output_root / category / f"{stock_code}.csv"
                if path.exists():
                    result = read_existing(path)
                    reused = True
                else:
                    result = source.daily(stock_code, start_date, end_date, adjustflag=adjustflag)
                    write_result(path, result)
                    reused = False
                dates = validate_result(result, stock_code, adjustflag, start_date, end_date)
                entries.append({
                    "path": path.relative_to(output_root).as_posix(),
                    "category": category,
                    "stock_code": stock_code,
                    "query": {
                        "api": "query_history_k_data_plus", "start_date": start_date,
                        "end_date": end_date, "frequency": "d", "adjustflag": adjustflag,
                    },
                    "rows": len(result.rows),
                    "min_date": dates["min_date"],
                    "max_date": dates["max_date"],
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                    "reused_from_incomplete_run": reused,
                })
                completed += 1
                if completed % 25 == 0 or completed == total:
                    print(f"progress {completed}/{total}", flush=True)

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
        "purpose": "single_source_daily_window_for_current_research_universe",
        "query_range": {"start_date": start_date, "end_date": end_date},
        "universe_semantics": "current_snapshot_only_not_historical_membership",
        "files": len(entries),
        "rows": sum(entry["rows"] for entry in entries),
        "bytes": sum(entry["bytes"] for entry in entries),
        "entries": entries,
        "limits": [
            "股票集合是2026-09-01导入的当前沪深300截面，不能用于历史股票池",
            "前复权序列会随未来公司行动回溯变化",
            "该快照尚未转换或替换现有标准层",
        ],
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "snapshot": str(output_root), "status": manifest["status"], "files": manifest["files"],
        "rows": manifest["rows"], "bytes": manifest["bytes"],
        "elapsed_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
