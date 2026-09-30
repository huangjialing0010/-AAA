"""独立余额复算，不调用生产记账函数；限定零初始持仓、无外部出入金。"""
from collections import defaultdict
from decimal import Decimal


def reconcile(account, initial_cash):
    if not isinstance(initial_cash, Decimal) or not initial_cash.is_finite() or initial_cash < 0:
        raise ValueError('初始现金无效')
    cash = initial_cash
    holdings = defaultdict(int)
    errors = []
    seen = set()
    previous = None
    for fill in account.shares.fills:
        if fill.fill_id in seen:
            errors.append('DUPLICATE_FILL')
        seen.add(fill.fill_id)
        order = (fill.day, fill.sequence)
        if previous is not None and order <= previous:
            errors.append('FILL_ORDER_INVALID')
        previous = order
        if account.as_of is None or fill.day > account.as_of:
            errors.append('FUTURE_FILL')
        sign = 1 if fill.side == 'BUY' else -1
        holdings[fill.code] += sign * fill.shares
        cash -= sign * fill.price * fill.shares + fill.fees
        if holdings[fill.code] < 0:
            errors.append('NEGATIVE_POSITION')
    dividend_cash = Decimal(0)
    receivable = Decimal(0)
    withheld = Decimal(0)
    events = set()
    for event in account.dividends.recognized:
        if event.event_id in events:
            errors.append('DUPLICATE_DIVIDEND')
        events.add(event.event_id)
        if account.as_of is None or event.ex_date > account.as_of:
            errors.append('FUTURE_RECOGNITION')
        if event.event_id in account.dividends.paid_ids:
            if account.as_of is None or event.pay_date > account.as_of:
                errors.append('EARLY_PAYMENT')
            dividend_cash += event.shares * event.payment_per_share
            withheld += event.shares * (event.gross_per_share - event.payment_per_share)
        else:
            receivable += event.shares * event.gross_per_share
    if not account.dividends.paid_ids <= events:
        errors.append('UNKNOWN_PAID_EVENT')
    cash += dividend_cash
    stored = defaultdict(int)
    for lot in account.shares.lots:
        stored[lot.code] += lot.shares
    if {k: v for k, v in holdings.items() if v} != dict(stored):
        errors.append('SHARES_MISMATCH')
    for actual, expected, label in (
        (account.shares.cash, cash, 'CASH_MISMATCH'),
        (account.dividends.cash_received, dividend_cash, 'DIVIDEND_CASH_MISMATCH'),
        (account.dividends.receivable, receivable, 'RECEIVABLE_MISMATCH'),
        (account.dividends.tax_withheld, withheld, 'WITHHELD_MISMATCH')):
        if actual != expected:
            errors.append(label)
    return {'status': 'FAIL' if errors else 'BALANCES_MATCH', 'errors': sorted(set(errors)),
            'expected_cash': cash, 'expected_shares': dict(holdings),
            'scope': 'BALANCE_RECONCILIATION_ONLY',
            'limitations': ['不证明成交可执行、持仓批次归属或分红资格正确',
                            '未核验递延税款、市场估值及外部出入金',
                            '仅复算现有记录，记录本身需独立来源和完整性校验']}
