#!/usr/bin/env python
"""独立验收510300 Yahoo/Sina/BaoStock跨源MVP快照。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORT_ROOT = PROJECT_ROOT / "data" / "imports"
REPORT_PATH = PROJECT_ROOT / "reports" / "mvp_etf_510300_data_quality.json"
START_DATE = "2012-05-28"
END_DATE = "2026-09-01"
EXPECTED_MARKET_DATES = 3466
EXPECTED_DIVIDENDS = 14
EXPECTED_YAHOO_GAP = ["2025-10-24"]
EXPECTED_YAHOO_OVERRIDES = ["2024-01-15", "2024-03-29", "2025-05-23"]


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
        raise RuntimeError("没有等待独立验收的510300跨源快照")
    return sorted(candidates)[-1]


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def independent_yahoo_rows(path: Path) -> tuple[list[dict[str, str]], list[tuple[str, Decimal]], int, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    chart = payload["chart"]
    if chart.get("error") is not None or len(chart.get("result") or []) != 1:
        raise RuntimeError("Yahoo原始响应不是唯一成功结果")
    result = chart["result"][0]
    if result.get("meta", {}).get("symbol") != "510300.SS":
        raise RuntimeError("Yahoo原始响应证券代码错误")
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    adjusted = result["indicators"]["adjclose"][0]["adjclose"]
    if any(len(quote[field]) != len(timestamps) for field in ("open", "high", "low", "close", "volume")):
        raise RuntimeError("Yahoo原始响应行情数组长度不一致")
    if len(adjusted) != len(timestamps):
        raise RuntimeError("Yahoo原始响应调整价数组长度不一致")
    rows = []
    missing_dates = []
    for index, stamp in enumerate(timestamps):
        value_date = datetime.fromtimestamp(int(stamp), tz=timezone.utc).date().isoformat()
        if not START_DATE <= value_date <= END_DATE:
            continue
        values = [quote[field][index] for field in ("open", "high", "low", "close", "volume")]
        if any(value is None for value in values):
            missing_dates.append(value_date)
            continue
        rows.append({
            "date": value_date,
            "code": "510300.SS",
            "open": format(Decimal(str(values[0])).quantize(Decimal("0.000001")), "f"),
            "high": format(Decimal(str(values[1])).quantize(Decimal("0.000001")), "f"),
            "low": format(Decimal(str(values[2])).quantize(Decimal("0.000001")), "f"),
            "close": format(Decimal(str(values[3])).quantize(Decimal("0.000001")), "f"),
            "adjclose": format(Decimal(str(adjusted[index])).quantize(Decimal("0.000001")), "f") if adjusted[index] is not None else "",
            "volume": str(int(values[4])),
            "row_source": "yahoo",
        })
    events = []
    for event in (result.get("events", {}).get("dividends") or {}).values():
        value_date = datetime.fromtimestamp(int(event["date"]), tz=timezone.utc).date().isoformat()
        if START_DATE <= value_date <= END_DATE:
            events.append((value_date, Decimal(str(event["amount"]))))
    events.sort()
    splits = len(result.get("events", {}).get("splits") or {})
    return rows, events, splits, missing_dates


def independent_sina_daily(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"var\s+_data=\((\[.*\])\);\s*$", text, flags=re.S)
    if not match:
        raise RuntimeError("无法独立解析新浪日K原文")
    rows = []
    for row in json.loads(match.group(1)):
        rows.append({
            "date": row["day"],
            "code": "sh.510300",
            "open": format(Decimal(row["open"]).quantize(Decimal("0.000001")), "f"),
            "high": format(Decimal(row["high"]).quantize(Decimal("0.000001")), "f"),
            "low": format(Decimal(row["low"]).quantize(Decimal("0.000001")), "f"),
            "close": format(Decimal(row["close"]).quantize(Decimal("0.000001")), "f"),
            "volume": str(int(row["volume"])),
        })
    return rows


def independent_tencent_qfq(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("code") != 0:
        raise RuntimeError("腾讯原始响应状态错误")
    rows = []
    for row in payload.get("data", {}).get("sh510300", {}).get("qfqday") or []:
        rows.append({
            "date": row[0],
            "open": format(Decimal(row[1]).quantize(Decimal("0.000001")), "f"),
            "close": format(Decimal(row[2]).quantize(Decimal("0.000001")), "f"),
            "high": format(Decimal(row[3]).quantize(Decimal("0.000001")), "f"),
            "low": format(Decimal(row[4]).quantize(Decimal("0.000001")), "f"),
            "volume_lots": format(Decimal(row[5]), "f"),
        })
    if not rows:
        raise RuntimeError("腾讯原始响应日K为空")
    return rows


def independent_sina_factor_events(path: Path) -> list[tuple[str, Decimal, Decimal]]:
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
        cash = current - previous
        if cash <= 0:
            raise RuntimeError(f"新浪累计分红未单调增加: {row}")
        events.append((row["d"], cash, current))
        previous = current
    return events


def validate_manifest_files(snapshot: Path, manifest: dict[str, Any], errors: list[str]) -> None:
    entries = manifest.get("entries", [])
    if len({entry["path"] for entry in entries}) != len(entries):
        errors.append("清单包含重复路径")
    total_bytes = 0
    total_rows = 0
    for entry in entries:
        path = snapshot / entry["path"]
        if not path.is_file():
            errors.append(f"文件缺失: {entry['path']}")
            continue
        actual_bytes = path.stat().st_size
        total_bytes += actual_bytes
        total_rows += entry.get("rows", 0)
        if actual_bytes != entry["bytes"] or sha256_file(path) != entry["sha256"]:
            errors.append(f"大小或SHA256不一致: {entry['path']}")
    if len(entries) != manifest.get("files"):
        errors.append("文件数与清单不一致")
    if total_bytes != manifest.get("bytes"):
        errors.append("总字节数与清单不一致")
    if total_rows != manifest.get("rows"):
        errors.append("总行数与清单不一致")


def main() -> int:
    parser = argparse.ArgumentParser(description="独立验收510300跨源MVP快照")
    parser.add_argument("--snapshot", type=Path, help="待验收快照；默认最新pending快照")
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
    validate_manifest_files(snapshot, manifest, errors)
    if manifest.get("query_start_date") != START_DATE or manifest.get("query_end_date") != END_DATE:
        errors.append("清单日期边界与冻结契约不一致")

    _, basic = read_csv(snapshot / "security_basic.csv")
    if len(basic) != 1 or not (
        basic[0].get("code") == "sh.510300"
        and basic[0].get("type") == "5"
        and basic[0].get("status") == "1"
        and basic[0].get("ipoDate") == START_DATE
    ):
        errors.append(f"证券身份不符合冻结契约: {basic}")

    try:
        raw_yahoo_rows, raw_yahoo_dividends, raw_yahoo_splits, yahoo_missing_dates = independent_yahoo_rows(
            snapshot / "market_data" / "yahoo_chart_raw.json"
        )
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raw_yahoo_rows, raw_yahoo_dividends, raw_yahoo_splits, yahoo_missing_dates = [], [], -1, []
        errors.append(str(exc))
    try:
        raw_sina_rows = independent_sina_daily(snapshot / "market_data" / "sina_kline_recent_raw.js")
    except (OSError, KeyError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raw_sina_rows = []
        errors.append(str(exc))
    sina_fields, sina_rows = read_csv(snapshot / "market_data" / "sina_kline_recent.csv")
    expected_sina_fields = ["date", "code", "open", "high", "low", "close", "volume"]
    if sina_fields != expected_sina_fields or sina_rows != raw_sina_rows:
        errors.append("新浪标准化日K与原始响应独立解析结果不一致")
    if len(sina_rows) != 1023:
        errors.append(f"新浪近期日K不是1023日: {len(sina_rows)}")
    sina_by_date = {row["date"]: row for row in sina_rows}
    raw_yahoo_by_date = {row["date"]: row for row in raw_yahoo_rows}
    expected_combined = [dict(row) for row in raw_yahoo_rows]
    for missing_date in yahoo_missing_dates:
        source = sina_by_date.get(missing_date)
        if source is None:
            errors.append(f"新浪未覆盖Yahoo缺口: {missing_date}")
            continue
        expected_combined.append({
            "date": missing_date,
            "code": "510300.SS",
            "open": source["open"],
            "high": source["high"],
            "low": source["low"],
            "close": source["close"],
            "adjclose": "",
            "volume": source["volume"],
            "row_source": "sina_gap_fill",
        })
    expected_by_date = {row["date"]: row for row in expected_combined}
    for override_date in EXPECTED_YAHOO_OVERRIDES:
        source = sina_by_date.get(override_date)
        target = expected_by_date.get(override_date)
        if source is None or target is None:
            errors.append(f"异常覆盖日缺少Yahoo或新浪证据: {override_date}")
            continue
        target.update({
            "open": source["open"],
            "high": source["high"],
            "low": source["low"],
            "close": source["close"],
            "volume": source["volume"],
            "row_source": "sina_corroborated_override",
        })
    expected_combined.sort(key=lambda row: row["date"])
    yahoo_fields, yahoo_rows = read_csv(snapshot / "market_data" / "daily_unadjusted.csv")
    expected_fields = ["date", "code", "open", "high", "low", "close", "adjclose", "volume", "row_source"]
    if yahoo_fields != expected_fields:
        errors.append(f"Yahoo标准化字段不一致: {yahoo_fields}")
    if yahoo_rows != expected_combined:
        errors.append("跨源标准化行情与Yahoo/Sina原始响应独立解析结果不一致")
    if yahoo_missing_dates != EXPECTED_YAHOO_GAP:
        errors.append(f"Yahoo缺口与冻结契约不一致: {yahoo_missing_dates}")
    dates = [row["date"] for row in yahoo_rows]
    if dates != sorted(set(dates)):
        errors.append("Yahoo行情日期重复或未升序")
    if len(yahoo_rows) != EXPECTED_MARKET_DATES:
        errors.append(f"Yahoo完整交易日不是{EXPECTED_MARKET_DATES}: {len(yahoo_rows)}")
    if not dates or dates[0] != START_DATE or dates[-1] != END_DATE:
        errors.append(f"Yahoo日期边界错误: {dates[0] if dates else None}..{dates[-1] if dates else None}")
    for row in yahoo_rows:
        try:
            if any(Decimal(row[field]) <= 0 for field in ("open", "high", "low", "close")):
                errors.append(f"Yahoo非正价格: {row['date']}")
            if row["row_source"] == "yahoo" and Decimal(row["adjclose"]) <= 0:
                errors.append(f"Yahoo调整价非正: {row['date']}")
            if int(row["volume"]) < 0:
                errors.append(f"Yahoo负成交量: {row['date']}")
        except ValueError:
            errors.append(f"Yahoo数值无法解析: {row['date']}")

    _, overlap = read_csv(snapshot / "cross_validation" / "baostock_2026_unadjusted.csv")
    yahoo_by_date = {row["date"]: row for row in yahoo_rows}
    price_mismatches = []
    volume_mismatches = []
    for row in overlap:
        other = yahoo_by_date.get(row["date"])
        if other is None:
            errors.append(f"BaoStock重叠日期不在Yahoo历史中: {row['date']}")
            continue
        if any(abs(Decimal(row[field]) - Decimal(other[field])) > Decimal("0.0001") for field in ("open", "high", "low", "close")):
            price_mismatches.append(row["date"])
        if int(Decimal(row["volume"])) != int(other["volume"]):
            volume_mismatches.append(row["date"])
    if len(overlap) < 150:
        errors.append(f"BaoStock重叠期少于150日: {len(overlap)}")
    if price_mismatches or volume_mismatches:
        errors.append(f"跨源重叠数据不一致: price={price_mismatches[:5]} volume={volume_mismatches[:5]}")

    sina_price_mismatches = []
    sina_volume_mismatches = []
    for row in sina_rows:
        other = raw_yahoo_by_date.get(row["date"])
        if other is None:
            continue
        if any(abs(Decimal(row[field]) - Decimal(other[field])) > Decimal("0.0001") for field in ("open", "high", "low", "close")):
            sina_price_mismatches.append(row["date"])
        if int(row["volume"]) != int(other["volume"]):
            sina_volume_mismatches.append(row["date"])
    anomaly_dates = sorted(set(sina_price_mismatches) | set(sina_volume_mismatches))
    if anomaly_dates != EXPECTED_YAHOO_OVERRIDES:
        errors.append(f"Yahoo与新浪异常日不符合冻结证据: {anomaly_dates}")

    try:
        raw_tencent_rows = independent_tencent_qfq(snapshot / "market_data" / "tencent_qfq_recent_raw.json")
    except (OSError, KeyError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raw_tencent_rows = []
        errors.append(str(exc))
    tencent_fields, tencent_rows = read_csv(snapshot / "market_data" / "tencent_qfq_recent.csv")
    if tencent_fields != ["date", "open", "close", "high", "low", "volume_lots"] or tencent_rows != raw_tencent_rows:
        errors.append("腾讯标准化日K与原始响应独立解析结果不一致")
    tencent_by_date = {row["date"]: row for row in tencent_rows}
    for evidence_date in EXPECTED_YAHOO_OVERRIDES + EXPECTED_YAHOO_GAP:
        sina = sina_by_date.get(evidence_date)
        tencent = tencent_by_date.get(evidence_date)
        if sina is None or tencent is None:
            errors.append(f"腾讯或新浪缺少争议日证据: {evidence_date}")
            continue
        deltas = [
            Decimal(sina[field]) - Decimal(tencent[field])
            for field in ("open", "high", "low", "close")
        ]
        if max(deltas) - min(deltas) > Decimal("0.0001"):
            errors.append(f"腾讯未佐证新浪日内价格形状: {evidence_date} {deltas}")
        tencent_shares = Decimal(tencent["volume_lots"]) * Decimal("100")
        if abs(Decimal(sina["volume"]) - tencent_shares) > Decimal("100"):
            errors.append(f"腾讯未佐证新浪成交量: {evidence_date}")

    _, dividends = read_csv(snapshot / "fund_actions" / "dividends.csv")
    _, yahoo_dividend_rows = read_csv(snapshot / "fund_actions" / "yahoo_dividends.csv")
    _, yahoo_split_rows = read_csv(snapshot / "fund_actions" / "yahoo_splits.csv")
    try:
        sina_events = independent_sina_factor_events(snapshot / "fund_actions" / "sina_hfq_raw.js")
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        sina_events = []
        errors.append(str(exc))
    cumulative = Decimal("0")
    for index, row in enumerate(dividends):
        amount = Decimal(row["cash_per_share"])
        cumulative += amount
        if not row["record_date"] <= row["ex_date"] <= row["payment_date"] or amount <= 0:
            errors.append(f"分红日期或金额无效: {row}")
        if Decimal(row["cumulative_cash"]) != cumulative:
            errors.append(f"分红累计值不一致: {row}")
        if index < len(sina_events) and (row["ex_date"], amount, cumulative) != sina_events[index]:
            errors.append(f"标准化分红与新浪原文不一致: {row}")
    if len(dividends) != EXPECTED_DIVIDENDS or len(sina_events) != EXPECTED_DIVIDENDS:
        errors.append(f"新浪分红事件不是{EXPECTED_DIVIDENDS}: csv={len(dividends)} raw={len(sina_events)}")
    if cumulative != Decimal("0.880"):
        errors.append(f"累计现金分红不是0.880: {cumulative}")
    yahoo_csv_events = [(row["date"], Decimal(row["amount"])) for row in yahoo_dividend_rows]
    if yahoo_csv_events != raw_yahoo_dividends:
        errors.append("Yahoo分红CSV与原始响应不一致")
    sina_pairs = {(row["ex_date"], Decimal(row["cash_per_share"])) for row in dividends}
    if not set(yahoo_csv_events).issubset(sina_pairs) or len(yahoo_csv_events) != 13:
        errors.append(f"Yahoo 13次分红未被新浪14次事件完整覆盖: {yahoo_csv_events}")
    if raw_yahoo_splits != 0 or yahoo_split_rows:
        errors.append("Yahoo返回未处理的份额拆分事件")

    report = {
        "status": "FAIL" if errors else "PASS_WITH_KNOWN_LIMITS",
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "snapshot": snapshot.name,
        "market_dates": len(yahoo_rows),
        "min_market_date": dates[0] if dates else None,
        "max_market_date": dates[-1] if dates else None,
        "baostock_overlap_dates": len(overlap),
        "sina_recent_dates": len(sina_rows),
        "tencent_recent_dates": len(tencent_rows),
        "yahoo_missing_dates": yahoo_missing_dates,
        "yahoo_override_dates": anomaly_dates,
        "dividend_events": len(dividends),
        "cumulative_cash_per_share": format(cumulative, "f"),
        "yahoo_dividend_events": len(yahoo_dividend_rows),
        "errors": errors,
        "warnings": warnings,
        "known_limits": manifest.get("limits", []),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if errors:
        manifest["status"] = "REJECTED_VALIDATION_FAILED"
        manifest["independent_validation"] = {
            "status": "FAIL",
            "report": REPORT_PATH.relative_to(PROJECT_ROOT).as_posix(),
            "validated_at": report["validated_at"],
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
