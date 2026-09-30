#!/usr/bin/env python
"""独立校验价格机制诊断封存物及总体统计。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
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


def close(a: float | None, b: str, tolerance: float = 1e-12) -> bool:
    if a is None:
        return b == ""
    try:
        return math.isclose(a, float(b), rel_tol=tolerance, abs_tol=tolerance)
    except ValueError:
        return False


def validate(run: Path) -> dict[str, Any]:
    errors: list[str] = []
    manifest_path = run / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SEALED":
        errors.append("运行未封存")
    for entry in manifest.get("outputs", []):
        path = run / entry["path"]
        if not path.is_file() or path.stat().st_size != entry["bytes"] or sha256_file(path) != entry["sha256"]:
            errors.append(f"输出哈希或大小不一致: {entry['path']}")
    for entry in manifest.get("implementation", []):
        path = PROJECT_ROOT / entry["path"]
        if not path.is_file() or sha256_file(path) != entry["sha256"]:
            errors.append(f"实现代码哈希不一致: {entry['path']}")
    plan = PROJECT_ROOT / manifest["plan_path"]
    canonical_manifest = PROJECT_ROOT / manifest["canonical_path"] / "manifest.json"
    if sha256_file(plan) != manifest["plan_sha256"]:
        errors.append("实验计划哈希不一致")
    if sha256_file(canonical_manifest) != manifest["canonical_manifest_sha256"]:
        errors.append("标准层清单哈希不一致")

    signals = read_csv(run / "signals.csv")
    observations = read_csv(run / "observations.csv")
    summary = read_csv(run / "summary.csv")
    ids = [row["signal_id"] for row in signals]
    if len(ids) != len(set(ids)):
        errors.append("signal_id重复")
    by_id: dict[str, list[dict[str, str]]] = {}
    for row in observations:
        by_id.setdefault(row["signal_id"], []).append(row)
        if row["signal_id"] not in set(ids):
            errors.append(f"观察引用未知信号: {row['signal_id']}")
        if row["status"] == "OBSERVED":
            expected = float(row["end_close_qfq"]) / float(row["entry_open_qfq"]) - 1
            if not math.isclose(expected, float(row["forward_return"]), rel_tol=1e-12, abs_tol=1e-12):
                errors.append(f"收益复算不一致: {row['signal_id']} {row['horizon']}")
    for signal_id in ids:
        horizons = sorted(int(row["horizon"]) for row in by_id.get(signal_id, []))
        if horizons != [21, 63, 126]:
            errors.append(f"信号期限不完整: {signal_id}")

    overall = {(row["mechanism"], int(row["horizon"])): row for row in summary if row["group_type"] == "overall"}
    for mechanism in ("L1", "R1"):
        for horizon in (21, 63, 126):
            rows = [row for row in observations if row["mechanism"] == mechanism and int(row["horizon"]) == horizon]
            observed = [row for row in rows if row["status"] == "OBSERVED"]
            target = overall.get((mechanism, horizon))
            if target is None:
                errors.append(f"缺少总体汇总: {mechanism} {horizon}")
                continue
            returns = [float(row["forward_return"]) for row in observed]
            excess = [float(row["excess_return"]) for row in observed if row["excess_return"] != ""]
            checks = {
                "signal_count": str(len(rows)),
                "observed_count": str(len(observed)),
            }
            for field, expected in checks.items():
                if target[field] != expected:
                    errors.append(f"{mechanism} {horizon} {field}不一致")
            numeric = {
                "coverage_rate": len(observed) / len(rows),
                "mean_return": statistics.fmean(returns),
                "median_return": statistics.median(returns),
                "mean_excess_return": statistics.fmean(excess),
                "median_excess_return": statistics.median(excess),
            }
            for field, expected in numeric.items():
                if not close(expected, target[field]):
                    errors.append(f"{mechanism} {horizon} {field}复算不一致")

    return {
        "status": "PASS" if not errors else "FAIL",
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run": run.name,
        "signals": len(signals),
        "observations": len(observations),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    report = validate(run)
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
