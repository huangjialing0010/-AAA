"""当前质量候选的估值与左右侧观察标签；只生成前向观察基线。"""

from __future__ import annotations

import math
import statistics
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from ashare_lab.selection.price_mechanism import calendar_prices, percentile


def _positive(value: str | None) -> float | None:
    try:
        number = float(value or "")
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _trailing_three_year_start(cutoff: str) -> str:
    day = date.fromisoformat(cutoff)
    try:
        anniversary = day.replace(year=day.year - 3)
    except ValueError:
        anniversary = day.replace(year=day.year - 3, day=28)
    return anniversary.isoformat()


def evaluate_candidate(
    rows: Iterable[Mapping[str, str]],
    calendar: Sequence[str],
    cutoff: str,
    *,
    minimum_valuation_observations: int = 504,
) -> dict[str, Any]:
    source = list(rows)
    rows_by_date = {row["date"]: dict(row, close_qfq=row.get("close", "")) for row in source}
    if len(rows_by_date) != len(source):
        raise ValueError("候选行情日期重复")
    if cutoff not in calendar:
        raise ValueError("公共截止日不在共同交易日历")

    result: dict[str, Any] = {
        "cutoff_date": cutoff,
        "valuation_window_start_exclusive": _trailing_three_year_start(cutoff),
        "status": "REVIEW",
        "mechanism": "",
        "review_reasons": "",
    }
    reasons: list[str] = []
    latest = rows_by_date.get(cutoff)
    if latest is None:
        reasons.append("MISSING_CUTOFF_ROW")
        result["review_reasons"] = ";".join(reasons)
        return result
    if latest.get("tradestatus") != "1":
        reasons.append("LATEST_NOT_TRADABLE")
    if latest.get("isST") != "0":
        reasons.append("LATEST_IS_ST_OR_UNKNOWN")

    start_exclusive = result["valuation_window_start_exclusive"]
    valuation_rows = [
        row for row in source if start_exclusive < row["date"] <= cutoff
    ]
    pe_values = [value for row in valuation_rows if (value := _positive(row.get("peTTM"))) is not None]
    pb_values = [value for row in valuation_rows if (value := _positive(row.get("pbMRQ"))) is not None]
    current_pe = _positive(latest.get("peTTM"))
    current_pb = _positive(latest.get("pbMRQ"))
    result.update({
        "positive_pe_observations": len(pe_values),
        "positive_pb_observations": len(pb_values),
        "current_pe_ttm": current_pe,
        "current_pb_mrq": current_pb,
    })
    if len(pe_values) < minimum_valuation_observations:
        reasons.append("INSUFFICIENT_POSITIVE_PE_HISTORY")
    if len(pb_values) < minimum_valuation_observations:
        reasons.append("INSUFFICIENT_POSITIVE_PB_HISTORY")
    if current_pe is None:
        reasons.append("LATEST_PE_NOT_POSITIVE")
    if current_pb is None:
        reasons.append("LATEST_PB_NOT_POSITIVE")

    signal_index = calendar.index(cutoff)
    current_close = _positive(latest.get("close"))
    result["current_close_qfq"] = current_close
    if signal_index < 252 or current_close is None:
        reasons.append("INSUFFICIENT_PRICE_WINDOW")
        result["review_reasons"] = ";".join(dict.fromkeys(reasons))
        return result

    prior_252 = calendar_prices(rows_by_date, calendar[signal_index - 252:signal_index], "close_qfq")
    prior_120 = calendar_prices(rows_by_date, calendar[signal_index - 120:signal_index], "close_qfq")
    current_60 = calendar_prices(rows_by_date, calendar[signal_index - 59:signal_index + 1], "close_qfq")
    prior_60 = calendar_prices(rows_by_date, calendar[signal_index - 79:signal_index - 19], "close_qfq")
    if None in (prior_252, prior_120, current_60, prior_60):
        reasons.append("INCOMPLETE_COMMON_CALENDAR_PRICE_WINDOW")
        result["review_reasons"] = ";".join(dict.fromkeys(reasons))
        return result

    assert prior_252 is not None and prior_120 is not None
    assert current_60 is not None and prior_60 is not None and current_close is not None
    drawdown = current_close / max(prior_252) - 1
    breakout_margin = current_close / max(prior_120) - 1
    ma_trend = statistics.fmean(current_60) / statistics.fmean(prior_60) - 1
    result.update({
        "drawdown_from_prior_252_high": drawdown,
        "breakout_above_prior_120_high": breakout_margin,
        "ma60_change_vs_20_days_ago": ma_trend,
    })

    if not reasons:
        assert current_pe is not None and current_pb is not None
        pe_p20 = percentile(pe_values, 0.20)
        pe_p80 = percentile(pe_values, 0.80)
        pb_median = statistics.median(pb_values)
        result.update({
            "pe_p20": pe_p20,
            "pe_p80": pe_p80,
            "pe_percentile_rank": sum(value <= current_pe for value in pe_values) / len(pe_values),
            "pb_median": pb_median,
        })
        l1 = current_pe <= pe_p20 and current_pb <= pb_median and drawdown <= -0.20
        r1 = current_pe <= pe_p80 and breakout_margin > 0 and ma_trend > 0
        if l1 and r1:
            result["status"] = "REVIEW"
            result["review_reasons"] = "REVIEW_MECHANISM_CONFLICT"
        elif l1 or r1:
            result["status"] = "BASELINE_TRIGGER"
            result["mechanism"] = "L1" if l1 else "R1"
        else:
            result["status"] = "NO_CURRENT_TRIGGER"
    else:
        result["review_reasons"] = ";".join(dict.fromkeys(reasons))
    return result


def select_watchlist(
    candidates: Iterable[dict[str, Any]],
    *,
    per_mechanism_limit: int = 4,
) -> list[dict[str, Any]]:
    active = [row for row in candidates if row.get("status") == "BASELINE_TRIGGER"]
    active.sort(key=lambda row: (
        -float(row["roe_3y_median"]),
        -float(row["cash_profit_per_share_proxy"]),
        row["stock_code"],
    ))
    counts = {"L1": 0, "R1": 0}
    industries: set[str] = set()
    selected: list[dict[str, Any]] = []
    for row in active:
        mechanism = row["mechanism"]
        industry = row["level1_industry"]
        if counts[mechanism] >= per_mechanism_limit or industry in industries:
            continue
        selected.append({
            **row,
            "watch_rank": len(selected) + 1,
            "watch_status": "FORWARD_WATCH_ONLY",
        })
        counts[mechanism] += 1
        industries.add(industry)
    return selected
