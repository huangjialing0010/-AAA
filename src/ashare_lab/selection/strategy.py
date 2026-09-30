"""冻结的12-1动量与低波动横截面选股规则。"""

from __future__ import annotations

import bisect
import math
import statistics
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Iterable


def month_end_dates(trade_dates: Iterable[str]) -> list[str]:
    last_by_month: dict[str, str] = {}
    for value in sorted(set(trade_dates)):
        last_by_month[value[:7]] = value
    return [last_by_month[key] for key in sorted(last_by_month)]


class MembershipHistory:
    def __init__(self, rows: Iterable[dict[str, str]]) -> None:
        groups: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            groups[row["observed_date"]].add(row["code"])
        self.dates = sorted(groups)
        self.members = [frozenset(groups[value]) for value in self.dates]
        for value, members in zip(self.dates, self.members):
            if len(members) != 300:
                raise ValueError(f"{value} 历史成分数量不是300: {len(members)}")

    def as_of(self, signal_date: str) -> frozenset[str]:
        index = bisect.bisect_right(self.dates, signal_date) - 1
        if index < 0:
            raise ValueError(f"{signal_date} 之前没有可观测历史成分")
        return self.members[index]


def _decimal(value: str) -> Decimal | None:
    if value == "":
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def factor_observation(
    rows: list[dict[str, str]],
    signal_date: str,
    *,
    minimum_median_amount: Decimal = Decimal("20000000"),
    trading_dates: Iterable[str] | None = None,
    max_window_halt_days: int = 0,
    max_consecutive_halt_days: int = 0,
    recent_halt_window: int = 0,
    max_recent_halt_days: int = 0,
    resumption_cooldown_days: int = 0,
) -> dict[str, str] | None:
    dates = [row["trade_date"] for row in rows]
    if dates != sorted(set(dates)):
        raise ValueError("行情日期重复或未升序")
    index = bisect.bisect_left(dates, signal_date)
    if index >= len(rows) or dates[index] != signal_date or index < 252:
        return None
    return _factor_at_index(rows, index, signal_date, minimum_median_amount,
                            list(trading_dates) if trading_dates is not None else None,
                            max_window_halt_days, max_consecutive_halt_days,
                            recent_halt_window, max_recent_halt_days,
                            resumption_cooldown_days)


def _factor_at_index(
    rows: list[dict[str, str]],
    index: int,
    signal_date: str,
    minimum_median_amount: Decimal,
    trading_dates: list[str] | None = None,
    max_window_halt_days: int = 0,
    max_consecutive_halt_days: int = 0,
    recent_halt_window: int = 0,
    max_recent_halt_days: int = 0,
    resumption_cooldown_days: int = 0,
) -> dict[str, str] | None:
    current = rows[index]
    if current.get("tradestatus") != "1" or current.get("is_st") != "0":
        return None

    window = rows[index - 252:index + 1]
    if trading_dates is not None:
        calendar_index = bisect.bisect_left(trading_dates, signal_date)
        if calendar_index < 252 or trading_dates[calendar_index:calendar_index+1] != [signal_date]:
            return None
        if [row["trade_date"] for row in window] != trading_dates[calendar_index-252:calendar_index+1]:
            return None
    halt_days = sum(row.get("tradestatus") != "1" for row in window)
    longest_halt = 0; current_halt = 0
    for row in window:
        current_halt = current_halt + 1 if row.get("tradestatus") != "1" else 0
        longest_halt = max(longest_halt, current_halt)
    if halt_days > max_window_halt_days or longest_halt > max_consecutive_halt_days:
        return None
    if recent_halt_window and sum(row.get("tradestatus") != "1" for row in rows[max(0, index-recent_halt_window):index]) > max_recent_halt_days:
        return None
    if recent_halt_window and sum(row.get("tradestatus") != "1" for row in rows[index-recent_halt_window:index]) > max_recent_halt_days:
        return None
    if resumption_cooldown_days > 0:
        resumed_days = 0
        for row in reversed(rows[index - 252:index + 1]):
            if row.get("tradestatus") != "1":
                break
            resumed_days += 1
        if resumed_days < resumption_cooldown_days:
            return None
    for row in window:
        price = _decimal(row.get("close_qfq", ""))
        if price is None or price <= 0 or row.get("code") != current["code"]:
            return None
    for field in ("open", "high", "low", "close", "preclose", "volume", "amount"):
        value = _decimal(current.get(field, ""))
        if value is None or value <= 0:
            return None

    close_now = _decimal(current.get("close_qfq", ""))
    close_21 = _decimal(rows[index - 21].get("close_qfq", ""))
    close_252 = _decimal(rows[index - 252].get("close_qfq", ""))
    if close_now is None or close_21 is None or close_252 is None or min(close_now, close_21, close_252) <= 0:
        return None

    volatility_prices = [_decimal(row.get("close_qfq", "")) for row in rows[index - 63:index + 1]]
    if any(value is None or value <= 0 for value in volatility_prices):
        return None
    prices = [value for value in volatility_prices if value is not None]
    returns = [float(current_price / previous_price - 1) for previous_price, current_price in zip(prices, prices[1:])]
    if len(returns) != 63:
        return None
    volatility = statistics.stdev(returns) * math.sqrt(252)

    amounts = [_decimal(row.get("amount", "")) for row in rows[index - 19:index + 1]]
    if any(value is None or value < 0 for value in amounts):
        return None
    median_amount = statistics.median(value for value in amounts if value is not None)
    if median_amount < minimum_median_amount:
        return None

    momentum = close_21 / close_252 - 1
    return {
        "signal_date": signal_date,
        "code": current["code"],
        "momentum_12_1": format(momentum, ".12f"),
        "volatility_63": format(volatility, ".12f"),
        "median_amount_20": format(median_amount, "f"),
    }


