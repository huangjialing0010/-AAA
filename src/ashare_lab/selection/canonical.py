"""把同批次双口径分段日线转换为逐股票标准数据。"""

from __future__ import annotations

import csv
import hashlib
import json
from decimal import Decimal
from collections.abc import Iterable
from pathlib import Path
from typing import Any


CANONICAL_FIELDS = (
    "trade_date",
    "code",
    "open",
    "high",
    "low",
    "close",
    "preclose",
    "volume",
    "amount",
    "turn",
    "tradestatus",
    "pct_chg",
    "is_st",
    "open_qfq",
    "high_qfq",
    "low_qfq",
    "close_qfq",
    "preclose_qfq",
    "raw_source_code",
    "qfq_source_code",
    "qfq_scale",
    "qfq_status",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def merge_dual_price_rows(
    unadjusted_rows: Iterable[dict[str, str]],
    qfq_rows: Iterable[dict[str, str]],
    *,
    expected_code: str,
) -> list[dict[str, str]]:
    raw = list(unadjusted_rows)
    qfq = list(qfq_rows)
    raw_by_date = {row["date"]: row for row in raw}
    qfq_by_date = {row["date"]: row for row in qfq}
    if len(raw_by_date) != len(raw) or len(qfq_by_date) != len(qfq):
        raise ValueError(f"{expected_code} 分段内存在重复日期")
    if set(raw_by_date) != set(qfq_by_date):
        raise ValueError(f"{expected_code} 双口径日期集合不一致")

    output: list[dict[str, str]] = []
    for trade_date in sorted(raw_by_date):
        raw_row = raw_by_date[trade_date]
        qfq_row = qfq_by_date[trade_date]
        raw_code = raw_row["code"].split(".")[-1]
        qfq_code = qfq_row["code"].split(".")[-1]
        if raw_code != expected_code or qfq_code != expected_code:
            raise ValueError(f"{expected_code} 文件内代码不一致")
        missing_qfq = (expected_code == "688223" and trade_date == "2022-01-26"
                       and qfq_row.get("adjustflag") == "3")
        if raw_row.get("adjustflag") != "3" or (qfq_row.get("adjustflag") != "2" and not missing_qfq):
            raise ValueError(f"{expected_code} 复权标记不一致")
        if missing_qfq and any(raw_row.get(f, "") != qfq_row.get(f, "")
                               for f in ("open", "high", "low", "close", "preclose", "turn", "pctChg", "isST")):
            raise ValueError("688223异常响应并非同批不复权原始值")
        for field in ("volume", "amount", "tradestatus"):
            if raw_row.get(field, "") != qfq_row.get(field, ""):
                raise ValueError(f"{expected_code} {trade_date} 双口径{field}不一致")
        output.append({
            "trade_date": trade_date,
            "code": expected_code,
            "open": raw_row.get("open", ""),
            "high": raw_row.get("high", ""),
            "low": raw_row.get("low", ""),
            "close": raw_row.get("close", ""),
            "preclose": raw_row.get("preclose", ""),
            "volume": raw_row.get("volume", ""),
            "amount": raw_row.get("amount", ""),
            "turn": raw_row.get("turn", ""),
            "tradestatus": raw_row.get("tradestatus", ""),
            "pct_chg": raw_row.get("pctChg", ""),
            "is_st": raw_row.get("isST", ""),
            "open_qfq": qfq_row.get("open", ""),
            "high_qfq": qfq_row.get("high", ""),
            "low_qfq": qfq_row.get("low", ""),
            "close_qfq": qfq_row.get("close", ""),
            "preclose_qfq": qfq_row.get("preclose", ""),
            "raw_source_code": raw_row.get("_source_code", raw_code),
            "qfq_source_code": qfq_row.get("_source_code", qfq_code),
            "qfq_scale": qfq_row.get("_scale", "1"),
            "qfq_status": "SOURCE_UNADJUSTED_RESPONSE" if missing_qfq else "AVAILABLE",
        })
        if missing_qfq:
            for field in ("open", "high", "low", "close", "preclose"):
                output[-1][field + "_qfq"] = ""
            output[-1]["qfq_scale"] = ""
    return output


def merge_code_segments(
    snapshot: Path,
    code: str,
    windows: Iterable[tuple[str, str]],
) -> list[dict[str, str]]:
    combined: list[dict[str, str]] = []
    for start_date, end_date in windows:
        name = f"{start_date}_{end_date}.csv"
        combined.extend(merge_dual_price_rows(
            read_csv(snapshot / "daily_unadjusted" / code / name),
            read_csv(snapshot / "daily_qfq" / code / name),
            expected_code=code,
        ))
    dates = [row["trade_date"] for row in combined]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError(f"{code} 分段合并后日期重复或未升序")
    return combined


def rows_from_manifest_entry(
    snapshot: Path,
    entry: dict[str, Any],
    *,
    effective_code: str,
) -> list[dict[str, str]]:
    """验证原始响应身份后，在内存中映射为连续的有效证券代码。"""
    rows = read_csv(snapshot / entry["relative_path"])
    response_code = entry.get("response_stock_code", entry["stock_code"])
    for row in rows:
        if row["code"].split(".")[-1] != response_code:
            raise ValueError(f"{entry['relative_path']} 原始响应代码不一致")
        missing_qfq = (effective_code == response_code == "688223" and entry["adjustflag"] == "2"
                       and row["date"] == "2022-01-26" and row.get("adjustflag") == "3"
                       and entry.get("missing_qfq_dates") == ["2022-01-26"])
        if row.get("adjustflag") != entry["adjustflag"] and not missing_qfq:
            raise ValueError(f"{entry['relative_path']} 原始响应复权标记不一致")
        if not entry["start_date"] <= row["date"] <= entry["end_date"]:
            raise ValueError(f"{entry['relative_path']} 原始响应日期越界")
    normalized = []
    for row in rows:
        copy = dict(row)
        copy["_source_code"] = response_code
        copy["code"] = effective_code
        normalized.append(copy)
    return normalized


def merge_code_entries(
    snapshot: Path,
    code: str,
    entries: Iterable[dict[str, Any]],
    *,
    lineage_context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """按原始清单合并不等长分段，并仅在标准层统一证券身份。"""
    grouped: dict[str, list[dict[str, str]]] = {
        "daily_unadjusted": [],
        "daily_qfq": [],
    }
    for entry in sorted(entries, key=lambda item: (item["category"], item["start_date"], item["end_date"])):
        if entry["stock_code"] != code:
            raise ValueError(f"{code} 清单混入其他有效证券代码")
        category = entry["category"]
        if category not in grouped:
            raise ValueError(f"{code} 未知行情类别: {category}")
        grouped[category].extend(rows_from_manifest_entry(snapshot, entry, effective_code=code))

    for category, rows in grouped.items():
        dates = [row["date"] for row in rows]
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise ValueError(f"{code} {category} 分段合并后日期重复或未升序")
    if code == "302132" and lineage_context is not None:
        apply_lineage(grouped, lineage_context)
    elif code == "302132" and any(r.get("_source_code") == "300114" for r in grouped["daily_qfq"]):
        raise ValueError("302132旧代码数据必须有已封存的桥接与补缺证据")
    return merge_dual_price_rows(
        grouped["daily_unadjusted"],
        grouped["daily_qfq"],
        expected_code=code,
    )


def load_lineage_context(snapshot: Path, reference: dict[str, Any]) -> dict[str, Any]:
    root = snapshot.parent/reference["snapshot"]
    if root.resolve().parent != snapshot.parent.resolve() or not root.name.startswith("lineage_302132_"):
        raise ValueError("沿革证据路径非法")
    mp = root/"manifest.json"
    if hashlib.sha256(mp.read_bytes()).hexdigest() != reference["manifest_sha256"]:
        raise ValueError("沿革证据清单哈希已变更")
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    if manifest.get("status") != "SEALED" or manifest.get("independent_validation", {}).get("status") != "PASS":
        raise ValueError("沿革证据未独立封存")
    for e in manifest["entries"]:
        path = root/e["path"]
        if path.resolve().parent != root.resolve() or hashlib.sha256(path.read_bytes()).hexdigest() != e["sha256"]:
            raise ValueError("沿革证据内容变更")
    return dict(root=root, **manifest["independent_validation"])


def apply_lineage(grouped: dict[str, list[dict[str, str]]], context: dict[str, Any]) -> None:
    raw = {r["date"]:r for r in grouped["daily_unadjusted"]}
    qfq = {r["date"]:r for r in grouped["daily_qfq"]}
    a, b = context["previous_date"], context["effective_date"]
    if any(d not in raw or d not in qfq for d in (a,b)):
        raise ValueError("沿革边界行情缺失")
    if Decimal(raw[a]["close"]) != Decimal(raw[b]["preclose"]):
        raise ValueError("不复权沿革边界不连续")
    scale = Decimal(qfq[b]["preclose"])/Decimal(qfq[a]["close"])
    if scale != Decimal(context["bridge_factor"]):
        raise ValueError("本批次桥接比例与独立证据不一致")
    for r in grouped["daily_qfq"]:
        expected = "300114" if r["date"] <= a else "302132"
        if r.get("_source_code") != expected:
            raise ValueError("沿革响应代码与生效日期不一致")
        if r["date"] <= a:
            for f in ("open", "high", "low", "close", "preclose"):
                if r.get(f, "") != "": r[f] = format(Decimal(r[f])*scale, "f")
            r["_scale"] = str(scale)
    history=[]
    for filename in context["raw_history_sources"]:
        for r in read_csv(context["root"]/filename):
            if r["code"] != "sz.300114" or r["adjustflag"] != "3" or r["date"]>a:
                raise ValueError("旧代码不复权来源身份错误")
            r.update(code="302132", _source_code="300114")
            history.append(r)
    grouped["daily_unadjusted"] = sorted(history + [r for r in grouped["daily_unadjusted"] if r["date"]>a], key=lambda r:r["date"])
