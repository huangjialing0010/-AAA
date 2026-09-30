"""冻结的移动平均线信号。"""

from __future__ import annotations

from collections import deque
from decimal import Decimal
from typing import Iterable


def moving_average_signals(
    rows: Iterable[dict[str, str]],
    window: int,
    *,
    first_signal_date: str = "2013-01-01",
) -> list[dict[str, str]]:
    if window < 2:
        raise ValueError("均线窗口必须至少为2")
    history: deque[Decimal] = deque()
    running = Decimal("0")
    signals = []
    for row in rows:
        value = Decimal(row["total_return_index"])
        history.append(value)
        running += value
        if len(history) > window:
            running -= history.popleft()
        if len(history) < window or row["trade_date"] < first_signal_date:
            continue
        average = running / Decimal(window)
        target = 1 if value > average else 0
        signals.append({
            "signal_date": row["trade_date"],
            "available_data_through": row["trade_date"],
            "window": str(window),
            "signal_value": format(value, "f"),
            "moving_average": format(average, ".8f"),
            "target_position": str(target),
        })
    return signals
