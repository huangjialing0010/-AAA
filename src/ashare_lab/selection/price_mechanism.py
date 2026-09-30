"""左右侧价格形态诊断；不包含财务筛选、组合构造或真实成交。"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


HORIZONS = (21, 63, 126)


def cooldown_allows(last_signal_index: int | None, current_index: int, cooldown_days: int = 126) -> bool:
    """冻结语义：触发后的126个交易日内（含第126日）不重复记信号。"""
    return last_signal_index is None or current_index - last_signal_index > cooldown_days


def finite_positive(value: str | None) -> float | None:
    try:
        number = float(value or "")
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def calendar_prices(
    rows_by_date: Mapping[str, Mapping[str, str]],
    dates: Sequence[str],
    field: str,
) -> list[float] | None:
    values: list[float] = []
    for day in dates:
        row = rows_by_date.get(day)
        value = finite_positive(row.get(field) if row else None)
        if value is None:
            return None
        values.append(value)
    return values


def mechanism_flags(
    rows_by_date: Mapping[str, Mapping[str, str]],
    calendar: Sequence[str],
    signal_index: int,
) -> dict[str, bool]:
    """在共同交易日历上计算冻结的L1/R1价格条件。"""
    if signal_index < 252 or signal_index >= len(calendar):
        return {"L1": False, "R1": False}
    signal_date = calendar[signal_index]
    signal_row = rows_by_date.get(signal_date)
    current = finite_positive(signal_row.get("close_qfq") if signal_row else None)
    if current is None or (signal_row or {}).get("tradestatus") != "1":
        return {"L1": False, "R1": False}

    prior_252 = calendar_prices(rows_by_date, calendar[signal_index - 252:signal_index], "close_qfq")
    l1 = prior_252 is not None and current / max(prior_252) - 1 <= -0.20

    prior_120 = calendar_prices(rows_by_date, calendar[signal_index - 120:signal_index], "close_qfq")
    current_60 = calendar_prices(rows_by_date, calendar[signal_index - 59:signal_index + 1], "close_qfq")
    prior_60 = calendar_prices(rows_by_date, calendar[signal_index - 79:signal_index - 19], "close_qfq")
    r1 = (
        prior_120 is not None
        and current_60 is not None
        and prior_60 is not None
        and current > max(prior_120)
        and statistics.fmean(current_60) > statistics.fmean(prior_60)
    )
    return {"L1": l1, "R1": r1}


def forward_observation(
    rows_by_date: Mapping[str, Mapping[str, str]],
    calendar: Sequence[str],
    signal_index: int,
    horizon: int,
) -> dict[str, Any]:
    """以下一交易日开盘为基点；日历窗口缺一日即不可观察。"""
    entry_index = signal_index + 1
    end_index = signal_index + horizon
    if entry_index >= len(calendar) or end_index >= len(calendar):
        return {"status": "INSUFFICIENT_FUTURE"}
    entry_date = calendar[entry_index]
    end_date = calendar[end_index]
    entry_row = rows_by_date.get(entry_date)
    entry = finite_positive(entry_row.get("open_qfq") if entry_row else None)
    if entry is None or (entry_row or {}).get("tradestatus") != "1":
        return {"status": "ENTRY_UNOBSERVABLE", "entry_date": entry_date, "end_date": end_date}

    holding_dates = calendar[entry_index:end_index + 1]
    closes = calendar_prices(rows_by_date, holding_dates, "close_qfq")
    highs = calendar_prices(rows_by_date, holding_dates, "high_qfq")
    lows = calendar_prices(rows_by_date, holding_dates, "low_qfq")
    if closes is None or highs is None or lows is None:
        return {"status": "PATH_INCOMPLETE", "entry_date": entry_date, "end_date": end_date}
    return {
        "status": "OBSERVED",
        "entry_date": entry_date,
        "end_date": end_date,
        "entry_open_qfq": entry,
        "end_close_qfq": closes[-1],
        "forward_return": closes[-1] / entry - 1,
        "max_favorable_excursion": max(highs) / entry - 1,
        "max_adverse_excursion": min(lows) / entry - 1,
    }


def percentile(values: Iterable[float], probability: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def phase_for_year(year: int) -> str:
    if year <= 2014:
        return "2011_2014"
    if year <= 2018:
        return "2015_2018"
    if year <= 2022:
        return "2019_2022"
    return "2023_2026"


def summarize_observations(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    source = list(rows)
    groups: dict[tuple[str, int, str, str], list[dict[str, Any]]] = {}
    for row in source:
        year = int(row["signal_date"][:4])
        labels = (("overall", "all"), ("year", str(year)), ("phase", phase_for_year(year)))
        for group_type, group_value in labels:
            key = (row["mechanism"], int(row["horizon"]), group_type, group_value)
            groups.setdefault(key, []).append(row)

    result: list[dict[str, Any]] = []
    for (mechanism, horizon, group_type, group_value), members in sorted(groups.items()):
        observed = [row for row in members if row["status"] == "OBSERVED"]
        returns = [float(row["forward_return"]) for row in observed]
        benchmark = [float(row["benchmark_return"]) for row in observed if row.get("benchmark_return") not in (None, "")]
        excess = [float(row["excess_return"]) for row in observed if row.get("excess_return") not in (None, "")]
        adverse = [float(row["max_adverse_excursion"]) for row in observed]
        favorable = [float(row["max_favorable_excursion"]) for row in observed]
        result.append({
            "mechanism": mechanism,
            "horizon": horizon,
            "group_type": group_type,
            "group_value": group_value,
            "signal_count": len(members),
            "observed_count": len(observed),
            "coverage_rate": len(observed) / len(members) if members else None,
            "mean_return": statistics.fmean(returns) if returns else None,
            "median_return": statistics.median(returns) if returns else None,
            "p10_return": percentile(returns, 0.10),
            "p25_return": percentile(returns, 0.25),
            "p75_return": percentile(returns, 0.75),
            "positive_rate": sum(value > 0 for value in returns) / len(returns) if returns else None,
            "mean_benchmark_return": statistics.fmean(benchmark) if benchmark else None,
            "mean_excess_return": statistics.fmean(excess) if excess else None,
            "median_excess_return": statistics.median(excess) if excess else None,
            "positive_excess_rate": sum(value > 0 for value in excess) / len(excess) if excess else None,
            "median_max_adverse_excursion": statistics.median(adverse) if adverse else None,
            "median_max_favorable_excursion": statistics.median(favorable) if favorable else None,
        })
    return result
