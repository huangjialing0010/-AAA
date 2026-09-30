"""510300 MVP的标准数据转换与总回报序列。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable


PRICE_QUANTUM = Decimal("0.000001")
INDEX_QUANTUM = Decimal("0.00000001")


class MvpDataError(RuntimeError):
    """MVP输入违反冻结的数据契约。"""


def decimal_text(value: Decimal, quantum: Decimal) -> str:
    return format(value.quantize(quantum, rounding=ROUND_HALF_UP), "f")


def build_total_return_rows(
    market_rows: Iterable[dict[str, str]],
    dividend_rows: Iterable[dict[str, str]],
) -> list[dict[str, str]]:
    markets = sorted((dict(row) for row in market_rows), key=lambda row: row["date"])
    dividends = [dict(row) for row in dividend_rows]
    if not markets:
        raise MvpDataError("行情为空")
    dates = [row["date"] for row in markets]
    if len(dates) != len(set(dates)):
        raise MvpDataError("行情日期重复")
    ex_cash = {row["ex_date"]: Decimal(row["cash_per_share"]) for row in dividends}
    if len(ex_cash) != len(dividends):
        raise MvpDataError("同一除息日存在多条分红")
    market_dates = set(dates)
    for row in dividends:
        if any(row[field] not in market_dates for field in ("record_date", "ex_date", "payment_date")):
            raise MvpDataError(f"分红日期不在行情交易日: {row}")

    output = []
    previous_close: Decimal | None = None
    index_value = Decimal("1000")
    for row in markets:
        prices = {field: Decimal(row[field]) for field in ("open", "high", "low", "close")}
        if min(prices.values()) <= 0:
            raise MvpDataError(f"非正价格: {row['date']}")
        if prices["high"] < max(prices.values()) or prices["low"] > min(prices.values()):
            raise MvpDataError(f"OHLC关系异常: {row['date']}")
        cash = ex_cash.get(row["date"], Decimal("0"))
        if previous_close is not None:
            index_value *= (prices["close"] + cash) / previous_close
        output.append({
            "trade_date": row["date"],
            "stock_code": "510300",
            "open": decimal_text(prices["open"], PRICE_QUANTUM),
            "high": decimal_text(prices["high"], PRICE_QUANTUM),
            "low": decimal_text(prices["low"], PRICE_QUANTUM),
            "close": decimal_text(prices["close"], PRICE_QUANTUM),
            "volume": str(int(row["volume"])),
            "row_source": row["row_source"],
            "dividend_ex_cash_per_share": decimal_text(cash, PRICE_QUANTUM),
            "total_return_index": decimal_text(index_value, INDEX_QUANTUM),
        })
        previous_close = prices["close"]
    return output
