#!/usr/bin/env python
"""独立验收当前质量观察基线的文件、阈值、排序和非交易边界。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
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


def number(row: dict[str, str], field: str) -> float:
    return float(row[field])


def independently_select(candidates: list[dict[str, str]]) -> list[str]:
    active = [row for row in candidates if row["status"] == "BASELINE_TRIGGER"]
    active.sort(key=lambda row: (
        -number(row, "roe_3y_median"),
        -number(row, "cash_profit_per_share_proxy"),
        row["stock_code"],
    ))
    counts = {"L1": 0, "R1": 0}
    industries: set[str] = set()
    selected: list[str] = []
    for row in active:
        mechanism = row["mechanism"]
        industry = row["level1_industry"]
        if counts[mechanism] >= 4 or industry in industries:
            continue
        selected.append(row["stock_code"])
        counts[mechanism] += 1
        industries.add(industry)
    return selected


def validate(output: Path) -> dict[str, Any]:
    errors: list[str] = []
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    expected_status = "GENERATED_FORWARD_WATCH_BASELINE_NOT_EXECUTABLE"
    if manifest.get("status") != expected_status:
        errors.append("报告状态不是不可执行前向基线")
    if manifest.get("orders_created") != 0 or manifest.get("trades_created") != 0:
        errors.append("基线报告错误地产生了订单或成交")
    expected_files = {"manifest.json", "candidates.csv", "watchlist.csv", "README.md"}
    actual_files = {path.name for path in output.iterdir() if path.is_file()}
    if actual_files != expected_files:
        errors.append(f"报告目录文件集合异常: {sorted(actual_files)}")
    for item in manifest.get("entries", []):
        path = output / item["path"]
        if not path.is_file() or path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            errors.append(f"输出文件大小或哈希不一致: {item['path']}")

    source_manifest = PROJECT_ROOT / "data" / "imports" / manifest["source"]["snapshot"] / "manifest.json"
    if (
        not source_manifest.is_file()
        or sha256_file(source_manifest) != manifest["source"]["manifest_sha256"]
        or json.loads(source_manifest.read_text(encoding="utf-8")).get("status") != "SEALED"
    ):
        errors.append("来源快照清单缺失、变化或未封存")
    for section, key in (("plan", "path"),):
        item = manifest.get(section, {})
        path = PROJECT_ROOT / item.get(key, "")
        if not path.is_file() or sha256_file(path) != item.get("sha256"):
            errors.append(f"{section} 文件哈希不一致")
    for key, relative in (
        ("runner_sha256", "tools/build_current_quality_watchlist.py"),
        ("module_sha256", "src/ashare_lab/selection/current_watchlist.py"),
    ):
        if sha256_file(PROJECT_ROOT / relative) != manifest.get("implementation", {}).get(key):
            errors.append(f"实现文件哈希不一致: {relative}")

    candidates = read_csv(output / "candidates.csv")
    watchlist = read_csv(output / "watchlist.csv")
    codes = [row["stock_code"] for row in candidates]
    if len(candidates) != 139 or len(set(codes)) != 139:
        errors.append("候选数量或代码唯一性错误")
    if {row["cutoff_date"] for row in candidates} != {manifest.get("cutoff_date")}:
        errors.append("候选截止日不一致")

    for row in candidates:
        if row["status"] != "BASELINE_TRIGGER":
            continue
        if number(row, "positive_pe_observations") < 504 or number(row, "positive_pb_observations") < 504:
            errors.append(f"{row['stock_code']} 估值观测不足却被标为触发")
        if row["mechanism"] == "L1":
            valid = (
                number(row, "current_pe_ttm") <= number(row, "pe_p20")
                and number(row, "current_pb_mrq") <= number(row, "pb_median")
                and number(row, "drawdown_from_prior_252_high") <= -0.20
            )
        elif row["mechanism"] == "R1":
            valid = (
                number(row, "current_pe_ttm") <= number(row, "pe_p80")
                and number(row, "breakout_above_prior_120_high") > 0
                and number(row, "ma60_change_vs_20_days_ago") > 0
            )
        else:
            valid = False
        if not valid:
            errors.append(f"{row['stock_code']} 触发标签与冻结阈值不符")

    expected_selected = independently_select(candidates)
    actual_selected = [row["stock_code"] for row in watchlist]
    if actual_selected != expected_selected:
        errors.append("观察名单与独立质量排序及席位约束不一致")
    selected_flags = [row["stock_code"] for row in candidates if row["watchlist_selected"] == "true"]
    if set(selected_flags) != set(actual_selected):
        errors.append("候选表入选标记与观察名单不一致")
    if [int(row["watch_rank"]) for row in watchlist] != list(range(1, len(watchlist) + 1)):
        errors.append("观察名单排名不连续")
    if any(row.get("watch_status") != "FORWARD_WATCH_ONLY" for row in watchlist):
        errors.append("观察名单缺少 FORWARD_WATCH_ONLY 标记")
    if len({row["level1_industry"] for row in watchlist}) != len(watchlist):
        errors.append("观察名单违反每行业最多一只")
    mechanism_counts = Counter(row["mechanism"] for row in watchlist)
    if any(count > 4 for count in mechanism_counts.values()):
        errors.append("观察名单违反每类最多四只")
    if manifest.get("candidate_count") != len(candidates) or manifest.get("watchlist_count") != len(watchlist):
        errors.append("清单计数与CSV不一致")

    return {
        "status": "PASS" if not errors else "FAIL",
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "output": output.name,
        "candidate_count": len(candidates),
        "baseline_trigger_count": sum(row["status"] == "BASELINE_TRIGGER" for row in candidates),
        "review_count": sum(row["status"] == "REVIEW" for row in candidates),
        "watchlist_count": len(watchlist),
        "mechanism_counts": dict(sorted(mechanism_counts.items())),
        "watchlist_codes": actual_selected,
        "orders_created": manifest.get("orders_created"),
        "trades_created": manifest.get("trades_created"),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = validate(args.output.resolve())
    if args.report:
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
