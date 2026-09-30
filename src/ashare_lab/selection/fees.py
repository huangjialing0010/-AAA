"""A股现金账户费用模型；费率必须由调用方显式提供。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_UP


CENT = Decimal("0.01")


@dataclass(frozen=True)
class FeeRuleProfile:
    profile_id: str
    effective_from: date
    commission_rate: Decimal
    commission_minimum: Decimal
    stamp_tax_sell_rate: Decimal

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise ValueError("费用规则必须有profile_id")
        for value in (self.commission_rate, self.commission_minimum, self.stamp_tax_sell_rate):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError("费用参数必须是非负有限Decimal")

    def calculate(self, *, side: str, price: Decimal, shares: int, day: date) -> Decimal:
        if side not in {"BUY", "SELL"}:
            raise ValueError("交易方向无效")
        if day < self.effective_from:
            raise ValueError("费用规则尚未生效")
        if not isinstance(price, Decimal) or price <= 0 or type(shares) is not int or shares <= 0:
            raise ValueError("价格或股数无效")
        turnover = price * shares
        commission = max(turnover * self.commission_rate, self.commission_minimum)
        stamp_tax = turnover * self.stamp_tax_sell_rate if side == "SELL" else Decimal("0")
        return (commission + stamp_tax).quantize(CENT, rounding=ROUND_UP)


@dataclass(frozen=True)
class HistoricalAshareFeeProfile:
    """支持印花税生效日切换的历史费用规则。"""

    profile_id: str
    effective_from: date
    commission_rate: Decimal
    commission_minimum: Decimal
    stamp_tax_before: Decimal
    stamp_tax_after: Decimal
    stamp_tax_change_date: date

    def calculate(self, *, side: str, price: Decimal, shares: int, day: date) -> Decimal:
        if side not in {"BUY", "SELL"}:
            raise ValueError("交易方向无效")
        if day < self.effective_from:
            raise ValueError("费用规则尚未生效")
        if not isinstance(price, Decimal) or price <= 0 or type(shares) is not int or shares <= 0:
            raise ValueError("价格或股数无效")
        turnover = price * shares
        commission = max(turnover * self.commission_rate, self.commission_minimum)
        if side == "SELL":
            rate = self.stamp_tax_after if day >= self.stamp_tax_change_date else self.stamp_tax_before
            commission += turnover * rate
        return commission.quantize(CENT, rounding=ROUND_UP)
