"""已锁定分红资格的应收/现金子账；不推断税率或生成持仓资格。"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class DividendEntitlement:
    event_id: str
    code: str
    register_date: date
    ex_date: date
    pay_date: date
    shares: int
    gross_per_share: Decimal
    payment_per_share: Decimal
    evidence_id: str

    def __post_init__(self):
        if not self.event_id or not self.evidence_id or len(self.code) != 6 or not self.code.isdigit():
            raise ValueError('缺少事件、证券或证据身份')
        if not self.register_date < self.ex_date <= self.pay_date:
            raise ValueError('分红日期顺序无效')
        if type(self.shares) is not int or self.shares < 0:
            raise ValueError('资格股数必须为非负整数')
        for value in (self.gross_per_share, self.payment_per_share):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError('分红金额必须为明确的非负 Decimal')
        if self.payment_per_share > self.gross_per_share:
            raise ValueError('派发金额不能超过税前金额')


@dataclass(frozen=True)
class DividendState:
    last_date: date | None = None
    recognized: tuple[DividendEntitlement, ...] = ()
    paid_ids: frozenset[str] = frozenset()
    receivable: Decimal = Decimal('0')
    cash_received: Decimal = Decimal('0')
    tax_withheld: Decimal = Decimal('0')


def advance_dividends(state, day, entitlements):
    """逐日处理；同日可重复调用。漏过应处理日则拒绝，不回填历史现金。"""
    if state.last_date is not None and day < state.last_date:
        raise ValueError('不能倒退账本日期')
    known = {event.event_id: event for event in state.recognized}
    supplied = {}
    for event in entitlements:
        if event.event_id in supplied and supplied[event.event_id] != event:
            raise ValueError('同一事件身份内容冲突')
        supplied[event.event_id] = event
        if event.event_id in known and known[event.event_id] != event:
            raise ValueError('已确认资格不得改写')
        if event.ex_date < day and event.event_id not in known:
            raise ValueError('漏过除息日，不允许追溯入账')
    recognized = dict(known)
    paid = set(state.paid_ids)
    receivable, cash, tax = state.receivable, state.cash_received, state.tax_withheld
    audit = []
    for event in sorted(supplied.values(), key=lambda e: e.event_id):
        if event.ex_date == day and event.event_id not in recognized:
            amount = event.gross_per_share * event.shares
            recognized[event.event_id] = event
            receivable += amount
            audit.append((event.event_id, 'RECEIVABLE', amount))
    for event in sorted(recognized.values(), key=lambda e: e.event_id):
        if event.event_id in paid:
            continue
        if event.pay_date < day:
            raise ValueError('漏过到账日，不允许追溯入账')
        if event.pay_date == day:
            gross = event.gross_per_share * event.shares
            payment = event.payment_per_share * event.shares
            receivable -= gross
            cash += payment
            tax += gross - payment
            paid.add(event.event_id)
            audit.append((event.event_id, 'PAYMENT', payment))
    return DividendState(day, tuple(recognized.values()), frozenset(paid), receivable, cash, tax), audit
