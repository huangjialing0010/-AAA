#!/usr/bin/env python
"""独立验收510300 MVP回测与虚拟账本输出。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_ROOT = PROJECT_ROOT / "data" / "canonical" / "mvp_510300_v2"
REPORT_PATH = PROJECT_ROOT / "reports" / "mvp_510300_run_quality.json"
MONEY = Decimal("0.01")
PRICE = Decimal("0.000001")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def latest_run() -> Path:
    candidates = [path for path in (PROJECT_ROOT / "reports").glob("mvp_510300_run_*") if (path / "run_manifest.json").is_file()]
    if not candidates:
        raise RuntimeError("没有可验收的MVP运行目录")
    return sorted(candidates)[-1]


def rounded(value: Decimal, quantum: Decimal = MONEY) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def main() -> int:
    parser = argparse.ArgumentParser(description="独立验收510300 MVP运行")
    parser.add_argument("--run", type=Path)
    args = parser.parse_args()
    run = args.run.resolve() if args.run else latest_run()
    if run.parent != (PROJECT_ROOT / "reports").resolve() or not run.name.startswith("mvp_510300_run_"):
        raise RuntimeError(f"运行目录不在允许范围: {run}")

    manifest_path = run / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if manifest.get("status") != "SEALED":
        errors.append("运行清单未封存")
    canonical_manifest_path = CANONICAL_ROOT / "manifest.json"
    if sha256_file(canonical_manifest_path) != manifest.get("input", {}).get("canonical_manifest_sha256"):
        errors.append("标准数据清单哈希不一致")
    entries = manifest.get("outputs", {}).get("entries", [])
    listed = {entry["path"] for entry in entries}
    actual = {path.name for path in run.iterdir() if path.is_file() and path.name != "run_manifest.json"}
    if listed != actual:
        errors.append(f"输出文件集合与清单不一致: listed={sorted(listed)} actual={sorted(actual)}")
    for entry in entries:
        path = run / entry["path"]
        if not path.is_file() or path.stat().st_size != entry["bytes"] or sha256_file(path) != entry["sha256"]:
            errors.append(f"输出大小或SHA256不一致: {entry['path']}")

    market = read_csv(CANONICAL_ROOT / "daily.csv")
    market_by_date = {row["trade_date"]: row for row in market}
    trade_dates = [row["trade_date"] for row in market]
    date_index = {value: index for index, value in enumerate(trade_dates)}
    signals = read_csv(run / "signals.csv")
    orders = read_csv(run / "orders.csv")
    trades = read_csv(run / "trades.csv")
    ledger = read_csv(run / "ledger.csv")
    dividend_ledger = read_csv(run / "dividend_ledger.csv")
    metrics = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
    virtual = json.loads((run / "virtual_account_snapshot.json").read_text(encoding="utf-8"))

    if len(ledger) != len(market) or [row["trade_date"] for row in ledger] != trade_dates:
        errors.append("账本日期与3468日标准行情不完全一致")
    for row in ledger:
        identity = Decimal(row["cash"]) + Decimal(row["dividend_receivable"]) + Decimal(row["market_value"])
        if identity != Decimal(row["equity"]):
            errors.append(f"资产恒等式不成立: {row['trade_date']}")
        if Decimal(row["cash"]) < 0 or Decimal(row["dividend_receivable"]) < 0:
            errors.append(f"现金或应收为负: {row['trade_date']}")
        if int(row["shares"]) < 0 or int(row["shares"]) % 100 != 0:
            errors.append(f"持仓不是非负100份整数: {row['trade_date']}")

    signal_map = {row["signal_date"]: row for row in signals}
    if len(signal_map) != len(signals) or any(row["available_data_through"] != row["signal_date"] or row["window"] != "200" for row in signals):
        errors.append("主策略信号日期、可用日期或窗口错误")
    filled_orders = {row["order_id"]: row for row in orders if row["status"] == "FILLED"}
    rejected_orders = [row for row in orders if row["status"] == "REJECTED"]
    if len(filled_orders) != len(trades):
        errors.append("已成交订单数与成交记录数不一致")
    cost = manifest["cost_scenario"]
    commission_rate = Decimal(str(cost["commission_rate_including_assumed_pass_through"]))
    minimum = Decimal(str(cost["minimum_commission"]))
    slippage = Decimal(str(cost["slippage_bps_each_side"])) / Decimal("10000")
    for order in orders:
        signal_date = order["signal_date"]
        order_date = order["order_date"]
        if signal_date not in date_index or order_date not in date_index or date_index[order_date] != date_index[signal_date] + 1:
            errors.append(f"订单不是信号后下一交易日: {order['order_id']}")
        if order["target_position"] not in {"0", "1"}:
            errors.append(f"订单目标仓位错误: {order['order_id']}")
    for trade in trades:
        order = filled_orders.get(trade["order_id"])
        if order is None or trade["signal_date"] != order["signal_date"] or trade["trade_date"] != order["order_date"]:
            errors.append(f"成交与订单无法对应: {trade['trade_id']}")
            continue
        quantity = int(trade["quantity"])
        if quantity <= 0 or quantity % 100 != 0:
            errors.append(f"成交数量不是正100份整数: {trade['trade_id']}")
        open_price = Decimal(market_by_date[trade["trade_date"]]["open"])
        factor = Decimal("1") + slippage if trade["side"] == "BUY" else Decimal("1") - slippage
        expected_price = rounded(open_price * factor, PRICE)
        if Decimal(trade["execution_price"]) != expected_price:
            errors.append(f"成交价未按次日开盘和滑点计算: {trade['trade_id']}")
        gross = rounded(expected_price * quantity)
        expected_commission = rounded(max(minimum, gross * commission_rate)) if commission_rate else Decimal("0")
        if Decimal(trade["gross_amount"]) != gross or Decimal(trade["commission"]) != expected_commission:
            errors.append(f"成交金额或佣金错误: {trade['trade_id']}")
        if Decimal(trade["stamp_tax"]) != 0:
            errors.append(f"ETF情景不应出现印花税: {trade['trade_id']}")
        expected_cash = -gross - expected_commission if trade["side"] == "BUY" else gross - expected_commission
        if Decimal(trade["cash_change"]) != expected_cash:
            errors.append(f"成交现金变化错误: {trade['trade_id']}")
    if rejected_orders and any(not row["reason"] for row in rejected_orders):
        errors.append("存在无拒绝原因的拒单")

    canonical_dividends = read_csv(CANONICAL_ROOT / "dividends.csv")
    if len(dividend_ledger) != len(canonical_dividends) * 2:
        errors.append("分红应收和发放事件数量不完整")
    for event in canonical_dividends:
        related = [row for row in dividend_ledger if row["ex_date"] == event["ex_date"]]
        types = {row["event_type"]: row for row in related}
        if set(types) != {"ENTITLEMENT", "PAYMENT"}:
            errors.append(f"分红事件不成对: {event['ex_date']}")
            continue
        entitlement = types["ENTITLEMENT"]
        expected_amount = rounded(Decimal(entitlement["entitled_shares"]) * Decimal(event["cash_per_share"]))
        if Decimal(entitlement["amount"]) != expected_amount or types["PAYMENT"]["amount"] != entitlement["amount"]:
            errors.append(f"分红应收或发放金额错误: {event['ex_date']}")

    period = "out_of_sample_2023_2026"
    strategy = metrics["strategy_200_cost"][period]
    benchmark = metrics["buy_and_hold_cost"][period]
    checks = {
        "oos_after_cost_return_positive": strategy["total_return"] > 0,
        "oos_max_drawdown_lower_than_buy_hold": abs(strategy["max_drawdown"]) < abs(benchmark["max_drawdown"]),
        "oos_calmar_not_lower_than_buy_hold": strategy["calmar"] >= benchmark["calmar"],
        "robustness_180_200_220_returns_all_positive": all(
            value > 0 for value in (
                metrics["robustness_cost"]["180"][period]["total_return"],
                strategy["total_return"],
                metrics["robustness_cost"]["220"][period]["total_return"],
            )
        ),
    }
    expected_gate = "PASS" if all(checks.values()) else "FAIL"
    if metrics["research_gate"]["checks"] != checks or metrics["research_gate"]["status"] != expected_gate:
        errors.append("研究闸门结果与独立重算不一致")
    if metrics["strategy_200_zero_cost"][period]["end_equity"] < strategy["end_equity"]:
        errors.append("无成本策略期末权益反而低于含成本策略")
    if virtual["as_of"] != ledger[-1]["trade_date"] or any(
        virtual[field] != ledger[-1][field]
        for field in ("cash", "dividend_receivable", "shares", "market_value", "equity")
    ):
        errors.append("虚拟账户快照与最终账本不一致")
    if virtual.get("live_broker_connected") is not False:
        errors.append("虚拟账户错误地声明接入实盘")

    report = {
        "status": "FAIL" if errors else "PASS",
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run": run.name,
        "market_days": len(market),
        "signals": len(signals),
        "orders": len(orders),
        "filled_orders": len(filled_orders),
        "rejected_orders": len(rejected_orders),
        "trades": len(trades),
        "ledger_rows": len(ledger),
        "dividend_events": len(canonical_dividends),
        "research_gate": expected_gate,
        "errors": errors,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        manifest["status"] = "REJECTED_VALIDATION_FAILED"
        manifest["independent_validation"] = {
            "status": "FAIL",
            "report": REPORT_PATH.relative_to(PROJECT_ROOT).as_posix(),
            "validated_at": report["validated_at"],
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1
    manifest["independent_validation"] = {
        "status": "PASS",
        "report": REPORT_PATH.relative_to(PROJECT_ROOT).as_posix(),
        "validated_at": report["validated_at"],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
