"""新开仓的含费预算适配；不是信号、订单或撮合器。"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .fees import FeeRuleProfile


@dataclass(frozen=True)
class BuySize:
    status: str
    shares: int
    gross: Decimal
    fees: Decimal
    cash_required: Decimal
    budget: Decimal


def size_opening_buy(*, cash: Decimal, single_budget: Decimal,
                     portfolio_budget: Decimal, execution_price: Decimal,
                     minimum_buy: int, buy_increment: int,
                     fees: FeeRuleProfile, day: date) -> BuySize:
    """在费用单调非减的既有费率模型下求最大合法买入数量。"""
    for amount in (cash, single_budget, portfolio_budget, execution_price):
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
            raise ValueError('金额必须为非负有限Decimal')
    if execution_price == 0:
        raise ValueError('执行价格必须大于零')
    if any(type(v) is not int or v <= 0 for v in (minimum_buy, buy_increment)):
        raise ValueError('数量规则必须为正整数')
    if type(fees) is not FeeRuleProfile:
        raise ValueError('仅支持已验证的单调费用模型FeeRuleProfile')
    if type(day) is not date or day < fees.effective_from:
        raise ValueError('费用规则日期无效')
    budget = min(cash, single_budget, portfolio_budget)

    def cost(shares):
        gross = execution_price * shares
        charge = fees.calculate(side='BUY', price=execution_price, shares=shares, day=day)
        return gross, charge, gross + charge

    if cost(minimum_buy)[2] > budget:
        return BuySize('BUDGET_BELOW_MINIMUM_LOT', 0, Decimal(0), Decimal(0), Decimal(0), budget)
    low = 0
    high = (int(budget / execution_price) - minimum_buy) // buy_increment
    while low < high:
        mid = (low + high + 1) // 2
        if cost(minimum_buy + mid * buy_increment)[2] <= budget:
            low = mid
        else:
            high = mid - 1
    shares = minimum_buy + low * buy_increment
    gross, charge, required = cost(shares)
    return BuySize('SIZED_NOT_EXECUTED', shares, gross, charge, required, budget)
