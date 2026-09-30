"""Read-only bridge from the original first-order journal into the share ledger.

The caller must validate source manifests before calling. This does not authorize
execution, value holdings, or persist a second copy of the original fill.
"""
from dataclasses import asdict
from datetime import date
from decimal import Decimal
import json

from .manual_account_report import summarize
from .share_account import Fill, ShareAccount
from .cash_dividend_account import CashDividendAccount, apply_fill
from .account_reconciliation import reconcile


def normalized(value):
    return json.loads(json.dumps(value, default=str))


def replay_first_journal(run, events, *, now):
    events = list(events)
    # Validate chronology, identity, first-order scope and independent cash math.
    report = summarize(run, events, now=now)
    account = CashDividendAccount(ShareAccount(Decimal(run['cash'])))
    for raw in report['fills']:
        fields = dict(raw)
        fields['day'] = date.fromisoformat(str(fields['day']))
        fields['sellable_on'] = (date.fromisoformat(str(fields['sellable_on']))
                                if fields.get('sellable_on') is not None else None)
        fields['price'] = Decimal(str(fields['price']))
        fields['fees'] = Decimal(str(fields['fees']))
        account, _ = apply_fill(account, Fill(**fields))
    if account.shares.cash != Decimal(report['cash']):
        raise ValueError('Replayed cash differs from original journal')
    if normalized([asdict(lot) for lot in account.shares.lots]) != normalized(report['positions']):
        raise ValueError('Replayed lots differ from original journal')
    balance = reconcile(account, Decimal(run['cash']))
    if balance['status'] != 'BALANCES_MATCH':
        raise ValueError('Independent replay reconciliation failed')
    return account, {'status': 'REPLAY_MATCH', 'account_id': run['account_id'],
                     'order_status': report['order_status'], 'reconciliation': balance,
                     'scope': 'FIRST_JOURNAL_READ_ONLY_NOT_EXECUTION_APPROVAL'}
