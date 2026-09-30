#!/usr/bin/env python
"""独立验收分段的历史沪深300成分双口径日线快照。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORT_ROOT = PROJECT_ROOT / "data" / "imports"
REQUIRED_FIELDS = {
    "date", "code", "open", "high", "low", "close", "preclose", "volume", "amount",
    "adjustflag", "turn", "tradestatus", "pctChg", "isST",
}
EXPECTED_SECURITY_LINEAGES = (
    {
        "effective_code": "302132",
        "previous_code": "300114",
        "previous_code_end_date": "2025-02-14",
        "effective_code_start_date": "2025-02-17",
        "applies_to_adjustflags": ["2"],
        "evidence": "docs/DECISION_20260904_302132_CODE_LINEAGE.md",
    },
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_pending_snapshot() -> Path:
    candidates = []
    for path in IMPORT_ROOT.glob("baostock_historical_daily_*"):
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("status") == "SEALED_PENDING_INDEPENDENT_VALIDATION":
            candidates.append(path)
    if not candidates:
        raise RuntimeError("没有等待独立验收的历史日线快照")
    return sorted(candidates)[-1]


def membership_spans(snapshot_name: str) -> dict[str, tuple[str, str]]:
    path = IMPORT_ROOT / snapshot_name / "hs300_membership_weekly.csv"
    values: dict[str, list[str]] = defaultdict(list)
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["query_date"] >= "2010-01-01":
                values[row["code"].split(".")[-1]].append(row["query_date"])
    return {code: (min(dates), max(dates)) for code, dates in values.items()}


def read_segment(path: Path, entry: dict[str, Any], errors: list[str]) -> dict[str, dict[str, str]]:
    rows_by_date: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        missing = REQUIRED_FIELDS - set(fields)
        if missing:
            errors.append(f"{entry['relative_path']} 缺少字段: {sorted(missing)}")
            return rows_by_date
        previous = ""
        rows = 0
        for row in reader:
            rows += 1
            value = row["date"]
            try:
                date.fromisoformat(value)
            except ValueError:
                errors.append(f"{entry['relative_path']} 日期格式错误: {value}")
                continue
            if value <= previous:
                errors.append(f"{entry['relative_path']} 日期重复或未升序: {value}")
            previous = value
            if not entry["start_date"] <= value <= entry["end_date"]:
                errors.append(f"{entry['relative_path']} 日期越界: {value}")
            known_missing = (entry["stock_code"] == "688223" and entry["adjustflag"] == "2"
                             and row["code"] == "sh.688223" and value == "2022-01-26"
                             and row["adjustflag"] == "3"
                             and entry.get("missing_qfq_dates") == ["2022-01-26"])
            if row["adjustflag"] != entry["adjustflag"] and not known_missing:
                errors.append(f"{entry['relative_path']} 复权标记不一致")
            response_stock_code = entry.get("response_stock_code", entry["stock_code"])
            if row["code"].split(".")[-1] != response_stock_code:
                errors.append(f"{entry['relative_path']} 股票代码不一致")
            rows_by_date[value] = row
    if rows >= 2000:
        errors.append(f"{entry['relative_path']} 行数达到{rows}，未规避分页风险")
    if rows != entry["rows"]:
        errors.append(f"{entry['relative_path']} 行数与清单不一致")
    actual_missing = [d for d, r in rows_by_date.items() if entry["adjustflag"] == "2" and r["adjustflag"] != "2"]
    if actual_missing != entry.get("missing_qfq_dates", []):
        errors.append("前复权缺失声明与响应不同")
    actual_min = min(rows_by_date) if rows_by_date else None
    actual_max = max(rows_by_date) if rows_by_date else None
    if actual_min != entry["min_date"] or actual_max != entry["max_date"]:
        errors.append(f"{entry['relative_path']} 日期范围与清单不一致")
    return rows_by_date


def expected_paths(codes: list[str], windows: list[tuple[str, str]]) -> set[str]:
    paths = {
        f"{category}/{code}/{start}_{end}.csv"
        for _, category in (("3", "daily_unadjusted"), ("2", "daily_qfq"))
        for code in codes
        for start, end in windows
    }
    if "302132" in codes:
        for start, end in windows:
            if start <= "2025-02-14" < end and start < "2025-02-17" <= end:
                paths.remove(f"daily_qfq/302132/{start}_{end}.csv")
                paths.add(f"daily_qfq/302132/{start}_2025-02-14.csv")
                paths.add(f"daily_qfq/302132/2025-02-17_{end}.csv")
    return paths


def expected_query_code(entry: dict[str, Any]) -> tuple[str, str]:
    if entry["stock_code"] == "302132" and entry["category"] == "daily_qfq":
        if entry["end_date"] <= "2025-02-14":
            return "300114", "PREVIOUS_CODE"
        if entry["start_date"] >= "2025-02-17":
            return "302132", "EFFECTIVE_CODE"
        return "", "INVALID_CROSS_BOUNDARY"
    return entry["stock_code"], "UNCHANGED"


def decimal_value(value: str, label: str, errors: list[str]) -> Decimal | None:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        errors.append(f"{label} 不是有效数值: {value}")
        return None
    if not parsed.is_finite():
        errors.append(f"{label} 不是有限数值: {value}")
        return None
    return parsed


def validate_lineage_boundary(
    raw_rows: dict[str, dict[str, str]],
    qfq_rows: dict[str, dict[str, str]],
    errors: list[str],
    bridge_factor: Decimal = Decimal("1"),
) -> None:
    previous_date = "2025-02-14"
    effective_date = "2025-02-17"
    for value in (previous_date, effective_date):
        if value not in raw_rows or value not in qfq_rows:
            errors.append(f"302132代码沿革边界缺少双口径行情: {value}")
            return
    raw_previous = decimal_value(raw_rows[previous_date]["close"], "302132变更前不复权收盘", errors)
    raw_preclose = decimal_value(raw_rows[effective_date]["preclose"], "302132变更日不复权昨收", errors)
    qfq_previous = decimal_value(qfq_rows[previous_date]["close"], "302132变更前前复权收盘", errors)
    qfq_preclose = decimal_value(qfq_rows[effective_date]["preclose"], "302132变更日前复权昨收", errors)
    if None in (raw_previous, raw_preclose, qfq_previous, qfq_preclose):
        return
    if raw_previous != raw_preclose:
        errors.append("302132代码变更日不复权昨收与300114末日收盘不连续")
    if qfq_previous * bridge_factor != qfq_preclose:
        errors.append("302132代码变更日前复权昨收与300114末日收盘不连续")
    if raw_previous == 0 or raw_preclose == 0:
        errors.append("302132代码沿革边界出现零价格，无法核对复权比例")
        return
    if qfq_previous * bridge_factor / raw_previous != qfq_preclose / raw_preclose:
        errors.append("302132代码沿革边界前后复权比例不连续")


def main() -> int:
    parser = argparse.ArgumentParser(description="独立验收BaoStock历史成分分段日线")
    parser.add_argument("--snapshot", type=Path, help="待验收快照；默认最新pending快照")
    args = parser.parse_args()
    snapshot = args.snapshot.resolve() if args.snapshot else latest_pending_snapshot()
    if snapshot.parent != IMPORT_ROOT.resolve() or not snapshot.name.startswith("baostock_historical_daily_"):
        raise RuntimeError(f"快照目录不在允许范围: {snapshot}")

    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SEALED_PENDING_INDEPENDENT_VALIDATION":
        raise RuntimeError(f"快照不处于待验收状态: {manifest.get('status')}")
    errors: list[str] = []
    evidence_ref = manifest["lineage_evidence"]
    evidence_root = IMPORT_ROOT / evidence_ref["snapshot"]
    if evidence_root.resolve().parent != IMPORT_ROOT.resolve() or not evidence_root.name.startswith("lineage_302132_"):
        raise RuntimeError("沿革证据路径非法")
    ep = evidence_root/"manifest.json"
    if sha256_file(ep) != evidence_ref["manifest_sha256"]:
        raise RuntimeError("沿革证据清单哈希改变")
    if json.loads(ep.read_text(encoding="utf-8")).get("status") != "SEALED":
        raise RuntimeError("沿革证据未封存")
    sys.path.insert(0, str(PROJECT_ROOT/"tools"))
    from validate_302132_lineage_evidence import verify
    lineage = verify(evidence_root)
    entries = manifest.get("entries", [])
    entry_map = {entry["relative_path"]: entry for entry in entries}
    if len(entry_map) != len(entries):
        errors.append("清单包含重复文件路径")

    windows = [(item["start_date"], item["end_date"]) for item in manifest["windows"]]
    codes = sorted({entry["stock_code"] for entry in entries})
    if manifest.get("security_lineages") != list(EXPECTED_SECURITY_LINEAGES):
        errors.append("证券代码沿革声明与独立验收规则不一致")
    if set(entry_map) != expected_paths(codes, windows):
        errors.append("清单路径集合与代码、窗口、复权口径及沿革分段不一致")
    if len(codes) != manifest["code_count"]:
        errors.append("唯一股票数与清单不一致")
    if len(entries) != manifest["data_files"]:
        errors.append("数据文件数与清单不一致")

    spans = membership_spans(manifest["universe_evidence"]["snapshot"])
    codes_with_research_membership = 0
    codes_without_any_data: list[str] = []
    dual_date_mismatch_codes: list[str] = []
    dual_market_field_mismatch_codes: list[str] = []
    total_rows = 0
    total_bytes = 0
    empty_files = 0
    missing_observations = []

    for code_index, code in enumerate(codes, start=1):
        combined: dict[str, dict[str, dict[str, str]]] = {}
        for adjustflag, category in (("3", "daily_unadjusted"), ("2", "daily_qfq")):
            category_rows: dict[str, dict[str, str]] = {}
            category_entries = sorted(
                (
                    entry for entry in entries
                    if entry["stock_code"] == code and entry["category"] == category
                ),
                key=lambda item: (item["start_date"], item["end_date"]),
            )
            for entry in category_entries:
                relative = entry["relative_path"]
                query_code, lineage_phase = expected_query_code(entry)
                if entry.get("query_stock_code", entry["stock_code"]) != query_code:
                    errors.append(f"查询代码与沿革规则不一致: {relative}")
                if entry.get("response_stock_code", entry["stock_code"]) != query_code:
                    errors.append(f"响应代码与沿革规则不一致: {relative}")
                if entry.get("lineage_phase", "UNCHANGED") != lineage_phase:
                    errors.append(f"沿革阶段与独立规则不一致: {relative}")
                path = snapshot / relative
                metadata_path = snapshot / entry["metadata_path"]
                if not path.is_file() or not metadata_path.is_file():
                    errors.append(f"数据或任务元数据缺失: {relative}")
                    continue
                if path.stat().st_size != entry["bytes"] or sha256_file(path) != entry["sha256"]:
                    errors.append(f"大小或SHA256不一致: {relative}")
                    continue
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if metadata != entry:
                    errors.append(f"任务元数据与总清单不一致: {relative}")
                segment_rows = read_segment(path, entry, errors)
                if set(category_rows) & set(segment_rows):
                    errors.append(f"{code} {category} 分段日期重叠")
                category_rows.update(segment_rows)
                total_rows += entry["rows"]
                total_bytes += entry["bytes"]
                empty_files += entry["rows"] == 0
            combined[category] = category_rows

        raw_rows = combined["daily_unadjusted"]
        qfq_rows = combined["daily_qfq"]
        if code == "688223" and qfq_rows.get("2022-01-26", {}).get("adjustflag") == "3":
            d = "2022-01-26"
            if raw_rows.get(d) != qfq_rows[d]:
                errors.append("688223上市首日异常响应与不复权原始行不一致")
            missing_observations.extend({"code":code, "date":d, "source_file":e["relative_path"]}
                                        for e in entries if e["stock_code"] == code and d in e.get("missing_qfq_dates", []))
        if code == "302132":
            raw_rows = {d:r for d,r in raw_rows.items() if d>lineage["previous_date"]}
            for name in lineage["raw_history_sources"]:
                with (evidence_root/name).open(encoding="utf-8",newline="") as f:
                    raw_rows.update((r["date"],r) for r in csv.DictReader(f))
        if set(raw_rows) != set(qfq_rows):
            dual_date_mismatch_codes.append(code)
        else:
            for value in raw_rows:
                if any(
                    raw_rows[value][field] != qfq_rows[value][field]
                    for field in ("volume", "amount", "tradestatus")
                ):
                    dual_market_field_mismatch_codes.append(code)
                    break
        if code == "302132":
            validate_lineage_boundary(raw_rows, qfq_rows, errors, Decimal(lineage["bridge_factor"]))
        all_dates = sorted(set(raw_rows) | set(qfq_rows))
        if not all_dates:
            codes_without_any_data.append(code)
        if code in spans:
            codes_with_research_membership += 1
            start, end = spans[code]
            if not any(start <= value <= end for value in all_dates):
                errors.append(f"{code} 在2010年后属于指数，但没有成员期间行情")
        if code_index % 100 == 0 or code_index == len(codes):
            print(f"validation_progress {code_index}/{len(codes)}", flush=True)

    if total_rows != manifest["rows"]:
        errors.append(f"总行数与清单不一致: {total_rows} != {manifest['rows']}")
    if total_bytes != manifest["bytes"]:
        errors.append(f"总字节数与清单不一致: {total_bytes} != {manifest['bytes']}")
    if empty_files != manifest["empty_data_files"]:
        errors.append("空文件数与清单不一致")
    if dual_date_mismatch_codes:
        errors.append(f"双口径日期不一致股票数: {len(dual_date_mismatch_codes)}")
    if dual_market_field_mismatch_codes:
        errors.append(f"双口径成交量/成交额/交易状态不一致股票数: {len(dual_market_field_mismatch_codes)}")

    if manifest.get("missing_qfq_observations") != missing_observations:
        errors.append("总清单前复权缺失记录不一致")
    if manifest.get("qfq_exception_decision_sha256") != sha256_file(PROJECT_ROOT/"docs/DECISION_20260908_688223_MISSING_QFQ.md"):
        errors.append("前复权异常契约哈希不一致")
    report = {
        "missing_qfq_observations": missing_observations,
        "status": "FAIL" if errors else "PASS",
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "snapshot": snapshot.name,
        "code_count": len(codes),
        "codes_with_membership_from_2010": codes_with_research_membership,
        "data_files": len(entries),
        "rows": total_rows,
        "bytes": total_bytes,
        "empty_data_files": empty_files,
        "codes_without_any_data": codes_without_any_data,
        "dual_date_mismatch_codes": dual_date_mismatch_codes,
        "dual_market_field_mismatch_codes": dual_market_field_mismatch_codes,
        "errors": errors[:200],
        "known_limits": manifest.get("limits", []),
        "lineage_validation": lineage,
    }
    report_path = PROJECT_ROOT / "reports" / f"{snapshot.name}_quality.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if errors:
        return 1

    manifest["status"] = "SEALED"
    manifest["independent_validation"] = {
        "status": "PASS",
        "report": report_path.relative_to(PROJECT_ROOT).as_posix(),
        "validated_at": report["validated_at"],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state_path = snapshot / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "SEALED"
    state["validated_at"] = report["validated_at"]
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
