"""MVP回测分段指标。"""

from __future__ import annotations

import math
import statistics
from decimal import Decimal
from typing import Iterable


def calculate_period_metrics(
    ledger_rows: Iterable[dict[str, str]],
    trade_rows: Iterable[dict[str, str]],
    start_date: str,
    end_date: str,
) -> dict[str, float | int | str | None]:
    ledger = list(ledger_rows)
    selected = [row for row in ledger if start_date <= row["trade_date"] <= end_date]
    if not selected:
        raise ValueError(f"区间没有账本记录: {start_date}..{end_date}")
    before = [row for row in ledger if row["trade_date"] < start_date]
    baseline = Decimal(before[-1]["equity"]) if before else Decimal(selected[0]["equity"])
    equities = [baseline] + [Decimal(row["equity"]) for row in selected]
    returns = []
    for previous, current in zip(equities, equities[1:]):
        returns.append(float(current / previous - 1) if previous != 0 else 0.0)
    total_return = float(equities[-1] / baseline - 1)
    years = len(selected) / 252
    annualized = (float(equities[-1] / baseline) ** (1 / years) - 1) if years > 0 and equities[-1] > 0 else None
    peak = equities[0]
    max_drawdown = Decimal("0")
    for value in equities[1:]:
        peak = max(peak, value)
        drawdown = value / peak - 1
        max_drawdown = min(max_drawdown, drawdown)
    volatility = statistics.stdev(returns) * math.sqrt(252) if len(returns) > 1 else 0.0
    mean_return = statistics.mean(returns) if returns else 0.0
    sharpe = mean_return / statistics.stdev(returns) * math.sqrt(252) if len(returns) > 1 and statistics.stdev(returns) > 0 else None
    drawdown_abs = abs(float(max_drawdown))
    calmar = annualized / drawdown_abs if annualized is not None and drawdown_abs > 0 else None
    trades = [row for row in trade_rows if start_date <= row["trade_date"] <= end_date]
    turnover_amount = sum(Decimal(row["gross_amount"]) for row in trades)
    average_equity = sum(equities[1:]) / len(selected)
    turnover = float(turnover_amount / average_equity) if average_equity else 0.0
    fees = sum(Decimal(row["commission"]) + Decimal(row["stamp_tax"]) for row in trades)
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
        "turnover": turnover,
        "fees": float(fees),
    }
