#!/usr/bin/env python
"""抓取观察名单2026Q2盈利记录，封存为二次核对原始快照。"""

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

from ashare_lab.sources.baostock_source import BaoStockSource, QueryResult, RetryingBaoStockSource  # noqa: E402


PLAN_PATH = PROJECT_ROOT / "docs" / "CURRENT_QUALITY_FORWARD_PILOT_V1.md"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_result(path: Path, result: QueryResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=result.fields)
        writer.writeheader()
        writer.writerows(result.rows)


def validate_profit(result: QueryResult, code: str, cutoff: str) -> None:
    required = {"code", "pubDate", "statDate", "netProfit"}
    if required - set(result.fields) or len(result.rows) != 1:
        raise RuntimeError(f"{code} 2026Q2盈利记录字段或行数异常")
    row = result.rows[0]
    if row["code"].split(".")[-1] != code or row["statDate"] != "2026-06-30":
        raise RuntimeError(f"{code} 证券或报告期不一致")
    if not row["pubDate"] or not date.fromisoformat(row["statDate"]) < date.fromisoformat(row["pubDate"]) <= date.fromisoformat(cutoff):
        raise RuntimeError(f"{code} 发布日期不符合截止日语义")
    try:
        if float(row["netProfit"]) <= 0:
            raise RuntimeError(f"{code} BaoStock netProfit非正")
    except ValueError as exc:
        raise RuntimeError(f"{code} BaoStock netProfit不可解析") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("watchlist_report", type=Path)
    args = parser.parse_args()
    report = args.watchlist_report.resolve()
    report_manifest_path = report / "manifest.json"
    report_manifest = json.loads(report_manifest_path.read_text(encoding="utf-8"))
    if report_manifest.get("status") != "GENERATED_FORWARD_WATCH_BASELINE_NOT_EXECUTABLE":
        raise RuntimeError("观察基线状态错误")
    watch_entry = next(item for item in report_manifest["entries"] if item["path"] == "watchlist.csv")
    watch_path = report / "watchlist.csv"
    if watch_path.stat().st_size != watch_entry["bytes"] or sha256_file(watch_path) != watch_entry["sha256"]:
        raise RuntimeError("观察名单文件与清单不一致")
    watchlist = read_csv(watch_path)
    if not watchlist or any(row.get("watch_status") != "FORWARD_WATCH_ONLY" for row in watchlist):
        raise RuntimeError("观察名单为空或缺少前向观察标记")

    now = datetime.now(timezone.utc)
    output = PROJECT_ROOT / "data" / "imports" / now.strftime("baostock_watchlist_profit_%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, Any]] = []
    with RetryingBaoStockSource(
        source_factory=lambda: BaoStockSource(socket_timeout_seconds=45.0)
    ) as source:
        for row in watchlist:
            code = row["stock_code"]
            result = source.profit(code, 2026, 2)
            validate_profit(result, code, report_manifest["cutoff_date"])
            path = output / "financial_profit" / f"{code}_2026Q2.csv"
            write_result(path, result)
            entries.append({
                "path": path.relative_to(output).as_posix(),
                "stock_code": code,
                "query": {"api": "query_profit_data", "stock_code": code, "year": 2026, "quarter": 2},
                "rows": len(result.rows),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })

    manifest = {
        "schema_version": 1,
        "status": "SEALED",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "BaoStock",
        "purpose": "watchlist_2026Q2_positive_profit_crosscheck",
        "files": len(entries),
        "rows": sum(item["rows"] for item in entries),
        "watchlist_count": len(watchlist),
        "cutoff_date": report_manifest["cutoff_date"],
        "lineage": {
            "watchlist_report": report.name,
            "watchlist_manifest_sha256": sha256_file(report_manifest_path),
            "watchlist_csv_sha256": watch_entry["sha256"],
        },
        "plan": {"path": PLAN_PATH.relative_to(PROJECT_ROOT).as_posix(), "sha256": sha256_file(PLAN_PATH)},
        "implementation": {"fetcher_sha256": sha256_file(Path(__file__).resolve())},
        "entries": entries,
        "limits": [
            "BaoStock netProfit字段只用于本轮正值与数值交叉核对，不永久等同归母净利润",
            "五只小样本不能证明全市场字段语义",
            "快照限本地研究，不公开再分发",
        ],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "SEALED", "snapshot": str(output), "files": len(entries), "rows": manifest["rows"]
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
