#!/usr/bin/env python
"""核对观察名单半年报：官方PDF归母净利润、东方财富候选值与BaoStock时点。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EAST_ROOT = PROJECT_ROOT / "data" / "imports" / "eastmoney_disclosure_pilot_20260903T061458Z"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_and_verify_manifest(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "SEALED":
        raise RuntimeError(f"快照未封存: {root}")
    for item in manifest.get("entries", []):
        path = root / item["path"]
        if not path.is_file() or path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            raise RuntimeError(f"快照文件大小或哈希不一致: {path}")
    return manifest


def find_metric_page(pdf_path: Path, expected_value: float) -> tuple[int | None, str]:
    expected = f"{expected_value:,.2f}"
    for index, page in enumerate(PdfReader(str(pdf_path)).pages):
        text = page.extract_text() or ""
        normalized = re.sub(r"\s+", "", text)
        if "归属于上市公司股东的净利润" in normalized and expected in normalized:
            return index, expected
    return None, expected


def validate(official_root: Path, profit_root: Path, watchlist_report: Path) -> dict[str, Any]:
    errors: list[str] = []
    official = load_and_verify_manifest(official_root)
    profit = load_and_verify_manifest(profit_root)
    report_manifest = json.loads((watchlist_report / "manifest.json").read_text(encoding="utf-8"))
    watch_entry = next(item for item in report_manifest["entries"] if item["path"] == "watchlist.csv")
    watch_path = watchlist_report / "watchlist.csv"
    if watch_path.stat().st_size != watch_entry["bytes"] or sha256_file(watch_path) != watch_entry["sha256"]:
        raise RuntimeError("观察名单文件与清单不一致")
    watchlist = read_csv(watch_path)
    codes = [row["stock_code"] for row in watchlist]

    east_manifest = load_and_verify_manifest(EAST_ROOT)
    east_rows = {
        row["SECURITY_CODE"].zfill(6): row
        for row in read_csv(EAST_ROOT / "quarterly_reports" / "2026-06-30.csv")
    }
    official_entries = {item["stock_code"]: item for item in official["entries"]}
    profit_entries = {item["stock_code"]: item for item in profit["entries"]}
    if set(codes) != set(official_entries) or set(codes) != set(profit_entries):
        errors.append("观察名单、官方PDF与BaoStock盈利记录的代码集合不一致")

    checks: list[dict[str, Any]] = []
    for code in codes:
        east = east_rows.get(code)
        if east is None:
            errors.append(f"{code} 缺少东方财富候选记录")
            continue
        parent_profit = float(east["PARENT_NETPROFIT"])
        pdf_entry = official_entries[code]
        page_index, formatted_value = find_metric_page(official_root / pdf_entry["path"], parent_profit)
        profit_rows = read_csv(profit_root / profit_entries[code]["path"])
        if len(profit_rows) != 1:
            errors.append(f"{code} BaoStock盈利记录行数错误")
            continue
        bao = profit_rows[0]
        bao_profit = float(bao["netProfit"])
        tolerance = max(1.0, abs(parent_profit) * 1e-8)
        east_vs_official = page_index is not None
        bao_matches_parent = abs(bao_profit - parent_profit) <= tolerance
        if not east_vs_official or parent_profit <= 0:
            errors.append(f"{code} 官方PDF未确认正的归母净利润")
        if bao_matches_parent:
            errors.append(f"{code} BaoStock netProfit意外等于归母净利润，需复核字段假设")
        checks.append({
            "stock_code": code,
            "name": next(row["name"] for row in watchlist if row["stock_code"] == code),
            "official_pdf": pdf_entry["path"],
            "official_url": pdf_entry["source_url"],
            "metric_page_zero_based": page_index,
            "official_parent_netprofit_formatted": formatted_value if east_vs_official else None,
            "eastmoney_parent_netprofit": parent_profit,
            "baostock_pubDate": bao["pubDate"],
            "baostock_netProfit": bao_profit,
            "baostock_minus_parent": bao_profit - parent_profit,
            "baostock_field_matches_parent": bao_matches_parent,
            "h1_positive_gate": "PASS" if east_vs_official and parent_profit > 0 else "FAIL",
        })

    return {
        "status": "PASS" if not errors else "FAIL",
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "admission": "H1_POSITIVE_PARENT_NETPROFIT_CONFIRMED" if not errors else "H1_REVIEW",
        "watchlist_report": watchlist_report.name,
        "official_snapshot": official_root.name,
        "baostock_profit_snapshot": profit_root.name,
        "eastmoney_snapshot": EAST_ROOT.name,
        "eastmoney_manifest_sha256": sha256_file(EAST_ROOT / "manifest.json"),
        "eastmoney_status": east_manifest["status"],
        "checks": checks,
        "errors": errors,
        "limits": [
            "本核对只确认2026半年报归母净利润为正及披露时点，不证明未来盈利",
            "BaoStock netProfit与归母净利润口径不同，不用于替代官方归母净利润",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("official_snapshot", type=Path)
    parser.add_argument("profit_snapshot", type=Path)
    parser.add_argument("watchlist_report", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.official_snapshot.resolve(), args.profit_snapshot.resolve(), args.watchlist_report.resolve())
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
