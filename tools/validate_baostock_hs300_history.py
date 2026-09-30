#!/usr/bin/env python
"""独立校验最新的 BaoStock 沪深300周频历史成分快照。"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORT_ROOT = PROJECT_ROOT / "data" / "imports"
REPORT_PATH = PROJECT_ROOT / "reports" / "baostock_hs300_history_quality.json"
SOURCE_COVERAGE_START = date(2006, 1, 4)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_weekly_dates(end_date: date) -> list[str]:
    calendar_path = PROJECT_ROOT / "data" / "canonical" / "reference" / "trade_calendar.csv"
    with calendar_path.open("r", encoding="utf-8", newline="") as handle:
        values = [date.fromisoformat(row["trade_date"]) for row in csv.DictReader(handle)]
    first_by_week: dict[tuple[int, int], date] = {}
    for value in values:
        if SOURCE_COVERAGE_START <= value <= end_date:
            iso = value.isocalendar()
            first_by_week.setdefault((iso.year, iso.week), value)
    return [value.isoformat() for value in sorted(first_by_week.values())]


def latest_snapshot() -> Path:
    candidates = []
    for path in IMPORT_ROOT.glob("baostock_hs300_history_*"):
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("status") in {"SEALED", "SEALED_WITH_GAPS"}:
            candidates.append(path)
    if not candidates:
        raise RuntimeError("没有可校验的沪深300历史成分快照")
    return sorted(candidates)[-1]


def main() -> int:
    snapshot = latest_snapshot()
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    errors: list[str] = []

    for entry in manifest.get("entries", []):
        path = snapshot / entry["path"]
        if not path.is_file():
            errors.append(f"清单文件不存在: {entry['path']}")
            continue
        if path.stat().st_size != entry["bytes"]:
            errors.append(f"文件大小不一致: {entry['path']}")
        if sha256_file(path) != entry["sha256"]:
            errors.append(f"SHA256不一致: {entry['path']}")

    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    membership_path = snapshot / "hs300_membership_weekly.csv"
    with membership_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            groups[row["query_date"]].append(row)

    update_dates: set[str] = set()
    member_sets: set[frozenset[str]] = set()
    for query_date, rows in groups.items():
        codes = [row["code"] for row in rows]
        if not 250 <= len(codes) <= 350:
            errors.append(f"{query_date} 成分数量异常: {len(codes)}")
        if len(codes) != len(set(codes)):
            errors.append(f"{query_date} 成分代码重复")
        response_dates = {row["updateDate"] for row in rows}
        if len(response_dates) != 1:
            errors.append(f"{query_date} updateDate数量异常: {len(response_dates)}")
        else:
            update_date = next(iter(response_dates))
            update_dates.add(update_date)
            if date.fromisoformat(update_date) > date.fromisoformat(query_date):
                errors.append(f"{query_date} 返回未来updateDate: {update_date}")
        member_sets.add(frozenset(codes))

    gap_path = snapshot / "coverage_gaps.csv"
    with gap_path.open("r", encoding="utf-8", newline="") as handle:
        gap_dates = [row["query_date"] for row in csv.DictReader(handle)]
    if set(groups) & set(gap_dates):
        errors.append("有效响应日期与覆盖缺口日期重叠")

    end_date = date.fromisoformat(manifest["query_range"]["end"])
    expected = expected_weekly_dates(end_date)
    observed = sorted(set(groups) | set(gap_dates))
    if observed != expected:
        errors.append("有效响应与缺口的并集未覆盖全部计划查询日")

    row_count = sum(len(rows) for rows in groups.values())
    checks = {
        "query_dates": len(expected),
        "successful_query_dates": len(groups),
        "coverage_gap_count": len(gap_dates),
        "rows": row_count,
        "unique_update_dates": len(update_dates),
        "distinct_member_sets": len(member_sets),
    }
    for key, actual in checks.items():
        if manifest.get(key) != actual:
            errors.append(f"清单统计不一致: {key} manifest={manifest.get(key)} actual={actual}")

    expected_status = "SEALED_WITH_GAPS" if gap_dates else "SEALED"
    if manifest.get("status") != expected_status:
        errors.append(f"快照状态不一致: 应为{expected_status}")

    report = {
        "status": "FAIL" if errors else ("PASS_WITH_KNOWN_GAPS" if gap_dates else "PASS"),
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "snapshot": snapshot.name,
        **checks,
        "coverage_gap_dates": gap_dates,
        "errors": errors,
        "known_limits": manifest.get("limits", []),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
