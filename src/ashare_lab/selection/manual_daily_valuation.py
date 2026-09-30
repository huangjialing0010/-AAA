"""Read-only daily valuation; adapters must verify seals and review provenance."""
from datetime import time
from .manual_quote import normalize_quote, SHANGHAI
from .account_reconciliation import reconcile
from .cash_dividend_account import valuation


def value_day(account, *, initial_cash, now, ledger_day, actions_verified, quotes):
    if now.tzinfo is None:
        raise ValueError('Timezone required')
    now = now.astimezone(SHANGHAI)
    day = now.date()
    balance = reconcile(account, initial_cash)
    if balance['status'] != 'BALANCES_MATCH':
        raise ValueError('Account reconciliation failed')
    gaps = []
    if ledger_day != day or (account.as_of is not None and account.as_of > day):
        gaps.append('LEDGER_NOT_CURRENT')
    if actions_verified is not True:
        gaps.append('CORPORATE_ACTIONS_NOT_VERIFIED')
    if now.time() < time(15, 5):
        gaps.append('END_OF_DAY_NOT_REACHED')
    prices, provenance = {}, {}
    for code in sorted({lot.code for lot in account.shares.lots}):
        snapshot = quotes.get(code)
        if not snapshot:
            gaps.append(code+':MISSING_QUOTE')
            continue
        try:
            observed = snapshot['observed_at']
            if observed.tzinfo is None or observed > now or not snapshot.get('evidence_id'):
                raise ValueError('INVALID_QUOTE_PROVENANCE')
            payload = snapshot['payload']
            bar = normalize_quote(payload, code=code, day=day, observed_at=observed)
            status = payload['data'].get('f292')
            if type(status) is not int or status != 5 or bar['quote_time'].time() < time(15):
                raise ValueError('CLOSE_NOT_CONFIRMED')
            prices[code] = bar['close']
            provenance[code] = {'evidence_id': snapshot['evidence_id'],
                                'quote_time': bar['quote_time'].isoformat(),
                                'close': str(bar['close'])}
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError) as error:
            gaps.append(code+':'+str(error))
    result = {'day': day.isoformat(), 'status': 'INCOMPLETE' if gaps else 'VALUED',
              'cash': str(account.shares.cash), 'equity_before_unresolved_tax': None,
              'net_pnl_before_unresolved_tax': None, 'account_return': None,
              'tax_status': 'NOT_ASSESSED', 'prices': provenance, 'gaps': gaps,
              'reconciliation': balance}
    if not gaps:
        values = valuation(account, prices)
        equity = values['equity_before_unresolved_tax']
        result['equity_before_unresolved_tax'] = str(equity)
        result['net_pnl_before_unresolved_tax'] = str(equity-initial_cash)
        # Do not label unresolved dividend taxation as a final net return.
        if not account.dividends.recognized:
            result['tax_status'] = 'NO_RECOGNIZED_DIVIDENDS'
            result['account_return'] = str((equity-initial_cash)/initial_cash) if initial_cash > 0 else None
    return result
