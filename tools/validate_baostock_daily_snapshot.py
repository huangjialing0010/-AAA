#!/usr/bin/env python
"""独立验证已封存的 BaoStock 双口径日线快照。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FIELDS = {
    "date", "code", "open", "high", "low", "close", "preclose", "volume", "amount",
    "adjustflag", "turn", "tradestatus", "pctChg", "isST",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path: Path) -> tuple[list[str], dict[str, dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    by_date = {row["date"]: row for row in rows}
    if len(by_date) != len(rows):
        raise RuntimeError(f"日期重复: {path}")
    return fields, by_date


def latest_sealed_snapshot() -> Path:
    candidates = []
    for path in (PROJECT_ROOT / "data" / "imports").glob("baostock_daily_*"):
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") == "SEALED":
            candidates.append(path)
    if not candidates:
        raise RuntimeError("没有已封存的 BaoStock 日线快照")
    return sorted(candidates)[-1]


def compare_old_qfq(code: str, new_rows: dict[str, dict[str, str]]) -> dict[str, Any]:
    old_path = PROJECT_ROOT / "data" / "canonical" / "daily_prices" / f"{code}.csv"
    with old_path.open("r", encoding="utf-8", newline="") as handle:
        old_rows = {row["trade_date"]: row for row in csv.DictReader(handle)}
    common_dates = sorted(set(old_rows) & set(new_rows))
    if not common_dates:
        return {"common_dates": 0}
    close_relative_diffs = []
    return_diffs = []
    exact_close_dates = 0
    for value in common_dates:
        old_close = float(old_rows[value]["close"])
        new_close = float(new_rows[value]["close"])
        if old_close == new_close:
            exact_close_dates += 1
        close_relative_diffs.append(abs(new_close / old_close - 1))
    for previous, current in zip(common_dates, common_dates[1:]):
        old_return = float(old_rows[current]["close"]) / float(old_rows[previous]["close"]) - 1
        new_return = float(new_rows[current]["close"]) / float(new_rows[previous]["close"]) - 1
        return_diffs.append(abs(old_return - new_return))
    last_date = common_dates[-1]
    return {
        "common_dates": len(common_dates),
        "min_date": common_dates[0],
        "max_date": last_date,
        "exact_close_dates": exact_close_dates,
        "max_close_relative_diff": max(close_relative_diffs),
        "max_daily_return_diff": max(return_diffs, default=0.0),
        "last_close_relative_diff": abs(
            float(new_rows[last_date]["close"]) / float(old_rows[last_date]["close"]) - 1
        ),
    }


def validate(snapshot: Path) -> dict[str, Any]:
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SEALED" or manifest.get("files") != 600:
        raise RuntimeError("快照未封存或文件数不是600")
    hash_errors = []
    for entry in manifest["entries"]:
        path = snapshot / Path(entry["path"])
        if not path.is_file() or path.stat().st_size != entry["bytes"] or sha256_file(path) != entry["sha256"]:
            hash_errors.append(entry["path"])
    if hash_errors:
        raise RuntimeError(f"清单哈希或大小不一致: {hash_errors[:10]}")

    codes = sorted(path.stem for path in (snapshot / "daily_qfq").glob("*.csv"))
    if len(codes) != 300:
        raise RuntimeError(f"前复权文件数不是300: {len(codes)}")
    issues = []
    comparisons = []
    latest_dates = []
    total_rows = 0
    for code in codes:
        q_fields, q_rows = load_rows(snapshot / "daily_qfq" / f"{code}.csv")
        u_fields, u_rows = load_rows(snapshot / "daily_unadjusted" / f"{code}.csv")
        if REQUIRED_FIELDS - set(q_fields) or REQUIRED_FIELDS - set(u_fields):
            issues.append(f"{code}:字段缺失")
            continue
        if set(q_rows) != set(u_rows):
            issues.append(f"{code}:复权与不复权日期集合不同")
            continue
        for trade_date in q_rows:
            q_row, u_row = q_rows[trade_date], u_rows[trade_date]
            if q_row["adjustflag"] != "2" or u_row["adjustflag"] != "3":
                issues.append(f"{code}:{trade_date}:复权标记错误")
                break
            for field in ("volume", "amount", "tradestatus", "isST"):
                if q_row[field] != u_row[field]:
                    issues.append(f"{code}:{trade_date}:{field}跨口径不一致")
                    break
            if q_row["tradestatus"] == "1":
                try:
                    values = [float(q_row[field]) for field in ("open", "high", "low", "close")]
                except ValueError:
                    issues.append(f"{code}:{trade_date}:正常交易日OHLC不可解析")
                    break
                if min(values) <= 0 or not all(math.isfinite(value) for value in values):
                    issues.append(f"{code}:{trade_date}:正常交易日OHLC异常")
                    break
        if q_rows:
            latest_dates.append(max(q_rows))
        total_rows += len(q_rows) + len(u_rows)
        comparisons.append({"stock_code": code, **compare_old_qfq(code, q_rows)})
    if issues:
        raise RuntimeError(f"日线质量错误: {issues[:10]}")

    latest_counts: dict[str, int] = {}
    for value in latest_dates:
        latest_counts[value] = latest_counts.get(value, 0) + 1
    report = {
        "status": "PASS_WITH_KNOWN_LIMITS",
        "validated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "snapshot": snapshot.name,
        "manifest_files": manifest["files"],
        "manifest_hash_errors": 0,
        "stock_count": len(codes),
        "total_rows_both_adjustments": total_rows,
        "latest_date_counts": latest_counts,
        "old_qfq_overlap": {
            "stocks": len(comparisons),
            "exact_last_close_stocks": sum(item.get("last_close_relative_diff") == 0 for item in comparisons),
            "max_last_close_relative_diff": max(item.get("last_close_relative_diff", 0) for item in comparisons),
            "max_daily_return_diff": max(item.get("max_daily_return_diff", 0) for item in comparisons),
            "details": comparisons,
        },
        "known_limits": [
            "股票集合仍是当前300只，不是历史沪深300成分",
            "前复权历史会随未来公司行动回溯变化",
            "与旧前复权来源的差异只用于评估，不能自动拼接",
        ],
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 BaoStock 双口径日线快照")
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    snapshot = args.snapshot.resolve() if args.snapshot else latest_sealed_snapshot()
    report = validate(snapshot)
    report_path = PROJECT_ROOT / "reports" / "baostock_daily_snapshot_quality.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"], "snapshot": report["snapshot"],
        "stock_count": report["stock_count"], "total_rows": report["total_rows_both_adjustments"],
        "latest_date_counts": report["latest_date_counts"],
        "exact_last_close_stocks": report["old_qfq_overlap"]["exact_last_close_stocks"],
        "max_last_close_relative_diff": report["old_qfq_overlap"]["max_last_close_relative_diff"],
        "max_daily_return_diff": report["old_qfq_overlap"]["max_daily_return_diff"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
