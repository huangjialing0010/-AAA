#!/usr/bin/env python
"""运行冻结的510300 200日均线MVP及基准、稳健性实验。"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ashare_lab.mvp import (  # noqa: E402
    CostConfig,
    DividendEvent,
    calculate_period_metrics,
    moving_average_signals,
    run_engine,
)


CANONICAL_ROOT = PROJECT_ROOT / "data" / "canonical" / "mvp_510300_v2"
INITIAL_CASH = Decimal("1000000")
MAIN_WINDOW = 200
ROBUSTNESS_WINDOWS = (180, 220)
PERIODS = {
    "development_2013_2018": ("2013-01-01", "2018-12-31"),
    "validation_2019_2022": ("2019-01-01", "2022-12-31"),
    "out_of_sample_2023_2026": ("2023-01-01", "2026-09-01"),
}
COSTS = CostConfig(
    commission_rate=Decimal("0.0003"),
    minimum_commission=Decimal("5"),
    slippage_bps=Decimal("5"),
    sell_stamp_tax_rate=Decimal("0"),
    lot_size=100,
)


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


def period_metrics(result: Any) -> dict[str, dict[str, Any]]:
    return {
        name: calculate_period_metrics(result.ledger, result.trades, start, end)
        for name, (start, end) in PERIODS.items()
    }


def benchmark_signals(market: list[dict[str, str]]) -> list[dict[str, str]]:
    candidates = [row["trade_date"] for row in market if row["trade_date"] < "2013-01-01"]
    if not candidates:
        raise RuntimeError("买入持有基准缺少2013年前信号日")
    signal_date = candidates[-1]
    return [{
        "signal_date": signal_date,
        "available_data_through": signal_date,
        "window": "buy_and_hold",
        "signal_value": "",
        "moving_average": "",
        "target_position": "1",
    }]


def file_entry(root: Path, path: Path) -> dict[str, Any]:
    rows = None
    if path.suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = max(sum(1 for _ in handle) - 1, 0)
    output = {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if rows is not None:
        output["rows"] = rows
    return output


def fmt(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.4%}"
    return str(value)


def main() -> int:
    canonical_manifest_path = CANONICAL_ROOT / "manifest.json"
    canonical_manifest = json.loads(canonical_manifest_path.read_text(encoding="utf-8"))
    if canonical_manifest.get("status") != "SEALED":
        raise RuntimeError("MVP标准数据未封存")
    market = read_csv(CANONICAL_ROOT / "daily.csv")
    raw_dividends = read_csv(CANONICAL_ROOT / "dividends.csv")
    dividends = [
        DividendEvent(
            record_date=row["record_date"],
            ex_date=row["ex_date"],
            payment_date=row["payment_date"],
            cash_per_share=Decimal(row["cash_per_share"]),
        )
        for row in raw_dividends
    ]

    main_signals = moving_average_signals(market, MAIN_WINDOW)
    main = run_engine(market, main_signals, dividends, initial_cash=INITIAL_CASH, costs=COSTS)
    main_zero_cost = run_engine(
        market, main_signals, dividends, initial_cash=INITIAL_CASH, costs=CostConfig.zero()
    )
    buy_hold_signals = benchmark_signals(market)
    buy_hold = run_engine(market, buy_hold_signals, dividends, initial_cash=INITIAL_CASH, costs=COSTS)
    buy_hold_zero_cost = run_engine(
        market, buy_hold_signals, dividends, initial_cash=INITIAL_CASH, costs=CostConfig.zero()
    )
    robustness = {}
    for window in ROBUSTNESS_WINDOWS:
        signals = moving_average_signals(market, window)
        result = run_engine(market, signals, dividends, initial_cash=INITIAL_CASH, costs=COSTS)
        robustness[str(window)] = period_metrics(result)

    metrics = {
        "experiment_status": "COMPLETED",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_cutoff": market[-1]["trade_date"],
        "initial_cash": float(INITIAL_CASH),
        "cost_scenario": {
            "commission_rate_including_assumed_pass_through": float(COSTS.commission_rate),
            "minimum_commission": float(COSTS.minimum_commission),
            "slippage_bps_each_side": float(COSTS.slippage_bps),
            "sell_stamp_tax_rate": float(COSTS.sell_stamp_tax_rate),
            "lot_size": COSTS.lot_size,
            "account_specific": False,
        },
        "strategy_200_cost": period_metrics(main),
        "strategy_200_zero_cost": period_metrics(main_zero_cost),
        "buy_and_hold_cost": period_metrics(buy_hold),
        "buy_and_hold_zero_cost": period_metrics(buy_hold_zero_cost),
        "robustness_cost": robustness,
    }
    period = "out_of_sample_2023_2026"
    strategy_oos = metrics["strategy_200_cost"][period]
    benchmark_oos = metrics["buy_and_hold_cost"][period]
    robustness_returns = [
        robustness[str(window)][period]["total_return"] for window in ROBUSTNESS_WINDOWS
    ]
    checks = {
        "oos_after_cost_return_positive": strategy_oos["total_return"] > 0,
        "oos_max_drawdown_lower_than_buy_hold": abs(strategy_oos["max_drawdown"]) < abs(benchmark_oos["max_drawdown"]),
        "oos_calmar_not_lower_than_buy_hold": (
            strategy_oos["calmar"] is not None
            and benchmark_oos["calmar"] is not None
            and strategy_oos["calmar"] >= benchmark_oos["calmar"]
        ),
        "robustness_180_200_220_returns_all_positive": all(
            value > 0 for value in [robustness_returns[0], strategy_oos["total_return"], robustness_returns[1]]
        ),
    }
    metrics["research_gate"] = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "meaning": "PASS仅表示值得继续前向虚拟盘研究，不表示长期盈利或允许实盘",
    }

    output_root = PROJECT_ROOT / "reports" / datetime.now().strftime("mvp_510300_run_%Y%m%dT%H%M%S")
    if output_root.exists():
        raise RuntimeError(f"运行目录已存在，禁止覆盖: {output_root}")
    output_root.mkdir(parents=True)
    write_csv(output_root / "signals.csv", main_signals, [
        "signal_date", "available_data_through", "window", "signal_value", "moving_average", "target_position",
    ])
    write_csv(output_root / "orders.csv", main.orders, [
        "order_id", "signal_date", "order_date", "side", "target_position", "requested_quantity", "status", "reason",
    ])
    write_csv(output_root / "trades.csv", main.trades, [
        "trade_id", "order_id", "signal_date", "trade_date", "side", "quantity", "execution_price",
        "gross_amount", "commission", "stamp_tax", "cash_change",
    ])
    write_csv(output_root / "ledger.csv", main.ledger, [
        "trade_date", "cash", "dividend_receivable", "shares", "close", "market_value", "equity",
        "signal_target_at_close", "pending_execution_after_close",
    ])
    write_csv(output_root / "dividend_ledger.csv", main.dividend_ledger, [
        "event_date", "event_type", "record_date", "ex_date", "payment_date", "entitled_shares",
        "cash_per_share", "amount",
    ])
    write_csv(output_root / "benchmark_trades.csv", buy_hold.trades, [
        "trade_id", "order_id", "signal_date", "trade_date", "side", "quantity", "execution_price",
        "gross_amount", "commission", "stamp_tax", "cash_change",
    ])
    write_csv(output_root / "benchmark_ledger.csv", buy_hold.ledger, [
        "trade_date", "cash", "dividend_receivable", "shares", "close", "market_value", "equity",
        "signal_target_at_close", "pending_execution_after_close",
    ])
    metrics_path = output_root / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    latest_signal = main_signals[-1]
    latest_ledger = main.ledger[-1]
    desired = int(latest_signal["target_position"])
    current = 1 if int(latest_ledger["shares"]) > 0 else 0
    action = "HOLD" if desired == current else ("BUY" if desired == 1 else "SELL")
    virtual = {
        "status": "PENDING_NEXT_TRADING_DAY_DATA",
        "as_of": market[-1]["trade_date"],
        "account_type": "research_virtual_account",
        "cash": latest_ledger["cash"],
        "dividend_receivable": latest_ledger["dividend_receivable"],
        "shares": latest_ledger["shares"],
        "market_value": latest_ledger["market_value"],
        "equity": latest_ledger["equity"],
        "latest_signal": latest_signal,
        "next_action": action,
        "execution": "不得在没有下一完整交易日行情时假设成交",
        "live_broker_connected": False,
    }
    virtual_path = output_root / "virtual_account_snapshot.json"
    virtual_path.write_text(json.dumps(virtual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_lines = [
        "# 510300 200日均线MVP结果",
        "",
        "## 事实",
        "",
        f"- 数据区间：{market[0]['trade_date']}至{market[-1]['trade_date']}，共{len(market)}个交易日。",
        f"- 主策略样本外含成本收益：{fmt(strategy_oos['total_return'])}。",
        f"- 主策略样本外最大回撤：{fmt(strategy_oos['max_drawdown'])}。",
        f"- 主策略样本外Calmar：{strategy_oos['calmar']}。",
        f"- 买入持有样本外含成本收益：{fmt(benchmark_oos['total_return'])}。",
        f"- 买入持有样本外最大回撤：{fmt(benchmark_oos['max_drawdown'])}。",
        f"- 买入持有样本外Calmar：{benchmark_oos['calmar']}。",
        f"- 研究闸门：{metrics['research_gate']['status']}。",
        "",
        "## 判断",
        "",
        (
            "该策略满足预先冻结的继续研究条件，但仍未证明长期盈利。"
            if metrics["research_gate"]["status"] == "PASS"
            else "该策略未满足预先冻结的继续研究条件，不应通过调参包装成成功。"
        ),
        "",
        "## 方案",
        "",
        (
            "保持规则冻结，进入至少一个完整市场阶段的前向虚拟盘观察；本阶段不接券商。"
            if metrics["research_gate"]["status"] == "PASS"
            else "保留已验证的数据和交易内核，淘汰当前策略假设；下一策略必须先写规则再运行。"
        ),
        "",
        "## 风险和代价",
        "",
        "- 单只宽基ETF实验不能证明选股Alpha，也不能代表未来收益。",
        "- 佣金为研究情景，不是用户真实账户费率；滑点为固定假设。",
        "- 公开网页数据通过了本次交叉验证，但仍不具备实盘数据服务等级。",
    ]
    summary_path = output_root / "summary.md"
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    output_files = [path for path in sorted(output_root.iterdir()) if path.is_file()]
    entries = [file_entry(output_root, path) for path in output_files]
    run_manifest = {
        "schema_version": 1,
        "status": "SEALED",
        "generated_at": metrics["generated_at"],
        "input": {
            "canonical_path": CANONICAL_ROOT.relative_to(PROJECT_ROOT).as_posix(),
            "canonical_manifest_sha256": sha256_file(canonical_manifest_path),
        },
        "strategy": {
            "main_window": MAIN_WINDOW,
            "robustness_windows": list(ROBUSTNESS_WINDOWS),
            "signal": "total_return_index_close_above_simple_moving_average",
            "execution": "next_trading_day_unadjusted_open",
            "long_only": True,
        },
        "cost_scenario": metrics["cost_scenario"],
        "research_gate": metrics["research_gate"],
        "outputs": {
            "files": len(entries),
            "bytes": sum(item["bytes"] for item in entries),
            "entries": entries,
        },
    }
    manifest_path = output_root / "run_manifest.json"
    manifest_path.write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "run": str(output_root),
        "research_gate": metrics["research_gate"],
        "oos_strategy": strategy_oos,
        "oos_buy_hold": benchmark_oos,
        "virtual_next_action": action,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
