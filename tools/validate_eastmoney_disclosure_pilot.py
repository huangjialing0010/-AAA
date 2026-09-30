#!/usr/bin/env python
"""独立校验最新东方财富公告日期候选试验快照。"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = PROJECT_ROOT / "reports" / "eastmoney_disclosure_pilot_quality.json"
A_SHARE_SECURITY_TYPE = "058001001"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_snapshot() -> Path:
    candidates = []
    for path in (PROJECT_ROOT / "data" / "imports").glob("eastmoney_disclosure_pilot_*"):
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("status") == "SEALED":
            candidates.append(path)
    if not candidates:
        raise RuntimeError("没有已封存的东方财富公告日期试验快照")
    return sorted(candidates)[-1]


def main() -> int:
    snapshot = latest_snapshot()
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    total_rows = 0
    periods: dict[str, int] = {}

    for entry in manifest.get("entries", []):
        path = snapshot / entry["path"]
        if not path.is_file():
            errors.append(f"文件不存在: {entry['path']}")
            continue
        if path.stat().st_size != entry["bytes"] or sha256_file(path) != entry["sha256"]:
            errors.append(f"文件大小或SHA256不一致: {entry['path']}")
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        period = entry["report_period"]
        codes = [row["SECURITY_CODE"].zfill(6) for row in rows]
        if len(codes) != len(set(codes)):
            errors.append(f"{period} 股票代码重复")
        for row in rows:
            if row.get("SECURITY_TYPE_CODE") != A_SHARE_SECURITY_TYPE:
                errors.append(f"{period} 包含非A股证券类型")
                break
            report_date = str(row.get("REPORTDATE", ""))[:10]
            update_date = str(row.get("UPDATE_DATE", ""))[:10]
            if report_date != period or not update_date or date.fromisoformat(update_date) <= date.fromisoformat(period):
                errors.append(f"{period} 报告期或候选公告日期异常: {row.get('SECURITY_CODE')}")
                break
        if len(rows) != entry["rows"]:
            errors.append(f"{period} 行数与清单不一致")
        periods[period] = len(rows)
        total_rows += len(rows)

    if total_rows != manifest.get("rows"):
        errors.append("总行数与清单不一致")
    crosscheck = manifest.get("baostock_sample_crosscheck", [])
    exact_matches = sum(bool(item.get("exact_match")) for item in crosscheck)
    if not crosscheck or exact_matches != len(crosscheck):
        errors.append("BaoStock公告日小样本未全部精确匹配")

    report = {
        "status": "FAIL" if errors else "PASS_AS_CANDIDATE_ONLY",
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "snapshot": snapshot.name,
        "period_rows": periods,
        "total_rows": total_rows,
        "baostock_crosscheck_samples": len(crosscheck),
        "baostock_exact_matches": exact_matches,
        "errors": errors,
        "known_limits": manifest.get("limits", []),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
