#!/usr/bin/env python
"""独立校验当前质量候选的BaoStock估值快照。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def validate(snapshot: Path) -> dict[str, Any]:
    errors: list[str] = []
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "SEALED" or manifest.get("candidate_count") != 139:
        errors.append("快照状态或候选数量错误")
    entries = manifest.get("entries", [])
    for item in entries:
        path = snapshot / item["path"]
        if not path.is_file() or path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            errors.append(f"文件大小或哈希不一致: {item['path']}")
    scope = read_csv(snapshot / "query_scope.csv")
    codes = [row["stock_code"] for row in scope]
    if len(codes) != 139 or len(set(codes)) != len(codes):
        errors.append("查询范围代码数量或唯一性错误")

    by_code: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for item in entries:
        if item.get("stock_code"):
            by_code[item["stock_code"]][item["category"]] = item
    positive_pe_counts: dict[str, int] = {}
    positive_pb_counts: dict[str, int] = {}
    latest_dates: Counter[str] = Counter()
    for code in codes:
        pair = by_code.get(code, {})
        if set(pair) != {"daily_unadjusted_valuation", "daily_qfq_valuation"}:
            errors.append(f"{code} 双口径文件不完整")
            continue
        raw = read_csv(snapshot / pair["daily_unadjusted_valuation"]["path"])
        qfq = read_csv(snapshot / pair["daily_qfq_valuation"]["path"])
        raw_by = {row["date"]: row for row in raw}
        qfq_by = {row["date"]: row for row in qfq}
        if len(raw_by) != len(raw) or len(qfq_by) != len(qfq) or set(raw_by) != set(qfq_by):
            errors.append(f"{code} 日期重复或双口径日期不一致")
            continue
        for day in raw_by:
            for field in ("volume", "amount", "tradestatus", "peTTM", "pbMRQ", "isST"):
                if raw_by[day][field] != qfq_by[day][field]:
                    errors.append(f"{code} {day} 双口径{field}不一致")
                    break
        positive_pe_counts[code] = sum(bool(row["peTTM"]) and float(row["peTTM"]) > 0 for row in raw)
        positive_pb_counts[code] = sum(bool(row["pbMRQ"]) and float(row["pbMRQ"]) > 0 for row in raw)
        latest_dates[max(raw_by)] += 1

    return {
        "status": "PASS" if not errors else "FAIL",
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "snapshot": snapshot.name,
        "candidate_count": len(codes),
        "dual_price_files": sum(len(value) for value in by_code.values()),
        "latest_date_distribution": dict(sorted(latest_dates.items())),
        "codes_with_at_least_504_positive_pe": sum(value >= 504 for value in positive_pe_counts.values()),
        "codes_with_at_least_504_positive_pb": sum(value >= 504 for value in positive_pb_counts.values()),
        "errors": errors,
        "limits": manifest.get("limits", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = validate(args.snapshot.resolve())
    if args.report:
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