def factor_observations(
    rows: list[dict[str, str]],
    signal_dates: Iterable[str],
    *,
    minimum_median_amount: Decimal = Decimal("20000000"),
    trading_dates: Iterable[str] | None = None,
    max_window_halt_days: int = 0,
    max_consecutive_halt_days: int = 0,
    recent_halt_window: int = 0,
    max_recent_halt_days: int = 0,
    resumption_cooldown_days: int = 0,
) -> list[dict[str, str]]:
    dates = [row["trade_date"] for row in rows]
    if dates != sorted(set(dates)):
        raise ValueError("行情日期重复或未升序")
    calendar = list(trading_dates) if trading_dates is not None else None
    output = []
    for signal_date in signal_dates:
        index = bisect.bisect_left(dates, signal_date)
        if index >= len(rows) or dates[index] != signal_date or index < 252:
            continue
        observation = _factor_at_index(rows, index, signal_date, minimum_median_amount, calendar,
                                       max_window_halt_days, max_consecutive_halt_days,
                                       recent_halt_window, max_recent_halt_days,
                                       resumption_cooldown_days)
        if observation is not None:
            output.append(observation)
    return output


def _percentiles(values: dict[str, Decimal], *, higher_is_better: bool) -> dict[str, Decimal]:
    if not values:
        return {}
    ordered = sorted(values, key=lambda code: (values[code], code))
    denominator = Decimal(max(1, len(ordered) - 1))
    raw = {code: Decimal(index) / denominator for index, code in enumerate(ordered)}
    if higher_is_better:
        return raw
    return {code: Decimal("1") - score for code, score in raw.items()}


def rank_candidates(observations: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    rows = [dict(row) for row in observations]
    momentum = {row["code"]: Decimal(row["momentum_12_1"]) for row in rows}
    volatility = {row["code"]: Decimal(row["volatility_63"]) for row in rows}
    momentum_scores = _percentiles(momentum, higher_is_better=True)
    volatility_scores = _percentiles(volatility, higher_is_better=False)
    for row in rows:
        code = row["code"]
        row["momentum_percentile"] = format(momentum_scores[code], ".12f")
        row["low_volatility_percentile"] = format(volatility_scores[code], ".12f")
        composite = (momentum_scores[code] + volatility_scores[code]) / Decimal("2")
        row["composite_score"] = format(composite, ".12f")
    return sorted(rows, key=lambda row: (-Decimal(row["composite_score"]), row["code"]))


def select_codes(ranked: Iterable[dict[str, str]], count: int) -> list[str]:
    if count < 1:
        raise ValueError("持股数必须大于0")
    return [row["code"] for row in list(ranked)[:count]]
