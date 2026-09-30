"""为一次性数据快照生成可追溯清单并核对来源与目标哈希。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


DATE_COLUMNS = ("日期", "报告期", "date", "trade_date")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def csv_summary(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        date_column = next((name for name in DATE_COLUMNS if name in columns), None)
        row_count = 0
        minimum = None
        maximum = None
        for row in reader:
            row_count += 1
            if date_column:
                value = (row.get(date_column) or "").strip()
                if value:
                    minimum = value if minimum is None or value < minimum else minimum
                    maximum = value if maximum is None or value > maximum else maximum
    return {
        "rows": row_count,
        "date_column": date_column,
        "date_min": minimum,
        "date_max": maximum,
        "columns": columns,
    }


def source_path(source_root: Path, relative_target: Path) -> Path:
    category = relative_target.parts[0]
    name = relative_target.name
    if category == "daily_qfq":
        return source_root / "data" / "daily_kline" / name
    if category == "financial_raw":
        return source_root / "data" / "financials" / "raw" / name
    if category == "industry_index":
        return source_root / "data" / "sw_index" / name
    if category == "reference":
        return source_root / "data" / "market" / name
    raise ValueError(f"未识别的数据类别: {category}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    snapshot = args.snapshot.resolve()
    files = sorted(
        path for path in snapshot.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "README.md"}
    )

    entries = []
    schemas: dict[str, Counter] = defaultdict(Counter)
    category_rows = Counter()
    category_bytes = Counter()

    for target in files:
        relative = target.relative_to(snapshot)
        source = source_path(source_root, relative)
        if not source.is_file():
            raise FileNotFoundError(source)
        source_hash = sha256(source)
        target_hash = sha256(target)
        if source_hash != target_hash:
            raise RuntimeError(f"复制后哈希不一致: {relative}")

        summary = csv_summary(target) if target.suffix.lower() == ".csv" else {
            "rows": None,
            "date_column": None,
            "date_min": None,
            "date_max": None,
            "columns": None,
        }
        category = relative.parts[0]
        if summary["columns"] is not None:
            schemas[category][tuple(summary["columns"])] += 1
        if summary["rows"] is not None:
            category_rows[category] += summary["rows"]
        category_bytes[category] += target.stat().st_size

        entries.append({
            "category": category,
            "source": str(source),
            "target": relative.as_posix(),
            "bytes": target.stat().st_size,
            "sha256": target_hash,
            "source_target_hash_equal": True,
            **{key: summary[key] for key in (
                "rows", "date_column", "date_min", "date_max"
            )},
        })

    category_summary = {}
    for category in sorted({entry["category"] for entry in entries}):
        category_summary[category] = {
            "files": sum(entry["category"] == category for entry in entries),
            "bytes": category_bytes[category],
            "rows": category_rows[category],
            "schemas": [
                {"files": count, "columns": list(columns)}
                for columns, count in schemas[category].items()
            ],
        }

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_root": str(source_root),
        "snapshot": str(snapshot),
        "copy_mode": "one_time_copy_no_runtime_dependency",
        "selection_rules": {
            "daily_qfq": "每个六位股票代码只取文件名日期最新的 *_qfq.csv",
            "financial_raw": "复制 raw 目录全部 CSV",
            "reference": "仅复制白名单市场参考文件",
            "industry_index": "复制全部31个申万一级行业指数 CSV",
        },
        "known_limits": [
            "日线数据截止2026-08-07，需由新项目补齐",
            "股票池与行业映射为当前快照，不代表历史状态",
            "财务数据没有真实公告日期，不能直接用于正式回测",
            "沪深300基准早期来源与零成交量记录需要再次核实",
        ],
        "summary": {
            "files": len(entries),
            "bytes": sum(entry["bytes"] for entry in entries),
            "all_source_target_hash_equal": all(
                entry["source_target_hash_equal"] for entry in entries
            ),
            "categories": category_summary,
        },
        "files": entries,
    }
    output = snapshot / "manifest.json"
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

