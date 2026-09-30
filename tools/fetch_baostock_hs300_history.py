#!/usr/bin/env python
"""按周抓取沪深300历史成员响应，先封存原始查询，不构造生效区间。"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / ".python-packages"))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.sources.baostock_source import RetryingBaoStockSource  # noqa: E402


SOURCE_COVERAGE_START = date(2006, 1, 4)
MIN_UNIQUE_UPDATE_DATES = 100
MIN_DISTINCT_MEMBER_SETS = 30


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def weekly_query_dates(end_date: date) -> list[str]:
    calendar_path = PROJECT_ROOT / "data" / "canonical" / "reference" / "trade_calendar.csv"
    with calendar_path.open("r", encoding="utf-8", newline="") as handle:
        dates = [date.fromisoformat(row["trade_date"]) for row in csv.DictReader(handle)]
    eligible = [value for value in dates if SOURCE_COVERAGE_START <= value <= end_date]
    first_by_week: dict[tuple[int, int], date] = {}
    for value in eligible:
        iso = value.isocalendar()
        first_by_week.setdefault((iso.year, iso.week), value)
    return [value.isoformat() for value in sorted(first_by_week.values())]


def main() -> int:
    started = datetime.now(timezone.utc)
    run_id = started.strftime("baostock_hs300_history_%Y%m%dT%H%M%SZ")
    output_root = PROJECT_ROOT / "data" / "imports" / run_id
    if output_root.exists():
        raise RuntimeError(f"输出目录已存在，禁止覆盖: {output_root}")
    output_root.mkdir(parents=True)
    query_dates = weekly_query_dates(datetime.now().astimezone().date())
    output_path = output_root / "hs300_membership_weekly.csv"
    gap_path = output_root / "coverage_gaps.csv"
    rows_written = 0
    update_dates: set[str] = set()
    distinct_sets: set[frozenset[str]] = set()
    gaps: list[dict[str, str]] = []
    fields = ["query_date", "updateDate", "code", "code_name"]

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        with RetryingBaoStockSource(max_queries_per_session=25) as source:
            for index, query_date in enumerate(query_dates, start=1):
                result = source.hs300_members(query_date)
                if not {"updateDate", "code", "code_name"}.issubset(result.fields):
                    raise RuntimeError(f"{query_date} 成分字段缺失: {result.fields}")
                codes = [row["code"] for row in result.rows]
                if not codes:
                    gaps.append({
                        "query_date": query_date,
                        "reason": "source_returned_empty_response",
                    })
                    print(f"coverage_gap {query_date}: empty response", flush=True)
                    continue
                if not 250 <= len(codes) <= 350:
                    raise RuntimeError(f"{query_date} 成分数量异常: {len(codes)}")
                if len(codes) != len(set(codes)):
                    raise RuntimeError(f"{query_date} 成分代码重复")
                response_dates = {row["updateDate"] for row in result.rows}
                if len(response_dates) != 1:
                    raise RuntimeError(f"{query_date} 返回多个 updateDate: {sorted(response_dates)}")
                update_date = next(iter(response_dates))
                if date.fromisoformat(update_date) > date.fromisoformat(query_date):
                    raise RuntimeError(f"{query_date} 返回未来 updateDate: {update_date}")
                update_dates.add(update_date)
                distinct_sets.add(frozenset(codes))
                for row in result.rows:
                    writer.writerow({
                        "query_date": query_date,
                        "updateDate": row["updateDate"],
                        "code": row["code"],
                        "code_name": row["code_name"],
                    })
                    rows_written += 1
                if index % 50 == 0 or index == len(query_dates):
                    print(f"progress {index}/{len(query_dates)}", flush=True)

    with gap_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["query_date", "reason"])
        writer.writeheader()
        writer.writerows(gaps)

    if len(update_dates) < MIN_UNIQUE_UPDATE_DATES:
        raise RuntimeError(f"历史更新日期过少: {len(update_dates)}")
    if len(distinct_sets) < MIN_DISTINCT_MEMBER_SETS:
        raise RuntimeError(f"历史成员集合变化过少: {len(distinct_sets)}")

    manifest = {
        "schema_version": 1,
        "status": "SEALED" if not gaps else "SEALED_WITH_GAPS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": "BaoStock",
        "purpose": "weekly_hs300_historical_membership_responses",
        "query_dates": len(query_dates),
        "unique_update_dates": len(update_dates),
        "distinct_member_sets": len(distinct_sets),
        "successful_query_dates": len(query_dates) - len(gaps),
        "coverage_gap_dates": [gap["query_date"] for gap in gaps],
        "coverage_gap_count": len(gaps),
        "files": 2,
        "rows": rows_written,
        "bytes": output_path.stat().st_size + gap_path.stat().st_size,
        "entries": [
            {
                "path": output_path.name,
                "rows": rows_written,
                "bytes": output_path.stat().st_size,
                "sha256": sha256_file(output_path),
            },
            {
                "path": gap_path.name,
                "rows": len(gaps),
                "bytes": gap_path.stat().st_size,
                "sha256": sha256_file(gap_path),
            },
        ],
        "query_range": {"start": query_dates[0], "end": query_dates[-1]},
        "limits": [
            "按周查询可能遗漏周中临时调整",
            "BaoStock在2005年返回空响应，因此本快照从2006-01-04开始",
            "空响应日期单独记录为coverage gap，不得静默前向填充",
            "尚未与中证指数官方调整公告交叉核对",
            "该快照尚未生成标准层生效区间",
        ],
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "snapshot": str(output_root), "status": manifest["status"],
        "query_dates": manifest["query_dates"], "unique_update_dates": manifest["unique_update_dates"],
        "coverage_gap_count": manifest["coverage_gap_count"], "rows": manifest["rows"],
        "elapsed_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
