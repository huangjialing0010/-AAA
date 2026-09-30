#!/usr/bin/env python
"""独立验收腾讯主源的510300 MVP快照。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORT_ROOT = PROJECT_ROOT / "data" / "imports"
REPORT_PATH = PROJECT_ROOT / "reports" / "mvp_etf_510300_tencent_data_quality.json"
START_DATE = "2012-05-28"
END_DATE = "2026-09-01"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def latest_pending() -> Path:
    paths = []
    for path in IMPORT_ROOT.glob("mvp_etf_510300_tencent_*"):
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") == "SEALED_PENDING_INDEPENDENT_VALIDATION":
            paths.append(path)
    if not paths:
        raise RuntimeError("没有待验收的腾讯主源510300快照")
    return sorted(paths)[-1]


def normalized_price(value: Any) -> str:
    return format(Decimal(str(value)).quantize(Decimal("0.000001")), "f")


def independent_tencent_rows(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError(f"腾讯原始响应状态错误: {payload.get('code')}")
    source = payload.get("data", {}).get("sh510300", {}).get("day") or []
    output = []
    for values in source:
        if len(values) < 6:
            raise RuntimeError(f"腾讯原始日K字段不足: {values}")
        lots = Decimal(str(values[5]))
        shares = lots * 100
        if shares != shares.to_integral_value():
            raise RuntimeError(f"腾讯成交量无法换算为整数份: {values}")
        output.append({
            "date": values[0],
            "code": "sh.510300",
            "open": normalized_price(values[1]),
            "high": normalized_price(values[3]),
            "low": normalized_price(values[4]),
            "close": normalized_price(values[2]),
            "volume": str(int(shares)),
            "volume_lots_source": format(lots, "f"),
            "row_source": "tencent_unadjusted",
        })
    return output


def independent_sina_rows(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"var\s+_data=\((\[.*\])\);\s*$", text, flags=re.S)
    if not match:
        raise RuntimeError("新浪近期原始响应结构异常")
    output = []
    for row in json.loads(match.group(1)):
        output.append({
            "date": row["day"],
            "code": "sh.510300",
            "open": normalized_price(row["open"]),
            "high": normalized_price(row["high"]),
            "low": normalized_price(row["low"]),
            "close": normalized_price(row["close"]),
            "volume": str(int(row["volume"])),
        })
    return output


def independent_sina_dividends(path: Path) -> list[tuple[str, Decimal, Decimal]]:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"=\s*(\{.*?\})\s*/\*", text, flags=re.S)
    if not match:
        raise RuntimeError("新浪复权因子原始响应结构异常")
    payload = json.loads(match.group(1))
    source = sorted(
        (row for row in payload.get("data", []) if row.get("d") != "1900-01-01"),
        key=lambda row: row["d"],
    )
    previous = Decimal("0")
    output = []
    for row in source:
        current = Decimal(str(row["u"]))
        if Decimal(str(row["s"])) != 1 or current <= previous:
            raise RuntimeError(f"新浪份额或累计现金异常: {row}")
        output.append((row["d"], current - previous, current))
        previous = current
    return output


def check_file_manifest(snapshot: Path, manifest: dict[str, Any], errors: list[str]) -> None:
    entries = manifest.get("entries", [])
    if len({entry["path"] for entry in entries}) != len(entries):
        errors.append("清单包含重复文件路径")
    total_bytes = 0
    total_rows = 0
    for entry in entries:
        path = snapshot / entry["path"]
        if not path.is_file():
            errors.append(f"文件缺失: {entry['path']}")
            continue
        total_bytes += path.stat().st_size
        total_rows += entry.get("rows", 0)
        if path.stat().st_size != entry["bytes"] or sha256_file(path) != entry["sha256"]:
            errors.append(f"大小或SHA256不一致: {entry['path']}")
    if len(entries) != manifest.get("files"):
        errors.append("总文件数与清单不一致")
    if total_bytes != manifest.get("bytes") or total_rows != manifest.get("rows"):
        errors.append("总字节数或总行数与清单不一致")


def compare_market(
    primary: dict[str, dict[str, str]],
    secondary: list[dict[str, str]],
    end_date: str,
) -> tuple[int, list[str], list[str]]:
    compared = 0
    price_errors = []
    volume_errors = []
    for row in secondary:
        if row["date"] > end_date or row["date"] not in primary:
            continue
        compared += 1
        other = primary[row["date"]]
        if any(
            abs(Decimal(row[field]) - Decimal(other[field])) > Decimal("0.0001")
            for field in ("open", "high", "low", "close")
        ):
            price_errors.append(row["date"])
        if abs(Decimal(row["volume"]) - Decimal(other["volume"])) > 100:
            volume_errors.append(row["date"])
    return compared, price_errors, volume_errors


def main() -> int:
    parser = argparse.ArgumentParser(description="独立验收腾讯主源510300快照")
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    snapshot = args.snapshot.resolve() if args.snapshot else latest_pending()
    if snapshot.parent != IMPORT_ROOT.resolve() or not snapshot.name.startswith("mvp_etf_510300_tencent_"):
        raise RuntimeError(f"快照路径不在允许范围: {snapshot}")
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SEALED_PENDING_INDEPENDENT_VALIDATION":
        raise RuntimeError(f"快照状态不是待验收: {manifest.get('status')}")
    errors: list[str] = []
    warnings: list[str] = []
    check_file_manifest(snapshot, manifest, errors)

    basic_fields, basic = read_csv(snapshot / "security_basic.csv")
    if not basic_fields or len(basic) != 1 or not (
        basic[0].get("code") == "sh.510300"
        and basic[0].get("type") == "5"
        and basic[0].get("status") == "1"
        and basic[0].get("ipoDate") == START_DATE
    ):
        errors.append(f"证券身份不符合冻结契约: {basic}")

    raw_paths = sorted((snapshot / "market_data").glob("tencent_unadjusted_raw_*.json"))
    raw_rows = []
    try:
        for path in raw_paths:
            raw_rows.extend(independent_tencent_rows(path))
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    market_fields, market_rows = read_csv(snapshot / "market_data" / "daily_unadjusted.csv")
    expected_fields = [
        "date", "code", "open", "high", "low", "close", "volume",
        "volume_lots_source", "row_source",
    ]
    if market_fields != expected_fields:
        errors.append(f"主行情字段不符合契约: {market_fields}")
    if market_rows != raw_rows:
        errors.append("腾讯标准化主行情与两段原始响应独立解析结果不一致")
    dates = [row["date"] for row in market_rows]
    if dates != sorted(set(dates)):
        errors.append("主行情日期重复或未升序")
    if len(dates) != 3468 or not dates or dates[0] != START_DATE or dates[-1] != END_DATE:
        errors.append(f"主行情边界或行数错误: {len(dates)} {dates[:1]} {dates[-1:]}")
    for row in market_rows:
        try:
            values = {field: Decimal(row[field]) for field in ("open", "high", "low", "close")}
            if min(values.values()) <= 0:
                errors.append(f"主行情非正价格: {row['date']}")
            if values["high"] < max(values.values()) or values["low"] > min(values.values()):
                errors.append(f"主行情OHLC关系错误: {row['date']}")
            if int(row["volume"]) < 0 or row["row_source"] != "tencent_unadjusted":
                errors.append(f"主行情成交量或逐行来源错误: {row['date']}")
        except ValueError:
            errors.append(f"主行情数值无法解析: {row['date']}")

    _, calendar = read_csv(snapshot / "cross_validation" / "trade_calendar.csv")
    calendar_dates = [row["trade_date"] for row in calendar]
    if dates != calendar_dates:
        errors.append("腾讯主行情日期与封存交易日历不完全一致")
    current_calendar = PROJECT_ROOT / manifest["sources"]["trade_calendar"]["path"]
    if not current_calendar.is_file() or sha256_file(current_calendar) != manifest["sources"]["trade_calendar"]["sha256"]:
        errors.append("交易日历来源文件已变化或缺失")

    try:
        raw_sina = independent_sina_rows(snapshot / "cross_validation" / "sina_recent_raw.js")
    except (OSError, KeyError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raw_sina = []
        errors.append(str(exc))
    sina_fields, sina = read_csv(snapshot / "cross_validation" / "sina_recent.csv")
    if sina_fields != ["date", "code", "open", "high", "low", "close", "volume"] or sina != raw_sina:
        errors.append("新浪标准化近期行情与原始响应独立解析结果不一致")
    primary = {row["date"]: row for row in market_rows}
    sina_compared, sina_price_errors, sina_volume_errors = compare_market(primary, sina, END_DATE)
    if sina_compared != 1021 or sina_price_errors or sina_volume_errors:
        errors.append(
            f"新浪重叠验证失败: compared={sina_compared} price={sina_price_errors[:5]} "
            f"volume={sina_volume_errors[:5]}"
        )

    _, baostock = read_csv(snapshot / "cross_validation" / "baostock_2026_unadjusted.csv")
    baostock_compared, bao_price_errors, bao_volume_errors = compare_market(primary, baostock, END_DATE)
    if baostock_compared != 161 or bao_price_errors or bao_volume_errors:
        errors.append(
            f"BaoStock重叠验证失败: compared={baostock_compared} price={bao_price_errors[:5]} "
            f"volume={bao_volume_errors[:5]}"
        )

    _, dividends = read_csv(snapshot / "fund_actions" / "dividends.csv")
    try:
        raw_dividends = independent_sina_dividends(snapshot / "fund_actions" / "sina_hfq_raw.js")
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raw_dividends = []
        errors.append(str(exc))
    cumulative = Decimal("0")
    market_dates = set(dates)
    for index, row in enumerate(dividends):
        amount = Decimal(row["cash_per_share"])
        cumulative += amount
        if not row["record_date"] <= row["ex_date"] <= row["payment_date"]:
            errors.append(f"分红日期顺序错误: {row}")
        if any(row[field] not in market_dates for field in ("record_date", "ex_date", "payment_date")):
            errors.append(f"分红日期不在主行情交易日: {row}")
        if Decimal(row["cumulative_cash"]) != cumulative:
            errors.append(f"累计现金分红错误: {row}")
        if index < len(raw_dividends) and (row["ex_date"], amount, cumulative) != raw_dividends[index]:
            errors.append(f"标准化分红与新浪因子原文不一致: {row}")
    if len(dividends) != 14 or len(raw_dividends) != 14 or cumulative != Decimal("0.880"):
        errors.append(f"冻结分红契约失败: csv={len(dividends)} raw={len(raw_dividends)} cumulative={cumulative}")

    report = {
        "status": "FAIL" if errors else "PASS_WITH_KNOWN_LIMITS",
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "snapshot": snapshot.name,
        "market_dates": len(market_rows),
        "calendar_dates": len(calendar_dates),
        "min_date": dates[0] if dates else None,
        "max_date": dates[-1] if dates else None,
        "sina_compared_dates": sina_compared,
        "baostock_compared_dates": baostock_compared,
        "dividend_events": len(dividends),
        "cumulative_cash_per_share": format(cumulative, "f"),
        "errors": errors,
        "warnings": warnings,
        "known_limits": manifest.get("limits", []),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    manifest["independent_validation"] = {
        "status": report["status"],
        "report": REPORT_PATH.relative_to(PROJECT_ROOT).as_posix(),
        "validated_at": report["validated_at"],
    }
    if errors:
        manifest["status"] = "REJECTED_VALIDATION_FAILED"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1
    manifest["status"] = "SEALED"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
