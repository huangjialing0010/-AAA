"""从已有日线数据推算并审计 A 股涨跌停/停牌规则。

本模块只产生研究证据，不声称提供交易所或供应商的真实规则快照。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping


@dataclass(frozen=True)
class InferredRule:
    ratio: float
    limit_up: float
    limit_down: float


def board_of(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301", "302")):
        return "chinext"
    if code.startswith(("430", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839", "870", "871", "872", "873", "920")):
        return "bse"
    return "main"


def price_limit_ratio(code: str, *, is_st: bool = False, trade_date: str | None = None) -> float:
    board = board_of(code)
    if board == "main" and is_st:
        return 0.10 if not trade_date or trade_date >= "2026-07-06" else 0.05
    return {"main": 0.10, "star": 0.20, "chinext": 0.20, "bse": 0.30}[board]


def infer_rule(row: Mapping[str, str]) -> InferredRule | None:
    try:
        pre_close = float(row["preclose"])
        if pre_close <= 0:
            return None
        code = str(row["code"]).zfill(6)
        ratio = price_limit_ratio(code, is_st=str(row.get("is_st", "0")) == "1", trade_date=row.get("trade_date"))
        return InferredRule(ratio, round(pre_close * (1 + ratio), 2), round(pre_close * (1 - ratio), 2))
    except (KeyError, TypeError, ValueError):
        return None


def classify_row(row: Mapping[str, str], *, tolerance: float = 0.001) -> dict[str, object] | None:
    rule = infer_rule(row)
    if rule is None:
        return None
    try:
        close = float(row["close"])
        volume = float(row.get("volume") or 0)
    except (TypeError, ValueError, KeyError):
        return None
    if close <= 0:
        return None
    up = abs(close - rule.limit_up) <= tolerance
    down = abs(close - rule.limit_down) <= tolerance
    suspended = row.get("tradestatus") != "1" or volume <= 0
    return {
        "trade_date": row.get("trade_date"),
        "code": str(row.get("code", "")).zfill(6),
        "board": board_of(str(row.get("code", ""))),
        "ratio": rule.ratio,
        "inferred_limit_up": up,
        "inferred_limit_down": down,
        "inferred_limit_price": rule.limit_up if up else rule.limit_down if down else None,
        "inferred_suspended": suspended,
        "source_status": row.get("tradestatus"),
    }
