#!/usr/bin/env python
"""独立校验历史沪深300选股标准层。"""

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


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_ROOT = PROJECT_ROOT / "data" / "canonical"
REPORTS_ROOT = PROJECT_ROOT / "reports"
EXPECTED_FIELDS = (
    "trade_date", "code", "open", "high", "low", "close", "preclose", "volume", "amount",
    "turn", "tradestatus", "pct_chg", "is_st", "open_qfq", "high_qfq", "low_qfq", "close_qfq",
    "preclose_qfq",
    "raw_source_code", "qfq_source_code", "qfq_scale", "qfq_status",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_pending() -> Path:
    candidates: list[Path] = []
    for path in CANONICAL_ROOT.glob("stock_selection_v1*"):
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") == "SEALED_PENDING_INDEPENDENT_VALIDATION":
            candidates.append(path)
    if not candidates:
        raise RuntimeError("没有等待独立验证的选股标准层")
    return sorted(candidates)[-1]


def valid_decimal(value: str, *, positive: bool = False, nonnegative: bool = False) -> bool:
    if value == "":
        return False
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return False
    if not parsed.is_finite():
        return False
    if positive and parsed <= 0:
        return False
    if nonnegative and parsed < 0:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="独立校验选股标准层")
    parser.add_argument("--canonical", type=Path, help="data/canonical下待验收目录")
    args = parser.parse_args()
    root = args.canonical.resolve() if args.canonical else latest_pending()
    if root.parent != CANONICAL_ROOT.resolve():
        raise RuntimeError(f"目录不在data/canonical下: {root}")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SEALED_PENDING_INDEPENDENT_VALIDATION":
        raise RuntimeError(f"标准层不处于待验证状态: {manifest.get('status')}")

    errors: list[str] = []
    source_root = PROJECT_ROOT/"data/imports"/manifest["source_snapshot"]
    if source_root.resolve().parent != (PROJECT_ROOT/"data/imports").resolve():
        raise ValueError("标准层来源路径非法")
    source_manifest_path = source_root/"manifest.json"
    if sha256_file(source_manifest_path) != manifest["source_manifest_sha256"]:
        raise ValueError("原始清单哈希改变")
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("status") != "SEALED": raise ValueError("原始快照未封存")
    for key in ("missing_qfq_observations", "qfq_exception_decision_sha256"):
        if manifest.get(key) != source_manifest.get(key): errors.append("标准层前复权缺失契约与源清单不同")
    if manifest.get("qfq_exception_decision_sha256") != sha256_file(PROJECT_ROOT/"docs/DECISION_20260908_688223_MISSING_QFQ.md"):
        errors.append("前复权异常契约哈希改变")
    source_entries = defaultdict(list)
    for e in source_manifest["entries"]: source_entries[e["stock_code"]].append(e)
    ref = source_manifest["lineage_evidence"]
    evidence_root = source_root.parent/ref["snapshot"]
    if evidence_root.resolve().parent != source_root.parent.resolve() or sha256_file(evidence_root/"manifest.json") != ref["manifest_sha256"]:
        raise ValueError("沿革证据引用错误")
    sys.path.insert(0, str(PROJECT_ROOT/"tools"))
    from validate_302132_lineage_evidence import verify
    bridge = verify(evidence_root)
    if manifest.get("lineage_bridge_factor") != bridge["bridge_factor"]: errors.append("标准层桥接因子错误")
    calendar_union = set()
    entries = manifest.get("price_entries", [])
    if len(entries) != 940 or manifest.get("code_count") != 940:
        errors.append("价格文件或唯一代码数不是940")
    codes = [entry.get("code", "") for entry in entries]
    if len(codes) != len(set(codes)):
        errors.append("价格清单代码重复")

    total_rows = 0
    nonempty_codes = 0
    date_spans: dict[str, tuple[str, str]] = {}
    for index, entry in enumerate(entries, start=1):
        originals = {"daily_unadjusted": {}, "daily_qfq": {}}
        for raw_entry in source_entries[entry["code"]]:
            raw_path = source_root/raw_entry["relative_path"]
            if sha256_file(raw_path) != raw_entry["sha256"]: raise ValueError("源行情哈希改变")
            with raw_path.open(encoding="utf-8", newline="") as source:
                for source_row in csv.DictReader(source):
                    group = originals[raw_entry["category"]]
                    if source_row["date"] in group: raise ValueError("原始行情日期重复")
                    group[source_row["date"]] = source_row
        if entry["code"] == "302132":
            originals["daily_unadjusted"] = {d:r for d,r in originals["daily_unadjusted"].items() if d>bridge["previous_date"]}
            for name in bridge["raw_history_sources"]:
                with (evidence_root/name).open(encoding="utf-8",newline="") as source:
                    originals["daily_unadjusted"].update((r["date"],r) for r in csv.DictReader(source))
        if set(originals["daily_unadjusted"]) != set(originals["daily_qfq"]): errors.append("源行情双口径日期不同")
        path = root / entry["path"]
        if not path.is_file():
            errors.append(f"文件缺失: {entry['path']}")
            continue
        if path.stat().st_size != entry["bytes"] or sha256_file(path) != entry["sha256"]:
            errors.append(f"大小或SHA256不一致: {entry['path']}")
            continue
        previous = ""
        rows = 0
        minimum = None
        maximum = None
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != EXPECTED_FIELDS:
                errors.append(f"字段不一致: {entry['path']}")
                continue
            for row in reader:
                rows += 1
                value = row["trade_date"]
                calendar_union.add(value)
                original = originals["daily_unadjusted"].get(value)
                adjusted = originals["daily_qfq"].get(value)
                missing_qfq = (entry["code"] == "688223" and value == "2022-01-26"
                               and adjusted is not None and adjusted.get("adjustflag") == "3")
                expected_status = "SOURCE_UNADJUSTED_RESPONSE" if missing_qfq else "AVAILABLE"
                if row["qfq_status"] != expected_status: errors.append("前复权可用状态错误")
                if missing_qfq and original != adjusted: errors.append("异常响应并非同批不复权数据")
                if original is None or adjusted is None:
                    errors.append(f"标准层存在来源没有的日期: {entry['code']} {value}")
                else:
                    scale = Decimal(bridge["bridge_factor"]) if entry["code"] == "302132" and value <= bridge["previous_date"] else Decimal("1")
                    if missing_qfq:
                        if row["qfq_scale"] != "": errors.append("缺失前复权缩放因子必须为空")
                    elif not valid_decimal(row["qfq_scale"], positive=True) or Decimal(row["qfq_scale"]) != scale:
                        errors.append("标准层缩放标记错误")
                    if row["raw_source_code"] != original["code"].split(".")[-1] or row["qfq_source_code"] != adjusted["code"].split(".")[-1]:
                        errors.append("标准层来源代码错误")
                    for field in ("open","high","low","close","preclose","volume","amount","turn","tradestatus"):
                        if row[field] != original.get(field, ""): errors.append(f"标准字段与原始响应不同: {entry['code']} {value} {field}")
                    for target, source_field in (("pct_chg","pctChg"),("is_st","isST")):
                        if row[target] != original.get(source_field, ""): errors.append("标准层状态字段与源响应不同")
                    for field in ("open","high","low","close","preclose"):
                        actual = row[field+"_qfq"]; raw_value = adjusted.get(field, "")
                        if raw_value == "" or missing_qfq:
                            if actual != "": errors.append("标准层把缺失前复权价格填为数值")
                        elif not valid_decimal(actual) or Decimal(actual) != Decimal(raw_value)*scale:
                            errors.append(f"前复权独立复算不一致: {entry['code']} {value} {field}")
                try:
                    date.fromisoformat(value)
                except ValueError:
                    errors.append(f"日期格式错误: {entry['path']} {value}")
                if value <= previous:
                    errors.append(f"日期重复或未升序: {entry['path']} {value}")
                previous = value
                if row["code"] != entry["code"]:
                    errors.append(f"代码不一致: {entry['path']} {row['code']}")
                if row["tradestatus"] == "1":
                    for field in ("open", "high", "low", "close", "preclose", "open_qfq", "close_qfq"):
                        if missing_qfq and field.endswith("_qfq"):
                            continue
                        if not valid_decimal(row[field], positive=True):
                            errors.append(f"可交易行价格无效: {entry['path']} {value} {field}")
                            break
                    if not valid_decimal(row["volume"], nonnegative=True) or not valid_decimal(row["amount"], nonnegative=True):
                        errors.append(f"可交易行成交字段无效: {entry['path']} {value}")
                minimum = value if minimum is None else minimum
                maximum = value
        if rows != entry["rows"] or minimum != entry["min_date"] or maximum != entry["max_date"]:
            errors.append(f"行数或日期范围与清单不一致: {entry['path']}")
        if rows != len(originals["daily_unadjusted"]): errors.append("标准层丢失源行情日期")
        if rows:
            nonempty_codes += 1
            date_spans[entry["code"]] = (minimum or "", maximum or "")
        total_rows += rows
        if index % 100 == 0 or index == len(entries):
            print(f"canonical_validation_progress {index}/{len(entries)}", flush=True)

    if total_rows != manifest.get("price_rows"):
        errors.append(f"总价格行数不一致: {total_rows} != {manifest.get('price_rows')}")
    ce = manifest["calendar_entry"]; cp = root/ce["path"]
    if sha256_file(cp) != ce["sha256"]: errors.append("日历哈希错误")
    with cp.open(encoding="utf-8", newline="") as f: dates = [r["trade_date"] for r in csv.DictReader(f)]
    if dates != sorted(calendar_union) or len(dates) != ce["rows"]: errors.append("共同日历与全量价格日期并集不一致")
    with (PROJECT_ROOT/"data/canonical/mvp_510300_v2/daily.csv").open(encoding="utf-8", newline="") as f:
        benchmark_dates = [r["trade_date"] for r in csv.DictReader(f)]
    if [d for d in dates if benchmark_dates[0] <= d <= benchmark_dates[-1]] != benchmark_dates:
        errors.append("共同日历与ETF覆盖期交易日历不一致")

    membership_entry = manifest["membership_entry"]
    membership_path = root / membership_entry["path"]
    membership_groups: dict[str, set[str]] = defaultdict(set)
    membership_rows = 0
    if not membership_path.is_file():
        errors.append("历史成分标准文件缺失")
    else:
        if membership_path.stat().st_size != membership_entry["bytes"] or sha256_file(membership_path) != membership_entry["sha256"]:
            errors.append("历史成分标准文件大小或SHA256不一致")
        with membership_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != ("observed_date", "source_update_date", "code", "code_name"):
                errors.append("历史成分标准字段不一致")
            for row in reader:
                membership_rows += 1
                membership_groups[row["observed_date"]].add(row["code"])
                if row["observed_date"] < row["source_update_date"]:
                    errors.append(f"历史成分出现未来源日期: {row['observed_date']}")
        bad_groups = [value for value, members in membership_groups.items() if len(members) != 300]
        if bad_groups:
            errors.append(f"历史成分非300只的观测日数量: {len(bad_groups)}")
    if membership_rows != membership_entry["rows"] or membership_rows != manifest.get("membership_rows"):
        errors.append("历史成分行数与清单不一致")
    membership_codes = set().union(*membership_groups.values()) if membership_groups else set()
    if membership_codes - set(codes):
        errors.append(f"历史成分存在无价格文件代码: {len(membership_codes - set(codes))}")
    for code in sorted(membership_codes):
        if code not in date_spans:
            errors.append(f"历史成分股票完全没有行情: {code}")

    report = {
        "status": "FAIL" if errors else "PASS",
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "canonical": root.name,
        "code_count": len(entries),
        "nonempty_codes": nonempty_codes,
        "price_rows": total_rows,
        "membership_observation_dates": len(membership_groups),
        "membership_rows": membership_rows,
        "errors": errors[:200],
        "known_limits": manifest.get("limits", []),
    }
    report_path = REPORTS_ROOT / f"{root.name}_quality.json"
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
