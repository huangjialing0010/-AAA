#!/usr/bin/env python
"""独立验收当前质量策略前向日志的基线与零交易边界。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def validate(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    expected_files = {"manifest.json", "signals.csv", "orders.csv", "trades.csv", "rejections.csv", "positions.csv", "daily_reconciliation.csv"}
    if {path.name for path in root.iterdir() if path.is_file()} != expected_files:
        errors.append("前向日志文件集合不符合契约")
    for item in manifest.get("entries", []):
        path = root / item["path"]
        if not path.is_file() or path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            errors.append(f"文件大小或哈希不一致: {item['path']}")
        elif len(read_csv(path)) != item["rows"]:
            errors.append(f"CSV行数与清单不一致: {item['path']}")
    signals = read_csv(root / "signals.csv")
    if len(signals) != 5 or any(
        row["event_type"] != "BASELINE_PRE_FREEZE"
        or row["actionable"] != "false"
        or row["reason"] != "PRE_FREEZE_NO_BACKFILL"
        for row in signals
    ):
        errors.append("基线信号数量或非行动边界错误")
    for name in ("orders.csv", "trades.csv", "rejections.csv", "positions.csv"):
        if read_csv(root / name):
            errors.append(f"初始化日志不应含记录: {name}")
    reconciliation = read_csv(root / "daily_reconciliation.csv")
    if (
        len(reconciliation) != 1
        or reconciliation[0]["status"] != "NOT_APPLICABLE_CAPITAL_UNSET"
        or any(reconciliation[0][field] for field in ("cash", "positions_market_value", "equity"))
    ):
        errors.append("初始化对账状态或空金额语义错误")
    admission_path = Path(manifest["lineage"]["admission_path"])
    if not admission_path.is_file() or sha256_file(admission_path) != manifest["lineage"]["admission_sha256"]:
        errors.append("准入决定缺失或哈希变化")
    if (
        manifest.get("status") != "FROZEN_WAITING_FOR_POST_FREEZE_SIGNAL"
        or manifest.get("execution_status") != "CAPITAL_AND_EXIT_RULES_UNSET"
        or manifest.get("order_count") != 0
        or manifest.get("trade_count") != 0
    ):
        errors.append("清单状态或零交易计数错误")
    return {
        "status": "PASS" if not errors else "FAIL",
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "forward_log": root.name,
        "baseline_signal_count": len(signals),
        "actionable_signal_count": sum(row["actionable"] == "true" for row in signals),
        "order_count": len(read_csv(root / "orders.csv")),
        "trade_count": len(read_csv(root / "trades.csv")),
        "execution_status": manifest.get("execution_status"),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("forward_log", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.forward_log.resolve())
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
