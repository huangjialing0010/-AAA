#!/usr/bin/env python
"""抓取两个季度的全市场业绩报表，验证公告日期候选字段。"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.sources.eastmoney_disclosure import (  # noqa: E402
    EastmoneyDisclosureSource,
    normalize_api_date,
)


PERIODS = ("2025-12-31", "2026-06-30")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_raw(path: Path, rows: list[dict[str, Any]]) -> list[str]:
    fields = sorted({field for row in rows for field in row})
    if not fields:
        raise RuntimeError(f"没有可写字段: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return fields


def latest_baostock_pilot() -> Path | None:
    candidates = []
    for path in (PROJECT_ROOT / "data" / "imports").glob("baostock_pilot_*"):
        manifest = path / "manifest.json"
        if manifest.is_file():
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            if payload.get("status") == "SEALED":
                candidates.append(path)
    return sorted(candidates)[-1] if candidates else None


def baostock_sample_dates() -> dict[tuple[str, str], str]:
    pilot = latest_baostock_pilot()
    if pilot is None:
        return {}
    result = {}
    for path in (pilot / "financial_profit").glob("*.csv"):
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                code = row.get("code", "").split(".")[-1]
                period = row.get("statDate", "")
                if code and period and row.get("pubDate"):
                    result[(code, period)] = row["pubDate"]
    return result


def main() -> int:
    started = datetime.now(timezone.utc)
    run_id = started.strftime("eastmoney_disclosure_pilot_%Y%m%dT%H%M%SZ")
    output_root = PROJECT_ROOT / "data" / "imports" / run_id
    if output_root.exists():
        raise RuntimeError(f"输出目录已存在，禁止覆盖: {output_root}")
    output_root.mkdir(parents=True)
    entries = []
    period_rows: dict[str, list[dict[str, Any]]] = {}
    source = EastmoneyDisclosureSource()

    for period in PERIODS:
        result = source.period(period)
        if not 3000 <= len(result.rows) <= 7000:
            raise RuntimeError(f"{period} A股记录数异常: {len(result.rows)}")
        codes = [str(row["SECURITY_CODE"]).zfill(6) for row in result.rows]
        if len(codes) != len(set(codes)):
            raise RuntimeError(f"{period} 股票代码重复")
        invalid_dates = []
        for row in result.rows:
            disclosure = normalize_api_date(row.get("UPDATE_DATE"))
            if not disclosure or date.fromisoformat(disclosure) <= date.fromisoformat(period):
                invalid_dates.append(row.get("SECURITY_CODE"))
        if invalid_dates:
            raise RuntimeError(f"{period} 公告日期缺失或不晚于报告期: {invalid_dates[:10]}")
        path = output_root / "quarterly_reports" / f"{period}.csv"
        fields = write_raw(path, result.rows)
        period_rows[period] = result.rows
        entries.append({
            "path": path.relative_to(output_root).as_posix(),
            "report_period": period,
            "pages": result.pages,
            "source_rows": result.source_rows,
            "security_type_counts": result.security_type_counts,
            "rows": len(result.rows),
            "columns": fields,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
        print(
            f"period {period}: {len(result.rows)} A-share rows / "
            f"{result.source_rows} source rows / {result.pages} pages",
            flush=True,
        )

    bao_dates = baostock_sample_dates()
    crosscheck = []
    for period, rows in period_rows.items():
        by_code = {str(row["SECURITY_CODE"]).zfill(6): row for row in rows}
        for (code, stat_date), bao_date in sorted(bao_dates.items()):
            if stat_date != period or code not in by_code:
                continue
            eastmoney_date = normalize_api_date(by_code[code]["UPDATE_DATE"])
            crosscheck.append({
                "stock_code": code,
                "report_period": period,
                "baostock_pub_date": bao_date,
                "eastmoney_update_date": eastmoney_date,
                "exact_match": bao_date == eastmoney_date,
            })

    manifest = {
        "schema_version": 1,
        "status": "SEALED",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": "Eastmoney RPT_LICO_FN_CPD",
        "purpose": "bulk_financial_disclosure_date_candidate_pilot",
        "security_scope": "客户端保留 SECURITY_TYPE_CODE=058001001（A股，含沪深北市场）",
        "files": len(entries),
        "rows": sum(entry["rows"] for entry in entries),
        "bytes": sum(entry["bytes"] for entry in entries),
        "entries": entries,
        "baostock_sample_crosscheck": crosscheck,
        "limits": [
            "UPDATE_DATE尚未证明是首次公开披露日期",
            "仅验证两个报告期",
            "东方财富服务端不接受证券类型组合过滤；抓取全响应后在客户端只保留A股类型，并记录来源类型计数",
            "尚未与交易所或巨潮资讯公告原文交叉核对",
        ],
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "snapshot": str(output_root), "status": manifest["status"],
        "files": manifest["files"], "rows": manifest["rows"],
        "crosscheck": crosscheck,
        "elapsed_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
