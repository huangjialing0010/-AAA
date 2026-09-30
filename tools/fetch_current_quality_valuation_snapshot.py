#!/usr/bin/env python
"""为当前质量候选抓取双口径行情与历史PE/PB，不生成交易信号。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGES = PROJECT_ROOT / ".python-packages"
sys.path.insert(0, str(LOCAL_PACKAGES))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.selection.current_quality import current_quality_profile  # noqa: E402
from ashare_lab.sources.baostock_source import (  # noqa: E402
    BaoStockSource,
    QueryResult,
    RetryingBaoStockSource,
)


CANONICAL_ROOT = PROJECT_ROOT / "data" / "canonical"
EASTMONEY_ROOT = PROJECT_ROOT / "data" / "imports" / "eastmoney_disclosure_pilot_20260903T061458Z"
PLAN_PATH = PROJECT_ROOT / "docs" / "CURRENT_QUALITY_FORWARD_PILOT_V1.md"
AS_OF = "2026-09-02"
START_DATE = "2023-01-01"
FINANCIAL_SECTORS = {"银行", "非银金融"}
EXPECTED_CANDIDATE_COUNT = 139
SNAPSHOT_PREFIX = "baostock_current_quality_valuation_"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_query_result(path: Path) -> QueryResult:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise RuntimeError(f"已有文件缺少表头: {path}")
        return QueryResult(fields=list(reader.fieldnames), rows=list(reader))


def verify_file(path: Path, entry: dict[str, Any]) -> None:
    if not path.is_file() or path.stat().st_size != entry["bytes"] or sha256_file(path) != entry["sha256"]:
        raise RuntimeError(f"输入文件与清单不一致: {path}")


def canonical_context() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    path = CANONICAL_ROOT / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("quality_report", {}).get("status") != "PASS_WITH_KNOWN_LIMITS":
        raise RuntimeError("财务标准层未通过既有质量闸门")
    entries = {entry["path"]: entry for entry in manifest["output"]["entries"]}
    return manifest, entries


def build_query_scope() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    canonical_manifest, entries = canonical_context()
    for relative in ("reference/current_universe.csv", "reference/current_industry.csv"):
        verify_file(CANONICAL_ROOT / relative, entries[relative])
    universe = read_csv(CANONICAL_ROOT / "reference" / "current_universe.csv")
    industries = {row["stock_code"]: row for row in read_csv(CANONICAL_ROOT / "reference" / "current_industry.csv")}

    east_manifest_path = EASTMONEY_ROOT / "manifest.json"
    east_manifest = json.loads(east_manifest_path.read_text(encoding="utf-8"))
    if east_manifest.get("status") != "SEALED":
        raise RuntimeError("东方财富半年报候选快照未封存")
    h1_relative = "quarterly_reports/2026-06-30.csv"
    h1_entry = next(entry for entry in east_manifest["entries"] if entry["path"] == h1_relative)
    h1_path = EASTMONEY_ROOT / h1_relative
    verify_file(h1_path, h1_entry)
    h1_rows = {row["SECURITY_CODE"].zfill(6): row for row in read_csv(h1_path)}

    scope: list[dict[str, Any]] = []
    verified_financial_files = 0
    for security in universe:
        code = security["stock_code"]
        industry = industries.get(code)
        if industry is None:
            raise RuntimeError(f"当前成分缺少行业: {code}")
        if industry["level1_name"] in FINANCIAL_SECTORS:
            continue
        relative = f"financial_reports/{code}.csv"
        entry = entries.get(relative)
        if entry is None:
            raise RuntimeError(f"当前成分缺少财务清单: {code}")
        financial_path = CANONICAL_ROOT / relative
        verify_file(financial_path, entry)
        verified_financial_files += 1
        h1 = h1_rows.get(code)
        profile = current_quality_profile(
            read_csv(financial_path),
            as_of=AS_OF,
            h1_parent_netprofit=h1.get("PARENT_NETPROFIT") if h1 else None,
        )
        if not profile["eligible"]:
            continue
        scope.append({
            "stock_code": code,
            "name": security["name"],
            "level1_industry": industry["level1_name"],
            "roe_3y_median": profile["roe_median"],
            "cash_profit_per_share_proxy": profile["cash_profit_per_share_proxy"],
            "cash_profit_proxy_ge_0_8": str(profile["cash_profit_proxy_ge_0_8"]).lower(),
            "h1_parent_netprofit_candidate": profile["h1_parent_netprofit"],
            "eligibility_status": "QUALITY_PASS_H1_CANDIDATE_REVIEW",
        })
    if len(scope) != EXPECTED_CANDIDATE_COUNT:
        raise RuntimeError(f"质量候选数量变化，冻结预期{EXPECTED_CANDIDATE_COUNT}，实际{len(scope)}")
    scope.sort(key=lambda row: row["stock_code"])
    lineage = {
        "canonical_manifest_sha256": sha256_file(CANONICAL_ROOT / "manifest.json"),
        "canonical_quality_status": canonical_manifest["quality_report"]["status"],
        "verified_financial_files": verified_financial_files,
        "eastmoney_snapshot": EASTMONEY_ROOT.name,
        "eastmoney_manifest_sha256": sha256_file(east_manifest_path),
        "eastmoney_h1_file_sha256": h1_entry["sha256"],
        "as_of": AS_OF,
    }
    return scope, lineage


def validate_result(result: QueryResult, *, code: str, start: str, end: str, adjustflag: str) -> None:
    required = {
        "date", "code", "open", "high", "low", "close", "preclose", "volume", "amount",
        "adjustflag", "turn", "tradestatus", "pctChg", "peTTM", "pbMRQ", "isST",
    }
    if required - set(result.fields):
        raise RuntimeError(f"{code} 估值行情字段缺失: {sorted(required - set(result.fields))}")
    if not result.rows:
        raise RuntimeError(f"{code} 估值行情为空")
    dates: list[str] = []
    for row in result.rows:
        if row["code"].split(".")[-1] != code or row["adjustflag"] != adjustflag:
            raise RuntimeError(f"{code} 响应身份或复权标记错误")
        if not start <= row["date"] <= end:
            raise RuntimeError(f"{code} 响应日期越界: {row['date']}")
        for field in ("peTTM", "pbMRQ"):
            if row[field] != "":
                try:
                    float(row[field])
                except ValueError as exc:
                    raise RuntimeError(f"{code} {row['date']} {field}不可解析") from exc
        dates.append(row["date"])
    if dates != sorted(set(dates)):
        raise RuntimeError(f"{code} 日期重复或未升序")


def write_rows(path: Path, result: QueryResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=result.fields)
        writer.writeheader()
        writer.writerows(result.rows)


def validate_pair(code: str, pair: dict[str, QueryResult]) -> None:
    raw = {row["date"]: row for row in pair["daily_unadjusted_valuation"].rows}
    qfq = {row["date"]: row for row in pair["daily_qfq_valuation"].rows}
    if set(raw) != set(qfq):
        raise RuntimeError(f"{code} 双口径日期不一致")
    for day in raw:
        for field in ("volume", "amount", "tradestatus", "peTTM", "pbMRQ", "isST"):
            if raw[day][field] != qfq[day][field]:
                raise RuntimeError(f"{code} {day} 双口径{field}不一致")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", type=Path, help="续传一个未封存的本工具快照目录")
    return parser.parse_args()


def frozen_end_date(output: Path) -> str:
    match = re.fullmatch(rf"{SNAPSHOT_PREFIX}(\d{{8}}T\d{{6}}Z)", output.name)
    if match is None:
        raise RuntimeError(f"快照目录名不符合冻结日期规则: {output.name}")
    created_utc = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return created_utc.astimezone().date().isoformat()


def resolve_output(resume: Path | None) -> tuple[Path, bool]:
    imports_root = (PROJECT_ROOT / "data" / "imports").resolve()
    if resume is None:
        now = datetime.now(timezone.utc)
        output = imports_root / now.strftime(f"{SNAPSHOT_PREFIX}%Y%m%dT%H%M%SZ")
        output.mkdir(parents=True, exist_ok=False)
        return output, False
    output = (resume if resume.is_absolute() else PROJECT_ROOT / resume).resolve()
    if output.parent != imports_root or not output.name.startswith(SNAPSHOT_PREFIX):
        raise RuntimeError("--resume 只能指向 data/imports 下的本工具快照目录")
    if not output.is_dir():
        raise RuntimeError(f"续传目录不存在: {output}")
    if (output / "manifest.json").exists():
        raise RuntimeError("已封存快照禁止续传")
    frozen_end_date(output)
    return output, True


def validate_scope_file(scope_path: Path, scope: list[dict[str, Any]]) -> None:
    with scope_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    expected_fields = list(scope[0])
    expected_rows = [{key: str(value) for key, value in row.items()} for row in scope]
    if fields != expected_fields or rows != expected_rows:
        raise RuntimeError("续传目录的 query_scope.csv 与当前重算范围不一致")


def reject_unexpected_data_files(output: Path, scope: list[dict[str, Any]]) -> None:
    expected_codes = {row["stock_code"] for row in scope}
    for category in ("daily_unadjusted_valuation", "daily_qfq_valuation"):
        directory = output / category
        if not directory.exists():
            continue
        unexpected = sorted(
            path.name for path in directory.iterdir()
            if not path.is_file() or path.suffix.lower() != ".csv" or path.stem not in expected_codes
        )
        if unexpected:
            raise RuntimeError(f"续传目录含意外文件 {category}: {unexpected[:5]}")


def entry(root: Path, path: Path, *, code: str, category: str, rows: int, query: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "stock_code": code,
        "category": category,
        "query": query,
        "rows": rows,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def main() -> int:
    args = parse_args()
    scope, lineage = build_query_scope()
    output, resumed = resolve_output(args.resume)
    end_date = frozen_end_date(output)
    scope_path = output / "query_scope.csv"
    if resumed:
        if not scope_path.is_file():
            raise RuntimeError("续传目录缺少 query_scope.csv")
        validate_scope_file(scope_path, scope)
        reject_unexpected_data_files(output, scope)
    else:
        with scope_path.open("x", encoding="utf-8", newline="") as handle:
            fields = list(scope[0])
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(scope)

    entries: list[dict[str, Any]] = []
    reused_files = 0
    fetched_files = 0
    with RetryingBaoStockSource(
        source_factory=lambda: BaoStockSource(socket_timeout_seconds=45.0)
    ) as source:
        for index, candidate in enumerate(scope, start=1):
            code = candidate["stock_code"]
            pair: dict[str, QueryResult] = {}
            for adjustflag, category in (("3", "daily_unadjusted_valuation"), ("2", "daily_qfq_valuation")):
                path = output / category / f"{code}.csv"
                if path.exists():
                    result = read_query_result(path)
                    reused_files += 1
                else:
                    result = source.daily_valuation(code, START_DATE, end_date, adjustflag)
                    write_rows(path, result)
                    fetched_files += 1
                validate_result(result, code=code, start=START_DATE, end=end_date, adjustflag=adjustflag)
                entries.append(entry(output, path, code=code, category=category, rows=len(result.rows), query={
                    "api": "query_history_k_data_plus",
                    "stock_code": code,
                    "start_date": START_DATE,
                    "end_date": end_date,
                    "frequency": "d",
                    "adjustflag": adjustflag,
                    "fields": result.fields,
                }))
                pair[category] = result
            validate_pair(code, pair)
            if index % 10 == 0 or index == len(scope):
                print(
                    f"progress {index}/{len(scope)} reused_files={reused_files} fetched_files={fetched_files}",
                    flush=True,
                )

    scope_entry = {
        "path": "query_scope.csv",
        "category": "query_scope_metadata",
        "rows": len(scope),
        "bytes": scope_path.stat().st_size,
        "sha256": sha256_file(scope_path),
    }
    manifest = {
        "schema_version": 1,
        "status": "SEALED",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "BaoStock",
        "purpose": "current_quality_candidates_dual_price_and_valuation_history",
        "research_use_only": True,
        "resumed": resumed,
        "reused_files": reused_files,
        "fetched_files": fetched_files,
        "query_range": {"start": START_DATE, "end": end_date},
        "candidate_count": len(scope),
        "files": len(entries) + 1,
        "rows": sum(item["rows"] for item in entries) + len(scope),
        "bytes": sum(item["bytes"] for item in entries) + scope_path.stat().st_size,
        "lineage": lineage,
        "plan": {
            "path": PLAN_PATH.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": sha256_file(PLAN_PATH),
        },
        "implementation": {
            "fetcher_sha256": sha256_file(Path(__file__).resolve()),
            "source_adapter_sha256": sha256_file(PROJECT_ROOT / "src" / "ashare_lab" / "sources" / "baostock_source.py"),
            "quality_module_sha256": sha256_file(PROJECT_ROOT / "src" / "ashare_lab" / "selection" / "current_quality.py"),
        },
        "entries": [scope_entry, *entries],
        "limits": [
            "只覆盖当前沪深300中通过质量初筛的139只，不代表全A",
            "东方财富2026半年报字段仍是候选证据，入选名单需二次核对",
            "每股现金利润代理不是正式经营现金流/净利润比",
            "BaoStock客户端许可不等于底层数据可公开再分发，快照限本地研究",
            "前复权序列可能随未来公司行动回溯变化",
        ],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "SEALED",
        "snapshot": str(output),
        "candidates": len(scope),
        "data_files": len(entries),
        "reused_files": reused_files,
        "fetched_files": fetched_files,
        "rows": sum(item["rows"] for item in entries),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
