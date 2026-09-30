#!/usr/bin/env python
"""独立复核选股研究组合的文件、成本、持仓和研究闸门。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import bisect
from functools import lru_cache
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_ROOT = PROJECT_ROOT / "reports"
MONEY = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def latest_pending() -> Path:
    candidates = []
    for path in REPORTS_ROOT.glob("stock_selection_mvp_*"):
        manifest_path = path / "run_manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") == "SEALED_PENDING_INDEPENDENT_VALIDATION":
            candidates.append(path)
    if not candidates:
        raise RuntimeError("没有等待独立验证的选股MVP运行")
    return sorted(candidates)[-1]


def independent_metrics(ledger: list[dict[str, str]], start_date: str, end_date: str = "9999-12-31") -> dict[str, float]:
    selected = [row for row in ledger if start_date <= row["trade_date"] <= end_date]
    before = [row for row in ledger if row["trade_date"] < start_date]
    if not selected or not before:
        raise RuntimeError("独立指标复算缺少样本外或基准日前账本")
    baseline = Decimal(before[-1]["equity"])
    values = [baseline] + [Decimal(row["equity"]) for row in selected]
    total_return = float(values[-1] / baseline - 1)
    annualized = float(values[-1] / baseline) ** (252 / len(selected)) - 1
    peak = values[0]
    drawdown = Decimal("0")
    for value in values[1:]:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    calmar = annualized / abs(float(drawdown)) if drawdown else None
    return {"total_return": total_return, "max_drawdown": float(drawdown), "calmar": calmar}


def validate_costs(trades: list[dict[str, str]], multiplier: Decimal, errors: list[str], label: str) -> None:
    commission_rate = Decimal("0.0003") * multiplier
    minimum = Decimal("5") * multiplier
    slip_rate = Decimal("0.0005") * multiplier
    for row in trades:
        gross = Decimal(row["gross_notional"])
        expected_commission = money(max(minimum, gross * commission_rate))
        expected_slippage = money(gross * slip_rate)
        rate = Decimal("0")
        if row["side"] == "SELL":
            rate = (Decimal("0.0005") if row["trade_date"] >= "2023-08-28" else Decimal("0.001")) * multiplier
        expected_stamp = money(gross * rate)
        if Decimal(row["commission"]) != expected_commission:
            errors.append(f"{label} 佣金错误: {row['trade_id']}")
        if Decimal(row["slippage_cost"]) != expected_slippage:
            errors.append(f"{label} 滑点错误: {row['trade_id']}")
        if Decimal(row["stamp_tax"]) != expected_stamp:
            errors.append(f"{label} 印花税错误: {row['trade_id']}")
        expected_cash = gross - expected_slippage - expected_commission - expected_stamp
        if row["side"] == "BUY":
            expected_cash = -gross - expected_slippage - expected_commission
        if Decimal(row["cash_change"]) != expected_cash:
            errors.append(f"{label} 现金变化错误: {row['trade_id']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="独立验证选股MVP运行")
    parser.add_argument("--run", type=Path, help="待验证运行目录")
    args = parser.parse_args()
    root = args.run.resolve() if args.run else latest_pending()
    if root.parent != REPORTS_ROOT.resolve() or not root.name.startswith("stock_selection_mvp_"):
        raise RuntimeError(f"运行目录不在允许范围: {root}")
    manifest_path = root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SEALED_PENDING_INDEPENDENT_VALIDATION":
        raise RuntimeError(f"运行不处于待验证状态: {manifest.get('status')}")

    errors: list[str] = []
    for entry in manifest.get("entries", []):
        path = root / entry["path"]
        if not path.is_file():
            errors.append(f"文件缺失: {entry['path']}")
            continue
        if path.stat().st_size != entry["bytes"] or sha256_file(path) != entry["sha256"]:
            errors.append(f"大小或SHA256不一致: {entry['path']}")
        if path.suffix == ".csv":
            with path.open("r", encoding="utf-8", newline="") as handle:
                rows = max(sum(1 for _ in handle) - 1, 0)
            if rows != entry.get("rows"):
                errors.append(f"CSV行数不一致: {entry['path']}")

    metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    if manifest.get("accounting_scope") != "RESEARCH_PORTFOLIO" or metrics.get("accounting_scope") != "RESEARCH_PORTFOLIO":
        errors.append("会计范围不是RESEARCH_PORTFOLIO")
    if manifest.get("live_broker_connected") is not False or metrics.get("live_broker_connected") is not False:
        errors.append("运行错误地声明了实盘连接")

    candidates = read_csv(root / "factor_candidates.csv")
    canonical = PROJECT_ROOT/"data/canonical/stock_selection_v1"
    cmp = canonical/"manifest.json"
    if sha256_file(cmp) != manifest["canonical_manifest_sha256"]: raise ValueError("标准层清单哈希改变")
    cm = json.loads(cmp.read_text(encoding="utf-8"))
    if cm.get("status") != "SEALED": raise ValueError("标准层未封存")
    for e in [*cm["price_entries"], cm["membership_entry"], cm["calendar_entry"]]:
        if sha256_file(canonical/e["path"]) != e["sha256"]: raise ValueError("标准行情或成分内容改变")
    calendar = [r["trade_date"] for r in read_csv(canonical/"trade_calendar.csv")]
    benchmark_calendar = [r["trade_date"] for r in read_csv(PROJECT_ROOT/"data/canonical/mvp_510300_v2/daily.csv")]
    engine_calendar = [d for d in benchmark_calendar if d>="2012-12-01"]
    months = {}
    for d in engine_calendar: months[d[:7]]=d
    signal_dates = sorted(d for month,d in months.items() if month<benchmark_calendar[-1][:7])
    mg = defaultdict(set)
    for r in read_csv(canonical/"membership_weekly.csv"): mg[r["observed_date"]].add(r["code"])
    md = sorted(mg); membership = {}
    for d in signal_dates:
        i=bisect.bisect_right(md,d)-1
        if i<0 or len(mg[md[i]])!=300: raise ValueError("历史成分缺失或数量错误")
        membership[d]=mg[md[i]]
    sys.path.insert(0,str(PROJECT_ROOT/"tools"))
    from selection_independent_audit import audit_factors, audit_portfolio
    halt_policy = manifest.get("halt_policy_parameters", {})
    expected_factors=audit_factors(canonical,cm["price_entries"],candidates,calendar,signal_dates,membership,halt_policy)
    price_paths={e["code"]:canonical/e["path"] for e in cm["price_entries"]}
    @lru_cache(maxsize=640)
    def market_for_code(code,year):
        with price_paths[code].open(encoding="utf-8",newline="") as f:
            return {r["trade_date"]:r for r in csv.DictReader(f) if r["trade_date"].startswith(year)}
    def provider(code,day): return market_for_code(code,day[:4]).get(day)
    candidate_groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in candidates:
        candidate_groups[row["signal_date"]].append(row)
    for signal_date, rows in candidate_groups.items():
        ranks = sorted(int(row["rank"]) for row in rows)
        if ranks != list(range(1, len(rows) + 1)):
            errors.append(f"{signal_date} 因子排名不连续")
        if any(int(row["eligible_count"]) != len(rows) for row in rows):
            errors.append(f"{signal_date} 合格股票数量字段错误")

    for count in (20, 30, 40):
        targets = read_csv(root / f"targets_{count}.csv")
        groups: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in targets:
            groups[row["signal_date"]].append(row)
        if set(groups) != set(candidate_groups):
            errors.append(f"targets_{count} 信号日期集合不一致")
        for signal_date, rows in groups.items():
            if len(rows) != count:
                errors.append(f"{signal_date} targets_{count} 数量错误")
                continue
            expected = [row["code"] for row in sorted(candidate_groups[signal_date], key=lambda item: int(item["rank"]))[:count]]
            actual = [row["code"] for row in sorted(rows, key=lambda item: int(item["rank"]))]
            if actual != expected:
                errors.append(f"{signal_date} targets_{count} 不是冻结排名前{count}")
            weight_sum = sum(Decimal(row["target_weight"]) for row in rows)
            if abs(weight_sum - Decimal("0.95")) > Decimal("0.0000001"):
                errors.append(f"{signal_date} targets_{count} 权重和错误")

    ledgers: dict[str, list[dict[str, str]]] = {}
    for label in ("portfolio_20", "portfolio_30", "portfolio_40", "portfolio_30_cost_stress_2x"):
        ledger = read_csv(root / f"{label}_ledger.csv")
        positions = read_csv(root / f"{label}_positions.csv")
        orders = read_csv(root / f"{label}_orders.csv")
        trades = read_csv(root / f"{label}_trades.csv")
        ledgers[label] = ledger
        if [r["trade_date"] for r in ledger] != engine_calendar: raise ValueError("账本交易日期不完整")
        count=30 if label.endswith("2x") else int(label.split("_")[-1])
        schedule=defaultdict(list)
        for r in read_csv(root/f"targets_{count}.csv"): schedule[r["signal_date"]].append(r["code"])
        audit_portfolio(ledger,positions,orders,trades,schedule,provider,Decimal(2) if label.endswith("2x") else Decimal(1))
        position_sums: dict[str, Decimal] = defaultdict(Decimal)
        for row in positions:
            position_sums[row["trade_date"]] += Decimal(row["research_value"])
        for row in ledger:
            if row["accounting_scope"] != "RESEARCH_PORTFOLIO":
                errors.append(f"{label} 账本会计范围错误")
            cash = Decimal(row["research_cash"])
            holdings = Decimal(row["holdings_value"])
            equity = Decimal(row["equity"])
            if cash + holdings != equity:
                errors.append(f"{label} {row['trade_date']} 资产恒等式错误")
            if position_sums.get(row["trade_date"], Decimal("0")) != holdings:
                errors.append(f"{label} {row['trade_date']} 持仓汇总错误")
        orders_by_id = {row["order_id"]: row for row in orders}
        if len(orders_by_id) != len(orders):
            errors.append(f"{label} 订单ID重复")
        filled_ids = {row["order_id"] for row in orders if row["status"] == "FILLED"}
        trade_order_ids = {row["order_id"] for row in trades}
        if filled_ids != trade_order_ids:
            errors.append(f"{label} 成交与已成订单集合不一致")
        for order in orders:
            if order["order_date"] <= order["signal_date"]:
                errors.append(f"{label} 非下一日成交语义: {order['order_id']}")
        validate_costs(trades, Decimal("2") if label.endswith("2x") else Decimal("1"), errors, label)

    benchmark_ledger = read_csv(root / "benchmark_510300_ledger.csv")
    for row in benchmark_ledger:
        if Decimal(row["cash"]) + Decimal(row["dividend_receivable"]) + Decimal(row["market_value"]) != Decimal(row["equity"]):
            errors.append(f"510300基准资产恒等式错误: {row['trade_date']}")
    # 与已封存、同起点同金额同成本的ETF持有基准逐行比对，防止只核对恒等式。
    prior_root = REPORTS_ROOT/"mvp_510300_run_20260904T093955"
    prior_manifest=json.loads((prior_root/"run_manifest.json").read_text(encoding="utf-8"))
    be=next(e for e in prior_manifest["outputs"]["entries"] if e["path"]=="benchmark_ledger.csv")
    if prior_manifest["status"]!="SEALED" or sha256_file(prior_root/be["path"])!=be["sha256"]:
        raise ValueError("已审计ETF基准发生变更")
    if benchmark_ledger != read_csv(prior_root/be["path"]): errors.append("ETF基准与既有审计账本不一致")

    period_bounds={"development_2013_2018":("2013-01-01","2018-12-31"),
                   "validation_2019_2022":("2019-01-01","2022-12-31"),
                   "out_of_sample_2023_2026":("2023-01-01","2026-12-31")}
    for label,ledger in ledgers.items():
        stored=metrics["double_cost_metrics"] if label.endswith("2x") else metrics["portfolio_metrics"][label.split("_")[-1]]
        for period,(start,end) in period_bounds.items():
            calculated=independent_metrics(ledger,start,end)
            for key,value in calculated.items():
                actual=stored[period][key]
                if (value is None)!=(actual is None) or value is not None and abs(value-actual)>1e-10:
                    errors.append(f"{label} {period} {key}复算不同")
    for annual in metrics["complete_year_excess"]:
        start,end=f"{annual['year']}-01-01",f"{annual['year']}-12-31"
        a=independent_metrics(ledgers["portfolio_30"],start,end)["total_return"]
        b=independent_metrics(benchmark_ledger,start,end)["total_return"]
        if abs(a-annual["strategy_total_return"])>1e-10 or abs(b-annual["benchmark_total_return"])>1e-10 or abs(a-b-annual["excess_return"])>1e-10 or annual["positive_excess"]!=(a>b):
            errors.append("完整年度超额收益复算不同")
    if {r["year"] for r in metrics["complete_year_excess"]}!={2023,2024,2025} or len(metrics["complete_year_excess"])!=3:
        errors.append("完整年度集合不正确")
    for name in ("equal_eligible_pool","momentum_only","low_volatility_only"):
        prefix="diagnostic_"+name; schedule={}
        for day,rs in expected_factors.items():
            if name=="equal_eligible_pool": codes=sorted(r["code"] for r in rs)
            elif name=="momentum_only": codes=[r["code"] for r in sorted(rs,key=lambda r:(-Decimal(r["momentum_12_1"]),r["code"]))[:30]]
            else: codes=[r["code"] for r in sorted(rs,key=lambda r:(Decimal(r["volatility_63"]),r["code"]))[:30]]
            schedule[day]=codes
        actual_targets=read_csv(root/(prefix+"_targets.csv")); actual_groups=defaultdict(list)
        for r in actual_targets: actual_groups[r["signal_date"]].append(r["code"])
        if dict(actual_groups)!=schedule: raise ValueError("诊断目标与独立因子复算不同")
        dl=read_csv(root/(prefix+"_ledger.csv")); dp=read_csv(root/(prefix+"_positions.csv")); do=read_csv(root/(prefix+"_orders.csv")); dt=read_csv(root/(prefix+"_trades.csv"))
        if [r["trade_date"] for r in dl]!=engine_calendar: raise ValueError("诊断账本日期缺失")
        validate_costs(dt,Decimal(1),errors,prefix)
        audit_portfolio(dl,dp,do,dt,schedule,provider)
        for period,(start,end) in period_bounds.items():
            calculated=independent_metrics(dl,start,end)
            for k,v in calculated.items():
                actual=metrics["diagnostic_metrics"][name][period][k]
                if (v is None)!=(actual is None) or v is not None and abs(v-actual)>1e-10: errors.append("诊断绩效复算错误")
        del dl,dp,do,dt
    if metrics.get("diagnostics_used_for_promotion") is not False: errors.append("诊断对照被用于晋级")
    for document in (manifest,metrics):
        if document.get("evidence_class")!="HISTORICAL_HOLDOUT_WITH_PRIOR_EXPOSURE" or document.get("forward_validation_status")!="NOT_STARTED":
            errors.append("历史样本标签或前向状态不准确")
    for relative,fingerprint in manifest["code_sha256"].items():
        if sha256_file(PROJECT_ROOT/relative)!=fingerprint: errors.append("本次运行代码版本已变更")
    if manifest.get("validation_decision_sha256")!=sha256_file(PROJECT_ROOT/"docs/DECISION_20260907_RESEARCH_VALIDATION.md"):
        errors.append("验证决策版本改变")

    main_independent = independent_metrics(ledgers["portfolio_30"], "2023-01-01")
    benchmark_independent = independent_metrics(benchmark_ledger, "2023-01-01")
    main_stored = metrics["portfolio_metrics"]["30"]["out_of_sample_2023_2026"]
    benchmark_stored = metrics["benchmark_510300_metrics"]["out_of_sample_2023_2026"]
    for key in ("total_return", "max_drawdown", "calmar"):
        if abs(main_independent[key] - main_stored[key]) > 1e-10:
            errors.append(f"主策略样本外{key}独立复算不一致")
        if abs(benchmark_independent[key] - benchmark_stored[key]) > 1e-10:
            errors.append(f"基准样本外{key}独立复算不一致")

    checks = {
        "oos_after_cost_return_positive": main_stored["total_return"] > 0,
        "oos_return_higher_than_510300": main_stored["total_return"] > benchmark_stored["total_return"],
        "oos_max_drawdown_lower_than_510300": abs(main_stored["max_drawdown"]) < abs(benchmark_stored["max_drawdown"]),
        "oos_calmar_not_lower_than_510300": main_stored["calmar"] >= benchmark_stored["calmar"],
        "at_least_two_of_three_complete_years_positive_excess": sum(
            bool(row["positive_excess"]) for row in metrics["complete_year_excess"]
        ) >= 2,
        "double_cost_oos_return_positive": metrics["double_cost_metrics"]["out_of_sample_2023_2026"]["total_return"] > 0,
        "neighbor_20_and_40_oos_returns_positive": all(
            metrics["portfolio_metrics"][str(count)]["out_of_sample_2023_2026"]["total_return"] > 0
            for count in (20, 40)
        ),
    }
    stored_gate = metrics["research_gate"]
    if checks != stored_gate["checks"]:
        errors.append("研究闸门逐项复算不一致")
    gate = "PASS" if all(checks.values()) else "FAIL"
    if gate != stored_gate["status"] or gate != manifest.get("research_gate"):
        errors.append("研究闸门总状态不一致")

    report = {
        "status": "FAIL" if errors else "PASS",
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run": root.name,
        "accounting_scope": "RESEARCH_PORTFOLIO",
        "factor_rows": len(candidates),
        "signal_dates": len(candidate_groups),
        "main_orders": len(read_csv(root / "portfolio_30_orders.csv")),
        "main_trades": len(read_csv(root / "portfolio_30_trades.csv")),
        "main_ledger_days": len(ledgers["portfolio_30"]),
        "main_position_rows": len(read_csv(root / "portfolio_30_positions.csv")),
        "research_gate": gate,
        "errors": errors[:200],
    }
    report_path = REPORTS_ROOT / f"{root.name}_quality.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if errors:
        return 1

    manifest["status"] = "SEALED"
    manifest["independent_validation"] = {
        "status": "PASS",
        "report": report_path.relative_to(PROJECT_ROOT).as_posix(),
        "validated_at": report["validated_at"],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
