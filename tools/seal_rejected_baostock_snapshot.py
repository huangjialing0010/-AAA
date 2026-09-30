#!/usr/bin/env python
"""为已确认不可用的 BaoStock 残缺目录生成拒绝清单，保留证据但阻止续跑。"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = PROJECT_ROOT / "data" / "imports" / "baostock_daily_20260901T093225Z"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_csv(path: Path) -> dict:
    rows = 0
    min_date = None
    max_date = None
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            value = row.get("date", "")
            rows += 1
            min_date = value if min_date is None or value < min_date else min_date
            max_date = value if max_date is None or value > max_date else max_date
    return {"rows": rows, "min_date": min_date, "max_date": max_date}


def main() -> int:
    manifest_path = SNAPSHOT / "manifest.json"
    if manifest_path.exists():
        raise RuntimeError(f"清单已存在，禁止覆盖: {manifest_path}")
    entries = []
    for path in sorted(SNAPSHOT.rglob("*.csv")):
        summary = inspect_csv(path)
        entries.append({
            "path": path.relative_to(SNAPSHOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            **summary,
        })
    truncated = [item for item in entries if item["rows"] % 2000 == 0 and item["max_date"] < "2026-09-01"]
    manifest = {
        "schema_version": 1,
        "status": "REJECTED_INCOMPLETE",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": "BaoStock",
        "original_target_files": 600,
        "actual_files": len(entries),
        "bytes": sum(item["bytes"] for item in entries),
        "rows": sum(item["rows"] for item in entries),
        "rejection_reasons": [
            "下载在145/600时因BaoStock长连接超时和远端断开停止",
            "600196.csv恰好4000行且只到2015-02-16，证明分页链静默截断",
            "该目录未完成双口径、全股票和统一截止日要求",
        ],
        "suspected_truncated_files": [item["path"] for item in truncated],
        "runtime_use_allowed": False,
        "resume_allowed": False,
        "entries": entries,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "snapshot": str(SNAPSHOT),
        "status": manifest["status"],
        "actual_files": manifest["actual_files"],
        "suspected_truncated_files": manifest["suspected_truncated_files"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
