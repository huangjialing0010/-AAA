"""Qlib 风格的最小 Point-in-Time 特征与标签适配层。

这里只提供可审计的样本构造，不声称复现 Qlib/LightGBM 原版结果。
特征只能读取样本日及以前的数据；未来价格只用于标签，且末端样本自动剔除。
"""

from __future__ import annotations

import math
import statistics
from decimal import Decimal, InvalidOperation
from typing import Iterable


def _number(value: str) -> float | None:
    try:
        result = float(Decimal(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def kbar_features(row: dict[str, str]) -> dict[str, float] | None:
    """计算 Qlib Alpha158 的 9 个 KBar 公式（使用不复权 OHLC）。"""
    try:
        open_price, high, low, close = (float(row[name]) for name in ("open", "high", "low", "close"))
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) and value > 0 for value in (open_price, high, low, close)):
        return None
    spread = high - low
    if spread <= 0:
        return None
    upper = high - max(open_price, close)
    lower = min(open_price, close) - low
    return {
        "KMID": (close - open_price) / open_price,
        "KLEN": spread / open_price,
        "KMID2": (close - open_price) / spread,
        "KUP": upper / open_price,
        "KUP2": upper / spread,
        "KLOW": lower / open_price,
        "KLOW2": lower / spread,
        "KSFT": (2 * close - high - low) / open_price,
        "KSFT2": (2 * close - high - low) / spread,
    }


def rolling_features(rows: list[dict[str, str]], index: int, *, window: int = 20) -> dict[str, float] | None:
    """计算 Alpha158 的 ROC/MA/STD 滚动子集（不复权收盘价）。"""
    if window < 2 or index < window:
        return None
    try:
        prices = [float(rows[pos]["close"]) for pos in range(index - window, index + 1)]
    except (KeyError, TypeError, ValueError):
        return None
    if any(not math.isfinite(value) or value <= 0 for value in prices):
        return None
    latest = prices[-1]
    return {
        f"ROC{window}": prices[0] / latest,
        f"MA{window}": statistics.mean(prices) / latest,
        f"STD{window}": statistics.stdev(prices) / latest,
    }


def volume_rolling_features(rows: list[dict[str, str]], index: int, *, window: int = 20) -> dict[str, float] | None:
    """计算 Alpha158 的 VMA/VSTD 滚动子集，当前成交量必须为正。"""
    if window < 2 or index < window:
        return None
    try:
        volumes = [float(rows[pos]["volume"]) for pos in range(index - window, index + 1)]
    except (KeyError, TypeError, ValueError):
        return None
    if any(not math.isfinite(value) or value < 0 for value in volumes) or volumes[-1] <= 0:
        return None
    current = volumes[-1]
    return {
        f"VMA{window}": statistics.mean(volumes) / current,
        f"VSTD{window}": statistics.stdev(volumes) / current,
    }


def price_position_features(rows: list[dict[str, str]], index: int, *, window: int = 20) -> dict[str, float] | None:
    """计算 Alpha158 的 MAX/MIN/RSV 价格位置子集（不复权）。"""
    if window < 2 or index < window:
        return None
    try:
        highs = [float(rows[pos]["high"]) for pos in range(index - window, index + 1)]
        lows = [float(rows[pos]["low"]) for pos in range(index - window, index + 1)]
        close = float(rows[index]["close"])
    except (KeyError, TypeError, ValueError):
        return None
    if any(not math.isfinite(value) or value <= 0 for value in highs + lows + [close]):
        return None
    highest, lowest = max(highs), min(lows)
    if highest <= lowest:
        return None
    return {f"MAX{window}": highest / close, f"MIN{window}": lowest / close,
            f"RSV{window}": (close - lowest) / (highest - lowest)}


def build_point_in_time_samples(
    rows: list[dict[str, str]],
    *,
    horizon: int = 2,
    lookback: int = 20,
) -> list[dict[str, str]]:
    """构造单证券日频样本；标签为未来第 horizon 日相对未来第1日的收益。"""
    if horizon < 1 or lookback < 5:
        raise ValueError("horizon 必须大于0，lookback 至少为5")
    dates = [row.get("trade_date", "") for row in rows]
    if dates != sorted(set(dates)):
        raise ValueError("行情日期重复或未升序")
    close = [_number(row.get("close_qfq", "")) for row in rows]
    amount = [_number(row.get("amount", "")) for row in rows]
    volume = [_number(row.get("volume", "")) for row in rows]
    output: list[dict[str, str]] = []
    for index in range(lookback, len(rows) - horizon):
        current = close[index]
        past = close[index - lookback]
        future = close[index + horizon]
        if current is None or past is None or future is None or min(current, past, future) <= 0:
            continue
        window = close[index - lookback + 1:index + 1]
        if any(value is None or value <= 0 for value in window):
            continue
        returns = [current_price / previous_price - 1 for previous_price, current_price in zip(window, window[1:])]
        if not returns:
            continue
        amount_window = amount[index - 4:index + 1]
        volume_window = volume[index - 4:index + 1]
        if any(value is None or value < 0 for value in amount_window + volume_window):
            continue
        median_amount = statistics.median(value for value in amount_window if value is not None)
        median_volume = statistics.median(value for value in volume_window if value is not None)
        if median_amount <= 0 or median_volume <= 0:
            continue
        kbar = kbar_features(rows[index])
        position = price_position_features(rows, index, window=20)
        volume_rolling = volume_rolling_features(rows, index, window=20)
        if kbar is None or position is None or volume_rolling is None:
            continue
        output.append({
            "trade_date": rows[index]["trade_date"],
            "code": rows[index].get("code", ""),
            "return_5": format(current / close[index - 5] - 1, ".12f"),
            "return_20": format(current / past - 1, ".12f"),
            "volatility_20": format(statistics.stdev(returns) * math.sqrt(252), ".12f"),
            "amount_median_5": format(median_amount, ".12f"),
            "volume_median_5": format(median_volume, ".12f"),
            "label_forward_return": format(future / close[index + 1] - 1, ".12f"),
            "label_horizon": str(horizon),
            **{name: format(value, ".12f") for name, value in kbar.items()},
            **{name: format(value, ".12f") for name, value in rolling_features(rows, index, window=20).items()},
            **{name: format(value, ".12f") for name, value in volume_rolling.items()},
            **{name: format(value, ".12f") for name, value in position.items()},
        })
    return output


def build_samples_by_code(
    rows_by_code: dict[str, list[dict[str, str]]],
    *,
    horizon: int = 2,
    lookback: int = 20,
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for code in sorted(rows_by_code):
        output.extend(build_point_in_time_samples(rows_by_code[code], horizon=horizon, lookback=lookback))
    return sorted(output, key=lambda row: (row["trade_date"], row["code"]))
