#!/usr/bin/env python
"""从已封存快照生成510300 MVP标准行情和分红。"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.mvp_data import build_total_return_rows  # noqa: E402


IMPORT_ROOT = PROJECT_ROOT / "data" / "imports"
OUTPUT_ROOT = PROJECT_ROOT / "data" / "canonical" / "mvp_510300_v2"
QUALITY_PATH = PROJECT_ROOT / "reports" / "mvp_510300_v2_canonical_quality.json"
DAILY_FIELDS = [
    "trade_date", "stock_code", "open", "high", "low", "close", "volume",
    "row_source", "dividend_ex_cash_per_share", "total_return_index",
]
DIVIDEND_FIELDS = [
    "record_date", "ex_date", "payment_date", "cash_per_share", "cumulative_cash",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def latest_sealed_snapshot() -> Path:
    candidates = []
    for path in IMPORT_ROOT.glob("mvp_etf_510300_*"):
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") == "SEALED" and manifest.get("independent_validation", {}).get("status") == "PASS_WITH_KNOWN_LIMITS":
            candidates.append(path)
    if not candidates:
        raise RuntimeError("没有通过独立验收的510300 MVP快照")
    return sorted(candidates)[-1]


def entry(root: Path, path: Path, rows: int) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "rows": rows,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def main() -> int:
    if OUTPUT_ROOT.exists():
        raise RuntimeError(f"MVP标准目录已存在，禁止覆盖: {OUTPUT_ROOT}")
    snapshot = latest_sealed_snapshot()
    source_manifest_path = snapshot / "manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    market_rows = read_csv(snapshot / "market_data" / "daily_unadjusted.csv")
    dividend_rows = read_csv(snapshot / "fund_actions" / "dividends.csv")
    daily_rows = build_total_return_rows(market_rows, dividend_rows)

    daily_path = OUTPUT_ROOT / "daily.csv"
    dividend_path = OUTPUT_ROOT / "dividends.csv"
    write_csv(daily_path, DAILY_FIELDS, daily_rows)
    write_csv(dividend_path, DIVIDEND_FIELDS, dividend_rows)
    entries = [entry(OUTPUT_ROOT, daily_path, len(daily_rows)), entry(OUTPUT_ROOT, dividend_path, len(dividend_rows))]
    non_primary = [row["trade_date"] for row in daily_rows if row["row_source"] != "tencent_unadjusted"]
    quality = {
        "status": "PASS_WITH_KNOWN_LIMITS",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "input_snapshot": snapshot.name,
        "market_dates": len(daily_rows),
        "min_date": daily_rows[0]["trade_date"],
        "max_date": daily_rows[-1]["trade_date"],
        "dividend_events": len(dividend_rows),
        "non_primary_dates": non_primary,
        "total_return_index_start": daily_rows[0]["total_return_index"],
        "total_return_index_end": daily_rows[-1]["total_return_index"],
        "known_limits": source_manifest.get("limits", []) + [
            "总回报指数按除息日每份现金分红再投资口径构建，只用于趋势信号，不作为成交价格",
            "标准数据不采用任何外部复权收盘价，避免使用未完全解释的复权序列",
        ],
    }
    QUALITY_PATH.write_text(json.dumps(quality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "status": "SEALED",
        "generated_at": quality["generated_at"],
        "input": {
            "snapshot": snapshot.relative_to(PROJECT_ROOT).as_posix(),
            "manifest_sha256": sha256_file(source_manifest_path),
        },
        "transform": {
            "version": "mvp-etf-canonical-v1",
            "execution_price": "unadjusted_ohlc",
            "signal_price": "internally_built_cash_dividend_total_return_index",
        },
        "output": {
            "files": len(entries),
            "rows": sum(item["rows"] for item in entries),
            "bytes": sum(item["bytes"] for item in entries),
            "entries": entries,
        },
        "quality_report": {
            "path": QUALITY_PATH.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": sha256_file(QUALITY_PATH),
            "status": quality["status"],
        },
    }
    (OUTPUT_ROOT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": manifest["status"],
        "input_snapshot": snapshot.name,
        "market_dates": len(daily_rows),
        "dividend_events": len(dividend_rows),
        "non_primary_dates": non_primary,
        "total_return_index_end": daily_rows[-1]["total_return_index"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
