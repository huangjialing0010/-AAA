#!/usr/bin/env python
"""一次性复制旧项目最新的不复权日线，生成独立、可追溯快照。"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = Path(r"D:\大A\data\daily_kline")
SNAPSHOT = PROJECT_ROOT / "data" / "imports" / "legacy_unadjusted_20260903"
EXPECTED_COLUMNS = ["日期", "开盘", "最高", "最低", "收盘", "成交量", "amount", "outstanding_share", "turnover"]
FILENAME_PATTERN = re.compile(r"^(\d{6})_(\d{8})\.csv$")


class ImportErrorWithContext(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_latest() -> dict[str, Path]:
    selected: dict[str, tuple[str, Path]] = {}
    for path in SOURCE_DIR.glob("*.csv"):
        match = FILENAME_PATTERN.match(path.name)
        if not match:
            continue
        code, snapshot_date = match.groups()
        current = selected.get(code)
        if current is None or snapshot_date > current[0]:
            selected[code] = (snapshot_date, path)
    return {code: item[1] for code, item in selected.items()}


def validate_source(path: Path, code: str) -> dict[str, Any]:
    seen_dates: set[str] = set()
    min_date: str | None = None
    max_date: str | None = None
    rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != EXPECTED_COLUMNS:
            raise ImportErrorWithContext(f"{path.name} 字段不符合不复权候选契约: {reader.fieldnames}")
        for row in reader:
            trade_date = row["日期"].strip()
            date.fromisoformat(trade_date)
            if trade_date in seen_dates:
                raise ImportErrorWithContext(f"{code} 日期重复: {trade_date}")
            seen_dates.add(trade_date)
            try:
                open_, high, low, close = map(float, (row["开盘"], row["最高"], row["最低"], row["收盘"]))
            except ValueError as exc:
                raise ImportErrorWithContext(f"{code} {trade_date} OHLC不可解析") from exc
            if min(open_, high, low, close) <= 0 or high < max(open_, close, low) or low > min(open_, close, high):
                raise ImportErrorWithContext(f"{code} {trade_date} OHLC关系异常")
            rows += 1
            min_date = trade_date if min_date is None or trade_date < min_date else min_date
            max_date = trade_date if max_date is None or trade_date > max_date else max_date
    if rows == 0:
        raise ImportErrorWithContext(f"{code} 没有数据行")
    return {"rows": rows, "min_date": min_date, "max_date": max_date}


def main() -> int:
    if not SOURCE_DIR.is_dir():
        raise ImportErrorWithContext(f"旧项目日线目录不存在: {SOURCE_DIR}")
    if SNAPSHOT.exists():
        raise ImportErrorWithContext(f"目标快照已存在，禁止覆盖: {SNAPSHOT}")
    selected = select_latest()
    if len(selected) != 766:
        raise ImportErrorWithContext(f"预计766只股票，实际选择{len(selected)}只")
    target_dir = SNAPSHOT / "daily_unadjusted"
    target_dir.mkdir(parents=True)
    entries = []
    max_date_counts: Counter[str] = Counter()
    for index, (code, source) in enumerate(sorted(selected.items()), start=1):
        summary = validate_source(source, code)
        source_hash = sha256_file(source)
        target = target_dir / source.name
        shutil.copy2(source, target)
        target_hash = sha256_file(target)
        if source_hash != target_hash or source.stat().st_size != target.stat().st_size:
            raise ImportErrorWithContext(f"复制校验失败: {source.name}")
        max_date_counts[summary["max_date"]] += 1
        entries.append({
            "category": "unadjusted_daily_candidate",
            "stock_code": code,
            "source": str(source),
            "target": target.relative_to(SNAPSHOT).as_posix(),
            "bytes": target.stat().st_size,
            "sha256": target_hash,
            "source_target_hash_equal": True,
            **summary,
        })
        if index % 100 == 0 or index == len(selected):
            print(f"progress {index}/{len(selected)}", flush=True)
    manifest = {
        "schema_version": 1,
        "status": "SEALED",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source_root": str(SOURCE_DIR),
        "snapshot": str(SNAPSHOT),
        "copy_mode": "one_time_copy_no_runtime_dependency",
        "category": "unadjusted_daily_candidate",
        "adjustment": "none",
        "selection_rule": "每个六位股票代码只取文件名日期最新的无_qfq CSV",
        "evidence": {
            "baostock_compared_stocks": 144,
            "baostock_common_dates": 3889,
            "exact_close_stocks": 144,
            "exact_volume_stocks": 144,
        },
        "summary": {
            "files": len(entries),
            "rows": sum(item["rows"] for item in entries),
            "bytes": sum(item["bytes"] for item in entries),
            "source_target_hash_equal": all(item["source_target_hash_equal"] for item in entries),
            "global_min_date": min(item["min_date"] for item in entries),
            "global_max_date": max(item["max_date"] for item in entries),
            "max_date_counts": dict(sorted(max_date_counts.items())),
        },
        "known_limits": [
            "缺少tradestatus、isST和preclose字段",
            "数据截止日期不晚于2026-08-07，仍需短区间增量补齐",
            "766只股票不是完整历史A股股票池",
            "进入标准层和回测前仍需独立质量验收",
        ],
        "files": entries,
    }
    (SNAPSHOT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "snapshot": str(SNAPSHOT),
        "status": manifest["status"],
        **manifest["summary"],
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
