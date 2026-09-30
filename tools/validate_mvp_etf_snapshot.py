#!/usr/bin/env python
"""独立验收510300 MVP行情和基金分红快照。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORT_ROOT = PROJECT_ROOT / "data" / "imports"
REPORT_PATH = PROJECT_ROOT / "reports" / "mvp_etf_510300_data_quality.json"
REQUIRED_DAILY_FIELDS = {
    "date", "code", "open", "high", "low", "close", "preclose", "volume", "amount",
    "adjustflag", "turn", "tradestatus", "pctChg", "isST",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_pending_snapshot() -> Path:
    candidates = []
    for path in IMPORT_ROOT.glob("mvp_etf_510300_*"):
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("status") == "SEALED_PENDING_INDEPENDENT_VALIDATION":
            candidates.append(path)
    if not candidates:
        raise RuntimeError("没有等待独立验收的510300 MVP快照")
    return sorted(candidates)[-1]


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def validate_daily(
    snapshot: Path,
    entry: dict[str, Any],
    errors: list[str],
) -> dict[str, tuple[str, str, str, str, str, str, str]]:
    path = snapshot / entry["path"]
    fields, rows = read_csv(path)
    missing = REQUIRED_DAILY_FIELDS - set(fields)
    if missing:
        errors.append(f"{entry['path']} 缺少字段: {sorted(missing)}")
        return {}
    if len(rows) != entry.get("rows"):
        errors.append(f"{entry['path']} 行数与清单不一致")
    if len(rows) >= 2000:
        errors.append(f"{entry['path']} 达到{len(rows)}行，存在分页截断风险")
    output = {}
    previous = ""
    for row in rows:
        value = row["date"]
        try:
            date.fromisoformat(value)
        except ValueError:
            errors.append(f"{entry['path']} 日期格式错误: {value}")
            continue
        if value <= previous:
            errors.append(f"{entry['path']} 日期重复或未升序: {value}")
        previous = value
        if not entry["start_date"] <= value <= entry["end_date"]:
            errors.append(f"{entry['path']} 日期越界: {value}")
        if row["code"] != "sh.510300" or row["adjustflag"] != entry["adjustflag"]:
            errors.append(f"{entry['path']} 代码或复权标志错误")
        output[value] = (
            row["volume"], row["amount"], row["tradestatus"], row["open"],
            row["high"], row["low"], row["close"],
        )
    actual_min = min(output) if output else None
    actual_max = max(output) if output else None
    if actual_min != entry.get("min_date") or actual_max != entry.get("max_date"):
        errors.append(f"{entry['path']} 实际日期范围与清单不一致")
    return output


def independent_factor_events(path: Path) -> list[tuple[str, Decimal, Decimal]]:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"=\s*(\{.*?\})\s*/\*", text, flags=re.S)
    if not match:
        raise RuntimeError("无法独立解析新浪复权因子原文")
    payload = json.loads(match.group(1))
    source = sorted(
        (row for row in payload.get("data", []) if row.get("d") != "1900-01-01"),
        key=lambda row: row["d"],
    )
    previous = Decimal("0")
    events = []
    for row in source:
        current = Decimal(str(row["u"]))
        if Decimal(str(row["s"])) != Decimal("1"):
            raise RuntimeError(f"存在未支持的份额拆分: {row}")
        events.append((row["d"], current - previous, current))
        previous = current
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description="独立验收510300 MVP数据快照")
    parser.add_argument("--snapshot", type=Path, help="待验收快照，默认最新pending快照")
    args = parser.parse_args()
    snapshot = args.snapshot.resolve() if args.snapshot else latest_pending_snapshot()
    if snapshot.parent != IMPORT_ROOT.resolve() or not snapshot.name.startswith("mvp_etf_510300_"):
        raise RuntimeError(f"快照目录不在允许范围: {snapshot}")

    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SEALED_PENDING_INDEPENDENT_VALIDATION":
        raise RuntimeError(f"快照不处于待验收状态: {manifest.get('status')}")

    errors: list[str] = []
    warnings: list[str] = []
    entries = manifest.get("entries", [])
    entry_map = {entry["path"]: entry for entry in entries}
    if len(entry_map) != len(entries):
        errors.append("清单包含重复路径")
    total_rows = 0
    total_bytes = 0
    for entry in entries:
        path = snapshot / entry["path"]
        if not path.is_file():
            errors.append(f"文件缺失: {entry['path']}")
            continue
        actual_bytes = path.stat().st_size
        if actual_bytes != entry["bytes"] or sha256_file(path) != entry["sha256"]:
            errors.append(f"大小或SHA256不一致: {entry['path']}")
        total_bytes += actual_bytes
        total_rows += entry.get("rows", 0)
    if total_bytes != manifest.get("bytes"):
        errors.append("总字节数与清单不一致")
    if total_rows != manifest.get("rows"):
        errors.append("总行数与清单不一致")
    if len(entries) != manifest.get("files"):
        errors.append("文件数与清单不一致")

    basic_entry = next((entry for entry in entries if entry["category"] == "security_basic"), None)
    if basic_entry:
        _, rows = read_csv(snapshot / basic_entry["path"])
        if len(rows) != 1:
            errors.append("证券基本资料不是唯一一行")
        elif not (
            rows[0].get("code") == "sh.510300"
            and rows[0].get("type") == "5"
            and rows[0].get("status") == "1"
            and rows[0].get("ipoDate") == "2012-05-28"
        ):
            errors.append(f"证券身份不符合冻结契约: {rows[0]}")
    else:
        errors.append("缺少证券基本资料")

    combined: dict[str, dict[str, tuple[str, str, str, str, str, str, str]]] = {}
    for category in ("daily_unadjusted", "daily_qfq"):
        category_rows = {}
        category_entries = sorted(
            (entry for entry in entries if entry["category"] == category),
            key=lambda entry: entry["start_date"],
        )
        if len(category_entries) != 3:
            errors.append(f"{category} 分段数量不是3")
        for entry in category_entries:
            rows = validate_daily(snapshot, entry, errors)
            overlap = set(category_rows) & set(rows)
            if overlap:
                errors.append(f"{category} 分段日期重叠: {min(overlap)}")
            category_rows.update(rows)
        combined[category] = category_rows

    raw = combined.get("daily_unadjusted", {})
    qfq = combined.get("daily_qfq", {})
    if set(raw) != set(qfq):
        errors.append("不复权和前复权日期集合不一致")
    market_mismatch = []
    price_difference_dates = []
    for value in sorted(set(raw) & set(qfq)):
        if raw[value][:3] != qfq[value][:3]:
            market_mismatch.append(value)
        if raw[value][3:] != qfq[value][3:]:
            price_difference_dates.append(value)
    if market_mismatch:
        errors.append(f"双口径成交量/成交额/交易状态不一致: {len(market_mismatch)}日")
    if not price_difference_dates:
        warnings.append("BaoStock对510300返回的前复权价格与不复权价格完全相同")

    normalized = snapshot / "fund_actions" / "dividends.csv"
    _, dividend_rows = read_csv(normalized) if normalized.is_file() else ([], [])
    try:
        factors = independent_factor_events(snapshot / "fund_actions" / "sina_hfq_raw.js")
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        factors = []
        errors.append(str(exc))
    if len(dividend_rows) != 14 or len(factors) != 14:
        errors.append(f"分红事件数不是冻结的14次: normalized={len(dividend_rows)} raw={len(factors)}")
    cumulative = Decimal("0")
    for index, row in enumerate(dividend_rows):
        try:
            record = date.fromisoformat(row["record_date"])
            ex_date = date.fromisoformat(row["ex_date"])
            payment = date.fromisoformat(row["payment_date"])
            amount = Decimal(row["cash_per_share"])
            cumulative += amount
            if not record <= ex_date <= payment or amount <= 0:
                errors.append(f"分红日期或金额无效: {row}")
            if Decimal(row["cumulative_cash"]) != cumulative:
                errors.append(f"累计分红不一致: {row}")
            if index < len(factors):
                expected = factors[index]
                if (row["ex_date"], amount, cumulative) != expected:
                    errors.append(f"标准化分红与因子原文不一致: {row} != {expected}")
        except (KeyError, ValueError):
            errors.append(f"分红记录无法解析: {row}")
    if cumulative != Decimal("0.880"):
        errors.append(f"截至冻结截止日累计现金分红不是0.880: {cumulative}")
    dividend_dates_missing_from_market = [
        row["ex_date"] for row in dividend_rows if row.get("ex_date") not in raw
    ]
    if dividend_dates_missing_from_market:
        errors.append(f"除息日不在行情交易日内: {dividend_dates_missing_from_market}")

    report = {
        "status": "FAIL" if errors else "PASS_WITH_KNOWN_LIMITS",
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "snapshot": snapshot.name,
        "query_end_date": manifest.get("query_end_date"),
        "files": len(entries),
        "rows": total_rows,
        "bytes": total_bytes,
        "market_dates": len(raw),
        "min_market_date": min(raw) if raw else None,
        "max_market_date": max(raw) if raw else None,
        "dividend_events": len(dividend_rows),
        "cumulative_cash_per_share": format(cumulative, "f"),
        "baostock_qfq_distinct_price_dates": len(price_difference_dates),
        "errors": errors,
        "warnings": warnings,
        "known_limits": manifest.get("limits", []),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if errors:
        return 1

    manifest["status"] = "SEALED"
    manifest["independent_validation"] = {
        "status": report["status"],
        "report": REPORT_PATH.relative_to(PROJECT_ROOT).as_posix(),
        "validated_at": report["validated_at"],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
