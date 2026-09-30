#!/usr/bin/env python
"""由已封存的历史成分双口径日线生成选股标准层。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORT_ROOT = PROJECT_ROOT / "data" / "imports"
CANONICAL_ROOT = PROJECT_ROOT / "data" / "canonical"
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.selection.canonical import CANONICAL_FIELDS, merge_code_entries, load_lineage_context  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_sealed_snapshot() -> Path:
    candidates: list[Path] = []
    for path in IMPORT_ROOT.glob("baostock_historical_daily_*"):
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") == "SEALED" and manifest.get("code_count") == 940:
            candidates.append(path)
    if not candidates:
        raise RuntimeError("没有940只且独立验证通过的SEALED历史日线快照")
    return sorted(candidates)[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description="生成历史沪深300选股标准层")
    parser.add_argument("--snapshot", type=Path, help="已独立验收的940只历史日线快照")
    parser.add_argument("--output-name", default="stock_selection_v1", help="data/canonical下的新目录名")
    args = parser.parse_args()

    snapshot = args.snapshot.resolve() if args.snapshot else latest_sealed_snapshot()
    if snapshot.parent != IMPORT_ROOT.resolve():
        raise RuntimeError(f"输入不在data/imports下: {snapshot}")
    raw_manifest_path = snapshot / "manifest.json"
    raw_manifest = json.loads(raw_manifest_path.read_text(encoding="utf-8"))
    if raw_manifest.get("status") != "SEALED" or raw_manifest.get("code_count") != 940:
        raise RuntimeError("输入必须是940只且独立验证通过的SEALED快照")

    output_root = (CANONICAL_ROOT / args.output_name).resolve()
    if output_root.parent != CANONICAL_ROOT.resolve() or output_root.exists():
        raise RuntimeError(f"输出目录无效或已存在，禁止覆盖: {output_root}")
    prices_root = output_root / "prices"
    prices_root.mkdir(parents=True)

    codes = sorted({entry["stock_code"] for entry in raw_manifest["entries"]})
    if len(codes) != 940:
        raise RuntimeError(f"唯一代码数不是940: {len(codes)}")

    entries = []
    lineage_context = load_lineage_context(snapshot, raw_manifest["lineage_evidence"])
    total_rows = 0
    calendar_dates = set()
    for index, code in enumerate(codes, start=1):
        code_entries = [entry for entry in raw_manifest["entries"] if entry["stock_code"] == code]
        rows = merge_code_entries(snapshot, code, code_entries, lineage_context=lineage_context if code == "302132" else None)
        calendar_dates.update(row["trade_date"] for row in rows)
        path = prices_root / f"{code}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CANONICAL_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        entry = {
            "path": path.relative_to(output_root).as_posix(),
            "code": code,
            "rows": len(rows),
            "min_date": rows[0]["trade_date"] if rows else None,
            "max_date": rows[-1]["trade_date"] if rows else None,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        entries.append(entry)
        total_rows += len(rows)
        if index % 100 == 0 or index == len(codes):
            print(f"canonical_progress {index}/{len(codes)}", flush=True)

    calendar_path = output_root/"trade_calendar.csv"
    with calendar_path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["trade_date"])
        writer.writeheader(); writer.writerows({"trade_date":d} for d in sorted(calendar_dates))
    membership_snapshot = IMPORT_ROOT / raw_manifest["universe_evidence"]["snapshot"]
    source_membership = membership_snapshot / raw_manifest["universe_evidence"]["membership_file"]
    membership_path = output_root / "membership_weekly.csv"
    membership_rows = 0
    with source_membership.open("r", encoding="utf-8", newline="") as source, membership_path.open(
        "w", encoding="utf-8", newline=""
    ) as target:
        reader = csv.DictReader(source)
        writer = csv.DictWriter(target, fieldnames=("observed_date", "source_update_date", "code", "code_name"))
        writer.writeheader()
        for row in reader:
            if row["query_date"] < "2010-01-01":
                continue
            writer.writerow({
                "observed_date": row["query_date"],
                "source_update_date": row["updateDate"],
                "code": row["code"].split(".")[-1],
                "code_name": row["code_name"],
            })
            membership_rows += 1

    membership_entry = {
        "path": membership_path.relative_to(output_root).as_posix(),
        "rows": membership_rows,
        "bytes": membership_path.stat().st_size,
        "sha256": sha256_file(membership_path),
    }
    manifest = {
        "schema_version": 1,
        "status": "SEALED_PENDING_INDEPENDENT_VALIDATION",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "purpose": "historical_hs300_price_volume_selection_v1",
        "source_snapshot": snapshot.name,
        "missing_qfq_observations": raw_manifest["missing_qfq_observations"],
        "qfq_exception_decision_sha256": raw_manifest["qfq_exception_decision_sha256"],
        "source_manifest_sha256": sha256_file(raw_manifest_path),
        "security_lineages": raw_manifest.get("security_lineages", []),
        "lineage_evidence": raw_manifest["lineage_evidence"],
        "validation_decision_sha256": sha256_file(PROJECT_ROOT/"docs/DECISION_20260907_RESEARCH_VALIDATION.md"),
        "lineage_bridge_factor": lineage_context["bridge_factor"],
        "lineage_missing_date": lineage_context["missing_date"],
        "lineage_decision_sha256": sha256_file(
            PROJECT_ROOT / "docs" / "DECISION_20260904_302132_CODE_LINEAGE.md"
        ),
        "membership_source_snapshot": membership_snapshot.name,
        "membership_source_sha256": sha256_file(source_membership),
        "design_sha256": sha256_file(PROJECT_ROOT / "docs" / "STOCK_SELECTION_MVP_DESIGN_V1_1.md"),
        "code_count": len(codes),
        "price_rows": total_rows,
        "membership_rows": membership_rows,
        "price_entries": entries,
        "calendar_entry": {"path": "trade_calendar.csv", "rows": len(calendar_dates),
                           "sha256": sha256_file(calendar_path), "bytes": calendar_path.stat().st_size},
        "membership_entry": membership_entry,
        "limits": [
            "688223在2022-01-26前复权价格明确缺失，包含该日的连续253日因子窗口不可用",
            "只覆盖历史沪深300范围，不代表全A股",
            "历史成分按observed_date可见，不按source_update_date倒灌",
            "财务因子关闭",
            "独立校验通过前不得进入回测",
            "共同日历由同批940只行情日期并集构成，独立对照ETF覆盖期日历；非交易所单独授权日历",
            "302132的300114历史响应仅在标准层映射为连续证券身份，原始响应未改写",
            "302132变更前不复权主来源为同证据批次的旧代码原始响应；原新代码响应保留用于交叉核对",
        ],
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(output_root),
        "status": manifest["status"],
        "code_count": len(codes),
        "price_rows": total_rows,
        "membership_rows": membership_rows,
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
