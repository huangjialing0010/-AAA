"""模拟自然人 A 股分红递延税款；不是个人税务结论。"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import calendar

CENT = Decimal('0.01')


def anniversary(day: date, years=0, months=0) -> date:
    month = day.month - 1 + months + years * 12
    year, month = day.year + month // 12, month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class NaturalPersonAShareTaxProfile:
    profile_id: str = 'NATURAL_PERSON_A_SHARE_V1'
    policy_effective: date = date(2015, 9, 8)
    rate: Decimal = Decimal('0.20')

    def __post_init__(self):
        if not self.profile_id or not self.policy_effective <= date.today():
            raise ValueError('税务配置身份或生效日无效')
        if self.rate != Decimal('0.20'):
            raise ValueError('本模拟配置只接受20%法定税率参数')


def holding_band(acquired_on: date, sale_day: date, profile=None) -> str:
    if profile is None:
        profile = NaturalPersonAShareTaxProfile()
    if acquired_on >= sale_day:
        raise ValueError('买入日必须早于卖出日')
    held_through = sale_day - timedelta(days=1)
    if held_through >= anniversary(acquired_on, years=1):
        return 'OVER_ONE_YEAR'
    if held_through >= anniversary(acquired_on, months=1):
        return 'OVER_ONE_MONTH_TO_ONE_YEAR'
    return 'ONE_MONTH_OR_LESS'


def deferred_dividend_tax(*, gross_cash: Decimal, shares: int, acquired_on: date,
                          register_date: date, sale_day: date,
                          profile=None) -> dict:
    if profile is None:
        profile = NaturalPersonAShareTaxProfile()
    if register_date < profile.policy_effective:
        return {'status': 'BLOCKED_POLICY_NOT_COVERED', 'tax': None,
                'profile_id': profile.profile_id}
    if type(shares) is not int or shares < 0 or not isinstance(gross_cash, Decimal) or not gross_cash.is_finite() or gross_cash < 0:
        raise ValueError('分红金额或股数无效')
    band = holding_band(acquired_on, sale_day, profile)
    base = {'ONE_MONTH_OR_LESS': Decimal('1'),
            'OVER_ONE_MONTH_TO_ONE_YEAR': Decimal('0.5'),
            'OVER_ONE_YEAR': Decimal('0')}[band]
    tax = (gross_cash * shares * profile.rate * base).quantize(CENT, rounding=ROUND_HALF_UP)
    return {'status': 'CALCULATED_DEFERRED_LIABILITY', 'tax': tax,
            'gross': gross_cash * shares, 'profile_id': profile.profile_id,
            'holding_band': band, 'liability_due_on': sale_day}
