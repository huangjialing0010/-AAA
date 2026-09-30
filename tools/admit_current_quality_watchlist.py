#!/usr/bin/env python
"""合并观察基线与半年报核对证据，生成仅限前向观察的准入决定。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("watchlist_report", type=Path)
    parser.add_argument("watchlist_validation", type=Path)
    parser.add_argument("profit_crosscheck", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = args.watchlist_report.resolve()
    validation_path = args.watchlist_validation.resolve()
    crosscheck_path = args.profit_crosscheck.resolve()
    manifest_path = report / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    crosscheck = json.loads(crosscheck_path.read_text(encoding="utf-8"))
    if validation.get("status") != "PASS" or validation.get("output") != report.name:
        raise RuntimeError("观察基线独立验收未通过或谱系不一致")
    if (
        crosscheck.get("status") != "PASS"
        or crosscheck.get("admission") != "H1_POSITIVE_PARENT_NETPROFIT_CONFIRMED"
        or crosscheck.get("watchlist_report") != report.name
    ):
        raise RuntimeError("半年报官方核对未通过或谱系不一致")
    watch_path = report / "watchlist.csv"
    with watch_path.open("r", encoding="utf-8", newline="") as handle:
        watchlist = list(csv.DictReader(handle))
    if any(row.get("watch_status") != "FORWARD_WATCH_ONLY" for row in watchlist):
        raise RuntimeError("观察名单缺少前向观察标记")
    result = {
        "schema_version": 1,
        "status": "ADMITTED_FOR_FORWARD_OBSERVATION_ONLY",
        "admitted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "freeze_date": datetime.now().astimezone().date().isoformat(),
        "data_cutoff": manifest["cutoff_date"],
        "watchlist_report": report.name,
        "watchlist_codes": [row["stock_code"] for row in watchlist],
        "watchlist_count": len(watchlist),
        "orders_created": 0,
        "trades_created": 0,
        "evidence": {
            "watchlist_manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
            "watchlist_validation": {"path": str(validation_path), "sha256": sha256_file(validation_path)},
            "profit_crosscheck": {"path": str(crosscheck_path), "sha256": sha256_file(crosscheck_path)},
        },
        "next_action": "WAIT_FOR_FIRST_POST_FREEZE_COMPLETED_WEEK_AND_REFRESH_DATA",
        "execution_boundary": [
            "冻结日前已有L1或R1只作为基线，不补记订单或成交",
            "只有冻结后周末首次新触发才可进入下一交易日计划",
            "账户资金仍未配置；出现首个新触发时须先确定研究本金，才能计算100股整数数量",
            "不连接券商、不发送真实订单",
        ],
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
