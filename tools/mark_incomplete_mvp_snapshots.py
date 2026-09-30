#!/usr/bin/env python
"""为抓取阶段中止且缺少清单的MVP目录补充拒绝状态，不修改原始文件。"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORT_ROOT = PROJECT_ROOT / "data" / "imports"
REASONS = {
    "mvp_etf_510300_20260903T071840Z": "BaoStock对ETF的前复权请求返回不复权标记，且历史覆盖不足",
    "mvp_etf_510300_20260903T072658Z": "Yahoo冻结区间发现2025-10-24完整行情为空",
    "mvp_etf_510300_20260903T073036Z": "新浪历史分红日期解析未兼容一位月份，抓取在封存前中止",
    "mvp_etf_510300_tencent_20260903T080841Z": "腾讯分段开始日为休市日，旧边界校验错误地要求首行等于参数日",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_rows(path: Path) -> int | None:
    if path.suffix.lower() != ".csv":
        return None
    with path.open("r", encoding="utf-8", newline="") as handle:
        return max(sum(1 for _ in csv.reader(handle)) - 1, 0)


def main() -> int:
    changed = []
    for name, reason in REASONS.items():
        root = IMPORT_ROOT / name
        manifest_path = root / "manifest.json"
        if not root.is_dir():
            raise RuntimeError(f"待标记目录不存在: {root}")
        if manifest_path.exists():
            continue
        entries = []
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            item = {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            rows = count_rows(path)
            if rows is not None:
                item["rows"] = rows
            entries.append(item)
        payload = {
            "schema_version": 1,
            "status": "REJECTED_INCOMPLETE",
            "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "admitted_to_downstream": False,
            "reason": reason,
            "files": len(entries),
            "bytes": sum(item["bytes"] for item in entries),
            "entries": entries,
            "preservation": "原始文件保留，未删除、未改写；该目录禁止进入标准化和回测",
        }
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        changed.append(name)
    print(json.dumps({"status": "OK", "marked": changed}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
