#!/usr/bin/env python
"""从封存的当前质量估值快照生成前向观察基线，不生成订单。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.selection.current_watchlist import evaluate_candidate, select_watchlist  # noqa: E402


PLAN_PATH = PROJECT_ROOT / "docs" / "CURRENT_QUALITY_FORWARD_PILOT_V1.md"
MODULE_PATH = PROJECT_ROOT / "src" / "ashare_lab" / "selection" / "current_watchlist.py"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def file_entry(root: Path, path: Path, rows: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if rows is not None:
        result["rows"] = rows
    return result


def load_snapshot(snapshot: Path) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, list[dict[str, str]]]]:
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SEALED" or manifest.get("candidate_count") != 139:
        raise RuntimeError("输入快照未封存或候选数量错误")
    for item in manifest.get("entries", []):
        path = snapshot / item["path"]
        if not path.is_file() or path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            raise RuntimeError(f"输入快照文件哈希或大小变化: {item['path']}")
    scope = read_csv(snapshot / "query_scope.csv")
    qfq_entries = {
        item["stock_code"]: item for item in manifest["entries"]
        if item.get("category") == "daily_qfq_valuation"
    }
    if len(scope) != 139 or set(qfq_entries) != {row["stock_code"] for row in scope}:
        raise RuntimeError("查询范围与前复权行情文件不一致")
    rows_by_code = {
        code: read_csv(snapshot / item["path"])
        for code, item in qfq_entries.items()
    }
    return manifest, scope, rows_by_code


def build(snapshot: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], str]:
    _, scope, rows_by_code = load_snapshot(snapshot)
    date_sets = [{row["date"] for row in rows} for rows in rows_by_code.values()]
    calendar = sorted(set().union(*date_sets))
    latest_dates = {max(dates) for dates in date_sets}
    if len(latest_dates) != 1:
        raise RuntimeError(f"候选最新日期不一致: {sorted(latest_dates)}")
    cutoff = latest_dates.pop()
    scope_by_code = {row["stock_code"]: row for row in scope}
    candidates: list[dict[str, Any]] = []
    for code in sorted(rows_by_code):
        profile = evaluate_candidate(rows_by_code[code], calendar, cutoff)
        candidates.append({**scope_by_code[code], **profile})
    watchlist = select_watchlist(candidates)
    selected_codes = {row["stock_code"] for row in watchlist}
    for row in candidates:
        row["watchlist_selected"] = str(row["stock_code"] in selected_codes).lower()
        if row["stock_code"] in selected_codes:
            row["selection_note"] = "FORWARD_WATCH_ONLY"
        elif row["status"] == "BASELINE_TRIGGER":
            row["selection_note"] = "NOT_SELECTED_CAP_OR_INDUSTRY_LIMIT"
        else:
            row["selection_note"] = row["status"]
    return candidates, watchlist, calendar, cutoff


def render_report(
    snapshot: Path,
    candidates: list[dict[str, Any]],
    watchlist: list[dict[str, Any]],
    calendar: list[str],
    cutoff: str,
) -> str:
    status_counts = Counter(row["status"] for row in candidates)
    mechanism_counts = Counter(row["mechanism"] for row in candidates if row["mechanism"])
    lines = [
        "# 当前质量候选前向观察基线",
        "",
        f"- 数据快照：`{snapshot.name}`",
        f"- 公共截止日：`{cutoff}`；共同市场日历共 `{len(calendar)}` 日",
        "- 状态：`GENERATED_FORWARD_WATCH_BASELINE_NOT_EXECUTABLE`",
        "- 证据类别：`CURRENT_SNAPSHOT_BASELINE_NOT_BACKTEST`",
        "- 本报告不含历史回填成交、虚拟订单或收益；观察名单仍须完成2026半年报关键值二次核对。",
        "",
        "## 结果",
        "",
        f"- 139只质量候选中：当前触发 `{status_counts.get('BASELINE_TRIGGER', 0)}` 只，无当前触发 `{status_counts.get('NO_CURRENT_TRIGGER', 0)}` 只，需复核 `{status_counts.get('REVIEW', 0)}` 只。",
        f"- 触发构成：L1 `{mechanism_counts.get('L1', 0)}` 只，R1 `{mechanism_counts.get('R1', 0)}` 只。",
        f"- 观察名单 `{len(watchlist)}` 只；这是截至截止日已存在的观察基线，不产生买入动作。",
        "",
        "## 观察名单",
        "",
    ]
    if watchlist:
        lines.extend([
            "| 排名 | 代码 | 名称 | 类型 | 行业 | 三年ROE中位数 | 现金利润代理 | PE历史分位 | 252日高点回撤 | 120日突破幅度 |",
            "|---:|---|---|---|---|---:|---:|---:|---:|---:|",
        ])
        for row in watchlist:
            lines.append(
                f"| {row['watch_rank']} | {row['stock_code']} | {row['name']} | {row['mechanism']} | "
                f"{row['level1_industry']} | {float(row['roe_3y_median']):.2%} | "
                f"{float(row['cash_profit_per_share_proxy']):.2f} | {float(row['pe_percentile_rank']):.2%} | "
                f"{float(row['drawdown_from_prior_252_high']):.2%} | "
                f"{float(row['breakout_above_prior_120_high']):.2%} |"
            )
    else:
        lines.append("当前没有满足冻结规则且可占用席位的股票；不为凑数量放宽阈值。")
    lines.extend([
        "",
        "## 风险与下一步",
        "",
        "- 当前沪深300成分与行业是当前快照，不能把本名单解释成历史可得组合。",
        "- 数据最新到2026-09-24，之后价格变化尚未反映；名单不是实时买入建议。",
        "- 入选股票的半年报关键值仍属候选证据；二次核对完成前，不生成可执行虚拟计划。",
        "- 完成二次核对并冻结名单后，只记录未来周末首次新触发；基线已有触发不补记交易。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    snapshot = args.snapshot.resolve()
    input_manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    candidates, watchlist, calendar, cutoff = build(snapshot)

    now = datetime.now(timezone.utc)
    output = PROJECT_ROOT / "reports" / now.strftime("current_quality_watchlist_%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=False)
    candidate_path = output / "candidates.csv"
    watchlist_path = output / "watchlist.csv"
    report_path = output / "README.md"
    fields = [
        "stock_code", "name", "level1_industry", "roe_3y_median", "cash_profit_per_share_proxy",
        "cash_profit_proxy_ge_0_8", "h1_parent_netprofit_candidate", "eligibility_status",
        "cutoff_date", "valuation_window_start_exclusive", "status", "mechanism", "review_reasons",
        "positive_pe_observations", "positive_pb_observations", "current_pe_ttm", "current_pb_mrq",
        "pe_p20", "pe_p80", "pe_percentile_rank", "pb_median", "current_close_qfq",
        "drawdown_from_prior_252_high", "breakout_above_prior_120_high", "ma60_change_vs_20_days_ago",
        "watchlist_selected", "selection_note", "watch_status",
    ]
    write_csv(candidate_path, candidates, fields)
    write_csv(watchlist_path, watchlist, ["watch_rank", *fields])
    report_path.write_text(
        render_report(snapshot, candidates, watchlist, calendar, cutoff), encoding="utf-8"
    )

    status_counts = Counter(row["status"] for row in candidates)
    manifest = {
        "schema_version": 1,
        "status": "GENERATED_FORWARD_WATCH_BASELINE_NOT_EXECUTABLE",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "evidence_class": "CURRENT_SNAPSHOT_BASELINE_NOT_BACKTEST",
        "cutoff_date": cutoff,
        "calendar_days": len(calendar),
        "candidate_count": len(candidates),
        "baseline_trigger_count": status_counts.get("BASELINE_TRIGGER", 0),
        "review_count": status_counts.get("REVIEW", 0),
        "watchlist_count": len(watchlist),
        "orders_created": 0,
        "trades_created": 0,
        "source": {
            "snapshot": snapshot.name,
            "manifest_sha256": sha256_file(snapshot / "manifest.json"),
            "status": input_manifest["status"],
        },
        "plan": {"path": PLAN_PATH.relative_to(PROJECT_ROOT).as_posix(), "sha256": sha256_file(PLAN_PATH)},
        "implementation": {
            "runner_sha256": sha256_file(Path(__file__).resolve()),
            "module_sha256": sha256_file(MODULE_PATH),
        },
        "entries": [
            file_entry(output, candidate_path, len(candidates)),
            file_entry(output, watchlist_path, len(watchlist)),
            file_entry(output, report_path),
        ],
        "admission_blockers": [
            "2026半年报关键值尚未完成第二来源或公告原文核对",
            "当前触发仅是冻结日前基线，不允许补记交易",
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": manifest["status"],
        "output": str(output),
        "cutoff_date": cutoff,
        "candidate_count": len(candidates),
        "baseline_trigger_count": manifest["baseline_trigger_count"],
        "review_count": manifest["review_count"],
        "watchlist_count": len(watchlist),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
