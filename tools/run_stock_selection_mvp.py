#!/usr/bin/env python
"""运行冻结的历史沪深300价格量选股研究组合。"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import argparse
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.mvp import CostConfig, DividendEvent, calculate_period_metrics, run_engine  # noqa: E402
from ashare_lab.selection.engine import ResearchCostConfig, ResearchEngineResult, execution_rejection, number, run_research_engine  # noqa: E402
from ashare_lab.sources.inferred_rule_audit import infer_rule  # noqa: E402
from ashare_lab.selection.metrics import calculate_research_metrics  # noqa: E402
from ashare_lab.selection.strategy import (  # noqa: E402
    MembershipHistory,
    factor_observations,
    month_end_dates,
    rank_candidates,
    select_codes,
)


CANONICAL_ROOT = PROJECT_ROOT / "data" / "canonical" / "stock_selection_v1"
BENCHMARK_ROOT = PROJECT_ROOT / "data" / "canonical" / "mvp_510300_v2"
REPORTS_ROOT = PROJECT_ROOT / "reports"
INITIAL_EQUITY = Decimal("1000000")
MAIN_COUNT = 30
ROBUSTNESS_COUNTS = (20, 40)
OOS_START = "2023-01-01"
PERIODS = {
    "development_2013_2018": ("2013-01-01", "2018-12-31"),
    "validation_2019_2022": ("2019-01-01", "2022-12-31"),
    "out_of_sample_2023_2026": (OOS_START, "2026-12-31"),
}
COSTS = ResearchCostConfig()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_entry(root: Path, path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if path.suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            result["rows"] = max(sum(1 for _ in handle) - 1, 0)
    return result


def latest_sealed_canonical() -> Path:
    manifest_path = CANONICAL_ROOT / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("选股标准层不存在")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SEALED" or manifest.get("code_count") != 940:
        raise RuntimeError("选股标准层未通过940只独立封存闸门")
    return CANONICAL_ROOT


def load_corporate_action_evidence() -> dict[str, dict[str, str]]:
    """读取已封存的公司行动证据，仅用于阻断未结算持仓。"""
    root = PROJECT_ROOT / "data" / "imports" / "corporate_actions_batch1_20260908"
    evidence: dict[str, dict[str, str]] = {}
    for path in sorted(root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        code = payload.get("source_code", "")
        effective = payload.get("effective_date", "")
        if code and effective:
            evidence[code] = {
                "last_tradable_date": effective,
                "event_type": payload.get("event_type", "UNKNOWN"),
                "evidence_path": str(path.relative_to(PROJECT_ROOT)),
            }
    return evidence


class CachedCsvMarketProvider:
    def __init__(self, canonical: Path, price_entries: list[dict[str, Any]], max_cached_codes: int = 640):
        self.canonical = canonical
        self.paths = {entry["code"]: canonical / entry["path"] for entry in price_entries}
        self._load = lru_cache(maxsize=max_cached_codes)(self._load_uncached)

    def _load_uncached(self, code: str, year: str) -> dict[str, dict[str, str]]:
        path = self.paths.get(code)
        if path is None:
            return {}
        with path.open(encoding="utf-8",newline="") as handle:
            return {row["trade_date"]: row for row in csv.DictReader(handle) if row["trade_date"].startswith(year)}

    def __call__(self, code: str, trade_date: str) -> dict[str, str] | None:
        return self._load(code, trade_date[:4]).get(trade_date)


def research_metrics(result: ResearchEngineResult, data_cutoff: str) -> dict[str, dict[str, Any]]:
    output = {}
    for name, (start, end) in PERIODS.items():
        output[name] = calculate_research_metrics(result.ledger, result.trades, start, min(end, data_cutoff))
    return output


def benchmark_result(market: list[dict[str, str]], dividends: list[DividendEvent]):
    signal_dates = [row["trade_date"] for row in market if row["trade_date"] < "2013-01-01"]
    if not signal_dates:
        raise RuntimeError("510300基准缺少2013年前信号日")
    signals = [{"signal_date": signal_dates[-1], "target_position": "1"}]
    costs = CostConfig(
        commission_rate=Decimal("0.0003"),
        minimum_commission=Decimal("5"),
        slippage_bps=Decimal("5"),
        sell_stamp_tax_rate=Decimal("0"),
        lot_size=100,
    )
    return run_engine(market, signals, dividends, initial_cash=INITIAL_EQUITY, costs=costs)


def output_engine(root: Path, prefix: str, result: ResearchEngineResult) -> list[Path]:
    outputs = [
        (root / f"{prefix}_orders.csv", result.orders, [
            "order_id", "signal_date", "order_date", "code", "side", "requested_notional", "status", "reason",
        ]),
        (root / f"{prefix}_trades.csv", result.trades, [
            "trade_id", "order_id", "signal_date", "trade_date", "code", "side", "gross_notional",
            "slippage_cost", "commission", "stamp_tax", "cash_change",
        ]),
        (root / f"{prefix}_ledger.csv", result.ledger, [
            "trade_date", "research_cash", "holdings_value", "equity", "gross_exposure", "holding_count",
            "signal_at_close", "accounting_scope",
        ]),
        (root / f"{prefix}_positions.csv", result.positions, [
            "trade_date", "code", "research_value", "weight", "qfq_reference_close", "stale_days",
        ]),
    ]
    for path, rows, fields in outputs:
        write_csv(path, rows, fields)
    return [item[0] for item in outputs]


def main() -> int:
    parser = argparse.ArgumentParser(description="运行选股研究组合")
    parser.add_argument("--halt-policy", choices=("strict", "recent20", "short5", "all", "cool5", "cool10", "cool20"), default="strict")
    parser.add_argument("--execution-policy", choices=("baseline", "inferred_open_limit"), default="baseline")
    args = parser.parse_args()
    def execution_policy(row, side):
        reason = execution_rejection(row, side)
        if reason or args.execution_policy != "inferred_open_limit" or row is None:
            return reason
        rule = infer_rule(row)
        raw_open = number(row.get("open", ""))
        if rule is not None and raw_open is not None:
            if side == "BUY" and abs(raw_open - Decimal(str(rule.limit_up))) <= Decimal("0.001"):
                return "INFERRED_LIMIT_UP_OPEN"
            if side == "SELL" and abs(raw_open - Decimal(str(rule.limit_down))) <= Decimal("0.001"):
                return "INFERRED_LIMIT_DOWN_OPEN"
        return ""
    halt_policy = {
        "strict": dict(max_window_halt_days=0, max_consecutive_halt_days=0),
        "recent20": dict(max_window_halt_days=253, max_consecutive_halt_days=253, recent_halt_window=20, max_recent_halt_days=0),
        "short5": dict(max_window_halt_days=5, max_consecutive_halt_days=5),
        "all": dict(max_window_halt_days=253, max_consecutive_halt_days=253),
        "cool5": dict(max_window_halt_days=253, max_consecutive_halt_days=253, resumption_cooldown_days=5),
        "cool10": dict(max_window_halt_days=253, max_consecutive_halt_days=253, resumption_cooldown_days=10),
        "cool20": dict(max_window_halt_days=253, max_consecutive_halt_days=253, resumption_cooldown_days=20),
    }[args.halt_policy]
    canonical = latest_sealed_canonical()
    canonical_manifest_path = canonical / "manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    benchmark_manifest = json.loads((BENCHMARK_ROOT / "manifest.json").read_text(encoding="utf-8"))
    if benchmark_manifest.get("status") != "SEALED":
        raise RuntimeError("510300基准标准层未封存")

    benchmark_market = read_csv(BENCHMARK_ROOT / "daily.csv")
    data_cutoff = benchmark_market[-1]["trade_date"]
    engine_calendar = [row["trade_date"] for row in benchmark_market if row["trade_date"] >= "2012-12-01"]
    all_month_ends = month_end_dates(engine_calendar)
    signal_dates = [value for value in all_month_ends if value >= "2012-12-01" and value[:7] < data_cutoff[:7]]
    if not signal_dates or signal_dates[-1][:7] == data_cutoff[:7]:
        raise RuntimeError("未正确排除数据截止月的伪月末信号")

    membership_rows = read_csv(canonical / "membership_weekly.csv")
    factor_calendar = [r["trade_date"] for r in read_csv(canonical/"trade_calendar.csv")]
    membership = MembershipHistory(membership_rows)
    members_by_signal = {value: membership.as_of(value) for value in signal_dates}
    observations_by_date: dict[str, list[dict[str, str]]] = defaultdict(list)

    price_entries = canonical_manifest["price_entries"]
    for index, entry in enumerate(price_entries, start=1):
        code = entry["code"]
        relevant_dates = [value for value in signal_dates if code in members_by_signal[value]]
        if relevant_dates:
            rows = read_csv(canonical / entry["path"])
            for observation in factor_observations(rows, relevant_dates, trading_dates=factor_calendar, **halt_policy):
                observations_by_date[observation["signal_date"]].append(observation)
        if index % 100 == 0 or index == len(price_entries):
            print(f"factor_progress {index}/{len(price_entries)}", flush=True)

    ranked_by_date: dict[str, list[dict[str, str]]] = {}
    candidate_rows: list[dict[str, str]] = []
    for signal_date in signal_dates:
        ranked = rank_candidates(observations_by_date.get(signal_date, []))
        if len(ranked) < max(ROBUSTNESS_COUNTS):
            raise RuntimeError(f"{signal_date} 合格股票不足40只: {len(ranked)}")
        for rank, row in enumerate(ranked, start=1):
            row["rank"] = str(rank)
            row["eligible_count"] = str(len(ranked))
            candidate_rows.append(row)
        ranked_by_date[signal_date] = ranked

    schedules = {
        count: {signal_date: select_codes(ranked_by_date[signal_date], count) for signal_date in signal_dates}
        for count in (MAIN_COUNT, *ROBUSTNESS_COUNTS)
    }
    diagnostic_schedules = {name:{} for name in ("equal_eligible_pool","momentum_only","low_volatility_only")}
    for day,ranked in ranked_by_date.items():
        diagnostic_schedules["equal_eligible_pool"][day]=sorted(r["code"] for r in ranked)
        diagnostic_schedules["momentum_only"][day]=[r["code"] for r in sorted(ranked,key=lambda r:(-Decimal(r["momentum_12_1"]),r["code"]))[:30]]
        diagnostic_schedules["low_volatility_only"][day]=[r["code"] for r in sorted(ranked,key=lambda r:(Decimal(r["volatility_63"]),r["code"]))[:30]]
    selected_codes = sorted({code for schedule in schedules.values() for codes in schedule.values() for code in codes})
    market_provider = CachedCsvMarketProvider(canonical, price_entries)
    corporate_action_evidence = load_corporate_action_evidence()

    results: dict[str, ResearchEngineResult] = {}
    for count in (MAIN_COUNT, *ROBUSTNESS_COUNTS):
        print(f"engine_start count={count}", flush=True)
        results[str(count)] = run_research_engine(
            engine_calendar, (), schedules[count], initial_equity=INITIAL_EQUITY, costs=COSTS,
            market_row_provider=market_provider,
            event_evidence=corporate_action_evidence,
            execution_rejection_fn=execution_policy,
        )
    print("engine_start cost_stress=2x", flush=True)
    stress = run_research_engine(
        engine_calendar, (), schedules[MAIN_COUNT], initial_equity=INITIAL_EQUITY,
        costs=COSTS.scaled(Decimal("2")), market_row_provider=market_provider,
        event_evidence=corporate_action_evidence,
        execution_rejection_fn=execution_policy,
    )

    dividends = [
        DividendEvent(
            record_date=row["record_date"], ex_date=row["ex_date"], payment_date=row["payment_date"],
            cash_per_share=Decimal(row["cash_per_share"]),
        )
        for row in read_csv(BENCHMARK_ROOT / "dividends.csv")
    ]
    benchmark = benchmark_result(benchmark_market, dividends)
    benchmark_periods = {
        name: calculate_period_metrics(benchmark.ledger, benchmark.trades, start, min(end, data_cutoff))
        for name, (start, end) in PERIODS.items()
    }
    scenario_metrics = {key: research_metrics(result, data_cutoff) for key, result in results.items()}
    stress_metrics = research_metrics(stress, data_cutoff)

    annual_excess = []
    for year in (2023, 2024, 2025):
        start, end = f"{year}-01-01", f"{year}-12-31"
        strategy_year = calculate_research_metrics(results[str(MAIN_COUNT)].ledger, results[str(MAIN_COUNT)].trades, start, end)
        benchmark_year = calculate_period_metrics(benchmark.ledger, benchmark.trades, start, end)
        annual_excess.append({
            "year": year,
            "strategy_total_return": strategy_year["total_return"],
            "benchmark_total_return": benchmark_year["total_return"],
            "excess_return": strategy_year["total_return"] - benchmark_year["total_return"],
            "positive_excess": strategy_year["total_return"] > benchmark_year["total_return"],
        })

    period = "out_of_sample_2023_2026"
    main_oos = scenario_metrics[str(MAIN_COUNT)][period]
    benchmark_oos = benchmark_periods[period]
    checks = {
        "oos_after_cost_return_positive": main_oos["total_return"] > 0,
        "oos_return_higher_than_510300": main_oos["total_return"] > benchmark_oos["total_return"],
        "oos_max_drawdown_lower_than_510300": abs(main_oos["max_drawdown"]) < abs(benchmark_oos["max_drawdown"]),
        "oos_calmar_not_lower_than_510300": (
            main_oos["calmar"] is not None and benchmark_oos["calmar"] is not None
            and main_oos["calmar"] >= benchmark_oos["calmar"]
        ),
        "at_least_two_of_three_complete_years_positive_excess": sum(
            bool(row["positive_excess"]) for row in annual_excess
        ) >= 2,
        "double_cost_oos_return_positive": stress_metrics[period]["total_return"] > 0,
        "neighbor_20_and_40_oos_returns_positive": all(
            scenario_metrics[str(count)][period]["total_return"] > 0 for count in ROBUSTNESS_COUNTS
        ),
    }
    gate = "PASS" if all(checks.values()) else "FAIL"

    run_id = datetime.now(timezone.utc).strftime("stock_selection_mvp_%Y%m%dT%H%M%SZ") + f"_{args.halt_policy}_{args.execution_policy}"
    output_root = REPORTS_ROOT / run_id
    if output_root.exists():
        raise RuntimeError(f"运行目录已存在: {output_root}")
    output_root.mkdir(parents=True)
    written: list[Path] = []

    candidate_fields = [
        "signal_date", "code", "momentum_12_1", "volatility_63", "median_amount_20",
        "momentum_percentile", "low_volatility_percentile", "composite_score", "rank", "eligible_count",
    ]
    candidates_path = output_root / "factor_candidates.csv"
    write_csv(candidates_path, candidate_rows, candidate_fields)
    written.append(candidates_path)
    for count, schedule in schedules.items():
        target_rows = [
            {"signal_date": signal_date, "rank": str(rank), "code": code, "target_weight": format(COSTS.target_exposure / Decimal(count), ".12f")}
            for signal_date, codes in schedule.items() for rank, code in enumerate(codes, start=1)
        ]
        path = output_root / f"targets_{count}.csv"
        write_csv(path, target_rows, ["signal_date", "rank", "code", "target_weight"])
        written.append(path)

    for key, result in results.items():
        written.extend(output_engine(output_root, f"portfolio_{key}", result))
    written.extend(output_engine(output_root, "portfolio_30_cost_stress_2x", stress))
    benchmark_ledger_path = output_root / "benchmark_510300_ledger.csv"
    benchmark_trades_path = output_root / "benchmark_510300_trades.csv"
    write_csv(benchmark_ledger_path, benchmark.ledger, [
        "trade_date", "cash", "dividend_receivable", "shares", "close", "market_value", "equity",
        "signal_target_at_close", "pending_execution_after_close",
    ])
    write_csv(benchmark_trades_path, benchmark.trades, [
        "trade_id", "order_id", "signal_date", "trade_date", "side", "quantity", "execution_price",
        "gross_amount", "commission", "stamp_tax", "cash_change",
    ])
    written.extend([benchmark_ledger_path, benchmark_trades_path])

    metrics = {
        "evidence_class": "HISTORICAL_HOLDOUT_WITH_PRIOR_EXPOSURE",
        "forward_validation_status": "NOT_STARTED",
        "halt_policy": args.halt_policy,
        "execution_policy": args.execution_policy,
        "halt_policy_parameters": halt_policy,
        "strategy_version": "selection_halt_sensitivity_v2" if args.halt_policy != "strict" else "selection_v1_frozen",
        "experiment_status": "COMPLETED_PENDING_INDEPENDENT_VALIDATION",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "accounting_scope": "RESEARCH_PORTFOLIO",
        "live_broker_connected": False,
        "data_cutoff": data_cutoff,
        "signal_count": len(signal_dates),
        "selected_code_union": len(selected_codes),
        "initial_equity": float(INITIAL_EQUITY),
        "cost_scenario": {
            "commission_rate": float(COSTS.commission_rate),
            "minimum_commission": float(COSTS.minimum_commission),
            "slippage_bps_each_side": float(COSTS.slippage_bps),
            "sell_stamp_tax_before_2023_08_28": float(COSTS.stamp_tax_before),
            "sell_stamp_tax_from_2023_08_28": float(COSTS.stamp_tax_after),
            "target_exposure": float(COSTS.target_exposure),
        },
        "portfolio_metrics": scenario_metrics,
        "double_cost_metrics": stress_metrics,
        "benchmark_510300_metrics": benchmark_periods,
        "complete_year_excess": annual_excess,
        "research_gate": {"status": gate, "checks": checks},
        "next_action": "ELIGIBLE_FOR_CORPORATE_ACTION_LEDGER_BUILD" if gate == "PASS" else "REJECT_STRATEGY",
    }
    diagnostic_metrics={}
    for name,schedule in diagnostic_schedules.items():
        print("diagnostic_start "+name,flush=True)
        result=run_research_engine(engine_calendar,(),schedule,initial_equity=INITIAL_EQUITY,costs=COSTS,market_row_provider=market_provider)
        prefix="diagnostic_"+name
        written.extend(output_engine(output_root,prefix,result))
        diagnostic_metrics[name]=research_metrics(result,data_cutoff)
        target_rows=[dict(signal_date=day,rank=str(i),code=code,target_weight=format(COSTS.target_exposure/len(codes),".12f"))
                     for day,codes in schedule.items() for i,code in enumerate(codes,1)]
        path=output_root/(prefix+"_targets.csv")
        write_csv(path,target_rows,["signal_date","rank","code","target_weight"]); written.append(path)
        del result
    metrics["diagnostic_metrics"]=diagnostic_metrics
    metrics["diagnostics_used_for_promotion"]=False
    metrics_path = output_root / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    written.append(metrics_path)

    summary_path = output_root / "summary.md"
    summary_path.write_text(
        "# 历史沪深300价格量选股MVP\n\n"
        f"- 会计范围：`RESEARCH_PORTFOLIO`，不是券商现金与真实股数账户。\n"
        "- 证据级别：已接触过市场表现的历史留出区间；尚未开始新增日期前向验证。\n"
        f"- 数据截止：{data_cutoff}\n"
        f"- 月度信号：{len(signal_dates)}次\n"
        f"- 主策略：12-1动量与低波动各50%，前30只，95%目标仓位。\n"
        f"- 最终样本外研究闸门：`{gate}`\n"
        f"- 样本外策略总收益：{main_oos['total_return']:.4%}\n"
        f"- 样本外510300总收益：{benchmark_oos['total_return']:.4%}\n"
        f"- 样本外策略最大回撤：{main_oos['max_drawdown']:.4%}\n"
        f"- 样本外510300最大回撤：{benchmark_oos['max_drawdown']:.4%}\n"
        f"- 下一步：`{metrics['next_action']}`\n\n"
        "通过也只允许补建公司行动和真实账本；失败则淘汰策略。\n",
        encoding="utf-8",
    )
    written.append(summary_path)
    manifest = {
        "schema_version": 1,
        "status": "SEALED_PENDING_INDEPENDENT_VALIDATION",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "accounting_scope": "RESEARCH_PORTFOLIO",
        "evidence_class": "HISTORICAL_HOLDOUT_WITH_PRIOR_EXPOSURE",
        "forward_validation_status": "NOT_STARTED",
        "halt_policy": args.halt_policy,
        "execution_policy": args.execution_policy,
        "halt_policy_parameters": halt_policy,
        "strategy_version": "selection_halt_sensitivity_v2" if args.halt_policy != "strict" else "selection_v1_frozen",
        "validation_decision_sha256": sha256_file(PROJECT_ROOT/"docs/DECISION_20260907_RESEARCH_VALIDATION.md"),
        "code_sha256": {p.relative_to(PROJECT_ROOT).as_posix():sha256_file(p) for p in [
            Path(__file__), PROJECT_ROOT/"src/ashare_lab/selection/strategy.py",PROJECT_ROOT/"src/ashare_lab/selection/engine.py",
            PROJECT_ROOT/"tools/selection_independent_audit.py",PROJECT_ROOT/"tools/validate_stock_selection_mvp_run.py"]},
        "live_broker_connected": False,
        "canonical_manifest_sha256": sha256_file(canonical_manifest_path),
        "design_v1_sha256": sha256_file(PROJECT_ROOT / "docs" / "STOCK_SELECTION_MVP_DESIGN.md"),
        "design_v1_1_sha256": sha256_file(PROJECT_ROOT / "docs" / "STOCK_SELECTION_MVP_DESIGN_V1_1.md"),
        "decision_sha256": sha256_file(PROJECT_ROOT / "docs" / "DECISION_20260904_SELECTION_ACCOUNTING_SCOPE.md"),
        "research_gate": gate,
        "entries": [file_entry(output_root, path) for path in written],
        "limits": [
            "不是券商现金与真实股数账户",
            "前复权收益包含公司行动效果但不展开真实公司行动路径",
            "通过研究闸门也不得直接进入虚拟盘或实盘",
        ],
    }
    manifest_path = output_root / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "run": str(output_root), "status": manifest["status"], "research_gate": gate,
        "oos_strategy_return": main_oos["total_return"], "oos_benchmark_return": benchmark_oos["total_return"],
        "next_action": metrics["next_action"],
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
