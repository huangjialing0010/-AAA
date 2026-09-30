"""研究组合的分段绩效指标。"""

from __future__ import annotations

import math
import statistics
from decimal import Decimal
from typing import Iterable


def calculate_research_metrics(
    ledger_rows: Iterable[dict[str, str]],
    trade_rows: Iterable[dict[str, str]],
    start_date: str,
    end_date: str,
) -> dict[str, float | int | str | None]:
    ledger = list(ledger_rows)
    selected = [row for row in ledger if start_date <= row["trade_date"] <= end_date]
    if not selected:
        raise ValueError(f"区间没有研究账本: {start_date}..{end_date}")
    before = [row for row in ledger if row["trade_date"] < start_date]
    baseline = Decimal(before[-1]["equity"]) if before else Decimal(selected[0]["equity"])
    equities = [baseline] + [Decimal(row["equity"]) for row in selected]
    daily_returns = [float(current / previous - 1) for previous, current in zip(equities, equities[1:])]
    total_return = float(equities[-1] / baseline - 1)
    years = len(selected) / 252
    annualized = float(equities[-1] / baseline) ** (1 / years) - 1 if years > 0 and equities[-1] > 0 else None
    peak = equities[0]
    max_drawdown = Decimal("0")
    for value in equities[1:]:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)
    volatility = statistics.stdev(daily_returns) * math.sqrt(252) if len(daily_returns) > 1 else 0.0
    sharpe = None
    if len(daily_returns) > 1 and statistics.stdev(daily_returns) > 0:
        sharpe = statistics.mean(daily_returns) / statistics.stdev(daily_returns) * math.sqrt(252)
    drawdown_abs = abs(float(max_drawdown))
    calmar = annualized / drawdown_abs if annualized is not None and drawdown_abs > 0 else None
    trades = [row for row in trade_rows if start_date <= row["trade_date"] <= end_date]
    turnover_amount = sum(Decimal(row["gross_notional"]) for row in trades)
    average_equity = sum(equities[1:]) / len(selected)
    costs = sum(
        Decimal(row["commission"]) + Decimal(row["stamp_tax"]) + Decimal(row["slippage_cost"])
        for row in trades
    )
    return {
        "start_date": selected[0]["trade_date"],
        "end_date": selected[-1]["trade_date"],
        "trading_days": len(selected),
        "start_equity": float(baseline),
        "end_equity": float(equities[-1]),
        "total_return": total_return,
        "annualized_return": annualized,
        "max_drawdown": float(max_drawdown),
        "annualized_volatility": volatility,
        "sharpe_zero_rate": sharpe,
        "calmar": calmar,
        "trade_count": len(trades),
        "turnover": float(turnover_amount / average_equity) if average_equity else 0.0,
        "total_costs": float(costs),
        "accounting_scope": "RESEARCH_PORTFOLIO",
    }


def calculate_index_metrics(
    index_rows: Iterable[dict[str, str]],
    start_date: str,
    end_date: str,
    *,
    value_field: str = "total_return_index",
) -> dict[str, float | int | str | None]:
    rows = list(index_rows)
    selected = [row for row in rows if start_date <= row["trade_date"] <= end_date]
    if not selected:
        raise ValueError(f"区间没有基准数据: {start_date}..{end_date}")
    before = [row for row in rows if row["trade_date"] < start_date]
    baseline = Decimal(before[-1][value_field]) if before else Decimal(selected[0][value_field])
    values = [baseline] + [Decimal(row[value_field]) for row in selected]
    if any(value <= 0 for value in values):
        raise ValueError("基准总回报指数必须为正数")
    daily_returns = [float(current / previous - 1) for previous, current in zip(values, values[1:])]
    total_return = float(values[-1] / baseline - 1)
    years = len(selected) / 252
    annualized = float(values[-1] / baseline) ** (1 / years) - 1 if years > 0 else None
    peak = values[0]
    max_drawdown = Decimal("0")
    for value in values[1:]:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)
    volatility = statistics.stdev(daily_returns) * math.sqrt(252) if len(daily_returns) > 1 else 0.0
    sharpe = None
    if len(daily_returns) > 1 and statistics.stdev(daily_returns) > 0:
        sharpe = statistics.mean(daily_returns) / statistics.stdev(daily_returns) * math.sqrt(252)
    drawdown_abs = abs(float(max_drawdown))
    calmar = annualized / drawdown_abs if annualized is not None and drawdown_abs > 0 else None
    return {
        "start_date": selected[0]["trade_date"],
        "end_date": selected[-1]["trade_date"],
        "trading_days": len(selected),
        "start_index": float(baseline),
        "end_index": float(values[-1]),
        "total_return": total_return,
        "annualized_return": annualized,
        "max_drawdown": float(max_drawdown),
        "annualized_volatility": volatility,
        "sharpe_zero_rate": sharpe,
        "calmar": calmar,
        "accounting_scope": "BENCHMARK_TOTAL_RETURN_INDEX",
    }
