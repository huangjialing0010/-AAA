"""当前前向试验的质量候选计算；不生成交易建议。"""

from __future__ import annotations

import statistics
from typing import Any, Iterable


ANNUAL_PERIODS = ("2023-12-31", "2024-12-31", "2025-12-31")


def parse_number(value: str | None) -> float | None:
    try:
        number = float(value or "")
    except (TypeError, ValueError):
        return None
    return number


def current_quality_profile(
    financial_rows: Iterable[dict[str, str]],
    *,
    as_of: str,
    h1_parent_netprofit: str | None,
) -> dict[str, Any]:
    available = {
        row["report_period"]: row
        for row in financial_rows
        if row.get("available_date_assumed", "") <= as_of
    }
    annual = [available.get(period) for period in ANNUAL_PERIODS]
    missing: list[str] = []
    values: dict[str, list[float]] = {}
    for field in ("net_profit", "operating_cashflow_per_share", "roe", "basic_eps"):
        parsed = [parse_number(row.get(field) if row else None) for row in annual]
        if any(value is None for value in parsed):
            missing.append(field)
            values[field] = []
        else:
            values[field] = [float(value) for value in parsed if value is not None]
    h1 = parse_number(h1_parent_netprofit)
    if h1 is None:
        missing.append("h1_parent_netprofit")

    net_profit = values["net_profit"]
    ocf = values["operating_cashflow_per_share"]
    roe = values["roe"]
    eps = values["basic_eps"]
    roe_median = statistics.median(roe) if len(roe) == 3 else None
    eps_total = sum(eps) if len(eps) == 3 else None
    cash_proxy = sum(ocf) / eps_total if len(ocf) == 3 and eps_total is not None and eps_total > 0 else None
    reasons = list(missing)
    if len(net_profit) == 3 and not all(value > 0 for value in net_profit):
        reasons.append("annual_net_profit_not_all_positive")
    if len(ocf) == 3 and sum(value > 0 for value in ocf) < 2:
        reasons.append("operating_cashflow_positive_years_below_2")
    if roe_median is not None and roe_median < 0.10:
        reasons.append("roe_median_below_10pct")
    if h1 is not None and h1 <= 0:
        reasons.append("h1_parent_netprofit_not_positive")
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "roe_median": roe_median,
        "cash_profit_per_share_proxy": cash_proxy,
        "cash_profit_proxy_ge_0_8": cash_proxy is not None and cash_proxy >= 0.8,
        "h1_parent_netprofit": h1,
    }
