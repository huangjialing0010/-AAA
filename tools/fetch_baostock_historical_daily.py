#!/usr/bin/env python
"""为历史沪深300成分抓取可恢复、分段的 BaoStock 双口径日线。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / ".python-packages"))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.sources.baostock_source import QueryResult, RetryingBaoStockSource  # noqa: E402


FIXED_WINDOWS = (
    ("2010-01-01", "2015-12-31"),
    ("2016-01-01", "2021-12-31"),
)
ADJUSTMENTS = (("3", "daily_unadjusted"), ("2", "daily_qfq"))
SECURITY_LINEAGES = (
    {
        "effective_code": "302132",
        "previous_code": "300114",
        "previous_code_end_date": "2025-02-14",
        "effective_code_start_date": "2025-02-17",
        "applies_to_adjustflags": ["2"],
        "evidence": "docs/DECISION_20260904_302132_CODE_LINEAGE.md",
    },
)
REQUIRED_FIELDS = {
    "date", "code", "open", "high", "low", "close", "preclose", "volume", "amount",
    "adjustflag", "turn", "tradestatus", "pctChg", "isST",
}


def fetch_status(status: str, **details) -> None:
    path = PROJECT_ROOT/"reports/historical_fetch_status.json"
    payload = {"status": status, "pid": os.getpid(), "updated_at": datetime.now(timezone.utc).isoformat(), **details}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_membership_snapshot() -> tuple[Path, dict[str, Any]]:
    candidates = []
    for path in (PROJECT_ROOT / "data" / "imports").glob("baostock_hs300_history_*"):
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("status") in {"SEALED", "SEALED_WITH_GAPS"}:
            candidates.append((path, payload))
    if not candidates:
        raise RuntimeError("没有可追溯的沪深300历史成分快照")
    return sorted(candidates, key=lambda item: item[0].name)[-1]


def load_history_universe() -> tuple[list[str], dict[str, Any]]:
    snapshot, manifest = latest_membership_snapshot()
    membership_path = snapshot / "hs300_membership_weekly.csv"
    with membership_path.open("r", encoding="utf-8", newline="") as handle:
        codes = sorted({row["code"].split(".")[-1] for row in csv.DictReader(handle)})
    if len(codes) != 940:
        raise RuntimeError(f"历史成分唯一代码应为940只，实际{len(codes)}只")
    evidence = {
        "snapshot": snapshot.name,
        "membership_file": membership_path.name,
        "membership_sha256": sha256_file(membership_path),
        "membership_status": manifest["status"],
        "membership_code_count": len(codes),
    }
    return codes, evidence


def verified_end_date() -> str:
    report_path = PROJECT_ROOT / "reports" / "baostock_daily_snapshot_quality.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if payload.get("status") != "PASS_WITH_KNOWN_LIMITS":
        raise RuntimeError("近期日线质量报告未通过，不能确定全量抓取截止日")
    counts = payload.get("latest_date_counts", {})
    if len(counts) != 1:
        raise RuntimeError(f"近期日线截止日不唯一: {counts}")
    value = next(iter(counts))
    date.fromisoformat(value)
    return value


def build_windows(end_date: str) -> tuple[tuple[str, str], ...]:
    date.fromisoformat(end_date)
    if end_date < "2022-01-01":
        raise RuntimeError("截止日早于第三分段起点")
    return FIXED_WINDOWS + (("2022-01-01", end_date),)


def lineage_periods(
    stock_code: str,
    adjustflag: str,
    start_date: str,
    end_date: str,
) -> list[tuple[str, str, str, str]]:
    """返回(查询代码, 起日, 止日, 沿革阶段)，不跨证券代码生效边界。"""
    for lineage in SECURITY_LINEAGES:
        if stock_code != lineage["effective_code"] or adjustflag not in lineage["applies_to_adjustflags"]:
            continue
        periods: list[tuple[str, str, str, str]] = []
        previous_end = lineage["previous_code_end_date"]
        effective_start = lineage["effective_code_start_date"]
        if start_date <= previous_end:
            periods.append((
                lineage["previous_code"],
                start_date,
                min(end_date, previous_end),
                "PREVIOUS_CODE",
            ))
        if end_date >= effective_start:
            periods.append((
                stock_code,
                max(start_date, effective_start),
                end_date,
                "EFFECTIVE_CODE",
            ))
        return [period for period in periods if period[1] <= period[2]]
    return [(stock_code, start_date, end_date, "UNCHANGED")]


def build_tasks(codes: list[str], windows: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    tasks = []
    for adjustflag, category in ADJUSTMENTS:
        for stock_code in codes:
            for segment_number, (start_date, end_date) in enumerate(windows, start=1):
                periods = lineage_periods(stock_code, adjustflag, start_date, end_date)
                for subsegment_number, (query_code, period_start, period_end, phase) in enumerate(periods, start=1):
                    segment = f"{period_start}_{period_end}"
                    tasks.append({
                        "stock_code": stock_code,
                        "query_stock_code": query_code,
                        "response_stock_code": query_code,
                        "lineage_phase": phase,
                        "adjustflag": adjustflag,
                        "category": category,
                        "segment_number": str(segment_number),
                        "subsegment_number": str(subsegment_number),
                        "start_date": period_start,
                        "end_date": period_end,
                        "relative_path": f"{category}/{stock_code}/{segment}.csv",
                        "metadata_path": f"metadata/{category}/{stock_code}_{segment}.json",
                    })
    return tasks


def validate_result(result: QueryResult, task: dict[str, str]) -> dict[str, Any]:
    missing = REQUIRED_FIELDS - set(result.fields)
    if missing:
        raise RuntimeError(f"{task['relative_path']} 缺少字段: {sorted(missing)}")
    dates = [row["date"] for row in result.rows]
    if len(dates) != len(set(dates)):
        raise RuntimeError(f"{task['relative_path']} 日期重复")
    if dates != sorted(dates):
        raise RuntimeError(f"{task['relative_path']} 日期未升序")
    if len(dates) >= 2000:
        raise RuntimeError(f"{task['relative_path']} 返回{len(dates)}行，分段不足以规避分页风险")
    for row in result.rows:
        value = row["date"]
        date.fromisoformat(value)
        if not task["start_date"] <= value <= task["end_date"]:
            raise RuntimeError(f"{task['relative_path']} 日期越界: {value}")
        known_missing = (task["stock_code"] == "688223" and task["adjustflag"] == "2"
                         and row["code"] == "sh.688223" and value == "2022-01-26"
                         and row["adjustflag"] == "3")
        if row["adjustflag"] != task["adjustflag"] and not known_missing:
            raise RuntimeError(f"{task['relative_path']} 复权标记不一致")
        response_stock_code = task.get("response_stock_code", task["stock_code"])
        if row["code"].split(".")[-1] != response_stock_code:
            raise RuntimeError(f"{task['relative_path']} 股票代码不一致: {row['code']}")
    return {
        **({"missing_qfq_dates": ["2022-01-26"],
            "qfq_exception_decision": "docs/DECISION_20260908_688223_MISSING_QFQ.md"}
           if task["stock_code"] == "688223" and task["adjustflag"] == "2"
           and any(r["date"] == "2022-01-26" and r["adjustflag"] == "3" for r in result.rows) else {}),
        "rows": len(result.rows),
        "min_date": min(dates) if dates else None,
        "max_date": max(dates) if dates else None,
    }


def read_result(path: Path) -> QueryResult:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return QueryResult(fields=list(reader.fieldnames or []), rows=list(reader))


def write_result(path: Path, result: QueryResult) -> None:
    if path.exists():
        raise RuntimeError(f"目标文件已存在，禁止覆盖: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=result.fields)
        writer.writeheader()
        writer.writerows(result.rows)


def entry_from_file(output_root: Path, task: dict[str, str], *, recovered: bool) -> dict[str, Any]:
    path = output_root / task["relative_path"]
    result = read_result(path)
    summary = validate_result(result, task)
    return {
        **task,
        **summary,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "recovered_after_interruption": recovered,
    }


def load_or_recover_entry(output_root: Path, task: dict[str, str]) -> dict[str, Any] | None:
    data_path = output_root / task["relative_path"]
    metadata_path = output_root / task["metadata_path"]
    if not data_path.exists() and not metadata_path.exists():
        return None
    if metadata_path.exists() and not data_path.exists():
        raise RuntimeError(f"元数据存在但数据文件缺失: {metadata_path}")
    if metadata_path.exists():
        entry = json.loads(metadata_path.read_text(encoding="utf-8"))
        for key in ("stock_code", "adjustflag", "category", "start_date", "end_date", "relative_path"):
            if entry.get(key) != task[key]:
                raise RuntimeError(f"续跑元数据与任务不一致: {metadata_path} / {key}")
        for key in ("query_stock_code", "response_stock_code"):
            if entry.get(key, entry["stock_code"]) != task[key]:
                raise RuntimeError(f"续跑元数据与任务不一致: {metadata_path} / {key}")
        if data_path.stat().st_size != entry["bytes"] or sha256_file(data_path) != entry["sha256"]:
            raise RuntimeError(f"续跑文件哈希或大小不一致: {data_path}")
        summary = validate_result(read_result(data_path), task)
        if entry.get("missing_qfq_dates", []) != summary.get("missing_qfq_dates", []):
            raise RuntimeError("续跑异常日期元数据不一致")
        return entry
    entry = entry_from_file(output_root, task, recovered=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return entry


def write_entry_metadata(output_root: Path, entry: dict[str, Any]) -> None:
    path = output_root / entry["metadata_path"]
    if path.exists():
        raise RuntimeError(f"任务元数据已存在，禁止覆盖: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def create_or_resume(args: argparse.Namespace) -> tuple[Path, dict[str, Any], list[str], tuple[tuple[str, str], ...]]:
    imports_root = (PROJECT_ROOT / "data" / "imports").resolve()
    universe, evidence = load_history_universe()
    end_date = verified_end_date()
    if args.limit_codes:
        universe = universe[: args.limit_codes]
    windows = build_windows(end_date)
    tasks = build_tasks(universe, windows)
    evidence_candidates = []
    for candidate in imports_root.glob("lineage_302132_*/manifest.json"):
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        if payload.get("status") == "SEALED" and payload.get("independent_validation", {}).get("status") == "PASS":
            evidence_candidates.append(candidate)
    if not evidence_candidates:
        raise RuntimeError("缺少独立封存的302132沿革证据")
    evidence_manifest = sorted(evidence_candidates)[-1]
    expected_state = {
        "schema_version": 2,
        "status": "RUNNING",
        "source": "BaoStock",
        "purpose": "historical_hs300_segmented_daily_pilot" if args.limit_codes else "historical_hs300_segmented_daily_full",
        "universe_evidence": evidence,
        "selected_code_count": len(universe),
        "selected_codes_sha256": hashlib.sha256("\n".join(universe).encode("ascii")).hexdigest(),
        "windows": [{"start_date": start, "end_date": end} for start, end in windows],
        "adjustments": [{"adjustflag": flag, "category": category} for flag, category in ADJUSTMENTS],
        "security_lineages": list(SECURITY_LINEAGES),
        "lineage_evidence": {"snapshot": evidence_manifest.parent.name, "manifest_sha256": sha256_file(evidence_manifest)},
        "expected_data_files": len(tasks),
        "task_blueprint_sha256": hashlib.sha256(
            json.dumps(tasks, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }
    if args.resume:
        output_root = args.resume.resolve()
        if output_root.parent != imports_root or not output_root.name.startswith("baostock_historical_daily_"):
            raise RuntimeError(f"续跑目录不在允许范围: {output_root}")
        if (output_root / "manifest.json").exists():
            raise RuntimeError("快照已经封存，禁止续跑")
        state_path = output_root / "run_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        needs_write = False
        if state.get("schema_version") == 2 and state.get("lineage_evidence") != expected_state["lineage_evidence"]:
            prior = state.get("lineage_evidence")
            if prior is None or sha256_file(imports_root/prior["snapshot"]/"manifest.json") != prior["manifest_sha256"]:
                raise RuntimeError("既有沿革证据引用不完整")
            state.setdefault("lineage_evidence_revisions", []).append(prior)
            state["lineage_evidence"] = expected_state["lineage_evidence"]
            needs_write = True
        if state.get("schema_version") == 1:
            for key, value in expected_state.items():
                if key not in {"schema_version", "security_lineages", "lineage_evidence", "expected_data_files", "task_blueprint_sha256"} and state.get(key) != value:
                    raise RuntimeError(f"迁移前任务不一致，未修改状态: {key}")
            lineage_root = output_root / "daily_qfq" / "302132"
            if lineage_root.exists() and any(lineage_root.rglob("*")):
                raise RuntimeError("旧版任务状态下已存在302132前复权文件，禁止自动迁移")
            legacy_expected = len(universe) * len(windows) * len(ADJUSTMENTS)
            if state.get("expected_data_files") != legacy_expected:
                raise RuntimeError("旧版任务状态的预期文件数不一致，禁止自动迁移")
            state.update({
                "schema_version": expected_state["schema_version"],
                "security_lineages": expected_state["security_lineages"],
                "lineage_evidence": expected_state["lineage_evidence"],
                "expected_data_files": expected_state["expected_data_files"],
                "task_blueprint_sha256": expected_state["task_blueprint_sha256"],
                "state_migration": {
                    "from_schema_version": 1,
                    "reason": "302132证券代码沿革导致BaoStock前复权标记异常",
                    "approved_decision": "docs/DECISION_20260904_302132_CODE_LINEAGE.md",
                    "migrated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                },
            })
            needs_write = True
        for key, value in expected_state.items():
            if key != "status" and state.get(key) != value:
                raise RuntimeError(f"续跑状态与当前任务不一致: {key}")
        if state.get("status") != "RUNNING":
            raise RuntimeError(f"续跑状态不是RUNNING: {state.get('status')}")
        if needs_write:
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        return output_root, state, universe, windows

    started = datetime.now(timezone.utc)
    suffix = "pilot_" if args.limit_codes else ""
    run_id = started.strftime(f"baostock_historical_daily_{suffix}%Y%m%dT%H%M%SZ")
    output_root = imports_root / run_id
    if output_root.exists():
        raise RuntimeError(f"输出目录已存在，禁止覆盖: {output_root}")
    output_root.mkdir(parents=True)
    state = {
        **expected_state,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    (output_root / "run_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return output_root, state, universe, windows


def main() -> int:
    parser = argparse.ArgumentParser(description="分段抓取历史沪深300成分的BaoStock双口径日线")
    parser.add_argument("--resume", type=Path, help="续跑未封存的 baostock_historical_daily_* 目录")
    parser.add_argument("--limit-codes", type=int, help="只取排序后的前N只，用于端到端试验")
    parser.add_argument("--max-new-files", type=int, help="新增指定数量后安全暂停；不写封存清单")
    args = parser.parse_args()
    if args.limit_codes is not None and args.limit_codes < 1:
        raise RuntimeError("--limit-codes 必须大于0")

    started = datetime.now(timezone.utc)
    output_root, state, universe, windows = create_or_resume(args)
    tasks = build_tasks(universe, windows)
    entries: list[dict[str, Any]] = []
    fetch_status("RUNNING", snapshot=output_root.name, completed_files=0, expected_files=len(tasks))
    reused = 0
    downloaded = 0

    with RetryingBaoStockSource(
        max_attempts=4,
        backoff_seconds=(5, 15, 30),
        max_queries_per_session=25,
    ) as source:
        for index, task in enumerate(tasks, start=1):
            entry = load_or_recover_entry(output_root, task)
            if entry is not None:
                reused += 1
            else:
                result = source.daily(
                    task["query_stock_code"], task["start_date"], task["end_date"],
                    adjustflag=task["adjustflag"],
                )
                validate_result(result, task)
                write_result(output_root / task["relative_path"], result)
                entry = entry_from_file(output_root, task, recovered=False)
                write_entry_metadata(output_root, entry)
                downloaded += 1
            entries.append(entry)
            if index % 25 == 0 or index == len(tasks):
                print(f"progress {index}/{len(tasks)} reused={reused}", flush=True)
                fetch_status("RUNNING",snapshot=output_root.name,completed_files=index,expected_files=len(tasks),reused=reused)
            if args.max_new_files is not None and downloaded >= args.max_new_files:
                fetch_status("PAUSED",snapshot=output_root.name,completed_files=index,expected_files=len(tasks))
                print(f"PAUSED_WITHOUT_SEAL completed={index}/{len(tasks)} new={downloaded}", flush=True)
                return 0

    if len(entries) != state["expected_data_files"]:
        raise RuntimeError(f"完成任务数不一致: {len(entries)} != {state['expected_data_files']}")
    empty_entries = sum(entry["rows"] == 0 for entry in entries)
    manifest = {
        "schema_version": 2,
        "status": "SEALED_PENDING_INDEPENDENT_VALIDATION",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": "BaoStock",
        "purpose": state["purpose"],
        "universe_evidence": state["universe_evidence"],
        "code_count": len(universe),
        "windows": state["windows"],
        "adjustments": state["adjustments"],
        "security_lineages": state["security_lineages"],
        "lineage_evidence": state["lineage_evidence"],
        "task_blueprint_sha256": state["task_blueprint_sha256"],
        "data_files": len(entries),
        "metadata_files": len(entries),
        "missing_qfq_observations": [{"code": e["stock_code"], "date": d,
                                      "source_file": e["relative_path"]}
                                     for e in entries for d in e.get("missing_qfq_dates", [])],
        "qfq_exception_decision_sha256": sha256_file(PROJECT_ROOT/"docs/DECISION_20260908_688223_MISSING_QFQ.md"),
        "rows": sum(entry["rows"] for entry in entries),
        "empty_data_files": empty_entries,
        "bytes": sum(entry["bytes"] for entry in entries),
        "entries": sorted(entries, key=lambda entry: entry["relative_path"]),
        "limits": [
            "688223上市首日混合响应仅作原始证据，前复权价格缺失，不可直接用于收益计算",
            "940只来源于沪深300历史成分，不是全A股股票池",
            "前复权历史可能随未来公司行动回溯变化",
            "独立验证通过前不得进入标准层或回测",
            "302132在2025-02-17前的前复权响应使用其连续旧代码300114，原始响应代码不改写",
        ],
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    state["status"] = "FETCH_COMPLETE_PENDING_VALIDATION"
    state["completed_at"] = manifest["created_at"]
    (output_root / "run_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "snapshot": str(output_root),
        "status": manifest["status"],
        "code_count": manifest["code_count"],
        "data_files": manifest["data_files"],
        "rows": manifest["rows"],
        "empty_data_files": manifest["empty_data_files"],
        "bytes": manifest["bytes"],
        "elapsed_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
    }, ensure_ascii=False, indent=2), flush=True)
    fetch_status("DOWNLOAD_COMPLETE",snapshot=output_root.name,completed_files=len(entries),expected_files=len(tasks))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        path=PROJECT_ROOT/"reports/historical_fetch_status.json"
        prior=json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        fetch_status("FAILED",snapshot=prior.get("snapshot"),error=f"{type(exc).__name__}: {exc}")
        raise
