"""已核定交易规则输入的保守订单检查；不从涨幅猜测板块规则。"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class ExecutionRules:
    day: date
    code: str
    evidence_id: str
    minimum_buy: int
    buy_increment: int
    lower_price: Decimal
    upper_price: Decimal
    sell_policy: str = 'UNVERIFIED'


def check_order(*, code, side, shares, signal_day, execution_day, price,
                tradable, rules, available_shares=0, total_shares=None):
    if side not in ('BUY', 'SELL'):
        return 'INVALID_SIDE'
    if type(shares) is not int or shares <= 0:
        return 'INVALID_SHARES'
    if signal_day >= execution_day:
        return 'SIGNAL_NOT_PRIOR_DAY'
    if rules is None or not rules.evidence_id:
        return 'MISSING_VERIFIED_RULES'
    if rules.day != execution_day or rules.code != code:
        return 'RULES_SCOPE_MISMATCH'
    if tradable is not True:
        return 'NOT_CONFIRMED_TRADABLE'
    for value in (price, rules.lower_price, rules.upper_price):
        if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
            return 'INVALID_PRICE_INPUT'
    if rules.lower_price >= rules.upper_price:
        return 'INVALID_PRICE_BOUNDS'
    if not rules.lower_price <= price <= rules.upper_price:
        return 'PRICE_OUTSIDE_BOUNDS'
    # 无盘口排队证据时保守拒绝触及不利涨跌停方向的订单。
    if side == 'BUY' and price == rules.upper_price:
        return 'BUY_AT_UPPER_LIMIT'
    if side == 'SELL' and price == rules.lower_price:
        return 'SELL_AT_LOWER_LIMIT'
    if side == 'BUY':
        if any(type(v) is not int or v <= 0 for v in (rules.minimum_buy, rules.buy_increment)):
            return 'INVALID_QUANTITY_RULES'
        if shares < rules.minimum_buy or (shares - rules.minimum_buy) % rules.buy_increment:
            return 'BUY_QUANTITY_RULE_VIOLATION'
    else:
        if type(available_shares) is not int or available_shares < shares:
            return 'INSUFFICIENT_SELLABLE_SHARES'
        if rules.sell_policy == 'ROUND100_WITH_WHOLE_REMAINDER':
            if shares % 100:
                if type(total_shares) is not int or total_shares < available_shares:
                    return 'MISSING_TOTAL_POSITION'
                if shares % 100 != total_shares % 100:
                    return 'ODD_LOT_SPLIT_FORBIDDEN'
        elif rules.sell_policy == 'MIN200_OR_ENTIRE_BALANCE':
            if shares < 200:
                if type(total_shares) is not int or total_shares < available_shares:
                    return 'MISSING_TOTAL_POSITION'
                if shares != total_shares:
                    return 'ODD_LOT_SPLIT_FORBIDDEN'
        else:
            return 'SELL_QUANTITY_POLICY_PENDING'
    return 'PRECHECK_PASS_NOT_FILL'
