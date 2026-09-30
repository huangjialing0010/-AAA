#!/usr/bin/env python
"""运行冻结的左右侧价格形态诊断，不生成组合或真实账户。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.selection.price_mechanism import (  # noqa: E402
    HORIZONS,
    cooldown_allows,
    forward_observation,
    mechanism_flags,
    summarize_observations,
)


CANONICAL_ROOT = PROJECT_ROOT / "data" / "canonical" / "stock_selection_v1"
REPORTS_ROOT = PROJECT_ROOT / "reports"
PLAN_PATH = PROJECT_ROOT / "docs" / "PRICE_MECHANISM_DIAGNOSTIC_PLAN_V1.md"
EVIDENCE_CLASS = "HISTORICAL_DIAGNOSTIC_WITH_PRIOR_EXPOSURE"
ACCOUNTING_SCOPE = "PRICE_MECHANISM_EVENT_STUDY"


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


def file_entry(root: Path, path: Path, *, rows: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if rows is not None:
        result["rows"] = rows
    return result


def load_and_validate_inputs() -> tuple[dict[str, Any], list[str], dict[str, list[dict[str, str]]]]:
    manifest_path = CANONICAL_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SEALED" or manifest.get("independent_validation", {}).get("status") != "PASS":
        raise RuntimeError("stock_selection_v1未通过封存及独立验证")
    if manifest.get("code_count") != 940:
        raise RuntimeError("标准层代码范围与冻结设计不一致")

    calendar_entry = manifest["calendar_entry"]
    membership_entry = manifest["membership_entry"]
    for entry in (calendar_entry, membership_entry):
        path = CANONICAL_ROOT / entry["path"]
        if path.stat().st_size != entry["bytes"] or sha256_file(path) != entry["sha256"]:
            raise RuntimeError(f"输入文件哈希或大小已变化: {entry['path']}")
    calendar = [row["trade_date"] for row in read_csv(CANONICAL_ROOT / calendar_entry["path"])]
    if calendar != sorted(set(calendar)):
        raise RuntimeError("共同交易日历重复或未升序")

    membership_rows = read_csv(CANONICAL_ROOT / membership_entry["path"])
    by_code: dict[str, list[dict[str, str]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    calendar_set = set(calendar)
    for row in membership_rows:
        key = (row["observed_date"], row["code"])
        if key in seen:
            raise RuntimeError(f"历史成分重复: {key}")
        if row["observed_date"] not in calendar_set:
            raise RuntimeError(f"历史成分观察日不在共同日历: {row['observed_date']}")
        seen.add(key)
        by_code[row["code"]].append(row)
    for rows in by_code.values():
        rows.sort(key=lambda row: row["observed_date"])
    return manifest, calendar, by_code


def benchmark_stats(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    return statistics.fmean(values), statistics.median(values)


def run_diagnostic(manifest: dict[str, Any], calendar: list[str], membership_by_code: dict[str, list[dict[str, str]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    calendar_index = {day: index for index, day in enumerate(calendar)}
    entries = {entry["code"]: entry for entry in manifest["price_entries"]}
    if set(membership_by_code) - set(entries):
        raise RuntimeError("历史成分中存在无标准行情证券")

    benchmark_values: dict[tuple[str, int], list[float]] = defaultdict(list)
    benchmark_signal_counts: dict[tuple[str, int], int] = defaultdict(int)
    signals: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    validated_price_files = 0

    for code in sorted(entries):
        entry = entries[code]
        price_path = CANONICAL_ROOT / entry["path"]
        if price_path.stat().st_size != entry["bytes"] or sha256_file(price_path) != entry["sha256"]:
            raise RuntimeError(f"行情文件哈希或大小已变化: {entry['path']}")
        rows = read_csv(price_path)
        if len(rows) != entry["rows"]:
            raise RuntimeError(f"行情文件行数已变化: {entry['path']}")
        rows_by_date = {row["trade_date"]: row for row in rows}
        if len(rows_by_date) != len(rows):
            raise RuntimeError(f"行情日期重复: {code}")
        validated_price_files += 1
        last_signal_index: dict[str, int] = {}
        cache: dict[tuple[int, int], dict[str, Any]] = {}

        for member in membership_by_code.get(code, []):
            signal_date = member["observed_date"]
            signal_index = calendar_index[signal_date]
            flags = mechanism_flags(rows_by_date, calendar, signal_index)
            for horizon in HORIZONS:
                observation = forward_observation(rows_by_date, calendar, signal_index, horizon)
                cache[(signal_index, horizon)] = observation
                benchmark_signal_counts[(signal_date, horizon)] += 1
                if observation["status"] == "OBSERVED":
                    benchmark_values[(signal_date, horizon)].append(float(observation["forward_return"]))

            for mechanism in ("L1", "R1"):
                if not flags[mechanism]:
                    continue
                previous = last_signal_index.get(mechanism)
                if not cooldown_allows(previous, signal_index):
                    continue
                last_signal_index[mechanism] = signal_index
                signal_id = f"{mechanism}:{code}:{signal_date}"
                signals.append({
                    "signal_id": signal_id,
                    "mechanism": mechanism,
                    "code": code,
                    "code_name": member.get("code_name", ""),
                    "signal_date": signal_date,
                    "signal_calendar_index": signal_index,
                    "cooldown_trading_days": 126,
                    "evidence_class": EVIDENCE_CLASS,
                    "accounting_scope": ACCOUNTING_SCOPE,
                })
                for horizon in HORIZONS:
                    observation = cache[(signal_index, horizon)]
                    row: dict[str, Any] = {
                        "signal_id": signal_id,
                        "mechanism": mechanism,
                        "code": code,
                        "code_name": member.get("code_name", ""),
                        "signal_date": signal_date,
                        "horizon": horizon,
                        **observation,
                    }
                    observations.append(row)

    for row in observations:
        key = (row["signal_date"], int(row["horizon"]))
        mean, median = benchmark_stats(benchmark_values.get(key, []))
        row["benchmark_member_count"] = benchmark_signal_counts.get(key, 0)
        row["benchmark_observed_count"] = len(benchmark_values.get(key, []))
        row["benchmark_return"] = mean
        row["benchmark_median_return"] = median
        row["excess_return"] = (
            float(row["forward_return"]) - mean
            if row["status"] == "OBSERVED" and mean is not None
            else None
        )
        row["evidence_class"] = EVIDENCE_CLASS
        row["accounting_scope"] = ACCOUNTING_SCOPE

    diagnostics = {
        "validated_price_files": validated_price_files,
        "membership_codes": len(membership_by_code),
        "membership_observation_dates": len({row["observed_date"] for rows in membership_by_code.values() for row in rows}),
        "calendar_days": len(calendar),
        "signals": len(signals),
        "observations": len(observations),
        "observed_by_horizon": {
            str(horizon): sum(row["status"] == "OBSERVED" and row["horizon"] == horizon for row in observations)
            for horizon in HORIZONS
        },
        "unobserved_reasons": dict(sorted((
            status,
            sum(row["status"] == status for row in observations),
        ) for status in {row["status"] for row in observations} if status != "OBSERVED")),
    }
    return signals, observations, diagnostics


def format_pct(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:.2f}%"


def render_report(summary: list[dict[str, Any]], diagnostics: dict[str, Any], created_at: str) -> str:
    overall = [row for row in summary if row["group_type"] == "overall"]
    lines = [
        "# 左右侧价格机制历史诊断结果",
        "",
        f"运行时间：{created_at}",
        "",
        "## 结论边界",
        "",
        "这是价格形态事件研究，不是完整选股策略、组合回测、虚拟账户或实盘建议。未使用财务、估值、行业、仓位、费用和真实成交限制；全部证据标记为 `HISTORICAL_DIAGNOSTIC_WITH_PRIOR_EXPOSURE`。",
        "",
        "## 覆盖",
        "",
        f"- 已验行情文件：{diagnostics['validated_price_files']}；历史成分证券：{diagnostics['membership_codes']}；周频观察日：{diagnostics['membership_observation_dates']}。",
        f"- 去重后信号：{diagnostics['signals']}；信号期限观察：{diagnostics['observations']}。",
        f"- 未观察原因：{json.dumps(diagnostics['unobserved_reasons'], ensure_ascii=False, sort_keys=True)}。",
        "",
        "## 总体描述",
        "",
        "|机制|期限|信号数|可观察数|覆盖率|中位收益|平均收益|正收益率|中位超额|平均超额|超额为正比例|中位最大不利波动|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in overall:
        lines.append(
            f"|{row['mechanism']}|{row['horizon']}日|{row['signal_count']}|{row['observed_count']}|"
            f"{format_pct(row['coverage_rate'])}|{format_pct(row['median_return'])}|{format_pct(row['mean_return'])}|"
            f"{format_pct(row['positive_rate'])}|{format_pct(row['median_excess_return'])}|{format_pct(row['mean_excess_return'])}|"
            f"{format_pct(row['positive_excess_rate'])}|{format_pct(row['median_max_adverse_excursion'])}|"
        )
    lines.extend([
        "",
        "完整的年度、固定四年阶段及总体分组见 `summary.csv`；逐信号期限结果见 `observations.csv`。不能只依据总体均值晋级策略，需同时检查跨年份稳定性、尾部亏损和覆盖率。",
        "",
        "## 已知限制与代价",
        "",
        "- 股票范围是历史沪深300周频快照，不是全A；周中临时调整可能遗漏，且成员历史尚未与中证官方公告全量交叉验证。",
        "- 前复权日线只用于形态和收益代理；下一日开盘不等于可成交，未计涨跌停、滑点、费用、公司行动真实份额与订单拒绝。",
        "- 任何必要日线缺失都会使对应期限不可观察，处理保守，但可能降低样本覆盖。",
        "- 历史区间已被研究者接触，不能重新命名为独立样本外；后续完整策略仍须预先冻结并做真实前向虚拟验证。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="显式输出目录；必须不存在")
    args = parser.parse_args()
    manifest, calendar, membership_by_code = load_and_validate_inputs()
    signals, observations, diagnostics = run_diagnostic(manifest, calendar, membership_by_code)
    summary = summarize_observations(observations)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or REPORTS_ROOT / f"price_mechanism_diagnostic_{timestamp}"
    output.mkdir(parents=True, exist_ok=False)
    signals_path = output / "signals.csv"
    observations_path = output / "observations.csv"
    summary_path = output / "summary.csv"
    summary_json_path = output / "summary.json"
    report_path = output / "report.md"

    write_csv(signals_path, signals, [
        "signal_id", "mechanism", "code", "code_name", "signal_date", "signal_calendar_index",
        "cooldown_trading_days", "evidence_class", "accounting_scope",
    ])
    write_csv(observations_path, observations, [
        "signal_id", "mechanism", "code", "code_name", "signal_date", "horizon", "status",
        "entry_date", "end_date", "entry_open_qfq", "end_close_qfq", "forward_return",
        "max_favorable_excursion", "max_adverse_excursion", "benchmark_member_count",
        "benchmark_observed_count", "benchmark_return", "benchmark_median_return", "excess_return",
        "evidence_class", "accounting_scope",
    ])
    summary_fields = [
        "mechanism", "horizon", "group_type", "group_value", "signal_count", "observed_count",
        "coverage_rate", "mean_return", "median_return", "p10_return", "p25_return", "p75_return",
        "positive_rate", "mean_benchmark_return", "mean_excess_return", "median_excess_return",
        "positive_excess_rate", "median_max_adverse_excursion", "median_max_favorable_excursion",
    ]
    write_csv(summary_path, summary, summary_fields)
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    summary_json_path.write_text(json.dumps({
        "status": "PASS_DESCRIPTIVE_ONLY",
        "created_at": created_at,
        "evidence_class": EVIDENCE_CLASS,
        "accounting_scope": ACCOUNTING_SCOPE,
        "diagnostics": diagnostics,
        "overall": [row for row in summary if row["group_type"] == "overall"],
        "limits": [
            "不是完整选股策略、组合回测、虚拟账户或实盘建议",
            "历史沪深300周频成分范围，不代表全A",
            "未纳入财务估值、成本和实际可成交限制",
            "历史区间已有研究暴露，不是独立样本外",
        ],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_report(summary, diagnostics, created_at), encoding="utf-8")

    outputs = [
        file_entry(output, signals_path, rows=len(signals)),
        file_entry(output, observations_path, rows=len(observations)),
        file_entry(output, summary_path, rows=len(summary)),
        file_entry(output, summary_json_path),
        file_entry(output, report_path),
    ]
    manifest_path = output / "run_manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": 1,
        "status": "SEALED",
        "created_at": created_at,
        "purpose": "left_right_price_mechanism_historical_diagnostic",
        "evidence_class": EVIDENCE_CLASS,
        "accounting_scope": ACCOUNTING_SCOPE,
        "plan_path": PLAN_PATH.relative_to(PROJECT_ROOT).as_posix(),
        "plan_sha256": sha256_file(PLAN_PATH),
        "implementation": [
            {
                "path": Path(__file__).resolve().relative_to(PROJECT_ROOT).as_posix(),
                "sha256": sha256_file(Path(__file__).resolve()),
            },
            {
                "path": "src/ashare_lab/selection/price_mechanism.py",
                "sha256": sha256_file(PROJECT_ROOT / "src" / "ashare_lab" / "selection" / "price_mechanism.py"),
            },
        ],
        "canonical_path": CANONICAL_ROOT.relative_to(PROJECT_ROOT).as_posix(),
        "canonical_manifest_sha256": sha256_file(CANONICAL_ROOT / "manifest.json"),
        "parameters": {
            "signal_dates": "exact_weekly_membership_observation_dates",
            "L1_drawdown": -0.20,
            "L1_prior_days": 252,
            "R1_breakout_prior_days": 120,
            "R1_moving_average_days": 60,
            "R1_moving_average_lag_days": 20,
            "cooldown_trading_days": 126,
            "horizons": list(HORIZONS),
            "entry_price": "next_common_trading_day_open_qfq",
        },
        "diagnostics": diagnostics,
        "outputs": outputs,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "SEALED", "output": str(output), **diagnostics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
